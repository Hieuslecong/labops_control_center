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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.services.zalo_assistant.service import zalo_assistant_service

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
                    await bot.send_message(chat_id=chat_id, text=result["reply"])
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
                await bot.send_message(chat_id=chat_id, text=reply_text)
            except Exception as link_send_err:
                logger.warning("Lỗi gửi xác nhận /link tới %s: %s", chat_id, link_send_err)
            return result

        # Duplicate idempotency protection: NEVER resend over transport if already SENT or DELIVERY_UNKNOWN
        if msg_id and self.quota_ledger.is_already_dispatched(msg_id):
            logger.info("⏭️ Bỏ qua gửi trùng idempotency cho msg_id=%s tới chat %s", msg_id, chat_id)
            return {**result, "deduplicated": True}

        try:
            sent = await bot.send_message(chat_id=chat_id, text=reply_text)
            provider_msg_id = getattr(sent, "message_id", None)
            if msg_id:
                self.quota_ledger.confirm_sent(
                    msg_id,
                    provider_message_id=str(provider_msg_id) if provider_msg_id is not None else "accepted",
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

        offset: Optional[int] = None
        consecutive_errors = 0

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

                await self._check_repo_security_alerts(bot=bot)
                await self._check_infra_telemetry_and_incidents(bot=bot)
                await self._flush_notification_outbox(bot=bot)
                await self._check_daily_report_schedule(bot=bot)
                await self._check_weekly_report_schedule(bot=bot)

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
            sent = await bot.send_message(chat_id=recipient_id, text=text)
            prov_id = getattr(sent, "message_id", None)
            return str(prov_id) if prov_id is not None else "accepted"

        try:
            await self.dispatcher.dispatch_pending(_send_fn, limit=15, subscriptions=subs)
        except Exception as exc:
            logger.warning("Lỗi flush notification outbox: %s", exc)

    async def _check_infra_telemetry_and_incidents(self, bot: Optional[Any] = None) -> None:
        now_mono = time.monotonic()
        if self._last_telemetry_eval_mono > 0 and (now_mono - self._last_telemetry_eval_mono) < 60.0:
            return
        self._last_telemetry_eval_mono = now_mono

        try:
            snap = await asyncio.to_thread(self.telemetry_collector.collect_snapshot)
            await asyncio.to_thread(self.telemetry_collector.record_sample, snap)
            evaluated = await asyncio.to_thread(self.incident_engine.evaluate_snapshot, snap)

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

    async def _check_daily_report_schedule(self, bot: Optional[Any] = None, now_utc: Optional[datetime] = None) -> None:
        """Deliver Daily Executive Briefing once per day at 08:00 Asia/Ho_Chi_Minh (window 08:00-08:59)."""
        ref_utc = now_utc or datetime.now(timezone.utc)
        local_now = ref_utc.astimezone(VN_TZ)
        if local_now.hour != 8:
            return

        period_key = local_now.strftime("%Y-%m-%d")
        existing = self.report_generator.get_daily_report_by_period(period_key)
        if existing is not None:
            dstate = existing.get("delivery_state") or {}
            if dstate.get("enqueued"):
                return
            report = existing
        else:
            report = await asyncio.to_thread(
                self.report_generator.generate_daily_executive_briefing,
                now_utc=ref_utc,
                ai_client=self.service.ai_client,
                telemetry_collector=self.telemetry_collector,
                incident_engine=self.incident_engine,
            )

        recipients = list(
            dict.fromkeys(
                list(self.report_chat_ids)
                + self.identity_rbac.get_verified_admin_recipients(
                    fallback_authorized_users=self.bot_engine.authorized_users,
                    alert_category="daily_report",
                )
            )
        )
        if not recipients:
            return

        summary_msg = report.get("zalo_summary") or report.get("content", "")[:1600]
        for target_chat in recipients:
            self.dispatcher.enqueue(
                idempotency_key=f"daily-report:{period_key}",
                recipient_id=target_chat,
                event_type="DAILY_EXECUTIVE_BRIEF",
                severity="REPORT",
                is_critical=False,
                report_id=report.get("id"),
                message_text=summary_msg,
            )

        self.report_generator.mark_daily_delivery_state(
            period_key,
            {"enqueued": True, "enqueued_at": datetime.now(timezone.utc).isoformat(), "recipients_count": len(recipients)},
        )
        if bot is not None:
            await self._flush_notification_outbox(bot=bot)

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

    async def _check_repo_security_alerts(self, bot: Optional[Any] = None) -> None:
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
