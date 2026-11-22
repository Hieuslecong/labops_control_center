"""
Database-backed Scheduler Lease Manager for LabOps AI SRE Worker.
Prevents split-brain duplicate polling/reporting across multiple processes or containers
using transactional row-level lease records in PostgreSQL/SQLite.
"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import text

from app.db.models import SchedulerLeaseRecord
from app.db.session import DatabaseManager

logger = logging.getLogger("labops.sre.lease")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SchedulerLeaseManager:
    def __init__(self, data_dir: str, db_manager: Optional[DatabaseManager] = None):
        self.data_dir = os.path.abspath(data_dir)
        self.db = db_manager or DatabaseManager(self.data_dir)
        self.hostname = socket.gethostname()

    def acquire_or_renew(
        self,
        lease_name: str,
        owner_id: str,
        *,
        ttl_seconds: int = 45,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        now = _utcnow()
        expires = now + timedelta(seconds=max(5, int(ttl_seconds)))
        pid = os.getpid()
        meta = dict(metadata or {})

        with self.db.session_scope() as session:
            # Lock row on PostgreSQL / transactional read on SQLite
            row = session.get(SchedulerLeaseRecord, lease_name)
            if row is None:
                session.add(
                    SchedulerLeaseRecord(
                        lease_name=lease_name,
                        owner_id=owner_id,
                        pid=pid,
                        hostname=self.hostname,
                        acquired_at=now,
                        heartbeat_at=now,
                        expires_at=expires,
                        metadata_json=meta,
                    )
                )
                return True

            exp = _as_utc(row.expires_at)
            if row.owner_id == owner_id or (exp is not None and exp <= now):
                if row.owner_id != owner_id:
                    row.acquired_at = now
                row.owner_id = owner_id
                row.pid = pid
                row.hostname = self.hostname
                row.heartbeat_at = now
                row.expires_at = expires
                if meta:
                    row.metadata_json = meta
                return True

            return False

    def release(self, lease_name: str, owner_id: str) -> bool:
        with self.db.session_scope() as session:
            row = session.get(SchedulerLeaseRecord, lease_name)
            if row is None or row.owner_id != owner_id:
                return False
            session.delete(row)
            return True

    def get_lease_status(self, lease_name: str) -> Optional[Dict[str, Any]]:
        now = _utcnow()
        with self.db.session_scope() as session:
            row = session.get(SchedulerLeaseRecord, lease_name)
            if row is None:
                return None
            exp = _as_utc(row.expires_at)
            acq = _as_utc(row.acquired_at)
            hb = _as_utc(row.heartbeat_at)
            return {
                "lease_name": row.lease_name,
                "owner_id": row.owner_id,
                "pid": row.pid,
                "hostname": row.hostname,
                "acquired_at": acq.isoformat() if acq else None,
                "heartbeat_at": hb.isoformat() if hb else None,
                "expires_at": exp.isoformat() if exp else None,
                "is_active": bool(exp and exp > now),
                "metadata": dict(row.metadata_json or {}),
            }

    def db_healthcheck(self) -> Dict[str, Any]:
        try:
            with self.db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {"status": "ONLINE", "backend": self.db.engine.dialect.name}
        except Exception as exc:
            return {"status": "OFFLINE", "error": str(exc)[:200]}
