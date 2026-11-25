"""
LabOps Personal AI Agent V4 — Deterministic Notification Policy, Correlation,
Incident Recovery/Repeat Engine, Request SLA Scheduling & Quota Governor.
Works completely independently of any LLM / 9Router availability.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AuditEventRecord,
    IncidentRecord,
    MemberProfileRecord,
    NotificationOutboxRecord,
    NotificationSubscriptionRecord,
    ReportRunRecord,
    ServerAccessRequestRecord,
    SupervisorAssignmentRecord,
    V4AssistantTaskRecord,
    V4EventConsumerReceiptRecord,
    V4EventStoreRecord,
    V4IncidentMetaRecord,
    V4NotificationOutboxRecord,
    V4WorkerCursorRecord,
    ZaloIdentityLinkRecord,
)
from app.db.session import DatabaseManager
from app.services.assistant_v4.config import NotificationsV4Config, SchedulerV4Config
from app.services.assistant_v4.event_store import EventEnvelope, EventStoreService, insert_event_in_session, redact_text
from app.services.assistant_v4.inbox_outbox import OutboxV4Service, chunk_zalo_message

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CUTOVER_CURSOR_KEY = "notification_cutover:state"
SHADOW_METRICS_KEY = "shadow:policy:metrics"


def resolve_notification_owner(config: Any) -> str:
    """Authoritative mapping from V4 config flags to effective external notification owner."""
    if config is None or not getattr(config, "enabled", False):
        return "V2_LEGACY"
    if getattr(config, "shadow_mode", False):
        return "V2_LEGACY_SHADOW"
    if getattr(config, "notification_v2", False):
        return "V4_ACTIVE"
    return "V2_LEGACY"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_vn(iso_or_dt: Any) -> str:
    if isinstance(iso_or_dt, datetime):
        return _as_utc(iso_or_dt).astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y (ICT)")
    if isinstance(iso_or_dt, str) and iso_or_dt.strip():
        try:
            dt = datetime.fromisoformat(iso_or_dt.strip().replace("Z", "+00:00"))
            return _as_utc(dt).astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y (ICT)")
        except ValueError:
            return iso_or_dt[:19]
    return "N/A"


def _public_base_url() -> str:
    base = (
        os.getenv("LABOPS_PUBLIC_DASHBOARD_URL")
        or os.getenv("NEXT_PUBLIC_APP_URL")
        or "https://aiotserver.fitdnu.id.vn"
    ).strip().rstrip("/")
    if "localhost" in base.lower() or "127.0.0.1" in base:
        return "https://aiotserver.fitdnu.id.vn"
    return base


class QuotaGovernorV4:
    """
    Quota & priority governor for V4 notifications.
    Counts every chunk and recipient, reserves emergency P0 quota,
    sheds/coalesces low-priority P2/P3 into digest near soft/high thresholds,
    and enqueues fallback channel visibility on hard provider caps.
    """

    def __init__(self, db_manager: DatabaseManager, cfg: Optional[NotificationsV4Config] = None):
        self.db = db_manager
        self.cfg = cfg or NotificationsV4Config()

    def _cursor_key(self, now: datetime) -> str:
        return f"quota:v4:{now.strftime('%Y-%m')}"

    def get_usage(self, session: Session, *, now_override: Optional[datetime] = None) -> Dict[str, Any]:
        now = _as_utc(now_override)
        key = self._cursor_key(now)
        row = session.get(V4WorkerCursorRecord, key)
        meta = dict(row.metadata_json or {}) if row else {}
        std = int(meta.get("standard_sent", 0))
        crit = int(meta.get("critical_sent", 0))
        shed = int(meta.get("shed_count", 0))
        total = std + crit
        budget = max(1, int(self.cfg.monthly_budget))
        reserve = min(int(self.cfg.critical_reserve), budget)
        std_limit = max(0, budget - reserve)
        pct = round((total / budget) * 100.0, 2)
        return {
            "month": now.strftime("%Y-%m"),
            "monthly_budget": budget,
            "critical_reserve": reserve,
            "standard_limit": std_limit,
            "standard_sent": std,
            "critical_sent": crit,
            "total_sent": total,
            "percent_used": pct,
            "soft_threshold_reached": pct >= self.cfg.soft_percent,
            "high_threshold_reached": pct >= self.cfg.high_percent,
            "budget_exhausted": total >= budget,
            "shed_count": shed,
            "digest_queue": list(meta.get("digest_queue") or []),
            "provider_confirmed": False,
        }

    def record_usage(
        self,
        session: Session,
        *,
        is_critical: bool,
        units: int = 1,
        now_override: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        now = _as_utc(now_override)
        key = self._cursor_key(now)
        row = session.get(V4WorkerCursorRecord, key)
        if row is None:
            row = V4WorkerCursorRecord(cursor_name=key, cursor_value="0", metadata_json={}, updated_at=now)
            session.add(row)
        meta = dict(row.metadata_json or {})
        if is_critical:
            meta["critical_sent"] = int(meta.get("critical_sent", 0)) + units
        else:
            meta["standard_sent"] = int(meta.get("standard_sent", 0)) + units
        meta["total_sent"] = int(meta.get("standard_sent", 0)) + int(meta.get("critical_sent", 0))
        row.cursor_value = str(meta["total_sent"])
        row.metadata_json = meta
        row.updated_at = now
        session.flush()
        return self.get_usage(session, now_override=now)

    def add_shed_digest_item(
        self,
        session: Session,
        *,
        summary_line: str,
        now_override: Optional[datetime] = None,
    ) -> None:
        now = _as_utc(now_override)
        key = self._cursor_key(now)
        row = session.get(V4WorkerCursorRecord, key)
        if row is None:
            row = V4WorkerCursorRecord(cursor_name=key, cursor_value="0", metadata_json={}, updated_at=now)
            session.add(row)
        meta = dict(row.metadata_json or {})
        meta["shed_count"] = int(meta.get("shed_count", 0)) + 1
        dq = list(meta.get("digest_queue") or [])
        dq.append(summary_line[:180])
        meta["digest_queue"] = dq[-100:]
        row.metadata_json = meta
        row.updated_at = now

    def decide_delivery(
        self,
        session: Session,
        *,
        severity: str,
        actionable: bool,
        chunk_count: int = 1,
        now_override: Optional[datetime] = None,
    ) -> str:
        """
        Return action:
          'SEND_ZALO' | 'COALESCE_DIGEST' | 'FALLBACK_ONLY'
        P0 never gets blocked by soft limits; hard exhaustion routes to fallback_channel.
        """
        sev = str(severity or "P1").upper()
        usage = self.get_usage(session, now_override=now_override)
        if usage["budget_exhausted"]:
            return "FALLBACK_ONLY"
        if sev == "P0":
            return "SEND_ZALO"
        if usage["standard_sent"] + chunk_count > usage["standard_limit"]:
            return "FALLBACK_ONLY" if actionable else "COALESCE_DIGEST"
        if sev in {"P2", "P3"} and usage["soft_threshold_reached"]:
            return "COALESCE_DIGEST"
        if sev == "P1" and not actionable and usage["high_threshold_reached"]:
            return "COALESCE_DIGEST"
        return "SEND_ZALO"


class DependencyCorrelator:
    """
    Correlates dependent infrastructure failures using topological host parent -> child dependencies
    without EVER inhibiting unrelated critical or security faults.
    """

    DEPENDENT_ON_NODE_DOWN = {
        "NODE_HEARTBEAT_STALE",
        "GPU_MISSING",
        "GPU_STALE",
        "TELEMETRY_STALE",
        "telemetry.stale",
        "gpu.missing",
    }
    NEVER_CORRELATE_SUPPRESS = {
        "REPO_SECURITY_FINDINGS",
        "SECURITY_SECRET_DETECTED",
        "ISOLATION_POLICY_VIOLATION",
        "FILESYSTEM_ERROR",
        "GPU_HARDWARE_FAULT",
        "DOCKER_OOM_KILLED",
        "POSTGRESQL_UNAVAILABLE",
    }

    def attach_and_check_parent(
        self,
        session: Session,
        *,
        incident_id: str,
        event_type: str,
        host_id: Optional[str],
        source: str,
    ) -> Optional[str]:
        """Returns parent_incident_id if this event is a dependent symptom of an active node outage."""
        if not host_id or event_type in self.NEVER_CORRELATE_SUPPRESS:
            return None
        if event_type not in self.DEPENDENT_ON_NODE_DOWN:
            return None

        root_node_inc = (
            session.execute(
                select(IncidentRecord).where(
                    IncidentRecord.host_id == host_id,
                    IncidentRecord.event_type == "NODE_HEARTBEAT_LOST",
                    IncidentRecord.status.in_(["OPEN", "ACKNOWLEDGED", "INVESTIGATING"]),
                    IncidentRecord.incident_id != incident_id,
                )
            )
            .scalars()
            .first()
        )
        if root_node_inc is None:
            return None

        meta = session.get(V4IncidentMetaRecord, incident_id)
        if meta is None:
            meta = V4IncidentMetaRecord(
                incident_id=incident_id,
                parent_incident_id=root_node_inc.incident_id,
                correlation_group_key=f"host:{host_id}",
                suppression_reason=f"Correlated symptom of {root_node_inc.incident_id} ({host_id} down)",
                metadata_json={"source": source},
            )
            session.add(meta)
        else:
            meta.parent_incident_id = root_node_inc.incident_id
            meta.correlation_group_key = f"host:{host_id}"
            meta.suppression_reason = f"Correlated symptom of {root_node_inc.incident_id}"
        session.flush()
        return root_node_inc.incident_id


class IncidentLifecycleV4:
    """
    Deterministic incident recovery verifier and reminder scheduler.
    Enforces: missing/stale telemetry NEVER confirms recovery!
    """

    def __init__(self, cfg: Optional[NotificationsV4Config] = None):
        self.cfg = cfg or NotificationsV4Config()

    def next_reminder_delta(self, *, severity: str, status: str, detected_at: datetime, now: datetime) -> Optional[timedelta]:
        sev = str(severity or "P1").upper()
        st = str(status or "OPEN").upper()
        if st == "RESOLVED":
            return None
        age = now - _as_utc(detected_at)
        if sev == "P0":
            if st == "ACKNOWLEDGED":
                return timedelta(minutes=self.cfg.p0_acked_repeat_minutes)
            return timedelta(minutes=self.cfg.p0_unacked_repeat_minutes)
        if sev == "P1":
            if age >= timedelta(days=3):
                return timedelta(hours=self.cfg.p1_stale_repeat_hours)
            return timedelta(hours=self.cfg.p1_repeat_hours)
        if sev == "P2":
            return timedelta(hours=self.cfg.p2_repeat_hours)
        return None

    def evaluate_recovery_from_snapshot(
        self,
        session: Session,
        snapshot: Dict[str, Any],
        *,
        now_override: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Auto-resolve incidents ONLY when fresh verified telemetry proves healthy recovery."""
        now = _as_utc(now_override)
        hosts_by_id = {str(h.get("host_id") or ""): h for h in (snapshot.get("hosts") or [])}
        gpus_by_key = {str(g.get("gpu_key") or ""): g for g in (snapshot.get("gpus") or [])}
        recovered: List[Dict[str, Any]] = []

        open_rows = (
            session.execute(
                select(IncidentRecord).where(
                    IncidentRecord.status.in_(["OPEN", "ACKNOWLEDGED", "INVESTIGATING"]),
                    IncidentRecord.event_type.in_(["NODE_HEARTBEAT_LOST", "NODE_HEARTBEAT_STALE", "GPU_THERMAL_HIGH", "STORAGE_HIGH_USAGE", "STORAGE_CRITICAL"]),
                )
            )
            .scalars()
            .all()
        )
        for inc in open_rows:
            is_healthy = False
            ev_summary = ""
            if inc.event_type in {"NODE_HEARTBEAT_LOST", "NODE_HEARTBEAT_STALE"}:
                h = hosts_by_id.get(str(inc.host_id or inc.resource_id))
                # Freshness MUST be strictly ONLINE — UNKNOWN/STALE/OFFLINE never recovers!
                if h and str(h.get("freshness_status") or "").upper() == "ONLINE" and str(h.get("status") or "").upper() == "ONLINE":
                    is_healthy = True
                    ev_summary = f"Node {inc.resource_id} heartbeat fresh (ONLINE)"
            elif inc.event_type == "GPU_THERMAL_HIGH":
                g = gpus_by_key.get(str(inc.resource_id))
                if g and g.get("host_online") and g.get("temp_celsius") is not None:
                    h = hosts_by_id.get(str(g.get("host_id") or ""))
                    if h and str(h.get("freshness_status") or "").upper() == "ONLINE" and float(g["temp_celsius"]) < 80.0:
                        is_healthy = True
                        ev_summary = f"GPU temp {g['temp_celsius']}°C < 80°C on ONLINE host"
            elif inc.event_type in {"STORAGE_HIGH_USAGE", "STORAGE_CRITICAL"}:
                h = hosts_by_id.get(str(inc.host_id or ""))
                if h and str(h.get("freshness_status") or "").upper() == "ONLINE" and h.get("disk_usage_percent") is not None:
                    if float(h["disk_usage_percent"]) < 80.0:
                        is_healthy = True
                        ev_summary = f"Disk usage dropped to {h['disk_usage_percent']}%"

            if is_healthy:
                inc.status = "RESOLVED"
                inc.resolved_by = "telemetry-recovery-verifier"
                inc.resolved_at = now
                inc.updated_at = now
                ep = max(1, int(inc.reopen_count or 0) + 1)
                insert_event_in_session(
                    session,
                    EventEnvelope(
                        source="incident-engine",
                        source_event_id=f"incident:{inc.incident_id}:ep{ep}:resolved_auto:{int(now.timestamp())}",
                        event_type="incident.resolved",
                        scope_type="lab",
                        scope_id=inc.host_id or "global",
                        resource_id=inc.incident_id,
                        actor_id="telemetry-recovery-verifier",
                        source_version=ep,
                        occurred_at=now,
                        observed_at=now,
                        correlation_id=inc.correlation_id or inc.incident_id,
                        payload={
                            "incident_id": inc.incident_id,
                            "incident_event_type": inc.event_type,
                            "severity": inc.severity,
                            "resource_id": inc.resource_id,
                            "host_id": inc.host_id,
                            "owner_id": inc.owner_id,
                            "title": inc.title,
                            "recovery_proof": ev_summary,
                        },
                    ),
                )
                recovered.append({"incident_id": inc.incident_id, "proof": ev_summary})
        return recovered


