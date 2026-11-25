"""
LabOps Personal AI Agent V4 — Feature-Flagged Deterministic Retention Maintenance Service.
- Feature-flagged OFF (`enabled=False`) and defaults to `dry_run=True`.
- Bounded SQLite/PostgreSQL batch deletion in short per-batch transactions (never runs VACUUM).
- Strictly foreign-key-aware deletion order (children before parents).
- Strictly preserves pending/retryable/unknown notifications, active tasks/watches/sessions,
  non-terminal action proposals, unconsumed events, dedupe safety windows, and ALL legacy
  business/audit/member/workspace/incident/KB/preference tables.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AuditEventRecord,
    V4ActionProposalRecord,
    V4AssistantTaskRecord,
    V4ChatMessageRecord,
    V4ChatSessionRecord,
    V4DeliveryAttemptRecord,
    V4EventConsumerReceiptRecord,
    V4EventStoreRecord,
    V4MessageInboxRecord,
    V4NotificationOutboxRecord,
    V4WatchRuleRecord,
    V4WebhookDeliveryRecord,
    V4WorkerCursorRecord,
)
from app.db.session import DatabaseManager
from app.services.assistant_v4.config import RetentionV4Config
from app.services.assistant_v4.event_store import redact_text

logger = logging.getLogger("labops.v4.retention")

RETENTION_CURSOR_KEY = "retention:last_run"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class RetentionMaintenanceService:
    """Deterministic, bounded, FK-safe retention & maintenance engine for v4_* operational tables."""

    PROTECTED_IMMUTABLE_TABLES: Tuple[str, ...] = (
        "audit_events",
        "incidents",
        "incident_events",
        "members",
        "member_profiles",
        "member_web_credentials",
        "workspaces",
        "server_access_requests",
        "notification_outbox",
        "notification_delivery_attempts",
        "telemetry_history_samples",
        "v4_knowledge_documents",
        "v4_user_preferences",
        "v4_worker_cursors",
        "v4_connector_states",
        "v4_investigations",
        "v4_incident_meta",
    )

    def __init__(self, db_manager: DatabaseManager, *, cfg: Optional[RetentionV4Config] = None):
        self.db = db_manager
        self.cfg = cfg or RetentionV4Config()

    def _safe_cutoff(self, now: datetime, *, days: int, enforce_dedupe_safety: bool) -> datetime:
        age_cutoff = now - timedelta(days=max(1, int(days)))
        if enforce_dedupe_safety:
            safety_cutoff = now - timedelta(hours=max(24, int(self.cfg.min_dedupe_safety_hours)))
            return min(age_cutoff, safety_cutoff)
        return age_cutoff

    def _run_batched_category(
        self,
        *,
        category: str,
        select_ids_fn: Callable[[Session, int], List[str]],
        count_fn: Callable[[Session], int],
        delete_ids_fn: Callable[[Session, List[str]], int],
        dry_run: bool,
    ) -> Dict[str, Any]:
        batch_sz = max(1, int(self.cfg.batch_size))
        max_batches = max(1, int(self.cfg.max_batches_per_run))

        if dry_run:
            with self.db.session_scope() as session:
                cand = int(count_fn(session) or 0)
            return {
                "category": category,
                "eligible": cand,
                "deleted": 0,
                "would_delete": min(cand, batch_sz * max_batches),
                "batches": 0,
                "truncated": cand > batch_sz * max_batches,
            }

        deleted_total = 0
        batches_ran = 0
        has_more = False
        for _ in range(max_batches):
            with self.db.session_scope() as session:
                ids = select_ids_fn(session, batch_sz)
                if not ids:
                    break
                cnt = int(delete_ids_fn(session, ids) or 0)
                deleted_total += cnt
                batches_ran += 1
                if len(ids) < batch_sz:
                    break
        else:
            with self.db.session_scope() as session:
                has_more = len(select_ids_fn(session, 1)) > 0

        return {
            "category": category,
            "eligible": deleted_total,
            "deleted": deleted_total,
            "would_delete": deleted_total,
            "batches": batches_ran,
            "truncated": has_more,
        }

    def get_status(self) -> Dict[str, Any]:
        last_run: Optional[Dict[str, Any]] = None
        with self.db.session_scope() as session:
            cur = session.get(V4WorkerCursorRecord, RETENTION_CURSOR_KEY)
            if cur is not None:
                last_run = dict(cur.metadata_json or {})
        if not self.cfg.enabled:
            status = "DISABLED"
        elif last_run and last_run.get("error"):
            status = "DEGRADED"
        else:
            status = "HEALTHY"
        return {
            "enabled": bool(self.cfg.enabled),
            "dry_run": bool(self.cfg.dry_run),
            "status": status,
            "run_interval_hours": int(self.cfg.run_interval_hours),
            "min_dedupe_safety_hours": int(self.cfg.min_dedupe_safety_hours),
            "batch_size": int(self.cfg.batch_size),
            "max_batches_per_run": int(self.cfg.max_batches_per_run),
            "protected_tables": list(self.PROTECTED_IMMUTABLE_TABLES),
            "last_run": last_run,
        }

    def run(
        self,
        *,
        dry_run: Optional[bool] = None,
        force: bool = False,
        actor: str = "retention-scheduler",
        now_override: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        now = _as_utc(now_override)
        eff_dry = bool(self.cfg.dry_run) if dry_run is None else bool(dry_run)

        if not self.cfg.enabled and not force:
            return {"enabled": False, "skipped": True, "reason": "RETENTION_DISABLED", "status": "DISABLED"}

        if not force:
            with self.db.session_scope() as session:
                cur = session.get(V4WorkerCursorRecord, RETENTION_CURSOR_KEY)
                if cur is not None and cur.cursor_value:
                    try:
                        prev_dt = _as_utc(datetime.fromisoformat(cur.cursor_value.replace("Z", "+00:00")))
                        elapsed_s = (now - prev_dt).total_seconds()
                        if elapsed_s < float(self.cfg.run_interval_hours) * 3600.0:
                            return {
                                "enabled": True,
                                "skipped": True,
                                "reason": "INTERVAL_NOT_ELAPSED",
                                "next_due_in_seconds": int(self.cfg.run_interval_hours * 3600 - elapsed_s),
                            }
                    except ValueError:
                        pass

        db_before = self.db.get_db_metrics()

        c_chat_msg = self._safe_cutoff(now, days=self.cfg.chat_messages_days, enforce_dedupe_safety=False)
        c_chat_sess = self._safe_cutoff(now, days=self.cfg.chat_sessions_days, enforce_dedupe_safety=False)
        c_del_att = self._safe_cutoff(now, days=self.cfg.delivery_attempts_days, enforce_dedupe_safety=True)
        c_outbox = self._safe_cutoff(now, days=self.cfg.terminal_outbox_days, enforce_dedupe_safety=True)
        c_inbox = self._safe_cutoff(now, days=self.cfg.inbox_processed_days, enforce_dedupe_safety=True)
        c_props = self._safe_cutoff(now, days=self.cfg.terminal_proposals_days, enforce_dedupe_safety=True)
        c_tasks = self._safe_cutoff(now, days=self.cfg.terminal_tasks_days, enforce_dedupe_safety=False)
        c_webhook = self._safe_cutoff(now, days=self.cfg.webhook_deliveries_days, enforce_dedupe_safety=True)
        c_events = self._safe_cutoff(now, days=self.cfg.event_store_days, enforce_dedupe_safety=True)
        c_receipts = self._safe_cutoff(now, days=self.cfg.event_receipts_days, enforce_dedupe_safety=True)

        # Strictly FK-safe child-first execution order (NO VACUUM!)
        categories: List[Dict[str, Any]] = []

        # 1. Chat messages (never delete messages belonging to active sessions active within retention)
        active_sess_sub = select(V4ChatSessionRecord.session_id).where(
            V4ChatSessionRecord.session_id == V4ChatMessageRecord.session_id,
            V4ChatSessionRecord.status == "ACTIVE",
            V4ChatSessionRecord.last_active_at >= c_chat_msg,
        )
        categories.append(
            self._run_batched_category(
                category="v4_chat_messages",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4ChatMessageRecord).where(
                        V4ChatMessageRecord.created_at < c_chat_msg, ~exists(active_sess_sub)
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4ChatMessageRecord.id)
                        .where(V4ChatMessageRecord.created_at < c_chat_msg, ~exists(active_sess_sub))
                        .order_by(V4ChatMessageRecord.created_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4ChatMessageRecord).where(V4ChatMessageRecord.id.in_(ids))
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        # 2. Closed/cleared/revoked chat sessions with no remaining messages
        rem_msg_sub = select(V4ChatMessageRecord.id).where(
            V4ChatMessageRecord.session_id == V4ChatSessionRecord.session_id
        )
        categories.append(
            self._run_batched_category(
                category="v4_chat_sessions",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4ChatSessionRecord).where(
                        V4ChatSessionRecord.status != "ACTIVE",
                        V4ChatSessionRecord.last_active_at < c_chat_sess,
                        ~exists(rem_msg_sub),
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4ChatSessionRecord.session_id)
                        .where(
                            V4ChatSessionRecord.status != "ACTIVE",
                            V4ChatSessionRecord.last_active_at < c_chat_sess,
                            ~exists(rem_msg_sub),
                        )
                        .order_by(V4ChatSessionRecord.last_active_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4ChatSessionRecord).where(V4ChatSessionRecord.session_id.in_(ids))
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        # 3. Delivery attempts (never delete IN_FLIGHT or attempts tied to active/retry/unknown outbox)
        active_outbox_sub = select(V4NotificationOutboxRecord.notification_id).where(
            V4NotificationOutboxRecord.notification_id == V4DeliveryAttemptRecord.outbox_id,
            V4NotificationOutboxRecord.status.in_(["PENDING", "CLAIMED", "FAILED_RETRYABLE", "UNKNOWN_DELIVERY"]),
        )
        categories.append(
            self._run_batched_category(
                category="v4_delivery_attempts",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4DeliveryAttemptRecord).where(
                        V4DeliveryAttemptRecord.started_at < c_del_att,
                        V4DeliveryAttemptRecord.result_class != "IN_FLIGHT",
                        ~exists(active_outbox_sub),
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4DeliveryAttemptRecord.attempt_id)
                        .where(
                            V4DeliveryAttemptRecord.started_at < c_del_att,
                            V4DeliveryAttemptRecord.result_class != "IN_FLIGHT",
                            ~exists(active_outbox_sub),
                        )
                        .order_by(V4DeliveryAttemptRecord.started_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4DeliveryAttemptRecord).where(V4DeliveryAttemptRecord.attempt_id.in_(ids))
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        # 4. Terminal outbox rows (NEVER deletes PENDING/CLAIMED/FAILED_RETRYABLE/UNKNOWN_DELIVERY)
        rem_att_sub = select(V4DeliveryAttemptRecord.attempt_id).where(
            V4DeliveryAttemptRecord.outbox_id == V4NotificationOutboxRecord.notification_id
        )
        blocking_att_sub = select(V4DeliveryAttemptRecord.attempt_id).where(
            V4DeliveryAttemptRecord.outbox_id == V4NotificationOutboxRecord.notification_id,
            ((V4DeliveryAttemptRecord.started_at >= c_del_att) | (V4DeliveryAttemptRecord.result_class == "IN_FLIGHT")),
        )
        terminal_outbox_statuses = ("SENT", "DLQ", "SUPPRESSED", "EXPIRED")
        categories.append(
            self._run_batched_category(
                category="v4_notification_outbox",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4NotificationOutboxRecord).where(
                        V4NotificationOutboxRecord.status.in_(terminal_outbox_statuses),
                        V4NotificationOutboxRecord.updated_at < c_outbox,
                        ~exists(blocking_att_sub),
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4NotificationOutboxRecord.notification_id)
                        .where(
                            V4NotificationOutboxRecord.status.in_(terminal_outbox_statuses),
                            V4NotificationOutboxRecord.updated_at < c_outbox,
                            ~exists(rem_att_sub),
                        )
                        .order_by(V4NotificationOutboxRecord.updated_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4NotificationOutboxRecord).where(
                        V4NotificationOutboxRecord.notification_id.in_(ids),
                        V4NotificationOutboxRecord.status.in_(terminal_outbox_statuses),
                    )
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        # 5. Terminal inbox messages (preserves RECEIVED/CLAIMED & safety dedupe window)
        categories.append(
            self._run_batched_category(
                category="v4_message_inbox",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4MessageInboxRecord).where(
                        V4MessageInboxRecord.state.in_(["PROCESSED", "FAILED", "IGNORED"]),
                        V4MessageInboxRecord.received_at < c_inbox,
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4MessageInboxRecord.id)
                        .where(
                            V4MessageInboxRecord.state.in_(["PROCESSED", "FAILED", "IGNORED"]),
                            V4MessageInboxRecord.received_at < c_inbox,
                        )
                        .order_by(V4MessageInboxRecord.received_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4MessageInboxRecord).where(
                        V4MessageInboxRecord.id.in_(ids),
                        V4MessageInboxRecord.state.in_(["PROCESSED", "FAILED", "IGNORED"]),
                    )
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        # 6. Terminal action proposals (preserves PROPOSED/PENDING_CONFIRMATION/CONFIRMED/EXECUTING)
        term_prop_states = ("SUCCEEDED", "REJECTED", "EXPIRED", "REVOKED", "FAILED", "UNKNOWN_OUTCOME")
        categories.append(
            self._run_batched_category(
                category="v4_action_proposals",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4ActionProposalRecord).where(
                        V4ActionProposalRecord.state.in_(term_prop_states),
                        V4ActionProposalRecord.updated_at < c_props,
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4ActionProposalRecord.action_id)
                        .where(
                            V4ActionProposalRecord.state.in_(term_prop_states),
                            V4ActionProposalRecord.updated_at < c_props,
                        )
                        .order_by(V4ActionProposalRecord.updated_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4ActionProposalRecord).where(
                        V4ActionProposalRecord.action_id.in_(ids),
                        V4ActionProposalRecord.state.in_(term_prop_states),
                    )
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        # 7. Terminal watch rules before terminal assistant tasks
        term_task_states = ("COMPLETED", "CANCELLED", "EXPIRED")
        categories.append(
            self._run_batched_category(
                category="v4_watch_rules",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4WatchRuleRecord).where(
                        V4WatchRuleRecord.status.in_(term_task_states),
                        V4WatchRuleRecord.updated_at < c_tasks,
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4WatchRuleRecord.rule_id)
                        .where(
                            V4WatchRuleRecord.status.in_(term_task_states),
                            V4WatchRuleRecord.updated_at < c_tasks,
                        )
                        .order_by(V4WatchRuleRecord.updated_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4WatchRuleRecord).where(
                        V4WatchRuleRecord.rule_id.in_(ids),
                        V4WatchRuleRecord.status.in_(term_task_states),
                    )
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        # 8. Terminal assistant tasks (NEVER deletes ACTIVE/WAITING_CONDITION/RUNNING)
        rem_watch_sub = select(V4WatchRuleRecord.rule_id).where(
            V4WatchRuleRecord.task_id == V4AssistantTaskRecord.task_id
        )
        blocking_watch_sub = select(V4WatchRuleRecord.rule_id).where(
            V4WatchRuleRecord.task_id == V4AssistantTaskRecord.task_id,
            ((~V4WatchRuleRecord.status.in_(term_task_states)) | (V4WatchRuleRecord.updated_at >= c_tasks)),
        )
        categories.append(
            self._run_batched_category(
                category="v4_assistant_tasks",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4AssistantTaskRecord).where(
                        V4AssistantTaskRecord.status.in_(term_task_states),
                        V4AssistantTaskRecord.updated_at < c_tasks,
                        ~exists(blocking_watch_sub),
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4AssistantTaskRecord.task_id)
                        .where(
                            V4AssistantTaskRecord.status.in_(term_task_states),
                            V4AssistantTaskRecord.updated_at < c_tasks,
                            ~exists(rem_watch_sub),
                        )
                        .order_by(V4AssistantTaskRecord.updated_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4AssistantTaskRecord).where(
                        V4AssistantTaskRecord.task_id.in_(ids),
                        V4AssistantTaskRecord.status.in_(term_task_states),
                    )
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        # 9. Webhook delivery dedupe records beyond retention & min_dedupe_safety_hours
        categories.append(
            self._run_batched_category(
                category="v4_webhook_deliveries",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4WebhookDeliveryRecord).where(
                        V4WebhookDeliveryRecord.received_at < c_webhook
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4WebhookDeliveryRecord.id)
                        .where(V4WebhookDeliveryRecord.received_at < c_webhook)
                        .order_by(V4WebhookDeliveryRecord.received_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4WebhookDeliveryRecord).where(V4WebhookDeliveryRecord.id.in_(ids))
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        # 10. Event store rows (ONLY consumed events past retention & dedupe safety; unconsumed preserved!)
        has_rcp_sub = select(V4EventConsumerReceiptRecord.id).where(
            V4EventConsumerReceiptRecord.event_id == V4EventStoreRecord.event_id
        )
        categories.append(
            self._run_batched_category(
                category="v4_event_store",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4EventStoreRecord).where(
                        V4EventStoreRecord.observed_at < c_events, exists(has_rcp_sub)
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4EventStoreRecord.event_id)
                        .where(V4EventStoreRecord.observed_at < c_events, exists(has_rcp_sub))
                        .order_by(V4EventStoreRecord.observed_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4EventStoreRecord).where(V4EventStoreRecord.event_id.in_(ids))
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        # 11. Consumer receipts (ONLY after parent event is removed from v4_event_store so replay protection is preserved!)
        parent_ev_sub = select(V4EventStoreRecord.event_id).where(
            V4EventStoreRecord.event_id == V4EventConsumerReceiptRecord.event_id
        )
        blocking_ev_sub = select(V4EventStoreRecord.event_id).where(
            V4EventStoreRecord.event_id == V4EventConsumerReceiptRecord.event_id,
            V4EventStoreRecord.observed_at >= c_events,
        )
        categories.append(
            self._run_batched_category(
                category="v4_event_consumer_receipts",
                count_fn=lambda s: s.execute(
                    select(func.count()).select_from(V4EventConsumerReceiptRecord).where(
                        V4EventConsumerReceiptRecord.processed_at < c_receipts,
                        ~exists(blocking_ev_sub if eff_dry else parent_ev_sub),
                    )
                ).scalar_one(),
                select_ids_fn=lambda s, lim: list(
                    s.execute(
                        select(V4EventConsumerReceiptRecord.id)
                        .where(V4EventConsumerReceiptRecord.processed_at < c_receipts, ~exists(parent_ev_sub))
                        .order_by(V4EventConsumerReceiptRecord.processed_at.asc())
                        .limit(lim)
                    )
                    .scalars()
                    .all()
                ),
                delete_ids_fn=lambda s, ids: s.execute(
                    delete(V4EventConsumerReceiptRecord).where(V4EventConsumerReceiptRecord.id.in_(ids))
                ).rowcount,
                dry_run=eff_dry,
            )
        )

        db_after = self.db.get_db_metrics()
        total_deleted = sum(int(c["deleted"]) for c in categories)
        total_would_delete = sum(int(c["would_delete"]) for c in categories)
        summary = {
            "enabled": bool(self.cfg.enabled),
            "dry_run": eff_dry,
            "executed_at": now.isoformat(),
            "total_deleted": total_deleted,
            "total_would_delete": total_would_delete,
            "categories": {c["category"]: c for c in categories},
            "db_bytes_before": db_before.get("total_bytes", 0),
            "db_bytes_after": db_after.get("total_bytes", 0),
            "db_size_bytes_after": db_after.get("db_size_bytes", 0),
            "wal_size_bytes_after": db_after.get("wal_size_bytes", 0),
            "vacuum_executed": False,
        }

        with self.db.session_scope() as session:
            cur = session.get(V4WorkerCursorRecord, RETENTION_CURSOR_KEY)
            if cur is None:
                session.add(
                    V4WorkerCursorRecord(
                        cursor_name=RETENTION_CURSOR_KEY,
                        cursor_value=now.isoformat(),
                        metadata_json=summary,
                        updated_at=now,
                    )
                )
            else:
                cur.cursor_value = now.isoformat()
                cur.metadata_json = summary
                cur.updated_at = now

            session.add(
                AuditEventRecord(
                    id=f"aud4-{uuid.uuid4().hex[:12]}",
                    timestamp=now.isoformat(),
                    actor=str(actor)[:128],
                    action="V4_RETENTION_MAINTENANCE",
                    target_type="DATABASE",
                    target_id="v4_tables",
                    result="DRY_RUN" if eff_dry else "EXECUTED",
                    correlation_id=f"ret-{int(now.timestamp())}",
                    details=redact_text(
                        json.dumps(
                            {
                                "dry_run": eff_dry,
                                "total_deleted": total_deleted,
                                "total_would_delete": total_would_delete,
                                "db_bytes_before": summary["db_bytes_before"],
                                "db_bytes_after": summary["db_bytes_after"],
                            },
                            ensure_ascii=False,
                        )
                    )[:1500],
                )
            )

        return summary
