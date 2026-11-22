"""
Zalo Bot Core Engine
Handles 7 primary commands:
/start, /help, /ask, /status, /jobs, /report, /quota (plus /gpu alias).
Consolidates metrics into single responses, enforces strict quota ledger,
and provides single-consumer polling lease protection.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
from app.services.zalo_assistant.quota_ledger import QuotaLedger, QuotaExceededError
from app.services.zalo_assistant.report_generator import ReportGenerator

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ZaloBotEngine:
    COMMANDS = {
        "/start": "Khởi động và giới thiệu trợ lý LabOps",
        "/help": "Xem danh sách các lệnh hỗ trợ",
        "/ask": "Hỏi đáp thông minh với AI (/ask <câu hỏi>)",
        "/status": "Tổng hợp trạng thái toàn bộ máy chủ, CPU, RAM, GPU, Ổ cứng và Cảnh báo",
        "/jobs": "Danh sách các tác vụ tính toán và tiến trình GPU đang chạy",
        "/report": "Xem hoặc tạo báo cáo tuần tổng hợp hoạt động của Lab",
        "/quota": "Kiểm tra hạn mức tin nhắn Zalo Basic (3.000 tin/tháng) và AI requests",
        "/alloc": "Bảng thống kê tài khoản nào đang được cấp cho GPU nào (/allocations)",
        "/gpu": "Truy vấn nhanh thông tin GPU (Alias của /status)",
    }

    # Chỉ bật các công cụ ĐỌC (Read-only tools). Khóa toàn bộ công cụ ghi/phá hủy.
    ENABLED_READ_TOOLS = [
        "inspect_host_status",
        "inspect_gpu_telemetry",
        "list_running_jobs",
        "generate_weekly_report",
        "check_message_quota",
        "query_ai_knowledge",
    ]
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
    ):
        self.quota_ledger = quota_ledger
        self.ai_client = ai_client
        self.report_generator = report_generator
        self.bot_token = bot_token if bot_token is not None else os.getenv("ZALO_BOT_TOKEN", "")
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
        """Tự động nạp lại cấu hình allowlist/AI từ zalo_config.json khi file thay đổi."""
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
        """Lưu allowlist và tùy chọn AI ra zalo_config.json (KHÔNG lưu bot_token)."""
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

    def is_authorized(self, user_id: str) -> bool:
        self.reload_persisted_config()
        uid = str(user_id or "").strip()
        if not uid or uid == "*":
            return False
        allowed = {
            str(u).strip()
            for u in (self.authorized_users or [])
            if str(u).strip() and str(u).strip() != "*"
        }
        return uid in allowed

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
    ) -> Dict[str, Any]:
        """
        Process user message or command with RBAC, rate limiting, and quota reservation.
        """
        text = command_text.strip()
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        src = (source or "zalo").strip().lower()
        role_upper = str(principal_role or "").strip().upper()

        if src == "dashboard":
            if role_upper not in ("ADMIN", "RESEARCHER"):
                return {
                    "success": False,
                    "error": "UNAUTHORIZED",
                    "suppress_outbound": True,
                    "reply": "⛔ Bạn không có quyền truy cập LabOps AI Assistant.",
                    "command": cmd,
                }
        else:
            if not self.is_authorized(sender_id) or (
                chat_id is not None and not self.is_chat_allowed(chat_id, sender_id=sender_id)
            ):
                return {
                    "success": False,
                    "error": "UNAUTHORIZED",
                    "suppress_outbound": True,
                    "reply": "⛔ Bạn không có quyền truy cập LabOps AI Assistant. Vui lòng liên hệ quản trị viên.",
                    "command": cmd,
                }

        known_cmds = {"/start", "/help", "/status", "/gpu", "/alloc", "/allocations", "/gpus", "/jobs", "/report", "/quota"}
        is_ask = cmd == "/ask" or cmd not in known_cmds
        if is_ask and not self._check_ask_rate_limit(sender_id):
            return {
                "success": False,
                "error": "RATE_LIMITED",
                "suppress_outbound": True,
                "reply": "⏳ Bạn đã gửi quá nhiều yêu cầu AI /ask. Vui lòng thử lại sau 1 phút.",
                "command": cmd or "/ask",
            }

        # 1. Routing commands
        if cmd == "/start":
            reply = self._cmd_start()
        elif cmd == "/help":
            reply = self._cmd_help()
        elif cmd in ("/status", "/gpu"):
            reply = self._cmd_status()
        elif cmd in ("/alloc", "/allocations", "/gpus"):
            reply = self._cmd_allocations()
        elif cmd == "/jobs":
            reply = self._cmd_jobs()
        elif cmd == "/report":
            wants_generate = any(w in arg.lower() for w in ["tạo", "new", "gen", "generate"])
            if wants_generate and src == "dashboard" and role_upper != "ADMIN":
                return {
                    "success": False,
                    "error": "FORBIDDEN",
                    "suppress_outbound": True,
                    "reply": "⛔ Chỉ quản trị viên mới được phép khởi tạo báo cáo tuần mới.",
                    "command": cmd,
                }
            reply = self._cmd_report(arg)
        elif cmd == "/quota":
            reply = self._cmd_quota()
        elif cmd == "/ask":
            reply = self._cmd_ask(arg)
        else:
            # Nếu người dùng gửi tin nhắn không có prefix lệnh, chuyển tiếp tới AI Ask
            reply = self._cmd_ask(text)

        # 2. Record in Quota Ledger ONLY for external Zalo messages (not internal dashboard API)
        if src == "dashboard":
            msg_id = f"dash-{os.urandom(6).hex()}"
        else:
            try:
                if defer_outbox_commit:
                    success, msg_id, quota_snapshot = self.quota_ledger.reserve_outbox(
                        recipient_id=sender_id,
                        message_text=reply,
                        is_critical_alert=False,
                        idempotency_key=idempotency_key,
                        metadata={"command": cmd or "/ask", "sender": sender_id, "chat_id": chat_id or ""},
                    )
                else:
                    success, msg_id, quota_snapshot = self.quota_ledger.reserve_and_send(
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

        # Save into in-memory recent message history
        history_item = {
            "id": msg_id,
            "sender_id": sender_id,
            "source": src,
            "incoming_text": command_text,
            "reply_snippet": reply[:100],
            "command": cmd or "/ask",
            "timestamp": _now(),
        }
        self.message_history.append(history_item)
        if len(self.message_history) > 100:
            self.message_history = self.message_history[-100:]

        return {
            "success": True,
            "message_id": msg_id,
            "command": cmd or "/ask",
            "reply": reply,
            "quota_status": self.quota_ledger.get_status(),
        }

    # ---------------- Command Handlers ----------------

    def _cmd_start(self) -> str:
        return (
            "🚀 Chào mừng bạn đến với LabOps AI Assistant!\n\n"
            "Tôi là trợ lý vận hành hạ tầng AIoT Lab. Các chức năng khả dụng:\n"
            "• /status: Kiểm tra trạng thái máy chủ & GPU A30\n"
            "• /jobs: Danh sách tác vụ đang chạy\n"
            "• /report: Báo cáo tuần tổng hợp\n"
            "• /quota: Kiểm tra hạn mức tin nhắn Zalo\n"
            "• /ask <nội dung>: Hỏi đáp kỹ thuật trực tiếp\n"
            "• /help: Hướng dẫn chi tiết"
        )

    def _cmd_help(self) -> str:
        lines = ["📖 DANH SÁCH LỆNH LABOPS AI ASSISTANT:"]
        for c, desc in self.COMMANDS.items():
            lines.append(f"• {c}: {desc}")
        lines.append("\nWorkstation `aiot-ws1` là máy chủ điều hành chính (Primary).")
        return "\n".join(lines)

    def _cmd_status(self) -> str:
        """Consolidated /status: Rack & WS, CPU, RAM, GPU A30, Disk, Services, Alerts."""
        # 1. Host CPU & Memory
        cpu_pct = 0.0
        ram_used = 0.0
        ram_total = 0.0
        try:
            import psutil
            cpu_pct = round(psutil.cpu_percent(interval=0.1), 1)
            vm = psutil.virtual_memory()
            ram_used = round(vm.used / (1024**3), 1)
            ram_total = round(vm.total / (1024**3), 1)
        except Exception:
            pass

        # 2. GPU Telemetry
        gpu_info = "NVIDIA A30 24GB: Đang kiểm tra..."
        vram_pct = 0
        temp_c = 0
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                parts = [p.strip() for p in res.stdout.strip().split(",")]
                if len(parts) >= 5:
                    used_mb = int(float(parts[1]))
                    total_mb = int(float(parts[2]))
                    util_pct = int(float(parts[3]))
                    vram_pct = round((used_mb / total_mb) * 100) if total_mb > 0 else 0
                    temp_c = round(float(parts[4]))
                    gpu_info = f"{parts[0]}: {used_mb:,} / {total_mb:,} MiB ({util_pct}% util, {temp_c}°C)"
        except Exception:
            pass

        # 3. Disk Space (Tất cả 4 ổ đĩa vật lý)
        disk_info = "Khả dụng"
        try:
            import psutil
            total_disk_bytes = 0
            free_disk_bytes = 0
            seen_devs = set()
            disk_breakdown = []
            parts = psutil.disk_partitions(all=False)
            for p in parts:
                dev = p.device
                mnt = p.mountpoint
                fstype = p.fstype
                if not dev or not mnt or not fstype:
                    continue
                if dev.startswith("/dev/loop") or fstype in ("vfat", "iso9660", "squashfs", "tmpfs", "devtmpfs", "swap"):
                    continue
                if not os.path.exists(mnt) or dev in seen_devs:
                    continue
                seen_devs.add(dev)
                try:
                    u = psutil.disk_usage(mnt)
                    total_disk_bytes += u.total
                    free_disk_bytes += u.free
                    if "HDD3" in mnt or os.path.basename(dev) == "sdc":
                        lbl = "/mnt/HDD3 (AI Data)"
                    elif mnt == "/":
                        lbl = "/ (Root SSD)"
                    else:
                        lbl = os.path.basename(dev).upper()
                    disk_breakdown.append(f"  • {lbl}: {round(u.free / (1024**3), 1)} GiB trống / {round(u.total / (1024**3), 1)} GiB")
                except Exception:
                    continue
            total_tb = round(total_disk_bytes / (1024**4), 2)
            free_tb = round(free_disk_bytes / (1024**4), 2)
            disk_info = f"{free_tb} TiB trống / {total_tb} TiB ({len(disk_breakdown)} ổ đĩa):\n" + "\n".join(disk_breakdown)
        except Exception:
            try:
                total, used, free = shutil.disk_usage("/")
                disk_info = f"{round(free / (1024**3), 1)} GiB khả dụng / {round(total / (1024**3), 1)} GiB"
            except Exception:
                pass

        # 4. AI Gateway status
        r_health = self.ai_client.check_health()
        r_str = f"{r_health['status']}"

        # 5. Cảnh báo động theo chỉ số thời gian thực
        alerts = [
            "• Server Rack (aiotlab-rack): Ngoại tuyến; Workstation là Primary Control Plane."
        ]
        if vram_pct >= 90:
            alerts.append(f"• VRAM GPU A30 đang tải cao (~{vram_pct}% dung lượng).")
        if temp_c >= 85:
            alerts.append(f"• Nhiệt độ GPU A30 ở mức cao ({temp_c}°C).")
        if len(alerts) == 1:
            alerts.append("• Tài nguyên phần cứng và GPU hoạt động bình thường, an toàn.")

        alert_text = "\n".join(alerts)

        return (
            "📊 TỔNG HỢP TRẠNG THÁI LABOPS\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🖥️ HẠ TẦNG:\n"
            "• Workstation (aiot-ws1): ONLINE [PRIMARY]\n"
            "• Server Rack (aiotlab-rack): OFFLINE (Mất kết nối)\n"
            f"• CPU / RAM: {cpu_pct}% | {ram_used}/{ram_total} GiB\n"
            f"• GPU: {gpu_info}\n"
            f"• Ổ cứng (Toàn bộ):\n{disk_info}\n"
            f"• AI Gateway: {r_str}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔔 CẢNH BÁO:\n"
            f"{alert_text}"
        )

    def _cmd_jobs(self) -> str:
        summary = self.report_generator._get_active_jobs_summary()
        count = summary["running_count"]
        lines = [f"⚙️ TÁC VỤ NGHIÊN CỨU ĐANG CHẠY ({count} tác vụ):", "━━━━━━━━━━━━━━━━━━━━"]
        if summary["jobs"]:
            for idx, j in enumerate(summary["jobs"], 1):
                lines.append(f"{idx}. {j['display']} (PID: {j['pid']})")
        else:
            lines.append("• Hiện không có tác vụ tính toán của người dùng.")
        lines.append("━━━━━━━━━━━━━━━━━━━━\nGợi ý: Sử dụng `/status` để xem tổng quan máy chủ.")
        return "\n".join(lines)

    def _cmd_allocations(self) -> str:
        """Thống kê chi tiết tài khoản nào đang được cấp quyền theo từng thiết bị GPU."""
        try:
            from app.services.labops_service import service
            groups = service.get_gpu_allocations_by_device()
            lines = [
                "📋 THỐNG KÊ TÀI KHOẢN THEO TỪNG GPU:",
                "━━━━━━━━━━━━━━━━━━━━"
            ]
            if groups:
                for g in groups:
                    if g.get("is_online"):
                        header_status = f"ONLINE | {g.get('vram_used_gb', 0)}/{g.get('vram_total_gb', 24.0)} GB VRAM"
                    else:
                        header_status = "HOST NGOẠI TUYẾN"
                    lines.append(f"\n🖥️ {g.get('gpu_label')} • {g.get('gpu_model')} ({header_status})")
                    accounts = g.get("accounts") or []
                    if not accounts:
                        lines.append("  • Chưa cấp phát tài khoản")
                        continue
                    lines.append(f"  Có {len(accounts)} tài khoản được cấp:")
                    for a in accounts:
                        st = a.get("status", "IDLE")
                        icon = (
                            "🟢" if st == "IN_USE"
                            else "🟣" if st == "RESERVED"
                            else "🔵" if st == "IDLE"
                            else "⚪"
                        )
                        vram_gb = a.get("vram_used_gb", 0)
                        if st == "IN_USE":
                            detail = f"Đang dùng {vram_gb}GB [{a.get('primary_script')}]"
                        elif st == "RESERVED":
                            detail = f"Đang giữ chỗ [{a.get('primary_script')}]"
                        elif st == "OFFLINE_HOST":
                            detail = a.get("status_label") or "Máy chủ Ngoại tuyến"
                        else:
                            detail = f"{a.get('status_label') or 'Sẵn sàng'} (0GB)"
                        lines.append(
                            f"  • {icon} @{a.get('username')} ({a.get('full_name')}) — "
                            f"{a.get('allocation_mode')} | Quota {a.get('vram_quota_gb')}GB | {detail}"
                        )
            else:
                lines.append("• Chưa có thiết bị GPU nào được cấu hình.")
            lines.append("\n━━━━━━━━━━━━━━━━━━━━\nGợi ý: Dùng `/jobs` để xem chi tiết tiến trình.")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Lỗi truy vấn bảng phân bổ GPU: {e}"

    def _cmd_report(self, arg: str) -> str:
        # Nếu có chữ "tạo" hoặc "generate" thì tạo mới, ngược lại lấy báo cáo gần nhất
        if any(w in arg.lower() for w in ["tạo", "new", "gen", "generate"]):
            report = self.report_generator.generate_weekly_report(author="Zalo User Request")
            return f"✅ Đã tạo báo cáo mới: {report['title']}\n\n{report['content'][:600]}...\n\n(Tải báo cáo đầy đủ tại Dashboard)."

        reports = self.report_generator.list_reports()
        if reports:
            latest = reports[0]
            snippet = latest["content"][:700]
            return f"📑 {latest['title']}\n━━━━━━━━━━━━━━━━━━━━\n{snippet}...\n\n(Xem toàn bộ tại /dashboard/ai-assistant)."
        else:
            # Tự động tạo bản đầu tiên
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

        return (
            "📈 HẠN MỨC TIN NHẮN ZALO & AI\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"• Tháng: {st['current_month']}\n"
            f"• Đã gửi: {tot_sent} / {tot_lim} tin ({st['percent_used']}%)\n"
            f"• Tin thông thường còn lại: {std_rem} / {std_lim}\n"
            f"• Tin dự phòng khẩn cấp: {crit_rem} / {crit_res}\n"
            f"• Lượt gọi AI 9Router: {st['ai_requests']} lượt\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Hạn mức được kiểm soát nghiêm ngặt theo quy tắc Zalo Basic Quota."
        )

    def _cmd_ask(self, question: str) -> str:
        if not question:
            return "Vui lòng nhập câu hỏi sau lệnh /ask. Ví dụ: `/ask Cách kiểm tra VRAM GPU A30?`"

        # Record AI request count
        self.quota_ledger.record_ai_request()

        # Cung cấp ngữ cảnh thực tế của hệ thống để AI trả lời chính xác mà không cần gọi tool
        live_telemetry = self._cmd_status()
        live_jobs = self._cmd_jobs()
        live_allocs = self._cmd_allocations()
        context_msg = (
            f"DỮ LIỆU THỜI GIAN THỰC TỪ MÁY CHỦ WORKSTATION AIOT-WS1:\n\n"
            f"{live_telemetry}\n\n"
            f"{live_jobs}\n\n"
            f"{live_allocs}\n\n"
            f"QUY TẮC PHẢN HỒI:\n"
            f"1. Trả lời trực tiếp, tự nhiên bằng tiếng Việt dựa trên dữ liệu thực tế ở trên.\n"
            f"2. Tuyệt đối không sinh tool_calls hoặc lệnh shell vì dữ liệu phần cứng và tác vụ đã có đủ."
        )

        ai_reply = self.ai_client.generate_chat_response(
            messages=[
                {"role": "system", "content": context_msg},
                {"role": "user", "content": question},
            ],
            temperature=0.2,
            max_tokens=600,
        )
        return f"🤖 Trợ lý AI:\n{ai_reply}"

    # ---------------- Single Polling Consumer Guard ----------------

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
