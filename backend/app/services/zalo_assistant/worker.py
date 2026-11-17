"""
Zalo Bot Polling Worker & Background Scheduler
Listens for incoming messages from Zalo Bot API, processes commands (/status, /jobs, /report, /quota, /ask),
calls 9Router AI Gateway for natural language inquiries, enforces quota ledger (3,000 msgs/month),
and handles scheduled weekly report notifications.
"""
from __future__ import annotations

import asyncio
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

from app.services.zalo_assistant.service import zalo_assistant_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (ZaloWorker) %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("zalo_worker")


class ZaloPollingWorker:
    def __init__(self):
        self.service = zalo_assistant_service
        self.bot_engine = self.service.bot_engine
        self.quota_ledger = self.service.quota_ledger
        self.report_generator = self.service.report_generator
        self.bot_token = os.getenv("ZALO_BOT_TOKEN") or self.bot_engine.bot_token
        self.is_running = False
        self.pid_file = os.path.join(self.service.data_dir, "zalo_worker.pid")
        self.last_reported_week = None

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

        self.is_running = True
        logger.info(f"🚀 Khởi động Zalo Polling Worker (PID: {os.getpid()})")
        logger.info(f"   • Model AI: {self.service.ai_client.model} ({self.service.ai_client.base_url})")
        logger.info(f"   • Hạn mức: {self.quota_ledger.TOTAL_MONTHLY_LIMIT} tin/tháng (2800 std / 200 crit)")

        try:
            from zalo_bot import Bot
        except ImportError:
            logger.error("❌ Thiếu thư viện python-zalo-bot. Cài đặt qua: pip install python-zalo-bot")
            self._release_lease()
            return

        bot = Bot(token=self.bot_token)
        try:
            me = await bot.get_me()
            logger.info(f"✅ Đã kết nối Zalo Bot Platform: {me.display_name} (ID: {me.id}, Type: {me.account_type})")
        except Exception as e:
            logger.error(f"❌ Không thể xác thực Token Zalo: {e}")
            self._release_lease()
            return

        offset: Optional[int] = None
        consecutive_errors = 0

        while self.is_running:
            try:
                # Long polling with 15s timeout
                updates = await bot.get_update(offset=offset, timeout=15, limit=20)
                consecutive_errors = 0

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

                    raw_text = getattr(message, "text", "") or ""
                    chat = getattr(message, "chat", None)
                    chat_id = str(getattr(chat, "id", "")) if chat else ""
                    from_user = getattr(message, "from_user", None)
                    sender_id = str(getattr(from_user, "id", chat_id)) if from_user else chat_id
                    sender_name = str(getattr(from_user, "display_name", "User")) if from_user else "User"

                    if not raw_text.strip() or not chat_id:
                        continue

                    logger.info(f"📩 Nhận tin nhắn từ '{sender_name}' ({sender_id}) trong chat {chat_id}: {raw_text[:50]}")

                    # Process command or query through Bot Engine
                    try:
                        result = self.bot_engine.handle_command(
                            command_text=raw_text,
                            sender_id=sender_id,
                            idempotency_key=f"zalo-msg-{getattr(message, 'message_id', os.urandom(4).hex())}",
                        )
                        reply_text = result.get("reply", "")
                        if reply_text:
                            # Send response back to chat/group
                            await bot.send_message(chat_id=chat_id, text=reply_text)
                            logger.info(f"📤 Đã gửi phản hồi ({result.get('command')}) tới chat {chat_id} (Dung lượng: {len(reply_text)} chars)")
                    except Exception as handle_err:
                        logger.error(f"Lỗi khi xử lý phản hồi lệnh: {handle_err}")

                # Check scheduled weekly report (Friday 17:00 Asia/Ho_Chi_Minh)
                self._check_weekly_report_schedule()

                # Yield control briefly
                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                logger.info("Zalo Polling Worker nhận lệnh dừng.")
                break
            except Exception as e:
                consecutive_errors += 1
                backoff = min(30, 2 ** consecutive_errors)
                logger.warning(f"Lỗi polling (lần {consecutive_errors}), thử lại sau {backoff}s: {e}")
                await asyncio.sleep(backoff)

        self._release_lease()
        logger.info("🛑 Zalo Polling Worker đã dừng an toàn.")

    def _check_weekly_report_schedule(self):
        """Check if weekly report needs to be generated and broadcasted."""
        now = datetime.now(timezone.utc)
        # GMT+7: weekday 4 is Friday. 17:00 ICT is 10:00 UTC.
        current_week_str = now.strftime("%Y-W%W")
        if now.weekday() == 4 and now.hour == 10 and self.last_reported_week != current_week_str:
            logger.info("⏰ Đến lịch định kỳ: Tự động tổng hợp báo cáo tuần LabOps...")
            try:
                rep = self.report_generator.generate_weekly_report(author="Automated Weekly Cron")
                self.last_reported_week = current_week_str
                logger.info(f"✅ Đã tạo báo cáo tuần: {rep['title']}")
            except Exception as e:
                logger.error(f"Lỗi khi tự động tạo báo cáo tuần: {e}")

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
