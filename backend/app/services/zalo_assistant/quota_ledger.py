"""
Zalo Message Quota Ledger & Outbox Manager
Enforces hard limit of 3,000 outbound messages/month:
- 2,800 standard outbound messages
- 200 reserved exclusively for critical alerts
Provides atomic quota reservation, idempotency, retry safety, and monthly reset reconciliation.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.db.atomic_json import PersistenceCorruptionError, atomic_write_json, safe_load_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _payload_hash(recipient_id: str, message_text: str, is_critical_alert: bool) -> str:
    raw = f"{recipient_id}\x00{int(bool(is_critical_alert))}\x00{message_text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class QuotaExceededError(Exception):
    def __init__(self, message: str, current_usage: int, limit: int):
        super().__init__(message)
        self.message = message
        self.current_usage = current_usage
        self.limit = limit


class QuotaLedger:
    TOTAL_MONTHLY_LIMIT = 3000
    STANDARD_LIMIT = 2800
    CRITICAL_ALERT_RESERVE = 200

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.ledger_path = os.path.join(data_dir, "zalo_quota_ledger.json")
        self.lock_path = self.ledger_path + ".lock"
        os.makedirs(data_dir, exist_ok=True)
        self._ensure_bootstrap()

    @contextmanager
    def _locked(self):
        with open(self.lock_path, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                state = self._load()
                yield state
                self._save(state)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _load(self) -> Dict[str, Any]:
        state = safe_load_json(self.ledger_path, self._new_state)
        if not isinstance(state, dict):
            raise PersistenceCorruptionError(
                f"Invalid quota ledger structure in '{self.ledger_path}': expected object"
            )
        return self._reconcile_month(state)

    def _save(self, state: Dict[str, Any]) -> None:
        atomic_write_json(self.ledger_path, state, mode=0o600, use_lock=False)

    def _new_state(self) -> Dict[str, Any]:
        return {
            "current_month": _current_month_key(),
            "limits": {
                "total": self.TOTAL_MONTHLY_LIMIT,
                "standard": self.STANDARD_LIMIT,
                "critical_reserve": self.CRITICAL_ALERT_RESERVE,
            },
            "usage": {
                "standard_sent": 0,
                "critical_sent": 0,
                "total_sent": 0,
                "ai_requests": 0,
            },
            "daily_usage": {},
            "outbox": [],
            "idempotency_keys": {},
            "history_months": {},
            "last_updated": _now(),
        }

    def _reconcile_month(self, state: Dict[str, Any]) -> Dict[str, Any]:
        curr = _current_month_key()
        if state.get("current_month") != curr:
            old_month = state.get("current_month", "unknown")
            state.setdefault("history_months", {})[old_month] = {
                "usage": state.get("usage", {}),
                "closed_at": _now(),
            }
            state["current_month"] = curr
            state["usage"] = {
                "standard_sent": 0,
                "critical_sent": 0,
                "total_sent": 0,
                "ai_requests": 0,
            }
            state["daily_usage"] = {}
            state["idempotency_keys"] = {}
            state["last_updated"] = _now()
        return state

    def _ensure_bootstrap(self) -> None:
        with self._locked():
            pass

    def get_status(self) -> Dict[str, Any]:
        with self._locked() as state:
            usage = state["usage"]
            standard_left = max(0, self.STANDARD_LIMIT - usage["standard_sent"])
            critical_left = max(0, self.CRITICAL_ALERT_RESERVE - usage["critical_sent"])
            total_left = max(0, self.TOTAL_MONTHLY_LIMIT - usage["total_sent"])

            return {
                "current_month": state["current_month"],
                "total_limit": self.TOTAL_MONTHLY_LIMIT,
                "standard_limit": self.STANDARD_LIMIT,
                "critical_reserve": self.CRITICAL_ALERT_RESERVE,
                "total_sent": usage["total_sent"],
                "standard_sent": usage["standard_sent"],
                "critical_sent": usage["critical_sent"],
                "ai_requests": usage.get("ai_requests", 0),
                "standard_remaining": standard_left,
                "critical_remaining": critical_left,
                "total_remaining": total_left,
                "percent_used": round((usage["total_sent"] / self.TOTAL_MONTHLY_LIMIT) * 100, 2),
                "is_standard_exhausted": standard_left <= 0,
                "is_critical_exhausted": critical_left <= 0,
                "daily_usage": state.get("daily_usage", {}),
                "last_updated": state["last_updated"],
            }

    def _reserve_in_state(
        self,
        state: Dict[str, Any],
        recipient_id: str,
        message_text: str,
        *,
        is_critical_alert: bool = False,
        idempotency_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        initial_status: str = "RESERVED",
    ) -> Tuple[bool, str, Dict[str, Any]]:
        norm_key = idempotency_key.strip() if idempotency_key and str(idempotency_key).strip() else None
        scoped_key = f"{recipient_id}:{norm_key}" if norm_key else None
        expected_hash = _payload_hash(recipient_id, message_text, is_critical_alert)

        if scoped_key and scoped_key in state.get("idempotency_keys", {}):
            stored = state["idempotency_keys"][scoped_key]
            if isinstance(stored, dict):
                if stored.get("payload_hash") != expected_hash:
                    raise ValueError("Idempotency-Key đã được sử dụng với nội dung khác.")
                if stored.get("status") != "FAILED_REFUNDED":
                    return True, str(stored.get("message_id", "")), dict(state["usage"])
            else:
                return True, str(stored), dict(state["usage"])

        usage = state["usage"]
        if is_critical_alert:
            if usage["critical_sent"] >= self.CRITICAL_ALERT_RESERVE:
                raise QuotaExceededError(
                    "Đã vượt hạn mức dự phòng tin nhắn cảnh báo khẩn cấp trong tháng (200 tin).",
                    usage["critical_sent"],
                    self.CRITICAL_ALERT_RESERVE,
                )
        else:
            if usage["standard_sent"] >= self.STANDARD_LIMIT:
                raise QuotaExceededError(
                    "Đã đạt giới hạn 2.800 tin nhắn thông thường trong tháng. 200 tin còn lại chỉ dành cho cảnh báo khẩn cấp.",
                    usage["standard_sent"],
                    self.STANDARD_LIMIT,
                )

        if usage.get("total_sent", 0) >= self.TOTAL_MONTHLY_LIMIT:
            raise QuotaExceededError(
                "Đã đạt tổng giới hạn 3.000 tin nhắn trong tháng.",
                usage.get("total_sent", 0),
                self.TOTAL_MONTHLY_LIMIT,
            )

        msg_id = f"zmsg-{uuid.uuid4().hex[:12]}"
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        today = now_dt.strftime("%Y-%m-%d")

        if is_critical_alert:
            usage["critical_sent"] += 1
        else:
            usage["standard_sent"] += 1
        usage["total_sent"] += 1

        daily = state.setdefault("daily_usage", {}).setdefault(today, {"standard": 0, "critical": 0, "total": 0})
        if is_critical_alert:
            daily["critical"] = daily.get("critical", 0) + 1
        else:
            daily["standard"] = daily.get("standard", 0) + 1
        daily["total"] = daily.get("total", 0) + 1

        outbox_entry: Dict[str, Any] = {
            "id": msg_id,
            "recipient_id": recipient_id,
            "message_snippet": message_text[:120],
            "is_critical": bool(is_critical_alert),
            "status": initial_status,
            "date_key": today,
            "created_at": now_iso,
            "metadata": metadata or {},
        }
        if initial_status == "SENT":
            outbox_entry["sent_at"] = now_iso

        state.setdefault("outbox", []).append(outbox_entry)
        if len(state["outbox"]) > 500:
            state["outbox"] = state["outbox"][-500:]

        if scoped_key:
            state.setdefault("idempotency_keys", {})[scoped_key] = {
                "message_id": msg_id,
                "payload_hash": expected_hash,
                "status": initial_status,
            }

        state["last_updated"] = now_iso
        return True, msg_id, dict(usage)

    def reserve_outbox(
        self,
        recipient_id: str,
        message_text: str,
        *,
        is_critical_alert: bool = False,
        idempotency_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Phase 1 of Two-Phase Outbox: atomically reserve quota & record RESERVED entry."""
        with self._locked() as state:
            return self._reserve_in_state(
                state,
                recipient_id,
                message_text,
                is_critical_alert=is_critical_alert,
                idempotency_key=idempotency_key,
                metadata=metadata,
                initial_status="RESERVED",
            )

    def confirm_sent(self, msg_id: str, *, provider_message_id: Optional[str] = None) -> Dict[str, Any]:
        """Phase 2a of Two-Phase Outbox: mark RESERVED entry as confirmed SENT."""
        with self._locked() as state:
            entry = next((e for e in state.get("outbox", []) if e.get("id") == msg_id), None)
            if entry is None:
                raise LookupError(f"Outbox message '{msg_id}' not found")
            if entry.get("status") == "FAILED_REFUNDED":
                raise ValueError(f"Outbox message '{msg_id}' was already refunded")

            now_iso = _now()
            if entry.get("status") != "SENT":
                entry["status"] = "SENT"
                entry["sent_at"] = now_iso
            if provider_message_id:
                entry["provider_message_id"] = provider_message_id

            for rec in state.get("idempotency_keys", {}).values():
                if isinstance(rec, dict) and rec.get("message_id") == msg_id:
                    rec["status"] = "SENT"

            state["last_updated"] = now_iso
            return dict(entry)

    def mark_delivery_failed(self, msg_id: str, error: str) -> Dict[str, Any]:
        """Phase 2b of Two-Phase Outbox: mark delivery failed and refund reserved quota."""
        with self._locked() as state:
            entry = next((e for e in state.get("outbox", []) if e.get("id") == msg_id), None)
            if entry is None:
                raise LookupError(f"Outbox message '{msg_id}' not found")
            if entry.get("status") == "FAILED_REFUNDED":
                return dict(entry)
            if entry.get("status") == "SENT":
                raise ValueError(f"Outbox message '{msg_id}' already confirmed SENT")

            now_iso = _now()
            entry["status"] = "FAILED_REFUNDED"
            entry["failed_at"] = now_iso
            entry["error"] = str(error)[:300]

            usage = state["usage"]
            is_crit = bool(entry.get("is_critical"))
            if is_crit:
                usage["critical_sent"] = max(0, int(usage.get("critical_sent", 0)) - 1)
            else:
                usage["standard_sent"] = max(0, int(usage.get("standard_sent", 0)) - 1)
            usage["total_sent"] = max(0, int(usage.get("total_sent", 0)) - 1)

            date_key = str(entry.get("date_key") or str(entry.get("created_at", ""))[:10])
            daily_bucket = state.get("daily_usage", {}).get(date_key)
            if isinstance(daily_bucket, dict):
                if is_crit:
                    daily_bucket["critical"] = max(0, int(daily_bucket.get("critical", 0)) - 1)
                else:
                    daily_bucket["standard"] = max(0, int(daily_bucket.get("standard", 0)) - 1)
                daily_bucket["total"] = max(0, int(daily_bucket.get("total", 0)) - 1)

            for rec in state.get("idempotency_keys", {}).values():
                if isinstance(rec, dict) and rec.get("message_id") == msg_id:
                    rec["status"] = "FAILED_REFUNDED"

            state["last_updated"] = now_iso
            return dict(entry)

    def reserve_and_send(
        self,
        recipient_id: str,
        message_text: str,
        *,
        is_critical_alert: bool = False,
        idempotency_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Atomic single-step outbox reservation + SENT mark (for internal/test direct dispatch).
        """
        with self._locked() as state:
            return self._reserve_in_state(
                state,
                recipient_id,
                message_text,
                is_critical_alert=is_critical_alert,
                idempotency_key=idempotency_key,
                metadata=metadata,
                initial_status="SENT",
            )

    def record_ai_request(self) -> int:
        with self._locked() as state:
            state["usage"]["ai_requests"] = state["usage"].get("ai_requests", 0) + 1
            state["last_updated"] = _now()
            return state["usage"]["ai_requests"]

    def get_recent_outbox(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._locked() as state:
            return list(reversed(state.get("outbox", [])[-limit:]))
