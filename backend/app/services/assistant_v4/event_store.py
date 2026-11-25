"""
LabOps Personal AI Agent V4 — Durable Event Store, Typed Envelope & Reconciliation.
- Validated EventEnvelope schema with recursive secret/token redaction.
- Single-transaction event emission (`insert_event_in_session`) sharing caller unit-of-work.
- Idempotent consumer receipts (`v4_event_consumer_receipts`) with late/out-of-order tracking.
- Bounded watermark-based reconciliation (`EventReconciler`) without replaying stale closed items.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    IncidentRecord,
    ServerAccessRequestRecord,
    V4EventConsumerReceiptRecord,
    V4EventStoreRecord,
    V4WorkerCursorRecord,
    WorkspaceRecord,
)
from app.db.session import DatabaseManager

logger = logging.getLogger("labops.v4.events")

_SECRET_RE = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.:/+=]{8,}"),
    re.compile(r"(?i)(token|secret|password|api[_-]?key|authorization|bearer)\s*[:=]\s*['\"]?([A-Za-z0-9_\-.:/+=]{6,})['\"]?"),
    re.compile(r"\b(sk_live_|pk_live_|sk_test_|ghp_|gho_|glpat-|sk-)[A-Za-z0-9_\-]{6,}\b"),
    re.compile(r"https?://hc-ping\.com/[A-Za-z0-9\-]+", re.IGNORECASE),
]

_SENSITIVE_KEYS = {
    "token",
    "secret",
    "password",
    "passphrase",
    "private_key",
    "encrypted_private_key",
    "api_key",
    "bot_token",
    "invite_token",
    "invitation_token",
    "ping_url",
    "authorization",
    "nonce",
    "code",
}

# Authoritative Event Coverage Catalog (Spec §4 + Audit)
EVENT_CATALOG: Dict[str, Dict[str, Any]] = {
    "node.unreachable": {"status": "SUPPORTED", "source": "telemetry/incident_engine", "default_severity": "P0"},
    "node.recovered": {"status": "SUPPORTED", "source": "telemetry/incident_engine", "default_severity": "P1"},
    "telemetry.stale": {"status": "SUPPORTED", "source": "telemetry/incident_engine", "default_severity": "P1"},
    "gpu.temperature.high": {"status": "SUPPORTED", "source": "gpu/incident_engine", "default_severity": "P1"},
    "gpu.error": {"status": "SUPPORTED", "source": "gpu/incident_engine", "default_severity": "P0"},
    "gpu.missing": {"status": "SUPPORTED", "source": "gpu/telemetry_collector", "default_severity": "P0"},
    "gpu.throttle": {"status": "NOT_SUPPORTED", "source": "nvidia-smi clock throttle not parsed yet", "default_severity": "P1"},
    "resource.pressure": {"status": "SUPPORTED", "source": "telemetry_collector", "default_severity": "P1"},
    "storage.threshold": {"status": "SUPPORTED", "source": "storage/incident_engine", "default_severity": "P1"},
    "quota.exceeded": {"status": "SUPPORTED", "source": "workspace/telemetry", "default_severity": "P1"},
    "service.down": {"status": "SUPPORTED", "source": "services/incident_engine", "default_severity": "P0"},
    "service.recovered": {"status": "SUPPORTED", "source": "services/incident_engine", "default_severity": "P1"},
    "network.degraded": {"status": "NOT_CONFIGURED", "source": "no active read-only network latency probe configured", "default_severity": "P1"},
    "vpn.unavailable": {"status": "SUPPORTED", "source": "wg_easy_adapter", "default_severity": "P0"},
    "ssh.failure": {"status": "SUPPORTED", "source": "security/audit_events", "default_severity": "P1"},
    "backup.failed": {"status": "NOT_CONFIGURED", "source": "no backup job scheduler daemon configured", "default_severity": "P1"},
    "certificate.expiring": {"status": "NOT_CONFIGURED", "source": "external TLS managed by Cloudflare/reverse proxy", "default_severity": "P1"},
    "request.created": {"status": "SUPPORTED", "source": "mram_service", "default_severity": "P1"},
    "request.updated": {"status": "SUPPORTED", "source": "mram_service", "default_severity": "P2"},
    "request.approved": {"status": "SUPPORTED", "source": "mram_service", "default_severity": "P1"},
    "request.rejected": {"status": "SUPPORTED", "source": "mram_service", "default_severity": "P1"},
    "request.overdue": {"status": "SUPPORTED", "source": "mram_service", "default_severity": "P1"},
    "request.withdrawn": {"status": "NOT_SUPPORTED", "source": "no self-withdraw endpoint in core MRAM", "default_severity": "P2"},
    "provisioning.succeeded": {"status": "SUPPORTED", "source": "mram_service", "default_severity": "P1"},
    "provisioning.failed": {"status": "SUPPORTED", "source": "mram_service", "default_severity": "P0"},
    "workspace.expiring": {"status": "SUPPORTED", "source": "mram_service/member_profiles", "default_severity": "P2"},
    "workspace.expired": {"status": "SUPPORTED", "source": "mram_service", "default_severity": "P1"},
    "workspace.failed": {"status": "SUPPORTED", "source": "docker/incident_engine", "default_severity": "P1"},
    "job.started": {"status": "PLANNED", "source": "psutil pid polling (no scheduler SDK)", "default_severity": "P3"},
    "job.completed": {"status": "SUPPORTED", "source": "jobs/task_watch", "default_severity": "P2"},
    "job.failed": {"status": "SUPPORTED", "source": "jobs/incident_engine", "default_severity": "P1"},
    "job.stalled": {"status": "SUPPORTED", "source": "jobs/incident_engine", "default_severity": "P2"},
    "training.oom": {"status": "SUPPORTED", "source": "jobs/incident_engine", "default_severity": "P1"},
    "training.nan": {"status": "NOT_CONFIGURED", "source": "requires opt-in structured training SDK adapter", "default_severity": "P1"},
    "training.checkpoint": {"status": "NOT_CONFIGURED", "source": "requires opt-in structured training SDK adapter", "default_severity": "P3"},
    "security.denied": {"status": "SUPPORTED", "source": "agent_tool_audit/auth", "default_severity": "P1"},
    "security.secret_detected": {"status": "SUPPORTED", "source": "repository_guard", "default_severity": "P0"},
    "security.privilege_changed": {"status": "SUPPORTED", "source": "identity_rbac/audit", "default_severity": "P1"},
    "repository.cloned": {"status": "SUPPORTED", "source": "repository_guard", "default_severity": "P3"},
    "repository.scan_failed": {"status": "SUPPORTED", "source": "repository_guard", "default_severity": "P1"},
    "repository.risk_found": {"status": "SUPPORTED", "source": "repository_guard", "default_severity": "P1"},
    "repository.approved": {"status": "SUPPORTED", "source": "repository_guard", "default_severity": "P3"},
    "incident.created": {"status": "SUPPORTED", "source": "incident_engine", "default_severity": "P1"},
    "incident.reopened": {"status": "SUPPORTED", "source": "incident_engine", "default_severity": "P1"},
    "incident.escalated": {"status": "SUPPORTED", "source": "incident_engine", "default_severity": "P0"},
    "incident.acknowledged": {"status": "SUPPORTED", "source": "incident_engine", "default_severity": "P2"},
    "incident.resolved": {"status": "SUPPORTED", "source": "incident_engine", "default_severity": "P1"},
    "github.action_failed": {"status": "NOT_CONFIGURED", "source": "github connector disabled by default", "default_severity": "P2"},
    "github.review_requested": {"status": "NOT_CONFIGURED", "source": "github connector disabled by default", "default_severity": "P2"},
    "github.issue_assigned": {"status": "NOT_CONFIGURED", "source": "github connector disabled by default", "default_severity": "P2"},
    "mail.important": {"status": "NOT_CONFIGURED", "source": "personal_email connector disabled by default", "default_severity": "P2"},
    "calendar.upcoming": {"status": "NOT_CONFIGURED", "source": "personal_calendar connector disabled by default", "default_severity": "P2"},
    "research.update": {"status": "NOT_CONFIGURED", "source": "web_search connector disabled by default", "default_severity": "P3"},
    "task.due": {"status": "SUPPORTED", "source": "assistant_v4.task_scheduler", "default_severity": "P2"},
    "task.condition_met": {"status": "SUPPORTED", "source": "assistant_v4.task_scheduler", "default_severity": "P1"},
    "task.failed": {"status": "SUPPORTED", "source": "assistant_v4.task_scheduler", "default_severity": "P1"},
    "notification.failed": {"status": "SUPPORTED", "source": "assistant_v4.outbox", "default_severity": "P1"},
    "worker.stalled": {"status": "SUPPORTED", "source": "assistant_v4.watchdog", "default_severity": "P0"},
    "ai.unavailable": {"status": "SUPPORTED", "source": "assistant_v4.agent_runtime", "default_severity": "P2"},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def redact_text(value: str) -> str:
    cleaned = str(value or "")
    for pat in _SECRET_RE:
        cleaned = pat.sub("[REDACTED]", cleaned)
    return cleaned


def redact_payload(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _SENSITIVE_KEYS or kl.endswith(("_secret", "_token", "_password", "_key_pem")):
                out[str(k)] = "[REDACTED]"
            else:
                out[str(k)] = redact_payload(v)
        return out
    if isinstance(obj, list):
        return [redact_payload(x) for x in obj[:50]]
    return obj


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: f"ev4-{uuid.uuid4().hex[:12]}")
    source: str = Field(min_length=1, max_length=64)
    source_event_id: str = Field(min_length=1, max_length=200)
    schema_version: int = Field(default=1, ge=1)
    event_type: str = Field(min_length=1, max_length=96)
    tenant_id: str = Field(default="aiot-lab", min_length=1, max_length=64)
    scope_type: str = Field(default="lab", min_length=1, max_length=32)
    scope_id: str = Field(default="global", min_length=1, max_length=128)
    resource_id: str = Field(min_length=1, max_length=255)
    actor_id: Optional[str] = Field(default=None, max_length=128)
    source_version: int = Field(default=1, ge=0)
    occurred_at: datetime = Field(default_factory=_utcnow)
    observed_at: datetime = Field(default_factory=_utcnow)
    correlation_id: str = Field(default_factory=lambda: f"corr-{uuid.uuid4().hex[:10]}", max_length=96)
    payload: Dict[str, Any] = Field(default_factory=dict)


def _serialize_event(row: V4EventStoreRecord) -> Dict[str, Any]:
    return {
        "event_id": row.event_id,
        "source": row.source,
        "source_event_id": row.source_event_id,
        "schema_version": row.schema_version,
        "event_type": row.event_type,
        "tenant_id": row.tenant_id,
        "scope_type": row.scope_type,
        "scope_id": row.scope_id,
        "resource_id": row.resource_id,
        "actor_id": row.actor_id,
        "source_version": row.source_version,
        "occurred_at": _as_utc(row.occurred_at).isoformat(),
        "observed_at": _as_utc(row.observed_at).isoformat(),
        "payload": dict(row.payload_redacted_json or {}),
        "correlation_id": row.correlation_id,
        "created_at": _as_utc(row.created_at).isoformat(),
    }


def insert_event_in_session(session: Session, envelope: EventEnvelope) -> Tuple[str, bool]:
    """
    Insert a validated EventEnvelope inside caller's existing DB transaction.
    Deduplicates on UNIQUE(source, source_event_id) without breaking outer atomic tx.
    Returns (event_id, created_bool).
    """
    existing = session.execute(
        select(V4EventStoreRecord).where(
            V4EventStoreRecord.source == envelope.source,
            V4EventStoreRecord.source_event_id == envelope.source_event_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.event_id, False

    redacted = redact_payload(envelope.payload)
    now = _utcnow()
    rec = V4EventStoreRecord(
        event_id=envelope.event_id,
        source=envelope.source,
        source_event_id=envelope.source_event_id,
        schema_version=envelope.schema_version,
        event_type=envelope.event_type,
        tenant_id=envelope.tenant_id,
        scope_type=envelope.scope_type,
        scope_id=envelope.scope_id,
        resource_id=envelope.resource_id,
        actor_id=envelope.actor_id,
        source_version=envelope.source_version,
        occurred_at=_as_utc(envelope.occurred_at),
        observed_at=_as_utc(envelope.observed_at),
        payload_redacted_json=redacted if isinstance(redacted, dict) else {"value": redacted},
        correlation_id=envelope.correlation_id,
        created_at=now,
    )
    session.add(rec)
    session.flush()
    return rec.event_id, True


class EventStoreService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def emit(self, envelope: EventEnvelope) -> Tuple[str, bool]:
        with self.db.session_scope() as session:
            return insert_event_in_session(session, envelope)

    def insert_in_tx(self, session: Session, envelope: EventEnvelope) -> Tuple[str, bool]:
        return insert_event_in_session(session, envelope)

    def list_events(self, *, event_type: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(V4EventStoreRecord)
            if event_type:
                stmt = stmt.where(V4EventStoreRecord.event_type == event_type)
            stmt = stmt.order_by(V4EventStoreRecord.observed_at.desc()).limit(max(1, min(limit, 500)))
            rows = session.execute(stmt).scalars().all()
            return [_serialize_event(r) for r in rows]

    def get_consumer_lag(
        self,
        consumer: str = "notification-policy-v4",
        *,
        now_override: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Return total event counts, unconsumed backlog count, and processing lag in seconds."""
        now = _as_utc(now_override)
        with self.db.session_scope() as session:
            total_events = int(session.execute(select(func.count()).select_from(V4EventStoreRecord)).scalar_one() or 0)
            total_receipts = int(
                session.execute(
                    select(func.count())
                    .select_from(V4EventConsumerReceiptRecord)
                    .where(V4EventConsumerReceiptRecord.consumer == consumer)
                ).scalar_one()
                or 0
            )
            already_sub = select(V4EventConsumerReceiptRecord.event_id).where(
                V4EventConsumerReceiptRecord.consumer == consumer,
                V4EventConsumerReceiptRecord.event_id == V4EventStoreRecord.event_id,
            )
            unprocessed_count = int(
                session.execute(select(func.count()).select_from(V4EventStoreRecord).where(~exists(already_sub))).scalar_one()
                or 0
            )
            oldest_obs = (
                session.execute(
                    select(V4EventStoreRecord.observed_at)
                    .where(~exists(already_sub))
                    .order_by(V4EventStoreRecord.observed_at.asc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            lag_s = round(max(0.0, (now - _as_utc(oldest_obs)).total_seconds()), 2) if oldest_obs is not None else 0.0
            return {
                "consumer": consumer,
                "total_events": total_events,
                "total_receipts": total_receipts,
                "unprocessed_event_count": unprocessed_count,
                "event_processing_lag_seconds": lag_s,
            }

    def process_pending(
        self,
        consumer: str,
        handler: Callable[[Session, Dict[str, Any], bool], str],
        *,
        batch_size: int = 50,
    ) -> Dict[str, int]:
        """
        Atomically claim & apply unconsumed events for `consumer` with per-event TX isolation.
        Enforces idempotency via v4_event_consumer_receipts UNIQUE(consumer, event_id)
        and detects late/out-of-order events by comparing (source_version, observed_at).
        """
        stats = {"checked": 0, "processed": 0, "late": 0, "duplicates": 0}
        with self.db.session_scope() as session:
            already_sub = (
                select(V4EventConsumerReceiptRecord.event_id)
                .where(
                    V4EventConsumerReceiptRecord.consumer == consumer,
                    V4EventConsumerReceiptRecord.event_id == V4EventStoreRecord.event_id,
                )
            )
            candidate_ids = list(
                session.execute(
                    select(V4EventStoreRecord.event_id)
                    .where(~exists(already_sub))
                    .order_by(V4EventStoreRecord.observed_at.asc(), V4EventStoreRecord.created_at.asc())
                    .limit(max(1, min(batch_size, 200)))
                )
                .scalars()
                .all()
            )
        stats["checked"] = len(candidate_ids)

        for event_id in candidate_ids:
            with self.db.session_scope() as session:
                ev = session.get(V4EventStoreRecord, event_id)
                if ev is None:
                    continue
                dup = session.execute(
                    select(V4EventConsumerReceiptRecord.id).where(
                        V4EventConsumerReceiptRecord.consumer == consumer,
                        V4EventConsumerReceiptRecord.event_id == ev.event_id,
                    )
                ).scalar_one_or_none()
                if dup is not None:
                    stats["duplicates"] += 1
                    continue

                latest_for_resource = (
                    session.execute(
                        select(V4EventConsumerReceiptRecord)
                        .where(
                            and_(
                                V4EventConsumerReceiptRecord.consumer == consumer,
                                V4EventConsumerReceiptRecord.resource_id == ev.resource_id,
                            )
                        )
                        .order_by(V4EventConsumerReceiptRecord.observed_at.desc())
                    )
                    .scalars()
                    .first()
                )
                is_late = False
                if latest_for_resource is not None:
                    prev_obs = _as_utc(latest_for_resource.observed_at)
                    cur_obs = _as_utc(ev.observed_at)
                    if ev.source_version < latest_for_resource.source_version or (
                        ev.source_version == latest_for_resource.source_version and cur_obs < prev_obs
                    ):
                        is_late = True
                        stats["late"] += 1

                ev_dict = _serialize_event(ev)
                status = handler(session, ev_dict, is_late) or ("LATE_IGNORED" if is_late else "PROCESSED")

                session.add(
                    V4EventConsumerReceiptRecord(
                        id=f"evrcp-{uuid.uuid4().hex[:12]}",
                        consumer=consumer,
                        event_id=ev.event_id,
                        source=ev.source,
                        source_event_id=ev.source_event_id,
                        resource_id=ev.resource_id,
                        source_version=ev.source_version,
                        observed_at=_as_utc(ev.observed_at),
                        result_status=status,
                        processed_at=_utcnow(),
                    )
                )
                session.flush()
                stats["processed"] += 1
        return stats


class EventReconciler:
    """Bounded cursor-tracked reconciler comparing core DB states with event_store."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def _get_cursor(self, session: Session, name: str) -> str:
        row = session.get(V4WorkerCursorRecord, name)
        return row.cursor_value if row is not None else ""

    def _set_cursor(self, session: Session, name: str, value: str, meta: Optional[Dict[str, Any]] = None) -> None:
        now = _utcnow()
        row = session.get(V4WorkerCursorRecord, name)
        if row is None:
            session.add(
                V4WorkerCursorRecord(
                    cursor_name=name,
                    cursor_value=str(value),
                    metadata_json=dict(meta or {}),
                    updated_at=now,
                )
            )
        else:
            row.cursor_value = str(value)
            if meta is not None:
                row.metadata_json = dict(meta)
            row.updated_at = now

    def reconcile(self, *, batch_size: int = 50) -> Dict[str, int]:
        """
        Scan currently active/open requests, incidents, and unhealthy workspaces in bounded batches.
        Backfills missing durable events with stable source_event_id WITHOUT replaying closed historical records.
        """
        backfilled = 0
        scanned = 0
        now = _utcnow()
        with self.db.session_scope() as session:
            # 1. Open or overdue SSH requests
            req_cursor = self._get_cursor(session, "reconcile:requests")
            req_stmt = (
                select(ServerAccessRequestRecord)
                .where(ServerAccessRequestRecord.status.in_(["PENDING_APPROVAL", "NEED_INFO", "EXPIRED"]))
                .order_by(ServerAccessRequestRecord.id.asc())
                .limit(batch_size)
            )
            if req_cursor:
                req_stmt = req_stmt.where(ServerAccessRequestRecord.id > req_cursor)
            reqs = session.execute(req_stmt).scalars().all()
            if not reqs and req_cursor:
                # Wrap cleanly from beginning for open records only
                reqs = (
                    session.execute(
                        select(ServerAccessRequestRecord)
                        .where(ServerAccessRequestRecord.status.in_(["PENDING_APPROVAL", "NEED_INFO", "EXPIRED"]))
                        .order_by(ServerAccessRequestRecord.id.asc())
                        .limit(batch_size)
                    )
                    .scalars()
                    .all()
                )
            for req in reqs:
                scanned += 1
                if req.status in {"PENDING_APPROVAL", "NEED_INFO"}:
                    ev_type = "request.created" if req.version == 1 else "request.updated"
                else:
                    ev_type = "request.overdue"
                src_eid = f"request:{req.id}:v{req.version}:{ev_type.split('.')[-1]}"
                _, created = insert_event_in_session(
                    session,
                    EventEnvelope(
                        source="labops-core",
                        source_event_id=src_eid,
                        event_type=ev_type,
                        scope_type="user",
                        scope_id=req.username,
                        resource_id=req.id,
                        actor_id=req.username,
                        source_version=req.version,
                        occurred_at=_as_utc(req.updated_at or req.submitted_at),
                        observed_at=now,
                        correlation_id=req.id,
                        payload={
                            "request_id": req.id,
                            "username": req.username,
                            "status": req.status,
                            "supervisor_id": req.primary_supervisor_id,
                            "project_title": req.project_title,
                            "reason_snippet": (req.registration_reason or "")[:180],
                            "submitted_at": _as_utc(req.submitted_at).isoformat(),
                            "review_expires_at": _as_utc(req.review_expires_at).isoformat(),
                            "review_path": f"/dashboard/ssh-requests/{req.id}/review",
                            "reconciled": True,
                        },
                    ),
                )
                if created:
                    backfilled += 1
            if reqs:
                self._set_cursor(session, "reconcile:requests", reqs[-1].id, {"count": len(reqs)})

            # 2. Active non-resolved incidents ONLY (never replay closed RESOLVED incidents!)
            inc_rows = (
                session.execute(
                    select(IncidentRecord)
                    .where(IncidentRecord.status.in_(["OPEN", "ACKNOWLEDGED", "INVESTIGATING"]))
                    .order_by(IncidentRecord.detected_at.desc())
                    .limit(batch_size)
                )
                .scalars()
                .all()
            )
            for inc in inc_rows:
                scanned += 1
                ep = max(1, int(inc.reopen_count or 0) + 1)
                ev_type = "incident.reopened" if ep > 1 else "incident.created"
                src_eid = f"incident:{inc.incident_id}:ep{ep}:{ev_type.split('.')[-1]}"
                _, created = insert_event_in_session(
                    session,
                    EventEnvelope(
                        source="incident-engine",
                        source_event_id=src_eid,
                        event_type=ev_type,
                        scope_type="user" if inc.owner_id else "lab",
                        scope_id=inc.owner_id or (inc.host_id or "global"),
                        resource_id=inc.incident_id,
                        actor_id=inc.owner_id or "sre_engine",
                        source_version=ep,
                        occurred_at=_as_utc(inc.detected_at),
                        observed_at=now,
                        correlation_id=inc.correlation_id or inc.incident_id,
                        payload={
                            "incident_id": inc.incident_id,
                            "incident_event_type": inc.event_type,
                            "severity": inc.severity,
                            "confidence": inc.confidence,
                            "status": inc.status,
                            "source": inc.source,
                            "resource_id": inc.resource_id,
                            "host_id": inc.host_id,
                            "owner_id": inc.owner_id,
                            "title": inc.title,
                            "recommended_action": inc.recommended_action,
                            "reconciled": True,
                        },
                    ),
                )
                if created:
                    backfilled += 1

            # 3. Failed workspaces if recorded in DB
            failed_ws = (
                session.execute(
                    select(WorkspaceRecord)
                    .where(WorkspaceRecord.lifecycle_state.in_(["FAILED", "PARTIAL_FAILURE"]))
                    .limit(batch_size)
                )
                .scalars()
                .all()
            )
            for ws in failed_ws:
                scanned += 1
                src_eid = f"workspace:{ws.id}:failed:{int(_as_utc(ws.updated_at).timestamp())}"
                # Check any prior workspace.failed for same ws_id so reconcile doesn't spam
                existing_ws_ev = session.execute(
                    select(V4EventStoreRecord.event_id).where(
                        V4EventStoreRecord.resource_id == ws.id,
                        V4EventStoreRecord.event_type == "workspace.failed",
                    )
                ).scalar_one_or_none()
                if existing_ws_ev is None:
                    _, created = insert_event_in_session(
                        session,
                        EventEnvelope(
                            source="workspace-controller",
                            source_event_id=src_eid,
                            event_type="workspace.failed",
                            scope_type="user",
                            scope_id=ws.owner,
                            resource_id=ws.id,
                            actor_id=ws.owner,
                            source_version=1,
                            occurred_at=_as_utc(ws.updated_at),
                            observed_at=now,
                            correlation_id=ws.id,
                            payload={"workspace_id": ws.id, "name": ws.name, "owner": ws.owner, "reconciled": True},
                        ),
                    )
                    if created:
                        backfilled += 1

        return {"scanned": scanned, "backfilled": backfilled}
