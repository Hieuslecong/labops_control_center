import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ENV", "development")

from app.db.session import DatabaseManager, upgrade_database_schema  # noqa: E402
from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient  # noqa: E402
from app.services.zalo_assistant.bot_core import ZaloBotEngine  # noqa: E402
from app.services.zalo_assistant.identity_rbac import ZaloIdentityRBACService, ZaloPrincipal  # noqa: E402
from app.services.zalo_assistant.incident_engine import IncidentRuleThresholds, UnifiedIncidentEngine  # noqa: E402
from app.services.zalo_assistant.lease_manager import SchedulerLeaseManager  # noqa: E402
from app.services.zalo_assistant.notification_dispatcher import NotificationDispatcher  # noqa: E402
from app.services.zalo_assistant.quota_ledger import QuotaLedger  # noqa: E402
from app.services.zalo_assistant.repo_alert_monitor import RepoSecurityAlertMonitor  # noqa: E402
from app.services.zalo_assistant.report_generator import ReportGenerator  # noqa: E402
from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector  # noqa: E402
from app.services.zalo_assistant.tool_registry import SREAgentRunner, SREToolRegistry  # noqa: E402
from app.services.zalo_assistant.worker import ZaloPollingWorker  # noqa: E402


class _FakeBot:
    def __init__(self, fail_for=None, timeout_for=None):
        self.sent = []
        self.fail_for = set(fail_for or [])
        self.timeout_for = set(timeout_for or [])
        self._seq = 0

    async def send_message(self, *, chat_id: str, text: str):
        if chat_id in self.timeout_for:
            raise TimeoutError("Zalo API request timed out")
        if chat_id in self.fail_for:
            raise RuntimeError("Zalo transport 502 error")
        self._seq += 1
        self.sent.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(message_id=f"zalo-msg-{self._seq}")


def _build_stack(tmp_path: Path, **telemetry_kwargs):
    data_dir = str(tmp_path)
    db = DatabaseManager(data_dir)
    ledger = QuotaLedger(data_dir)
    rbac = ZaloIdentityRBACService(data_dir, db_manager=db)
    telemetry = UnifiedTelemetryCollector(data_dir, db_manager=db, core_service=SimpleNamespace(**telemetry_kwargs) if telemetry_kwargs else None)
    incidents = UnifiedIncidentEngine(data_dir, db_manager=db)
    reports = ReportGenerator(data_dir, db_manager=db)
    ai_client = AIGatewayClient(base_url="http://127.0.0.1:19999/v1", model="model_free", timeout=1)
    registry = SREToolRegistry(
        data_dir,
        db_manager=db,
        telemetry_collector=telemetry,
        incident_engine=incidents,
        report_generator=reports,
    )
    bot_engine = ZaloBotEngine(
        quota_ledger=ledger,
        ai_client=ai_client,
        report_generator=reports,
        config_path=os.path.join(data_dir, "zalo_config.json"),
        identity_rbac=rbac,
        telemetry_collector=telemetry,
        incident_engine=incidents,
        tool_registry=registry,
    )
    return db, ledger, rbac, telemetry, incidents, reports, ai_client, registry, bot_engine


