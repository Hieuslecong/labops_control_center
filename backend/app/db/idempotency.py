from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from sqlalchemy import delete, select

from app.db.models import IdempotencyRecord
from app.db.session import DatabaseManager


class IdempotencyConflictError(Exception):
    def __init__(self, message: str = "Idempotency-Key đã được dùng cho yêu cầu với nội dung khác", status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def compute_request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyStore:
    """Actor + endpoint + key + payload-hash scoped idempotency store backed by SQLAlchemy."""

    def __init__(self, db: DatabaseManager, default_ttl_seconds: int = 86400) -> None:
        self.db = db
        self.default_ttl_seconds = default_ttl_seconds

    def check_or_store(
        self,
        *,
        actor: str,
        endpoint: str,
        key: Optional[str],
        payload: Any,
        compute_fn: Callable[[], Dict[str, Any]],
        status_code: int = 200,
    ) -> Dict[str, Any]:
        if not key or not key.strip():
            return compute_fn()

        norm_key = key.strip()
        req_hash = compute_request_hash(payload)
        now = datetime.now(timezone.utc)

        with self.db.session_scope() as s:
            s.execute(delete(IdempotencyRecord).where(IdempotencyRecord.expires_at <= now))
            existing = s.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.actor == actor,
                    IdempotencyRecord.endpoint == endpoint,
                    IdempotencyRecord.idempotency_key == norm_key,
                )
            )
            if existing is not None:
                if existing.request_hash != req_hash:
                    raise IdempotencyConflictError()
                cached = dict(existing.response_json or {})
                cached["idempotent_replay"] = True
                return cached

        result = compute_fn()

        with self.db.session_scope() as s:
            existing_after = s.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.actor == actor,
                    IdempotencyRecord.endpoint == endpoint,
                    IdempotencyRecord.idempotency_key == norm_key,
                )
            )
            if existing_after is not None:
                if existing_after.request_hash != req_hash:
                    raise IdempotencyConflictError()
                cached = dict(existing_after.response_json or {})
                cached["idempotent_replay"] = True
                return cached

            rec = IdempotencyRecord(
                actor=actor,
                endpoint=endpoint,
                idempotency_key=norm_key,
                request_hash=req_hash,
                status_code=status_code,
                response_json=result,
                created_at=now,
                expires_at=now + timedelta(seconds=self.default_ttl_seconds),
            )
            s.add(rec)
        return result
