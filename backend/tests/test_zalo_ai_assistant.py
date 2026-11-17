import os
import shutil
import tempfile
import pytest
from app.services.zalo_assistant.quota_ledger import QuotaLedger, QuotaExceededError
from app.services.zalo_assistant.report_generator import ReportGenerator
from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
from app.services.zalo_assistant.bot_core import ZaloBotEngine


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_quota_ledger_limits(temp_dir):
    ledger = QuotaLedger(temp_dir)
    status = ledger.get_status()
    assert status["total_limit"] == 3000
    assert status["standard_limit"] == 2800
    assert status["critical_reserve"] == 200
    assert status["total_sent"] == 0

    # Reserve 1 standard message
    ok, msg_id, usage = ledger.reserve_and_send("user1", "Hello test")
    assert ok is True
    assert msg_id.startswith("zmsg-")
    assert usage["standard_sent"] == 1
    assert usage["total_sent"] == 1

    # Idempotent replay
    ok2, msg_id2, usage2 = ledger.reserve_and_send("user1", "Hello test", idempotency_key="idemp-1")
    assert ok2 is True
    ok3, msg_id3, usage3 = ledger.reserve_and_send("user1", "Hello test", idempotency_key="idemp-1")
    assert msg_id2 == msg_id3
    assert usage3["total_sent"] == 2


def test_quota_ledger_critical_reserve(temp_dir):
    ledger = QuotaLedger(temp_dir)
    # Simulate standard limit exhausted
    with ledger._locked() as state:
        state["usage"]["standard_sent"] = 2800
        state["usage"]["total_sent"] = 2800

    # Attempt standard message -> should fail
    with pytest.raises(QuotaExceededError) as exc_info:
        ledger.reserve_and_send("user1", "Standard msg", is_critical_alert=False)
    assert "2.800" in str(exc_info.value)

    # Attempt critical alert -> should succeed from 200 reserve
    ok, msg_id, usage = ledger.reserve_and_send("user1", "Critical Alert", is_critical_alert=True)
    assert ok is True
    assert usage["critical_sent"] == 1
    assert usage["total_sent"] == 2801


def test_bot_core_commands(temp_dir):
    ledger = QuotaLedger(temp_dir)
    report_gen = ReportGenerator(temp_dir)
    ai_client = AIGatewayClient(model="model_free")
    bot = ZaloBotEngine(ledger, ai_client, report_gen, authorized_users=["admin", "user1"])

    # Unauthorized
    res_unauth = bot.handle_command("/status", sender_id="unauthorized_user")
    assert res_unauth["success"] is False
    assert "⛔" in res_unauth["reply"]

    # Authorized /help
    res_help = bot.handle_command("/help", sender_id="user1")
    assert res_help["success"] is True
    assert "/status" in res_help["reply"]
    assert "/jobs" in res_help["reply"]
    assert "/report" in res_help["reply"]

    # Authorized /status
    res_status = bot.handle_command("/status", sender_id="admin")
    assert res_status["success"] is True
    assert "aiot-ws1" in res_status["reply"]
    assert "aiotlab-rack" in res_status["reply"]
    assert "OFFLINE" in res_status["reply"]

    # Alias /gpu
    res_gpu = bot.handle_command("/gpu", sender_id="admin")
    assert res_gpu["success"] is True
    assert "GPU" in res_gpu["reply"]

    # Authorized /quota
    res_quota = bot.handle_command("/quota", sender_id="user1")
    assert res_quota["success"] is True
    assert "3.000" in res_quota["reply"]

    # Authorized /jobs
    res_jobs = bot.handle_command("/jobs", sender_id="user1")
    assert res_jobs["success"] is True
    assert "TÁC VỤ" in res_jobs["reply"]


def test_report_generator(temp_dir):
    report_gen = ReportGenerator(temp_dir)
    report = report_gen.generate_weekly_report(author="Test Runner")
    assert report["status"] == "GENERATED"
    assert "aiot-ws1" in report["content"]
    assert "aiotlab-rack" in report["content"]
    assert "OFFLINE" in report["content"]

    reports_list = report_gen.list_reports()
    assert len(reports_list) == 1
    assert reports_list[0]["id"] == report["id"]


def test_read_only_tools_restriction(temp_dir):
    ledger = QuotaLedger(temp_dir)
    report_gen = ReportGenerator(temp_dir)
    ai_client = AIGatewayClient(model="model_free")
    bot = ZaloBotEngine(ledger, ai_client, report_gen)

    # Verify write tools are explicitly in DISABLED list
    assert "execute_arbitrary_shell" in bot.DISABLED_WRITE_TOOLS
    assert "sudo_system_commands" in bot.DISABLED_WRITE_TOOLS
    assert "restart_server_or_daemon" in bot.DISABLED_WRITE_TOOLS
    assert "delete_user_workspace" in bot.DISABLED_WRITE_TOOLS