# 1. Database Migration & Rollback Test
def test_alembic_upgrade_and_downgrade_sre_v2(tmp_path: Path):
    db_file = tmp_path / "mig_test.sqlite3"
    db_url = f"sqlite:///{db_file}"
    # Bootstrap 0001+0002 first via alembic upgrade
    cfg = AlembicConfig(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    res = upgrade_database_schema(db_url)
    assert res["current_revision"] == "0005_labops_personal_ai_v4"

    db = DatabaseManager(str(tmp_path), database_url=db_url)
    tables = set(inspect(db.engine).get_table_names())
    for expected in (
        "zalo_identity_links",
        "notification_subscriptions",
        "notification_outbox",
        "notification_delivery_attempts",
        "incidents",
        "incident_events",
        "report_runs",
        "daily_report_snapshots",
        "telemetry_history_samples",
        "agent_tool_audit",
        "scheduler_leases",
    ):
        assert expected in tables

    # Clean rollback to 0002 and re-upgrade to head
    alembic_command.downgrade(cfg, "0002_member_username_reuse")
    tables_after_down = set(inspect(db.engine).get_table_names())
    assert "incidents" not in tables_after_down
    assert "scheduler_leases" not in tables_after_down

    alembic_command.upgrade(cfg, "head")
    tables_after_up = set(inspect(db.engine).get_table_names())
    assert "incidents" in tables_after_up
    assert "scheduler_leases" in tables_after_up


# 2. Identity Linking, Spoofing Prevention, and Strict RBAC
def test_identity_linking_and_rbac_isolation(tmp_path: Path):
    db, ledger, rbac, telemetry, incidents, reports, ai_client, registry, bot_engine = _build_stack(tmp_path)

    # Spoof attempt with invalid code is rejected
    bad_claim = rbac.claim_link_code(zalo_user_id="zalo-evil", code="LAB-FAKE00")
    assert bad_claim["success"] is False

    # Admin generates valid one-time code for researcher 'tranglt'
    code_info = rbac.generate_link_code(username="tranglt", member_id="mem-trang", role="RESEARCHER", actor="admin")
    good_claim = rbac.claim_link_code(zalo_user_id="zalo-trang", code=code_info["code"], display_name="Trang")
    assert good_claim["success"] is True
    assert good_claim["role"] == "RESEARCHER"

    # Reusing same one-time code fails
    reuse_claim = rbac.claim_link_code(zalo_user_id="zalo-other", code=code_info["code"])
    assert reuse_claim["success"] is False

    # Bind Admin account
    rbac.admin_bind_identity(zalo_user_id="zalo-admin", username="admin", member_id="mem-admin", role="ADMIN", admin_actor="admin")

    # Even if zalo-trang is in legacy allowlist, active RBAC link keeps role strictly RESEARCHER and excludes from Admin alerts!
    bot_engine.authorized_users = ["zalo-admin", "zalo-trang"]
    admin_recips = rbac.get_verified_admin_recipients(fallback_authorized_users=bot_engine.authorized_users)
    assert admin_recips == ["zalo-admin"]
    assert "zalo-trang" not in admin_recips

    # Create incidents for two owners + jobs for two owners
    incidents.upsert_incident(
        event_type="DOCKER_OOM_KILLED",
        severity="P1",
        source="docker",
        resource_id="ws-vinh",
        owner_id="vinhnq",
        fingerprint="oom-vinh",
        title="OOM in vinhnq workspace",
    )
    inc_trang, _ = incidents.upsert_incident(
        event_type="DOCKER_OOM_KILLED",
        severity="P1",
        source="docker",
        resource_id="ws-tranglt",
        owner_id="tranglt",
        fingerprint="oom-trang",
        title="OOM in tranglt workspace",
    )

    # Researcher /security -> forbidden
    res_sec = bot_engine.handle_command("/security", sender_id="zalo-trang", source="zalo")
    assert res_sec["success"] is False
    assert res_sec["error"] == "FORBIDDEN"

    # Researcher /incidents -> only sees own incident, NEVER vinhnq's incident
    res_inc = bot_engine.handle_command("/incidents", sender_id="zalo-trang", source="zalo")
    assert res_inc["success"] is True
    assert "ws-tranglt" in res_inc["reply"]
    assert "ws-vinh" not in res_inc["reply"]

    # Researcher trying to inspect/ack vinhnq's incident is denied
    vinh_inc = incidents.list_incidents(owner_id="vinhnq")[0]
    res_vinh = bot_engine.handle_command(f"/incident {vinh_inc['incident_id']}", sender_id="zalo-trang", source="zalo")
    assert "không có quyền" in res_vinh["reply"]

    # Researcher can ack their own incident
    res_ack = bot_engine.handle_command(f"/ack {inc_trang['incident_id']}", sender_id="zalo-trang", source="zalo")
    assert "ACKNOWLEDGED" in res_ack["reply"]


# 3. Outbox Idempotency, Timeout DELIVERY_UNKNOWN & Worker DB Lease
def test_outbox_idempotency_timeout_and_db_lease(tmp_path: Path):
    db, ledger, rbac, telemetry, incidents, reports, ai_client, registry, bot_engine = _build_stack(tmp_path)
    rbac.admin_bind_identity(zalo_user_id="zalo-admin", username="admin", member_id="m1", role="ADMIN")
    lease_mgr = SchedulerLeaseManager(str(tmp_path), db_manager=db)

    # DB lease prevents split-brain second worker while active, allows failover once expired/released
    assert lease_mgr.acquire_or_renew("zalo_sre_worker", "worker-1", ttl_seconds=30) is True
    assert lease_mgr.acquire_or_renew("zalo_sre_worker", "worker-2", ttl_seconds=30) is False
    assert lease_mgr.release("zalo_sre_worker", "worker-1") is True
    assert lease_mgr.acquire_or_renew("zalo_sre_worker", "worker-2", ttl_seconds=30) is True

    # Worker idempotency prevents duplicate transport send & timeout marks DELIVERY_UNKNOWN without blind refund
    svc_stub = SimpleNamespace(
        data_dir=str(tmp_path),
        bot_engine=bot_engine,
        quota_ledger=ledger,
        report_generator=reports,
        lease_manager=lease_mgr,
        identity_rbac=rbac,
        telemetry_collector=telemetry,
        incident_engine=incidents,
        notification_dispatcher=NotificationDispatcher(str(tmp_path), quota_ledger=ledger, db_manager=db),
        repo_alert_monitor=RepoSecurityAlertMonitor(str(tmp_path), ledger, bot_engine, identity_rbac=rbac, incident_engine=incidents),
        ai_client=ai_client,
    )
    worker = ZaloPollingWorker(service_instance=svc_stub)

    ok_bot = _FakeBot()
    r1 = asyncio.run(
        worker.process_incoming_message(ok_bot, raw_text="/quota", chat_id="zalo-admin", sender_id="zalo-admin", message_id="m-100")
    )
    assert r1["success"] is True
    assert len(ok_bot.sent) == 1

    # Replay same message_id -> MUST NOT re-send on transport!
    r2 = asyncio.run(
        worker.process_incoming_message(ok_bot, raw_text="/quota", chat_id="zalo-admin", sender_id="zalo-admin", message_id="m-100")
    )
    assert r2.get("deduplicated") is True
    assert len(ok_bot.sent) == 1

    # Timeout -> marks DELIVERY_UNKNOWN and preserves quota deduction (does not refund)
    before_sent = ledger.get_status()["standard_sent"]
    timeout_bot = _FakeBot(timeout_for={"zalo-admin"})
    r3 = asyncio.run(
        worker.process_incoming_message(timeout_bot, raw_text="/quota", chat_id="zalo-admin", sender_id="zalo-admin", message_id="m-timeout")
    )
    assert r3["error"] == "DELIVERY_UNKNOWN"
    assert r3["refunded"] is False
    assert ledger.get_status()["standard_sent"] == before_sent + 1


# 4. Multi-Admin Repo Security Alert Partial Failure Retry & Admin-Only Enforcement
def test_repo_alerts_per_recipient_retry_and_incident_creation(tmp_path: Path):
    db, ledger, rbac, telemetry, incidents, reports, ai_client, registry, bot_engine = _build_stack(tmp_path)
    rbac.admin_bind_identity(zalo_user_id="admin-A", username="adminA", member_id="mA", role="ADMIN")
    rbac.admin_bind_identity(zalo_user_id="admin-B", username="adminB", member_id="mB", role="ADMIN")
    rbac.admin_bind_identity(zalo_user_id="res-C", username="resC", member_id="mC", role="RESEARCHER")

    now_iso = datetime.now(timezone.utc).isoformat()
    sample_reqs = [
        {
            "id": "req-repo-alert-1",
            "owner": "TrangLe1912",
            "repo_name": "risk-constrained-endpointing",
            "commit_sha": "401ad754616072d26d92ef3548398bef2e47fbb5",
            "submitted_by": "tranglt",
            "target_workspace": "ws-tranglt",
            "source": "workspace_clone",
            "clone_status": "CLONED",
            "review_status": "REVIEW_REQUIRED",
            "state": "AUTO_APPROVED",
            "submitted_at": now_iso,
            "updated_at": now_iso,
            "report": {
                "secrets_count": 1,
                "vulnerabilities": {"critical": 0, "high": 2, "medium": 0, "low": 0},
                "misconfig_high_count": 0,
                "coverage": {"overall_coverage": "POLICY_COMPLETE", "submodules": "NOT_PRESENT", "lfs": "NOT_PRESENT"},
                "findings": [
                    {"id": "generic-api-key", "type": "SECRET", "severity": "HIGH", "target_file": "secret.py:2", "masked_value": "DO_NOT_LEAK"}
                ],
            },
        }
    ]

    monitor = RepoSecurityAlertMonitor(
        str(tmp_path),
        ledger,
        bot_engine,
        fetch_requests_fn=lambda: sample_reqs,
        identity_rbac=rbac,
        incident_engine=incidents,
    )

    # First attempt: admin-B fails, admin-A succeeds, res-C must never be targeted
    partial_bot = _FakeBot(fail_for={"admin-B"})
    res1 = asyncio.run(monitor.check_and_notify(partial_bot, force=True))
    assert res1["sent"] == 1
    assert [m["chat_id"] for m in partial_bot.sent] == ["admin-A"]
    assert "DO_NOT_LEAK" not in partial_bot.sent[0]["text"]
    assert "localhost" not in partial_bot.sent[0]["text"]

    # Incident was created
    open_incs = incidents.list_incidents(source="repository_guard")
    assert len(open_incs) == 1
    assert open_incs[0]["event_type"] == "REPO_SECURITY_FINDINGS"

    # Second attempt after admin-B recovers: ONLY admin-B receives retry; admin-A is NOT spammed again!
    recovered_bot = _FakeBot()
    res2 = asyncio.run(monitor.check_and_notify(recovered_bot, force=True))
    assert res2["sent"] == 1
    assert [m["chat_id"] for m in recovered_bot.sent] == ["admin-B"]


# 5. Telemetry Freshness (Rack ONLINE / Stale) & No False P0 on Normal High GPU Training
def test_telemetry_freshness_and_incident_rules(tmp_path: Path):
    now_iso = datetime.now(timezone.utc).isoformat()
    stale_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    db, ledger, rbac, telemetry, incidents, reports, ai_client, registry, bot_engine = _build_stack(tmp_path)
    incidents.thresholds = IncidentRuleThresholds(hysteresis_samples=2, disk_warn_percent=85.0, disk_crit_percent=95.0)

    # Snapshot 1: Rack IS ONLINE, Workstation has 99% GPU util & 96% VRAM (normal training!) + WS OOM
    snap1 = telemetry.collect_snapshot(
        servers_override=[
            {
                "id": "track",
                "hostname": "aiotlab-rack",
                "name": "Server 01 — Rack",
                "status": "ONLINE",
                "collected_at": now_iso,
                "last_heartbeat_at": now_iso,
                "freshness_status": "ONLINE",
                "telemetry_source": "gateway_mtls",
                "cpu_usage_percent": 40.0,
                "ram_used_gb": 32.0,
                "ram_total_gb": 128.0,
                "disk_usage_percent": 87.0,
                "disk_used_tb": 8.7,
                "disk_total_tb": 10.0,
            },
            {
                "id": "workstation",
                "hostname": "aiot-ws1",
                "name": "Server 02 — Workstation",
                "status": "ONLINE",
                "collected_at": stale_iso,
                "last_heartbeat_at": stale_iso,
                "telemetry_source": "go_node_agent",
                "cpu_usage_percent": 50.0,
                "ram_used_gb": 20.0,
                "ram_total_gb": 64.0,
                "disk_usage_percent": 40.0,
            },
        ],
        gpus_override=[
            {
                "id": "ws-gpu-0",
                "server_id": "track",
                "uuid": "GPU-AAA-111",
                "name": "NVIDIA A30",
                "gpu_util_percent": 99,
                "vram_used_mb": 23500,
                "vram_total_mb": 24576,
                "temp_celsius": 72,
            }
        ],
        workspaces_override=[
            {"id": "ws-1", "name": "ws-tranglt", "owner": "tranglt", "host_id": "track", "oom_killed": True, "restart_count": 0}
        ],
        jobs_override=[],
        services_override={"postgresql": {"installed": True, "accepting_connections": True}},
    )

    host_map = {h["host_id"]: h for h in snap1["hosts"]}
    assert host_map["track"]["status"] == "ONLINE"  # Rack dynamically shows ONLINE!
    assert host_map["workstation"]["status"] == "OFFLINE"  # Stale/expired hb (>5m) accurately flagged OFFLINE!
    assert snap1["gpus"][0]["gpu_key"] == "track:GPU-AAA-111"

    incidents.evaluate_snapshot(snap1)
    # Normal high GPU utilization/VRAM (99%, 72°C) MUST NOT trigger GPU incident
    gpu_incs = incidents.list_incidents(source="gpu")
    assert len(gpu_incs) == 0

    # OOM incident created for owner tranglt
    docker_incs = incidents.list_incidents(source="docker")
    assert len(docker_incs) == 1
    assert docker_incs[0]["owner_id"] == "tranglt"

    # Hysteresis sample 2 triggers sustained disk > 85% incident once, and doesn't duplicate OOM incident
    incidents.evaluate_snapshot(snap1)
    assert len(incidents.list_incidents(source="storage")) == 1
    assert len(incidents.list_incidents(source="docker")) == 1


# 6. Daily Executive Briefing (08:00 ICT Window, Missing History N/A, AI Offline Fallback, Idempotency)
def test_daily_executive_briefing_deterministic_and_idempotent(tmp_path: Path):
    db, ledger, rbac, telemetry, incidents, reports, ai_client, registry, bot_engine = _build_stack(tmp_path)

    # 08:05 Asia/Ho_Chi_Minh on 2026-11-23 == 01:05 UTC
    target_utc = datetime(2026, 11, 23, 1, 5, tzinfo=timezone.utc)
    # Explicitly test with empty historical samples -> must output N/A for 24h peak/avg and use deterministic fallback when AI gateway is unreachable
    rep1 = reports.generate_daily_executive_briefing(
        now_utc=target_utc,
        ai_client=ai_client,
        telemetry_collector=telemetry,
        incident_engine=incidents,
        repo_requests=[],
    )
    assert rep1["period_key"] == "2026-11-23"
    assert len(rep1["zalo_summary"]) < 1800
    assert "localhost" not in rep1["zalo_summary"].lower()
    assert "N/A" in rep1["content"]
    assert "## 6. AI Operational Insights" in rep1["content"]
    assert "## 7. Today's Priorities" in rep1["content"]

    # Calling again for same period without force=True must return existing run (no duplicate)
    rep2 = reports.generate_daily_executive_briefing(
        now_utc=target_utc,
        telemetry_collector=telemetry,
        incident_engine=incidents,
        repo_requests=[],
    )
    assert rep2["id"] == rep1["id"]


# 7. Real AI Agent Tool Calling, Timeout Protection, and Prompt Injection Defense
def test_ai_agent_tool_calling_rbac_and_timeout(tmp_path: Path):
    db, ledger, rbac, telemetry, incidents, reports, ai_client, registry, bot_engine = _build_stack(tmp_path)

    # Simulate slow/hanging custom tool exceeding timeout
    def _slow_tool(args, principal):
        time.sleep(0.5)
        return {"ok": True}

    fast_timeout_registry = SREToolRegistry(
        str(tmp_path),
        db_manager=db,
        telemetry_collector=telemetry,
        incident_engine=incidents,
        report_generator=reports,
        custom_handlers={"get_service_health": _slow_tool},
        tool_timeout_seconds=0.05,
    )

    researcher = ZaloPrincipal("z-res", "tranglt", "m-tr", "RESEARCHER", "Trang", True, True)
    admin = ZaloPrincipal("z-adm", "admin", "m-ad", "ADMIN", "Admin", True, True)

    # RBAC at tool layer denies researcher from get_security_events
    denied = fast_timeout_registry.execute_tool("get_security_events", {}, researcher)
    assert denied["error"] == "PERMISSION_DENIED"

    # Timeout handled cleanly
    timed_out = fast_timeout_registry.execute_tool("get_service_health", {}, admin)
    assert timed_out["error"] == "TOOL_TIMEOUT"

    # Prompt injection attempt to call shell tool is rejected
    inj_res = fast_timeout_registry.execute_tool("execute_arbitrary_shell", {"cmd": "rm -rf /"}, admin)
    assert inj_res["error"] == "UNKNOWN_TOOL"

    # Native tool calling simulation via fake AI client
    class _FakeToolAI:
        def generate_with_tools(self, messages, *, tools, temperature=0.1, max_tokens=800):
            return {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "get_server_metrics", "arguments": "{}"}},
                    {"function": {"name": "execute_arbitrary_shell", "arguments": "{}"}},
                ],
            }

        def generate_chat_response(self, messages, **kwargs):
            return "Kết luận: Hệ thống bình thường.\nBằng chứng: Kiểm tra get_server_metrics.\nĐộ chắc chắn: Cao.\nHành động: Theo dõi định kỳ."

    runner = SREAgentRunner(registry)
    inv = runner.run_investigation("Tại sao Workstation hôm nay chậm?", admin, ai_client=_FakeToolAI())
    assert len(inv["tool_traces"]) == 2
    assert inv["tool_traces"][0]["tool"] == "get_server_metrics"
    assert inv["tool_traces"][0]["status"] == "OK"
    assert len(inv["evidence_timestamps"]) >= 1
    assert "execute_arbitrary_shell" in inv["tool_traces"][1]["tool"]


