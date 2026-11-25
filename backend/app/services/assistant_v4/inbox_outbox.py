"""
LabOps Personal AI Agent V4 — Durable Inbox & Fenced Priority Outbox.
- Durable inbox with single-leader cursor persistence, webhook HMAC verification,
  payload limits, and duplicate external_message_id suppression.
- Fenced outbox with dual PostgreSQL (`FOR UPDATE SKIP LOCKED`) and SQLite WAL
  optimistic compare-and-swap claiming committed BEFORE external I/O.
- Pre-call IN_FLIGHT attempt persistence, timeout -> UNKNOWN_DELIVERY classification,
  lease reaper, Unicode-safe chunking, and per-recipient auth recheck at delivery time.
"""
from __future__ import annotations

import hmac
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import and_, select, text, update
from sqlalchemy.orm import Session

from app.db.models import (
    V4DeliveryAttemptRecord,
    V4MessageInboxRecord,
    V4NotificationOutboxRecord,
    V4WorkerCursorRecord,
)
from app.db.session import DatabaseManager
from app.services.assistant_v4.event_store import redact_payload, redact_text

logger = logging.getLogger("labops.v4.queues")

PRIORITY_ORDER = {"P0": 0, "P1": 1, "REPORT": 2, "P2": 3, "P3": 4}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def priority_for_severity(severity: str) -> int:
    return PRIORITY_ORDER.get(str(severity or "P1").upper(), 3)


def verify_zalo_webhook_secret(header_secret: Optional[str], expected_secret: str) -> bool:
    """Validate X-Bot-Api-Secret-Token using constant-time comparison."""
    exp = str(expected_secret or "").strip()
    got = str(header_secret or "").strip()
    if not exp or not got:
        return False
    return hmac.compare_digest(got.encode("utf-8"), exp.encode("utf-8"))


def chunk_zalo_message(text_body: str, *, max_chars: int = 1800) -> List[str]:
    """
    Split long messages cleanly on UTF-8 / line / word boundaries within max_chars.
    Always returns at least one chunk for non-empty input.
    """
    cleaned = str(text_body or "").strip()
    if not cleaned:
        return []
    limit = max(200, int(max_chars))
    if len(cleaned) <= limit:
        return [cleaned]

    chunks: List[str] = []
    remaining = cleaned
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        reserve = 18  # Room for `[Phần X/Y]\n`
        slice_limit = max(100, limit - reserve)
        cut = remaining.rfind("\n", 0, slice_limit)
        if cut < int(slice_limit * 0.4):
            cut = remaining.rfind(" ", 0, slice_limit)
        if cut <= 0:
            cut = slice_limit
        part = remaining[:cut].rstrip()
        if not part:
            part = remaining[:slice_limit]
            cut = slice_limit
        chunks.append(part)
        remaining = remaining[cut:].lstrip()

    if len(chunks) <= 1:
        return chunks
    total = len(chunks)
    tagged: List[str] = []
    for idx, c in enumerate(chunks, 1):
        prefix = f"[Phần {idx}/{total}]\n"
        if len(prefix) + len(c) <= limit:
            tagged.append(prefix + c)
        else:
            tagged.append(c[:limit])
    return tagged


class IngressModeConflictError(RuntimeError):
    """Raised if concurrent webhook + polling ingress is attempted for the same bot."""


