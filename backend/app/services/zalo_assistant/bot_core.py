"""
Zalo Bot Core Engine (LabOps AI SRE Agent v2)
Handles operational commands and schema-validated AI tool investigations:
/start, /help, /ask, /status, /gpu, /jobs, /myjobs, /myworkspace, /alloc,
/incidents, /incident <id>, /ack <id>, /report [today|week], /security,
/quota, /link <CODE>.
Enforces LabOps identity-bound RBAC, rate limiting, and quota accounting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
from app.services.zalo_assistant.identity_rbac import ZaloIdentityRBACService, ZaloPrincipal
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
from app.services.zalo_assistant.quota_ledger import QuotaExceededError, QuotaLedger
from app.services.zalo_assistant.report_generator import ReportGenerator
from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector
from app.services.zalo_assistant.tool_registry import SREAgentRunner, SREToolRegistry

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_vn_time(iso_str: Any) -> str:
    if not isinstance(iso_str, str) or not iso_str.strip():
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%H:%M %d/%m/%Y")
    except Exception:
        try:
            dt = datetime.fromisoformat(str(iso_str).strip().replace("Z", "+00:00"))
            return (dt + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
        except Exception:
            return str(iso_str)[:16]


class ZaloBotEngine:
    COMMANDS = {
        "/start": "Khởi động và giới thiệu trợ lý AI SRE v2",
        "/help": "Xem danh sách lệnh hỗ trợ theo phân quyền",
        "/status": "Trạng thái máy chủ, GPU (UUID), lưu trữ và freshness thời gian thực",
        "/gpu": "Truy vấn nhanh GPU theo từng máy chủ",
        "/jobs": "Tác vụ tính toán đang chạy (Admin: toàn cụm; Researcher: của bạn)",
        "/myjobs": "Danh sách tác vụ tính toán và lịch sử job của bạn",
        "/myworkspace": "Trạng thái Docker workspace, quota đĩa và RAM của bạn",
        "/alloc": "Phân bổ GPU theo thành viên",
        "/incidents": "Danh sách sự cố hạ tầng/bảo mật đang mở",
        "/incident": "Chi tiết sự cố (/incident <incident_id>)",
        "/ack": "Xác nhận đang xử lý sự cố (/ack <incident_id>)",
        "/report": "Báo cáo tổng hợp (/report today hoặc /report week)",
        "/security": "Tổng hợp an ninh kho mã & cảnh báo (Chỉ Admin)",
        "/requests": "Danh sách đơn xin cấp SSH đang chờ phê duyệt (Chỉ Admin)",
        "/quota": "Kiểm tra hạn mức tin nhắn nội bộ Zalo & AI requests",
        "/link": "Liên kết tài khoản Zalo với LabOps (/link <MÃ>)",
        "/ask": "Điều tra hạ tầng bằng AI Agent (/ask <câu hỏi>)",
        "/new": "Bắt đầu phiên hội thoại mới (V4)",
        "/clear": "Xóa ngữ cảnh hội thoại hiện tại (không xóa audit log)",
        "/memory": "Xem trạng thái phiên nhớ và tùy chọn cá nhân",
        "/tasks": "Danh sách tác vụ & watch rules đang hoạt động",
        "/watch": "Theo dõi ngưỡng (/watch <target> <metric> <lt|lte|gt|gte> <val> [sec])",
        "/cancel": "Hủy tác vụ hoặc watch (/cancel <task_id>)",
        "/settings": "Xem hoặc đặt tùy chọn (/settings [key=value])",
        "/summary": "Tóm tắt tình hình (/summary [today|week])",
        "/compare": "So sánh tải hiện tại với lịch sử (/compare [host_id])",
        "/who": "Tra cứu thông tin thành viên (Chỉ Admin)",
        "/confirm": "Xác nhận đề xuất hành động (/confirm <action_id> <code>)",
    }

    ENABLED_READ_TOOLS = list(SREToolRegistry.TOOL_SPECS.keys())
    DISABLED_WRITE_TOOLS = [
        "execute_arbitrary_shell",
        "sudo_system_commands",
        "remote_ssh_execution",
        "restart_server_or_daemon",
        "delete_user_workspace",
        "docker_privileged_operations",
        "network_routing_modification",
    ]

    def __init__(
        self,
        quota_ledger: QuotaLedger,
        ai_client: AIGatewayClient,
        report_generator: ReportGenerator,
        bot_token: Optional[str] = None,
        authorized_users: Optional[List[str]] = None,
        allowed_chat_ids: Optional[List[str]] = None,
        config_path: Optional[str] = None,
        identity_rbac: Optional[ZaloIdentityRBACService] = None,
        telemetry_collector: Optional[UnifiedTelemetryCollector] = None,
        incident_engine: Optional[UnifiedIncidentEngine] = None,
        tool_registry: Optional[SREToolRegistry] = None,
    ):
        self.quota_ledger = quota_ledger
        self.ai_client = ai_client
        self.report_generator = report_generator
        self.bot_token = bot_token if bot_token is not None else os.getenv("ZALO_BOT_TOKEN", "")
        self.data_dir = getattr(quota_ledger, "data_dir", None) or getattr(report_generator, "data_dir", "/tmp")

        db_mgr = getattr(report_generator, "db", None)
        self.identity_rbac = identity_rbac or ZaloIdentityRBACService(self.data_dir, db_manager=db_mgr)
        self.telemetry_collector = telemetry_collector or UnifiedTelemetryCollector(self.data_dir, db_manager=db_mgr)
        self.incident_engine = incident_engine or UnifiedIncidentEngine(self.data_dir, db_manager=db_mgr)
        self.tool_registry = tool_registry or SREToolRegistry(
            self.data_dir,
            db_manager=db_mgr,
            telemetry_collector=self.telemetry_collector,
            incident_engine=self.incident_engine,
            report_generator=self.report_generator,
        )
        self.agent_runner = SREAgentRunner(self.tool_registry)

        self._explicit_allowlist = (authorized_users is not None or allowed_chat_ids is not None)
        if config_path is not None:
            self._config_path: Optional[str] = config_path
        elif not self._explicit_allowlist and getattr(quota_ledger, "data_dir", None):
            self._config_path = os.path.join(quota_ledger.data_dir, "zalo_config.json")
        else:
            self._config_path = None
        self._config_mtime: Optional[float] = None

        if authorized_users is not None:
            cleaned_users = [str(u).strip() for u in authorized_users if str(u).strip()]
            if "*" in cleaned_users:
                raise ValueError("Wildcard '*' bị cấm trong danh sách authorized_users.")
            self.authorized_users = cleaned_users
        else:
            self.authorized_users = [
                u.strip()
                for u in os.getenv("ZALO_AUTHORIZED_USERS", "").split(",")
                if u.strip() and u.strip() != "*"
            ]

        if allowed_chat_ids is not None:
            cleaned_chats = [str(c).strip() for c in allowed_chat_ids if str(c).strip()]
            if "*" in cleaned_chats:
                raise ValueError("Wildcard '*' bị cấm trong danh sách allowed_chat_ids.")
            self.allowed_chat_ids = cleaned_chats
        else:
            self.allowed_chat_ids = [
                c.strip()
                for c in os.getenv("ZALO_ALLOWED_CHAT_IDS", "").split(",")
                if c.strip() and c.strip() != "*"
            ]

        self.is_polling = False
        self._polling_task: Optional[asyncio.Task] = None
        self.consumer_id = f"consumer-ws-{os.getpid()}"
        self.last_polling_heartbeat = None
        self.message_history: List[Dict[str, Any]] = []
        self._ask_lock = threading.Lock()
        self._ask_timestamps: Dict[str, List[float]] = defaultdict(list)
        self._decision_lock = threading.Lock()
        self._admin_decision_drafts: Dict[str, Dict[str, Any]] = {}
        self._decision_draft_ttl_sec = 300.0
        self.v4_facade: Optional[Any] = None
        if self._config_path and not self._explicit_allowlist:
            self.reload_persisted_config(force=True)

    def reload_persisted_config(self, force: bool = False) -> bool:
        if not self._config_path or not os.path.exists(self._config_path):
            return False
        try:
            mtime = os.path.getmtime(self._config_path)
        except OSError:
            return False
        if not force and self._config_mtime is not None and mtime == self._config_mtime:
            return False
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False
            if isinstance(data.get("authorized_users"), list):
                self.authorized_users = [
                    str(u).strip()
                    for u in data["authorized_users"]
                    if str(u).strip() and str(u).strip() != "*"
                ]
            if isinstance(data.get("allowed_chat_ids"), list):
                self.allowed_chat_ids = [
                    str(c).strip()
                    for c in data["allowed_chat_ids"]
                    if str(c).strip() and str(c).strip() != "*"
                ]
            if data.get("ai_model"):
                self.ai_client.model = str(data["ai_model"]).strip()
            if data.get("timeout_seconds") is not None:
                try:
                    t_val = int(data["timeout_seconds"])
                    if 1 <= t_val <= 120:
                        self.ai_client.timeout = t_val
                except (ValueError, TypeError):
                    pass
            self._config_mtime = mtime
            return True
        except Exception as exc:
            logger.warning("Không thể đọc cấu hình %s: %s", self._config_path, exc)
            return False

    def save_persisted_config(self) -> None:
        if not self._config_path:
            return
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        payload = {
            "authorized_users": [u for u in (self.authorized_users or []) if u and u != "*"],
            "allowed_chat_ids": [c for c in (self.allowed_chat_ids or []) if c and c != "*"],
            "ai_model": self.ai_client.model,
            "timeout_seconds": self.ai_client.timeout,
            "updated_at": _now(),
        }
        tmp_path = f"{self._config_path}.tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._config_path)
        try:
            self._config_mtime = os.path.getmtime(self._config_path)
        except OSError:
            pass

    def resolve_principal(self, sender_id: str) -> ZaloPrincipal:
        self.reload_persisted_config()
        return self.identity_rbac.resolve_zalo_principal(
            sender_id,
            fallback_authorized_users=self.authorized_users,
        )

    def is_authorized(self, user_id: str) -> bool:
        return self.resolve_principal(user_id).is_authorized

    def is_chat_allowed(self, chat_id: Optional[str], sender_id: Optional[str] = None) -> bool:
        self.reload_persisted_config()
        cid = str(chat_id or "").strip()
        if not cid or cid == "*":
            return False
        valid_chats = {
            str(c).strip()
            for c in (self.allowed_chat_ids or [])
            if str(c).strip() and str(c).strip() != "*"
        }
        if valid_chats:
            return cid in valid_chats
        sid = str(sender_id or "").strip()
        return bool(sid) and cid == sid and self.is_authorized(sid)

    def _check_ask_rate_limit(self, sender_id: str, limit: int = 10, window_sec: float = 60.0) -> bool:
        key = str(sender_id or "anonymous").strip()
        now = time.monotonic()
        with self._ask_lock:
            recent = [t for t in self._ask_timestamps[key] if now - t < window_sec]
            if len(recent) >= limit:
                self._ask_timestamps[key] = recent
                return False
            recent.append(now)
            self._ask_timestamps[key] = recent
            return True

    async def ahandle_command(
        self,
        command_text: str,
        sender_id: str = "web-admin",
        *,
        idempotency_key: Optional[str] = None,
        chat_id: Optional[str] = None,
        source: str = "zalo",
        principal_role: Optional[str] = None,
        defer_outbox_commit: bool = False,
        sender_name: str = "",
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self.handle_command,
            command_text,
            sender_id,
            idempotency_key=idempotency_key,
            chat_id=chat_id,
            source=source,
            principal_role=principal_role,
            defer_outbox_commit=defer_outbox_commit,
            sender_name=sender_name,
        )

    def handle_command(
        self,
        command_text: str,
        sender_id: str = "web-admin",
        *,
        idempotency_key: Optional[str] = None,
        chat_id: Optional[str] = None,
        source: str = "zalo",
        principal_role: Optional[str] = None,
        defer_outbox_commit: bool = False,
        sender_name: str = "",
    ) -> Dict[str, Any]:
        text = command_text.strip()
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        src = (source or "zalo").strip().lower()
        role_upper = str(principal_role or "").strip().upper()

        # Allow /link <CODE> even before sender_id is in allowlist
        if cmd == "/link" and src != "dashboard":
            claim_res = self.identity_rbac.claim_link_code(
                zalo_user_id=sender_id,
                code=arg,
                display_name=sender_name,
            )
            if claim_res.get("success"):
                reply = (
                    "✅ Đã liên kết danh tính Zalo với LabOps!\n"
                    f"• Tài khoản: @{claim_res['username']} ({claim_res['role']})\n"
                    f"• Zalo ID: {sender_id}\n"
                    "Bạn có thể sử dụng /help để xem các lệnh được phân quyền."
                )
                return {
                    "success": True,
                    "message_id": f"link-{os.urandom(4).hex()}",
                    "command": "/link",
                    "reply": reply,
                    "identity": claim_res,
                }
            return {
                "success": False,
                "error": claim_res.get("error") or "INVALID_LINK_CODE",
                "command": "/link",
                "reply": f"⛔ {claim_res.get('message') or 'Mã liên kết không hợp lệ.'}",
            }

        if src == "dashboard":
            if role_upper not in ("ADMIN", "RESEARCHER", "STUDENT"):
                return {
                    "success": False,
                    "error": "UNAUTHORIZED",
                    "suppress_outbound": True,
                    "reply": "⛔ Bạn không có quyền truy cập LabOps AI Assistant.",
                    "command": cmd,
                }
            principal = ZaloPrincipal(
                zalo_user_id=sender_id,
                username=sender_id,
                member_id="",
                role=role_upper,
                display_name=sender_id,
                is_authorized=True,
                is_linked=True,
            )
        else:
            principal = self.resolve_principal(sender_id)
            if not principal.is_authorized or (
                chat_id is not None and not self.is_chat_allowed(chat_id, sender_id=sender_id)
            ):
                return {
                    "success": False,
                    "error": "UNAUTHORIZED",
                    "suppress_outbound": True,
                    "reply": "⛔ Bạn không có quyền truy cập LabOps AI Assistant. Nếu đã có mã liên kết trên Dashboard, hãy gõ `/link <MÃ>`.",
                    "command": cmd,
                }

        known_cmds = {
            "/start",
            "/help",
            "/status",
            "/gpu",
            "/alloc",
            "/allocations",
            "/gpus",
            "/jobs",
            "/myjobs",
            "/myworkspace",
            "/incidents",
            "/incident",
            "/ack",
            "/report",
            "/security",
            "/requests",
            "/ssh-requests",
            "/quota",
            "/new",
            "/clear",
            "/memory",
            "/tasks",
            "/watch",
            "/cancel",
            "/settings",
            "/summary",
            "/compare",
            "/who",
            "/confirm",
        }
        has_active_draft = (
            cmd not in known_cmds
            and principal.is_admin
            and self._get_decision_draft(principal, chat_id or sender_id) is not None
        )
        is_admin_ssh_decision_intent = (
            cmd not in known_cmds
            and principal.is_admin
            and self._parse_natural_ssh_decision_intent(text) is not None
        )
        is_ask = cmd == "/ask" or cmd not in known_cmds
        if (
            is_ask
            and not has_active_draft
            and not is_admin_ssh_decision_intent
            and not self._check_ask_rate_limit(sender_id, limit=50 if principal.is_admin else 10)
        ):
            return {
                "success": False,
                "error": "RATE_LIMITED",
                "suppress_outbound": True,
                "reply": "⏳ Bạn đã gửi quá nhiều yêu cầu AI /ask. Vui lòng thử lại sau 1 phút.",
                "command": cmd or "/ask",
            }

        agent_meta: Dict[str, Any] = {}

        if cmd == "/start":
            reply = self._cmd_start(principal)
        elif cmd == "/help":
            reply = self._cmd_help(principal)
        elif cmd in ("/status", "/gpu"):
            reply = self._cmd_status()
        elif cmd in ("/alloc", "/allocations", "/gpus"):
            reply = self._cmd_allocations(principal)
        elif cmd == "/jobs":
            reply = self._cmd_jobs(principal, own_only=not principal.is_admin)
        elif cmd == "/myjobs":
            reply = self._cmd_jobs(principal, own_only=True)
        elif cmd == "/myworkspace":
            reply = self._cmd_myworkspace(principal)
        elif cmd == "/incidents":
            reply = self._cmd_incidents(principal)
        elif cmd == "/incident":
            reply = self._cmd_incident_detail(arg, principal)
        elif cmd == "/ack":
            reply = self._cmd_ack(arg, principal)
        elif cmd == "/security":
            if not principal.is_admin:
                return {
                    "success": False,
                    "error": "FORBIDDEN",
                    "suppress_outbound": src != "zalo",
                    "reply": "⛔ Lệnh /security chỉ dành cho Quản trị viên (Admin).",
                    "command": "/security",
                }
            reply = self._cmd_security()
        elif cmd in ("/requests", "/ssh-requests"):
            if not principal.is_admin:
                return {
                    "success": False,
                    "error": "FORBIDDEN",
                    "suppress_outbound": src != "zalo",
                    "reply": "⛔ Lệnh /requests chỉ dành cho Quản trị viên (Admin).",
                    "command": "/requests",
                }
            reply = self._cmd_requests(principal)
        elif cmd == "/report":
            wants_generate = any(w in arg.lower() for w in ["tạo", "new", "gen", "generate"])
            if wants_generate and not principal.is_admin:
                return {
                    "success": False,
                    "error": "FORBIDDEN",
                    "suppress_outbound": src != "zalo",
                    "reply": "⛔ Chỉ quản trị viên mới được phép khởi tạo báo cáo mới.",
                    "command": cmd,
                }
            reply = self._cmd_report(arg, principal)
        elif cmd == "/quota":
            reply = self._cmd_quota()
        elif cmd == "/new":
            self._clear_decision_draft(principal, chat_id or sender_id)
            reply = self._cmd_v4_new(principal, source=src, chat_id=chat_id or sender_id)
        elif cmd == "/clear":
            self._clear_decision_draft(principal, chat_id or sender_id)
            reply = self._cmd_v4_clear(principal, source=src, chat_id=chat_id or sender_id)
        elif cmd == "/memory":
            reply = self._cmd_v4_memory(principal, source=src, chat_id=chat_id or sender_id)
        elif cmd == "/tasks":
            reply = self._cmd_v4_tasks(principal)
        elif cmd == "/watch":
            reply = self._cmd_v4_watch(arg, principal)
        elif cmd == "/cancel":
            reply = self._cmd_v4_cancel(arg, principal)
        elif cmd == "/settings":
            reply = self._cmd_v4_settings(arg, principal)
        elif cmd == "/summary":
            reply = self._cmd_report(arg or "today", principal)
        elif cmd == "/compare":
            reply = self._cmd_v4_compare(arg, principal, source=src)
        elif cmd == "/who":
            if not principal.is_admin:
                return {
                    "success": False,
                    "error": "FORBIDDEN",
                    "suppress_outbound": src != "zalo",
                    "reply": "⛔ Lệnh /who chỉ dành cho Quản trị viên (Admin).",
                    "command": "/who",
                }
            reply = self._cmd_v4_who(arg, principal, source=src)
        elif cmd == "/confirm":
            reply = self._cmd_v4_confirm(arg, principal, chat_id=chat_id or sender_id)
        elif cmd == "/ask":
            reply, agent_meta = self._cmd_ask(arg, principal, source=src, chat_id=chat_id or "")
        else:
            conv = self._try_handle_conversational_ssh_decision(
                text,
                principal,
                chat_id=chat_id or sender_id,
                source=src,
            )
            if conv is not None:
                if conv.get("forbidden"):
                    return {
                        "success": False,
                        "error": "FORBIDDEN",
                        "suppress_outbound": src != "zalo",
                        "reply": conv["reply"],
                        "command": "/ask",
                    }
                reply = str(conv["reply"])
                agent_meta = conv.get("meta") or {}
                self._record_v4_turn_memory(
                    principal=principal,
                    source=src,
                    chat_id=chat_id or sender_id,
                    user_text=text,
                    reply_text=reply,
                )
            else:
                op_act = self._try_handle_natural_operational_action(
                    text,
                    principal,
                    chat_id=chat_id or sender_id,
                    source=src,
                )
                if op_act is not None:
                    if op_act.get("forbidden"):
                        return {
                            "success": False,
                            "error": "FORBIDDEN",
                            "suppress_outbound": src != "zalo",
                            "reply": op_act["reply"],
                            "command": "/ask",
                        }
                    reply = str(op_act["reply"])
                    agent_meta = op_act.get("meta") or {}
                    self._record_v4_turn_memory(
                        principal=principal,
                        source=src,
                        chat_id=chat_id or sender_id,
                        user_text=text,
                        reply_text=reply,
                    )
                else:
                    reply, agent_meta = self._cmd_ask(text, principal, source=src, chat_id=chat_id or "")

        if src == "dashboard":
            msg_id = f"dash-{os.urandom(6).hex()}"
        else:
            try:
                if defer_outbox_commit:
                    _, msg_id, _ = self.quota_ledger.reserve_outbox(
                        recipient_id=sender_id,
                        message_text=reply,
                        is_critical_alert=False,
                        idempotency_key=idempotency_key,
                        metadata={"command": cmd or "/ask", "sender": sender_id, "chat_id": chat_id or ""},
                    )
                else:
                    _, msg_id, _ = self.quota_ledger.reserve_and_send(
                        recipient_id=sender_id,
                        message_text=reply,
                        is_critical_alert=False,
                        idempotency_key=idempotency_key,
                        metadata={"command": cmd or "/ask", "sender": sender_id},
                    )
            except QuotaExceededError as q_err:
                return {
                    "success": False,
                    "reply": f"⚠️ {q_err.message}",
                    "command": cmd,
                    "error": "QUOTA_EXCEEDED",
                }
            except ValueError as v_err:
                return {
                    "success": False,
                    "reply": f"⚠️ {v_err}",
                    "command": cmd,
                    "error": "IDEMPOTENCY_CONFLICT",
                    "suppress_outbound": True,
                }

        history_item = {
            "id": msg_id,
            "sender_id": sender_id,
            "username": principal.username,
            "role": principal.role,
            "source": src,
            "incoming_text": command_text,
            "reply_snippet": reply[:100],
            "command": cmd or "/ask",
            "timestamp": _now(),
        }
        self.message_history.append(history_item)
        if len(self.message_history) > 100:
            self.message_history = self.message_history[-100:]

        res = {
            "success": True,
            "message_id": msg_id,
            "command": cmd or "/ask",
            "reply": reply,
            "quota_status": self.quota_ledger.get_status(),
            "principal": {"username": principal.username, "role": principal.role, "is_linked": principal.is_linked},
        }
        if agent_meta:
            res.update(agent_meta)
        return res

    # ---------------- Command Handlers ----------------

    def _cmd_start(self, principal: ZaloPrincipal) -> str:
        is_v4 = self.v4_facade is not None and getattr(self.v4_facade.config, "enabled", False)
        ver_label = "LabOps Personal AI Agent V4 (SRE v2 + V4)" if is_v4 else "LabOps AI SRE Agent v2"
        extra = (
            " Hỗ trợ thêm hội thoại ngữ cảnh `/new`, `/memory`, theo dõi ngưỡng `/watch`, tác vụ `/tasks` và so sánh `/compare`."
            if is_v4
            else ""
        )
        return (
            f"🚀 Chào mừng @{principal.username} ({principal.role}) đến với {ver_label}!\n\n"
            f"Tôi hỗ trợ giám sát máy chủ, GPU, sự cố, báo cáo ngày/tuần và phân tích điều tra bằng AI Agent.{extra}\n"
            "Gõ `/help` để xem danh sách lệnh theo phân quyền của bạn."
        )

    def _cmd_help(self, principal: ZaloPrincipal) -> str:
        is_v4 = self.v4_facade is not None and getattr(self.v4_facade.config, "enabled", False)
        title = (
            f"📖 DANH SÁCH LỆNH LABOPS PERSONAL AI V4 ({principal.role}):"
            if is_v4
            else f"📖 DANH SÁCH LỆNH LABOPS AI SRE v2 ({principal.role}):"
        )
        lines = [title]
        for c, desc in self.COMMANDS.items():
            if c in {"/security", "/requests", "/who"} and not principal.is_admin:
                continue
            lines.append(f"• {c}: {desc}")
        lines.append("\nQuyền dữ liệu được kiểm soát chặt tại backend theo tài khoản LabOps.")
        return "\n".join(lines)

    def _cmd_status(self) -> str:
        """Dynamic runtime telemetry from UnifiedTelemetryCollector (NEVER hardcoded)."""
        snap = self.telemetry_collector.collect_snapshot()
        hosts = snap.get("hosts") or []
        gpus = snap.get("gpus") or []
        r_health = self.ai_client.check_health()
        open_incs = [i for i in self.incident_engine.list_incidents(limit=50) if i.get("status") != "RESOLVED"]

        lines = [
            "📊 TỔNG HỢP TRẠNG THÁI HẠ TẦNG (THỜI GIAN THỰC)",
            "━━━━━━━━━━━━━━━━━━━━",
            f"• Sức khỏe cụm: {snap.get('overall_health', 'UNKNOWN')} | Cập nhật: {_fmt_vn_time(snap.get('collected_at'))}",
            "🖥️ MÁY CHỦ:",
        ]
        if not hosts:
            lines.append("• Chưa có dữ liệu máy chủ (UNKNOWN)")
        else:
            for h in hosts:
                st = h.get("status", "UNKNOWN")
                cpu = f"{h['cpu_usage_percent']}%" if h.get("cpu_usage_percent") is not None else "N/A"
                ram = (
                    f"{h['ram_used_gb']}/{h['ram_total_gb']} GiB"
                    if h.get("ram_total_gb")
                    else "N/A"
                )
                disk = (
                    f"{h['disk_used_tb']}/{h['disk_total_tb']} TB ({h['disk_usage_percent']}%)"
                    if h.get("disk_total_tb")
                    else "N/A"
                )
                if st in {"ONLINE", "DEGRADED"}:
                    lines.append(
                        f"• {h.get('display_name')} ({h.get('hostname')}): {st}\n"
                        f"  CPU {cpu} | RAM {ram} | Ổ đĩa {disk}"
                    )
                else:
                    lines.append(
                        f"• {h.get('display_name')} ({h.get('hostname')}): {st} (Mất kết nối)"
                    )

        lines.append("🎮 GPU:")
        if not gpus:
            lines.append("• Chưa ghi nhận GPU khả dụng.")
        else:
            for g in gpus:
                if g.get("host_online"):
                    lines.append(
                        f"• {g['model']} ({g['host_id']}): "
                        f"VRAM {int(g.get('vram_used_mb') or 0):,}/{int(g.get('vram_total_mb') or 0):,} MiB | "
                        f"Hiệu suất {g.get('utilization_gpu')}% | Nhiệt độ {g.get('temp_celsius')}°C"
                    )
                else:
                    lines.append(f"• {g['model']} ({g['host_id']}): Máy chủ ngoại tuyến")

        lines.append(f"• AI Gateway: {r_health.get('status', 'UNKNOWN')}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🔔 SỰ CỐ ĐANG MỞ ({len(open_incs)}):")
        if open_incs:
            for inc in open_incs[:3]:
                lines.append(f"• [{inc['severity']}] {inc['incident_id']}: {inc['title']}")
        else:
            lines.append("• Không có sự cố đang mở.")
        return "\n".join(lines)

    def _cmd_jobs(self, principal: ZaloPrincipal, *, own_only: bool = False) -> str:
        res = self.tool_registry.execute_tool("get_active_jobs", {}, principal, source="zalo")
        jobs = list(res.get("jobs") or [])
        # Also include psutil live summary if registry list empty & admin or matching user
        if not jobs:
            ps_summary = self.report_generator._get_active_jobs_summary()
            for j in ps_summary.get("jobs") or []:
                if not own_only or principal.is_admin or j.get("user") == principal.username:
                    if not (not principal.is_admin and j.get("user") != principal.username):
                        jobs.append(j)
        if own_only and not principal.is_admin:
            jobs = [j for j in jobs if (j.get("owner") or j.get("user")) == principal.username]

        scope_lbl = f"của @{principal.username}" if (own_only or not principal.is_admin) else "toàn hệ thống"
        lines = [f"⚙️ TÁC VỤ ĐANG CHẠY ({scope_lbl}: {len(jobs)} tác vụ):", "━━━━━━━━━━━━━━━━━━━━"]
        if jobs:
            for idx, j in enumerate(jobs[:12], 1):
                title = j.get("display") or f"{j.get('owner') or 'user'} • {j.get('name')} [{j.get('status') or 'RUNNING'}]"
                lines.append(f"{idx}. {title}")
        else:
            lines.append("• Không có tác vụ tính toán đang chạy trong phạm vi quyền của bạn.")
        return "\n".join(lines)

    def _cmd_myworkspace(self, principal: ZaloPrincipal) -> str:
        res = self.tool_registry.execute_tool("get_workspace_usage", {}, principal, source="zalo")
        wss = res.get("workspaces") or []
        if not principal.is_admin:
            wss = [w for w in wss if w.get("owner") == principal.username]
        if not wss:
            return f"📦 Không tìm thấy Docker Workspace nào thuộc tài khoản @{principal.username}."
        lines = [f"📦 WORKSPACE CỦA @{principal.username}:", "━━━━━━━━━━━━━━━━━━━━"]
        for w in wss[:5]:
            lines.append(
                f"• {w.get('name')} ({w.get('host_id')}): state={w.get('container_state')} | "
                f"CPU {w.get('cpu_cores_limit')}c | RAM {w.get('ram_mb_limit')}MB | "
                f"Disk {w.get('storage_used_gb') or 'N/A'}/{w.get('storage_quota_gb')}GB | "
                f"OOM={w.get('oom_killed')} | Restart={w.get('restart_count')}"
            )
        return "\n".join(lines)

    def _cmd_allocations(self, principal: Optional[ZaloPrincipal] = None) -> str:
        eff_principal = principal or ZaloPrincipal("admin", "admin", "", "ADMIN", "Admin", True, True)
        try:
            from app.services import labops_service as core_mod

            groups = core_mod.service.get_gpu_allocations_by_device()
        except Exception:
            groups = []

        if groups:
            lines = ["📋 THỐNG KÊ TÀI KHOẢN THEO TỪNG GPU:", "━━━━━━━━━━━━━━━━━━━━"]
            for grp in groups:
                label = grp.get("gpu_label") or grp.get("gpu_id") or "GPU"
                model = grp.get("model") or "NVIDIA GPU"
                st = "ONLINE" if grp.get("is_online") else "OFFLINE/UNKNOWN"
                lines.append(f"\n🖥️ {label} • {model} ({st})")
                accs = grp.get("accounts") or []
                if not eff_principal.is_admin:
                    accs = [a for a in accs if a.get("username") == eff_principal.username]
                if not accs:
                    lines.append("  • Không có tài khoản được cấp phát.")
                    continue
                for a in accs:
                    lines.append(
                        f"  • @{a.get('username')} — {a.get('allocation_mode') or a.get('mode') or 'SHARED'} | Quota {a.get('vram_quota_gb')}GB | {a.get('status')}"
                    )
            return "\n".join(lines)

        res = self.tool_registry.execute_tool("get_gpu_metrics", {}, eff_principal, source="zalo")
        gpus = res.get("gpus") or []
        lines = ["📋 THỐNG KÊ TÀI KHOẢN THEO TỪNG GPU:", "━━━━━━━━━━━━━━━━━━━━"]
        if not gpus:
            lines.append("• Chưa có thiết bị GPU nào.")
            return "\n".join(lines)
        for g in gpus:
            st = "ONLINE" if g.get("host_online") else "OFFLINE/UNKNOWN"
            lines.append(f"\n🖥️ {g['gpu_key']} • {g['model']} ({st})")
            accs = g.get("allocations") or []
            if not accs:
                lines.append("  • Không có phân bổ hiển thị trong phạm vi quyền.")
                continue
            for a in accs:
                lines.append(
                    f"  • @{a.get('username')} — {a.get('mode')} | Quota {a.get('vram_quota_gb')}GB | {a.get('status')}"
                )
        return "\n".join(lines)

    def _cmd_incidents(self, principal: ZaloPrincipal) -> str:
        owner_f = None if principal.is_admin else principal.username
        incs = self.incident_engine.list_incidents(owner_id=owner_f, limit=15)
        open_incs = [i for i in incs if i.get("status") != "RESOLVED"]
        lines = [f"🚨 SỰ CỐ ĐANG MỞ ({len(open_incs)}):", "━━━━━━━━━━━━━━━━━━━━"]
        if not open_incs:
            lines.append("• Không có sự cố đang mở trong phạm vi quyền của bạn.")
            return "\n".join(lines)
        for inc in open_incs[:10]:
            lines.append(
                f"• {inc['incident_id']} [{inc['severity']}] ({inc['status']}) {inc.get('resource_id')}: {inc['title']}"
            )
        lines.append("\nDùng /incident <id> để xem chi tiết hoặc /ack <id> để xác nhận.")
        return "\n".join(lines)

    def _cmd_incident_detail(self, arg: str, principal: ZaloPrincipal) -> str:
        inc_id = str(arg or "").strip()
        if not inc_id:
            return "Vui lòng nhập mã sự cố. Ví dụ: /incident inc-abc123"
        inc = self.incident_engine.get_incident(inc_id)
        if inc is None:
            return f"❌ Không tìm thấy sự cố {inc_id}."
        if not principal.is_admin and inc.get("owner_id") != principal.username:
            return "⛔ Bạn không có quyền xem sự cố của thành viên khác."
        return (
            f"📌 CHI TIẾT SỰ CỐ {inc['incident_id']}\n"
            f"• Tiêu đề: {inc['title']}\n"
            f"• Mức độ: {inc['severity']} | Trạng thái: {inc['status']}\n"
            f"• Nguồn: {inc['source']} | Tài nguyên: {inc['resource_id']} | Chủ sở hữu: {inc.get('owner_id') or 'N/A'}\n"
            f"• Phát hiện: {_fmt_vn_time(inc['detected_at'])} | Cập nhật: {_fmt_vn_time(inc['last_observed_at'])} (Lặp: {inc['occurrence_count']})\n"
            f"• Khuyến nghị: {inc.get('recommended_action') or 'N/A'}"
        )

    def _cmd_ack(self, arg: str, principal: ZaloPrincipal) -> str:
        inc_id = str(arg or "").strip()
        if not inc_id:
            return "Vui lòng nhập mã sự cố cần xác nhận. Ví dụ: /ack inc-abc123"
        try:
            updated = self.incident_engine.acknowledge_incident(
                inc_id, actor=principal.username, role=principal.role, note="Acknowledged via Zalo Bot"
            )
            return f"✅ @{principal.username} đã xác nhận (ACKNOWLEDGED) sự cố {updated['incident_id']}."
        except PermissionError as p_err:
            return f"⛔ {p_err}"
        except Exception as exc:
            return f"❌ Không thể xác nhận sự cố: {exc}"

    def _cmd_security(self) -> str:
        res = self.tool_registry.execute_tool("get_security_events", {"limit": 10}, ZaloPrincipal("admin", "admin", "", "ADMIN", "Admin", True, True))
        sec_incs = res.get("security_incidents") or []
        open_sec = [i for i in sec_incs if i.get("status") != "RESOLVED"]
        lines = [
            f"🛡️ TỔNG QUAN AN NINH HỆ THỐNG ({len(open_sec)} sự cố repo đang mở):",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        for i in open_sec[:5]:
            lines.append(f"• {i['incident_id']} [{i['severity']}] {i['title']}")
        if not open_sec:
            lines.append("• Hiện không có sự cố Repository Guard đang mở.")
        lines.append(f"\nSự kiện Audit gần đây: {len(res.get('audit_events') or [])} mục.")
        return "\n".join(lines)

    def _cmd_requests(self, principal: ZaloPrincipal) -> str:
        res = self.tool_registry.execute_tool("get_pending_ssh_requests", {}, principal, source="zalo")
        if res.get("error"):
            return f"⛔ Không thể lấy danh sách đơn chờ duyệt: {res.get('error')}"
        p_reqs = res.get("pending_requests") or []
        w_sum = res.get("web_accounts_summary") or {}
        if not p_reqs:
            return (
                "📋 ĐƠN XIN CẤP TÀI KHOẢN SSH CHỜ DUYỆT (0 đơn):\n"
                f"• Hiện không có đơn nào đang chờ phê duyệt (Tổng TK Web: {w_sum.get('total', 0)})."
            )
        lines = [
            f"📋 ĐƠN XIN CẤP TÀI KHOẢN SSH ĐANG CHỜ DUYỆT ({len(p_reqs)} đơn):",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        for idx, item in enumerate(p_reqs[:10], 1):
            req_id = str(item.get("request_id") or "")
            pool_info = (
                f" | Ổ: {item.get('recommended_pool_id')} ({item.get('recommended_mount_point')})"
                if item.get("recommended_pool_id")
                else ""
            )
            pool_load = ""
            if item.get("pool_assigned_members") is not None and item.get("pool_net_allocatable_gib") is not None:
                pool_load = f" [Đang có {item['pool_assigned_members']} TV, trống {item['pool_net_allocatable_gib']} GiB]"
            lines.append(
                f"{idx}. {item.get('full_name')} (@{item.get('username')}, mã {item.get('member_code') or 'N/A'})\n"
                f"   • Mã đơn: {req_id} | GVHD: {item.get('supervisor_name')}\n"
                f"   • Đề tài: {item.get('project_title')}\n"
                f"   • Đề xuất: {item.get('recommended_host')}{pool_info}{pool_load} • {item.get('recommended_spec')} | SLA còn ~{item.get('remaining_sla_hours')}h"
            )
        first_uname = str((p_reqs[0] or {}).get("username") or "tai_khoan")
        lines.append(
            "\n💬 Admin không cần dùng lệnh cứng — chỉ cần nhắn tự nhiên cho tôi (Agent sẽ kiểm tra điều kiện và hỏi xác nhận lại 1 lần), ví dụ:\n"
            f"• \"@{first_uname} đồng ý duyệt cấp tài nguyên cho bạn ấy\"\n"
            f"• \"@{first_uname} tài khoản này tôi từ chối cấp vì lý do ...\""
        )
        return "\n".join(lines)

    # ---------------- Conversational Admin SSH Decision Agent ----------------

    def _draft_key(self, principal: ZaloPrincipal, chat_id: str) -> str:
        return f"{principal.username or principal.zalo_user_id}:{chat_id or principal.zalo_user_id}"

    def _get_decision_draft(self, principal: ZaloPrincipal, chat_id: str) -> Optional[Dict[str, Any]]:
        key = self._draft_key(principal, chat_id)
        now_m = time.monotonic()
        with self._decision_lock:
            draft = self._admin_decision_drafts.get(key)
            if not draft:
                return None
            if (now_m - float(draft.get("updated_at") or 0.0)) > self._decision_draft_ttl_sec:
                self._admin_decision_drafts.pop(key, None)
                return None
            return dict(draft)

    def _set_decision_draft(self, principal: ZaloPrincipal, chat_id: str, draft: Dict[str, Any]) -> None:
        key = self._draft_key(principal, chat_id)
        copy = dict(draft)
        copy["updated_at"] = time.monotonic()
        with self._decision_lock:
            self._admin_decision_drafts[key] = copy

    def _clear_decision_draft(self, principal: ZaloPrincipal, chat_id: str) -> None:
        key = self._draft_key(principal, chat_id)
        with self._decision_lock:
            self._admin_decision_drafts.pop(key, None)

    @staticmethod
    def _strip_accents_lower(text: str) -> str:
        from app.services.mram_service import _strip_vn_accents

        return _strip_vn_accents(text)

    @classmethod
    def _is_cancel_phrase(cls, text: str) -> bool:
        raw_lower = " ".join(re.sub(r"[,.!?;:]+", " ", str(text or "").lower()).split())
        if re.search(
            r"^(?:đừng|thôi\s+đừng|(?:thôi\s+)?(?:đừng|dừng|hủy|huỷ|ngưng)\s+(?:có\s+)?(?:duyệt|phê\s+duyệt|cấp|làm|từ\s+chối|yêu\s+cầu(?:\s+bổ\s+sung)?|bổ\s+sung|xác\s+nhận|chốt|thực\s+hiện|gửi)(?:\s+.*)?)$",
            raw_lower,
        ):
            return True
        if re.search(
            r"^(?:thôi\s+)?không\s+(?:có\s+)?(?:duyệt|phê\s+duyệt|cấp|làm|từ\s+chối|yêu\s+cầu(?:\s+bổ\s+sung)?|bổ\s+sung|xác\s+nhận|chốt|thực\s+hiện|gửi)\s+nữa(?:\s+(?:nhé|nha|em|đi|thôi|ạ)){0,3}$",
            raw_lower,
        ):
            return True
        norm = cls._strip_accents_lower(re.sub(r"[,.!?;:]+", " ", str(text or "")))
        if re.search(
            r"^(?:thoi\s+)?(?:khong|huy|ngung)\s+(?:co\s+)?(?:duyet|phe\s+duyet|cap|lam|tu\s+choi|yeu\s+cau(?:\s+bo\s+sung)?|bo\s+sung|xac\s+nhan|chot|thuc\s+hien|gui)\s+(?:nua|di|thoi)(?:\s+(?:nhe|nha|em|di|thoi|a)){0,3}$",
            norm,
        ):
            return True
        return norm in {
            "huy",
            "huy bo",
            "thoi",
            "bo qua",
            "khong lam nua",
            "dung lai",
            "khong duyet nua",
            "cancel",
            "abort",
        }

    @classmethod
    def _is_affirmative_confirmation(cls, text: str) -> bool:
        raw_lower = " ".join(re.sub(r"[,.!?;:]+", " ", str(text or "").lower()).split())
        if not raw_lower:
            return False
        if re.search(r"\b(?:đừng|không|chưa|sai|hủy|huỷ|thôi|dừng|ngưng)\b", raw_lower):
            return False
        norm = cls._strip_accents_lower(re.sub(r"[,.!?;:]+", " ", str(text or "")))
        if not norm or len(norm) > 60:
            return False
        if any(neg in norm.split() for neg in ("khong", "chua", "sai", "dung")):
            # Allow "dung roi" ("đúng rồi" -> dung roi), block "sai" / "khong" / "chua" / "dung duyet|lam"
            if re.search(r"\b(?:khong|chua|sai|dung\s+(?:co\s+)?(?:lam|duyet|cap|thuc\s+hien|chot|xac\s+nhan))\b", norm):
                return False
        confirm_exact = {
            "ok",
            "oke",
            "okay",
            "ok em",
            "ok nhe",
            "ok nha",
            "ok duyet",
            "ok duyet di",
            "ok dong y",
            "ok xac nhan",
            "ok chot",
            "ok thuc hien",
            "xac nhan",
            "xac nhan nhe",
            "xac nhan di",
            "dung",
            "dung roi",
            "dung roi em",
            "dung roi nhe",
            "chinh xac",
            "chuan",
            "chuan roi",
            "dong y",
            "dong y nhe",
            "duyet di",
            "duyet luon",
            "tu choi di",
            "thuc hien",
            "thuc hien di",
            "gui di",
            "chot",
            "chot nhe",
            "chot di",
            "chot duyet",
            "chot duyet di",
            "u",
            "uh",
            "vang",
            "yes",
            "confirm",
            "tien hanh",
            "tien hanh di",
        }
        if norm in confirm_exact:
            return True
        return bool(
            re.match(
                r"^(?:ok|oke|okay|dung\s+roi|xac\s+nhan|chinh\s+xac|chot|dong\s+y|thuc\s+hien|duyet\s+di|tien\s+hanh)\b(?:\s+(?:nhe|nha|em|di|luon|nha\s+em|xac\s+nhan|dong\s+y|duyet|tu\s+choi)){0,3}$",
                norm,
            )
        )

    @classmethod
    def _is_negative_rejection(cls, text: str) -> bool:
        norm = cls._strip_accents_lower(re.sub(r"[,.!?;:]+", " ", str(text or "")))
        return norm in {
            "khong",
            "khong dung",
            "chua dung",
            "sai",
            "sai roi",
            "khong phai",
            "chua chuan",
            "sua lai",
            "chinh lai",
        }

    @classmethod
    def _parse_natural_ssh_decision_intent(cls, text: str) -> Optional[str]:
        raw = str(text or "").strip()
        if not raw or raw.startswith("/") or cls._is_cancel_phrase(raw):
            return None
        norm = cls._strip_accents_lower(raw)

        # Explicit imperative decision overrides status/question checks unless ending as a status question
        imperative_action_patterns = (
            r"\bphe\s+duyet\b",
            r"\bdong\s+y\s+(?:duyet|cap)\b",
            r"\bduyet\s+cap\b",
            r"\bduyet\s+cho\b",
            r"\bduyet\s+don\b",
            r"\byeu\s+cau\s+bo\s+sung\b",
            r"\bde\s+nghi\s+bo\s+sung\b",
        )
        has_imperative_override = any(re.search(p, norm) for p in imperative_action_patterns)

        # Status/lookup questions must return None even when @username is mentioned
        status_question_phrases = (
            "duoc duyet chua",
            "da duyet chua",
            "duyet hay chua",
            "bi tu choi",
            "dang cho duyet",
            "cho duyet the nao",
            "xem don",
            "cho toi xem",
            "cho minh xem",
            "cho em xem",
            "ly do xin cap",
            "ai huong dan",
            "ginagr vien",
            "giang vien",
            "kiem tra",
            "trang thai don",
            "tinh trang don",
            "thong tin don",
            "co don nao",
            "danh sach",
            "nhung don nao",
            "bao nhieu don",
            "liet ke",
            "lam sao",
            "huong dan",
            "ai dang cho",
            "ai chua duoc",
            "tai sao",
            "vi sao",
            "ra sao",
            "the nao",
        )
        if (
            any(k in norm for k in status_question_phrases)
            or re.search(r"\bduyet\s+chua\s*(?:\?|$)", norm)
        ) and not has_imperative_override:
            return None

        # Avoid triggering on remaining pure lookup/status questions
        lookup_markers = (
            "trang thai",
            "tai sao don",
            "vi sao don",
        )
        has_at_mention = bool(re.search(r"@[a-z0-9_.-]+", raw.lower()))
        if any(k in norm for k in lookup_markers) and not has_at_mention:
            return None
        if re.match(r"^(?:tai sao|vi sao|ai da|kiem tra|cho xem)\b", norm):
            return None

        # Check REJECTED BEFORE APPROVED because "không đồng ý cấp" contains "đồng ý cấp"
        reject_patterns = (
            r"\btu choi\b",
            r"\bkhong duyet\b",
            r"\bkhong phe duyet\b",
            r"\bkhong dong y\s+(?:cap|duyet|phe duyet)\b",
            r"\bkhong cap\b",
            r"\bkhong chap nhan\b",
            r"\bkhong cho\b.*(?:dung|ssh|truy cap|tai nguyen|may)",
            r"\bhuy don\b",
            r"\btu choi ban\b",
            r"\btu choi don\b",
            r"\bbac bo\b",
            r"\b(?:co|tinh)\s+tu\s+choi\b",
        )
        for pat in reject_patterns:
            if re.search(pat, norm):
                return "REJECTED"

        needinfo_patterns = (
            r"\byeu cau bo sung\b",
            r"\bde nghi bo sung\b",
            r"\bnhac bo sung\b",
            r"\bbao bo sung\b",
            r"\b(?:yeu cau|de nghi|nhac|bao)\s+.{1,45}\bbo sung\b",
            r"\b(?:yeu cau|de nghi|nhac|bao)\s+.{1,45}\blam ro\b",
            r"\bgui lai don\b",
            r"\bbo sung them\b",
            r"\bbo sung thong tin\b",
            r"\bbo sung ho so\b",
            r"\bban ay bo sung\b",
            r"\bnhac ban ay bo sung\b",
            r"\bbao ban ay bo sung\b",
            r"\bcan bo sung\b",
            r"\byeu cau lam ro\b",
            r"\bcan lam ro\b",
            r"\bbo sung\b.*(?:thong tin|gvhd|de tai|muc tieu|ho so)",
        )
        for pat in needinfo_patterns:
            if re.search(pat, norm):
                return "NEED_INFO"

        approve_patterns = (
            r"\bphe duyet\b",
            r"\bdong y duyet\b",
            r"\bdong y cap\b",
            r"\bchap thuan\b",
            r"\bcho phep\b",
            r"\bmo quyen\b",
            r"\bmo tai khoan\b",
            r"\bcap may\b",
            r"\bcap cho\b",
            r"\bduyet cap\b",
            r"\bduyet don\b",
            r"\bduyet cho\b",
            r"\bduyet ban\b",
            r"\bduyet em\b",
            r"\bduyet tai khoan\b",
            r"\bduyet yeu cau\b",
            r"\bcap tai nguyen\b",
            r"\bcap ssh\b",
            r"\bcap quyen ssh\b",
            r"\bdong y cho\b.*(?:ssh|tai nguyen|dung may|workstation)",
        )
        norm_no_apply = re.sub(r"\b(?:xin|dang\s+ky)\s+cap\b", " xin ", norm)
        for pat in approve_patterns:
            if re.search(pat, norm_no_apply):
                return "APPROVED"

        norm_for_bare_approve = re.sub(r"\b(?:dang\s+cho|cho|chua|duoc)\s+duyet\b", " ", norm)
        if (has_at_mention or re.search(r"\b(?:sshreq-|req-|don\s+so\s+\d+|tai\s+khoan\s+|sinh\s+vien\s+|ban\s+)", norm)) and re.search(
            r"\b(?:duyet|approve)\b", norm_for_bare_approve
        ):
            if not re.search(r"\b(?:chua|sao|gi|nao|khong)\s*\?+$", cls._strip_accents_lower(raw)):
                return "APPROVED"

        return None

    @staticmethod
    def _clean_reason_text(raw_reason: str) -> str:
        s = " ".join(str(raw_reason or "").strip().split())
        s = re.sub(r'^["\']+|["\']+$', "", s).strip()
        s = re.sub(r"[,.!?;:\s]+$", "", s).strip()
        # Strip trailing polite Vietnamese sentence particles only when preceded by whitespace
        s = re.sub(r"\s+(?:nhé|nha|ạ|nhỉ|nhé em|đi em|đi|luôn nhé|giúp tôi|giúp mình)\s*$", "", s, flags=re.IGNORECASE).strip()
        s = re.sub(r"[,.!?;:\s]+$", "", s).strip()
        return s

    @classmethod
    def _extract_reason_from_decision_prose(cls, text: str, decision: str) -> str:
        raw = " ".join(str(text or "").strip().split())
        if not raw:
            return ""
        # Match explicit reason markers (vì lý do, bởi vì, lý do:, lý do ..., vì ...)
        m = re.search(
            r"(?:^|[\s,;:.])(?:vì\s+lý\s+do(?:\s+là|\s*[:\-])?|vi\s+ly\s+do(?:\s+la|\s*[:\-])?|lý\s+do(?:\s+là|\s*[:\-]|\s+)|ly\s+do(?:\s+la|\s*[:\-]|\s+)|bởi\s+vì\s+|boi\s+vi\s+|tại\s+vì\s+|tai\s+vi\s+|ghi\s+chú(?:\s+là|\s*[:\-]|\s+)|lời\s+nhắn(?:\s+là|\s*[:\-]|\s+)|vì\s+|vi\s+|do\s+là\s+)(.+)$",
            raw,
            flags=re.IGNORECASE,
        )
        if m:
            cleaned = cls._clean_reason_text(m.group(1))
            if cleaned:
                return cleaned

        if decision == "NEED_INFO":
            ni_m = re.search(
                r"(?:bổ\s+sung(?:\s+thêm|\s+thông\s+tin|\s+hồ\s+sơ)?|bo\s+sung(?:\s+them|\s+thong\s+tin|\s+ho\s+so)?|làm\s+rõ|lam\s+ro)\s*(?:là|:|\s)\s*(.+)$",
                raw,
                flags=re.IGNORECASE,
            )
            if ni_m:
                cleaned = cls._clean_reason_text(ni_m.group(1))
                if len(cleaned) >= 5:
                    return cleaned

        if decision == "REJECTED":
            # Support colon separator after account/verb ("Từ chối @sv_x: trùng đề tài")
            colon_m = re.search(r"[:]\s*([^:]{5,})$", raw)
            if colon_m:
                cleaned = cls._clean_reason_text(colon_m.group(1))
                if len(cleaned) >= 5:
                    return cleaned
        return ""

    @classmethod
    def _extract_resource_overrides_from_prose(cls, text: str) -> Dict[str, Any]:
        raw = str(text or "")
        norm = cls._strip_accents_lower(raw)
        ov: Dict[str, Any] = {}
        cpu_m = re.search(r"\b(\d{1,2})\s*(?:v?cpu|core|cores|nhan|luong)\b", norm)
        if cpu_m:
            ov["cpu_cores"] = int(cpu_m.group(1))
        ram_m = re.search(r"\b(\d{1,3})\s*(?:gb|gib)\s*ram\b|\bram\s*(\d{1,3})\s*(?:gb|gib)?\b", norm)
        if ram_m:
            ov["ram_gb"] = int(ram_m.group(1) or ram_m.group(2))
        disk_m = re.search(r"\b(\d{2,4})\s*(?:gb|gib)\s*(?:o|o\s*cung|disk|storage|dung\s*luong|luu\s*tru)\b", norm)
        if disk_m:
            ov["storage_quota_gb"] = int(disk_m.group(1))
        days_m = re.search(r"\b(\d{1,3})\s*ngay\b", norm)
        if days_m:
            ov["duration_days"] = int(days_m.group(1))

        if re.search(r"\b(?:khong|ko|no)\s+(?:dung\s+|cap\s+)?gpu\b", norm):
            ov["gpu_mode"] = "NONE"
        elif re.search(r"\b(?:exclusive|doc\s*quyen)\s+gpu\b", norm):
            ov["gpu_mode"] = "EXCLUSIVE"
        elif re.search(r"\b(?:co|dung|cap|shared)\s+gpu\b", norm):
            ov["gpu_mode"] = "SHARED"

        pool_m = re.search(r"\b(pool-[a-z0-9_-]+)\b", raw.lower())
        if pool_m:
            ov["pool_id"] = pool_m.group(1)
        else:
            uuid_m = re.search(
                r"(?:pool|o(?:\s+cung|\s+dia)?|disk)\s*[=:]?\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
                raw.lower(),
            )
            if uuid_m:
                ov["pool_id"] = uuid_m.group(1)
            else:
                path_m = re.search(r"(/mnt/hdd\d+|/dev/(?:sd[a-z]\d*|nvme\d+n\d+(?:p\d+)?))", raw, flags=re.IGNORECASE)
                if path_m:
                    ov["pool_id"] = path_m.group(1)
                else:
                    disk_nl_m = re.search(
                        r"\b(?:o(?:\s+cung|\s+dia)?|dia|disk|pool)\s*(?:luu\s+tru\s+|thu\s+cap\s+|ai\s+)?[:=]?\s*(hdd\d+|sd[a-z]\d*|nvme\d+n\d+(?:p\d+)?|root)\b",
                        norm,
                    )
                    if disk_nl_m:
                        tok = disk_nl_m.group(1).lower()
                        ov["pool_id"] = f"pool-{tok}"
        return ov

    def _finalize_or_ask_confirmation(
        self,
        *,
        principal: ZaloPrincipal,
        chat_id: str,
        decision: str,
        req: Dict[str, Any],
        reason: str,
        overrides: Dict[str, Any],
    ) -> Dict[str, Any]:
        from app.services.mram_service import STUDENT_DEFAULT_STORAGE_GB, STUDENT_MAX_RAM_GB, mram_service

        req_id = str(req["id"])
        uname = str(req.get("username") or "")
        full_name = str(req.get("full_name") or uname)
        cat = str(req.get("member_category") or "STUDENT").upper()
        prereq_notices: List[str] = []
        clean_ov = dict(overrides or {})

        if decision in {"REJECTED", "NEED_INFO"} and len(str(reason or "").strip()) < 5:
            draft = {
                "stage": "AWAITING_PREREQUISITE",
                "missing": "REASON",
                "decision": decision,
                "request_id": req_id,
                "request": req,
                "reason": str(reason or "").strip(),
                "overrides": clean_ov,
            }
            self._set_decision_draft(principal, chat_id, draft)
            verb_lbl = "TỪ CHỐI cấp tài nguyên" if decision == "REJECTED" else "YÊU CẦU BỔ SUNG thông tin"
            ask_what = "lý do từ chối" if decision == "REJECTED" else "nội dung cần bổ sung"
            return {
                "reply": (
                    f"🤖 Tôi đã hiểu Admin muốn {verb_lbl} cho tài khoản {full_name} (@{uname}, mã đơn {req_id}).\n"
                    f"• Điều kiện tiên quyết còn thiếu: Chưa có {ask_what} để gửi phản hồi cho tài khoản xin cấp.\n"
                    f"👉 Admin vui lòng nhắn thêm {ask_what} nhé (hoặc nhắn \"Hủy\" để dừng)?"
                ),
                "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_PREREQUISITE"},
            }

        eff_reason = str(reason or "").strip()
        cfg_preview: Dict[str, Any] = {}
        pool_mount = ""
        pool_load_note = ""
        if decision == "APPROVED":
            if cat == "STUDENT" and clean_ov.get("ram_gb") is not None and int(clean_ov["ram_gb"]) > STUDENT_MAX_RAM_GB:
                requested_ram = int(clean_ov["ram_gb"])
                clean_ov["ram_gb"] = STUDENT_MAX_RAM_GB
                prereq_notices.append(
                    f"⚠️ Điều kiện tiên quyết: @{uname} là Sinh viên nên hệ thống quy định mặc định {STUDENT_DEFAULT_STORAGE_GB} GiB ổ cứng "
                    f"và tối đa {STUDENT_MAX_RAM_GB} GiB RAM (Admin vừa nêu {requested_ram} GiB RAM). Tôi đã tự điều chỉnh về mức tối đa {STUDENT_MAX_RAM_GB} GiB RAM."
                )
            cfg_preview = mram_service.resolve_default_approval_config(req_id, overrides=clean_ov or None)
            dist_list = mram_service.get_host_pool_distribution(
                str(cfg_preview.get("host_id") or "workstation"),
                required_storage_gb=float(cfg_preview.get("storage_quota_gb") or STUDENT_DEFAULT_STORAGE_GB),
            )
            matched_pool = next((d for d in dist_list if d["pool_id"] == cfg_preview.get("pool_id")), None)
            if matched_pool:
                pool_mount = str(matched_pool.get("mount_point") or "")
                tier_tag = " • Ổ ƯU TIÊN" if int(matched_pool.get("priority_rank", 0)) == 0 else " • Ổ DỰ PHÒNG HẠN CHẾ"
                pool_load_note = f" (đang có {matched_pool['assigned_member_count']} TV, trống {matched_pool['net_allocatable_gib']} GiB{tier_tag})"
            if not eff_reason:
                eff_reason = (
                    f"Đã phê duyệt cấp tài nguyên mặc định trên máy chủ {cfg_preview['host_id']} "
                    f"(ổ {cfg_preview['pool_id']}, {cfg_preview['cpu_cores']} vCPU, {cfg_preview['ram_gb']} GiB RAM, "
                    f"{cfg_preview['storage_quota_gb']} GiB ổ cứng)."
                )

        draft = {
            "stage": "AWAITING_CONFIRMATION",
            "missing": None,
            "decision": decision,
            "request_id": req_id,
            "request": req,
            "reason": eff_reason,
            "overrides": clean_ov,
        }
        self._set_decision_draft(principal, chat_id, draft)

        if decision == "APPROVED":
            gpu_lbl = (
                f"SHARED ({cfg_preview.get('gpu_vram_gb') or 12} GiB VRAM)"
                if cfg_preview.get("gpu_mode") == "SHARED"
                else str(cfg_preview.get("gpu_mode") or "NONE")
            )
            pool_txt = f"{cfg_preview.get('pool_id')}" + (f" ({pool_mount})" if pool_mount else "") + pool_load_note
            lines = [
                "🤖 Tôi đã hiểu yêu cầu của Admin. Xin Admin xác nhận lại 1 lần nữa trước khi thực thi:",
                "━━━━━━━━━━━━━━━━━━━━",
                "• Hành động: ✅ PHÊ DUYỆT & CẤP PHÁT TÀI NGUYÊN SSH",
                f"• Tài khoản xin cấp: {full_name} (@{uname} • mã đơn {req_id} • {cat})",
                f"• Đề tài: {req.get('project_title')}",
                f"• Máy chủ mặc định: {cfg_preview.get('host_id') or 'workstation'}",
                f"• Ổ cứng lưu trữ (chia đều tự động): {pool_txt}",
                f"• Cấu hình dự kiến: {cfg_preview.get('cpu_cores')} vCPU | {cfg_preview.get('ram_gb')} GiB RAM | {cfg_preview.get('storage_quota_gb')} GiB Ổ cứng | GPU: {gpu_lbl}",
                f"• Lời nhắn gửi @{uname}: \"{eff_reason}\"",
            ]
            if prereq_notices:
                lines.append("")
                lines.extend(prereq_notices)
            lines.append("\n👉 Admin nhắn \"Xác nhận\", \"Đúng rồi\" hoặc \"OK\" để hệ thống cấp phát ngay (hoặc nhắn chỉnh sửa / \"Hủy\").")
            return {
                "reply": "\n".join(lines),
                "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_CONFIRMATION"},
            }

        action_lbl = "⛔ TỪ CHỐI cấp tài nguyên SSH" if decision == "REJECTED" else "⚠️ YÊU CẦU BỔ SUNG thông tin đơn SSH"
        reason_lbl = "Lý do từ chối sẽ gửi" if decision == "REJECTED" else "Nội dung yêu cầu bổ sung gửi"
        lines = [
            "🤖 Tôi đã hiểu yêu cầu của Admin. Xin Admin xác nhận lại 1 lần nữa trước khi thực thi:",
            "━━━━━━━━━━━━━━━━━━━━",
            f"• Hành động: {action_lbl}",
            f"• Tài khoản xin cấp: {full_name} (@{uname} • mã đơn {req_id})",
            f"• Đề tài: {req.get('project_title')}",
            f"• {reason_lbl} cho @{uname}: \"{eff_reason}\"",
            "\n👉 Admin nhắn \"Xác nhận\", \"Đúng rồi\" hoặc \"OK\" để tôi hoàn tất và phản hồi cho tài khoản xin cấp (hoặc nhắn \"Sửa lý do thành ...\", \"Hủy\").",
        ]
        return {
            "reply": "\n".join(lines),
            "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_CONFIRMATION"},
        }

    def _ask_target_prerequisite(
        self,
        *,
        principal: ZaloPrincipal,
        chat_id: str,
        decision: str,
        reason: str,
        overrides: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        ambiguous: bool = False,
    ) -> Dict[str, Any]:
        draft = {
            "stage": "AWAITING_PREREQUISITE",
            "missing": "TARGET",
            "decision": decision,
            "request_id": None,
            "request": None,
            "reason": str(reason or "").strip(),
            "overrides": dict(overrides or {}),
            "candidates": candidates[:10],
        }
        self._set_decision_draft(principal, chat_id, draft)
        dec_lbl = {"APPROVED": "PHÊ DUYỆT", "REJECTED": "TỪ CHỐI", "NEED_INFO": "YÊU CẦU BỔ SUNG"}.get(decision, decision)
        header = (
            f"🤖 Admin muốn {dec_lbl} đơn SSH, nhưng có nhiều tài khoản khớp với tên vừa nêu:"
            if ambiguous
            else f"🤖 Admin muốn {dec_lbl} đơn SSH, nhưng chưa chỉ rõ tài khoản nào trong danh sách chờ:"
        )
        lines = [header]
        for idx, c in enumerate(candidates[:8], 1):
            lines.append(
                f"{idx}. {c.get('full_name')} (@{c.get('username')}, mã {c.get('member_code') or 'N/A'}) — {c.get('project_title')} [{c.get('id')}]"
            )
        if reason:
            lines.append(f"• Lý do tôi đã ghi nhận tạm: \"{reason}\"")
        lines.append("\n👉 Admin vui lòng nhắn tên, `@username` hoặc số thứ tự (ví dụ \"số 1\") để tôi chuẩn bị bản xác nhận nhé (hoặc nhắn \"Hủy\").")
        return {
            "reply": "\n".join(lines),
            "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_PREREQUISITE"},
        }

    def _execute_confirmed_decision_draft(
        self,
        *,
        draft: Dict[str, Any],
        principal: ZaloPrincipal,
    ) -> Dict[str, Any]:
        from app.services.mram_service import MramConflictError, MramValidationError, mram_service

        req_id = str(draft["request_id"])
        decision = str(draft["decision"]).upper()
        reason = str(draft.get("reason") or "").strip()
        overrides = dict(draft.get("overrides") or {})

        try:
            res = mram_service.zalo_admin_decide_request(
                identifier=req_id,
                decision=decision,
                admin_username=principal.username or "admin",
                reason=reason,
                overrides=overrides or None,
            )
        except (LookupError, PermissionError, MramValidationError, MramConflictError) as exc:
            msg = getattr(exc, "message", None) or str(exc)
            return {
                "reply": f"⛔ Không thể thực thi quyết định cho đơn {req_id}: {msg}",
                "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_ERROR"},
            }
        except Exception as exc:
            return {
                "reply": f"⛔ Lỗi xử lý đơn SSH {req_id}: {exc}",
                "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_ERROR"},
            }

        req_data = res.get("request") or {}
        uname = req_data.get("username") or "member"
        final_reason = req_data.get("decision_reason") or reason
        app_cfg = (req_data.get("latest_approval") or {}).get("approved_config") or {}
        prov = res.get("provisioning") or {}
        ver_info = prov.get("verification") or {}
        notif = res.get("applicant_notification") or {}

        if decision == "APPROVED":
            host_lbl = app_cfg.get("host_id") or "workstation"
            pool_lbl = app_cfg.get("pool_id") or "N/A"
            mount_lbl = app_cfg.get("mount_point") or ""
            pool_full = f"{pool_lbl} ({mount_lbl})" if mount_lbl else str(pool_lbl)
            ssh_ip = ver_info.get("ssh_ip")
            ssh_port = ver_info.get("ssh_port")
            prov_status = str(prov.get("status") or req_data.get("status") or "")
            header = (
                f"✅ ĐÃ PHÊ DUYỆT & CẤP PHÁT ĐƠN SSH ({req_id})"
                if prov_status == "VERIFIED_READY"
                else f"⚠️ ĐÃ DUYỆT ĐƠN NHƯNG CẤP PHÁT SSH GẶP LỖI ({req_id})"
            )
            lines = [
                header,
                "━━━━━━━━━━━━━━━━━━━━",
                f"• Thành viên: @{uname} ({req_data.get('member_category') or 'STUDENT'})",
                f"• Máy chủ & Ổ lưu trữ: {host_lbl} | Pool {pool_full}",
                (
                    f"• Tài nguyên đã cấp: {app_cfg.get('cpu_cores', 4)} vCPU | "
                    f"{app_cfg.get('ram_gb', 16)} GiB RAM | "
                    f"{app_cfg.get('storage_quota_gb', 50)} GiB Ổ cứng | "
                    f"GPU: {app_cfg.get('gpu_mode', 'NONE')}"
                ),
                f"• Trạng thái cấp phát: {prov_status}",
            ]
            if prov_status != "VERIFIED_READY" and prov.get("error"):
                lines.append(f"• Chi tiết lỗi: {prov.get('error')}")
            if ssh_ip and ssh_port:
                lines.append(f"• SSH Endpoint: {ssh_ip}:{ssh_port}")
            lines.append(f"• Phản hồi đã lưu: {final_reason}")
            lines.append(f"• Thông báo cho người xin cấp: {notif.get('delivery_detail') or notif.get('delivery_status') or 'Đã cập nhật trên Web Portal'}")
            return {
                "reply": "\n".join(lines),
                "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_EXECUTED"},
            }

        title = "⛔ ĐÃ TỪ CHỐI ĐƠN SSH" if decision == "REJECTED" else "⚠️ ĐÃ YÊU CẦU BỔ SUNG THÔNG TIN"
        return {
            "reply": "\n".join(
                [
                    f"{title} ({req_id})",
                    "━━━━━━━━━━━━━━━━━━━━",
                    f"• Thành viên: @{uname}",
                    f"• Lý do phản hồi gửi người xin cấp: {final_reason}",
                    f"• Thông báo cho người xin cấp: {notif.get('delivery_detail') or notif.get('delivery_status') or 'Đã cập nhật trên Web Portal'}",
                ]
            ),
            "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_EXECUTED"},
        }

    def _try_handle_conversational_ssh_decision(
        self,
        text: str,
        principal: ZaloPrincipal,
        *,
        chat_id: str,
        source: str,
    ) -> Optional[Dict[str, Any]]:
        clean = str(text or "").strip()
        if not clean or clean.startswith("/"):
            return None

        from app.services.mram_service import mram_service

        draft = self._get_decision_draft(principal, chat_id) if principal.is_admin else None

        # 1. Handle ongoing Admin draft conversation (AWAITING_PREREQUISITE / AWAITING_CONFIRMATION)
        if draft is not None:
            if source != "dashboard" and principal.zalo_user_id and chat_id and chat_id != principal.zalo_user_id:
                return {
                    "reply": "⛔ Không chấp nhận xác nhận lệnh phê duyệt/từ chối trong nhóm chat. Vui lòng nhắn riêng (DM 1-1) với Bot.",
                    "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_BLOCKED"},
                }

            if self._is_cancel_phrase(clean):
                self._clear_decision_draft(principal, chat_id)
                return {
                    "reply": "❎ Đã hủy yêu cầu đang soạn. Không có thay đổi nào được thực thi.",
                    "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_CANCELLED"},
                }

            stage = draft.get("stage")
            if stage == "AWAITING_PREREQUISITE":
                missing = draft.get("missing")
                if missing == "TARGET":
                    candidates = list(draft.get("candidates") or [])
                    selected_req: Optional[Dict[str, Any]] = None
                    bare_num = re.fullmatch(r"(?:so|thu|#)?\s*(\d{1,2})", self._strip_accents_lower(clean))
                    if bare_num and candidates:
                        idx = int(bare_num.group(1))
                        if 1 <= idx <= len(candidates):
                            selected_req = candidates[idx - 1]
                    if selected_req is None:
                        m_info = mram_service.match_pending_requests_by_query(clean)
                        if len(m_info["matches"]) == 1:
                            selected_req = m_info["matches"][0]
                        elif len(m_info["matches"]) > 1:
                            return self._ask_target_prerequisite(
                                principal=principal,
                                chat_id=chat_id,
                                decision=draft["decision"],
                                reason=draft.get("reason") or "",
                                overrides=draft.get("overrides") or {},
                                candidates=m_info["matches"],
                                ambiguous=True,
                            )
                        else:
                            # Bare last name word match among candidates ("Tiến", "Chương")
                            clean_norm = self._strip_accents_lower(clean)
                            name_hits = [
                                c for c in candidates
                                if clean_norm and clean_norm in self._strip_accents_lower(c.get("full_name") or "").split()
                            ]
                            if len(name_hits) == 1:
                                selected_req = name_hits[0]
                    if selected_req is None:
                        # Check if user moved on to an unrelated query
                        if (
                            re.search(
                                r"\b(?:gpu|cpu|ram|nhiet|su co|incident|job|status|bao cao|da duyet chua|duoc duyet chua|cho duyet|danh sach don|trang thai don|xem don)\b",
                                self._strip_accents_lower(clean),
                            )
                            and not self._parse_natural_ssh_decision_intent(clean)
                        ):
                            self._clear_decision_draft(principal, chat_id)
                            return None
                        return self._ask_target_prerequisite(
                            principal=principal,
                            chat_id=chat_id,
                            decision=draft["decision"],
                            reason=draft.get("reason") or "",
                            overrides=draft.get("overrides") or {},
                            candidates=candidates or mram_service.match_pending_requests_by_query("")["all_pending"],
                            ambiguous=False,
                        )
                    extra_reason = self._extract_reason_from_decision_prose(clean, draft["decision"])
                    extra_ov = self._extract_resource_overrides_from_prose(clean)
                    merged_ov = {**(draft.get("overrides") or {}), **extra_ov}
                    return self._finalize_or_ask_confirmation(
                        principal=principal,
                        chat_id=chat_id,
                        decision=draft["decision"],
                        req=selected_req,
                        reason=extra_reason or draft.get("reason") or "",
                        overrides=merged_ov,
                    )

                if missing == "REASON":
                    # Check if Admin actually gave a totally unrelated system question instead of a reason
                    if re.search(
                        r"\b(?:nhiet do|trang thai may chu|su co dang mo|kiem tra gpu|job dang chay|da duyet chua|duoc duyet chua|cho duyet|danh sach don|trang thai don|xem don)\b",
                        self._strip_accents_lower(clean),
                    ):
                        self._clear_decision_draft(principal, chat_id)
                        return None
                    extracted = self._extract_reason_from_decision_prose(clean, draft["decision"]) or self._clean_reason_text(
                        re.sub(
                            r"^(?:vì\s+lý\s+do(?:\s+là)?|lý\s+do(?:\s+là|\s*[:\-])|vì\s+|do\s+|là\s+)",
                            "",
                            clean,
                            flags=re.IGNORECASE,
                        )
                    )
                    if len(extracted) < 5:
                        return {
                            "reply": (
                                "⚠️ Lý do phản hồi cho người xin cấp cần tối thiểu 5 ký tự rõ nghĩa.\n"
                                "👉 Admin vui lòng nhập lý do cụ thể hơn (hoặc nhắn \"Hủy\" để dừng)?"
                            ),
                            "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_PREREQUISITE"},
                        }
                    return self._finalize_or_ask_confirmation(
                        principal=principal,
                        chat_id=chat_id,
                        decision=draft["decision"],
                        req=draft["request"],
                        reason=extracted,
                        overrides=draft.get("overrides") or {},
                    )

            elif stage == "AWAITING_CONFIRMATION":
                if self._is_affirmative_confirmation(clean):
                    self._clear_decision_draft(principal, chat_id)
                    return self._execute_confirmed_decision_draft(draft=draft, principal=principal)

                if self._is_negative_rejection(clean):
                    return {
                        "reply": (
                            "ℹ️ Bản nháp vẫn đang được giữ. Admin muốn sửa nội dung nào ạ?\n"
                            "• Ví dụ: `Sửa lý do thành ...` hoặc `Đổi sang từ chối vì ...`\n"
                            "• Hoặc nhắn `Hủy` để bỏ qua yêu cầu này."
                        ),
                        "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_CONFIRMATION"},
                    }

                # Check if Admin wants to edit reason / decision / resources on the open draft
                edit_reason_m = re.search(
                    r"(?:sửa\s+lý\s+do|sua\s+ly\s+do|đổi\s+lý\s+do|doi\s+ly\s+do|thay\s+lý\s+do|chỉnh\s+lý\s+do|cập\s+nhật\s+lý\s+do|đổi\s+lời\s+nhắn|sửa\s+lời\s+nhắn)\s*(?:thành|là|sang|:)?\s*(.+)$",
                    clean,
                    flags=re.IGNORECASE,
                )
                if edit_reason_m:
                    raw_new_r = edit_reason_m.group(1).strip()
                    auto_confirm = bool(
                        re.search(r"\b(?:rồi|và|xong)\s+(?:chốt|xác\s+nhận|thực\s+hiện|gửi\s+luôn)\b", raw_new_r, flags=re.IGNORECASE)
                    )
                    new_reason = re.sub(
                        r"\s+(?:rồi|và|xong)\s+(?:chốt|xác\s+nhận|thực\s+hiện|gửi).*$",
                        "",
                        raw_new_r,
                        flags=re.IGNORECASE,
                    )
                    new_reason = self._clean_reason_text(new_reason)
                    if len(new_reason) < 5:
                        return {
                            "reply": "⚠️ Lý do mới quá ngắn (cần tối thiểu 5 ký tự). Admin vui lòng nhập rõ hơn nhé.",
                            "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_PREREQUISITE"},
                        }
                    if auto_confirm:
                        updated_draft = {**draft, "reason": new_reason}
                        self._clear_decision_draft(principal, chat_id)
                        return self._execute_confirmed_decision_draft(draft=updated_draft, principal=principal)
                    return self._finalize_or_ask_confirmation(
                        principal=principal,
                        chat_id=chat_id,
                        decision=draft["decision"],
                        req=draft["request"],
                        reason=new_reason,
                        overrides=draft.get("overrides") or {},
                    )

                new_ov = self._extract_resource_overrides_from_prose(clean)
                switch_intent = self._parse_natural_ssh_decision_intent(clean)
                if new_ov and not switch_intent:
                    merged_ov = {**(draft.get("overrides") or {}), **new_ov}
                    return self._finalize_or_ask_confirmation(
                        principal=principal,
                        chat_id=chat_id,
                        decision=draft["decision"],
                        req=draft["request"],
                        reason=draft.get("reason") or "",
                        overrides=merged_ov,
                    )

                if switch_intent is not None:
                    switch_match = mram_service.match_pending_requests_by_query(clean)
                    matched_list = list(switch_match.get("matches") or [])
                    if len(matched_list) == 1:
                        target_req = matched_list[0]
                        same_target = str(target_req.get("id")) == str(draft.get("request_id"))
                        switched_reason = self._extract_reason_from_decision_prose(clean, switch_intent)
                        switched_ov = {**(draft.get("overrides") or {}), **new_ov} if same_target else dict(new_ov)
                        if not switched_reason and same_target and str(draft.get("decision") or "").upper() != "APPROVED":
                            switched_reason = str(draft.get("reason") or "").strip()
                        return self._finalize_or_ask_confirmation(
                            principal=principal,
                            chat_id=chat_id,
                            decision=switch_intent,
                            req=target_req,
                            reason=switched_reason,
                            overrides=switched_ov,
                        )
                    if len(matched_list) > 1:
                        return self._ask_target_prerequisite(
                            principal=principal,
                            chat_id=chat_id,
                            decision=switch_intent,
                            reason=self._extract_reason_from_decision_prose(clean, switch_intent),
                            overrides=dict(new_ov),
                            candidates=matched_list,
                            ambiguous=True,
                        )
                    if not switch_match.get("explicit_unmatched_mention"):
                        switched_reason = self._extract_reason_from_decision_prose(clean, switch_intent)
                        switched_ov = {**(draft.get("overrides") or {}), **new_ov}
                        if not switched_reason and str(draft.get("decision") or "").upper() != "APPROVED":
                            switched_reason = str(draft.get("reason") or "").strip()
                        return self._finalize_or_ask_confirmation(
                            principal=principal,
                            chat_id=chat_id,
                            decision=switch_intent,
                            req=draft["request"],
                            reason=switched_reason,
                            overrides=switched_ov,
                        )

                # If it's neither a new decision nor a confirmation/edit, check if Admin moved to an unrelated question
                if not switch_intent:
                    norm_c = self._strip_accents_lower(clean)
                    if any(
                        w in norm_c
                        for w in (
                            "nhiet do",
                            "gpu",
                            "cpu",
                            "su co",
                            "job",
                            "server",
                            "may chu",
                            "vpn",
                            "quota",
                            "da duyet chua",
                            "duoc duyet chua",
                            "cho duyet",
                            "danh sach don",
                            "trang thai don",
                            "xem don",
                        )
                    ):
                        self._clear_decision_draft(principal, chat_id)
                        return None
                    req_cur = draft.get("request") or {}
                    return {
                        "reply": (
                            f"⏳ Tôi vẫn đang chờ Admin xác nhận bản nháp cho @{req_cur.get('username')} ({draft.get('decision')}).\n"
                            "👉 Admin nhắn `Xác nhận` / `OK` để thực thi, `Sửa lý do thành ...` để chỉnh, hoặc `Hủy` để bỏ qua."
                        ),
                        "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_CONFIRMATION"},
                    }

        # 2. Detect new natural-language SSH request decision intent
        decision = self._parse_natural_ssh_decision_intent(clean)
        if decision is None:
            return None

        if not principal.is_admin:
            return {
                "forbidden": True,
                "reply": "⛔ Chỉ Quản trị viên (ADMIN) mới có quyền phê duyệt, từ chối hoặc yêu cầu bổ sung đơn xin cấp tài nguyên SSH.",
            }

        if source != "dashboard" and principal.zalo_user_id and chat_id and chat_id != principal.zalo_user_id:
            return {
                "reply": "⛔ Không chấp nhận lệnh phê duyệt/từ chối tài nguyên trong nhóm chat. Vui lòng nhắn riêng (DM 1-1) với Bot.",
                "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_BLOCKED"},
            }

        match_info = mram_service.match_pending_requests_by_query(clean)
        matches = match_info["matches"]
        all_pending = match_info["all_pending"]
        unmatched_mention = match_info["explicit_unmatched_mention"]
        reason = self._extract_reason_from_decision_prose(clean, decision)
        overrides = self._extract_resource_overrides_from_prose(clean)

        if unmatched_mention:
            self._clear_decision_draft(principal, chat_id)
            if not all_pending:
                return {
                    "reply": f"ℹ️ Không tìm thấy đơn xin cấp SSH nào của {unmatched_mention} đang chờ duyệt (hiện không có đơn nào trong hàng đợi).",
                    "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_NOT_FOUND"},
                }
            sample = "\n".join(
                f"{idx}. {r.get('full_name')} (@{r.get('username')}) — {r.get('project_title')} [{r.get('id')}]"
                for idx, r in enumerate(all_pending[:6], 1)
            )
            return {
                "reply": (
                    f"⛔ Không tìm thấy đơn SSH đang chờ duyệt nào cho tài khoản {unmatched_mention}.\n"
                    f"Các đơn đang chờ hiện tại:\n{sample}"
                ),
                "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_NOT_FOUND"},
            }

        if not all_pending:
            self._clear_decision_draft(principal, chat_id)
            return {
                "reply": "ℹ️ Hiện tại không có đơn xin cấp tài nguyên SSH nào đang ở trạng thái chờ phê duyệt.",
                "meta": {"v4_route": "v4", "v4_intent": "ADMIN_DECISION_NOT_FOUND"},
            }

        if len(matches) == 0:
            norm_clean = self._strip_accents_lower(clean)
            has_pronoun_ref = bool(
                re.search(
                    r"\b(?:don nay|don do|don vua xem|tai khoan nay|tai khoan do|ban nay|ban ay|ban do|em nay|em ay|sinh vien nay|nguoi nay)\b",
                    norm_clean,
                )
            )
            if len(all_pending) == 1 and has_pronoun_ref:
                matches = [all_pending[0]]
            else:
                ctx_req = self._resolve_recent_session_ssh_request(
                    principal=principal,
                    chat_id=chat_id,
                    source=source,
                    all_pending=all_pending,
                    allow_implicit_single=not has_pronoun_ref,
                )
                if ctx_req is not None and (
                    has_pronoun_ref
                    or re.search(r"\b(?:duyet\s+(?:luon|di|nhe|nha)|dong\s+y|tu\s+choi|bo\s+sung|cho\s+phep|cap\s+cho)\b", norm_clean)
                ):
                    matches = [ctx_req]
                else:
                    return self._ask_target_prerequisite(
                        principal=principal,
                        chat_id=chat_id,
                        decision=decision,
                        reason=reason,
                        overrides=overrides,
                        candidates=all_pending,
                        ambiguous=False,
                    )

        if len(matches) > 1:
            return self._ask_target_prerequisite(
                principal=principal,
                chat_id=chat_id,
                decision=decision,
                reason=reason,
                overrides=overrides,
                candidates=matches,
                ambiguous=True,
            )

        return self._finalize_or_ask_confirmation(
            principal=principal,
            chat_id=chat_id,
            decision=decision,
            req=matches[0],
            reason=reason,
            overrides=overrides,
        )

    def _record_v4_turn_memory(
        self,
        *,
        principal: ZaloPrincipal,
        source: str,
        chat_id: str,
        user_text: str,
        reply_text: str,
    ) -> None:
        if (
            self.v4_facade is None
            or not getattr(self.v4_facade.config, "enabled", False)
            or not getattr(self.v4_facade.config, "natural_language", False)
        ):
            return
        try:
            pid = self._principal_owner_id(principal)
            eff_chat = chat_id or principal.zalo_user_id or principal.username
            sess = self.v4_facade.memory.get_or_create_session(
                principal_id=pid,
                channel=source,
                chat_id=eff_chat,
            )
            self.v4_facade.memory.append_turn_messages(
                session_id=sess["session_id"],
                expected_version=None,
                entries=[
                    {"role": "user", "content": str(user_text or "").strip()},
                    {"role": "assistant", "content": str(reply_text or "").strip()},
                ],
            )
        except Exception:
            pass

    def _resolve_recent_session_ssh_request(
        self,
        *,
        principal: ZaloPrincipal,
        chat_id: str,
        source: str,
        all_pending: List[Dict[str, Any]],
        allow_implicit_single: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if not all_pending:
            return None
        by_req_id = {str(r.get("id") or "").lower(): r for r in all_pending if r.get("id")}
        by_uname = {str(r.get("username") or "").lower(): r for r in all_pending if r.get("username")}

        candidates_text: List[tuple[str, str]] = []
        if self.v4_facade is not None and getattr(self.v4_facade.config, "enabled", False):
            try:
                pid = self._principal_owner_id(principal)
                eff_chat = chat_id or principal.zalo_user_id or principal.username
                sess = self.v4_facade.memory.get_or_create_session(
                    principal_id=pid,
                    channel=source,
                    chat_id=eff_chat,
                )
                hist = self.v4_facade.memory.get_bounded_context(
                    session_id=sess["session_id"],
                    principal_id=pid,
                    channel=source,
                    chat_id=eff_chat,
                )
                for m in reversed(hist[-8:]):
                    candidates_text.append((str(m.get("role") or ""), str(m.get("content") or "")))
            except Exception:
                pass

        for h in reversed(self.message_history[-8:]):
            if str(h.get("username") or "") == str(principal.username or ""):
                candidates_text.append(("user", str(h.get("incoming_text") or "")))
                candidates_text.append(("assistant", str(h.get("reply_snippet") or "")))

        for idx, (role, content) in enumerate(candidates_text):
            low = content.lower()
            req_ids = list(dict.fromkeys(re.findall(r"\b(sshreq-[a-z0-9_-]+)\b", low)))
            matched_rids = [by_req_id[rid] for rid in req_ids if rid in by_req_id]
            if len(matched_rids) == 1:
                if not allow_implicit_single or ("chi tiết đơn xin cấp" in low or idx <= 1):
                    return matched_rids[0]
            elif len(matched_rids) > 1:
                # Last turn listed multiple requests -> do not guess blindly
                return None

            mentions = [m.lower() for m in re.findall(r"@([a-z0-9_.-]+)", low) if m.lower() != "admin"]
            matched_users = [by_uname[u] for u in dict.fromkeys(mentions) if u in by_uname]
            if len(matched_users) == 1:
                if not allow_implicit_single or idx <= 1:
                    return matched_users[0]
            elif len(matched_users) > 1:
                return None
        return None

    def _try_handle_natural_operational_action(
        self,
        text: str,
        principal: ZaloPrincipal,
        *,
        chat_id: str,
        source: str,
    ) -> Optional[Dict[str, Any]]:
        clean = str(text or "").strip()
        if not clean or clean.startswith("/"):
            return None
        norm = self._strip_accents_lower(re.sub(r"[,.!?;:]+", " ", clean))

        # Do not intercept @username dossier lookups or natural SSH decisions
        has_at_mention = bool(re.search(r"@[a-z0-9_.-]+", clean.lower()))
        if self._parse_natural_ssh_decision_intent(clean) is not None:
            return None

        # A. Greetings, identity & capability guidance
        greet_re = re.compile(
            r"^(?:alo+|hello+|hi+|chao(?:\s+(?:ban|em|anh|chi|bot|ai))?|xin\s+chao|"
            r"ban\s+la\s+ai|ban\s+ten\s+gi|gioi\s+thieu|"
            r"(?:ban\s+)?(?:co\s+the\s+)?lam\s+(?:duoc\s+)?(?:nhung\s+)?gi(?:\s+cho\s+toi)?|"
            r"ban\s+ho\s+tro\s+(?:duoc\s+)?(?:nhung\s+)?gi|"
            r"(?:co\s+the\s+)?giup\s+(?:duoc\s+)?gi(?:\s+cho\s+toi)?|giup\s+toi(?:\s+voi)?)$"
        )
        if len(norm.split()) <= 10 and greet_re.match(norm):
            snap = self.telemetry_collector.collect_snapshot()
            hosts = snap.get("hosts") or []
            online_cnt = sum(1 for h in hosts if h.get("status") in {"ONLINE", "DEGRADED"})
            open_incs = [
                i
                for i in self.incident_engine.list_incidents(
                    owner_id=None if principal.is_admin else principal.username,
                    limit=50,
                )
                if i.get("status") != "RESOLVED"
            ]
            pending_cnt = 0
            if principal.is_admin:
                try:
                    from app.services.mram_service import mram_service

                    pending_cnt = len(mram_service.list_requests_for_admin(status_filter="PENDING_APPROVAL"))
                except Exception:
                    pending_cnt = 0
            lines = [
                f"👋 Xin chào @{principal.username}! Tôi là LabOps Personal AI Agent V4 ({principal.role}).",
                f"• Cụm hiện tại: {online_cnt}/{len(hosts)} máy chủ ONLINE | {len(open_incs)} sự cố mở"
                + (f" | {pending_cnt} đơn SSH chờ duyệt." if principal.is_admin else "."),
                "",
                "💬 Bạn có thể nhắn tự nhiên (không cần thuộc lệnh `/`) để tôi thực hiện ngay:",
            ]
            if principal.is_admin:
                lines.extend(
                    [
                        "• Xét duyệt SSH: \"tôi có cần kiểm duyệt ssh nào không\", \"cho tôi xem @sv_nguyenhuutien\", \"duyệt cho bạn này nhé\", \"từ chối @user vì lý do ...\"",
                        "• Báo cáo & Sự cố: \"tạo báo cáo ngày hôm nay\", \"lập báo cáo tuần\", \"xác nhận sự cố đang mở\", \"đóng sự cố inc-... vì đã xong\"",
                    ]
                )
            lines.extend(
                [
                    "• Cảnh báo & Nhắc việc: \"báo tôi khi CPU workstation vượt 85% trong 5 phút\", \"nhắc tôi sau 30 phút kiểm tra job\", \"hủy cảnh báo vừa tạo\"",
                    "• Hạ tầng & Phiên chat: \"ai đang dùng nhiều GPU nhất\", \"so sánh tải hôm nay với hôm qua\", \"hạn mức tin nhắn zalo\", \"tôi đã ra các lệnh nào\", \"xóa bộ nhớ chat\"",
                ]
            )
            return {"reply": "\n".join(lines), "meta": {"v4_route": "v4", "v4_intent": "CAPABILITY_HELP"}}

        # B. User's command & question history ("tôi đã ra các lệnh nào", "lịch sử lệnh")
        if re.search(
            r"\b(?:da\s+ra\s+(?:cac\s+|nhung\s+)?lenh|lich\s+su\s+(?:lenh|cau\s+hoi|chat|thao\s+tac)|"
            r"vua\s+(?:hoi|ra\s+lenh|gui)\s+(?:nhung\s+)?gi|nay\s+gio\s+(?:toi\s+)?(?:noi|hoi)\s+gi)\b",
            norm,
        ):
            items: List[str] = []
            if self.v4_facade is not None:
                try:
                    pid = self._principal_owner_id(principal)
                    eff_chat = chat_id or principal.zalo_user_id or principal.username
                    sess = self.v4_facade.memory.get_or_create_session(
                        principal_id=pid,
                        channel=source,
                        chat_id=eff_chat,
                    )
                    with self.v4_facade.db.session_scope() as session:
                        from app.db.models import V4ChatMessageRecord

                        rows = (
                            session.execute(
                                select(V4ChatMessageRecord)
                                .where(
                                    V4ChatMessageRecord.session_id == sess["session_id"],
                                    V4ChatMessageRecord.role == "user",
                                )
                                .order_by(V4ChatMessageRecord.seq.desc())
                                .limit(12)
                            )
                            .scalars()
                            .all()
                        )
                        for r in reversed(rows):
                            ts = _fmt_vn_time(r.created_at.isoformat() if r.created_at else "")
                            items.append(f"[{ts}] {r.content_redacted}")
                except Exception:
                    pass
            if not items:
                for h in self.message_history[-12:]:
                    if str(h.get("username") or "") == str(principal.username or ""):
                        ts = _fmt_vn_time(h.get("timestamp"))
                        items.append(f"[{ts}] {h.get('incoming_text')}")
            lines = [f"📜 LỊCH SỬ CÁC LỆNH & CÂU HỎI GẦN ĐÂY CỦA @{principal.username} ({len(items)} mục):"]
            if not items:
                lines.append("• Hiện chưa ghi nhận lệnh nào trong phiên này.")
            else:
                for idx, entry in enumerate(items, 1):
                    lines.append(f"{idx}. {entry}")
            return {"reply": "\n".join(lines), "meta": {"v4_route": "v4", "v4_intent": "COMMAND_HISTORY"}}

        # C. Natural-language Daily / Weekly Reports
        if re.search(r"\b(?:tao|lap|xem|gui|doc)\s+bao\s+cao\b|\btom\s+tat\s+tinh\s+hinh\b", norm) and "ssh" not in norm:
            wants_gen = any(w in norm for w in ("tao", "lap", "moi", "tong hop", "cap nhat"))
            is_weekly = "tuan" in norm or "weekly" in norm
            if wants_gen and not principal.is_admin:
                return {"forbidden": True, "reply": "⛔ Chỉ quản trị viên mới được phép khởi tạo báo cáo mới."}
            if is_weekly:
                arg = "tạo tuần" if wants_gen else ""
            else:
                arg = "today tạo" if wants_gen else "today"
            return {"reply": self._cmd_report(arg, principal), "meta": {"v4_route": "v4", "v4_intent": "NL_REPORT_ACTION"}}

        # D. Natural-language Incident ACK / RESOLVE / Detail
        inc_match = re.search(r"\b(inc-[a-z0-9_-]+)\b", clean.lower())
        if re.search(r"\b(?:xem|chi\s+tiet)\s+su\s+co\b", norm) and inc_match:
            return {"reply": self._cmd_incident_detail(inc_match.group(1), principal), "meta": {"v4_route": "v4", "v4_intent": "NL_INCIDENT_DETAIL"}}

        is_ack_inc = bool(
            re.search(
                r"\b(?:xac\s+nhan|tiep\s+nhan|ack)\s+(?:(?:giup|ho)(?:\s+toi)?\s+|(?:tat\s+ca|toan\s+bo|cac|nhung|het)\s+)*su\s+co\b",
                norm,
            )
        )
        is_resolve_inc = bool(
            re.search(
                r"\b(?:dong|giai\s+quyet|xu\s+ly\s+xong|hoan\s+tat|resolve)\s+(?:(?:giup|ho)(?:\s+toi)?\s+|(?:tat\s+ca|toan\s+bo|cac|nhung|het)\s+)*su\s+co\b",
                norm,
            )
        )
        if is_ack_inc or is_resolve_inc:
            target_inc_id = inc_match.group(1) if inc_match else ""
            if is_resolve_inc and not principal.is_admin:
                return {"forbidden": True, "reply": "⛔ Chỉ Quản trị viên (Admin) mới có quyền đóng (RESOLVED) sự cố."}
            if not target_inc_id:
                open_incs = [
                    i
                    for i in self.incident_engine.list_incidents(
                        owner_id=None if principal.is_admin else principal.username,
                        limit=20,
                    )
                    if i.get("status") != "RESOLVED"
                ]
                if not open_incs:
                    return {"reply": "✅ Hiện không có sự cố nào đang mở cần xử lý.", "meta": {"v4_route": "v4", "v4_intent": "NL_INCIDENT_ACTION"}}
                if len(open_incs) > 1 and is_ack_inc and any(w in norm for w in ("tat ca", "toan bo", "het", "cac")):
                    ack_lines = [self._cmd_ack(str(i["incident_id"]), principal) for i in open_incs[:10]]
                    return {
                        "reply": f"✅ Đã tiếp nhận/xác nhận {len(ack_lines)} sự cố đang mở:\n" + "\n".join(ack_lines),
                        "meta": {"v4_route": "v4", "v4_intent": "NL_INCIDENT_ACK"},
                    }
                if len(open_incs) > 1:
                    sample = "\n".join(
                        f"• {i['incident_id']} [{i['severity']}] ({i['status']}) {i['title']}"
                        for i in open_incs[:5]
                    )
                    return {
                        "reply": f"⚠️ Hiện có {len(open_incs)} sự cố đang mở. Vui lòng nêu rõ mã sự cố (`inc-...`):\n{sample}",
                        "meta": {"v4_route": "v4", "v4_intent": "NL_INCIDENT_PREREQUISITE"},
                    }
                target_inc_id = str(open_incs[0]["incident_id"])
            if is_ack_inc:
                return {"reply": self._cmd_ack(target_inc_id, principal), "meta": {"v4_route": "v4", "v4_intent": "NL_INCIDENT_ACK"}}
            note = self._extract_reason_from_decision_prose(clean, "APPROVED") or "Resolved via conversational AI assistant"
            try:
                upd = self.incident_engine.resolve_incident(
                    target_inc_id,
                    actor=principal.username,
                    role=principal.role,
                    note=note,
                )
                return {
                    "reply": f"✅ @{principal.username} đã đóng (RESOLVED) sự cố `{upd['incident_id']}` (Ghi chú: {note}).",
                    "meta": {"v4_route": "v4", "v4_intent": "NL_INCIDENT_RESOLVE"},
                }
            except Exception as exc:
                return {"reply": f"❌ Không thể đóng sự cố {target_inc_id}: {exc}", "meta": {"v4_route": "v4", "v4_intent": "NL_INCIDENT_ERROR"}}

        # E. Natural-language Watch / Reminder Cancel & List
        if re.search(
            r"\b(?:huy|dung|xoa|tat|bo)\s+(?:giup\s+|ho\s+)?(?:cac\s+|toan\s+bo\s+)?(?:canh\s+bao|theo\s+doi|tac\s+vu|watch|lich\s+nhac|nhac\s+viec)\b",
            norm,
        ):
            tid_m = re.search(r"\b(tsk4-[a-z0-9_-]+)\b", clean.lower())
            if tid_m:
                return {"reply": self._cmd_v4_cancel(tid_m.group(1), principal), "meta": {"v4_route": "v4", "v4_intent": "NL_TASK_CANCEL"}}
            if self.v4_facade is None:
                return {"reply": self._v4_unavailable(), "meta": {"v4_route": "v4", "v4_intent": "NL_TASK_CANCEL"}}
            tasks = self.v4_facade.tasks.list_tasks(
                owner_id=self._principal_owner_id(principal),
                is_admin=principal.is_admin,
            )
            active_tasks = [t for t in tasks if t.get("status") in {"ACTIVE", "WAITING_CONDITION"}]
            if not active_tasks:
                return {"reply": "ℹ️ Bạn hiện không có cảnh báo hay lịch nhắc nào đang hoạt động để hủy.", "meta": {"v4_route": "v4", "v4_intent": "NL_TASK_CANCEL"}}
            active_tasks.sort(key=lambda t: str(t.get("created_at") or ""), reverse=True)
            cancel_all = any(w in norm for w in ("tat ca", "toan bo", "het"))
            to_cancel = active_tasks if cancel_all else [active_tasks[0]]
            cancelled_lines = []
            for t in to_cancel:
                res = self.v4_facade.tasks.cancel_task(
                    t["task_id"],
                    actor_id=self._principal_owner_id(principal),
                    actor_role=principal.role,
                )
                cancelled_lines.append(f"• `{res['task_id']}` ({t.get('title')}) → {res['status']}")
            return {
                "reply": "✅ Đã hủy theo dõi / lịch nhắc:\n" + "\n".join(cancelled_lines),
                "meta": {"v4_route": "v4", "v4_intent": "NL_TASK_CANCEL"},
            }

        if re.search(
            r"\b(?:danh\s+sach|xem\s+(?:lai\s+)?(?:cac\s+)?|liet\s+ke)\s+(?:canh\s+bao|theo\s+doi|tac\s+vu|watch|lich\s+nhac|nhac\s+viec)\b"
            r"|\bdang\s+(?:dat|theo\s+doi|co)\s+(?:nhung\s+)?(?:canh\s+bao|lich\s+nhac|tac\s+vu)\b",
            norm,
        ):
            return {"reply": self._cmd_v4_tasks(principal), "meta": {"v4_route": "v4", "v4_intent": "NL_TASKS_LIST"}}

        # F. Session Memory & Personal Settings
        if re.search(r"\b(?:xoa|lam\s+sach|don\s+dep|quen)\s+(?:giup\s+)?(?:bo\s+nho|ngu\s+canh|doan\s+chat|cuoc\s+tro\s+chuyen)\b", norm):
            self._clear_decision_draft(principal, chat_id)
            return {"reply": self._cmd_v4_clear(principal, source=source, chat_id=chat_id), "meta": {"v4_route": "v4", "v4_intent": "NL_SESSION_CLEAR"}}

        if re.search(r"\b(?:tao|mo|bat\s+dau)\s+(?:giup\s+)?(?:phien|cuoc\s+tro\s+chuyen)\s+moi\b", norm):
            self._clear_decision_draft(principal, chat_id)
            return {"reply": self._cmd_v4_new(principal, source=source, chat_id=chat_id), "meta": {"v4_route": "v4", "v4_intent": "NL_SESSION_NEW"}}

        if re.search(r"\b(?:xem|kiem\s+tra)\s+(?:bo\s+nho\s+ngu\s+canh|ngu\s+canh\s+phien)\b", norm):
            return {"reply": self._cmd_v4_memory(principal, source=source, chat_id=chat_id), "meta": {"v4_route": "v4", "v4_intent": "NL_MEMORY_STATUS"}}

        if re.search(r"\b(?:che\s+do\s+tom\s+tat|cai\s+dat\s+ca\s+nhan|tuy\s+chon\s+ca\s+nhan)\b", norm):
            if any(w in norm for w in ("ngan gon", "rut gon", "compact")):
                return {"reply": self._cmd_v4_settings("digest_style=compact", principal), "meta": {"v4_route": "v4", "v4_intent": "NL_SETTINGS"}}
            if any(w in norm for w in ("chi tiet", "day du", "detailed")):
                return {"reply": self._cmd_v4_settings("digest_style=detailed", principal), "meta": {"v4_route": "v4", "v4_intent": "NL_SETTINGS"}}
            return {"reply": self._cmd_v4_settings("", principal), "meta": {"v4_route": "v4", "v4_intent": "NL_SETTINGS"}}

        # G. Focused Specialized Queries (Quota, Compare, GPU Top, Workspace, Repo Security)
        if re.search(r"\b(?:han\s+muc\s+tin\s+nhan|bao\s+nhieu\s+tin\s+nhan|quota\s+zalo|tin\s+nhan\s+zalo\s+con)\b", norm):
            return {"reply": self._cmd_quota(), "meta": {"v4_route": "v4", "v4_intent": "NL_QUOTA"}}

        if re.search(r"\bso\s+sanh\b", norm) and not has_at_mention:
            hid = "track" if ("rack" in norm or "track" in norm) else ("workstation" if ("workstation" in norm or "ws" in norm) else "")
            return {"reply": self._cmd_v4_compare(hid, principal, source=source), "meta": {"v4_route": "v4", "v4_intent": "NL_COMPARE"}}

        if re.search(r"\b(?:ai\s+dang\s+(?:dung|chiem|su\s+dung)\s+(?:nhieu\s+)?(?:gpu|vram)|phan\s+bo\s+(?:gpu|vram)|xep\s+hang\s+(?:gpu|vram))\b", norm):
            return {"reply": self._cmd_allocations(principal), "meta": {"v4_route": "v4", "v4_intent": "NL_GPU_ALLOC"}}

        if re.search(r"\b(?:workspace|container)\s+cua\s+toi\b", norm):
            return {"reply": self._cmd_myworkspace(principal), "meta": {"v4_route": "v4", "v4_intent": "NL_MYWORKSPACE"}}

        if re.search(r"\b(?:an\s+ninh\s+repo|bao\s+mat\s+(?:repo|kho\s+ma)|quet\s+trivy|su\s+co\s+repo)\b", norm):
            if not principal.is_admin:
                return {"forbidden": True, "reply": "⛔ Thông tin kiểm tra an ninh kho mã toàn lab chỉ dành cho Quản trị viên (Admin)."}
            return {"reply": self._cmd_security(), "meta": {"v4_route": "v4", "v4_intent": "NL_REPO_SECURITY"}}

        return None

    def _cmd_report(self, arg: str, principal: ZaloPrincipal) -> str:
        lower = arg.lower().strip()
        if lower.startswith("today") or "hôm nay" in lower or "daily" in lower:
            if not principal.is_admin:
                return "⛔ Báo cáo điều hành hằng ngày toàn hệ thống chỉ dành cho Quản trị viên."
            force_gen = any(w in lower for w in ("tạo", "new", "gen", "force"))
            rep = self.report_generator.generate_daily_executive_briefing(
                ai_client=self.ai_client,
                telemetry_collector=self.telemetry_collector,
                incident_engine=self.incident_engine,
                force=force_gen,
                author=f"Admin ({principal.username})",
            )
            return rep.get("zalo_summary") or rep.get("content", "")[:1500]

        if any(w in lower for w in ["tạo", "new", "gen", "generate"]):
            report = self.report_generator.generate_weekly_report(author="Zalo User Request")
            return f"✅ Đã tạo báo cáo mới: {report['title']}\n\n{report['content'][:600]}..."

        reports = self.report_generator.list_reports()
        if reports:
            latest = reports[0]
            return f"📑 {latest['title']}\n━━━━━━━━━━━━━━━━━━━━\n{latest['content'][:700]}..."
        report = self.report_generator.generate_weekly_report()
        return f"📑 {report['title']}\n━━━━━━━━━━━━━━━━━━━━\n{report['content'][:700]}..."

    def _cmd_quota(self) -> str:
        st = self.quota_ledger.get_status()
        tot_sent = f"{st['total_sent']:,}".replace(",", ".")
        tot_lim = f"{st['total_limit']:,}".replace(",", ".")
        std_rem = f"{st['standard_remaining']:,}".replace(",", ".")
        std_lim = f"{st['standard_limit']:,}".replace(",", ".")
        crit_rem = f"{st['critical_remaining']:,}".replace(",", ".")
        crit_res = f"{st['critical_reserve']:,}".replace(",", ".")
        warn = "\n⚠️ CẢNH BÁO: Hạn mức tin nhắn nội bộ đã vượt 85% hoặc quỹ khẩn cấp sắp hết!" if st.get("is_quota_warning") else ""

        return (
            "📈 HẠN MỨC TIN NHẮN NỘI BỘ ZALO & AI\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"• Tháng: {st['current_month']}\n"
            f"• Đã gửi: {tot_sent} / {tot_lim} tin ({st['percent_used']}%)\n"
            f"• Tin thông thường còn lại: {std_rem} / {std_lim}\n"
            f"• Tin dự phòng khẩn cấp: {crit_rem} / {crit_res}\n"
            f"• Lượt gọi AI 9Router: {st['ai_requests']} lượt\n"
            "• Lưu ý: Hạn mức do cấu hình nội bộ LabOps theo dõi, không phải xác nhận từ Zalo API."
            f"{warn}"
        )

    def _cmd_ask(
        self,
        question: str,
        principal: ZaloPrincipal,
        *,
        source: str = "zalo",
        chat_id: str = "",
    ) -> tuple[str, Dict[str, Any]]:
        if not question:
            return "Vui lòng nhập câu hỏi sau lệnh /ask. Ví dụ: `/ask Tại sao Workstation hôm nay chậm?`", {}

        if (
            self.v4_facade is not None
            and getattr(self.v4_facade.config, "enabled", False)
            and getattr(self.v4_facade.config, "natural_language", False)
        ):
            eff_chat = chat_id or principal.zalo_user_id or principal.username
            res = self.v4_facade.handle_natural_message(
                text=question,
                principal=principal,
                channel=source,
                chat_id=eff_chat,
            )
            if res.get("ai_used"):
                self.quota_ledger.record_ai_request()
            return str(res.get("reply") or ""), {
                "tool_traces": res.get("tool_traces", []),
                "evidence_timestamps": res.get("evidence_timestamps", []),
                "context_scope": res.get("context_scope"),
                "ai_used": bool(res.get("ai_used", False)),
                "correlation_id": res.get("correlation_id"),
                "v4_route": res.get("route"),
                "v4_intent": res.get("intent"),
                "v4_session_id": res.get("session_id"),
            }

        self.quota_ledger.record_ai_request()
        inv = self.agent_runner.run_investigation(
            question,
            principal,
            ai_client=self.ai_client,
            source=source,
        )
        return f"🤖 Trợ lý AI SRE v2:\n{inv['reply']}", {
            "tool_traces": inv.get("tool_traces", []),
            "evidence_timestamps": inv.get("evidence_timestamps", []),
            "context_scope": inv.get("context_scope"),
            "ai_used": inv.get("ai_used", False),
            "correlation_id": inv.get("correlation_id"),
        }

    # ---------------- Additive V4 Commands ----------------

    def _v4_unavailable(self) -> str:
        return "ℹ️ Module Personal AI V4 chưa được khởi tạo trên worker này."

    @staticmethod
    def _principal_owner_id(principal: ZaloPrincipal) -> str:
        return principal.username or principal.zalo_user_id

    def _cmd_v4_new(self, principal: ZaloPrincipal, *, source: str, chat_id: str) -> str:
        if self.v4_facade is None:
            return self._v4_unavailable()
        sess = self.v4_facade.memory.start_new_session(
            principal_id=self._principal_owner_id(principal),
            channel=source,
            chat_id=chat_id,
        )
        return f"🆕 Đã tạo phiên hội thoại mới `{sess['session_id']}` (cô lập theo kênh `{source}`)."

    def _cmd_v4_clear(self, principal: ZaloPrincipal, *, source: str, chat_id: str) -> str:
        if self.v4_facade is None:
            return self._v4_unavailable()
        res = self.v4_facade.memory.clear_active_context(
            principal_id=self._principal_owner_id(principal),
            channel=source,
            chat_id=chat_id,
        )
        return f"🧹 Đã xóa {res.get('cleared_messages', 0)} tin nhắn ngữ cảnh chat hiện tại (lịch sử audit vẫn được bảo toàn)."

    def _cmd_v4_memory(self, principal: ZaloPrincipal, *, source: str, chat_id: str) -> str:
        if self.v4_facade is None:
            return self._v4_unavailable()
        ov = self.v4_facade.memory.get_memory_overview(
            principal_id=self._principal_owner_id(principal),
            channel=source,
            chat_id=chat_id,
        )
        sessions = ov.get("active_sessions") or []
        prefs = (ov.get("preferences") or {}).get("opted_in_preferences") or {}
        lines = [f"🧠 BỘ NHỚ NGỮ CẢNH ({self._principal_owner_id(principal)}):"]
        lines.append(f"• Phiên hoạt động ({source}): {len(sessions)}")
        for s in sessions[:3]:
            lines.append(f"  - `{s['session_id']}` (tin: {s.get('sequence_no', 0)}, cập nhật: {_fmt_vn_time(s.get('last_active_at'))})")
        lines.append(f"• Tùy chọn cá nhân đã bật ({len(prefs)}): {', '.join(prefs.keys()) if prefs else 'chưa có'}")
        lines.append("• Dùng `/clear` để làm sạch ngữ cảnh chat hoặc `/new` để mở phiên mới.")
        return "\n".join(lines)

    def _cmd_v4_tasks(self, principal: ZaloPrincipal) -> str:
        if self.v4_facade is None:
            return self._v4_unavailable()
        tasks = self.v4_facade.tasks.list_tasks(
            owner_id=self._principal_owner_id(principal),
            is_admin=principal.is_admin,
        )
        if not tasks:
            return "📋 Bạn chưa có tác vụ hoặc watch rule nào đang lưu."
        lines = [f"📋 DANH SÁCH TÁC VỤ & WATCH ({len(tasks)}):"]
        for t in tasks[:15]:
            lines.append(f"• `{t['task_id']}` [{t['status']}] {t['title']}")
        lines.append("• Dùng `/cancel <task_id>` để hủy tác vụ của bạn.")
        return "\n".join(lines)

    def _cmd_v4_watch(self, arg: str, principal: ZaloPrincipal) -> str:
        if self.v4_facade is None:
            return self._v4_unavailable()
        if not getattr(self.v4_facade.config, "task_manager", False):
            return "⏸️ Tính năng theo dõi chủ động đang tắt (`assistant_v4.task_manager = false`)."
        parts = arg.split()
        if len(parts) < 4:
            return (
                "Cú pháp: `/watch <target_id> <metric> <lt|lte|gt|gte> <threshold> [sustained_seconds]`\n"
                "Ví dụ: `/watch workstation gpu_temperature_c lt 75 300`"
            )
        target_id, metric, comp, thresh_raw = parts[:4]
        hold_s = int(parts[4]) if len(parts) >= 5 and parts[4].isdigit() else 300
        try:
            from app.services.assistant_v4.task_scheduler import WatchSpec

            spec = WatchSpec(
                target_id=target_id,
                metric=metric,
                comparator=comp,
                threshold=float(thresh_raw),
                sustained_seconds=hold_s,
            )
            created = self.v4_facade.tasks.create_watch(
                owner_id=self._principal_owner_id(principal),
                owner_role=principal.role,
                spec=spec,
            )
            return (
                f"✅ Đã tạo Watch `{created['task_id']}`:\n"
                f"• {spec.target_id}: `{spec.metric} {spec.comparator} {spec.threshold}` (duy trì {spec.sustained_seconds}s)\n"
                f"• Hủy bằng: `/cancel {created['task_id']}`"
            )
        except Exception as exc:
            return f"⛔ Không thể tạo watch: {exc}"

    def _cmd_v4_cancel(self, arg: str, principal: ZaloPrincipal) -> str:
        if self.v4_facade is None:
            return self._v4_unavailable()
        tid = arg.strip().split()[0] if arg.strip() else ""
        if not tid:
            return "Vui lòng nhập mã tác vụ. Ví dụ: `/cancel tsk4-xxxxxx`"
        try:
            res = self.v4_facade.tasks.cancel_task(
                tid,
                actor_id=self._principal_owner_id(principal),
                actor_role=principal.role,
            )
            return f"✅ Đã hủy tác vụ `{res['task_id']}` (trạng thái: {res['status']})."
        except Exception as exc:
            return f"⛔ Không thể hủy `{tid}`: {exc}"

    def _cmd_v4_settings(self, arg: str, principal: ZaloPrincipal) -> str:
        if self.v4_facade is None:
            return self._v4_unavailable()
        pid = self._principal_owner_id(principal)
        if not arg.strip():
            prefs = self.v4_facade.memory.get_preferences(pid)
            return (
                f"⚙️ TÙY CHỌN CÁ NHÂN ({pid}):\n"
                f"• Múi giờ: {prefs.get('timezone', 'Asia/Ho_Chi_Minh')}\n"
                f"• Opt-in preferences: {prefs.get('opted_in_preferences') or {}}\n"
                "• Cập nhật: `/settings <key>=<value>`"
            )
        if "=" not in arg:
            return "Cú pháp cập nhật: `/settings <key>=<value>` (ví dụ `/settings digest_style=compact`)."
        k, v = [p.strip() for p in arg.split("=", 1)]
        updated = self.v4_facade.memory.set_preference(pid, k, v)
        return f"✅ Đã lưu tùy chọn `{k}` cho @{pid} (phiên bản v{updated.get('version', 1)})."

    def _cmd_v4_compare(self, arg: str, principal: ZaloPrincipal, *, source: str) -> str:
        if self.v4_facade is None:
            return self._v4_unavailable()
        host_arg = arg.strip().split()[0] if arg.strip() else None
        out = self.v4_facade.tools.execute_tool(
            "compare_resource_metrics",
            {"host_id": host_arg} if host_arg else {},
            principal,
            source=source,
        )
        status = out.get("status")
        evd = out.get("evidence_ref")
        obs = _fmt_vn_time(out.get("observed_at"))
        data = out.get("data") or {}
        if status == "ERROR":
            return f"⛔ Không thể so sánh chỉ số ({out.get('error_code') or 'ERR'}, nguồn: {evd})."

        snap = data.get("current_snapshot") if status == "INSUFFICIENT_HISTORY" else {}
        hosts = data.get("current_hosts") or (snap or {}).get("hosts") or []
        gpus = data.get("current_gpus") or (snap or {}).get("gpus") or []
        hist = data.get("history_kpis") or {}

        lines = [f"📊 SO SÁNH CHỈ SỐ HẠ TẦNG ({status}, nguồn: {evd}, lúc {obs}):"]
        if status == "INSUFFICIENT_HISTORY":
            lines.append(f"⚠️ {data.get('message') or 'Chưa đủ mẫu lịch sử để so sánh với kỳ trước.'}")
        elif hist:
            cpu_avg = hist.get("cpu_avg_percent")
            prev_cpu = hist.get("prev_cpu_avg_percent")
            cpu_cmp = f" | Kỳ trước: {prev_cpu}%" if prev_cpu is not None else " | Kỳ trước: chưa đủ mẫu"
            lines.append(
                f"• Lịch sử {hist.get('window_hours', 24)}h ({hist.get('sample_count', 0)} mẫu): "
                f"Khả dụng {hist.get('availability_percent', 'N/A')}% | CPU TB: {cpu_avg if cpu_avg is not None else 'N/A'}% "
                f"(Đỉnh: {hist.get('cpu_peak_percent', 'N/A')}%){cpu_cmp}"
            )
            lines.append(
                f"• GPU lịch sử: Tải TB {hist.get('gpu_util_avg_percent', 'N/A')}% | VRAM đỉnh {hist.get('gpu_vram_peak_mb', 'N/A')} MiB"
            )
            if hist.get("data_limit_note"):
                lines.append(f"• Lưu ý: {hist['data_limit_note']}")

        if hosts:
            lines.append("🖥️ HIỆN TẠI THEO MÁY CHỦ:")
            for h in hosts:
                st = h.get("status", "UNKNOWN")
                if st in {"ONLINE", "DEGRADED"}:
                    cpu = f"{h.get('cpu_usage_percent')}%" if h.get("cpu_usage_percent") is not None else "N/A"
                    ram = f"{h.get('ram_used_gb')}/{h.get('ram_total_gb')} GiB" if h.get("ram_total_gb") else "N/A"
                    lines.append(f"  - {h.get('display_name') or h.get('hostname')} ({st}): CPU {cpu} | RAM {ram}")
                else:
                    lines.append(f"  - {h.get('display_name') or h.get('hostname')}: {st} ({h.get('freshness_status')})")
        if gpus:
            lines.append("🎮 HIỆN TẠI THEO GPU:")
            for g in gpus:
                if g.get("host_online"):
                    lines.append(
                        f"  - {g.get('model')} ({g.get('host_id')}): Tải {g.get('utilization_gpu')}% | "
                        f"Nhiệt {g.get('temp_celsius')}°C | VRAM {g.get('vram_used_mb')}/{g.get('vram_total_mb')} MiB"
                    )
                else:
                    lines.append(f"  - {g.get('model')} ({g.get('host_id')}): Máy chủ ngoại tuyến")
        return "\n".join(lines)

    def _cmd_v4_who(self, arg: str, principal: ZaloPrincipal, *, source: str) -> str:
        if self.v4_facade is None:
            return self._v4_unavailable()
        uname = arg.strip().split()[0] if arg.strip() else None
        out = self.v4_facade.tools.execute_tool(
            "get_member_info",
            {"username": uname} if uname else {},
            principal,
            source=source,
        )
        evd = out.get("evidence_ref")
        if out.get("status") != "OK":
            target_lbl = uname or principal.username
            return f"⛔ Không tìm thấy hồ sơ thành viên `{target_lbl}` ({out.get('error_code') or 'ERR'}, nguồn: {evd})."
        m = (out.get("data") or {}).get("member") or {}
        gpus = ", ".join(str(x) for x in (m.get("gpu_permissions") or [])) or "Không giới hạn/Chưa cấp"
        ssh_p = m.get("ssh_port") if m.get("ssh_port") is not None else "N/A"
        cpu_l = f"{m['cpu_limit_cores']} cores" if m.get("cpu_limit_cores") is not None else "Không giới hạn"
        ram_l = f"{m['ram_limit_gb']} GiB" if m.get("ram_limit_gb") is not None else "Không giới hạn"
        disk_l = f"{m['storage_quota_gb']} GiB" if m.get("storage_quota_gb") is not None else "Không giới hạn"
        return "\n".join(
            [
                f"👤 HỒ SƠ THÀNH VIÊN @{m.get('username')} (nguồn: {evd}):",
                f"• Họ tên: {m.get('full_name') or 'N/A'} | Vai trò: {m.get('role') or 'N/A'} | Trạng thái: {m.get('status') or 'N/A'}",
                f"• Máy chủ: {m.get('assigned_server') or 'N/A'} | Workspace: {m.get('workspace_name') or 'N/A'} | SSH Port: {ssh_p}",
                f"• Định mức: CPU {cpu_l} | RAM {ram_l} | Ổ đĩa {disk_l}",
                f"• Quyền GPU: {gpus}",
            ]
        )

    def _cmd_v4_confirm(self, arg: str, principal: ZaloPrincipal, *, chat_id: str) -> str:
        if self.v4_facade is None:
            return self._v4_unavailable()
        parts = arg.strip().split()
        if len(parts) != 2:
            return "Cú pháp bắt buộc: `/confirm <action_id> <one_time_code>`"
        action_id, code = parts
        try:
            res = self.v4_facade.approvals.confirm_zalo_action(
                action_id=action_id,
                code=code,
                principal=principal,
                chat_id=chat_id,
            )
            return f"✅ Đã xác nhận `{res['action_id']}` -> trạng thái `{res['state']}`."
        except Exception as exc:
            return f"⛔ Xác nhận thất bại: {exc}"

    def get_consumer_status(self) -> Dict[str, Any]:
        return {
            "consumer_id": self.consumer_id,
            "is_polling": self.is_polling,
            "has_token": bool(self.bot_token),
            "token_masked": f"{self.bot_token[:6]}...{self.bot_token[-4:]}" if len(self.bot_token) > 10 else "(Chưa cấu hình)",
            "last_heartbeat": self.last_polling_heartbeat,
            "authorized_users_count": len(self.authorized_users or []),
            "enabled_read_tools": self.ENABLED_READ_TOOLS,
            "disabled_write_tools": self.DISABLED_WRITE_TOOLS,
        }