# 8. Daily Brief Strict Recipient Verification (Hiếu Lê 0c00ed813eccd7928edd DM Only, No Fallback)
def test_daily_brief_strict_recipient_verification_and_no_fallback(tmp_path: Path):
    db, ledger, rbac, telemetry, incidents, reports, ai_client, registry, bot_engine = _build_stack(tmp_path)
    bot_engine.authorized_users = ["legacy-user-1", "researcher-1"]
    rbac.admin_bind_identity(zalo_user_id="researcher-1", username="tranglt", member_id="m-tr", role="RESEARCHER")

    svc_stub = SimpleNamespace(
        data_dir=str(tmp_path),
        bot_engine=bot_engine,
        quota_ledger=ledger,
        report_generator=reports,
        lease_manager=SchedulerLeaseManager(str(tmp_path), db_manager=db),
        identity_rbac=rbac,
        telemetry_collector=telemetry,
        incident_engine=incidents,
        notification_dispatcher=NotificationDispatcher(str(tmp_path), quota_ledger=ledger, db_manager=db),
        repo_alert_monitor=RepoSecurityAlertMonitor(str(tmp_path), ledger, bot_engine, identity_rbac=rbac, incident_engine=incidents),
        ai_client=ai_client,
    )
    worker = ZaloPollingWorker(service_instance=svc_stub)
    worker.report_chat_ids = ["group-ops-1"]
    bot = _FakeBot()

    # Before 0c00ed813eccd7928edd is verified -> MUST NOT send to anyone or fallback!
    utc_0800 = datetime(2026, 11, 23, 1, 0, tzinfo=timezone.utc)
    asyncio.run(worker._check_daily_report_schedule(bot=bot, now_utc=utc_0800))
    assert len(bot.sent) == 0
    rep = reports.get_daily_report_by_period("2026-11-23")
    assert rep is not None
    assert rep["status"] == "RECIPIENT_NOT_VERIFIED"

    # Group chat interaction must be rejected for private chat verification
    assert rbac.record_observed_private_chat("0c00ed813eccd7928edd", "group-chat-999", "Hiếu Lê") is False
    assert rbac.verify_daily_brief_recipient()["verified"] is False

    # Valid 1-1 DM interaction + ADMIN link -> verified & delivers ONLY to 0c00ed813eccd7928edd
    rbac.record_observed_private_chat("0c00ed813eccd7928edd", "0c00ed813eccd7928edd", "Hiếu Lê")
    rbac.admin_bind_identity(
        zalo_user_id="0c00ed813eccd7928edd",
        username="admin",
        member_id="mem-admin",
        role="ADMIN",
        display_name="Hiếu Lê",
    )
    ver = rbac.verify_daily_brief_recipient()
    assert ver["verified"] is True
    assert ver["chat_id"] == "0c00ed813eccd7928edd"

    asyncio.run(worker._check_daily_report_schedule(bot=bot, now_utc=utc_0800))
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == "0c00ed813eccd7928edd"
    assert "Hiếu Lê | Admin" in bot.sent[0]["text"]
    assert "group-ops-1" not in [m["chat_id"] for m in bot.sent]
    assert "legacy-user-1" not in [m["chat_id"] for m in bot.sent]

    # Re-running schedule at 08:05 / after worker restart NEVER sends duplicate
    worker2 = ZaloPollingWorker(service_instance=svc_stub)
    asyncio.run(worker2._check_daily_report_schedule(bot=bot, now_utc=datetime(2026, 11, 23, 1, 5, tzinfo=timezone.utc)))
    assert len(bot.sent) == 1


