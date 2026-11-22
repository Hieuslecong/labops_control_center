"""
Zalo Bot Polling Worker & Background Scheduler
Listens for incoming messages from Zalo Bot API, processes commands (/status, /jobs, /report, /quota, /ask),
calls 9Router AI Gateway for natural language inquiries, enforces quota ledger (3,000 msgs/month),
and handles scheduled weekly report notifications.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Ensure project root & backend are in sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.services.zalo_assistant.repo_alert_monitor import RepoSecurityAlertMonitor
from app.services.zalo_assistant.service import zalo_assistant_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (ZaloWorker) %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("zalo_worker")


class ZaloPollingWorker:
    def __init__(self, service_instance: Optional[Any] = None):
        self.service = service_instance or zalo_assistant_service
        self.bot_engine = self.service.bot_engine
        self.quota_ledger = self.service.quota_ledger
        self.report_generator = self.service.report_generator
        self.bot_token = os.getenv("ZALO_BOT_TOKEN") or self.bot_engine.bot_token
        self.is_running = False
        self.pid_file = os.path.join(self.service.data_dir, "zalo_worker.pid")
        self.status_file = os.path.join(self.service.data_dir, "zalo_worker_status.json")
        self.last_reported_week = self.report_generator.get_last_reported_week()
        self.report_chat_ids = [
            c.strip()
            for c in os.getenv("ZALO_REPORT_CHAT_IDS", "").split(",")
            if c.strip() and c.strip() != "*"
        ]
        self.repo_alert_monitor = RepoSecurityAlertMonitor(
            data_dir=self.service.data_dir,
            quota_ledger=self.quota_ledger,
            bot_engine=self.bot_engine,
        )

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
        idemp_key = f"zalo-msg-{message_id}" if message_id else None

        result = await self.bot_engine.ahandle_command(
            command_text=raw_text,
            sender_id=sender_id,
            idempotency_key=idemp_key,
            chat_id=chat_id,
            source="zalo",
            defer_outbox_commit=True,
        )

        if not result.get("success") or result.get("suppress_outbound"):
            return result

        reply_text = result.get("reply", "")
        msg_id = result.get("message_id")
        if not reply_text:
            return result

        try:
            sent = await bot.send_message(chat_id=chat_id, text=reply_text)
            provider_msg_id = getattr(sent, "message_id", None)
            if msg_id:
                self.quota_ledger.confirm_sent(
                    msg_id,
                    provider_message_id=str(provider_msg_id) if provider_msg_id is not None else None,
                )
            logger.info(
                "📤 Đã gửi phản hồi (%s) tới chat %s (Dung lượng: %d chars)",
                result.get("command"),
                chat_id,
                len(reply_text),
            )
            return result
        except Exception as send_err:
            if msg_id:
                try:
                    self.quota_ledger.mark_delivery_failed(msg_id, str(send_err))
                except Exception as ref_err:
                    logger.error("Không thể hoàn quota cho %s: %s", msg_id, ref_err)
            logger.error("Lỗi khi gửi tin nhắn Zalo (đã hoàn quota %s): %s", msg_id, send_err)
            return {"success": False, "error": "DELIVERY_FAILED", "refunded": True, "detail": str(send_err)}

    def _acquire_lease(self) -> bool:
        """Single consumer PID lease to prevent multiple instances polling simultaneously."""
        os.makedirs(os.path.dirname(self.pid_file), exist_ok=True)
        if os.path.exists(self.pid_file):
            try:
                with open(self.pid_file, "r") as f:
                    old_pid = int(f.read().strip())
                # Check if process is still running
                os.kill(old_pid, 0)
                logger.warning(f"⚠️ Một Zalo Polling Worker khác đang chạy (PID: {old_pid}). Thoát để tránh xung đột offset.")
                return False
            except (OSError, ValueError):
                # Stale PID file
                pass

        try:
            with open(self.pid_file, "w") as f:
                f.write(str(os.getpid()))
            return True
        except Exception as e:
            logger.error(f"Không thể tạo lease PID file: {e}")
            return False

    def _write_worker_status(
        self,
        is_polling: bool,
        bot_name: str = "",
        last_error: Optional[str] = None,
    ) -> None:
        os.makedirs(os.path.dirname(self.status_file), exist_ok=True)
        payload = {
            "pid": os.getpid(),
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
            if os.path.exists(self.pid_file):
                with open(self.pid_file, "r") as f:
                    curr_pid = int(f.read().strip())
                if curr_pid == os.getpid():
                    os.remove(self.pid_file)
        except Exception:
            pass

    async def start(self):
        if not self.bot_token:
            logger.error("❌ Không tìm thấy ZALO_BOT_TOKEN trong môi trường hoặc cấu hình. Vui lòng kiểm tra .env.local.")
            return

        if not self._acquire_lease():
            return

        self.bot_engine.reload_persisted_config(force=True)
        self.is_running = True
        logger.info(f"🚀 Khởi động Zalo Polling Worker (PID: {os.getpid()})")
        logger.info(f"   • Model AI: {self.service.ai_client.model} ({self.service.ai_client.base_url})")
        logger.info(f"   • Hạn mức: {self.quota_ledger.TOTAL_MONTHLY_LIMIT} tin/tháng (2800 std / 200 crit)")
        logger.info(f"   • Zalo User ID được cấp phép: {len(self.bot_engine.authorized_users or [])} tài khoản")
        if not self.bot_engine.authorized_users:
            logger.warning(
                "⚠️ ZALO_AUTHORIZED_USERS đang rỗng (lưu ý: wildcard '*' bị cấm). Bot sẽ từ chối toàn bộ tin nhắn cho tới khi cấu hình ID hợp lệ."
            )

        try:
            from zalo_bot import Bot
        except ImportError:
            logger.error("❌ Thiếu thư viện python-zalo-bot. Cài đặt qua: pip install python-zalo-bot")
            self._write_worker_status(False, last_error="MISSING_ZALO_BOT_PACKAGE")
            self._release_lease()
            return

        bot = Bot(token=self.bot_token)
        bot_name = ""
        try:
            me = await bot.get_me()
            bot_name = str(getattr(me, "display_name", "") or "")
            logger.info(f"✅ Đã kết nối Zalo Bot Platform: {me.display_name} (ID: {me.id}, Type: {me.account_type})")
            self._write_worker_status(True, bot_name=bot_name)
        except Exception as e:
            logger.error(f"❌ Không thể xác thực Token Zalo: {e}")
            self._write_worker_status(False, last_error=str(e))
            self._release_lease()
            return

        offset: Optional[int] = None
        consecutive_errors = 0

        while self.is_running:
            try:
                self.bot_engine.reload_persisted_config()
                # Long polling with 15s timeout
                updates = await bot.get_update(offset=offset, timeout=15, limit=20)
                consecutive_errors = 0
                self._write_worker_status(True, bot_name=bot_name)

                update_list = updates if isinstance(updates, list) else ([updates] if updates else [])
                for update in update_list:
                    if not update:
                        continue

                    # Update offset to confirm receipt
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
                    try:
                        await self.process_incoming_message(
                            bot,
                            raw_text=raw_text,
                            chat_id=chat_id,
                            sender_id=sender_id,
                            sender_name=sender_name,
                            message_id=str(msg_id_val) if msg_id_val is not None else None,
                        )
                    except Exception as handle_err:
                        logger.error("Lỗi khi xử lý phản hồi lệnh: %s", handle_err)

                # Check scheduled weekly report (Friday 17:00 Asia/Ho_Chi_Minh)
                await self._check_weekly_report_schedule(bot=bot)

                # Check post-clone repository review alerts for Admin
                await self._check_repo_security_alerts(bot=bot)

                # Yield control briefly
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
        logger.info("🛑 Zalo Polling Worker đã dừng an toàn.")

    async def _check_weekly_report_schedule(self, bot: Optional[Any] = None) -> None:
        """Check if weekly report needs to be generated and broadcasted."""
        now = datetime.now(timezone.utc)
        # GMT+7: weekday 4 is Friday. 17:00 ICT is 10:00 UTC.
        current_week_str = now.strftime("%Y-W%W")
        if now.weekday() == 4 and now.hour == 10 and self.last_reported_week != current_week_str:
            logger.info("⏰ Đến lịch định kỳ: Tự động tổng hợp báo cáo tuần LabOps...")
            try:
                rep = await asyncio.to_thread(
                    self.report_generator.generate_weekly_report,
                    author="Automated Weekly Cron",
                )
                self.report_generator.set_last_reported_week(current_week_str)
                self.last_reported_week = current_week_str
                logger.info("✅ Đã tạo báo cáo tuần: %s", rep["title"])

                if bot is not None and self.report_chat_ids:
                    summary_msg = f"📑 {rep['title']}\n━━━━━━━━━━━━━━━━━━━━\n{rep['content'][:1200]}"
                    for target_chat in self.report_chat_ids:
                        try:
                            _, outbox_id, _ = self.quota_ledger.reserve_outbox(
                                recipient_id=target_chat,
                                message_text=summary_msg,
                                is_critical_alert=False,
                                idempotency_key=f"weekly-report-{current_week_str}",
                                metadata={"command": "/report", "scheduled": True},
                            )
                        except Exception as q_err:
                            logger.warning("Không thể reserve quota gửi báo cáo tới %s: %s", target_chat, q_err)
                            continue
                        try:
                            sent = await bot.send_message(chat_id=target_chat, text=summary_msg)
                            prov_id = getattr(sent, "message_id", None)
                            self.quota_ledger.confirm_sent(
                                outbox_id,
                                provider_message_id=str(prov_id) if prov_id is not None else None,
                            )
                        except Exception as send_err:
                            self.quota_ledger.mark_delivery_failed(outbox_id, str(send_err))
                            logger.error("Lỗi gửi báo cáo tuần tới %s: %s", target_chat, send_err)
            except Exception as e:
                logger.error("Lỗi khi tự động tạo báo cáo tuần: %s", e)

    async def _check_repo_security_alerts(self, bot: Optional[Any] = None) -> None:
        """Periodically notify Admin Zalo recipients when post-clone repo review finds issues."""
        if bot is None:
            return
        try:
            await self.repo_alert_monitor.check_and_notify(bot)
        except Exception as exc:
            logger.warning("Lỗi kiểm tra cảnh báo kiểm duyệt repo sau clone: %s", exc)

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
