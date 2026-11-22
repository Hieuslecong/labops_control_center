"""
Unified Zalo Assistant & AI SRE Agent v2 Service Facade
Connects QuotaLedger, DatabaseManager, SchedulerLeaseManager, ZaloIdentityRBACService,
UnifiedTelemetryCollector, UnifiedIncidentEngine, NotificationDispatcher,
RepoSecurityAlertMonitor, ReportGenerator, SREToolRegistry, and ZaloBotEngine.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.session import DatabaseManager
from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
from app.services.zalo_assistant.bot_core import ZaloBotEngine
from app.services.zalo_assistant.identity_rbac import ZaloIdentityRBACService
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
from app.services.zalo_assistant.lease_manager import SchedulerLeaseManager
from app.services.zalo_assistant.notification_dispatcher import NotificationDispatcher
from app.services.zalo_assistant.quota_ledger import QuotaLedger
from app.services.zalo_assistant.repo_alert_monitor import RepoSecurityAlertMonitor
from app.services.zalo_assistant.report_generator import ReportGenerator
from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector
from app.services.zalo_assistant.tool_registry import SREToolRegistry


class ZaloAssistantService:
    def __init__(self, data_dir: Optional[str] = None):
        base_dir = data_dir or os.getenv("LABOPS_DATA_DIR") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
        )
        self.data_dir = os.path.abspath(base_dir)
        self.db = DatabaseManager(self.data_dir)
        self.quota_ledger = QuotaLedger(self.data_dir)
        self.ai_client = AIGatewayClient()
        self.lease_manager = SchedulerLeaseManager(self.data_dir, db_manager=self.db)
        self.identity_rbac = ZaloIdentityRBACService(self.data_dir, db_manager=self.db)
        self.telemetry_collector = UnifiedTelemetryCollector(self.data_dir, db_manager=self.db)
        self.incident_engine = UnifiedIncidentEngine(self.data_dir, db_manager=self.db)
        self.report_generator = ReportGenerator(self.data_dir, db_manager=self.db)
        self.notification_dispatcher = NotificationDispatcher(
            self.data_dir,
            quota_ledger=self.quota_ledger,
            db_manager=self.db,
        )
        self.tool_registry = SREToolRegistry(
            self.data_dir,
            db_manager=self.db,
            telemetry_collector=self.telemetry_collector,
            incident_engine=self.incident_engine,
            report_generator=self.report_generator,
        )
        self.bot_engine = ZaloBotEngine(
            quota_ledger=self.quota_ledger,
            ai_client=self.ai_client,
            report_generator=self.report_generator,
            config_path=os.path.join(self.data_dir, "zalo_config.json"),
            identity_rbac=self.identity_rbac,
            telemetry_collector=self.telemetry_collector,
            incident_engine=self.incident_engine,
            tool_registry=self.tool_registry,
        )
        self.repo_alert_monitor = RepoSecurityAlertMonitor(
            data_dir=self.data_dir,
            quota_ledger=self.quota_ledger,
            bot_engine=self.bot_engine,
            identity_rbac=self.identity_rbac,
            incident_engine=self.incident_engine,
        )
        self.bot_engine.reload_persisted_config(force=True)

    def _read_worker_health(self) -> Dict[str, Any]:
        status_file = os.path.join(self.data_dir, "zalo_worker_status.json")
        pid_file = os.path.join(self.data_dir, "zalo_worker.pid")
        data: Dict[str, Any] = {}
        if os.path.exists(status_file):
            try:
                with open(status_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}

        pid = data.get("pid")
        if pid is None and os.path.exists(pid_file):
            try:
                with open(pid_file, "r", encoding="utf-8") as f:
                    pid = int(f.read().strip())
            except Exception:
                pid = None

        pid_alive = False
        if isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)
                pid_alive = True
            except OSError:
                pid_alive = False

        last_hb = data.get("last_heartbeat")
        hb_fresh = False
        if isinstance(last_hb, str) and last_hb.strip():
            try:
                dt = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                hb_fresh = (datetime.now(timezone.utc) - dt).total_seconds() <= 60.0
            except ValueError:
                hb_fresh = False

        lease = self.lease_manager.get_lease_status("zalo_sre_worker")
        lease_active = bool(lease and lease.get("is_active"))
        is_polling = lease_active or (bool(data.get("is_polling")) and pid_alive and hb_fresh)
        return {
            "worker_pid": pid if pid_alive else (lease.get("pid") if lease_active and lease else None),
            "pid_alive": pid_alive,
            "lease": lease,
            "is_polling": is_polling,
            "last_heartbeat": (lease.get("heartbeat_at") if lease_active and lease else last_hb),
            "bot_name": data.get("bot_name"),
            "authorized_users_count": data.get("authorized_users_count"),
            "last_error": data.get("last_error"),
        }

    def get_overview(self) -> Dict[str, Any]:
        self.bot_engine.reload_persisted_config()
        quota = self.quota_ledger.get_status()
        ai_health = self.ai_client.check_health()
        consumer = self.bot_engine.get_consumer_status()
        worker_health = self._read_worker_health()
        if worker_health.get("worker_pid"):
            consumer["consumer_id"] = f"worker-ws-{worker_health['worker_pid']}"
        consumer["is_polling"] = bool(worker_health.get("is_polling") or consumer.get("is_polling"))
        consumer["last_heartbeat"] = worker_health.get("last_heartbeat") or consumer.get("last_heartbeat")
        consumer["worker_pid"] = worker_health.get("worker_pid")
        consumer["lease"] = worker_health.get("lease")
        if worker_health.get("bot_name"):
            consumer["bot_name"] = worker_health["bot_name"]

        links = self.identity_rbac.list_links()
        active_links = [lk for lk in links if lk.get("status") == "ACTIVE"]
        auth_count = len(self.bot_engine.authorized_users or []) + len(active_links)
        consumer["authorized_users_count"] = auth_count

        snap = self.telemetry_collector.collect_snapshot()
        open_incidents = [i for i in self.incident_engine.list_incidents(limit=100) if i.get("status") != "RESOLVED"]
        reports = self.report_generator.list_reports()

        if not consumer["has_token"]:
            zalo_status = "STANDBY (Chưa nạp Token)"
        elif auth_count == 0:
            zalo_status = "MISCONFIGURED (Chưa cấp quyền / Liên kết Zalo ID)"
        elif not consumer["is_polling"]:
            zalo_status = "WORKER_OFFLINE"
        else:
            zalo_status = "READY"

        recent_alerts = [
            {
                "id": inc["incident_id"],
                "level": "CRITICAL" if inc["severity"] == "P0" else ("WARNING" if inc["severity"] == "P1" else "INFO"),
                "message": f"[{inc['severity']}/{inc['status']}] {inc['title']}",
                "timestamp": inc.get("last_observed_at") or inc.get("detected_at"),
            }
            for inc in open_incidents[:8]
        ]
        if not recent_alerts:
            for h in snap.get("hosts") or []:
                if h.get("status") not in {"ONLINE"}:
                    recent_alerts.append(
                        {
                            "id": f"host-{h['host_id']}",
                            "level": "WARNING" if h.get("status") in {"DEGRADED", "STALE"} else "CRITICAL",
                            "message": f"Host {h['hostname']} ({h['host_id']}): {h['status']} (freshness={h['freshness_status']})",
                            "timestamp": h.get("collected_at") or snap.get("collected_at"),
                        }
                    )

        return {
            "zalo_status": zalo_status,
            "consumer": consumer,
            "quota": quota,
            "ai_gateway": ai_health,
            "active_model": self.ai_client.model,
            "reports_count": len(reports),
            "latest_report": reports[0] if reports else None,
            "recent_alerts": recent_alerts,
            "system_health": snap.get("overall_health"),
            "hosts": snap.get("hosts", []),
            "open_incidents_count": len(open_incidents),
            "p0_incidents_count": sum(1 for i in open_incidents if i.get("severity") == "P0"),
            "linked_identities_count": len(active_links),
        }

    def get_heartbeat(self) -> Dict[str, Any]:
        worker_health = self._read_worker_health()
        db_health = self.lease_manager.db_healthcheck()
        snap = self.telemetry_collector.collect_snapshot()
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": db_health,
            "worker": worker_health,
            "overall_health": snap.get("overall_health"),
            "hosts": [
                {
                    "host_id": h["host_id"],
                    "status": h["status"],
                    "freshness_status": h["freshness_status"],
                    "last_heartbeat_at": h["last_heartbeat_at"],
                }
                for h in snap.get("hosts", [])
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
            "allowed_chat_ids": self.bot_engine.allowed_chat_ids,
            "ai_model": self.ai_client.model,
            "ai_gateway_url": self.ai_client.base_url,
            "timeout_seconds": self.ai_client.timeout,
            "monthly_quota_limit": self.quota_ledger.TOTAL_MONTHLY_LIMIT,
            "standard_quota_limit": self.quota_ledger.STANDARD_LIMIT,
            "critical_reserve": self.quota_ledger.CRITICAL_ALERT_RESERVE,
            "allowed_features": list(self.bot_engine.COMMANDS.keys()),
            "alert_thresholds": {
                "gpu_temp_warn_c": self.incident_engine.thresholds.gpu_temp_warn_c,
                "gpu_temp_crit_c": self.incident_engine.thresholds.gpu_temp_crit_c,
                "disk_warn_percent": self.incident_engine.thresholds.disk_warn_percent,
                "disk_crit_percent": self.incident_engine.thresholds.disk_crit_percent,
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

        self.bot_engine.save_persisted_config()
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
            "tool_audit": self.tool_registry.list_recent_tool_audit(30),
            "pending_approvals": [],
            "audit_history": audit_history,
        }

    def get_history(self) -> Dict[str, Any]:
        quota_status = self.quota_ledger.get_status()
        outbox = self.quota_ledger.get_recent_outbox(50)
        db_outbox = self.notification_dispatcher.list_outbox_entries(50)
        attempts = self.notification_dispatcher.list_delivery_attempts(50)
        messages = list(reversed(self.bot_engine.message_history))

        return {
            "quota": quota_status,
            "outbox": outbox,
            "notification_outbox": db_outbox,
            "delivery_attempts": attempts,
            "recent_messages": messages,
            "daily_breakdown": quota_status.get("daily_usage", {}),
        }


zalo_assistant_service = ZaloAssistantService()