# 9. Schedule Window, Restart Catch-Up, Missed Window Alert & Timeout DELIVERY_UNKNOWN
def test_daily_brief_catchup_missed_window_and_timeout_delivery_unknown(tmp_path: Path):
    db, ledger, rbac, telemetry, incidents, reports, ai_client, registry, bot_engine = _build_stack(tmp_path)
    rbac.bind_daily_brief_admin(
        zalo_user_id="0c00ed813eccd7928edd",
        username="admin",
        verified_chat_id="0c00ed813eccd7928edd",
    )

    svc_stub = SimpleNamespace(
        data_dir=str(tmp_path),
        bot_engine=bot_engine,
        quota_ledger=ledger,
        report_generator=reports,
        lease_manager=SchedulerLeaseManager(str(tmp_path), db_manager=db),
        identity_rbac=rbac,
        telemetry_collector=telemetry,
        incident_engine=incidents,
        notification_dispatcher=NotificationDispatcher(str(tmp_path), quota_ledger=ledger, db_manager=db),
        repo_alert_monitor=RepoSecurityAlertMonitor(str(tmp_path), ledger, bot_engine, identity_rbac=rbac, incident_engine=incidents),
        ai_client=ai_client,
    )
    worker = ZaloPollingWorker(service_instance=svc_stub)

    # Restart catch-up at 10:30 ICT (03:30 UTC, within 4h catch-up window), with provider timeout
    timeout_bot = _FakeBot(timeout_for={"0c00ed813eccd7928edd"})
    asyncio.run(
        worker._check_daily_report_schedule(
            bot=timeout_bot,
            now_utc=datetime(2026, 11, 23, 3, 30, tzinfo=timezone.utc),
        )
    )
    rep = reports.get_daily_report_by_period("2026-11-23")
    assert rep is not None
    assert rep["status"] == "DELIVERY_UNKNOWN"
    assert rep["delivery_state"].get("is_late_catchup") is True
    assert "Gửi bù sau khởi động" in rep["zalo_summary"]

    # After DELIVERY_UNKNOWN, healthy bot on next tick MUST NOT blindly re-send!
    ok_bot = _FakeBot()
    asyncio.run(
        worker._check_daily_report_schedule(
            bot=ok_bot,
            now_utc=datetime(2026, 11, 23, 3, 35, tzinfo=timezone.utc),
        )
    )
    assert len(ok_bot.sent) == 0

    # Missed window (>4h past 08:00 without any run on next day 14:00 ICT = 07:00 UTC) -> MISSED_WINDOW + P1 Incident
    asyncio.run(
        worker._check_daily_report_schedule(
            bot=ok_bot,
            now_utc=datetime(2026, 11, 24, 7, 0, tzinfo=timezone.utc),
        )
    )
    missed = reports.get_daily_report_by_period("2026-11-24")
    assert missed is not None
    assert missed["status"] == "MISSED_WINDOW"
    assert any(i.get("event_type") == "DAILY_BRIEF_MISSED_WINDOW" for i in incidents.list_incidents())


# 10. Collector Down N/A + Partition/Job Dedup + Empty Priorities Phrase
def test_daily_brief_metrics_dedup_collector_down_and_empty_priorities(tmp_path: Path):
    db, ledger, rbac, telemetry, incidents, reports, ai_client, registry, bot_engine = _build_stack(tmp_path)

    # Repo collector unreachable -> must report N/A (not 0) in Security section
    rep = reports.generate_daily_executive_briefing(
        now_utc=datetime(2026, 11, 23, 1, 0, tzinfo=timezone.utc),
        ai_client=None,
        telemetry_collector=telemetry,
        incident_engine=incidents,
        repo_requests=[],
        repo_collector_ok=False,
        incident_collector_ok=False,
    )
    assert "Repo mới clone: N/A" in rep["zalo_summary"]
    assert "Sự cố đang mở: N/A" in rep["zalo_summary"]
    assert "LABOPS | DAILY EXECUTIVE BRIEF" in rep["zalo_summary"]
    assert len(rep["zalo_summary"]) < 2000
