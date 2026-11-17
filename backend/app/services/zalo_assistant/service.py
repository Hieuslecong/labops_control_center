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
        base_dir = data_dir or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
        )
        self.data_dir = base_dir
        self.quota_ledger = QuotaLedger(self.data_dir)
        self.ai_client = AIGatewayClient(model="model_free")
        self.report_generator = ReportGenerator(self.data_dir)
        self.bot_engine = ZaloBotEngine(
            quota_ledger=self.quota_ledger,
            ai_client=self.ai_client,
            report_generator=self.report_generator,
        )

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
            "recent_alerts": [
                {
                    "id": "alt-01",
                    "level": "CRITICAL",
                    "message": "Máy chủ Server Rack (aiotlab-rack) hiện ngoại tuyến. Đang chạy chế độ Workstation Primary.",
                    "timestamp": "2026-11-17T15:00:00+07:00",
                },
                {
                    "id": "alt-02",
                    "level": "INFO",
                    "message": "9Router AI Gateway đã kết nối thành công qua model model_free trên cổng 20128.",
                    "timestamp": "2026-11-17T15:17:00+07:00",
                },
            ],
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
            "ai_model": self.ai_client.model,
            "ai_gateway_url": self.ai_client.base_url,
            "timeout_seconds": self.ai_client.timeout,
            "monthly_quota_limit": QuotaLedger.TOTAL_MONTHLY_LIMIT,
            "standard_quota_limit": QuotaLedger.STANDARD_LIMIT,
            "critical_reserve": QuotaLedger.CRITICAL_ALERT_RESERVE,
            "allowed_features": ["/start", "/help", "/ask", "/status", "/jobs", "/report", "/quota", "/gpu"],
            "alert_thresholds": {
                "gpu_temp_celsius": 90,
                "vram_used_percent": 95,
                "disk_free_gb": 50,
            },
        }

    def update_config(self, new_config: Dict[str, Any]) -> Dict[str, Any]:
        if "bot_token" in new_config and new_config["bot_token"]:
            token = str(new_config["bot_token"]).strip()
            self.bot_engine.bot_token = token
            os.environ["ZALO_BOT_TOKEN"] = token

        if "authorized_users" in new_config:
            users = new_config["authorized_users"]
            if isinstance(users, list):
                self.bot_engine.authorized_users = [str(u).strip() for u in users if u]

        if "ai_model" in new_config and new_config["ai_model"]:
            self.ai_client.model = str(new_config["ai_model"]).strip()

        if "timeout_seconds" in new_config:
            try:
                self.ai_client.timeout = int(new_config["timeout_seconds"])
            except (ValueError, TypeError):
                pass

        return self.get_config()

    def get_tools_and_permissions(self) -> Dict[str, Any]:
        return {
            "authorized_users": self.bot_engine.authorized_users,
            "read_only_tools": self.bot_engine.ENABLED_READ_TOOLS,
            "disabled_write_tools": self.bot_engine.DISABLED_WRITE_TOOLS,
            "pending_approvals": [],
            "audit_history": [
                {
                    "action": "CONFIG_UPDATE",
                    "actor": "admin",
                    "detail": "Kích hoạt Workstation Primary và cô lập mTLS tunnel cũ của rack.",
                    "timestamp": "2026-11-17T15:20:00+07:00",
                },
                {
                    "action": "MODEL_VERIFY",
                    "actor": "system",
                    "detail": "Xác thực mô hình model_free trên 9Router cục bộ (cổng 20128).",
                    "timestamp": "2026-11-17T15:17:45+07:00",
                },
            ],
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