class DurableMessageInbox:
    MAX_MESSAGE_CHARS = 8000
    MAX_PAYLOAD_BYTES = 16384

    def __init__(self, db_manager: DatabaseManager, *, active_mode: str = "polling"):
        self.db = db_manager
        self.active_mode = active_mode

    def ensure_ingress_mode(self, incoming_mode: str) -> None:
        if incoming_mode != self.active_mode:
            raise IngressModeConflictError(
                f"Ingress mode '{incoming_mode}' rejected while '{self.active_mode}' is active"
            )

    def get_cursor(self, cursor_name: str) -> Optional[str]:
        with self.db.session_scope() as session:
            row = session.get(V4WorkerCursorRecord, cursor_name)
            return row.cursor_value if row is not None and row.cursor_value else None

    def ingest_webhook_payload(
        self,
        *,
        bot_id: str,
        headers: Dict[str, str],
        expected_secret: str,
        raw_body: bytes,
        parsed_body: Dict[str, Any],
    ) -> Dict[str, Any]:
        self.ensure_ingress_mode("webhook")
        hdr_lower = {str(k).lower(): v for k, v in headers.items()}
        if not verify_zalo_webhook_secret(hdr_lower.get("x-bot-api-secret-token"), expected_secret):
            raise PermissionError("INVALID_WEBHOOK_SECRET")
        if len(raw_body or b"") > self.MAX_PAYLOAD_BYTES:
            raise ValueError("PAYLOAD_TOO_LARGE")

        msg = parsed_body.get("message") if isinstance(parsed_body.get("message"), dict) else parsed_body
        ext_id = str(msg.get("message_id") or parsed_body.get("event_id") or "").strip()
        chat_id = str((msg.get("chat") or {}).get("id") or msg.get("chat_id") or "").strip()
        sender_id = str((msg.get("from") or msg.get("from_user") or {}).get("id") or chat_id).strip()
        text_val = msg.get("text")
        if not ext_id or not chat_id:
            raise ValueError("MISSING_MESSAGE_IDENTIFIERS")
        if not isinstance(text_val, str) or not text_val.strip():
            return {"accepted": False, "ignored": True, "reason": "UNSUPPORTED_NON_TEXT_MESSAGE"}

        return self.ingest_message(
            channel="zalo",
            bot_id=bot_id,
            external_message_id=ext_id,
            chat_id=chat_id,
            sender_id=sender_id,
            text=text_val[: self.MAX_MESSAGE_CHARS],
            update_id=parsed_body.get("update_id"),
            payload_meta={"ingress": "webhook"},
        )

    def ingest_message(
        self,
        *,
        channel: str = "zalo",
        bot_id: str = "default",
        external_message_id: str,
        chat_id: str,
        sender_id: str,
        text: str,
        principal_id: Optional[str] = None,
        update_id: Optional[int] = None,
        payload_meta: Optional[Dict[str, Any]] = None,
        cursor_name: Optional[str] = None,
        cursor_value: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_ext_id = str(external_message_id or "").strip()
        clean_chat = str(chat_id or "").strip()
        clean_sender = str(sender_id or "").strip()
        if not clean_ext_id or not clean_chat or not clean_sender:
            raise ValueError("external_message_id, chat_id, sender_id are required")
        if not isinstance(text, str) or not text.strip():
            return {"accepted": False, "ignored": True, "reason": "EMPTY_OR_UNSUPPORTED"}

        clipped_text = redact_text(text.strip())[: self.MAX_MESSAGE_CHARS]
        now = _utcnow()
        with self.db.session_scope() as session:
            existing = session.execute(
                select(V4MessageInboxRecord).where(
                    V4MessageInboxRecord.channel == channel,
                    V4MessageInboxRecord.bot_id == bot_id,
                    V4MessageInboxRecord.external_message_id == clean_ext_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if cursor_name and cursor_value is not None:
                    self._save_cursor_in_tx(session, cursor_name, str(cursor_value))
                return {
                    "accepted": True,
                    "duplicate": True,
                    "inbox_id": existing.id,
                    "state": existing.state,
                }

            rec_id = f"inb-{uuid.uuid4().hex[:12]}"
            safe_meta = redact_payload(payload_meta or {})
            rec = V4MessageInboxRecord(
                id=rec_id,
                channel=channel,
                bot_id=bot_id,
                external_message_id=clean_ext_id,
                update_id=update_id,
                chat_id=clean_chat,
                sender_id=clean_sender,
                principal_id=principal_id,
                payload_redacted_json={"text": clipped_text, "meta": safe_meta},
                received_at=now,
                state="RECEIVED",
                attempt=0,
                fence=0,
                updated_at=now,
            )
            session.add(rec)
            if cursor_name and cursor_value is not None:
                self._save_cursor_in_tx(session, cursor_name, str(cursor_value))
            session.flush()
            return {"accepted": True, "duplicate": False, "inbox_id": rec_id, "state": "RECEIVED"}

    @staticmethod
    def _save_cursor_in_tx(session: Session, cursor_name: str, cursor_value: str) -> None:
        now = _utcnow()
        row = session.get(V4WorkerCursorRecord, cursor_name)
        if row is None:
            session.add(
                V4WorkerCursorRecord(
                    cursor_name=cursor_name,
                    cursor_value=cursor_value,
                    metadata_json={},
                    updated_at=now,
                )
            )
        else:
            row.cursor_value = cursor_value
            row.updated_at = now

    def mark_processed(self, inbox_id: str, *, state: str = "PROCESSED", principal_id: Optional[str] = None, error: Optional[str] = None) -> None:
        now = _utcnow()
        with self.db.session_scope() as session:
            row = session.get(V4MessageInboxRecord, inbox_id)
            if row is not None:
                row.state = state
                if principal_id is not None:
                    row.principal_id = principal_id
                if error is not None:
                    row.error_detail = redact_text(error)[:500]
                row.processed_at = now
                row.updated_at = now

    def list_recent(self, *, limit: int = 50, principal_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(V4MessageInboxRecord)
            if principal_id:
                stmt = stmt.where(V4MessageInboxRecord.principal_id == principal_id)
            stmt = stmt.order_by(V4MessageInboxRecord.received_at.desc()).limit(max(1, min(limit, 200)))
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "id": r.id,
                    "channel": r.channel,
                    "bot_id": r.bot_id,
                    "external_message_id": r.external_message_id,
                    "chat_id": r.chat_id,
                    "sender_id": r.sender_id,
                    "principal_id": r.principal_id,
                    "state": r.state,
                    "text_snippet": str((r.payload_redacted_json or {}).get("text") or "")[:120],
                    "received_at": _as_utc(r.received_at).isoformat(),
                    "processed_at": _as_utc(r.processed_at).isoformat() if r.processed_at else None,
                }
                for r in rows
            ]


class OutboxV4Service:
    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        target_chars: int = 1800,
        fallback_channel: str = "dashboard_inbox",
    ):
        self.db = db_manager
        self.target_chars = target_chars
        self.fallback_channel = fallback_channel

    def enqueue_in_session(
        self,
        session: Session,
        *,
        dedupe_key: str,
        recipient_id: str,
        event_type: str,
        severity: str = "P1",
        body_text: str,
        channel: str = "zalo",
        event_id: Optional[str] = None,
        task_id: Optional[str] = None,
        policy_version: str = "v4.0",
        body_version: int = 1,
        max_attempts: int = 5,
        not_before: Optional[datetime] = None,
        expires_at: Optional[datetime] = None,
        correlation_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        chunk: bool = True,
    ) -> List[Dict[str, Any]]:
        clean_recipient = str(recipient_id or "").strip()
        if not clean_recipient or clean_recipient == "*":
            raise ValueError("Invalid recipient_id")
        clean_key = str(dedupe_key or "").strip()
        if not clean_key:
            raise ValueError("dedupe_key is required")

        sev = str(severity or "P1").upper()
        prio = priority_for_severity(sev)
        is_crit = sev == "P0"
        now = _utcnow()
        nb = _as_utc(not_before) or now
        exp = _as_utc(expires_at)

        safe_body = redact_text(body_text)
        parts = chunk_zalo_message(safe_body, max_chars=self.target_chars) if chunk else [safe_body]
        if not parts:
            parts = [safe_body or "(empty)"]

        total = len(parts)
        results: List[Dict[str, Any]] = []
        for idx, part_text in enumerate(parts, 1):
            part_key = clean_key if total == 1 else f"{clean_key}:part{idx}of{total}"
            existing = session.execute(
                select(V4NotificationOutboxRecord).where(
                    V4NotificationOutboxRecord.dedupe_key == part_key
                )
            ).scalar_one_or_none()
            if existing is not None:
                results.append(
                    {
                        "notification_id": existing.notification_id,
                        "created": False,
                        "status": existing.status,
                        "dedupe_key": part_key,
                    }
                )
                continue

            nid = f"nout4-{uuid.uuid4().hex[:12]}"
            rec = V4NotificationOutboxRecord(
                notification_id=nid,
                dedupe_key=part_key,
                event_id=event_id,
                task_id=task_id,
                recipient_id=clean_recipient,
                channel=channel,
                priority=prio,
                severity=sev,
                event_type=event_type,
                body_version=body_version,
                policy_version=policy_version,
                is_critical=is_crit,
                chunk_index=idx,
                chunk_total=total,
                body_text=part_text,
                status="PENDING",
                attempts=0,
                max_attempts=max(1, int(max_attempts)),
                not_before=nb,
                expires_at=exp,
                fence=0,
                correlation_id=correlation_id or f"corr-{uuid.uuid4().hex[:8]}",
                metadata_json=redact_payload(metadata or {}),
                created_at=now,
                updated_at=now,
            )
            session.add(rec)
            results.append(
                {
                    "notification_id": nid,
                    "created": True,
                    "status": "PENDING",
                    "dedupe_key": part_key,
                }
            )
        session.flush()
        return results

    def enqueue(self, **kwargs: Any) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            return self.enqueue_in_session(session, **kwargs)

    def claim_batch(
        self,
        worker_id: str,
        *,
        batch_size: int = 15,
        lease_seconds: int = 60,
        now_override: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Short atomic claim committed BEFORE any external I/O.
        - PostgreSQL: uses UPDATE ... FROM (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING.
        - SQLite WAL: priority ordered select + per-row optimistic CAS on (notification_id, status, fence).
        """
        now = _as_utc(now_override) or _utcnow()
        lease_until = now + timedelta(seconds=max(5, int(lease_seconds)))
        limit = max(1, min(int(batch_size), 100))

        with self.db.session_scope() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                rows = session.execute(
                    text(
                        """
                        WITH candidate AS (
                            SELECT notification_id
                            FROM v4_notification_outbox
                            WHERE status IN ('PENDING', 'FAILED_RETRYABLE')
                              AND not_before <= :now
                              AND (expires_at IS NULL OR expires_at > :now)
                            ORDER BY priority ASC, not_before ASC, created_at ASC, notification_id ASC
                            FOR UPDATE SKIP LOCKED
                            LIMIT :lim
                        )
                        UPDATE v4_notification_outbox o
                        SET status = 'CLAIMED',
                            claimed_by = :worker_id,
                            lease_until = :lease_until,
                            fence = o.fence + 1,
                            attempts = o.attempts + 1,
                            updated_at = :now
                        FROM candidate c
                        WHERE o.notification_id = c.notification_id
                        RETURNING o.notification_id, o.dedupe_key, o.recipient_id, o.channel,
                                  o.priority, o.severity, o.event_type, o.is_critical,
                                  o.body_text, o.attempts, o.max_attempts, o.fence,
                                  o.correlation_id, o.metadata_json
                        """
                    ),
                    {
                        "now": now,
                        "lease_until": lease_until,
                        "worker_id": worker_id,
                        "lim": limit,
                    },
                ).mappings().all()
                return [dict(r) for r in rows]

            # SQLite / generic fallback with strict optimistic CAS per row
            candidates = (
                session.execute(
                    select(V4NotificationOutboxRecord)
                    .where(
                        V4NotificationOutboxRecord.status.in_(["PENDING", "FAILED_RETRYABLE"]),
                        V4NotificationOutboxRecord.not_before <= now,
                        (
                            (V4NotificationOutboxRecord.expires_at.is_(None))
                            | (V4NotificationOutboxRecord.expires_at > now)
                        ),
                    )
                    .order_by(
                        V4NotificationOutboxRecord.priority.asc(),
                        V4NotificationOutboxRecord.not_before.asc(),
                        V4NotificationOutboxRecord.created_at.asc(),
                    )
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            claimed: List[Dict[str, Any]] = []
            for row in candidates:
                old_fence = int(row.fence)
                res = session.execute(
                    update(V4NotificationOutboxRecord)
                    .where(
                        V4NotificationOutboxRecord.notification_id == row.notification_id,
                        V4NotificationOutboxRecord.status.in_(["PENDING", "FAILED_RETRYABLE"]),
                        V4NotificationOutboxRecord.fence == old_fence,
                    )
                    .values(
                        status="CLAIMED",
                        claimed_by=worker_id,
                        lease_until=lease_until,
                        fence=old_fence + 1,
                        attempts=int(row.attempts) + 1,
                        updated_at=now,
                    )
                )
                if getattr(res, "rowcount", 0) == 1:
                    claimed.append(
                        {
                            "notification_id": row.notification_id,
                            "dedupe_key": row.dedupe_key,
                            "recipient_id": row.recipient_id,
                            "channel": row.channel,
                            "priority": int(row.priority),
                            "severity": row.severity,
                            "event_type": row.event_type,
                            "is_critical": bool(row.is_critical),
                            "body_text": row.body_text,
                            "attempts": int(row.attempts) + 1,
                            "max_attempts": int(row.max_attempts),
                            "fence": old_fence + 1,
                            "correlation_id": row.correlation_id,
                            "metadata_json": dict(row.metadata_json or {}),
                        }
                    )
            return claimed

    def record_attempt_start(
        self,
        *,
        outbox_id: str,
        recipient_id: str,
        channel: str,
        attempt_number: int,
        fence: int,
    ) -> str:
        """Persist IN_FLIGHT attempt BEFORE executing network request."""
        att_id = f"att4-{uuid.uuid4().hex[:12]}"
        with self.db.session_scope() as session:
            session.add(
                V4DeliveryAttemptRecord(
                    attempt_id=att_id,
                    outbox_id=outbox_id,
                    recipient_id=recipient_id,
                    channel=channel,
                    attempt_number=attempt_number,
                    fence=fence,
                    started_at=_utcnow(),
                    result_class="IN_FLIGHT",
                )
            )
        return att_id

    def finalize_sent(
        self,
        *,
        notification_id: str,
        worker_id: str,
        fence: int,
        attempt_id: str,
        provider_message_id: str,
        duration_ms: int = 0,
    ) -> bool:
        now = _utcnow()
        with self.db.session_scope() as session:
            res = session.execute(
                update(V4NotificationOutboxRecord)
                .where(
                    V4NotificationOutboxRecord.notification_id == notification_id,
                    V4NotificationOutboxRecord.status == "CLAIMED",
                    V4NotificationOutboxRecord.claimed_by == worker_id,
                    V4NotificationOutboxRecord.fence == fence,
                )
                .values(
                    status="SENT",
                    provider_message_id=provider_message_id,
                    last_error=None,
                    last_error_class=None,
                    lease_until=None,
                    sent_at=now,
                    updated_at=now,
                )
            )
            if getattr(res, "rowcount", 0) != 1:
                return False
            att = session.get(V4DeliveryAttemptRecord, attempt_id)
            if att is not None:
                att.finished_at = now
                att.result_class = "PROVIDER_ACCEPTED"
                att.provider_message_id = provider_message_id
                att.duration_ms = duration_ms
            return True

    def finalize_unknown_delivery(
        self,
        *,
        notification_id: str,
        worker_id: str,
        fence: int,
        attempt_id: str,
        error_detail: str,
        duration_ms: int = 0,
    ) -> bool:
        now = _utcnow()
        err = redact_text(error_detail)[:500]
        with self.db.session_scope() as session:
            res = session.execute(
                update(V4NotificationOutboxRecord)
                .where(
                    V4NotificationOutboxRecord.notification_id == notification_id,
                    V4NotificationOutboxRecord.status == "CLAIMED",
                    V4NotificationOutboxRecord.claimed_by == worker_id,
                    V4NotificationOutboxRecord.fence == fence,
                )
                .values(
                    status="UNKNOWN_DELIVERY",
                    last_error_class="TIMEOUT_OR_LOST_RESPONSE",
                    last_error=err,
                    lease_until=None,
                    updated_at=now,
                )
            )
            if getattr(res, "rowcount", 0) != 1:
                return False
            att = session.get(V4DeliveryAttemptRecord, attempt_id)
            if att is not None:
                att.finished_at = now
                att.result_class = "UNKNOWN_DELIVERY"
                att.redacted_error = err
                att.duration_ms = duration_ms
            return True

    def finalize_retry_or_dlq(
        self,
        *,
        notification_id: str,
        worker_id: str,
        fence: int,
        attempt_id: Optional[str],
        error_class: str,
        error_detail: str,
        retryable: bool = True,
        retry_after_seconds: Optional[int] = None,
        duration_ms: int = 0,
    ) -> str:
        now = _utcnow()
        err = redact_text(error_detail)[:500]
        with self.db.session_scope() as session:
            row = session.get(V4NotificationOutboxRecord, notification_id)
            if row is None or row.status != "CLAIMED" or row.claimed_by != worker_id or int(row.fence) != fence:
                return "STALE_WORKER_REJECTED"

            if retryable and int(row.attempts) < int(row.max_attempts):
                delay = retry_after_seconds if retry_after_seconds is not None else min(
                    600, (15 * (2 ** max(0, int(row.attempts) - 1))) + int(random.uniform(0, 5))
                )
                next_status = "FAILED_RETRYABLE"
                next_at = now + timedelta(seconds=max(1, int(delay)))
            else:
                next_status = "DLQ"
                next_at = row.not_before

            res = session.execute(
                update(V4NotificationOutboxRecord)
                .where(
                    V4NotificationOutboxRecord.notification_id == notification_id,
                    V4NotificationOutboxRecord.status == "CLAIMED",
                    V4NotificationOutboxRecord.claimed_by == worker_id,
                    V4NotificationOutboxRecord.fence == fence,
                )
                .values(
                    status=next_status,
                    not_before=next_at,
                    lease_until=None,
                    last_error_class=str(error_class or "ERROR")[:64],
                    last_error=err,
                    updated_at=now,
                )
            )
            if getattr(res, "rowcount", 0) != 1:
                return "STALE_WORKER_REJECTED"
            if attempt_id:
                att = session.get(V4DeliveryAttemptRecord, attempt_id)
                if att is not None:
                    att.finished_at = now
                    att.result_class = str(error_class or "TRANSIENT_FAILURE")[:48]
                    att.redacted_error = err
                    att.duration_ms = duration_ms
            return next_status

    def suppress_recipient_pending(self, recipient_ids: List[str], *, reason: str = "PRINCIPAL_REVOKED") -> int:
        """Suppress any queued unsent V4 notifications for revoked recipients."""
        clean_ids = sorted({str(rid).strip() for rid in recipient_ids if str(rid or "").strip()})
        if not clean_ids:
            return 0
        now = _utcnow()
        clean_reason = redact_text(reason)[:300]
        with self.db.session_scope() as session:
            res = session.execute(
                update(V4NotificationOutboxRecord)
                .where(
                    V4NotificationOutboxRecord.recipient_id.in_(clean_ids),
                    V4NotificationOutboxRecord.status.in_(["PENDING", "FAILED_RETRYABLE"]),
                )
                .values(
                    status="SUPPRESSED",
                    last_error_class="PRINCIPAL_REVOKED",
                    last_error=clean_reason,
                    lease_until=None,
                    updated_at=now,
                )
            )
            return int(getattr(res, "rowcount", 0) or 0)

    def finalize_suppressed(
        self,
        *,
        notification_id: str,
        worker_id: str,
        fence: int,
        reason: str,
    ) -> bool:
        now = _utcnow()
        with self.db.session_scope() as session:
            res = session.execute(
                update(V4NotificationOutboxRecord)
                .where(
                    V4NotificationOutboxRecord.notification_id == notification_id,
                    V4NotificationOutboxRecord.status == "CLAIMED",
                    V4NotificationOutboxRecord.claimed_by == worker_id,
                    V4NotificationOutboxRecord.fence == fence,
                )
                .values(
                    status="SUPPRESSED",
                    last_error_class="SUPPRESSED",
                    last_error=redact_text(reason)[:300],
                    lease_until=None,
                    updated_at=now,
                )
            )
            return getattr(res, "rowcount", 0) == 1

    def reap_expired_leases(self, *, now_override: Optional[datetime] = None) -> Dict[str, int]:
        """
        Reclaim expired CLAIMS without blindly retrying ambiguous deliveries:
        - If an IN_FLIGHT attempt started for the expired claim's fence -> UNKNOWN_DELIVERY.
        - Otherwise -> FAILED_RETRYABLE (or DLQ if attempts >= max_attempts).
        Also expires stale unsent PENDING/FAILED_RETRYABLE rows past expires_at -> EXPIRED.
        """
        now = _as_utc(now_override) or _utcnow()
        stats = {"reclaimed_retry": 0, "marked_unknown": 0, "marked_dlq": 0, "expired": 0}

        with self.db.session_scope() as session:
            expired_pending = (
                session.execute(
                    select(V4NotificationOutboxRecord).where(
                        V4NotificationOutboxRecord.status.in_(["PENDING", "FAILED_RETRYABLE"]),
                        V4NotificationOutboxRecord.expires_at.is_not(None),
                        V4NotificationOutboxRecord.expires_at <= now,
                    )
                )
                .scalars()
                .all()
            )
            for r in expired_pending:
                r.status = "EXPIRED"
                r.last_error_class = "EXPIRED_BEFORE_SEND"
                r.updated_at = now
                stats["expired"] += 1

            stuck = (
                session.execute(
                    select(V4NotificationOutboxRecord).where(
                        V4NotificationOutboxRecord.status == "CLAIMED",
                        V4NotificationOutboxRecord.lease_until.is_not(None),
                        V4NotificationOutboxRecord.lease_until <= now,
                    )
                )
                .scalars()
                .all()
            )
            for row in stuck:
                inflight = (
                    session.execute(
                        select(V4DeliveryAttemptRecord).where(
                            and_(
                                V4DeliveryAttemptRecord.outbox_id == row.notification_id,
                                V4DeliveryAttemptRecord.fence == row.fence,
                                V4DeliveryAttemptRecord.result_class == "IN_FLIGHT",
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if inflight is not None:
                    row.status = "UNKNOWN_DELIVERY"
                    row.last_error_class = "WORKER_CRASH_AFTER_SEND_START"
                    row.last_error = "Worker lease expired after network attempt started"
                    row.lease_until = None
                    row.updated_at = now
                    inflight.finished_at = now
                    inflight.result_class = "UNKNOWN_DELIVERY"
                    inflight.redacted_error = "Worker lease expired while IN_FLIGHT"
                    stats["marked_unknown"] += 1
                elif int(row.attempts) >= int(row.max_attempts):
                    row.status = "DLQ"
                    row.last_error_class = "LEASE_EXPIRED_MAX_ATTEMPTS"
                    row.lease_until = None
                    row.updated_at = now
                    stats["marked_dlq"] += 1
                else:
                    row.status = "FAILED_RETRYABLE"
                    row.last_error_class = "LEASE_EXPIRED_PRE_SEND"
                    row.not_before = now + timedelta(seconds=5)
                    row.lease_until = None
                    row.updated_at = now
                    stats["reclaimed_retry"] += 1
        return stats

    async def deliver_claimed_batch(
        self,
        worker_id: str,
        send_fn: Callable[[str, str, str], Awaitable[Optional[str]]],
        *,
        is_recipient_authorized_fn: Optional[Callable[[str, str], bool]] = None,
        batch_size: int = 15,
    ) -> Dict[str, int]:
        """Claim a batch, verify recipient auth at delivery time, execute send_fn outside TX, fence finalize."""
        claimed = self.claim_batch(worker_id, batch_size=batch_size)
        stats = {"claimed": len(claimed), "sent": 0, "retry": 0, "dlq": 0, "unknown": 0, "suppressed": 0}
        for item in claimed:
            nid = item["notification_id"]
            fence = int(item["fence"])
            recipient = item["recipient_id"]
            channel = item["channel"]

            if is_recipient_authorized_fn is not None and not is_recipient_authorized_fn(recipient, channel):
                if self.finalize_suppressed(
                    notification_id=nid,
                    worker_id=worker_id,
                    fence=fence,
                    reason="RECIPIENT_REVOKED_OR_DISABLED_AT_DELIVERY",
                ):
                    stats["suppressed"] += 1
                continue

            att_id = self.record_attempt_start(
                outbox_id=nid,
                recipient_id=recipient,
                channel=channel,
                attempt_number=int(item["attempts"]),
                fence=fence,
            )
            t0 = _utcnow()
            try:
                prov_id = await send_fn(recipient, item["body_text"], channel)
                dur_ms = max(0, int((_utcnow() - t0).total_seconds() * 1000))
                if not prov_id:
                    prov_id = "accepted"
                if self.finalize_sent(
                    notification_id=nid,
                    worker_id=worker_id,
                    fence=fence,
                    attempt_id=att_id,
                    provider_message_id=str(prov_id),
                    duration_ms=dur_ms,
                ):
                    stats["sent"] += 1
            except TimeoutError as exc:
                dur_ms = max(0, int((_utcnow() - t0).total_seconds() * 1000))
                if self.finalize_unknown_delivery(
                    notification_id=nid,
                    worker_id=worker_id,
                    fence=fence,
                    attempt_id=att_id,
                    error_detail=str(exc),
                    duration_ms=dur_ms,
                ):
                    stats["unknown"] += 1
            except Exception as exc:
                dur_ms = max(0, int((_utcnow() - t0).total_seconds() * 1000))
                msg = str(exc)
                low = msg.lower()
                if "timeout" in low or "timed out" in low:
                    if self.finalize_unknown_delivery(
                        notification_id=nid,
                        worker_id=worker_id,
                        fence=fence,
                        attempt_id=att_id,
                        error_detail=msg,
                        duration_ms=dur_ms,
                    ):
                        stats["unknown"] += 1
                else:
                    is_cap = "quota" in low or "429" in low or "cap" in low
                    retryable = not ("forbidden" in low or "invalid recipient" in low or "cap_hard" in low)
                    err_cls = "CAP_REJECTED" if is_cap else "TRANSIENT_FAILURE"
                    out_st = self.finalize_retry_or_dlq(
                        notification_id=nid,
                        worker_id=worker_id,
                        fence=fence,
                        attempt_id=att_id,
                        error_class=err_cls,
                        error_detail=msg,
                        retryable=retryable,
                        duration_ms=dur_ms,
                    )
                    if err_cls == "CAP_REJECTED" and channel == "zalo":
                        try:
                            self.enqueue(
                                dedupe_key=f"{item['dedupe_key']}:cap-fallback",
                                recipient_id=recipient,
                                channel=self.fallback_channel,
                                event_type=item["event_type"],
                                severity=item["severity"],
                                body_text=item["body_text"],
                                correlation_id=item.get("correlation_id", ""),
                                metadata={"fallback_reason": "PROVIDER_CAP_REJECTED", "source_notification_id": nid},
                                chunk=False,
                            )
                            stats["fallback_enqueued"] = stats.get("fallback_enqueued", 0) + 1
                        except Exception as fb_exc:
                            logger.warning("Failed to enqueue CAP_REJECTED fallback for %s: %s", nid, fb_exc)
                    if out_st == "DLQ":
                        stats["dlq"] += 1
                    elif out_st == "FAILED_RETRYABLE":
                        stats["retry"] += 1
        return stats

    def list_outbox(self, *, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(V4NotificationOutboxRecord)
            if status:
                stmt = stmt.where(V4NotificationOutboxRecord.status == status.upper())
            stmt = stmt.order_by(V4NotificationOutboxRecord.created_at.desc()).limit(max(1, min(limit, 200)))
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "notification_id": r.notification_id,
                    "dedupe_key": r.dedupe_key,
                    "event_id": r.event_id,
                    "task_id": r.task_id,
                    "recipient_id": r.recipient_id,
                    "channel": r.channel,
                    "priority": int(r.priority),
                    "severity": r.severity,
                    "event_type": r.event_type,
                    "status": r.status,
                    "attempts": int(r.attempts),
                    "fence": int(r.fence),
                    "provider_message_id": r.provider_message_id,
                    "last_error_class": r.last_error_class,
                    "last_error": r.last_error,
                    "body_snippet": r.body_text[:160],
                    "created_at": _as_utc(r.created_at).isoformat(),
                    "sent_at": _as_utc(r.sent_at).isoformat() if r.sent_at else None,
                }
                for r in rows
            ]

    def get_metrics(self, *, now_override: Optional[datetime] = None) -> Dict[str, Any]:
        now = _as_utc(now_override) or _utcnow()
        with self.db.session_scope() as session:
            rows = session.execute(select(V4NotificationOutboxRecord)).scalars().all()
            by_status: Dict[str, int] = {}
            oldest_pending_age_by_prio: Dict[str, float] = {}
            for r in rows:
                by_status[r.status] = by_status.get(r.status, 0) + 1
                if r.status in {"PENDING", "FAILED_RETRYABLE", "CLAIMED"}:
                    age_s = max(0.0, (now - (_as_utc(r.created_at) or now)).total_seconds())
                    sev = r.severity
                    if sev not in oldest_pending_age_by_prio or age_s > oldest_pending_age_by_prio[sev]:
                        oldest_pending_age_by_prio[sev] = round(age_s, 2)
            return {
                "total": len(rows),
                "by_status": by_status,
                "oldest_pending_age_seconds_by_priority": oldest_pending_age_by_prio,
            }
