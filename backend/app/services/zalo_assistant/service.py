"""
Unified Zalo Assistant & AI Gateway Service Facade
Manages configuration, quota ledger, 9Router client, report generator, and bot engine.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
from app.services.zalo_assistant.bot_core import ZaloBotEngine
from app.services.zalo_assistant.quota_ledger import QuotaLedger
from app.services.zalo_assistant.report_generator import ReportGenerator


class ZaloAssistantService:
    def __init__(self, data_dir: Optional[str] = None):
        base_dir = data_dir or os.getenv("LABOPS_DATA_DIR") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
        )
        self.data_dir = base_dir
        self.quota_ledger = QuotaLedger(self.data_dir)
        self.ai_client = AIGatewayClient()
        self.report_generator = ReportGenerator(self.data_dir)
        self.bot_engine = ZaloBotEngine(
            quota_ledger=self.quota_ledger,
            ai_client=self.ai_client,
            report_generator=self.report_generator,
        )

    def _build_dynamic_alerts(self, ai_health: Dict[str, Any]) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []
        try:
            from datetime import datetime, timezone
            from app.services.labops_service import service as core_svc

            now_iso = datetime.now(timezone.utc).isoformat()
            for srv in getattr(core_svc, "servers", []) or []:
                status = str(srv.get("status", "UNKNOWN")).upper()
                name = srv.get("name") or srv.get("hostname") or srv.get("id")
                if status in {"OFFLINE", "DISCONNECTED", "UNREACHABLE"}:
                    alerts.append({
                        "id": f"srv-{srv.get('id', 'node')}",
                        "level": "CRITICAL",
                        "message": f"Máy chủ {name} ({srv.get('hostname', '')}) hiện {status}.",
                        "timestamp": srv.get("last_updated") or now_iso,
                    })
                elif status == "ONLINE":
                    alerts.append({
                        "id": f"srv-{srv.get('id', 'node')}",
                        "level": "INFO",
                        "message": f"Máy chủ {name} ({srv.get('hostname', '')}) đang hoạt động ONLINE.",
                        "timestamp": srv.get("last_updated") or now_iso,
                    })
            for ev in (getattr(core_svc, "events", []) or [])[:5]:
                ev_msg = str(ev.get("message") or ev.get("title") or ev.get("description") or "").strip()
                if ev_msg:
                    alerts.append({
                        "id": str(ev.get("id", f"ev-{len(alerts)}")),
                        "level": str(ev.get("severity", ev.get("level", "INFO"))).upper(),
                        "message": ev_msg,
                        "timestamp": ev.get("timestamp") or ev.get("date") or now_iso,
                    })
        except Exception:
            pass

        from datetime import datetime, timezone
        alerts.append({
            "id": "ai-gateway-health",
            "level": "INFO" if ai_health.get("status") == "ONLINE" else "WARNING",
            "message": f"AI Gateway: {ai_health.get('status')} sẵn sàng kết nối.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return alerts[:10]

    def get_overview(self) -> Dict[str, Any]:
        quota = self.quota_ledger.get_status()
        ai_health = self.ai_client.check_health()
        consumer = self.bot_engine.get_consumer_status()
        reports = self.report_generator.list_reports()

        return {
            "zalo_status": "READY" if consumer["has_token"] else "STANDBY (Chưa nạp Token)",
            "consumer": consumer,
            "quota": quota,
            "ai_gateway": ai_health,
            "active_model": self.ai_client.model,
            "reports_count": len(reports),
            "latest_report": reports[0] if reports else None,
            "recent_alerts": self._build_dynamic_alerts(ai_health),
        }

    def get_config(self) -> Dict[str, Any]:
        return {
            "zalo_token_configured": bool(self.bot_engine.bot_token),
            "zalo_token_masked": (
                f"{self.bot_engine.bot_token[:6]}...{self.bot_engine.bot_token[-4:]}"
                if len(self.bot_engine.bot_token) > 10
                else "Chưa cấu hình"
            ),
            "authorized_users": self.bot_engine.authorized_users,
            "allowed_chat_ids": self.bot_engine.allowed_chat_ids,
            "ai_model": self.ai_client.model,
            "ai_gateway_url": self.ai_client.base_url,
            "timeout_seconds": self.ai_client.timeout,
            "monthly_quota_limit": QuotaLedger.TOTAL_MONTHLY_LIMIT,
            "standard_quota_limit": QuotaLedger.STANDARD_LIMIT,
            "critical_reserve": QuotaLedger.CRITICAL_ALERT_RESERVE,
            "allowed_features": ["/start", "/help", "/ask", "/status", "/alloc", "/jobs", "/report", "/quota", "/gpu"],
            "alert_thresholds": {
                "gpu_temp_celsius": 90,
                "vram_used_percent": 95,
                "disk_free_gb": 50,
            },
        }

    def update_config(self, new_config: Dict[str, Any]) -> Dict[str, Any]:
        if "authorized_users" in new_config:
            users = new_config["authorized_users"]
            if not isinstance(users, list):
                raise ValueError("authorized_users phải là danh sách định danh người dùng.")
            cleaned_users = [str(u).strip() for u in users if str(u).strip()]
            if "*" in cleaned_users:
                raise ValueError("Wildcard '*' bị cấm trong danh sách người dùng Zalo được ủy quyền.")
        else:
            cleaned_users = None

        if "allowed_chat_ids" in new_config:
            chats = new_config["allowed_chat_ids"]
            if not isinstance(chats, list):
                raise ValueError("allowed_chat_ids phải là danh sách chat_id được phép.")
            cleaned_chats = [str(c).strip() for c in chats if str(c).strip()]
            if "*" in cleaned_chats:
                raise ValueError("Wildcard '*' bị cấm trong danh sách nhóm/chat Zalo được phép.")
        else:
            cleaned_chats = None

        validated_timeout: Optional[int] = None
        if "timeout_seconds" in new_config and new_config["timeout_seconds"] is not None:
            try:
                validated_timeout = int(new_config["timeout_seconds"])
            except (ValueError, TypeError) as exc:
                raise ValueError("timeout_seconds phải nằm trong khoảng 1..120 giây.") from exc
            if validated_timeout < 1 or validated_timeout > 120:
                raise ValueError("timeout_seconds phải nằm trong khoảng 1..120 giây.")

        if "bot_token" in new_config and new_config["bot_token"]:
            token = str(new_config["bot_token"]).strip()
            self.bot_engine.bot_token = token
            os.environ["ZALO_BOT_TOKEN"] = token

        if cleaned_users is not None:
            self.bot_engine.authorized_users = cleaned_users

        if cleaned_chats is not None:
            self.bot_engine.allowed_chat_ids = cleaned_chats

        if "ai_model" in new_config and new_config["ai_model"]:
            self.ai_client.model = str(new_config["ai_model"]).strip()

        if validated_timeout is not None:
            self.ai_client.timeout = validated_timeout

        return self.get_config()

    def get_tools_and_permissions(self) -> Dict[str, Any]:
        audit_history: List[Dict[str, Any]] = []
        try:
            from app.services.labops_service import service as core_svc

            for a in (getattr(core_svc, "audit_logs", []) or [])[:15]:
                audit_history.append({
                    "action": a.get("action", "AUDIT_EVENT"),
                    "actor": a.get("actor", "system"),
                    "detail": a.get("details") or a.get("detail") or f"{a.get('target_type', '')}:{a.get('target_id', '')}",
                    "timestamp": a.get("timestamp", ""),
                })
        except Exception:
            pass

        return {
            "authorized_users": self.bot_engine.authorized_users,
            "allowed_chat_ids": self.bot_engine.allowed_chat_ids,
            "read_only_tools": self.bot_engine.ENABLED_READ_TOOLS,
            "disabled_write_tools": self.bot_engine.DISABLED_WRITE_TOOLS,
            "pending_approvals": [],
            "audit_history": audit_history,
        }

    def get_history(self) -> Dict[str, Any]:
        quota_status = self.quota_ledger.get_status()
        outbox = self.quota_ledger.get_recent_outbox(50)
        messages = list(reversed(self.bot_engine.message_history))

        return {
            "quota": quota_status,
            "outbox": outbox,
            "recent_messages": messages,
            "daily_breakdown": quota_status.get("daily_usage", {}),
        }


# Singleton instance
zalo_assistant_service = ZaloAssistantService()
