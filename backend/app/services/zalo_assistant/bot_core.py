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
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
        "/quota": "Kiểm tra hạn mức tin nhắn nội bộ Zalo & AI requests",
        "/link": "Liên kết tài khoản Zalo với LabOps (/link <MÃ>)",
        "/ask": "Điều tra hạ tầng bằng AI Agent (/ask <câu hỏi>)",
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
            "/quota",
        }
        is_ask = cmd == "/ask" or cmd not in known_cmds
        if is_ask and not self._check_ask_rate_limit(sender_id):
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
        elif cmd == "/ask":
            reply, agent_meta = self._cmd_ask(arg, principal, source=src)
        else:
            reply, agent_meta = self._cmd_ask(text, principal, source=src)

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
        return (
            f"🚀 Chào mừng @{principal.username} ({principal.role}) đến với LabOps AI SRE Agent v2!\n\n"
            "Tôi hỗ trợ giám sát máy chủ, GPU, sự cố, báo cáo ngày/tuần và phân tích điều tra bằng AI Agent.\n"
            "Gõ `/help` để xem danh sách lệnh theo phân quyền của bạn."
        )

    def _cmd_help(self, principal: ZaloPrincipal) -> str:
        lines = [f"📖 DANH SÁCH LỆNH LABOPS AI SRE v2 ({principal.role}):"]
        for c, desc in self.COMMANDS.items():
            if c == "/security" and not principal.is_admin:
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
            f"• Sức khỏe cụm: {snap.get('overall_health', 'UNKNOWN')} | Timestamp: {snap.get('collected_at', 'N/A')}",
            "🖥️ MÁY CHỦ:",
        ]
        if not hosts:
            lines.append("• Chưa có dữ liệu máy chủ (UNKNOWN)")
        else:
            for h in hosts:
                st = h.get("status", "UNKNOWN")
                fresh = h.get("freshness_status", "UNKNOWN")
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
                src = h.get("source", "unknown")
                if st in {"ONLINE", "DEGRADED"}:
                    lines.append(
                        f"• {h.get('display_name')} ({h.get('hostname')}): {st} [{fresh}, src={src}]\n"
                        f"  CPU {cpu} | RAM {ram} | Disk {disk}"
                    )
                else:
                    lines.append(
                        f"• {h.get('display_name')} ({h.get('hostname')}): {st} [fresh={fresh}, last_hb={h.get('last_heartbeat_at') or 'N/A'}]"
                    )

        lines.append("🎮 GPU TELEMETRY:")
        if not gpus:
            lines.append("• Chưa ghi nhận GPU khả dụng.")
        else:
            for g in gpus:
                if g.get("host_online"):
                    lines.append(
                        f"• {g['gpu_key']} ({g['model']}): "
                        f"{int(g.get('vram_used_mb') or 0):,}/{int(g.get('vram_total_mb') or 0):,} MiB "
                        f"({g.get('utilization_gpu')}% util, {g.get('temp_celsius')}°C)"
                    )
                else:
                    lines.append(f"• {g['gpu_key']} ({g['model']}): HOST KHÔNG KHẢ DỤNG")

        lines.append(f"• AI Gateway: {r_health.get('status', 'UNKNOWN')}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🔔 SỰ CỐ ĐANG MỞ ({len(open_incs)}):")
        if open_incs:
            for inc in open_incs[:3]:
                lines.append(f"• [{inc['severity']}] {inc['incident_id']}: {inc['title']}")
        else:
            lines.append("• Không có sự cố đang mở được ghi nhận.")
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

    def _cmd_allocations(self, principal: ZaloPrincipal) -> str:
        res = self.tool_registry.execute_tool("get_gpu_metrics", {}, principal, source="zalo")
        gpus = res.get("gpus") or []
        lines = ["📋 PHÂN BỔ GPU THEO QUYỀN TRUY CẬP:", "━━━━━━━━━━━━━━━━━━━━"]
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
                f"• `{inc['incident_id']}` [{inc['severity']}/{inc['confidence']}] ({inc['status']}) `{inc.get('resource_id')}`: {inc['title']}"
            )
        lines.append("\nDùng `/incident <id>` để xem bằng chứng hoặc `/ack <id>` để xác nhận.")
        return "\n".join(lines)

    def _cmd_incident_detail(self, arg: str, principal: ZaloPrincipal) -> str:
        inc_id = str(arg or "").strip()
        if not inc_id:
            return "Vui lòng nhập mã sự cố. Ví dụ: `/incident inc-abc123`"
        inc = self.incident_engine.get_incident(inc_id)
        if inc is None:
            return f"❌ Không tìm thấy sự cố `{inc_id}`."
        if not principal.is_admin and inc.get("owner_id") != principal.username:
            return "⛔ Bạn không có quyền xem sự cố của thành viên khác."
        return (
            f"📌 CHI TIẾT SỰ CỐ `{inc['incident_id']}`\n"
            f"• Tiêu đề: {inc['title']}\n"
            f"• Mức độ: {inc['severity']} ({inc['confidence']}) | Trạng thái: {inc['status']}\n"
            f"• Nguồn: {inc['source']} | Tài nguyên: {inc['resource_id']} | Chủ sở hữu: {inc.get('owner_id') or 'N/A'}\n"
            f"• Phát hiện: {inc['detected_at']} | Lần cuối: {inc['last_observed_at']} (Lặp: {inc['occurrence_count']})\n"
            f"• Khuyến nghị: {inc.get('recommended_action') or 'N/A'}"
        )

    def _cmd_ack(self, arg: str, principal: ZaloPrincipal) -> str:
        inc_id = str(arg or "").strip()
        if not inc_id:
            return "Vui lòng nhập mã sự cố cần xác nhận. Ví dụ: `/ack inc-abc123`"
        try:
            updated = self.incident_engine.acknowledge_incident(
                inc_id, actor=principal.username, role=principal.role, note="Acknowledged via Zalo Bot"
            )
            return f"✅ @{principal.username} đã xác nhận (ACKNOWLEDGED) sự cố `{updated['incident_id']}`."
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
            lines.append(f"• `{i['incident_id']}` [{i['severity']}] {i['title']}")
        if not open_sec:
            lines.append("• Hiện không có sự cố Repository Guard đang mở.")
        lines.append(f"\nSự kiện Audit gần đây: {len(res.get('audit_events') or [])} mục.")
        return "\n".join(lines)

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

    def _cmd_ask(self, question: str, principal: ZaloPrincipal, *, source: str = "zalo") -> tuple[str, Dict[str, Any]]:
        if not question:
            return "Vui lòng nhập câu hỏi sau lệnh /ask. Ví dụ: `/ask Tại sao Workstation hôm nay chậm?`", {}

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
