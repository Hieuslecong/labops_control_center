"""
Zalo Bot Polling Worker & Autonomous SRE Scheduler (LabOps AI SRE Agent v2)
- Database-backed single-consumer lease (with PID file diagnostics).
- Strictly RBAC-checked incoming command/agent handling.
- Duplicate-safe outbox idempotency & DELIVERY_UNKNOWN timeout handling.
- Periodic telemetry sampling, Incident Engine evaluation, and P0/P1 notification dispatch.
- Event-driven + 30s reconciliation Repository Guard alerts.
- Daily Executive Briefing at 08:00 Asia/Ho_Chi_Minh & Weekly Report Friday 17:00 ICT.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import app.core.config  # noqa: F401,E402
from app.db.models import V4WorkerCursorRecord  # noqa: E402
from app.services.assistant_v4.inbox_outbox import chunk_zalo_message  # noqa: E402
from app.services.zalo_assistant.service import zalo_assistant_service  # noqa: E402

PARITY_RELEASE_ANNOUNCEMENT_KEY = "worker:release_notice:zalo_admin_parity_42_v1"


def build_admin_parity_release_announcement() -> str:
    """Build the one-time Vietnamese Admin Zalo release note for 42-action Web parity."""
    return (
        "🚀 CẬP NHẬT HỆ THỐNG: ZALO AI AGENT ĐÃ ĐỒNG BỘ 100% QUYỀN QUẢN TRỊ WEB ADMIN (42 THAO TÁC / 7 PHÂN HỆ)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Chào Admin, từ bản đồng bộ mới nhất, bạn có thể điều khiển toàn bộ thao tác quản trị trên Web Dashboard ngay qua Zalo (mọi lệnh từ nhóm chat đều cách ly gửi OTP riêng về DM 1-1):\n\n"
        "1️⃣ Thành viên, Mật khẩu & Vòng đời (9): tạo tài khoản Web, cấp lại mật khẩu Web / mật khẩu SSH tạm lần đầu, gán GVHD, đổi quota, tạm ngưng / khôi phục / lưu trữ / xóa vĩnh viễn thành viên (R4).\n"
        "2️⃣ Khóa SSH, Phiên & Workspace (7): cấp phát SSH/workspace, xoay vòng SSH key, thu hồi 1 key hoặc toàn bộ SSH, ngắt phiên SSH, kiểm định lại 6 cổng Verification Gate, kiểm tra/nhận Vault.\n"
        "3️⃣ Đơn xin cấp SSH MRAM (5): phê duyệt, từ chối, yêu cầu bổ sung, gửi lại mời Zalo, quét SLA quá hạn.\n"
        "4️⃣ WireGuard VPN (3): cấp mới, bật/tạm tắt, thu hồi vĩnh viễn thiết bị.\n"
        "5️⃣ GPU, Jobs & Processes (5): cấp GPU, giải phóng reservation, dừng job, dừng mềm SIGTERM / hủy SIGKILL tiến trình.\n"
        "6️⃣ Kho Mã & Bảo mật (3): duyệt/từ chối & quét lại Repository Guard, quyết định Security Scan.\n"
        "7️⃣ Sự cố, Báo cáo, Zalo & V4 (5+5): ACK/Resolve sự cố, đánh giá rule ngay, tạo báo cáo ngày/tuần, tạo mã `/link` / gán / gỡ Zalo ID, cấu hình cảnh báo, nạp lại V4 & chạy retention.\n\n"
        "🛡️ Cơ chế an toàn & chống làm sai ý mới:\n"
        "• Hỏi làm rõ (1 / 2 / 3) khi yêu cầu mơ hồ hoặc trùng nhiều người (VD: `cấp lại mật khẩu cho khuongnd`).\n"
        "• 100% thao tác R1–R4 hiện `Phiếu Xác Nhận Hiểu Đúng` kèm các lựa chọn tương tự (`ĐỔI <số>`) và `HỦY`.\n"
        "• Hỗ trợ Short Reply OTP: khi chỉ có 1 phiếu chờ trong DM 1-1, chỉ cần gõ thẳng mã OTP 6 ký tự (hoặc dùng `/confirm <action_id> <otp>`).\n"
        "• Lệnh R4 xóa vĩnh viễn bắt buộc OTP + `CONFIRM DELETE <username>`. Chính sách mặc định `vault_only` không lưu mật khẩu rõ vào log/DB.\n\n"
        "💡 Ví dụ thử ngay trong DM 1-1:\n"
        "• `cấp lại mật khẩu cho khuongnd`\n"
        "• `xác minh workspace cho khuongnd`\n"
        "• `đổi quota 150GB cho khuongnd` | `quét SLA`"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (ZaloWorker) %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("zalo_worker")

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "timeout" in name or "timed out" in msg or "timeout" in msg


class ZaloPollingWorker:
    LEASE_NAME = "zalo_sre_worker"

    def __init__(self, service_instance: Optional[Any] = None):
        self.service = service_instance or zalo_assistant_service
        self.bot_engine = self.service.bot_engine
        self.quota_ledger = self.service.quota_ledger
        self.report_generator = self.service.report_generator
        self.lease_manager = self.service.lease_manager
        self.identity_rbac = self.service.identity_rbac
        self.telemetry_collector = self.service.telemetry_collector
        self.incident_engine = self.service.incident_engine
        self.dispatcher = self.service.notification_dispatcher
        self.repo_alert_monitor = self.service.repo_alert_monitor
        self.bot_token = os.getenv("ZALO_BOT_TOKEN") or self.bot_engine.bot_token
        self.is_running = False
        self.lease_owner_id = f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.pid_file = os.path.join(self.service.data_dir, "zalo_worker.pid")
        self.status_file = os.path.join(self.service.data_dir, "zalo_worker_status.json")
        self.last_reported_week = self.report_generator.get_last_reported_week()
        self.report_chat_ids = [
            c.strip()
            for c in os.getenv("ZALO_REPORT_CHAT_IDS", "").split(",")
            if c.strip() and c.strip() != "*"
        ]
        self._last_telemetry_eval_mono: float = 0.0

    async def _send_zalo_chunks(self, bot: Any, *, chat_id: str, text: str) -> List[str]:
        """Split long outbound Zalo messages cleanly (<=1800 chars) so Zalo never drops >2000-char payloads."""
        chunks = chunk_zalo_message(text, max_chars=1800) or ([str(text)] if str(text or "").strip() else [])
        msg_ids: List[str] = []
        for idx, chunk in enumerate(chunks):
            if idx > 0:
                await asyncio.sleep(0.15)
            sent = await bot.send_message(chat_id=chat_id, text=chunk)
            prov_id = getattr(sent, "message_id", None)
            if prov_id is not None:
                msg_ids.append(str(prov_id))
        return msg_ids

    async def process_incoming_message(
        self,
        bot: Any,
        *,
        raw_text: str,
        chat_id: str,
        sender_id: str,
        sender_name: str = "User",
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not raw_text.strip() or not str(chat_id or "").strip():
            return {"dropped": True, "reason": "EMPTY"}

        text_stripped = raw_text.strip()
        is_link_cmd = text_stripped.lower().startswith("/link")

        if not is_link_cmd:
            if not self.bot_engine.is_authorized(sender_id) or not self.bot_engine.is_chat_allowed(
                chat_id, sender_id=sender_id
            ):
                logger.warning(
                    "Bỏ qua im lặng tin nhắn Zalo trái phép từ '%s' (%s) trong chat %s",
                    sender_name,
                    sender_id,
                    chat_id,
                )
                return {"dropped": True, "reason": "UNAUTHORIZED"}

        logger.info("📩 Nhận tin nhắn từ '%s' (%s) trong chat %s: %s", sender_name, sender_id, chat_id, raw_text[:50])
        self.identity_rbac.record_observed_private_chat(sender_id, chat_id, display_name=sender_name)
        idemp_key = f"zalo-msg-{message_id}" if message_id else None

        if idemp_key:
            existing_rec = self.quota_ledger.get_idempotency_record(sender_id, idemp_key)
            if existing_rec and existing_rec.get("status") in {"SENT", "DELIVERED", "DELIVERY_UNKNOWN"}:
                logger.info(
                    "⏭️ Bỏ qua tin nhắn trùng idempotency_key=%s msg_id=%s status=%s",
                    idemp_key,
                    existing_rec.get("message_id"),
                    existing_rec.get("status"),
                )
                return {
                    "success": True,
                    "deduplicated": True,
                    "message_id": existing_rec.get("message_id"),
                    "status": existing_rec.get("status"),
                }

        result = await self.bot_engine.ahandle_command(
            command_text=raw_text,
            sender_id=sender_id,
            idempotency_key=idemp_key,
            chat_id=chat_id,
            source="zalo",
            defer_outbox_commit=True,
            sender_name=sender_name,
        )

        if not result.get("success"):
            if result.get("command") == "/link" and result.get("reply"):
                try:
                    await self._send_zalo_chunks(bot, chat_id=chat_id, text=result["reply"])
                except Exception:
                    pass
            return result

        if result.get("suppress_outbound"):
            return result

        reply_text = result.get("reply", "")
        msg_id = result.get("message_id")
        if not reply_text:
            return result

        # /link verification reply does not reserve outbox quota ledger
        if result.get("command") == "/link":
            try:
                await self._send_zalo_chunks(bot, chat_id=chat_id, text=reply_text)
            except Exception as link_send_err:
                logger.warning("Lỗi gửi xác nhận /link tới %s: %s", chat_id, link_send_err)
            return result

        # Duplicate idempotency protection: NEVER resend over transport if already SENT or DELIVERY_UNKNOWN
        if msg_id and self.quota_ledger.is_already_dispatched(msg_id):
            logger.info("⏭️ Bỏ qua gửi trùng idempotency cho msg_id=%s tới chat %s", msg_id, chat_id)
            return {**result, "deduplicated": True}

        try:
            provider_msg_ids = await self._send_zalo_chunks(bot, chat_id=chat_id, text=reply_text)
            if msg_id:
                self.quota_ledger.confirm_sent(
                    msg_id,
                    provider_message_id=provider_msg_ids[0] if provider_msg_ids else "accepted",
                )
            for dm_item in list(result.get("dm_notifications") or []):
                dm_chat = str(dm_item.get("chat_id") or "").strip()
                dm_text = str(dm_item.get("text") or "").strip()
                if dm_chat and dm_text and dm_chat != chat_id:
                    try:
                        await self._send_zalo_chunks(bot, chat_id=dm_chat, text=dm_text)
                    except Exception as dm_err:
                        logger.warning("Không thể gửi Phiếu Xác Nhận sang DM 1-1 %s: %s", dm_chat, dm_err)
            logger.info(
                "📤 Đã gửi phản hồi (%s) tới chat %s (Dung lượng: %d chars)",
                result.get("command"),
                chat_id,
                len(reply_text),
            )
            return result
        except Exception as send_err:
            if msg_id:
                if _is_timeout_error(send_err):
                    self.quota_ledger.mark_delivery_unknown(msg_id, str(send_err))
                    logger.warning("Timeout gửi tin Zalo %s (trạng thái DELIVERY_UNKNOWN, không hoàn quota): %s", msg_id, send_err)
                    return {"success": False, "error": "DELIVERY_UNKNOWN", "refunded": False, "detail": str(send_err)}
                try:
                    self.quota_ledger.mark_delivery_failed(msg_id, str(send_err))
                except Exception as ref_err:
                    logger.error("Không thể hoàn quota cho %s: %s", msg_id, ref_err)
            logger.error("Lỗi khi gửi tin nhắn Zalo (đã hoàn quota %s): %s", msg_id, send_err)
            return {"success": False, "error": "DELIVERY_FAILED", "refunded": True, "detail": str(send_err)}

    def _acquire_lease(self) -> bool:
        """Database lease primary lock (with PID file diagnostics)."""
        acquired = self.lease_manager.acquire_or_renew(
            self.LEASE_NAME,
            self.lease_owner_id,
            ttl_seconds=45,
            metadata={"mode": "sre_worker_v2"},
        )
        if not acquired:
            current = self.lease_manager.get_lease_status(self.LEASE_NAME)
            logger.warning("⚠️ Một Zalo SRE Worker khác đang giữ DB lease: %s. Thoát an toàn.", current)
            return False

        os.makedirs(os.path.dirname(self.pid_file), exist_ok=True)
        try:
            with open(self.pid_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        return True

    def _renew_lease(self) -> None:
        try:
            ok = self.lease_manager.acquire_or_renew(
                self.LEASE_NAME,
                self.lease_owner_id,
                ttl_seconds=45,
                metadata={"mode": "sre_worker_v2"},
            )
            if not ok:
                logger.error("❌ Mất quyền giữ DB lease '%s'; dừng polling để tránh xung đột.", self.LEASE_NAME)
                self.is_running = False
        except Exception as exc:
            logger.warning("Lỗi gia hạn DB lease: %s", exc)

    def _write_worker_status(
        self,
        is_polling: bool,
        bot_name: str = "",
        last_error: Optional[str] = None,
    ) -> None:
        os.makedirs(os.path.dirname(self.status_file), exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "lease_owner": self.lease_owner_id,
            "is_polling": is_polling,
            "bot_name": bot_name,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "authorized_users_count": len(self.bot_engine.authorized_users or []),
            "last_error": last_error,
        }
        tmp_path = f"{self.status_file}.tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.status_file)
        except Exception as exc:
            logger.debug("Không thể ghi trạng thái worker: %s", exc)

    def _release_lease(self):
        try:
            self.lease_manager.release(self.LEASE_NAME, self.lease_owner_id)
        except Exception:
            pass
        try:
            if os.path.exists(self.pid_file):
                with open(self.pid_file, "r") as f:
                    curr_pid = int(f.read().strip())
                if curr_pid == os.getpid():
                    os.remove(self.pid_file)
        except Exception:
            pass

    async def _maybe_send_parity_release_announcement(
        self,
        bot: Any,
        *,
        force: bool = False,
        target_chat_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Send a one-time DM 1-1 release announcement introducing 42-action Web Admin parity to verified Admins."""
        v4 = getattr(self.service, "v4", None)
        if not force and (v4 is None or not getattr(v4.config, "write_actions", False)):
            return {"sent": False, "reason": "WRITE_ACTIONS_DISABLED", "delivered_to": []}

        if not force:
            try:
                with self.service.db.session_scope() as session:
                    existing = session.get(V4WorkerCursorRecord, PARITY_RELEASE_ANNOUNCEMENT_KEY)
                    if existing is not None and existing.cursor_value == "sent":
                        return {
                            "sent": False,
                            "reason": "ALREADY_SENT",
                            "delivered_to": list((existing.metadata_json or {}).get("sent_to") or []),
                        }
            except Exception as check_err:
                logger.warning("Không kiểm tra được trạng thái thông báo bản parity mới: %s", check_err)

        if target_chat_ids:
            recipients = [str(c).strip() for c in target_chat_ids if str(c).strip()]
        else:
            recipients = self.identity_rbac.get_verified_admin_recipients(
                fallback_authorized_users=self.bot_engine.authorized_users,
                alert_category="system_release",
            )
        if not recipients:
            return {"sent": False, "reason": "NO_VERIFIED_ADMINS", "delivered_to": []}

        text = build_admin_parity_release_announcement()
        delivered_to: List[str] = []
        errors: Dict[str, str] = {}
        for admin_chat_id in recipients:
            try:
                await self._send_zalo_chunks(bot, chat_id=admin_chat_id, text=text)
                delivered_to.append(admin_chat_id)
            except Exception as send_exc:
                errors[admin_chat_id] = str(send_exc)[:200]
                logger.warning("Không thể gửi thông báo tính năng mới tới Admin %s: %s", admin_chat_id, send_exc)

        if delivered_to:
            now_utc = datetime.now(timezone.utc)
            meta = {
                "sent_to": delivered_to,
                "sent_at": now_utc.isoformat(),
                "errors": errors,
            }
            try:
                with self.service.db.session_scope() as session:
                    rec = session.get(V4WorkerCursorRecord, PARITY_RELEASE_ANNOUNCEMENT_KEY)
                    if rec is None:
                        session.add(
                            V4WorkerCursorRecord(
                                cursor_name=PARITY_RELEASE_ANNOUNCEMENT_KEY,
                                cursor_value="sent",
                                metadata_json=meta,
                                updated_at=now_utc,
                            )
                        )
                    else:
                        rec.cursor_value = "sent"
                        rec.metadata_json = meta
                        rec.updated_at = now_utc
            except Exception as save_err:
                logger.warning("Đã gửi thông báo Admin nhưng không lưu được cursor idempotent: %s", save_err)
            logger.info("📣 Đã gửi thông báo giới thiệu 42 chức năng quản trị mới tới Admin Zalo: %s", delivered_to)
            return {"sent": True, "reason": "DELIVERED", "delivered_to": delivered_to, "errors": errors}

        return {"sent": False, "reason": "DELIVERY_FAILED", "delivered_to": [], "errors": errors}

    async def start(self):
        if not self.bot_token:
            logger.error("❌ Không tìm thấy ZALO_BOT_TOKEN trong môi trường hoặc cấu hình.")
            return

        if not self._acquire_lease():
            return

        self.bot_engine.reload_persisted_config(force=True)
        self.is_running = True
        logger.info("🚀 Khởi động LabOps AI SRE Worker v2 (PID: %d, Lease: %s)", os.getpid(), self.lease_owner_id)

        try:
            from zalo_bot import Bot
        except ImportError:
            logger.error("❌ Thiếu thư viện python-zalo-bot.")
            self._write_worker_status(False, last_error="MISSING_ZALO_BOT_PACKAGE")
            self._release_lease()
            return

        bot = Bot(token=self.bot_token)
        bot_name = ""
        try:
            me = await bot.get_me()
            bot_name = str(getattr(me, "display_name", "") or "")
            logger.info("✅ Đã kết nối Zalo Bot Platform: %s (ID: %s, Type: %s)", me.display_name, me.id, me.account_type)
            self._write_worker_status(True, bot_name=bot_name)
        except Exception as e:
            logger.error("❌ Không thể xác thực Token Zalo: %s", e)
            self._write_worker_status(False, last_error=str(e))
            self._release_lease()
            return

        try:
            await self._maybe_send_parity_release_announcement(bot)
        except Exception as ann_err:
            logger.warning("Lỗi khi gửi thông báo tính năng mới lúc khởi động: %s", ann_err)

        offset: Optional[int] = None
        consecutive_errors = 0
        v4 = getattr(self.service, "v4", None)
        if v4 is not None and getattr(v4.config, "enabled", False):
            try:
                saved_cursor = await asyncio.to_thread(v4.inbox.get_cursor, "zalo_polling_offset")
                if saved_cursor and str(saved_cursor).isdigit():
                    offset = int(saved_cursor)
                    logger.info("📌 Khôi phục cursor polling V4 offset=%s", offset)
            except Exception as cur_err:
                logger.warning("Không đọc được V4 polling cursor: %s", cur_err)

        while self.is_running:
            try:
                self._renew_lease()
                if not self.is_running:
                    break
                self.bot_engine.reload_persisted_config()

                updates = await bot.get_update(offset=offset, timeout=15, limit=20)
                consecutive_errors = 0
                self._write_worker_status(True, bot_name=bot_name)

                update_list = updates if isinstance(updates, list) else ([updates] if updates else [])
                for update in update_list:
                    if not update:
                        continue
                    update_id = getattr(update, "update_id", None)
                    if update_id is not None:
                        offset = update_id + 1

                    message = getattr(update, "message", None)
                    if not message:
                        continue

                    self.bot_engine.reload_persisted_config()
                    raw_text = getattr(message, "text", "") or ""
                    chat = getattr(message, "chat", None)
                    chat_id = str(getattr(chat, "id", "")) if chat else ""
                    from_user = getattr(message, "from_user", None)
                    sender_id = str(getattr(from_user, "id", chat_id)) if from_user else chat_id
                    sender_name = str(getattr(from_user, "display_name", "User")) if from_user else "User"
                    msg_id_val = getattr(message, "message_id", None)
                    v4_inbox_id: Optional[str] = None
                    if v4 is not None and getattr(v4.config, "enabled", False) and chat_id and sender_id:
                        try:
                            ext_id = str(msg_id_val if msg_id_val is not None else (update_id if update_id is not None else uuid.uuid4().hex[:12]))
                            inb_res = await asyncio.to_thread(
                                v4.inbox.ingest_message,
                                channel="zalo",
                                bot_id=bot_name or "default",
                                external_message_id=ext_id,
                                chat_id=chat_id,
                                sender_id=sender_id,
                                text=raw_text,
                                update_id=int(update_id) if update_id is not None else None,
                                cursor_name="zalo_polling_offset",
                                cursor_value=str(offset) if offset is not None else None,
                            )
                            if inb_res.get("duplicate"):
                                continue
                            v4_inbox_id = inb_res.get("inbox_id")
                        except Exception as inb_err:
                            logger.warning("V4 inbox ingest error: %s", inb_err)
                    try:
                        await self.process_incoming_message(
                            bot,
                            raw_text=raw_text,
                            chat_id=chat_id,
                            sender_id=sender_id,
                            sender_name=sender_name,
                            message_id=str(msg_id_val) if msg_id_val is not None else None,
                        )
                        if v4 is not None and v4_inbox_id:
                            await asyncio.to_thread(v4.inbox.mark_processed, v4_inbox_id, state="PROCESSED")
                    except Exception as handle_err:
                        if v4 is not None and v4_inbox_id:
                            try:
                                await asyncio.to_thread(
                                    v4.inbox.mark_processed,
                                    v4_inbox_id,
                                    state="FAILED",
                                    error=str(handle_err),
                                )
                            except Exception:
                                pass
                        logger.error("Lỗi khi xử lý phản hồi lệnh: %s", handle_err)

                from app.services.assistant_v4.notification_policy import resolve_notification_owner

                v4_owner = resolve_notification_owner(getattr(v4, "config", None) if v4 is not None else None)
                v4_handles_notif = (v4 is not None and v4_owner == "V4_ACTIVE")
                await self._check_repo_security_alerts(bot=bot, suppress_direct_send=v4_handles_notif)
                await self._check_infra_telemetry_and_incidents(
                    bot=bot,
                    suppress_v2_enqueue=v4_handles_notif,
                )
                await self._check_mram_sla_and_expiry()
                await self._flush_notification_outbox(bot=bot)
                if not v4_handles_notif:
                    await self._check_daily_report_schedule(bot=bot)
                    await self._check_weekly_report_schedule(bot=bot)
                if v4 is not None and getattr(v4.config, "enabled", False):
                    await self._run_v4_worker_step(v4, bot=bot)

                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                logger.info("Zalo Polling Worker nhận lệnh dừng.")
                break
            except Exception as e:
                consecutive_errors += 1
                backoff = min(30, 2 ** consecutive_errors)
                logger.warning("Lỗi polling (lần %d), thử lại sau %ds: %s", consecutive_errors, backoff, e)
                self._write_worker_status(True, bot_name=bot_name, last_error=str(e))
                await asyncio.sleep(backoff)

        self._write_worker_status(False, bot_name=bot_name)
        self._release_lease()
        logger.info("🛑 Zalo SRE Worker đã dừng an toàn.")

    async def _flush_notification_outbox(self, bot: Any) -> None:
        subs = {s["recipient_zalo_id"]: s for s in self.identity_rbac.list_subscriptions()}

        async def _send_fn(recipient_id: str, text: str) -> Optional[str]:
            msg_ids = await self._send_zalo_chunks(bot, chat_id=recipient_id, text=text)
            return msg_ids[0] if msg_ids else "accepted"

        try:
            await self.dispatcher.dispatch_pending(_send_fn, limit=15, subscriptions=subs)
        except Exception as exc:
            logger.warning("Lỗi flush notification outbox: %s", exc)

    async def _check_infra_telemetry_and_incidents(
        self,
        bot: Optional[Any] = None,
        *,
        suppress_v2_enqueue: bool = False,
    ) -> None:
        now_mono = time.monotonic()
        if self._last_telemetry_eval_mono > 0 and (now_mono - self._last_telemetry_eval_mono) < 60.0:
            return
        self._last_telemetry_eval_mono = now_mono

        try:
            snap = await asyncio.to_thread(self.telemetry_collector.collect_snapshot)
            await asyncio.to_thread(self.telemetry_collector.record_sample, snap)
            evaluated = await asyncio.to_thread(self.incident_engine.evaluate_snapshot, snap)

            if suppress_v2_enqueue:
                return

            for item in evaluated:
                inc = item.get("incident") or {}
                action = item.get("action")
                if action not in {"CREATED", "REOPENED"}:
                    continue
                sev = str(inc.get("severity") or "P2").upper()
                if sev not in {"P0", "P1"}:
                    continue

                # Determine recipients by RBAC & owner scoping
                owner_id = inc.get("owner_id")
                recipients = self.identity_rbac.get_owner_recipients(
                    owner_id,
                    include_admins=True,
                    fallback_authorized_users=self.bot_engine.authorized_users,
                    alert_category="p0" if sev == "P0" else "p1",
                )
                msg_text = (
                    f"{'🚨' if sev == 'P0' else '⚠️'} [{sev}/{inc.get('confidence')}] {inc.get('title')}\n"
                    f"• Mã sự cố: {inc.get('incident_id')}\n"
                    f"• Nguồn: {inc.get('source')} | Tài nguyên: {inc.get('resource_id')}\n"
                    f"• Khuyến nghị: {inc.get('recommended_action') or 'Kiểm tra trên Dashboard'}\n"
                    f"👉 Dùng `/ack {inc.get('incident_id')}` để xác nhận."
                )
                for rec_id in recipients:
                    self.dispatcher.enqueue(
                        idempotency_key=f"inc:{inc['incident_id']}:{action}:v{inc.get('reopen_count', 0)}",
                        recipient_id=rec_id,
                        event_type=inc.get("event_type", "INFRA_INCIDENT"),
                        severity=sev,
                        is_critical=(sev == "P0"),
                        incident_id=inc.get("incident_id"),
                        message_text=msg_text,
                        correlation_id=inc.get("correlation_id") or "",
                    )
        except Exception as exc:
            logger.warning("Lỗi đánh giá telemetry & incident engine: %s", exc)

    async def deliver_daily_brief_report(
        self,
        period_key: str,
        *,
        bot: Optional[Any] = None,
        is_test: bool = False,
        now_utc: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Deliver Daily Executive Briefing strictly to verified ADMIN recipient 0c00ed813eccd7928edd via DM."""
        ref_utc = now_utc or datetime.now(timezone.utc)
        report = self.report_generator.get_daily_report_by_period(period_key)
        if report is None:
            report = await asyncio.to_thread(
                self.report_generator.generate_daily_executive_briefing,
                now_utc=ref_utc,
                ai_client=self.service.ai_client,
                telemetry_collector=self.telemetry_collector,
                incident_engine=self.incident_engine,
            )

        ver = self.identity_rbac.verify_daily_brief_recipient()
        if not ver.get("verified"):
            d_upd = {
                "status": "RECIPIENT_NOT_VERIFIED",
                "recipient_id": ver.get("zalo_user_id"),
                "recipient_display_name": ver.get("display_name"),
                "error": ver.get("reason") or "Recipient identity or private chat_id not verified",
                "instruction": ver.get("instruction"),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            if not is_test:
                self.report_generator.transition_daily_report_state(
                    period_key, status="RECIPIENT_NOT_VERIFIED", delivery_state=d_upd
                )
            return {"success": False, "status": "RECIPIENT_NOT_VERIFIED", "verification": ver, "report_id": report.get("id")}

        chat_id = str(ver["chat_id"])
        summary_msg = report.get("zalo_summary") or report.get("content", "")[:1750]
        if len(summary_msg) > 1950:
            summary_msg = summary_msg[:1950]

        idemp_key = (
            f"daily-brief-test:{period_key}:{chat_id}:{int(time.time())}"
            if is_test
            else f"daily-brief:{period_key}:{chat_id}"
        )

        # Check existing outbox state for scheduled / retry runs
        if not is_test:
            existing_outbox = [
                o for o in self.dispatcher.list_outbox_entries(limit=100)
                if o.get("idempotency_key") == idemp_key and o.get("recipient_id") == chat_id
            ]
            if existing_outbox:
                ob = existing_outbox[0]
                ob_status = str(ob.get("status") or "").upper()
                if ob_status in {"SENT", "DELIVERY_UNKNOWN"}:
                    self.report_generator.transition_daily_report_state(
                        period_key,
                        status=ob_status,
                        delivery_state={
                            "status": ob_status,
                            "recipient_id": chat_id,
                            "chat_id_ref": chat_id,
                            "provider_message_id": ob.get("provider_message_id"),
                            "sent_at": ob.get("sent_at"),
                            "retry_count": ob.get("attempt_count", 0),
                        },
                    )
                    return {"success": ob_status == "SENT", "deduplicated": True, "status": ob_status}

        if not is_test:
            self.report_generator.transition_daily_report_state(
                period_key,
                status="SENDING",
                delivery_state={"status": "SENDING", "recipient_id": chat_id, "chat_id_ref": chat_id},
            )

        self.dispatcher.enqueue(
            idempotency_key=idemp_key,
            recipient_id=chat_id,
            event_type="DAILY_EXECUTIVE_BRIEF_TEST" if is_test else "DAILY_EXECUTIVE_BRIEF",
            severity="REPORT",
            is_critical=False,
            report_id=report.get("id"),
            message_text=summary_msg,
        )

        if bot is None:
            return {"success": True, "status": "ENQUEUED", "idempotency_key": idemp_key, "recipient_id": chat_id}

        await self._flush_notification_outbox(bot=bot)

        after = [
            o for o in self.dispatcher.list_outbox_entries(limit=100)
            if o.get("idempotency_key") == idemp_key and o.get("recipient_id") == chat_id
        ]
        if not after:
            return {"success": False, "status": "UNKNOWN", "recipient_id": chat_id}

        ob = after[0]
        ob_st = str(ob.get("status") or "").upper()
        mapped_status = (
            "SENT"
            if ob_st == "SENT"
            else ("DELIVERY_UNKNOWN" if ob_st == "DELIVERY_UNKNOWN" else ("RETRY_PENDING" if ob_st == "FAILED_RETRYABLE" else "FAILED"))
        )
        upd = {
            "status": mapped_status,
            "recipient_id": chat_id,
            "chat_id_ref": chat_id,
            "provider_message_id": ob.get("provider_message_id"),
            "sent_at": ob.get("sent_at"),
            "retry_count": ob.get("attempt_count", 0),
            "error": ob.get("last_error"),
            "idempotency_key": idemp_key,
        }
        if is_test:
            self.report_generator.transition_daily_report_state(
                period_key,
                delivery_state={"last_test_delivery": upd},
            )
        else:
            self.report_generator.transition_daily_report_state(
                period_key,
                status=mapped_status,
                delivery_state=upd,
            )
        return {"success": mapped_status == "SENT", "status": mapped_status, "delivery": upd, "verification": ver}

    async def _check_daily_report_schedule(self, bot: Optional[Any] = None, now_utc: Optional[datetime] = None) -> None:
        """Persistent 08:00 Asia/Ho_Chi_Minh Daily Executive Briefing scheduler with catch-up & missed-window alerting."""
        cfg = self.identity_rbac.get_daily_brief_config()
        if not cfg.get("enabled", True):
            return

        ref_utc = now_utc or datetime.now(timezone.utc)
        if ref_utc.tzinfo is None:
            ref_utc = ref_utc.replace(tzinfo=timezone.utc)
        local_now = ref_utc.astimezone(VN_TZ)
        today_08 = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
        if local_now < today_08:
            return

        period_key = today_08.strftime("%Y-%m-%d")
        existing = self.report_generator.get_daily_report_by_period(period_key)
        hours_since_08 = max(0.0, (local_now - today_08).total_seconds() / 3600.0)
        catchup_hours = float(cfg.get("catchup_hours") or 4)

        if existing is not None:
            st = str(existing.get("status") or "").upper()
            if st in {"SENT", "DELIVERY_UNKNOWN", "MISSED_WINDOW", "FAILED"}:
                return
            # Wait for retry window / don't tight-loop RECIPIENT_NOT_VERIFIED except when freshly verified
            if st == "RECIPIENT_NOT_VERIFIED":
                ver_check = self.identity_rbac.verify_daily_brief_recipient()
                if not ver_check.get("verified"):
                    return
        else:
            if hours_since_08 > catchup_hours:
                reason = f"Hệ thống ngoại tuyến vượt quá cửa sổ catch-up ({catchup_hours}h) sau 08:00 {period_key}."
                self.report_generator.record_missed_daily_window(
                    period_key,
                    win_start_utc=(today_08 - timedelta(days=1)).astimezone(timezone.utc),
                    win_end_utc=today_08.astimezone(timezone.utc),
                    reason=reason,
                )
                try:
                    self.incident_engine.upsert_incident(
                        event_type="DAILY_BRIEF_MISSED_WINDOW",
                        severity="P1",
                        source="scheduler",
                        resource_id="daily-brief-scheduler",
                        fingerprint=f"daily-brief-missed:{period_key}",
                        title=f"Bỏ lỡ lịch gửi Daily Executive Brief ngày {period_key} (quá hạn catch-up)",
                        recommended_action="Kiểm tra uptime worker và bấm Retry trong /dashboard/ai-assistant.",
                    )
                except Exception:
                    pass
                return

            is_late = hours_since_08 > (5.0 / 60.0)
            await asyncio.to_thread(
                self.report_generator.generate_daily_executive_briefing,
                now_utc=ref_utc,
                is_late_catchup=is_late,
                ai_client=self.service.ai_client,
                telemetry_collector=self.telemetry_collector,
                incident_engine=self.incident_engine,
            )

        await self.deliver_daily_brief_report(period_key, bot=bot, is_test=False, now_utc=ref_utc)

    async def _check_weekly_report_schedule(self, bot: Optional[Any] = None) -> None:
        """Friday 17:00 Asia/Ho_Chi_Minh weekly operational report."""
        now_local = datetime.now(VN_TZ)
        current_week_str = now_local.strftime("%Y-W%W")
        if now_local.weekday() == 4 and now_local.hour == 17 and self.last_reported_week != current_week_str:
            logger.info("⏰ Đến lịch định kỳ: Tự động tổng hợp báo cáo tuần LabOps...")
            try:
                rep = await asyncio.to_thread(
                    self.report_generator.generate_weekly_report,
                    author="Automated Weekly Cron",
                )
                self.report_generator.set_last_reported_week(current_week_str)
                self.last_reported_week = current_week_str
                logger.info("✅ Đã tạo báo cáo tuần: %s", rep["title"])

                recipients = list(
                    dict.fromkeys(
                        list(self.report_chat_ids)
                        + self.identity_rbac.get_verified_admin_recipients(
                            fallback_authorized_users=self.bot_engine.authorized_users,
                            alert_category="daily_report",
                        )
                    )
                )
                if bot is not None and recipients:
                    summary_msg = f"📑 {rep['title']}\n━━━━━━━━━━━━━━━━━━━━\n{rep['content'][:1200]}"
                    for target_chat in recipients:
                        self.dispatcher.enqueue(
                            idempotency_key=f"weekly-report:{current_week_str}",
                            recipient_id=target_chat,
                            event_type="WEEKLY_OPERATIONAL_REPORT",
                            severity="REPORT",
                            is_critical=False,
                            report_id=rep.get("id"),
                            message_text=summary_msg,
                        )
                    await self._flush_notification_outbox(bot=bot)
            except Exception as e:
                logger.error("Lỗi khi tự động tạo báo cáo tuần: %s", e)

    async def _check_repo_security_alerts(self, bot: Optional[Any] = None, *, suppress_direct_send: bool = False) -> None:
        if bot is None and not suppress_direct_send:
            return
        try:
            await self.repo_alert_monitor.check_and_notify(bot, suppress_direct_send=suppress_direct_send)
        except Exception as exc:
            logger.warning("Lỗi kiểm tra cảnh báo kiểm duyệt repo sau clone: %s", exc)

    async def _check_mram_sla_and_expiry(self) -> None:
        try:
            from app.services.mram_service import mram_service

            await asyncio.to_thread(mram_service.expire_overdue_requests)
            await asyncio.to_thread(mram_service.sweep_expired_ssh_access)
        except Exception as exc:
            logger.debug("Lỗi kiểm tra MRAM 24h SLA / SSH expiry: %s", exc)

    async def _run_v4_worker_step(self, v4: Any, *, bot: Optional[Any] = None) -> None:
        try:
            await asyncio.to_thread(v4.run_worker_cycle)
        except Exception as exc:
            logger.warning("V4 worker cycle error: %s", exc)

        from app.services.assistant_v4.notification_policy import resolve_notification_owner

        if bot is not None and resolve_notification_owner(getattr(v4, "config", None)) == "V4_ACTIVE":
            async def _v4_send(recipient_id: str, text: str, channel: str) -> Optional[str]:
                msg_ids = await self._send_zalo_chunks(bot, chat_id=recipient_id, text=text)
                return msg_ids[0] if msg_ids else "accepted"

            subs = {s["recipient_zalo_id"]: s for s in self.identity_rbac.list_subscriptions()}

            def _v4_auth_check(recipient_id: str, channel: str) -> bool:
                if channel == "zalo":
                    if not self.bot_engine.is_authorized(recipient_id):
                        return False
                    sub = subs.get(recipient_id)
                    if sub is not None and not sub.get("enabled", True):
                        return False
                    return True
                return True

            try:
                await v4.outbox.deliver_claimed_batch(
                    self.lease_owner_id,
                    _v4_send,
                    is_recipient_authorized_fn=_v4_auth_check,
                    batch_size=15,
                )
            except Exception as exc:
                logger.warning("V4 outbox deliver error: %s", exc)

    def stop(self):
        self.is_running = False


async def run_worker():
    worker = ZaloPollingWorker()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except NotImplementedError:
            pass

    await worker.start()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
