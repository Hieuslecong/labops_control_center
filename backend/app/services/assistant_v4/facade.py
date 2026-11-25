"""
LabOps Personal AI Agent V4 — Unified Facade wiring Phases 1-7 into Zalo & Dashboard.
Feature-flagged OFF by default (`config.enabled == False`).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import select

from app.db.models import (
    IncidentRecord,
    V4InvestigationRecord,
    V4WorkerCursorRecord,
)
from app.db.session import DatabaseManager
from app.services.assistant_v4.config import AssistantV4Config, load_assistant_v4_config
from app.services.assistant_v4.event_store import (
    EVENT_CATALOG,
    EventEnvelope,
    EventReconciler,
    EventStoreService,
)
from app.services.assistant_v4.inbox_outbox import DurableMessageInbox, OutboxV4Service
from app.services.assistant_v4.memory_service import ScopedSessionMemory
from app.services.assistant_v4.notification_policy import (
    SHADOW_METRICS_KEY,
    resolve_notification_owner,
)
from app.services.assistant_v4.retention_service import RetentionMaintenanceService

if TYPE_CHECKING:
    from app.services.zalo_assistant.identity_rbac import ZaloIdentityRBACService, ZaloPrincipal
    from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
    from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector

logger = logging.getLogger("labops.v4.facade")


class AssistantV4Facade:
    def __init__(
        self,
        *,
        data_dir: str,
        db_manager: DatabaseManager,
        ai_client: Any,
        identity_rbac: ZaloIdentityRBACService,
        telemetry_collector: UnifiedTelemetryCollector,
        incident_engine: UnifiedIncidentEngine,
        authorized_users: Optional[List[str]] = None,
        config: Optional[AssistantV4Config] = None,
    ):
        from app.services.assistant_v4.agent_runtime import (
            IntentRouter,
            ModelCapabilityProbe,
            select_agent_runtime,
        )
        from app.services.assistant_v4.approval_service import ActionProposalService
        from app.services.assistant_v4.investigation_service import InvestigationService
        from app.services.assistant_v4.notification_policy import DigestSchedulerV4, EventPolicyEngine
        from app.services.assistant_v4.secure_tools import SecureToolExecutor
        from app.services.assistant_v4.skills_and_connectors import ConnectorsService, KnowledgeBaseService, SkillRegistry
        from app.services.assistant_v4.task_scheduler import PersistentScheduler, TaskService, WatchSpec, WatchdogAdapter

        self._intent_router = IntentRouter
        self._watch_spec_cls = WatchSpec
        self._skill_registry_cls = SkillRegistry
        self.data_dir = data_dir
        self.db = db_manager
        self._authorized_users = list(authorized_users or [])
        self._config_path: Optional[str] = None
        self.config = config or load_assistant_v4_config()
        self.ai_client = ai_client
        self.identity_rbac = identity_rbac
        self.telemetry = telemetry_collector
        self.incidents = incident_engine

        self.events = EventStoreService(db_manager)
        self.reconciler = EventReconciler(db_manager)
        self.inbox = DurableMessageInbox(db_manager, active_mode=self.config.ingress_mode)
        self.outbox = OutboxV4Service(db_manager, target_chars=self.config.notifications.target_chars)

        self.policy = EventPolicyEngine(
            db_manager,
            cfg=self.config.notifications,
            shadow_mode=self.config.shadow_mode,
            fallback_admin_ids=authorized_users,
        )
        self.digests = DigestSchedulerV4(db_manager, cfg=self.config.scheduler)
        self.tasks = TaskService(db_manager, cfg=self.config.tasks)
        self.scheduler = PersistentScheduler(db_manager, cfg=self.config.tasks)
        self.watchdog = WatchdogAdapter(
            db_manager,
            cfg=self.config.watchdog,
            require_scheduler_tick=bool(self.config.task_manager),
        )
        self.retention = RetentionMaintenanceService(db_manager, cfg=self.config.retention)

        self.memory = ScopedSessionMemory(db_manager, cfg=self.config.memory)
        self.skills = SkillRegistry(connector_cfg=self.config.external_connectors)
        self.kb = KnowledgeBaseService(db_manager)
        try:
            self.kb.bootstrap_defaults()
        except Exception:
            pass
        self.connectors = ConnectorsService(db_manager, cfg=self.config.external_connectors)

        def _reload_principal(p: ZaloPrincipal) -> ZaloPrincipal:
            if not p.zalo_user_id or p.zalo_user_id == p.username:
                return p
            return self.identity_rbac.resolve_zalo_principal(
                p.zalo_user_id, fallback_authorized_users=authorized_users
            )

        self.approvals = ActionProposalService(
            db_manager,
            write_actions_enabled=self.config.write_actions,
            incident_engine=incident_engine,
            identity_reloader=_reload_principal,
        )
        self.tools = SecureToolExecutor(
            data_dir,
            db_manager=db_manager,
            telemetry_collector=telemetry_collector,
            incident_engine=incident_engine,
            identity_reloader=_reload_principal,
            task_service=self.tasks,
            proposal_service=self.approvals,
        )
        self.runtime = select_agent_runtime(
            requested=self.config.agent_runtime,
            db_manager=db_manager,
            ai_client=ai_client,
            memory=self.memory,
            tool_executor=self.tools,
            budget=self.config.model,
        )
        self.probe = ModelCapabilityProbe(ai_client, self.tools)
        self.investigations = InvestigationService(
            db_manager,
            tool_executor=self.tools,
            cfg=self.config.investigation,
            ai_client=ai_client,
        )

    def record_repo_decision_event(self, *, request_id: str, req_info: Dict[str, Any], decision: str, actor: str) -> None:
        try:
            self.events.emit(
                EventEnvelope(
                    source="repository_guard",
                    source_event_id=f"repo:{request_id}:decision:{decision.lower()}",
                    event_type="repository.approved" if decision == "APPROVED" else "repository.risk_found",
                    scope_type="admin",
                    scope_id="admin",
                    resource_id=str(req_info.get("canonical_repo") or request_id),
                    actor_id=actor,
                    payload={"request_id": request_id, "decision": decision, "repo": req_info.get("repo_name")},
                )
            )
        except Exception:
            pass

    def handle_natural_message(
        self,
        *,
        text: str,
        principal: ZaloPrincipal,
        channel: str = "zalo",
        chat_id: str = "",
        bot_id: str = "default",
    ) -> Dict[str, Any]:
        clean = str(text or "").strip()
        if not clean:
            return {"reply": "Vui lòng nhập nội dung câu hỏi.", "ai_used": False}

        effective_chat = chat_id or principal.zalo_user_id or principal.username
        sess = self.memory.get_or_create_session(
            principal_id=principal.username or principal.zalo_user_id,
            channel=channel,
            bot_id=bot_id,
            chat_id=effective_chat,
        )
        intent = self._intent_router.classify(clean)

        if intent == "GENERAL_CHAT":
            return self.runtime.run_turn(
                question=clean,
                principal=principal,
                session_id=sess["session_id"],
                channel=channel,
                chat_id=effective_chat,
                allowed_tools=[],
            )

        if intent == "WATCH_OR_REMINDER_NL":
            proposal = self._parse_nl_watch_or_reminder(clean, principal)
            if proposal is not None:
                return proposal

        return self.runtime.run_turn(
            question=clean,
            principal=principal,
            session_id=sess["session_id"],
            channel=channel,
            chat_id=effective_chat,
            allowed_tools=None,
        )

    def _parse_nl_watch_or_reminder(self, text: str, principal: ZaloPrincipal) -> Optional[Dict[str, Any]]:
        low = text.lower()
        target = "workstation" if ("workstation" in low or "ws" in low) else ("track" if "rack" in low else "workstation")
        # Extract GPU temp watch like "dưới 80°C trong 5 phút"
        m_temp = re.search(r"(dưới|duoi|nhỏ hơn|<|trên|tren|lớn hơn|>=|>)\s*(\d+(?:\.\d+)?)\s*(?:°c|độ|c)", low)
        if m_temp:
            op_word, val_s = m_temp.groups()
            comp = "lt" if op_word in {"dưới", "duoi", "nhỏ hơn", "<"} else "gte"
            m_min = re.search(r"(\d+)\s*(?:phút|phut|min|m)", low)
            hold_s = (int(m_min.group(1)) * 60) if m_min else 300
            hold_s = max(60, min(hold_s, 86400))
            spec = self._watch_spec_cls(
                target_id=target,
                metric="gpu_temperature_c",
                comparator=comp,
                threshold=float(val_s),
                sustained_seconds=hold_s,
                once=True,
            )
            if self.config.task_manager:
                created = self.tasks.create_watch(
                    owner_id=principal.username or principal.zalo_user_id,
                    owner_role=principal.role,
                    spec=spec,
                )
                return {
                    "reply": (
                        f"✅ Đã tạo Watch `{created['task_id']}`:\n"
                        f"• Mục tiêu: {spec.target_id} | {spec.metric} {comp} {spec.threshold}°C\n"
                        f"• Duy trì: {spec.sustained_seconds}s | Chế độ: báo 1 lần\n"
                        f"• Hủy bất cứ lúc nào: `/cancel {created['task_id']}`"
                    ),
                    "ai_used": False,
                    "task": created,
                }
            return {
                "reply": (
                    f"📋 Đề xuất Watch:\n"
                    f"• Mục tiêu: {spec.target_id} | {spec.metric} {comp} {spec.threshold}°C (duy trì {spec.sustained_seconds}s, 1 lần)\n"
                    f"👉 Gõ `/watch {spec.target_id} {spec.metric} {comp} {spec.threshold} {spec.sustained_seconds}` để xác nhận tạo."
                ),
                "ai_used": False,
                "proposal": spec.model_dump(),
            }
        return None

    def _run_digests_step(self, *, now_override: Optional[datetime], has_changes: bool) -> int:
        due = self.digests.evaluate_due_digests(now_utc=now_override, has_changes=has_changes)
        if not due:
            return 0
        enqueued = 0
        with self.db.session_scope() as session:
            for item in due:
                kind = str(item.get("kind") or "")
                cat = "weekly" if kind == "weekly_monday" else "daily"
                admins = self.policy._verified_admins(session, severity="REPORT", category=cat)
                ev_stub = {
                    "event_id": f"digest:{kind}:{item['period']}",
                    "event_type": f"digest.{kind}",
                    "resource_id": item["period"],
                    "correlation_id": f"digest-{item['period']}",
                }
                enqueued += self.policy._enqueue_for_audience(
                    session,
                    event=ev_stub,
                    recipients=admins,
                    severity="REPORT",
                    actionable=False,
                    dedupe_base=f"digest:{kind}:{item['period']}",
                    body_text=item["text"],
                    category=cat,
                    now_override=now_override,
                )
        return enqueued

    def _run_proactive_investigations(self, *, max_per_cycle: int = 2) -> int:
        ran = 0
        with self.db.session_scope() as session:
            open_crit = (
                session.execute(
                    select(IncidentRecord)
                    .where(
                        IncidentRecord.status.in_(["OPEN", "ACKNOWLEDGED"]),
                        IncidentRecord.severity.in_(["P0", "P1"]),
                    )
                    .order_by(IncidentRecord.detected_at.desc())
                    .limit(10)
                )
                .scalars()
                .all()
            )
            candidates: List[str] = []
            for inc in open_crit:
                ep = max(1, int(inc.reopen_count or 0) + 1)
                ep_key = f"{inc.incident_id}:ep{ep}"
                exists_inv = session.execute(
                    select(V4InvestigationRecord.run_id).where(V4InvestigationRecord.episode_key == ep_key)
                ).scalar_one_or_none()
                if exists_inv is None:
                    candidates.append(inc.incident_id)
                if len(candidates) >= max_per_cycle:
                    break

        for inc_id in candidates:
            try:
                self.investigations.investigate_incident(inc_id)
                ran += 1
            except Exception as exc:
                logger.warning("Proactive investigation skipped for %s: %s", inc_id, exc)
        return ran

    def _sync_components_from_config(self) -> None:
        self.inbox.active_mode = self.config.ingress_mode
        self.outbox.target_chars = self.config.notifications.target_chars
        self.policy.cfg = self.config.notifications
        self.policy.shadow_mode = bool(self.config.shadow_mode)
        self.policy.quota.cfg = self.config.notifications
        self.policy.lifecycle.cfg = self.config.notifications
        self.digests.cfg = self.config.scheduler
        self.tasks.cfg = self.config.tasks
        self.scheduler.cfg = self.config.tasks
        self.watchdog.cfg = self.config.watchdog
        self.watchdog.require_scheduler_tick = bool(self.config.task_manager)
        self.retention.cfg = self.config.retention
        self.memory.cfg = self.config.memory
        self.skills = self._skill_registry_cls(connector_cfg=self.config.external_connectors)
        self.connectors.cfg = self.config.external_connectors
        self.connectors.timeout_s = max(1.0, min(float(getattr(self.config.external_connectors, "request_timeout_s", 10.0)), 60.0))
        self.approvals.write_actions_enabled = bool(self.config.write_actions)
        self.runtime.budget = self.config.model
        self.investigations.cfg = self.config.investigation

    def reload_config(self, config_path: Optional[str] = None, *, actor: str = "config-reload") -> Dict[str, Any]:
        """
        Explicitly reload YAML + environment-backed AssistantV4Config and resynchronize ownership.
        Note: edits to `.env.local` on disk require restarting backend and labops-ai-sre-worker
        processes to enter process environment variables.
        """
        target_path = config_path or self._config_path
        if config_path:
            self._config_path = config_path
        self.config = load_assistant_v4_config(config_path=target_path)
        self._sync_components_from_config()
        owner = resolve_notification_owner(self.config)
        cutover = self.policy.sync_notification_ownership(owner, actor=actor)
        return {
            "reloaded": True,
            "owner": owner,
            "cutover": cutover,
            "flags": self._serialize_flags(),
            "reload_semantics": {
                "supports_explicit_yaml_reload": True,
                "env_changes_require_restart": True,
                "note": "Changes in .env.local require restarting backend and labops-ai-sre-worker processes.",
            },
        }

    def prepare_notification_cutover(self, *, actor: str = "admin", now_override: Optional[datetime] = None) -> Dict[str, Any]:
        """Explicitly record V2-to-V4 cutover boundary and mark pre-cutover events SKIPPED_PRE_CUTOVER."""
        self._sync_components_from_config()
        owner = resolve_notification_owner(self.config)
        return self.policy.sync_notification_ownership(
            owner,
            actor=actor,
            mark_existing_unconsumed_as_precutover=(owner == "V4_ACTIVE"),
            now_override=now_override,
        )

    def revoke_principal_state(
        self,
        *,
        username: Optional[str] = None,
        zalo_user_id: Optional[str] = None,
        reason: str = "PRINCIPAL_REVOKED",
    ) -> Dict[str, int]:
        """Revoke active V4 sessions, tasks/watches, open proposals, and unsent outbox rows for a user."""
        ids = [x for x in (username, zalo_user_id) if x and str(x).strip()]
        sessions = self.memory.revoke_principal_sessions(ids)
        tasks_cancelled = 0
        for pid in sorted(set(ids)):
            tasks_cancelled += self.tasks.revoke_owner_access(pid)
        proposals = self.approvals.revoke_actor_proposals(ids, reason=reason)
        outbox_suppressed = self.outbox.suppress_recipient_pending(ids, reason=reason)
        return {
            "sessions_revoked": sessions,
            "tasks_cancelled": tasks_cancelled,
            "proposals_revoked": proposals,
            "outbox_suppressed": outbox_suppressed,
        }

    def _serialize_flags(self) -> Dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "event_engine": self.config.event_engine,
            "notification_v2": self.config.notification_v2,
            "shadow_mode": self.config.shadow_mode,
            "task_manager": self.config.task_manager,
            "natural_language": self.config.natural_language,
            "proactive_investigation": self.config.proactive_investigation,
            "write_actions": self.config.write_actions,
            "watchdog": self.config.watchdog.enabled,
            "retention": self.config.retention.enabled,
            "retention_dry_run": self.config.retention.dry_run,
        }

    def run_worker_cycle(self, *, now_override: Optional[datetime] = None) -> Dict[str, Any]:
        """Run single non-LLM background SRE cycle when flags are enabled."""
        if not self.config.enabled:
            return {"enabled": False}
        self._sync_components_from_config()
        snap = self.telemetry.collect_snapshot()
        owner = resolve_notification_owner(self.config)
        out: Dict[str, Any] = {
            "enabled": True,
            "notification_owner": owner,
            "ownership": self.policy.sync_notification_ownership(owner, now_override=now_override),
            "reconciled_approvals": self.approvals.reconcile_stuck_executing(now_override=now_override),
        }
        has_changes = False
        if self.config.event_engine:
            out["reconcile"] = self.reconciler.reconcile()
            with self.db.session_scope() as session:
                out["recovered"] = self.policy.lifecycle.evaluate_recovery_from_snapshot(session, snap, now_override=now_override)
            if out["recovered"]:
                has_changes = True
        if self.config.notification_v2 or self.config.shadow_mode:
            out["policy"] = self.policy.process_events()
            out["reminders"] = self.policy.run_incident_reminders(now_override=now_override)
            out["reaped"] = self.outbox.reap_expired_leases(now_override=now_override)
            if (out["policy"] or {}).get("processed", 0) > 0:
                has_changes = True
            if owner == "V4_ACTIVE":
                out["digests_enqueued"] = self._run_digests_step(now_override=now_override, has_changes=has_changes)
        if self.config.proactive_investigation:
            out["investigations_triggered"] = self._run_proactive_investigations(max_per_cycle=2)
        if self.config.task_manager:
            out["scheduler"] = self.scheduler.tick(
                snapshot=snap,
                now_override=now_override,
                shadow_mode=bool(self.config.shadow_mode),
            )
        if self.config.retention.enabled:
            out["retention"] = self.retention.run(now_override=now_override)

        hb_dt = now_override or datetime.now(timezone.utc)
        if hb_dt.tzinfo is None:
            hb_dt = hb_dt.replace(tzinfo=timezone.utc)
        else:
            hb_dt = hb_dt.astimezone(timezone.utc)
        with self.db.session_scope() as session:
            cur = session.get(V4WorkerCursorRecord, "worker:v4:last_cycle")
            hb_meta = {"owner": owner, "has_changes": has_changes}
            if cur is None:
                session.add(
                    V4WorkerCursorRecord(
                        cursor_name="worker:v4:last_cycle",
                        cursor_value=hb_dt.isoformat(),
                        metadata_json=hb_meta,
                        updated_at=hb_dt,
                    )
                )
            else:
                cur.cursor_value = hb_dt.isoformat()
                cur.metadata_json = hb_meta
                cur.updated_at = hb_dt

        if self.config.watchdog.enabled:
            out["watchdog"] = self.watchdog.maybe_ping(now_override=now_override)
        return out

    def _read_worker_cycle_heartbeat(self, *, now_override: Optional[datetime] = None) -> Dict[str, Any]:
        ref_now = now_override or datetime.now(timezone.utc)
        if ref_now.tzinfo is None:
            ref_now = ref_now.replace(tzinfo=timezone.utc)
        else:
            ref_now = ref_now.astimezone(timezone.utc)
        cursor_ts: Optional[str] = None
        cursor_meta: Dict[str, Any] = {}
        sched_ts: Optional[str] = None
        shadow_meta: Dict[str, Any] = {}
        try:
            with self.db.session_scope() as session:
                c_wk = session.get(V4WorkerCursorRecord, "worker:v4:last_cycle")
                if c_wk is not None:
                    cursor_ts = c_wk.cursor_value or None
                    cursor_meta = dict(c_wk.metadata_json or {})
                c_sc = session.get(V4WorkerCursorRecord, "scheduler:last_tick")
                if c_sc is not None:
                    sched_ts = c_sc.cursor_value or None
                c_sh = session.get(V4WorkerCursorRecord, SHADOW_METRICS_KEY)
                if c_sh is not None:
                    shadow_meta = dict(c_sh.metadata_json or {})
        except Exception:
            pass

        worker_file_hb: Optional[str] = None
        worker_polling = False
        status_path = os.path.join(self.data_dir, "zalo_worker_status.json")
        if os.path.exists(status_path):
            try:
                with open(status_path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    worker_file_hb = raw.get("last_heartbeat")
                    worker_polling = bool(raw.get("is_polling"))
            except Exception:
                pass

        best_ts = cursor_ts or worker_file_hb
        age_s: Optional[float] = None
        if best_ts:
            try:
                dt = datetime.fromisoformat(str(best_ts).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_s = round(max(0.0, (ref_now - dt.astimezone(timezone.utc)).total_seconds()), 2)
            except ValueError:
                age_s = None

        return {
            "last_cycle_at": cursor_ts,
            "worker_status_heartbeat": worker_file_hb,
            "worker_polling": worker_polling,
            "scheduler_last_tick": sched_ts,
            "heartbeat_age_seconds": age_s,
            "cycle_meta": cursor_meta,
            "shadow_meta": shadow_meta,
        }

    def get_health(self, *, now_override: Optional[datetime] = None) -> Dict[str, Any]:
        self.watchdog.require_scheduler_tick = bool(self.config.task_manager)
        wd = self.watchdog.evaluate_progress_checks(now_override=now_override)
        q = self.outbox.get_metrics(now_override=now_override)
        db_m = self.db.get_db_metrics()
        ret_st = self.retention.get_status()
        hb = self._read_worker_cycle_heartbeat(now_override=now_override)
        owner = resolve_notification_owner(self.config)
        ownership_state = self.policy.get_notification_ownership_state()

        db_ok = bool((wd.get("checks") or {}).get("db_reachability", {}).get("ok"))
        db_state = "HEALTHY" if db_ok else "OFFLINE"

        if not self.config.enabled:
            worker_state = "DISABLED"
        elif not db_ok:
            worker_state = "OFFLINE"
        elif hb["heartbeat_age_seconds"] is None:
            worker_state = "NOT_CONFIGURED"
        elif hb["heartbeat_age_seconds"] > float(self.config.watchdog.grace_seconds):
            worker_state = "OFFLINE"
        else:
            worker_state = "HEALTHY"

        if not self.config.watchdog.enabled:
            wd_state = "DISABLED"
        elif not wd.get("configured"):
            wd_state = "NOT_CONFIGURED"
        elif not wd.get("healthy"):
            wd_state = "DEGRADED"
        else:
            wd_state = "HEALTHY"

        if not self.config.natural_language:
            ai_state = "DISABLED"
        elif self.runtime.breaker.is_open:
            ai_state = "DEGRADED"
        else:
            ai_state = "HEALTHY"

        by_st = q.get("by_status") or {}
        if not self.config.enabled or (not self.config.notification_v2 and not self.config.shadow_mode):
            outbox_state = "DISABLED"
        elif by_st.get("UNKNOWN_DELIVERY", 0) > 0 or by_st.get("DLQ", 0) > 0:
            outbox_state = "DEGRADED"
        else:
            outbox_state = "HEALTHY"

        components = {
            "database": db_state,
            "worker": worker_state,
            "outbox": outbox_state,
            "watchdog": wd_state,
            "ai_gateway": ai_state,
            "retention": ret_st["status"],
        }

        if not self.config.enabled:
            overall = "DISABLED"
        elif db_state == "OFFLINE" or worker_state == "OFFLINE":
            overall = "OFFLINE"
        elif worker_state == "NOT_CONFIGURED":
            overall = "NOT_CONFIGURED"
        elif any(v == "DEGRADED" for v in components.values()) or not wd.get("healthy"):
            overall = "DEGRADED"
        else:
            overall = "HEALTHY"

        return {
            "status": overall,
            "valid_health_states": ["HEALTHY", "DEGRADED", "OFFLINE", "NOT_CONFIGURED", "DISABLED"],
            "components": components,
            "v4_enabled": self.config.enabled,
            "flags": self._serialize_flags(),
            "notification_ownership": {**ownership_state, "effective_owner": owner},
            "ingress_mode": self.config.ingress_mode,
            "agent_runtime": self.config.agent_runtime,
            "ai_circuit_breaker_open": self.runtime.breaker.is_open,
            "outbox_summary": q,
            "watchdog": wd,
            "database": db_m,
            "retention": ret_st,
            "config_reload_semantics": {
                "supports_explicit_yaml_reload": True,
                "env_changes_require_restart": True,
                "note": "Changes to .env.local require restarting backend and labops-ai-sre-worker.",
            },
        }

    def get_metrics(self, *, now_override: Optional[datetime] = None) -> Dict[str, Any]:
        out_m = self.outbox.get_metrics(now_override=now_override)
        ev_m = self.events.get_consumer_lag(now_override=now_override)
        wd = self.watchdog.evaluate_progress_checks(now_override=now_override)
        hb = self._read_worker_cycle_heartbeat(now_override=now_override)
        rt_m = self.runtime.get_runtime_metrics()
        db_m = self.db.get_db_metrics()
        ret_st = self.retention.get_status()
        owner = resolve_notification_owner(self.config)
        ownership_state = self.policy.get_notification_ownership_state()

        with self.db.session_scope() as session:
            quota = self.policy.quota.get_usage(session, now_override=now_override)

        by_st = out_m.get("by_status") or {}
        backlog_count = int(by_st.get("PENDING", 0)) + int(by_st.get("FAILED_RETRYABLE", 0)) + int(by_st.get("CLAIMED", 0))
        ages = list((out_m.get("oldest_pending_age_seconds_by_priority") or {}).values())
        oldest_pending_age = round(max(ages), 2) if ages else 0.0
        sh_meta = hb.get("shadow_meta") or {}

        return {
            "worker": {
                "heartbeat_age_seconds": hb["heartbeat_age_seconds"],
                "last_cycle_at": hb["last_cycle_at"],
                "worker_status_heartbeat": hb["worker_status_heartbeat"],
                "worker_polling": hb["worker_polling"],
                "scheduler_last_tick": hb["scheduler_last_tick"],
                "watchdog_checks": wd.get("checks"),
            },
            "events": ev_m,
            "outbox": {
                **out_m,
                "backlog_count": backlog_count,
                "oldest_pending_age_seconds": oldest_pending_age,
            },
            "delivery_outcomes": {
                "sent": int(by_st.get("SENT", 0)),
                "failed_retryable": int(by_st.get("FAILED_RETRYABLE", 0)),
                "dlq": int(by_st.get("DLQ", 0)),
                "unknown_delivery": int(by_st.get("UNKNOWN_DELIVERY", 0)),
                "suppressed": int(by_st.get("SUPPRESSED", 0)),
                "expired": int(by_st.get("EXPIRED", 0)),
            },
            "shadow": {
                "active": bool(self.config.shadow_mode),
                "would_send_count": int(sh_meta.get("count", 0)),
                "decisions_total": int(sh_meta.get("decisions_total", 0)),
                "last_evaluated_at": sh_meta.get("last_evaluated_at"),
                "recent_would_send": list(sh_meta.get("would_send") or [])[-20:],
            },
            "notification_ownership": {
                **ownership_state,
                "effective_owner": owner,
            },
            "quota": {
                **quota,
                "provider_confirmed_usage": None,
                "estimate_only": not bool(quota.get("provider_confirmed")),
            },
            "ai": {
                "avg_latency_ms": rt_m["avg_latency_ms"],
                "p95_latency_ms": rt_m["p95_latency_ms"],
                "timeout_calls": rt_m["timeout_calls"],
                "timeout_rate": rt_m["timeout_rate"],
                "error_calls": rt_m["error_calls"],
                "error_rate": rt_m["error_rate"],
                "total_calls": rt_m["total_calls"],
            },
            "circuit_breaker": rt_m["circuit_breaker"],
            "ai_circuit_breaker_open": self.runtime.breaker.is_open,
            "database": db_m,
            "retention": ret_st,
            "watchdog": wd,
        }

    def get_event_coverage(self) -> Dict[str, Any]:
        items = [{"event_type": k, **v} for k, v in sorted(EVENT_CATALOG.items())]
        counts: Dict[str, int] = {}
        for it in items:
            st = it["status"]
            counts[st] = counts.get(st, 0) + 1
        return {"summary": counts, "events": items}
