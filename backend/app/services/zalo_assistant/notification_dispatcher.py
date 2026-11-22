"""
Reliable Per-Recipient Notification Outbox & Dispatcher for LabOps AI SRE Agent v2
Tracks independent delivery state per (idempotency_key, recipient_id) in SQLAlchemy,
supports priority queueing (P0 > P1 > REPORT > P2), quiet hours (with P0 bypass),
exponential backoff with jitter, and delivery audit attempts.
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import NotificationDeliveryAttemptRecord, NotificationOutboxRecord
from app.db.session import DatabaseManager

logger = logging.getLogger("labops.sre.dispatcher")

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_PRIORITY_ORDER = {"P0": 0, "P1": 1, "REPORT": 2, "P2": 3}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_in_quiet_hours(start_hhmm: Optional[str], end_hhmm: Optional[str], now_local: Optional[datetime] = None) -> bool:
    if not start_hhmm or not end_hhmm:
        return False
    try:
        sh, sm = [int(x) for x in start_hhmm.split(":", 1)]
        eh, em = [int(x) for x in end_hhmm.split(":", 1)]
    except Exception:
        return False
    local_dt = now_local or datetime.now(VN_TZ)
    cur = local_dt.hour * 60 + local_dt.minute
    start_m = sh * 60 + sm
    end_m = eh * 60 + em
    if start_m <= end_m:
        return start_m <= cur < end_m
    return cur >= start_m or cur < end_m


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError,)):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "timeout" in name or "timed out" in msg or "timeout" in msg


class NotificationDispatcher:
    def __init__(
        self,
        data_dir: str,
        *,
        quota_ledger: Any,
        db_manager: Optional[DatabaseManager] = None,
    ):
        self.data_dir = os.path.abspath(data_dir)
        self.quota_ledger = quota_ledger
        self.db = db_manager or DatabaseManager(self.data_dir)

    def enqueue(
        self,
        *,
        idempotency_key: str,
        recipient_id: str,
        event_type: str,
        message_text: str,
        severity: str = "P1",
        is_critical: bool = False,
        incident_id: Optional[str] = None,
        report_id: Optional[str] = None,
        correlation_id: str = "",
        max_attempts: int = 5,
    ) -> Dict[str, Any]:
        clean_recipient = str(recipient_id or "").strip()
        if not clean_recipient or clean_recipient == "*":
            raise ValueError("Invalid recipient_id")
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            raise ValueError("idempotency_key is required")

        p_hash = hashlib.sha256(f"{clean_recipient}\x00{severity}\x00{message_text}".encode("utf-8")).hexdigest()
        now = _utcnow()

        with self.db.session_scope() as session:
            existing = session.execute(
                select(NotificationOutboxRecord).where(
                    NotificationOutboxRecord.idempotency_key == clean_key,
                    NotificationOutboxRecord.recipient_id == clean_recipient,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return {
                    "id": existing.id,
                    "created": False,
                    "status": existing.status,
                    "recipient_id": existing.recipient_id,
                    "idempotency_key": existing.idempotency_key,
                }

            rec_id = f"nout-{uuid.uuid4().hex[:12]}"
            rec = NotificationOutboxRecord(
                id=rec_id,
                idempotency_key=clean_key,
                recipient_id=clean_recipient,
                channel="zalo",
                event_type=str(event_type or "SRE_ALERT"),
                severity=str(severity or "P1").upper(),
                incident_id=incident_id,
                report_id=report_id,
                is_critical=bool(is_critical or str(severity).upper() == "P0"),
                payload_hash=p_hash,
                message_text=message_text,
                status="PENDING",
                attempt_count=0,
                max_attempts=max(1, int(max_attempts)),
                next_retry_at=now,
                correlation_id=correlation_id or f"corr-{uuid.uuid4().hex[:8]}",
                created_at=now,
                updated_at=now,
            )
            session.add(rec)
            return {
                "id": rec_id,
                "created": True,
                "status": "PENDING",
                "recipient_id": clean_recipient,
                "idempotency_key": clean_key,
            }

    async def dispatch_pending(
        self,
        send_fn: Callable[[str, str], Awaitable[Optional[str]]],
        *,
        limit: int = 20,
        subscriptions: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        now = _utcnow()
        due_rows: List[Dict[str, Any]] = []

        with self.db.session_scope() as session:
            rows = session.execute(
                select(NotificationOutboxRecord).where(
                    NotificationOutboxRecord.status.in_(["PENDING", "FAILED_RETRYABLE"])
                )
            ).scalars().all()
            # Priority ordering P0 < P1 < REPORT < P2, then created_at
            rows.sort(
                key=lambda r: (
                    _PRIORITY_ORDER.get(str(r.severity).upper(), 9),
                    _as_utc(r.created_at) or now,
                )
            )
            for r in rows[:limit]:
                nra = _as_utc(r.next_retry_at)
                if nra is not None and nra > now:
                    continue
                due_rows.append(
                    {
                        "id": r.id,
                        "idempotency_key": r.idempotency_key,
                        "recipient_id": r.recipient_id,
                        "event_type": r.event_type,
                        "severity": str(r.severity).upper(),
                        "is_critical": bool(r.is_critical),
                        "message_text": r.message_text,
                        "attempt_count": int(r.attempt_count),
                        "max_attempts": int(r.max_attempts),
                    }
                )

        stats = {"checked": len(due_rows), "sent": 0, "failed": 0, "unknown": 0, "deferred_quiet": 0}
        subs_map = subscriptions or {}

        for item in due_rows:
            sub = subs_map.get(item["recipient_id"])
            if sub and sub.get("enabled") is False:
                self._mark_suppressed(item["id"], "SUBSCRIPTION_DISABLED")
                continue

            if (
                sub
                and _is_in_quiet_hours(sub.get("quiet_hours_start"), sub.get("quiet_hours_end"))
                and not (item["severity"] == "P0" and sub.get("bypass_quiet_for_p0", True))
            ):
                stats["deferred_quiet"] += 1
                continue

            # Reserve quota ledger atomically (each recipient + idempotency key is independent)
            q_key = f"nout:{item['idempotency_key']}:{item['recipient_id']}"
            try:
                _, ledger_msg_id, _ = self.quota_ledger.reserve_outbox(
                    recipient_id=item["recipient_id"],
                    message_text=item["message_text"],
                    is_critical_alert=bool(item["is_critical"]),
                    idempotency_key=q_key,
                    metadata={"outbox_id": item["id"], "event_type": item["event_type"]},
                )
            except Exception as q_err:
                logger.warning("Quota reserve failed for %s (%s): %s", item["id"], item["severity"], q_err)
                self._record_attempt_failure(
                    item["id"],
                    item["recipient_id"],
                    item["attempt_count"] + 1,
                    item["max_attempts"],
                    f"QUOTA_RESERVE_FAILED: {q_err}",
                    duration_ms=0,
                    unknown=False,
                )
                stats["failed"] += 1
                continue

            if self.quota_ledger.is_already_dispatched(ledger_msg_id):
                # Do NOT double send on transport when ledger already saw SENT/DELIVERY_UNKNOWN
                self._mark_sent(item["id"], provider_message_id="ledger-dedup")
                stats["sent"] += 1
                continue

            started = time.monotonic()
            try:
                prov_id = await send_fn(item["recipient_id"], item["message_text"])
                dur_ms = int((time.monotonic() - started) * 1000)
                if not prov_id:
                    # Still count as accepted by transport if send_fn completed cleanly without raising
                    prov_id = "accepted"
                self.quota_ledger.confirm_sent(ledger_msg_id, provider_message_id=str(prov_id))
                self._mark_sent(item["id"], provider_message_id=str(prov_id), attempt_num=item["attempt_count"] + 1, duration_ms=dur_ms)
                stats["sent"] += 1
            except Exception as send_err:
                dur_ms = int((time.monotonic() - started) * 1000)
                is_timeout = _is_timeout_error(send_err)
                if is_timeout:
                    # Unknown provider state -> do NOT blindly refund quota or aggressively resend immediately
                    self.quota_ledger.mark_delivery_unknown(ledger_msg_id, str(send_err))
                    self._record_attempt_failure(
                        item["id"],
                        item["recipient_id"],
                        item["attempt_count"] + 1,
                        item["max_attempts"],
                        str(send_err),
                        duration_ms=dur_ms,
                        unknown=True,
                    )
                    stats["unknown"] += 1
                else:
                    try:
                        self.quota_ledger.mark_delivery_failed(ledger_msg_id, str(send_err))
                    except Exception:
                        pass
                    self._record_attempt_failure(
                        item["id"],
                        item["recipient_id"],
                        item["attempt_count"] + 1,
                        item["max_attempts"],
                        str(send_err),
                        duration_ms=dur_ms,
                        unknown=False,
                    )
                    stats["failed"] += 1

        return stats

    def _mark_suppressed(self, outbox_id: str, reason: str) -> None:
        now = _utcnow()
        with self.db.session_scope() as session:
            row = session.get(NotificationOutboxRecord, outbox_id)
            if row is not None:
                row.status = "SUPPRESSED"
                row.last_error = reason
                row.updated_at = now

    def _mark_sent(
        self,
        outbox_id: str,
        *,
        provider_message_id: str,
        attempt_num: int = 1,
        duration_ms: int = 0,
    ) -> None:
        now = _utcnow()
        with self.db.session_scope() as session:
            row = session.get(NotificationOutboxRecord, outbox_id)
            if row is None:
                return
            row.status = "SENT"
            row.attempt_count = max(row.attempt_count, attempt_num)
            row.provider_message_id = provider_message_id
            row.last_error = None
            row.sent_at = now
            row.updated_at = now
            session.add(
                NotificationDeliveryAttemptRecord(
                    id=f"ndel-{uuid.uuid4().hex[:12]}",
                    outbox_id=outbox_id,
                    recipient_id=row.recipient_id,
                    attempt_number=attempt_num,
                    status="SENT",
                    provider_message_id=provider_message_id,
                    duration_ms=duration_ms,
                    attempted_at=now,
                )
            )

    def _record_attempt_failure(
        self,
        outbox_id: str,
        recipient_id: str,
        attempt_num: int,
        max_attempts: int,
        error: str,
        *,
        duration_ms: int,
        unknown: bool,
    ) -> None:
        now = _utcnow()
        with self.db.session_scope() as session:
            row = session.get(NotificationOutboxRecord, outbox_id)
            if row is None:
                return
            row.attempt_count = attempt_num
            row.last_error = str(error)[:400]
            row.updated_at = now
            if unknown:
                row.status = "DELIVERY_UNKNOWN"
                att_status = "TIMEOUT_UNKNOWN"
            elif attempt_num >= max_attempts:
                row.status = "FAILED_PERMANENT"
                att_status = "FAILED"
            else:
                backoff_sec = min(600, (15 * (2 ** (attempt_num - 1))) + int(random.uniform(0, 7)))
                row.status = "FAILED_RETRYABLE"
                row.next_retry_at = now + timedelta(seconds=backoff_sec)
                att_status = "FAILED"

            session.add(
                NotificationDeliveryAttemptRecord(
                    id=f"ndel-{uuid.uuid4().hex[:12]}",
                    outbox_id=outbox_id,
                    recipient_id=recipient_id,
                    attempt_number=attempt_num,
                    status=att_status,
                    error_detail=str(error)[:400],
                    duration_ms=duration_ms,
                    attempted_at=now,
                )
            )

    def list_outbox_entries(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            rows = session.execute(
                select(NotificationOutboxRecord)
                .order_by(NotificationOutboxRecord.created_at.desc())
                .limit(limit)
            ).scalars().all()
            return [
                {
                    "id": r.id,
                    "idempotency_key": r.idempotency_key,
                    "recipient_id": r.recipient_id,
                    "event_type": r.event_type,
                    "severity": r.severity,
                    "incident_id": r.incident_id,
                    "report_id": r.report_id,
                    "is_critical": bool(r.is_critical),
                    "status": r.status,
                    "attempt_count": r.attempt_count,
                    "provider_message_id": r.provider_message_id,
                    "last_error": r.last_error,
                    "message_snippet": r.message_text[:120],
                    "created_at": _as_utc(r.created_at).isoformat() if r.created_at else None,
                    "sent_at": _as_utc(r.sent_at).isoformat() if r.sent_at else None,
                }
                for r in rows
            ]

    def list_delivery_attempts(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            rows = session.execute(
                select(NotificationDeliveryAttemptRecord)
                .order_by(NotificationDeliveryAttemptRecord.attempted_at.desc())
                .limit(limit)
            ).scalars().all()
            return [
                {
                    "id": a.id,
                    "outbox_id": a.outbox_id,
                    "recipient_id": a.recipient_id,
                    "attempt_number": a.attempt_number,
                    "status": a.status,
                    "provider_message_id": a.provider_message_id,
                    "error_detail": a.error_detail,
                    "duration_ms": a.duration_ms,
                    "attempted_at": _as_utc(a.attempted_at).isoformat() if a.attempted_at else None,
                }
                for a in rows
            ]
