"""Phase 4 tests: Conversational AI runtime, scoped memory, RBAC tools & A01-A05."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ENV", "development")

from app.db.models import AgentToolAuditRecord  # noqa: E402
from app.db.session import DatabaseManager  # noqa: E402
from app.services.assistant_v4.agent_runtime import (  # noqa: E402
    IntentRouter,
    ModelCapabilityProbe,
    NativeAgentRuntime,
)
from app.services.assistant_v4.config import MemoryConfig, ModelBudgetConfig  # noqa: E402
from app.services.assistant_v4.memory_service import ScopedSessionMemory  # noqa: E402
from app.services.assistant_v4.secure_tools import SecureToolExecutor  # noqa: E402
from app.services.zalo_assistant.identity_rbac import ZaloPrincipal  # noqa: E402
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine  # noqa: E402
from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector  # noqa: E402


class _FakeAIClient:
    def __init__(self, script=None, fail=False):
        self.model = "model_free"
        self.script = list(script or [])
        self.calls = []
        self.fail = fail

    def generate_with_tools(self, messages, *, tools=None, temperature=0.1, max_tokens=600):
        self.calls.append({"messages": messages, "tools": tools})
        if self.fail:
            return {"content": "", "tool_calls": [], "error": "Gateway 503"}
        if self.script:
            return self.script.pop(0)
        return {"content": "Trả lời tổng hợp từ AI.", "tool_calls": []}

    def generate_chat_response(self, messages, *, system_prompt=None, temperature=0.5, max_tokens=600):
        self.calls.append({"messages": messages, "system_prompt": system_prompt, "chat_only": True})
        if self.fail:
            return "Lỗi cổng kết nối AI Gateway 9Router (HTTP 503)."
        if self.script:
            nxt = self.script.pop(0)
            if isinstance(nxt, dict):
                return nxt.get("content") or "OK"
            return str(nxt)
        return "GPU workstation đang ở 84°C (dữ liệu thời gian thực)."


def _setup_executor(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    core = SimpleNamespace(
        local_host_id="workstation",
        servers=[
            {"id": "workstation", "hostname": "aiot-ws1", "status": "ONLINE", "last_heartbeat_at": "2026-11-25T15:00:00+00:00", "cpu_usage_percent": 40.0},
        ],
        gpus=[{"id": "gpu0", "uuid": "GPU-1", "server_id": "workstation", "name": "A30", "utilization_gpu": 50, "temp_celsius": 72, "vram_used_mb": 4000, "vram_total_mb": 24576}],
        jobs=[],
        workspace_controller=SimpleNamespace(
            list_workspaces=lambda: [
                {"id": "ws-id-1", "name": "ws-minhtq", "owner": "minhtq", "host_id": "workstation", "container_id": "cid-minhtq-1", "observed_state": "RUNNING", "ram_mb_limit": 16384},
                {"id": "ws-id-2", "name": "ws-minh", "owner": "minh", "host_id": "workstation", "container_id": "cid-minh-2", "observed_state": "RUNNING", "ram_mb_limit": 8192},
            ]
        ),
        get_gpu_allocations_by_device=lambda: [],
    )
    tel = UnifiedTelemetryCollector(str(tmp_path), db_manager=db, core_service=core)
    inc = UnifiedIncidentEngine(str(tmp_path), db_manager=db)
    log_file = tmp_path / "backend.log"
    log_file.write_text("2026-11-25 info boot\nbearer=secret_token_should_redact_123456\nline with keyword ok\n", encoding="utf-8")
    exe = SecureToolExecutor(str(tmp_path), db_manager=db, telemetry_collector=tel, incident_engine=inc, log_paths={"backend": str(log_file)})
    return db, exe


def test_a01_container_prefix_collision_omitted_name_and_forged_principal(tmp_path: Path):
    db, exe = _setup_executor(tmp_path)
    minh = ZaloPrincipal("z-minh", "minh", "m-minh", "RESEARCHER", "Minh", True, True)
    minhtq = ZaloPrincipal("z-minhtq", "minhtq", "m-tq", "RESEARCHER", "Minh TQ", True, True)

    # Exact own lookup works; other user's exact lookup DENIED; omitted returns ONLY caller's container
    r_own = exe.execute_tool("get_container_stats", {"container_id": "ws-minh"}, minh)
    assert r_own["status"] == "OK" and r_own["data"]["container"]["owner"] == "minh"

    r_other = exe.execute_tool("get_container_stats", {"container_id": "ws-minhtq"}, minh)
    assert r_other["status"] == "DENIED"

    # Prefix collision MUST NOT leak minhtq to minh or vice versa
    r_prefix = exe.execute_tool("get_container_stats", {"container_id": "ws-min"}, minh)
    assert r_prefix["error_code"] == "CONTAINER_NOT_FOUND"

    r_omitted = exe.execute_tool("get_container_stats", {}, minhtq)
    assert [c["owner"] for c in r_omitted["data"]["containers"]] == ["minhtq"]

    # Forged username/role arguments rejected by strict schema or ignored by server principal
    r_forged = exe.execute_tool("get_member_info", {"username": "minhtq", "role": "ADMIN"}, minh)
    assert r_forged["error_code"] == "INVALID_ARGUMENTS"
    r_cross_member = exe.execute_tool("get_member_info", {"username": "minhtq"}, minh)
    assert r_cross_member["status"] == "DENIED"

    # All denied calls audited in AgentToolAuditRecord
    from sqlalchemy import select

    with db.session_scope() as s:
        denied_rows = s.execute(select(AgentToolAuditRecord).where(AgentToolAuditRecord.rbac_allowed.is_(False))).scalars().all()
        assert len(denied_rows) >= 2


def test_a02_malformed_args_log_redaction_and_prompt_injection(tmp_path: Path):
    db, exe = _setup_executor(tmp_path)
    admin = ZaloPrincipal("z-adm", "admin", "m-adm", "ADMIN", "Admin", True, True)
    researcher = ZaloPrincipal("z-r", "tranglt", "m-r", "RESEARCHER", "Trang", True, True)

    # Unknown / shell / raw SQL blocked unconditionally
    assert exe.execute_tool("execute_arbitrary_shell", {"cmd": "id"}, admin)["error_code"] == "TOOL_NOT_ALLOWED"
    assert exe.execute_tool("raw_sql_execution", {"sql": "SELECT 1"}, admin)["error_code"] == "TOOL_NOT_ALLOWED"

    # Non-admin denied Admin logs
    assert exe.execute_tool("search_redacted_logs", {"source": "backend", "keyword": "secret"}, researcher)["status"] == "DENIED"

    # Admin log search redacts tokens & limits output
    log_res = exe.execute_tool("search_redacted_logs", {"source": "backend", "keyword": "bearer", "lines": 5}, admin)
    assert log_res["status"] == "OK"
    assert "secret_token_should_redact_123456" not in str(log_res)
    assert "[REDACTED]" in str(log_res)


def test_a03_cross_session_and_cross_channel_memory_isolation(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    mem = ScopedSessionMemory(db, cfg=MemoryConfig(max_messages=6))
    s_admin_zalo = mem.get_or_create_session(principal_id="admin", channel="zalo", chat_id="group-1")
    s_user_zalo = mem.get_or_create_session(principal_id="tranglt", channel="zalo", chat_id="group-1")
    s_admin_dash = mem.get_or_create_session(principal_id="admin", channel="dashboard", chat_id="dash")

    mem.append_turn_messages(session_id=s_admin_zalo["session_id"], expected_version=1, entries=[{"role": "user", "content": "Admin secret task"}])
    # Researcher in same group-1 gets completely empty isolated session!
    assert mem.get_bounded_context(session_id=s_user_zalo["session_id"], principal_id="tranglt", channel="zalo", chat_id="group-1") == []
    # Cross-principal session access MUST raise PermissionError
    with pytest.raises(PermissionError):
        mem.get_bounded_context(session_id=s_admin_zalo["session_id"], principal_id="tranglt", channel="zalo", chat_id="group-1")
    assert s_admin_zalo["session_id"] != s_admin_dash["session_id"]


def test_a04_bounded_tool_loop_circuit_breaker_and_capability_probe(tmp_path: Path):
    db, exe = _setup_executor(tmp_path)
    mem = ScopedSessionMemory(db)
    admin = ZaloPrincipal("z-adm", "admin", "m-adm", "ADMIN", "Admin", True, True)
    sess = mem.get_or_create_session(principal_id="admin", channel="zalo", chat_id="z-adm")

    # Infinite tool-calling model loop MUST terminate within request_limit=2!
    loop_script = [
        {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "get_node_status", "arguments": "{}"}}]},
        {"content": "", "tool_calls": [{"id": "c2", "function": {"name": "get_node_status", "arguments": "{}"}}]},
        {"content": "", "tool_calls": [{"id": "c3", "function": {"name": "get_node_status", "arguments": "{}"}}]},
    ]
    rt = NativeAgentRuntime(
        db_manager=db,
        ai_client=_FakeAIClient(script=loop_script),
        memory=mem,
        tool_executor=exe,
        budget=ModelBudgetConfig(request_limit=2, tool_limit=4, circuit_breaker_failures=2),
    )
    out = rt.run_turn(question="Check status loop", principal=admin, session_id=sess["session_id"], channel="zalo", chat_id="z-adm")
    assert out["model_requests_used"] <= 2

    # Circuit breaker opens after 2 gateway failures
    rt_fail = NativeAgentRuntime(
        db_manager=db,
        ai_client=_FakeAIClient(fail=True),
        memory=mem,
        tool_executor=exe,
        budget=ModelBudgetConfig(request_limit=2, circuit_breaker_failures=2),
    )
    rt_fail.run_turn(question="q1", principal=admin, session_id=sess["session_id"], channel="zalo", chat_id="z-adm")
    rt_fail.run_turn(question="q2", principal=admin, session_id=sess["session_id"], channel="zalo", chat_id="z-adm")
    assert rt_fail.breaker.is_open is True
    r3 = rt_fail.run_turn(question="q3", principal=admin, session_id=sess["session_id"], channel="zalo", chat_id="z-adm")
    assert r3["degraded"] is True

    probe = ModelCapabilityProbe(_FakeAIClient(script=["PONG", {"content": "", "tool_calls": [{"id": "1", "function": {"name": "get_node_status", "arguments": "{}"}}]}]), exe)
    p_res = probe.run_probe(admin)
    assert p_res["plain_chat"] is True and p_res["tool_calling_supported"] is True


def test_a05_multi_turn_follow_up_fresh_data_and_general_chat_isolation(tmp_path: Path):
    db, exe = _setup_executor(tmp_path)
    mem = ScopedSessionMemory(db)
    admin = ZaloPrincipal("z-adm", "admin", "m-adm", "ADMIN", "Admin", True, True)
    sess = mem.get_or_create_session(principal_id="admin", channel="zalo", chat_id="z-adm")

    assert IntentRouter.classify("Giải thích lỗi CUDA Out of Memory và cách tối ưu batch size") == "GENERAL_CHAT"
    assert IntentRouter.classify("GPU nào đang nóng nhất?") == "LABOPS_AGENT"
    assert IntentRouter.classify("/status") == "EXPLICIT_COMMAND"

    script = [
        {"content": "", "tool_calls": [{"id": "t1", "function": {"name": "get_gpu_metrics", "arguments": "{}"}}]},
        {"content": "GPU workstation hiện đang 72°C (mới nhất lúc 22:00 ICT)."},
        {"content": "", "tool_calls": [{"id": "t2", "function": {"name": "compare_resource_metrics", "arguments": json.dumps({"metric": "gpu"})}}]},
        {"content": "Chưa có đủ mẫu lịch sử hôm qua để so sánh chính xác; chỉ số hiện tại vẫn đọc mới là 72°C."},
    ]
    rt = NativeAgentRuntime(db_manager=db, ai_client=_FakeAIClient(script=script), memory=mem, tool_executor=exe)
    r1 = rt.run_turn(question="GPU nào đang nóng nhất?", principal=admin, session_id=sess["session_id"], channel="zalo", chat_id="z-adm")
    r2 = rt.run_turn(question="So với hôm qua thì sao?", principal=admin, session_id=sess["session_id"], channel="zalo", chat_id="z-adm")
    assert "72" in r1["reply"]
    assert r2["tool_traces"][0]["tool"] == "compare_resource_metrics"
    assert r2["tool_traces"][0]["status"] == "INSUFFICIENT_HISTORY"
