"""
Zalo Message Quota Ledger & Outbox Manager
Enforces hard limit of 3,000 outbound messages/month:
- 2,800 standard outbound messages
- 200 reserved exclusively for critical alerts
Provides atomic quota reservation, idempotency, retry safety, and monthly reset reconciliation.
"""
from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


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
        if not os.path.exists(self.ledger_path):
            return self._new_state()
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                state = json.load(f)
                return self._reconcile_month(state)
        except Exception:
            return self._new_state()

    def _save(self, state: Dict[str, Any]) -> None:
        tmp = self.ledger_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.ledger_path)

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
        Atomic message outbox reservation with strict quota guard and idempotency.
        Returns: (success: bool, message_id_or_reason: str, quota_snapshot: dict)
        """
        with self._locked() as state:
            # 1. Idempotency check
            if idempotency_key and idempotency_key in state.get("idempotency_keys", {}):
                prev_id = state["idempotency_keys"][idempotency_key]
                return True, prev_id, state["usage"]

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

            # 2. Atomic increment
            msg_id = f"zmsg-{uuid.uuid4().hex[:12]}"
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            if is_critical_alert:
                usage["critical_sent"] += 1
            else:
                usage["standard_sent"] += 1
            usage["total_sent"] += 1

            # Daily breakdown
            daily = state.setdefault("daily_usage", {}).setdefault(today, {"standard": 0, "critical": 0, "total": 0})
            if is_critical_alert:
                daily["critical"] = daily.get("critical", 0) + 1
            else:
                daily["standard"] = daily.get("standard", 0) + 1
            daily["total"] = daily.get("total", 0) + 1

            # Outbox recording
            outbox_entry = {
                "id": msg_id,
                "recipient_id": recipient_id,
                "message_snippet": message_text[:120],
                "is_critical": is_critical_alert,
                "status": "SENT",
                "created_at": _now(),
                "metadata": metadata or {},
            }
            state.setdefault("outbox", []).append(outbox_entry)
            # Retain at most recent 500 outbox entries
            if len(state["outbox"]) > 500:
                state["outbox"] = state["outbox"][-500:]

            if idempotency_key:
                state.setdefault("idempotency_keys", {})[idempotency_key] = msg_id

            state["last_updated"] = _now()
            return True, msg_id, state["usage"]

    def record_ai_request(self) -> int:
        with self._locked() as state:
            state["usage"]["ai_requests"] = state["usage"].get("ai_requests", 0) + 1
            state["last_updated"] = _now()
            return state["usage"]["ai_requests"]

    def get_recent_outbox(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._locked() as state:
            return list(reversed(state.get("outbox", [])[-limit:]))
