"""
LabOps Personal AI Agent V4 — Unified Facade wiring Phases 1-7 into Zalo & Dashboard.
Feature-flagged OFF by default (`config.enabled == False`).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import select

from app.db.models import (
    IncidentRecord,
    V4InvestigationRecord,
    V4WorkerCursorRecord,
)
from app.db.session import DatabaseManager
from app.services.assistant_v4.admin_action_catalog import (
    ADMIN_ACTION_CATALOG,
    check_disambiguation,
    find_matching_members,
    get_action_spec,
)
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
    VN_TZ,
    resolve_notification_owner,
)
from app.services.assistant_v4.retention_service import RetentionMaintenanceService

if TYPE_CHECKING:
    from app.services.zalo_assistant.identity_rbac import ZaloIdentityRBACService, ZaloPrincipal
    from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
    from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector

logger = logging.getLogger("labops.v4.facade")


def _fmt_vn_time(iso_str: Any) -> str:
    if not isinstance(iso_str, str) or not iso_str.strip():
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y")
    except Exception:
        return str(iso_str)[:16]


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
        service_context: Optional[Dict[str, Any]] = None,
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

        self.service_context: Dict[str, Any] = dict(service_context or {})
        self.service_context.setdefault("identity_rbac", identity_rbac)
        self.service_context.setdefault("telemetry_collector", telemetry_collector)
        self.service_context.setdefault("incident_engine", incident_engine)
        self.service_context["v4_facade"] = self

        self.approvals = ActionProposalService(
            db_manager,
            write_actions_enabled=self.config.write_actions,
            zalo_r3_actions_enabled=self.config.zalo_r3_actions_enabled,
            zalo_r4_actions_enabled=self.config.zalo_r4_actions_enabled,
            secret_delivery_mode=self.config.zalo_secret_delivery_mode,
            short_otp_confirm=self.config.zalo_short_otp_confirm,
            incident_engine=incident_engine,
            identity_reloader=_reload_principal,
            service_context=self.service_context,
        )
        self.tools = SecureToolExecutor(
            data_dir,
            db_manager=db_manager,
            telemetry_collector=telemetry_collector,
            incident_engine=incident_engine,
            identity_reloader=_reload_principal,
            task_service=self.tasks,
            proposal_service=self.approvals,
            service_context=self.service_context,
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
            out = self.runtime.run_turn(
                question=clean,
                principal=principal,
                session_id=sess["session_id"],
                channel=channel,
                chat_id=effective_chat,
                allowed_tools=[],
            )
            if out.get("degraded") and str(out.get("reply") or "").startswith("⚠️ AI Gateway"):
                out["reply"] = (
                    "ℹ️ Cổng AI tổng quát (9Router) hiện đang ngoại tuyến nên chưa trả lời câu hỏi kiến thức tự do.\n"
                    "• Tuy nhiên, mọi thao tác quản trị & vận hành LabOps (đơn SSH, GPU/máy chủ, cảnh báo, nhắc việc, sự cố, báo cáo) "
                    "vẫn hoạt động trực tiếp bằng tiếng Việt tự nhiên."
                )
            out.setdefault("route", "v4")
            out.setdefault("intent", intent)
            out.setdefault("session_id", sess["session_id"])
            out.setdefault("context_scope", f"{principal.role}:{principal.username}")
            return out

        if intent == "WATCH_OR_REMINDER_NL":
            proposal = self._parse_nl_watch_or_reminder(clean, principal)
            if proposal is not None:
                try:
                    self.memory.append_turn_messages(
                        session_id=sess["session_id"],
                        expected_version=None,
                        entries=[
                            {"role": "user", "content": clean},
                            {"role": "assistant", "content": str(proposal["reply"])},
                        ],
                    )
                except Exception:
                    pass
                proposal.setdefault("route", "v4")
                proposal.setdefault("intent", intent)
                proposal.setdefault("session_id", sess["session_id"])
                proposal.setdefault("context_scope", f"{principal.role}:{principal.username}")
                return proposal

        if self.config.write_actions:
            admin_routed = self.try_handle_admin_action_intent(
                text=clean,
                principal=principal,
                chat_id=effective_chat,
                session_id=sess["session_id"],
            )
            if admin_routed is not None:
                try:
                    self.memory.append_turn_messages(
                        session_id=sess["session_id"],
                        expected_version=None,
                        entries=[
                            {"role": "user", "content": clean},
                            {"role": "assistant", "content": str(admin_routed.get("memory_safe_reply") or admin_routed["reply"])},
                        ],
                    )
                except Exception:
                    pass
                admin_routed.setdefault("route", "v4")
                admin_routed.setdefault("intent", admin_routed.get("intent") or "ADMIN_PARITY_ACTION")
                admin_routed.setdefault("session_id", sess["session_id"])
                admin_routed.setdefault("context_scope", f"{principal.role}:{principal.username}")
                return admin_routed

        try:
            prior_history = self.memory.get_bounded_context(
                session_id=sess["session_id"],
                principal_id=principal.username or principal.zalo_user_id,
                channel=channel,
                chat_id=effective_chat,
            )
        except Exception:
            prior_history = []

        out = self.runtime.run_turn(
            question=clean,
            principal=principal,
            session_id=sess["session_id"],
            channel=channel,
            chat_id=effective_chat,
            allowed_tools=None,
        )
        if out.get("degraded") and getattr(self.ai_client, "base_url", None):
            fallback = self._deterministic_v4_tool_fallback(
                question=clean,
                principal=principal,
                corr_id=str(out.get("correlation_id") or ""),
                prior_history=prior_history,
            )
            if fallback is not None:
                out["reply"] = fallback["reply"]
                out["tool_traces"] = list(out.get("tool_traces") or []) + list(fallback.get("tool_traces") or [])
                out["evidence_timestamps"] = list(fallback.get("evidence_timestamps") or [])
                out["fallback_used"] = True
                try:
                    with self.db.session_scope() as s:
                        from app.db.models import V4ChatMessageRecord

                        last_msg = (
                            s.execute(
                                select(V4ChatMessageRecord)
                                .where(V4ChatMessageRecord.session_id == sess["session_id"])
                                .order_by(V4ChatMessageRecord.seq.desc())
                                .limit(1)
                            )
                            .scalars()
                            .first()
                        )
                        if (
                            last_msg is not None
                            and last_msg.role == "assistant"
                            and str(last_msg.content_redacted or "").startswith(
                                ("⚠️ AI Gateway", "🤖 LabOps Personal AI V4 (Chế độ công cụ trực tiếp")
                            )
                        ):
                            last_msg.content_redacted = str(fallback["reply"])[:4000]
                        else:
                            self.memory.append_turn_messages(
                                session_id=sess["session_id"],
                                expected_version=None,
                                entries=[
                                    {"role": "user", "content": clean},
                                    {"role": "assistant", "content": str(fallback["reply"])},
                                ],
                            )
                except Exception:
                    pass
        out.setdefault("route", "v4")
        out.setdefault("intent", intent)
        out.setdefault("session_id", sess["session_id"])
        out.setdefault("context_scope", f"{principal.role}:{principal.username}")
        return out

    @staticmethod
    def _format_ssh_request_dossier(
        r: Dict[str, Any],
        *,
        member_info: Optional[Dict[str, Any]] = None,
        show_action_hints: bool = True,
    ) -> List[str]:
        req_id = str(r.get("id") or "")
        uname = str(r.get("username") or (member_info or {}).get("username") or "")
        full_name = str(r.get("full_name") or (member_info or {}).get("full_name") or uname)
        mcode = str(r.get("member_code") or (member_info or {}).get("member_code") or "N/A")
        mcat = str(r.get("member_category") or (member_info or {}).get("member_category") or "STUDENT")
        dept = str(r.get("department_class") or (member_info or {}).get("department_class") or "").strip()
        dept_str = f" • {dept}" if dept else ""
        sup_name = str(r.get("supervisor_name") or (member_info or {}).get("supervisor_name") or "Chưa khai báo")
        proj = str(r.get("project_title") or "Chưa nêu tên đề tài")
        reason = str(r.get("registration_reason") or "").strip() or "Chưa ghi rõ"
        purpose = str(r.get("ssh_purpose") or "").strip() or "Chưa ghi rõ"
        tasks = str(r.get("expected_tasks") or "").strip() or "Chưa ghi rõ"
        gpu_req = bool(r.get("gpu_requested"))
        gpu_just = str(r.get("gpu_justification") or "").strip()
        gpu_str = f"Có ({gpu_just})" if (gpu_req and gpu_just) else ("Có (SHARED)" if gpu_req else "Không")
        dur_days = r.get("requested_duration_days")
        dur_str = f"{dur_days} ngày" if dur_days else ("Không giới hạn" if mcat == "LECTURER" else "30 ngày (mặc định)")
        status_str = str(r.get("status") or "PENDING_APPROVAL")
        sub_at = _fmt_vn_time(r.get("submitted_at"))
        rem_h = round(int(r.get("remaining_review_seconds") or 0) / 3600.0, 1)

        rec = dict(r.get("recommendation") or {})
        if not rec.get("recommended_pool_id"):
            try:
                from app.services.mram_service import mram_service

                rec = mram_service.compute_resource_recommendation(
                    member_category=mcat,
                    requested_duration_days=dur_days,
                    gpu_requested=gpu_req,
                )
            except Exception:
                pass
        rat = rec.get("rationale") or {}
        host_lbl = rec.get("recommended_host_id") or r.get("recommended_host") or "workstation"
        pool_id = rec.get("recommended_pool_id") or r.get("recommended_pool_id") or "N/A"
        mount_pt = rec.get("recommended_mount_point") or r.get("recommended_mount_point") or ""
        free_gib = rat.get("chosen_pool_net_allocatable_gib")
        mem_cnt = rat.get("chosen_pool_assigned_members")
        pool_load = (
            f" (đang có {mem_cnt} TV, trống {free_gib} GiB)"
            if (mem_cnt is not None and free_gib is not None)
            else ""
        )
        mount_txt = f" ({mount_pt})" if mount_pt else ""
        spec_line = (
            f"{rec.get('cpu_cores', 4)} vCPU | {rec.get('ram_gb', 16)} GiB RAM | "
            f"{rec.get('storage_quota_gb', 50)} GiB Ổ cứng | GPU: {rec.get('gpu_mode', 'NONE')}"
        )

        purpose_task_line = (
            f"• Mục đích & Tác vụ dự kiến: {purpose}"
            if (purpose.strip().lower() == tasks.strip().lower() or tasks == "Chưa ghi rõ")
            else f"• Mục đích SSH & Tác vụ dự kiến: {purpose} | Tác vụ: {tasks}"
        )

        lines = [
            f"📋 CHI TIẾT ĐƠN XIN CẤP TÀI NGUYÊN SSH ({req_id})",
            f"• Người xin cấp: {full_name} (@{uname} • Mã: {mcode} • {mcat}{dept_str})",
            f"• Giảng viên hướng dẫn (GVHD): {sup_name}",
            f"• Đề tài / Dự án: {proj}",
            f"• Lý do xin cấp: {reason}",
            purpose_task_line,
            f"• Yêu cầu GPU & Thời hạn: {gpu_str} | Thời hạn: {dur_str}",
            f"• Trạng thái đơn: {status_str} (Nộp lúc: {sub_at} • SLA còn ~{rem_h}h)",
            f"• Đề xuất cấp phát trên {host_lbl}: Ổ {pool_id}{mount_txt}{pool_load} • Cấu hình: {spec_line}",
        ]
        if show_action_hints and status_str in {"PENDING_APPROVAL", "NEED_INFO"}:
            lines.append(
                f"• Gợi ý xử lý nhanh: Nhắn \"@{uname} đồng ý duyệt cấp tài nguyên\" hoặc \"@{uname} tôi từ chối cấp vì lý do ...\""
            )
        return lines

    def _deterministic_v4_tool_fallback(
        self,
        *,
        question: str,
        principal: ZaloPrincipal,
        corr_id: str = "",
        prior_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Read-only V4 tool fallback when 9Router gateway is offline/unconfigured."""
        from app.services.mram_service import _strip_vn_accents, mram_service

        raw_q = str(question or "").strip()
        q = raw_q.lower()
        q_norm = _strip_vn_accents(raw_q)

        recent_ssh_context = any(
            (
                "sshreq-" in str(m.get("content") or "").lower()
                or "đơn xin cấp ssh" in str(m.get("content") or "").lower()
                or "chi tiết đơn xin cấp" in str(m.get("content") or "").lower()
            )
            for m in (prior_history or [])[-4:]
        )

        ssh_keywords = (
            "don",
            "duyet",
            "kiem duyet",
            "xet duyet",
            "phe duyet",
            "cho duyet",
            "yeu cau",
            "ssh request",
            "mram",
            "xin cap",
            "cap ssh",
            "ly do",
            "giang vien",
            "ginagr",
            "gvhd",
            "huong dan",
            "de tai",
            "muc dich",
        )
        followup_detail_phrases = (
            "thong tin hon",
            "chi tiet",
            "cu the",
            "la gi",
            "do ai",
            "ai huong dan",
            "xem them",
        )
        is_ssh_review_query = any(k in q_norm for k in ssh_keywords) or (
            recent_ssh_context and any(p in q_norm for p in followup_detail_phrases)
        )

        at_mentions = [
            m.lower() for m in re.findall(r"@([a-z0-9_.-]+)", q) if m.lower() != "admin"
        ]
        matched_reqs: List[Dict[str, Any]] = []
        if principal.is_admin:
            try:
                m_info = mram_service.match_pending_requests_by_query(raw_q)
                matched_reqs = list(m_info.get("matches") or [])
            except Exception:
                matched_reqs = []
            singular_ref_phrases = (
                "ban nay",
                "ban ay",
                "ban do",
                "sinh vien nay",
                "tai khoan nay",
                "don nay",
                "don tren",
                "nguoi nay",
                "em nay",
                "anh nay",
                "chi nay",
            )
            plural_list_markers = ("cac don", "danh sach", "tat ca", "bao nhieu don", "co don ssh nao", "nhung don")
            if (
                not matched_reqs
                and not at_mentions
                and not any(p in q_norm for p in plural_list_markers)
                and (any(p in q_norm for p in singular_ref_phrases) or any(p in q_norm for p in followup_detail_phrases))
            ):
                for prev in reversed(prior_history or []):
                    if str(prev.get("role") or "").lower() != "assistant":
                        continue
                    prev_content = str(prev.get("content") or "")
                    prev_req_ids = list(dict.fromkeys(re.findall(r"\bsshreq-[a-z0-9]+\b", prev_content.lower())))
                    if len(prev_req_ids) == 1:
                        try:
                            ctx_match = mram_service.match_pending_requests_by_query(prev_req_ids[0])
                            matched_reqs = list(ctx_match.get("matches") or [])
                        except Exception:
                            matched_reqs = []
                    break

        selected_calls: List[tuple[str, Dict[str, Any]]] = []
        mode = "general"

        if principal.is_admin and matched_reqs:
            mode = "specific_request"
            for r in matched_reqs[:2]:
                selected_calls.append(("get_request", {"target_id": str(r["id"])}))
                if r.get("username"):
                    selected_calls.append(("get_member_info", {"username": str(r["username"])}))
        elif at_mentions:
            mode = "member_lookup"
            selected_calls.append(("get_member_info", {"username": at_mentions[0]}))
        elif principal.is_admin and is_ssh_review_query:
            mode = "ssh_list"
            selected_calls.append(("list_pending_requests", {}))
            if any(k in q_norm for k in ("may chu", "server", "cpu", "ram", "suc khoe", "he thong")):
                selected_calls.append(("get_node_status", {}))
        elif not principal.is_admin and (
            is_ssh_review_query
            or any(
                k in q_norm
                for k in (
                    "don cua toi",
                    "tai khoan cua toi",
                    "ho so cua toi",
                    "ssh cua toi",
                    "duoc duyet chua",
                    "da duyet chua",
                    "trang thai don",
                    "xin cap cua toi",
                )
            )
        ):
            mode = "member_lookup"
            selected_calls.append(("get_member_info", {"username": principal.username}))
        elif any(k in q_norm for k in ("gpu", "vram", "nhiet", "cuda", "a30")):
            selected_calls = [("get_gpu_metrics", {}), ("get_node_status", {}), ("list_my_jobs", {})]
        elif any(k in q_norm for k in ("su co", "incident", "canh bao", "loi")):
            selected_calls = [("list_incident_history", {}), ("get_node_status", {})]
        elif any(k in q_norm for k in ("job", "tac vu", "train", "tien trinh")):
            selected_calls = [("list_my_jobs", {}), ("get_gpu_metrics", {})]
        elif any(k in q_norm for k in ("o cung", "disk", "dung luong", "storage")) or re.search(r"\bo\b", q_norm):
            selected_calls = [("get_storage_usage", {}), ("get_node_status", {})]
        elif any(k in q_norm for k in ("vpn", "wireguard")):
            selected_calls = [("get_vpn_status", {}), ("get_node_status", {})]
        else:
            selected_calls = [
                ("get_node_status", {}),
                ("get_gpu_metrics", {}),
                ("list_my_jobs", {}),
                ("list_incident_history", {}),
            ]

        traces: List[Dict[str, Any]] = []
        timestamps: List[str] = []
        lines: List[str] = [
            "🤖 LabOps Personal AI V4 (Chế độ công cụ trực tiếp):",
            f"• Quyền truy cập: {principal.role} (@{principal.username})",
        ]

        executed_results: List[tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        for tname, targs in selected_calls[:4]:
            res = self.tools.execute_tool(
                tname,
                targs,
                principal,
                correlation_id=corr_id or None,
                source="v4_fallback",
                question=question,
            )
            traces.append(
                {
                    "tool": tname,
                    "status": res.get("status"),
                    "observed_at": res.get("observed_at"),
                    "freshness": res.get("freshness"),
                    "error_code": res.get("error_code"),
                }
            )
            obs = res.get("observed_at")
            if obs:
                timestamps.append(str(obs))
            executed_results.append((tname, targs, res))

        if mode == "specific_request":
            members_by_user: Dict[str, Dict[str, Any]] = {}
            req_objs: List[Dict[str, Any]] = []
            for tname, _, res in executed_results:
                if res.get("status") != "OK":
                    continue
                data = res.get("data") or {}
                if tname == "get_member_info" and data.get("member"):
                    mem = data["member"]
                    members_by_user[str(mem.get("username") or "").lower()] = mem
                elif tname == "get_request" and data.get("request"):
                    req_objs.append(data["request"])
            for r in req_objs:
                mem_inf = members_by_user.get(str(r.get("username") or "").lower())
                lines.extend(self._format_ssh_request_dossier(r, member_info=mem_inf, show_action_hints=True))
        elif mode == "member_lookup":
            for tname, targs, res in executed_results:
                if res.get("status") != "OK":
                    uname_miss = targs.get("username") or (at_mentions[0] if at_mentions else "")
                    if res.get("status") == "DENIED" or res.get("error_code") in {"PERMISSION_DENIED", "RBAC_DENIED"}:
                        lines.append(
                            f"• ⛔ Từ chối truy cập (RBAC): Bạn không có quyền xem hồ sơ hoặc đơn xin cấp SSH của @{uname_miss}."
                        )
                    else:
                        lines.append(f"• Không tìm thấy hồ sơ thành viên hoặc đơn xin cấp SSH cho @{uname_miss}.")
                        if principal.is_admin:
                            lines.append("• Admin có thể gõ `/requests` để xem danh sách các đơn đang chờ duyệt.")
                    continue
                mem = (res.get("data") or {}).get("member") or {}
                lines.append(
                    f"👤 HỒ SƠ THÀNH VIÊN: {mem.get('full_name') or mem.get('username')} (@{mem.get('username')})"
                )
                lines.append(
                    f"• Mã: {mem.get('member_code') or 'N/A'} | Phân loại: {mem.get('member_category') or mem.get('role') or 'N/A'} | Trạng thái: {mem.get('status') or 'N/A'}"
                )
                if mem.get("supervisor_name"):
                    lines.append(f"• Giảng viên hướng dẫn (GVHD): {mem.get('supervisor_name')}")
                lines.append(
                    f"• Hạ tầng & Định mức: Máy chủ {mem.get('assigned_server') or 'N/A'} | Workspace: {mem.get('workspace_name') or 'N/A'} | SSH Port: {mem.get('ssh_port') or 'N/A'}"
                )
                lines.append(
                    f"• Tài nguyên: {mem.get('cpu_limit_cores') or 'N/A'} vCPU | {mem.get('ram_limit_gb') or 'N/A'} GiB RAM | {mem.get('storage_quota_gb') or 'N/A'} GiB Storage | GPU: {mem.get('gpu_permissions') or 'NONE'}"
                )
                rec_reqs = mem.get("recent_ssh_requests") or []
                if rec_reqs:
                    lines.append(f"• Đơn xin cấp SSH gần đây ({len(rec_reqs)}):")
                    for r in rec_reqs[:3]:
                        lines.extend(
                            self._format_ssh_request_dossier(r, member_info=mem, show_action_hints=principal.is_admin)
                        )
                else:
                    lines.append("• Đơn xin cấp SSH: Không có đơn nào được ghi nhận.")
        else:
            for tname, _, res in executed_results:
                data = res.get("data") or {}
                if res.get("status") != "OK":
                    lines.append(f"• {tname}: {res.get('status')} ({res.get('error_code') or 'ERR'})")
                    continue

                if tname == "get_node_status":
                    for h in data.get("hosts") or []:
                        st = h.get("status")
                        if st in {"ONLINE", "DEGRADED"}:
                            cpu = f"{h.get('cpu_usage_percent')}%" if h.get("cpu_usage_percent") is not None else "N/A"
                            ram = f"{h.get('ram_used_gb')}/{h.get('ram_total_gb')} GiB" if h.get("ram_total_gb") else "N/A"
                            lines.append(f"• Máy chủ {h.get('hostname')}: {st} | CPU {cpu} | RAM {ram}")
                        else:
                            lines.append(f"• Máy chủ {h.get('hostname')}: {st} ({h.get('freshness_status')})")
                elif tname == "get_gpu_metrics":
                    for g in data.get("gpus") or []:
                        if g.get("host_online"):
                            util = f"{g.get('utilization_gpu')}%" if g.get("utilization_gpu") is not None else "N/A"
                            temp = f"{g.get('temp_celsius')}°C" if g.get("temp_celsius") is not None else "N/A"
                            vram = f"{g.get('vram_used_mb')}/{g.get('vram_total_mb')} MiB" if g.get("vram_total_mb") else "N/A"
                            lines.append(f"• GPU {g.get('model')} ({g.get('host_id')}): Tải {util}, Nhiệt độ {temp}, VRAM {vram}")
                        else:
                            lines.append(f"• GPU {g.get('model')} ({g.get('host_id')}): Máy chủ ngoại tuyến")
                elif tname == "get_storage_usage":
                    for s in data.get("storage") or []:
                        lines.append(f"• Lưu trữ {s.get('hostname')}: {s.get('disk_used_tb')}/{s.get('disk_total_tb')} TB ({s.get('disk_usage_percent')}%)")
                    if principal.is_admin:
                        try:
                            dist = mram_service.get_host_pool_distribution("workstation", required_storage_gb=50.0)
                            if dist:
                                elig = [d for d in dist if d.get("eligible_for_request")]
                                elig.sort(
                                    key=lambda d: (
                                        int(d.get("priority_rank", 0)),
                                        -float(d.get("effective_free_per_member_gib") or 0.0),
                                        int(d.get("assigned_member_count") or 0),
                                        -float(d.get("net_allocatable_gib") or 0.0),
                                        str(d.get("pool_id") or ""),
                                    )
                                )
                                rec_pid = str(elig[0]["pool_id"]) if elig else ""
                                lines.append("• Phân bổ & Độ trống các ổ cứng trên workstation (ưu tiên sdd/sdb, hạn chế HDD3):")
                                for d in dist:
                                    pool_meta = d.get("pool") or {}
                                    lbl = pool_meta.get("label") or d.get("pool_id")
                                    dev = d.get("device") or ""
                                    mnt = d.get("mount_point") or ""
                                    free_g = d.get("net_allocatable_gib")
                                    mcnt = d.get("assigned_member_count")
                                    eff = d.get("effective_free_per_member_gib")
                                    tier_lbl = " [ƯU TIÊN]" if int(d.get("priority_rank", 0)) == 0 else " [HẠN CHẾ - DỰ PHÒNG]"
                                    badge = (
                                        " [Đề xuất cấp tiếp theo]"
                                        if str(d.get("pool_id")) == rec_pid
                                        else ("" if d.get("eligible") else " [Không khả dụng]")
                                    )
                                    lines.append(
                                        f"  - {lbl} ({dev}, {mnt}){tier_lbl}: Trống khả dụng {free_g} GiB | {mcnt} thành viên | Hiệu dụng {eff} GiB/TV{badge}"
                                    )
                        except Exception:
                            pass
                elif tname == "list_my_jobs":
                    jobs = data.get("jobs") or []
                    lines.append(f"• Tác vụ tính toán nhìn thấy: {len(jobs)}")
                    for j in jobs[:5]:
                        lines.append(f"  - @{j.get('owner')}: {j.get('name')} [{j.get('status')}] (VRAM: {j.get('vram_mb', 0)} MiB)")
                elif tname == "list_incident_history":
                    incs = [i for i in (data.get("incidents") or []) if i.get("status") != "RESOLVED"]
                    lines.append(f"• Sự cố đang mở: {len(incs)}")
                    for inc in incs[:3]:
                        lines.append(f"  - {inc.get('incident_id')} [{inc.get('severity')}] {inc.get('title')}")
                elif tname == "list_pending_requests":
                    reqs = data.get("requests") or []
                    compact_multi = len(reqs) > 1

                    def _clip_field(val: str, limit: int = 160) -> str:
                        s_val = " ".join(str(val or "").split())
                        if not compact_multi or len(s_val) <= limit:
                            return s_val
                        return s_val[: limit - 3].rstrip() + "..."

                    lines.append(f"• Đơn xin cấp SSH đang chờ duyệt: {data.get('pending_count', len(reqs))}")
                    for idx, r in enumerate(reqs[:10], 1):
                        host_lbl = r.get("recommended_host") or "workstation"
                        pool_id = r.get("recommended_pool_id")
                        mount_pt = r.get("recommended_mount_point") or ""
                        pool_part = f"{host_lbl}/{pool_id}" if pool_id else host_lbl
                        if mount_pt and pool_id:
                            pool_part = f"{host_lbl}/{pool_id} ({mount_pt})"
                        mem_cnt = r.get("recommended_member_count")
                        free_gib = r.get("recommended_free_gib")
                        pool_load = (
                            f" [đang có {mem_cnt} TV, trống {free_gib} GiB]"
                            if (mem_cnt is not None and free_gib is not None)
                            else ""
                        )
                        spec_part = r.get("recommended_spec") or "4C/16G/50GiB"
                        gpu_lbl = (
                            f"Có ({_clip_field(r.get('gpu_justification'), 80)})"
                            if (r.get("gpu_requested") and r.get("gpu_justification"))
                            else ("Có (SHARED)" if r.get("gpu_requested") else "Không")
                        )
                        dur_days = r.get("requested_duration_days")
                        mcat = r.get("member_category") or "STUDENT"
                        dur_lbl = f"{dur_days} ngày" if dur_days else ("Không giới hạn" if mcat == "LECTURER" else "30 ngày")
                        purp = str(r.get("ssh_purpose") or "N/A").strip()
                        tsk = str(r.get("expected_tasks") or "N/A").strip()
                        purp_line = _clip_field(
                            purp
                            if (purp.lower() == tsk.lower() or tsk == "N/A")
                            else f"{purp} | Tác vụ: {tsk}"
                        )
                        proj_line = _clip_field(r.get("project_title") or "Chưa nêu tên đề tài")
                        reason_line = _clip_field(r.get("registration_reason") or "Chưa ghi rõ")
                        lines.append(
                            f"  {idx}. {r.get('full_name') or r.get('username')} (@{r.get('username')} • Mã: {r.get('member_code') or 'N/A'} • {mcat}) — Mã đơn: {r.get('id')}\n"
                            f"     • GVHD: {r.get('supervisor_name') or 'Chưa khai báo'}\n"
                            f"     • Đề tài: {proj_line}\n"
                            f"     • Lý do xin cấp: {reason_line}\n"
                            f"     • Mục đích & Tác vụ: {purp_line} | GPU: {gpu_lbl} | Thời hạn: {dur_lbl}\n"
                            f"     • Đề xuất: {pool_part}{pool_load} • {spec_part} | SLA còn ~{r.get('remaining_sla_hours', 0)}h"
                        )
                    if reqs:
                        first_u = reqs[0].get("username")
                        lines.append(
                            f"• Hỏi chi tiết từng người: \"cho tôi xem @{first_u}\" | "
                            f"Xử lý nhanh: \"@{first_u} đồng ý duyệt cấp tài nguyên\" hoặc \"@{first_u} tôi từ chối cấp vì lý do ...\""
                        )
                elif tname == "get_vpn_status":
                    lines.append(f"• VPN Gateway: {data.get('gateway_status') or data.get('status') or 'N/A'} | Peers online: {data.get('online_peers', 0)}")

        if timestamps:
            lines.append(f"• Thời gian quan sát: {_fmt_vn_time(timestamps[0])} (ICT / UTC+7)")
        return {
            "reply": "\n".join(lines),
            "tool_traces": traces,
            "evidence_timestamps": list(dict.fromkeys(timestamps)),
        }

    def _parse_nl_watch_or_reminder(self, text: str, principal: ZaloPrincipal) -> Optional[Dict[str, Any]]:
        from app.services.mram_service import _strip_vn_accents

        raw = str(text or "").strip()
        low = raw.lower()
        norm = _strip_vn_accents(raw)
        target = "track" if ("rack" in norm or "track" in norm) else "workstation"

        # 1. Condition-based hardware watch (supports GPU temp, GPU util, CPU, RAM, Storage, Node Online/Offline)
        m_cmp_num = re.search(
            r"(dưới|duoi|nhỏ hơn|nho hon|thấp hơn|thap hon|<=|<|trên|tren|vượt|vuot|lớn hơn|lon hon|cao hơn|cao hon|>=|>)\s*(\d+(?:\.\d+)?)\s*(°c|do\s*c|do|c|%|phan\s*tram)?",
            low,
        )
        m_temp = re.search(r"(dưới|duoi|nhỏ hơn|<|trên|tren|lớn hơn|vượt|vuot|>=|>)\s*(\d+(?:\.\d+)?)\s*(?:°c|độ|c)", low)
        watch_spec: Optional[Any] = None
        unit_lbl = ""

        if m_temp and ("%" not in low) and not any(w in norm for w in ("cpu", "ram", "o dia", "o cung", "disk", "storage")):
            op_word, val_s = m_temp.groups()
            comp = "lt" if op_word in {"dưới", "duoi", "nhỏ hơn", "<"} else "gte"
            m_min = re.search(r"(\d+)\s*(?:phút|phut|min|m)\b", low)
            hold_s = max(60, min((int(m_min.group(1)) * 60) if m_min else 300, 86400))
            watch_spec = self._watch_spec_cls(
                target_id=target,
                metric="gpu_temperature_c",
                comparator=comp,
                threshold=float(val_s),
                sustained_seconds=hold_s,
                once=True,
            )
            unit_lbl = "°C"
        elif m_cmp_num:
            op_word, val_s, val_unit = m_cmp_num.groups()
            norm_op = _strip_vn_accents(op_word)
            comp = "lt" if norm_op in {"duoi", "nho hon", "thap hon", "<"} else ("lte" if norm_op == "<=" else "gt" if norm_op in {"vuot", ">"} else "gte")
            metric = ""
            if any(w in norm for w in ("nhiet", "°c", "temp")) or val_unit in {"°c", "do", "do c", "c"}:
                metric = "gpu_temperature_c"
                unit_lbl = "°C"
            elif "cpu" in norm:
                metric = "cpu_percent"
                unit_lbl = "%"
            elif "ram" in norm:
                metric = "ram_percent"
                unit_lbl = "%"
            elif any(w in norm for w in ("o dia", "o cung", "dung luong", "disk", "storage")):
                metric = "storage_percent"
                unit_lbl = "%"
            elif any(w in norm for w in ("gpu", "vram")):
                metric = "gpu_util_percent" if ("%" in low or "tai" in norm or "util" in norm or val_unit == "%") else "gpu_temperature_c"
                unit_lbl = "%" if metric == "gpu_util_percent" else "°C"
            if metric:
                m_hold = re.search(r"(?:trong|duy\s*tri|giu)\s*(\d+)\s*(phut|min|m|giay|s)\b", norm)
                if m_hold:
                    hv, hu = int(m_hold.group(1)), m_hold.group(2)
                    hold_s = hv * (1 if hu in {"giay", "s"} else 60)
                else:
                    m_min = re.search(r"(\d+)\s*(?:phut|min|m)\b", norm)
                    hold_s = (int(m_min.group(1)) * 60) if m_min else 300
                hold_s = max(60, min(hold_s, 86400))
                watch_spec = self._watch_spec_cls(
                    target_id=target,
                    metric=metric,
                    comparator=comp,
                    threshold=float(val_s),
                    sustained_seconds=hold_s,
                    once=True,
                )
        elif any(w in norm for w in ("theo doi", "canh bao", "bao toi", "bao cho toi")) and any(
            w in norm for w in ("online", "offline", "mat ket noi")
        ):
            is_online_target = "online" in norm and "offline" not in norm
            watch_spec = self._watch_spec_cls(
                target_id=target,
                metric="node_status",
                comparator="eq",
                threshold=1.0 if is_online_target else 0.0,
                sustained_seconds=60,
                once=True,
            )
            unit_lbl = ""

        if watch_spec is not None:
            if self.config.task_manager:
                created = self.tasks.create_watch(
                    owner_id=principal.username or principal.zalo_user_id,
                    owner_role=principal.role,
                    spec=watch_spec,
                )
                return {
                    "reply": (
                        f"✅ Đã tạo Watch `{created['task_id']}`:\n"
                        f"• Mục tiêu: {watch_spec.target_id} | {watch_spec.metric} {watch_spec.comparator} {watch_spec.threshold}{unit_lbl}\n"
                        f"• Duy trì: {watch_spec.sustained_seconds}s | Chế độ: báo 1 lần\n"
                        f"• Hủy bất cứ lúc nào: \"hủy cảnh báo vừa tạo\" hoặc `/cancel {created['task_id']}`"
                    ),
                    "ai_used": False,
                    "task": created,
                }
            return {
                "reply": (
                    f"📋 Đề xuất Watch:\n"
                    f"• Mục tiêu: {watch_spec.target_id} | {watch_spec.metric} {watch_spec.comparator} {watch_spec.threshold}{unit_lbl} "
                    f"(duy trì {watch_spec.sustained_seconds}s, 1 lần)\n"
                    f"👉 Gõ `/watch {watch_spec.target_id} {watch_spec.metric} {watch_spec.comparator} {watch_spec.threshold} {watch_spec.sustained_seconds}` để xác nhận tạo."
                ),
                "ai_used": False,
                "proposal": watch_spec.model_dump(),
            }

        # 2. Time-based Reminder ("nhắc tôi sau 30 phút kiểm tra lại job")
        m_rem = re.search(
            r"(?:sau\s+(\d+)\s*(phút|phut|min|m|giờ|gio|tiếng|tieng|h)|(\d+)\s*(phút|phut|min|m|giờ|gio|tiếng|tieng|h)\s*nữa)",
            low,
        )
        if m_rem and any(k in norm for k in ("nhac", "hen", "lich")):
            num_s = m_rem.group(1) or m_rem.group(3)
            unit_s = (m_rem.group(2) or m_rem.group(4) or "phut").lower()
            qty = max(1, int(num_s))
            is_hours = unit_s.startswith(("g", "t", "h"))
            mins = min(43200, qty * (60 if is_hours else 1))
            # Extract clean reminder title
            title_cand = re.sub(
                r"^(?:hãy\s+|hay\s+|giúp\s+|giup\s+)?(?:nhắc\s+tôi|nhac\s+toi|nhắc\s+mình|nhac\s+minh|hẹn\s+tôi|hen\s+toi|đặt\s+lịch\s+nhắc|dat\s+lich\s+nhac)\s*(?:tôi\s+|mình\s+)?",
                "",
                raw,
                flags=re.IGNORECASE,
            ).strip()
            title_cand = re.sub(
                r"(?:vào\s+)?(?:sau\s+\d+\s*(?:phút|phut|min|m|giờ|gio|tiếng|tieng|h)|\d+\s*(?:phút|phut|min|m|giờ|gio|tiếng|tieng|h)\s*nữa)(?:\s*(?:để|là|:))?\s*",
                "",
                title_cand,
                flags=re.IGNORECASE,
            ).strip(" ,.;:-")
            title = title_cand or f"Nhắc việc LabOps (sau {qty} {'giờ' if is_hours else 'phút'})"
            run_at = datetime.now(timezone.utc) + timedelta(minutes=mins)
            if self.config.task_manager:
                created = self.tasks.create_reminder(
                    owner_id=principal.username or principal.zalo_user_id,
                    owner_role=principal.role,
                    title=title,
                    run_at=run_at,
                )
                return {
                    "reply": (
                        f"⏰ Đã đặt lịch nhắc `{created['task_id']}`:\n"
                        f"• Nội dung: {title}\n"
                        f"• Thời gian nhắc: {_fmt_vn_time(run_at.isoformat())} (ICT / UTC+7, sau {mins} phút)\n"
                        f"• Hủy bất cứ lúc nào: \"hủy lịch nhắc vừa tạo\" hoặc `/cancel {created['task_id']}`"
                    ),
                    "ai_used": False,
                    "task": created,
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
        self.approvals.zalo_r3_actions_enabled = bool(self.config.zalo_r3_actions_enabled)
        self.approvals.zalo_r4_actions_enabled = bool(self.config.zalo_r4_actions_enabled)
        self.approvals.secret_delivery_mode = str(self.config.zalo_secret_delivery_mode or "vault_only")
        self.approvals.short_otp_confirm = bool(self.config.zalo_short_otp_confirm)
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
            "zalo_r3_actions_enabled": self.config.zalo_r3_actions_enabled,
            "zalo_r4_actions_enabled": self.config.zalo_r4_actions_enabled,
            "zalo_secret_delivery_mode": self.config.zalo_secret_delivery_mode,
            "zalo_short_otp_confirm": self.config.zalo_short_otp_confirm,
            "parity_actions_count": len(ADMIN_ACTION_CATALOG),
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
            "parity_actions_count": len(ADMIN_ACTION_CATALOG),
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

    def try_handle_admin_action_intent(
        self,
        *,
        text: str,
        principal: ZaloPrincipal,
        chat_id: str,
        session_id: str = "nl-admin",
    ) -> Optional[Dict[str, Any]]:
        """
        Deterministic Vietnamese intent router for Web Admin parity actions:
        - Strictly blocks non-ADMIN callers on admin mutations.
        - Enforces Disambiguation Gate (`CLARIFYING`) when ambiguous across similar actions or targets.
        - Emits 4-part Intent-Confirmation Cards (`PENDING_CONFIRMATION`) for 100% of R1..R4 actions.
        - Routes OTP cards to DM 1-1 (`dm_notifications`) when invoked from a group chat.
        """
        from app.services.mram_service import _strip_vn_accents

        raw = str(text or "").strip()
        if not raw or raw.startswith("/"):
            return None
        norm = _strip_vn_accents(raw)

        # Skip pure informational questions
        if re.match(r"^(?:xem|cho\s+toi\s+xem|kiem\s+tra\s+trang\s+thai|tai\s+sao|giai\s+thich)\b", norm) and not any(
            w in norm for w in ("xac minh workspace", "kiem dinh workspace", "verify workspace")
        ):
            return None

        # Extract explicit @mention or username token after "cho / tai khoan / thanh vien / user"
        at_m = re.findall(r"@([a-z0-9_.-]+)", raw.lower())
        target_q = next((u for u in at_m if u != "admin"), "")
        if not target_q:
            m_user = re.search(
                r"\b(?:cho|tai\s+khoan|thanh\s+vien|user|cua)\s+(?!moi\b|he\s+thong\b|admin\b)([a-z0-9_.-]{2,40})\b",
                norm,
            )
            if m_user:
                cand_u = m_user.group(1)
                if cand_u not in {"web", "ssh", "vpn", "gpu", "job", "don", "su", "co", "nay", "do", "toi", "minh"}:
                    target_q = cand_u

        selected_action: Optional[str] = None
        extra_args: Dict[str, Any] = {}
        explicit_action = False

        # 1. Password Reset / Reissue (Web vs SSH Temp vs Disambiguate)
        if any(w in norm for w in ("mat khau", "password", "pass")) and any(
            w in norm for w in ("reset", "cap lai", "dat lai", "khoi phuc", "doi")
        ):
            has_web = any(w in norm for w in ("web", "portal", "dashboard"))
            has_ssh = any(w in norm for w in ("ssh", "tam", "lan dau", "container", "temp"))
            if has_ssh and not has_web:
                selected_action = "reissue_ssh_temp_password"
                explicit_action = True
            elif has_web and not has_ssh:
                selected_action = "reissue_web_password"
                explicit_action = True
            else:
                explicit_action = False
                selected_action = "reissue_ssh_temp_password"

        # 2. Member Lifecycle (Suspend / Restore / Archive / Delete R4 / Quota)
        elif any(w in norm for w in ("tam ngung", "suspend")) and ("ssh" not in norm or "tai khoan" in norm):
            selected_action = "suspend_member"
            explicit_action = True
        elif any(w in norm for w in ("khoi phuc", "mo khoa", "restore")) and any(
            w in norm for w in ("tai khoan", "thanh vien", "member", "@")
        ):
            selected_action = "restore_member"
            explicit_action = True
        elif any(w in norm for w in ("luu tru", "archive")) and any(
            w in norm for w in ("tai khoan", "thanh vien", "member", "workspace", "@")
        ):
            selected_action = "archive_member"
            explicit_action = True
        elif any(w in norm for w in ("xoa vinh vien", "delete permanent")) and any(
            w in norm for w in ("tai khoan", "thanh vien", "member", "@")
        ):
            selected_action = "delete_member_permanent"
            explicit_action = True
        elif any(w in norm for w in ("khoa", "chan", "tat tai khoan", "vo hieu")) and any(
            w in norm for w in ("tai khoan", "thanh vien", "member", "@")
        ) and "vpn" not in norm and "key" not in norm:
            selected_action = "suspend_member"
            explicit_action = False
        elif any(w in norm for w in ("cap nhat quota", "doi quota", "nang quota", "giam quota")):
            qm = re.search(r"(\d{1,5})\s*(?:gb|gib)\b", norm)
            if qm:
                selected_action = "update_storage_quota"
                extra_args["quota_gb"] = int(qm.group(1))
                explicit_action = True

        # 3. SSH Keys, Sessions & Workspace Verification / Secrets
        elif any(w in norm for w in ("xoay ssh", "xoay khoa ssh", "rotate ssh", "rotate key")):
            selected_action = "rotate_ssh_key"
            explicit_action = True
        elif any(w in norm for w in ("ngat phien ssh", "dong phien ssh", "terminate ssh")):
            selected_action = "terminate_ssh_sessions"
            explicit_action = True
        elif any(w in norm for w in ("thu hoi quyen ssh", "thu hoi toan bo ssh", "revoke ssh access")):
            selected_action = "revoke_ssh_access"
            explicit_action = True
        elif any(w in norm for w in ("kiem dinh workspace", "xac minh workspace", "verify workspace", "verification gate")):
            selected_action = "verify_member_workspace"
            explicit_action = True
        elif any(w in norm for w in ("lay vault", "claim secret", "nhan bi mat")):
            selected_action = "claim_member_secrets_once"
            explicit_action = True
        elif "ssh" in norm and any(w in norm for w in ("reset", "ngat", "thu hoi", "xoay")):
            selected_action = "terminate_ssh_sessions"
            explicit_action = False

        # 4. VPN Device Ops
        elif "vpn" in norm and any(w in norm for w in ("cap them", "them thiet bi", "tao thiet bi", "add vpn")):
            selected_action = "add_vpn_device"
            explicit_action = True
        elif "vpn" in norm and any(w in norm for w in ("tam tat", "bat lai", "toggle")):
            selected_action = "toggle_vpn_device"
            extra_args["enable"] = "bat" in norm or "enable" in norm
            explicit_action = True
        elif "vpn" in norm and any(w in norm for w in ("thu hoi vinh vien", "xoa vinh vien", "revoke vpn")):
            selected_action = "revoke_vpn_device"
            explicit_action = True
        elif "vpn" in norm and any(w in norm for w in ("tat", "khoa", "xoa", "thu hoi")):
            selected_action = "toggle_vpn_device"
            explicit_action = False

        # 5. Compute / Processes / R1 Queue / Repo / V4
        elif any(w in norm for w in ("quet sla", "sweep sla")):
            selected_action = "sweep_mram_sla"
            target_q = "mram-queue"
            explicit_action = True
        elif any(w in norm for w in ("danh gia su co", "evaluate incident")):
            selected_action = "evaluate_incidents"
            target_q = "system"
            explicit_action = True
        elif any(w in norm for w in ("nap lai cau hinh v4", "reload v4", "reload config")):
            selected_action = "reload_v4_config"
            target_q = "v4-runtime"
            explicit_action = True
        elif any(w in norm for w in ("chay retention", "don dep v4", "run retention")):
            selected_action = "run_v4_retention"
            target_q = "v4-retention"
            extra_args["dry_run"] = "xoa that" not in norm and "thuc te" not in norm
            explicit_action = True
        elif any(w in norm for w in ("quet lai repo", "rescan repo")):
            rm = re.search(r"\b(req-[a-z0-9_-]+|[a-z0-9_-]{6,32})\b", raw.lower())
            selected_action = "rescan_repository_request"
            target_q = rm.group(1) if rm else target_q
            explicit_action = True
        elif any(w in norm for w in ("dung job", "kill process", "sigkill", "sigterm", "dung tien trinh")):
            if "sigkill" in norm or "cuong buc" in norm:
                selected_action = "kill_process"
                explicit_action = True
            elif "sigterm" in norm or "dung mem" in norm:
                selected_action = "stop_process"
                explicit_action = True
            else:
                selected_action = "stop_process"
                explicit_action = False

        if selected_action is None:
            return None

        if not principal.is_admin:
            return {
                "forbidden": True,
                "ai_used": False,
                "reply": "⛔ Chỉ Quản trị viên (ADMIN) mới có quyền đề xuất và xác nhận thao tác quản trị hệ thống.",
                "intent": "ADMIN_PARITY_FORBIDDEN",
            }

        # Run Disambiguation Gate (intent ambiguity AND multi-match target ambiguity)
        disamb = check_disambiguation(
            action_type=selected_action,
            raw_text=raw if not explicit_action else "",
            target_query=target_q or None,
            explicit_action=explicit_action,
            service_context=self.service_context,
        )
        if disamb is not None:
            return {
                "status": "CLARIFYING",
                "ai_used": False,
                "intent": "ADMIN_DISAMBIGUATION",
                "reply": disamb["message"],
                "clarification": disamb,
                "default_action_type": selected_action,
                "extra_args": extra_args,
            }

        spec = get_action_spec(selected_action)
        if spec is None:
            return None

        # Ensure member-scoped actions have an identifiable target before proposing
        if spec.target_type == "MEMBER" and selected_action != "create_web_account" and not target_q:
            lsvc = self.service_context.get("labops_service")
            candidates = list(getattr(lsvc, "members", None) or []) if lsvc is not None else []
            active_cands = [m for m in candidates if str(m.get("status") or "").upper() != "DELETED"][:8]
            if not active_cands:
                return {"ai_used": False, "reply": "⚠️ Vui lòng nêu rõ `@username` thành viên cần thao tác."}
            lines = [f"🔎 Bạn muốn áp dụng `{spec.title_vi}` cho thành viên nào? Chọn số thứ tự:"]
            opts = []
            for idx, m in enumerate(active_cands, 1):
                uname = str(m.get("username") or m.get("id"))
                fname = str(m.get("fullName") or m.get("full_name") or uname)
                opts.append({"index": idx, "kind": "TARGET", "target_id": uname, "username": uname, "action_type": selected_action, "label": f"{fname} (@{uname})"})
                lines.append(f"{idx}. {fname} (@{uname}) [{m.get('status')}]")
            lines.append("\n👉 Nhắn số thứ tự (`1`, `2`, ...) để chọn, hoặc nhắn `HỦY`.")
            clar = {
                "status": "CLARIFYING",
                "clarification_kind": "TARGET_AMBIGUITY",
                "options": opts,
                "message": "\n".join(lines),
            }
            return {
                "status": "CLARIFYING",
                "ai_used": False,
                "intent": "ADMIN_DISAMBIGUATION",
                "reply": clar["message"],
                "clarification": clar,
                "default_action_type": selected_action,
                "extra_args": extra_args,
            }

        resolved_target = target_q
        if spec.target_type == "MEMBER":
            lsvc = self.service_context.get("labops_service")
            cands = find_matching_members(lsvc, target_q) if (lsvc is not None and target_q) else []
            if len(cands) == 1:
                resolved_target = str(cands[0].get("username") or cands[0].get("id") or target_q)
        elif not resolved_target:
            resolved_target = "default"

        return self.create_admin_confirmation_proposal(
            action_type=selected_action,
            target_id=resolved_target,
            principal=principal,
            chat_id=chat_id,
            session_id=session_id,
            extra_args=extra_args,
            reason=raw[:200],
        )

    def create_admin_confirmation_proposal(
        self,
        *,
        action_type: str,
        target_id: str,
        principal: ZaloPrincipal,
        chat_id: str,
        session_id: str = "nl-admin",
        extra_args: Optional[Dict[str, Any]] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Create a PENDING_CONFIRMATION proposal card with strict DM 1-1 OTP isolation."""
        spec = get_action_spec(action_type)
        if spec is None:
            raise LookupError(f"Unknown admin action '{action_type}'")
        payload = {"target_id": target_id, **(extra_args or {})}
        if reason and "reason" in spec.args_model.model_fields and not payload.get("reason"):
            payload["reason"] = reason

        origin_chat = str(chat_id or principal.zalo_user_id or "dashboard")
        confirm_chat = str(principal.zalo_user_id or origin_chat)
        prop = self.approvals.propose_action(
            actor_id=principal.username,
            actor_role=principal.role,
            chat_id=origin_chat,
            confirm_chat_id=confirm_chat,
            origin_chat_id=origin_chat,
            session_id=session_id,
            action_type=spec.action_id,
            target_type=spec.target_type,
            target_id=target_id,
            canonical_args=payload,
            reason=reason,
        )
        dm_notifications: List[Dict[str, str]] = []
        if prop.get("routed_to_dm") and confirm_chat and prop.get("dm_confirmation_card"):
            dm_notifications.append({"chat_id": confirm_chat, "text": str(prop["dm_confirmation_card"])})
            reply_txt = str(prop.get("group_confirmation_notice") or prop.get("instruction") or "")
        else:
            reply_txt = str(prop.get("confirmation_card") or prop.get("instruction") or "")
        memory_safe_reply = str(prop.get("group_confirmation_notice") or f"📝 Đã tạo Phiếu Xác Nhận Hiểu Đúng `{prop['action_id']}` cho `{action_type}` trên `{target_id}`.")
        return {
            "status": "PENDING_CONFIRMATION",
            "ai_used": False,
            "intent": "ADMIN_CONFIRMATION_CARD",
            "reply": reply_txt,
            "memory_safe_reply": memory_safe_reply,
            "proposal": prop,
            "dm_notifications": dm_notifications,
        }