class EventPolicyEngine:
    """
    Deterministic event consumer mapping v4_event_store events into:
    - Immediate priority outbox notifications (Zalo + fallback visibility)
    - Scheduled Request SLA reminders (12h, 22h, 24h) + cancellation on resolve/decision
    - Incident dependency correlation & scheduled reminders
    - Shadow mode evaluation (records decisions without enqueueing real outbound rows)
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        cfg: Optional[NotificationsV4Config] = None,
        shadow_mode: bool = False,
        fallback_admin_ids: Optional[List[str]] = None,
    ):
        self.db = db_manager
        self.cfg = cfg or NotificationsV4Config()
        self.shadow_mode = shadow_mode
        self.fallback_admin_ids = list(fallback_admin_ids or [])
        self.events = EventStoreService(db_manager)
        self.outbox = OutboxV4Service(db_manager, target_chars=self.cfg.target_chars)
        self.quota = QuotaGovernorV4(db_manager, self.cfg)
        self.correlator = DependencyCorrelator()
        self.lifecycle = IncidentLifecycleV4(self.cfg)

    @staticmethod
    def _is_in_quiet_hours(sub: Optional[NotificationSubscriptionRecord], now_utc: datetime) -> bool:
        if sub is None or not sub.quiet_hours_start or not sub.quiet_hours_end:
            return False
        try:
            sh, sm = [int(x) for x in str(sub.quiet_hours_start).strip().split(":", 1)]
            eh, em = [int(x) for x in str(sub.quiet_hours_end).strip().split(":", 1)]
        except Exception:
            return False
        local = _as_utc(now_utc).astimezone(VN_TZ)
        cur_m = local.hour * 60 + local.minute
        start_m = sh * 60 + sm
        end_m = eh * 60 + em
        if start_m == end_m:
            return False
        if start_m < end_m:
            return start_m <= cur_m < end_m
        return cur_m >= start_m or cur_m < end_m

    @staticmethod
    def _allows_category(sub: Optional[NotificationSubscriptionRecord], *, severity: str, category: str = "") -> bool:
        if sub is None:
            return True
        if not sub.enabled:
            return False
        cat = str(category or "").strip().lower()
        sev = str(severity or "P1").upper()
        if cat == "repo":
            return bool(sub.receive_repo_alerts)
        if cat == "daily":
            return bool(sub.receive_daily_report)
        if cat == "weekly":
            return bool(sub.receive_weekly_report)
        if cat == "p0" or sev == "P0":
            return bool(sub.receive_p0)
        if cat == "p1" or sev == "P1":
            return bool(sub.receive_p1)
        return True

    def _verified_admins(self, session: Session, *, severity: str = "P1", category: str = "") -> List[str]:
        links = session.execute(
            select(ZaloIdentityLinkRecord).where(
                ZaloIdentityLinkRecord.status == "ACTIVE",
                ZaloIdentityLinkRecord.role == "ADMIN",
            )
        ).scalars().all()
        subs = {
            s.recipient_zalo_id: s
            for s in session.execute(select(NotificationSubscriptionRecord)).scalars().all()
        }
        admins = [lk.zalo_user_id for lk in links if lk.zalo_user_id]
        if not admins:
            admins = [a for a in self.fallback_admin_ids if a and a != "*"]
        out: List[str] = []
        for aid in dict.fromkeys(admins):
            sub = subs.get(aid)
            if not self._allows_category(sub, severity=severity, category=category):
                continue
            out.append(aid)
        return out

    def _verified_user_recipient(self, session: Session, username: Optional[str]) -> Optional[str]:
        uname = str(username or "").strip()
        if not uname:
            return None
        lk = (
            session.execute(
                select(ZaloIdentityLinkRecord).where(
                    ZaloIdentityLinkRecord.username == uname,
                    ZaloIdentityLinkRecord.status == "ACTIVE",
                )
            )
            .scalars()
            .first()
        )
        return lk.zalo_user_id if lk and lk.zalo_user_id else None

    def _verified_supervisor_recipient(self, session: Session, *, student_username: str, supervisor_id: Optional[str]) -> Optional[str]:
        if not supervisor_id:
            return None
        student = session.execute(
            select(MemberProfileRecord).where(MemberProfileRecord.username == student_username)
        ).scalars().first()
        if student is None:
            return None
        active_assign = (
            session.execute(
                select(SupervisorAssignmentRecord).where(
                    SupervisorAssignmentRecord.student_member_id == student.member_id,
                    SupervisorAssignmentRecord.supervisor_member_id == supervisor_id,
                    SupervisorAssignmentRecord.is_active.is_(True),
                )
            )
            .scalars()
            .first()
        )
        if active_assign is None:
            return None
        sup_prof = session.get(MemberProfileRecord, supervisor_id)
        if sup_prof is None:
            return None
        return self._verified_user_recipient(session, sup_prof.username)

    def get_notification_ownership_state(self) -> Dict[str, Any]:
        with self.db.session_scope() as session:
            cur = session.get(V4WorkerCursorRecord, CUTOVER_CURSOR_KEY)
            if cur is None:
                default_owner = "V2_LEGACY_SHADOW" if self.shadow_mode else "V2_LEGACY"
                return {
                    "owner": default_owner,
                    "previous_owner": None,
                    "v4_active_since": None,
                    "last_transition_at": None,
                    "transition_count": 0,
                }
            meta = dict(cur.metadata_json or {})
            return {
                "owner": cur.cursor_value or meta.get("owner") or "V2_LEGACY",
                "previous_owner": meta.get("previous_owner"),
                "v4_active_since": meta.get("v4_active_since"),
                "last_transition_at": meta.get("last_transition_at"),
                "transition_count": int(meta.get("transition_count", 0)),
            }

    def sync_notification_ownership(
        self,
        owner: str,
        *,
        actor: str = "worker",
        mark_existing_unconsumed_as_precutover: bool = False,
        now_override: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Persist V2/Shadow/V4 notification ownership transitions cleanly, audit them,
        clear stale shadow reminders on cutover, and prevent historical event backlog floods.
        """
        now = _as_utc(now_override)
        now_iso = now.isoformat()
        with self.db.session_scope() as session:
            cur = session.get(V4WorkerCursorRecord, CUTOVER_CURSOR_KEY)
            prev_owner = cur.cursor_value if cur is not None else None
            meta = dict(cur.metadata_json or {}) if cur is not None else {}

            if prev_owner == owner and cur is not None and not mark_existing_unconsumed_as_precutover:
                return {
                    "owner": owner,
                    "previous_owner": meta.get("previous_owner"),
                    "v4_active_since": meta.get("v4_active_since"),
                    "last_transition_at": meta.get("last_transition_at"),
                    "transition_count": int(meta.get("transition_count", 0)),
                    "changed": False,
                }

            trans_count = int(meta.get("transition_count", 0))
            if prev_owner is None or prev_owner != owner:
                trans_count += 1
                meta["previous_owner"] = prev_owner
                meta["last_transition_at"] = now_iso
                meta["transition_count"] = trans_count

                if owner == "V4_ACTIVE":
                    meta["v4_active_since"] = now_iso
                session.add(
                    AuditEventRecord(
                        id=f"aud4-{uuid.uuid4().hex[:12]}",
                        timestamp=now_iso,
                        actor=str(actor)[:128],
                        action="V4_NOTIFICATION_OWNERSHIP_CHANGED",
                        target_type="NOTIFICATION_OWNER",
                        target_id=owner,
                        result="TRANSITIONED",
                        correlation_id=f"cutover-{int(now.timestamp())}",
                        details=redact_text(
                            f"from={prev_owner or 'UNINITIALIZED'} to={owner} shadow_mode={self.shadow_mode}"
                        )[:500],
                    )
                )

            meta["owner"] = owner
            if cur is None:
                cur = V4WorkerCursorRecord(
                    cursor_name=CUTOVER_CURSOR_KEY,
                    cursor_value=owner,
                    metadata_json=meta,
                    updated_at=now,
                )
                session.add(cur)
            else:
                cur.cursor_value = owner
                cur.metadata_json = meta
                cur.updated_at = now

            skipped_precutover = 0
            if owner == "V4_ACTIVE":
                # Reset any shadow-mode incident reminders cleanly so cutover does not cause a burst
                shadow_metas = (
                    session.execute(select(V4IncidentMetaRecord).where(V4IncidentMetaRecord.shadow_only.is_(True)))
                    .scalars()
                    .all()
                )
                for sm in shadow_metas:
                    sm.shadow_only = False
                    inc_row = session.get(IncidentRecord, sm.incident_id)
                    if inc_row is None or inc_row.status == "RESOLVED":
                        sm.next_reminder_at = None
                    else:
                        delta = self.lifecycle.next_reminder_delta(
                            severity=inc_row.severity,
                            status=inc_row.status,
                            detected_at=inc_row.detected_at,
                            now=now,
                        )
                        sm.next_reminder_at = (now + delta) if delta else None
                    sm.updated_at = now

                should_mark_backlog = mark_existing_unconsumed_as_precutover or (
                    prev_owner in {"V2_LEGACY", "V2_LEGACY_SHADOW"}
                )
                max_v2_dt: Optional[datetime] = None
                if not should_mark_backlog and prev_owner is None:
                    raw_max_v2 = session.execute(select(func.max(NotificationOutboxRecord.created_at))).scalar_one_or_none()
                    if raw_max_v2 is not None:
                        max_v2_dt = _as_utc(raw_max_v2)

                if should_mark_backlog or max_v2_dt is not None:
                    meta["skip_reconciled_before"] = now_iso
                    cutoff_dt = now if should_mark_backlog else max_v2_dt
                    already_rcp = select(V4EventConsumerReceiptRecord.event_id).where(
                        V4EventConsumerReceiptRecord.consumer == "notification-policy-v4",
                        V4EventConsumerReceiptRecord.event_id == V4EventStoreRecord.event_id,
                    )
                    pre_evs = (
                        session.execute(
                            select(V4EventStoreRecord).where(
                                ~exists(already_rcp),
                                V4EventStoreRecord.observed_at <= cutoff_dt,
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for pev in pre_evs:
                        if pev.event_type in {"incident.created", "incident.reopened"}:
                            p_load = pev.payload_redacted_json or {}
                            inc_id = str(p_load.get("incident_id") or pev.resource_id)
                            sev = str(p_load.get("severity") or "P1").upper()
                            inc_row = session.get(IncidentRecord, inc_id)
                            if inc_row is not None and inc_row.status != "RESOLVED":
                                imeta = session.get(V4IncidentMetaRecord, inc_id)
                                delta = self.lifecycle.next_reminder_delta(
                                    severity=sev, status=inc_row.status, detected_at=inc_row.detected_at, now=now
                                )
                                if imeta is None:
                                    session.add(
                                        V4IncidentMetaRecord(
                                            incident_id=inc_id,
                                            shadow_only=False,
                                            next_reminder_at=(now + delta) if delta else None,
                                            updated_at=now,
                                        )
                                    )
                                else:
                                    imeta.shadow_only = False
                                    imeta.next_reminder_at = (now + delta) if delta else None
                                    imeta.updated_at = now
                        session.add(
                            V4EventConsumerReceiptRecord(
                                id=f"evrcp-{uuid.uuid4().hex[:12]}",
                                consumer="notification-policy-v4",
                                event_id=pev.event_id,
                                source=pev.source,
                                source_event_id=pev.source_event_id,
                                resource_id=pev.resource_id,
                                source_version=pev.source_version,
                                observed_at=_as_utc(pev.observed_at),
                                result_status="SKIPPED_PRE_CUTOVER",
                                processed_at=now,
                            )
                        )
                        skipped_precutover += 1
                cur.metadata_json = dict(meta)

            session.flush()
            return {
                "owner": owner,
                "previous_owner": meta.get("previous_owner"),
                "v4_active_since": meta.get("v4_active_since"),
                "last_transition_at": meta.get("last_transition_at"),
                "transition_count": trans_count,
                "skipped_precutover": skipped_precutover,
                "changed": prev_owner != owner,
            }

    @staticmethod
    def _record_shadow_decision(
        session: Session,
        *,
        event: Dict[str, Any],
        severity: str,
        recipients: List[str],
        reason: str,
        would_channel: str,
        chunk_count: int = 1,
        now_override: Optional[datetime] = None,
    ) -> None:
        now = _as_utc(now_override)
        cur = session.get(V4WorkerCursorRecord, SHADOW_METRICS_KEY)
        if cur is None:
            cur = V4WorkerCursorRecord(
                cursor_name=SHADOW_METRICS_KEY,
                cursor_value="0",
                metadata_json={"would_send": [], "decisions_total": 0, "count": 0},
                updated_at=now,
            )
            session.add(cur)
        meta = dict(cur.metadata_json or {})
        items = list(meta.get("would_send") or [])
        items.append(
            {
                "event_id": event.get("event_id"),
                "event_type": event.get("event_type"),
                "resource_id": event.get("resource_id"),
                "severity": severity,
                "recipients": list(recipients),
                "would_channel": would_channel,
                "reason": reason,
                "chunk_count": int(chunk_count),
                "evaluated_at": now.isoformat(),
            }
        )
        meta["would_send"] = items[-200:]
        meta["decisions_total"] = int(meta.get("decisions_total", 0)) + 1
        meta["count"] = int(meta.get("count", 0)) + len(recipients)
        meta["last_evaluated_at"] = now.isoformat()
        cur.cursor_value = str(meta["count"])
        cur.metadata_json = meta
        cur.updated_at = now

    def _enqueue_for_audience(
        self,
        session: Session,
        *,
        event: Dict[str, Any],
        recipients: List[str],
        severity: str,
        actionable: bool,
        dedupe_base: str,
        body_text: str,
        category: str = "",
        now_override: Optional[datetime] = None,
    ) -> int:
        now = _as_utc(now_override)
        subs = {
            s.recipient_zalo_id: s
            for s in session.execute(select(NotificationSubscriptionRecord)).scalars().all()
        }
        chunks = chunk_zalo_message(body_text, max_chars=self.cfg.target_chars)

        if self.shadow_mode:
            eligible: List[str] = []
            for rec_id in dict.fromkeys(recipients):
                sub = subs.get(rec_id)
                if sub is not None and not self._allows_category(sub, severity=severity, category=category):
                    continue
                eligible.append(rec_id)

            if not eligible:
                self._record_shadow_decision(
                    session,
                    event=event,
                    severity=severity,
                    recipients=[],
                    reason="FILTERED_NO_ELIGIBLE_RECIPIENTS",
                    would_channel="none",
                    chunk_count=max(1, len(chunks)),
                    now_override=now,
                )
                return 0

            is_p0 = str(severity or "P1").upper() == "P0"
            zalo_recips: List[str] = []
            quiet_recips: List[str] = []
            for rec_id in eligible:
                sub = subs.get(rec_id)
                if self._is_in_quiet_hours(sub, now) and not (is_p0 and (sub is None or sub.bypass_quiet_for_p0)):
                    quiet_recips.append(rec_id)
                else:
                    zalo_recips.append(rec_id)

            if quiet_recips:
                self._record_shadow_decision(
                    session,
                    event=event,
                    severity=severity,
                    recipients=quiet_recips,
                    reason="WOULD_DEFER_QUIET_HOURS",
                    would_channel=self.cfg.fallback_channel,
                    chunk_count=max(1, len(chunks)),
                    now_override=now,
                )
            if zalo_recips:
                decision = self.quota.decide_delivery(
                    session,
                    severity=severity,
                    actionable=actionable,
                    chunk_count=max(1, len(chunks)),
                    now_override=now,
                )
                if decision == "SEND_ZALO":
                    reason = "WOULD_SEND_ZALO"
                    ch = "zalo"
                elif decision == "COALESCE_DIGEST":
                    reason = "WOULD_COALESCE_DIGEST"
                    ch = self.cfg.fallback_channel
                else:
                    reason = "WOULD_FALLBACK_QUOTA_EXHAUSTED"
                    ch = self.cfg.fallback_channel
                self._record_shadow_decision(
                    session,
                    event=event,
                    severity=severity,
                    recipients=zalo_recips,
                    reason=reason,
                    would_channel=ch,
                    chunk_count=max(1, len(chunks)),
                    now_override=now,
                )
            return 0

        total_enqueued = 0
        for rec_id in dict.fromkeys(recipients):
            sub = subs.get(rec_id)
            if sub is not None and not self._allows_category(sub, severity=severity, category=category):
                continue

            is_p0 = str(severity or "P1").upper() == "P0"
            if self._is_in_quiet_hours(sub, now) and not (is_p0 and (sub is None or sub.bypass_quiet_for_p0)):
                rows = self.outbox.enqueue_in_session(
                    session,
                    dedupe_key=f"{dedupe_base}:{rec_id}:quiet-fallback",
                    recipient_id=rec_id,
                    channel=self.cfg.fallback_channel,
                    event_type=event["event_type"],
                    severity=severity,
                    body_text=body_text,
                    event_id=event.get("event_id"),
                    correlation_id=event.get("correlation_id", ""),
                    metadata={"quiet_hours_deferred": True},
                )
                total_enqueued += sum(1 for r in rows if r.get("created"))
                continue

            decision = self.quota.decide_delivery(
                session,
                severity=severity,
                actionable=actionable,
                chunk_count=max(1, len(chunks)),
            )
            if decision == "COALESCE_DIGEST":
                self.quota.add_shed_digest_item(session, summary_line=f"[{severity}] {body_text.splitlines()[0]}")
                self.outbox.enqueue_in_session(
                    session,
                    dedupe_key=f"{dedupe_base}:{rec_id}:fallback",
                    recipient_id=rec_id,
                    channel=self.cfg.fallback_channel,
                    event_type=event["event_type"],
                    severity=severity,
                    body_text=body_text,
                    event_id=event.get("event_id"),
                    correlation_id=event.get("correlation_id", ""),
                )
                continue

            target_channel = "zalo" if decision == "SEND_ZALO" else self.cfg.fallback_channel
            rows = self.outbox.enqueue_in_session(
                session,
                dedupe_key=f"{dedupe_base}:{rec_id}:{target_channel}",
                recipient_id=rec_id,
                channel=target_channel,
                event_type=event["event_type"],
                severity=severity,
                body_text=body_text,
                event_id=event.get("event_id"),
                correlation_id=event.get("correlation_id", ""),
            )
            created_cnt = sum(1 for r in rows if r.get("created"))
            if target_channel == "zalo" and created_cnt > 0:
                self.quota.record_usage(session, is_critical=is_p0, units=created_cnt)
            total_enqueued += created_cnt
        return total_enqueued

    def _schedule_request_sla_tasks(self, session: Session, *, req_id: str, username: str, submitted_at: datetime, review_expires_at: datetime, payload: Dict[str, Any]) -> None:
        if self.shadow_mode:
            return
        windows = [
            ("12h", submitted_at + timedelta(hours=12)),
            ("22h", submitted_at + timedelta(hours=22)),
            ("24h", review_expires_at),
        ]
        for label, run_at in windows:
            idem = f"req:{req_id}:sla:{label}"
            existing = session.execute(
                select(V4AssistantTaskRecord.task_id).where(V4AssistantTaskRecord.idempotency_key == idem)
            ).scalar_one_or_none()
            if existing is not None:
                continue
            session.add(
                V4AssistantTaskRecord(
                    task_id=f"tsk4-{uuid.uuid4().hex[:12]}",
                    owner_id="admin",
                    owner_role="ADMIN",
                    task_type="request_sla",
                    title=f"Nhắc hạn duyệt đơn {req_id} (@{username}) mốc {label}",
                    validated_spec_json={"request_id": req_id, "username": username, "milestone": label, "review_path": payload.get("review_path")},
                    missed_run_policy="coalesce",
                    status="ACTIVE",
                    next_run_at=run_at,
                    deadline_at=review_expires_at,
                    expires_at=review_expires_at + timedelta(hours=1),
                    idempotency_key=idem,
                )
            )

    def _cancel_request_sla_tasks(self, session: Session, req_id: str) -> None:
        tasks = (
            session.execute(
                select(V4AssistantTaskRecord).where(
                    V4AssistantTaskRecord.task_type == "request_sla",
                    V4AssistantTaskRecord.status.in_(["ACTIVE", "WAITING_CONDITION"]),
                )
            )
            .scalars()
            .all()
        )
        now = _utcnow()
        for t in tasks:
            if (t.validated_spec_json or {}).get("request_id") == req_id:
                t.status = "CANCELLED"
                t.cancellation_requested = True
                t.updated_at = now

    def process_events(self, *, batch_size: int = 50) -> Dict[str, int]:
        """Consume newly committed events deterministically with zero LLM dependency."""

        def _handle(session: Session, ev: Dict[str, Any], is_late: bool) -> str:
            etype = ev["event_type"]
            payload = ev.get("payload") or {}
            now = _utcnow()
            base_url = _public_base_url()

            if not self.shadow_mode:
                c_row = session.get(V4WorkerCursorRecord, CUTOVER_CURSOR_KEY)
                c_meta = dict(c_row.metadata_json or {}) if c_row else {}
                skip_before_raw = c_meta.get("skip_reconciled_before")
                if skip_before_raw and payload.get("reconciled"):
                    try:
                        skip_dt = _as_utc(datetime.fromisoformat(str(skip_before_raw).replace("Z", "+00:00")))
                        occ_dt = _as_utc(datetime.fromisoformat(str(ev["occurred_at"]).replace("Z", "+00:00")))
                        if occ_dt < skip_dt:
                            if etype in {"incident.created", "incident.reopened"}:
                                inc_id = str(payload.get("incident_id") or ev["resource_id"])
                                sev = str(payload.get("severity") or "P1").upper()
                                if session.get(V4IncidentMetaRecord, inc_id) is None:
                                    delta = self.lifecycle.next_reminder_delta(
                                        severity=sev, status="OPEN", detected_at=occ_dt, now=now
                                    )
                                    session.add(
                                        V4IncidentMetaRecord(
                                            incident_id=inc_id,
                                            shadow_only=False,
                                            next_reminder_at=(now + delta) if delta else None,
                                            updated_at=now,
                                        )
                                    )
                            return "SKIPPED_PRE_CUTOVER"
                    except Exception:
                        pass

            if is_late and etype in {"telemetry.stale", "incident.resolved", "node.recovered"}:
                return "LATE_IGNORED"

            if etype == "request.created":
                req_id = payload.get("request_id") or ev["resource_id"]
                admins = self._verified_admins(session)
                sup_id = payload.get("supervisor_id")
                sup_rec = self._verified_supervisor_recipient(session, student_username=str(payload.get("username") or ""), supervisor_id=sup_id)
                recipients = list(admins)
                if sup_rec and sup_rec not in recipients:
                    recipients.append(sup_rec)

                review_path = payload.get("review_path") or f"/dashboard/ssh-requests/{req_id}/review"
                msg = (
                    f"📩 YÊU CẦU CẤP SSH MỚI ({req_id})\n"
                    f"• Thành viên: {payload.get('full_name') or payload.get('username')} (@{payload.get('username')})\n"
                    f"• Lý do: {payload.get('reason_snippet') or payload.get('project_title') or 'N/A'}\n"
                    f"• Gửi lúc: {_fmt_vn(payload.get('submitted_at') or ev['occurred_at'])}\n"
                    f"• Hạn SLA (24h): {_fmt_vn(payload.get('review_expires_at'))}\n"
                    f"🔗 Dashboard: {base_url}{review_path}"
                )
                self._enqueue_for_audience(
                    session,
                    event=ev,
                    recipients=recipients,
                    severity="P1",
                    actionable=True,
                    dedupe_base=f"ev:{ev['source_event_id']}",
                    body_text=msg,
                )
                sub_dt = _as_utc(datetime.fromisoformat(str(payload.get("submitted_at") or ev["occurred_at"]).replace("Z", "+00:00")))
                exp_raw = payload.get("review_expires_at")
                exp_dt = _as_utc(datetime.fromisoformat(str(exp_raw).replace("Z", "+00:00"))) if exp_raw else (sub_dt + timedelta(hours=24))
                self._schedule_request_sla_tasks(
                    session,
                    req_id=str(req_id),
                    username=str(payload.get("username") or ""),
                    submitted_at=sub_dt,
                    review_expires_at=exp_dt,
                    payload=payload,
                )
                return "PROCESSED"

            if etype == "request.updated":
                # Minor updates do NOT reset SLA! Only cancel if resolved status
                return "PROCESSED_NO_SLA_RESET"

            if etype in {"request.approved", "request.rejected", "request.overdue"}:
                req_id = str(payload.get("request_id") or ev["resource_id"])
                self._cancel_request_sla_tasks(session, req_id)
                uname = str(payload.get("username") or "")
                applicant_rec = self._verified_user_recipient(session, uname)
                sup_rec = self._verified_supervisor_recipient(session, student_username=uname, supervisor_id=payload.get("supervisor_id"))
                admins = self._verified_admins(session)
                recipients: List[str] = list(admins)
                if applicant_rec:
                    recipients.append(applicant_rec)
                if sup_rec:
                    recipients.append(sup_rec)
                label = {"request.approved": "✅ ĐÃ PHÊ DUYỆT", "request.rejected": "⛔ TỪ CHỐI", "request.overdue": "⏰ QUÁ HẠN SLA 24H"}[etype]
                msg = (
                    f"{label} ĐƠN SSH ({req_id})\n"
                    f"• Thành viên: @{uname}\n"
                    f"• Lý do/Ghi chú: {payload.get('reason') or 'Cập nhật từ hệ thống'}\n"
                    f"• Thời gian: {_fmt_vn(ev['occurred_at'])}\n"
                    f"🔗 Dashboard: {base_url}/dashboard/ssh-requests/{req_id}/review"
                )
                self._enqueue_for_audience(
                    session,
                    event=ev,
                    recipients=recipients,
                    severity="P1",
                    actionable=True,
                    dedupe_base=f"ev:{ev['source_event_id']}",
                    body_text=msg,
                )
                return "PROCESSED"

            if etype in {"provisioning.succeeded", "provisioning.failed"}:
                uname = str(payload.get("username") or "")
                req_id = str(payload.get("request_id") or ev["resource_id"])
                sev = "P0" if etype == "provisioning.failed" else "P1"
                recipients = list(self._verified_admins(session, severity=sev, category="p0" if sev == "P0" else "p1"))
                app_rec = self._verified_user_recipient(session, uname)
                if app_rec:
                    recipients.append(app_rec)
                ok = etype == "provisioning.succeeded"
                msg = (
                    f"{'🎉 CẤP PHÁT HOÀN TẤT' if ok else '🚨 CẤP PHÁT THẤT BẠI'} ({req_id})\n"
                    f"• Tài khoản: @{uname} | Host: {payload.get('host_id') or 'N/A'}\n"
                    f"• Chi tiết: {payload.get('status') or ''} {payload.get('error') or ''}".strip()
                )
                self._enqueue_for_audience(
                    session,
                    event=ev,
                    recipients=recipients,
                    severity=sev,
                    actionable=not ok,
                    dedupe_base=f"ev:{ev['source_event_id']}",
                    body_text=msg,
                )
                return "PROCESSED"

            if etype in {"incident.created", "incident.reopened", "incident.escalated", "incident.resolved", "incident.acknowledged"}:
                inc_id = str(payload.get("incident_id") or ev["resource_id"])
                sev = str(payload.get("severity") or "P1").upper()
                inc_ev_type = str(payload.get("incident_event_type") or "")
                host_id = payload.get("host_id")
                source = str(payload.get("source") or "")

                # Correlation check before notifying
                parent_id = None
                if etype in {"incident.created", "incident.reopened"}:
                    parent_id = self.correlator.attach_and_check_parent(
                        session,
                        incident_id=inc_id,
                        event_type=inc_ev_type,
                        host_id=host_id,
                        source=source,
                    )

                meta = session.get(V4IncidentMetaRecord, inc_id)
                if meta is None:
                    meta = V4IncidentMetaRecord(
                        incident_id=inc_id,
                        parent_incident_id=parent_id,
                        shadow_only=bool(self.shadow_mode),
                        updated_at=now,
                    )
                    session.add(meta)
                else:
                    meta.shadow_only = bool(self.shadow_mode)

                if etype == "incident.acknowledged":
                    inc_row = session.get(IncidentRecord, inc_id)
                    if inc_row:
                        delta = self.lifecycle.next_reminder_delta(severity=inc_row.severity, status="ACKNOWLEDGED", detected_at=inc_row.detected_at, now=now)
                        meta.next_reminder_at = (now + delta) if delta else None
                    return "PROCESSED_ACK"

                if etype == "incident.resolved":
                    meta.next_reminder_at = None
                    if sev not in {"P0", "P1"}:
                        return "PROCESSED_QUIET_RESOLVE"
                    recipients = list(self._verified_admins(session))
                    owner_rec = self._verified_user_recipient(session, payload.get("owner_id"))
                    if owner_rec:
                        recipients.append(owner_rec)
                    msg = (
                        f"✅ SỰ CỐ ĐÃ PHỤC HỒI ({inc_id})\n"
                        f"• Tiêu đề: {payload.get('title')}\n"
                        f"• Bằng chứng: {payload.get('recovery_proof') or 'Đã chuyển RESOLVED'}\n"
                        f"• Thời điểm: {_fmt_vn(ev['occurred_at'])}"
                    )
                    self._enqueue_for_audience(
                        session,
                        event=ev,
                        recipients=recipients,
                        severity="P1",
                        actionable=False,
                        dedupe_base=f"ev:{ev['source_event_id']}",
                        body_text=msg,
                    )
                    return "PROCESSED_RESOLVED"

                if parent_id:
                    return f"CORRELATED_TO:{parent_id}"

                inc_cat = "repo" if "REPO_" in inc_ev_type else ("p0" if sev == "P0" else "p1")
                recipients = list(self._verified_admins(session, severity=sev, category=inc_cat))
                owner_rec = self._verified_user_recipient(session, payload.get("owner_id"))
                if owner_rec:
                    recipients.append(owner_rec)

                delta = self.lifecycle.next_reminder_delta(severity=sev, status="OPEN", detected_at=now, now=now)
                meta.next_reminder_at = (now + delta) if delta else None

                icon = "🚨" if sev == "P0" else ("⚠️" if sev == "P1" else "ℹ️")
                msg = (
                    f"{icon} [{sev}] {payload.get('title') or etype}\n"
                    f"• Mã sự cố: {inc_id} | Nguồn: {source or 'telemetry'} | Tài nguyên: {payload.get('resource_id') or ev['resource_id']}\n"
                    f"• Thời điểm: {_fmt_vn(ev['occurred_at'])}\n"
                    f"• Khuyến nghị: {payload.get('recommended_action') or 'Kiểm tra Dashboard'}\n"
                    f"🔗 Chi tiết: {base_url}/dashboard/ai-assistant"
                )
                self._enqueue_for_audience(
                    session,
                    event=ev,
                    recipients=recipients,
                    severity=sev,
                    actionable=(sev in {"P0", "P1"}),
                    dedupe_base=f"ev:{ev['source_event_id']}",
                    body_text=msg,
                    category=inc_cat,
                )
                return "PROCESSED"

            if etype == "repository.risk_found":
                admins = self._verified_admins(session, severity="P1", category="repo")
                repo_label = payload.get("repo") or ev["resource_id"]
                msg = (
                    f"⚠️ KHO MÃ BỊ TỪ CHỐI / CÓ RỦI RO ({repo_label})\n"
                    f"• Yêu cầu: {payload.get('request_id') or ev['resource_id']} | Quyết định: {payload.get('decision') or 'REJECTED'}\n"
                    f"• Thời gian: {_fmt_vn(ev['occurred_at'])}\n"
                    f"🔗 Kiểm duyệt: {base_url}/dashboard/security"
                )
                self._enqueue_for_audience(
                    session,
                    event=ev,
                    recipients=admins,
                    severity="P1",
                    actionable=True,
                    dedupe_base=f"ev:{ev['source_event_id']}",
                    body_text=msg,
                    category="repo",
                )
                return "PROCESSED"

            if etype == "repository.approved":
                admins = self._verified_admins(session, severity="P2", category="repo")
                repo_label = payload.get("repo") or ev["resource_id"]
                msg = f"✅ Kho mã `{repo_label}` đã được Admin phê duyệt lúc {_fmt_vn(ev['occurred_at'])}."
                if self.shadow_mode:
                    self._record_shadow_decision(
                        session,
                        event=ev,
                        severity="P2",
                        recipients=admins,
                        reason="WOULD_SEND_DASHBOARD_ONLY",
                        would_channel=self.cfg.fallback_channel,
                        now_override=now,
                    )
                    return "SHADOW_DRY_RUN"
                for aid in admins:
                    self.outbox.enqueue_in_session(
                        session,
                        dedupe_key=f"ev:{ev['source_event_id']}:{aid}:dashboard",
                        recipient_id=aid,
                        channel=self.cfg.fallback_channel,
                        event_type=etype,
                        severity="P2",
                        body_text=msg,
                        event_id=ev["event_id"],
                        correlation_id=ev.get("correlation_id", ""),
                    )
                return "PROCESSED"

            if etype == "workspace.failed":
                admins = self._verified_admins(session, severity="P1", category="p1")
                owner = payload.get("owner") or ev.get("actor_id")
                owner_rec = self._verified_user_recipient(session, owner)
                recipients = list(admins)
                if owner_rec and owner_rec not in recipients:
                    recipients.append(owner_rec)
                msg = (
                    f"🚨 WORKSPACE GẶP LỖI ({payload.get('name') or ev['resource_id']})\n"
                    f"• Chủ sở hữu: @{owner or 'unknown'}\n"
                    f"• Thời điểm: {_fmt_vn(ev['occurred_at'])}\n"
                    f"🔗 Kiểm tra: {base_url}/dashboard/workspaces"
                )
                self._enqueue_for_audience(
                    session,
                    event=ev,
                    recipients=recipients,
                    severity="P1",
                    actionable=True,
                    dedupe_base=f"ev:{ev['source_event_id']}",
                    body_text=msg,
                    category="p1",
                )
                return "PROCESSED"

            if etype in {"ai.unavailable", "service.recovered"}:
                admins = self._verified_admins(session, severity="P2")
                msg = (
                    f"{'⚠️ AI Gateway tạm gián đoạn (Circuit Breaker OPEN)' if etype == 'ai.unavailable' else '✅ AI Gateway đã phục hồi bình thường'}\n"
                    f"• Tài nguyên: {ev['resource_id']} | Thời gian: {_fmt_vn(ev['occurred_at'])}"
                )
                self._enqueue_for_audience(
                    session,
                    event=ev,
                    recipients=admins,
                    severity="P2",
                    actionable=False,
                    dedupe_base=f"ev:{ev['source_event_id']}",
                    body_text=msg,
                )
                return "PROCESSED"

            return "IGNORED_UNROUTED"

        def _wrapped_handler(session: Session, ev: Dict[str, Any], is_late: bool) -> str:
            res = _handle(session, ev, is_late)
            if self.shadow_mode:
                if res.startswith("CORRELATED_TO:"):
                    return f"SHADOW_{res}"
                if res not in {"LATE_IGNORED", "IGNORED_UNROUTED", "SKIPPED_PRE_CUTOVER"}:
                    return "SHADOW_DRY_RUN"
            return res

        return self.events.process_pending("notification-policy-v4", _wrapped_handler, batch_size=batch_size)

    def run_incident_reminders(self, *, now_override: Optional[datetime] = None) -> int:
        """Emit concise repeat notifications for long-running unclosed P0/P1/P2 incidents."""
        now = _as_utc(now_override)
        enqueued = 0
        with self.db.session_scope() as session:
            due_metas = (
                session.execute(
                    select(V4IncidentMetaRecord).where(
                        V4IncidentMetaRecord.next_reminder_at.is_not(None),
                        V4IncidentMetaRecord.next_reminder_at <= now,
                    )
                )
                .scalars()
                .all()
            )
            for meta in due_metas:
                inc = session.get(IncidentRecord, meta.incident_id)
                if inc is None or inc.status == "RESOLVED":
                    meta.next_reminder_at = None
                    meta.shadow_only = False
                    continue
                if meta.suppressed_until and _as_utc(meta.suppressed_until) > now:
                    continue

                inc_cat = "repo" if "REPO_" in str(inc.event_type or "") else ("p0" if inc.severity == "P0" else "p1")
                admins = self._verified_admins(session, severity=inc.severity, category=inc_cat)

                if meta.shadow_only and not self.shadow_mode:
                    # Resynchronize shadow-era reminder forward without emitting a burst at cutover
                    delta = self.lifecycle.next_reminder_delta(
                        severity=inc.severity, status=inc.status, detected_at=inc.detected_at, now=now
                    )
                    meta.shadow_only = False
                    meta.next_reminder_at = (now + delta) if delta else None
                    meta.updated_at = now
                    continue

                dur_min = max(1, int((now - _as_utc(inc.detected_at)).total_seconds() // 60))
                hrs, mins = divmod(dur_min, 60)
                dur_str = f"{hrs}h {mins}m" if hrs else f"{mins}m"
                meta.reminder_count = int(meta.reminder_count or 0) + 1
                meta.last_reminded_at = now
                delta = self.lifecycle.next_reminder_delta(
                    severity=inc.severity, status=inc.status, detected_at=inc.detected_at, now=now
                )
                meta.next_reminder_at = (now + delta) if delta else None

                ev_stub = {
                    "event_id": f"rem-{inc.incident_id}-{meta.reminder_count}",
                    "event_type": "incident.reminder",
                    "resource_id": inc.incident_id,
                    "correlation_id": inc.correlation_id or inc.incident_id,
                }

                if self.shadow_mode:
                    meta.shadow_only = True
                    self._record_shadow_decision(
                        session,
                        event=ev_stub,
                        severity=inc.severity,
                        recipients=admins,
                        reason="SHADOW_REMINDER_DRY_RUN",
                        would_channel="zalo",
                        now_override=now,
                    )
                    continue

                msg = (
                    f"🔁 [NHẮC SỰ CỐ {inc.severity}/{inc.status}] {inc.incident_id}\n"
                    f"• Kéo dài: {dur_str} | Quan sát gần nhất: {_fmt_vn(inc.last_observed_at)}\n"
                    f"• Tóm tắt: {inc.title}\n"
                    f"• Dùng `/ack {inc.incident_id}` để xác nhận."
                )
                enqueued += self._enqueue_for_audience(
                    session,
                    event=ev_stub,
                    recipients=admins,
                    severity=inc.severity,
                    actionable=True,
                    dedupe_base=f"inc-rem:{inc.incident_id}:n{meta.reminder_count}",
                    body_text=msg,
                    category=inc_cat,
                    now_override=now,
                )
        return enqueued


class DigestSchedulerV4:
    """Schedules 08:00 ICT daily brief, 17:00 ICT change-only digest, and Monday 08:00 weekly report without double-send."""

    def __init__(self, db_manager: DatabaseManager, cfg: Optional[SchedulerV4Config] = None):
        self.db = db_manager
        self.cfg = cfg or SchedulerV4Config()

    def evaluate_due_digests(
        self,
        *,
        now_utc: Optional[datetime] = None,
        has_changes: bool = False,
        summary_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        now = _as_utc(now_utc)
        local = now.astimezone(VN_TZ)
        day_key = local.strftime("%Y-%m-%d")
        is_monday = local.weekday() == 0
        actions: List[Dict[str, Any]] = []
        ctx = summary_context or {}

        with self.db.session_scope() as session:
            wk_period = local.strftime("%Y-W%W")
            # Check whether legacy V2 already sent today's daily brief or this week's weekly report
            v2_daily_done = (
                session.execute(
                    select(ReportRunRecord.id).where(
                        ReportRunRecord.period_key == day_key,
                        ReportRunRecord.status.in_(["SENT", "DELIVERY_UNKNOWN", "SENDING"]),
                    )
                )
                .scalars()
                .first()
                is not None
            )
            v2_weekly_done = (
                session.execute(
                    select(NotificationOutboxRecord.id).where(
                        NotificationOutboxRecord.idempotency_key == f"weekly-report:{wk_period}",
                        NotificationOutboxRecord.status.in_(["SENT", "DELIVERY_UNKNOWN", "PENDING", "CLAIMED"]),
                    )
                )
                .scalars()
                .first()
                is not None
            )

            # Monday 08:00 weekly replaces morning brief when configured (prevents double-send!)
            if is_monday and 8 <= local.hour < 12 and self.cfg.weekly_replaces_daily_on_monday:
                wk_key = f"digest:weekly:{wk_period}"
                if session.get(V4WorkerCursorRecord, wk_key) is None:
                    if v2_weekly_done:
                        session.add(V4WorkerCursorRecord(cursor_name=wk_key, cursor_value="COVERED_BY_V2_LEGACY", metadata_json={"type": "v2_carryover"}, updated_at=now))
                    else:
                        session.add(V4WorkerCursorRecord(cursor_name=wk_key, cursor_value=day_key, metadata_json={"type": "weekly"}, updated_at=now))
                        dm_key = f"digest:morning:{day_key}"
                        if session.get(V4WorkerCursorRecord, dm_key) is None:
                            session.add(V4WorkerCursorRecord(cursor_name=dm_key, cursor_value="REPLACED_BY_WEEKLY", metadata_json={"type": "suppressed"}, updated_at=now))
                        actions.append({"kind": "weekly_monday", "period": wk_period, "text": ctx.get("weekly_text") or f"📑 Báo cáo tuần {wk_period} (Thứ Hai 08:00 ICT)"})
            elif 8 <= local.hour < 12:
                dm_key = f"digest:morning:{day_key}"
                if session.get(V4WorkerCursorRecord, dm_key) is None:
                    if v2_daily_done:
                        session.add(V4WorkerCursorRecord(cursor_name=dm_key, cursor_value="COVERED_BY_V2_LEGACY", metadata_json={"type": "v2_carryover"}, updated_at=now))
                    else:
                        session.add(V4WorkerCursorRecord(cursor_name=dm_key, cursor_value=day_key, metadata_json={"type": "morning"}, updated_at=now))
                        actions.append({"kind": "morning_daily", "period": day_key, "text": ctx.get("morning_text") or f"☀️ Bản tin sáng 08:00 ICT ngày {day_key}"})

            # 17:00 ICT evening summary ONLY if changes exist when configured
            if local.hour >= 17 and (not self.cfg.evening_only_if_changed or has_changes):
                ev_key = f"digest:evening:{day_key}"
                if session.get(V4WorkerCursorRecord, ev_key) is None:
                    session.add(V4WorkerCursorRecord(cursor_name=ev_key, cursor_value=day_key, metadata_json={"type": "evening"}, updated_at=now))
                    actions.append({"kind": "evening_summary", "period": day_key, "text": ctx.get("evening_text") or f"🌆 Tổng kết cuối ngày 17:00 ICT ({day_key})"})

        return actions
