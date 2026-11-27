"""
Comprehensive verification tests for Web Admin <-> Zalo AI Agent 42-Action Parity,
Mandatory Intent-Confirmation & Disambiguation Gate (R1-R4), DM 1-1 OTP Isolation,
R4 Typed Confirmation, Secret Redaction, and E2E @khuongnd conversation flow.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from sqlalchemy import select

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ENV", "development")

from app.db.models import (  # noqa: E402
    AgentToolAuditRecord,
    AuditEventRecord,
    IncidentRecord,
    ServerAccessRequestRecord,
    V4ActionProposalRecord,
    V4ChatMessageRecord,
)
from app.services.assistant_v4.admin_action_catalog import (  # noqa: E402
    ADMIN_ACTION_CATALOG,
    AMBIGUITY_GROUPS,
    check_disambiguation,
)
from app.services.assistant_v4.approval_service import (  # noqa: E402
    FORBIDDEN_ACTIONS,
    classify_action_risk,
)
from app.services.assistant_v4.config import AssistantV4Config  # noqa: E402
from app.services.assistant_v4.facade import AssistantV4Facade  # noqa: E402
from app.services.assistant_v4.secure_tools import SecureToolExecutor  # noqa: E402
from app.services.zalo_assistant.identity_rbac import ZaloPrincipal  # noqa: E402
from app.services.zalo_assistant.service import ZaloAssistantService  # noqa: E402
from app.services.zalo_assistant.worker import ZaloPollingWorker  # noqa: E402


EXPECTED_42_ACTION_IDS = {
    # Group 1 (9)
    "create_web_account",
    "reissue_web_password",
    "reissue_ssh_temp_password",
    "assign_supervisor",
    "update_storage_quota",
    "suspend_member",
    "restore_member",
    "archive_member",
    "delete_member_permanent",
    # Group 2 (7)
    "provision_member_ssh",
    "rotate_ssh_key",
    "revoke_ssh_access",
    "revoke_ssh_key",
    "terminate_ssh_sessions",
    "verify_member_workspace",
    "claim_member_secrets_once",
    # Group 3 (5)
    "approve_ssh_request",
    "reject_ssh_request",
    "request_info_ssh_request",
    "resend_ssh_zalo_invite",
    "sweep_mram_sla",
    # Group 4 (3)
    "add_vpn_device",
    "toggle_vpn_device",
    "revoke_vpn_device",
    # Group 5 (5)
    "allocate_gpu",
    "release_gpu_reservation",
    "stop_job",
    "stop_process",
    "kill_process",
    # Group 6 (3)
    "decide_repository_request",
    "rescan_repository_request",
    "decide_security_scan",
    # Group 7 (10)
    "ack_incident",
    "resolve_incident",
    "evaluate_incidents",
    "generate_report",
    "create_zalo_link_code",
    "bind_zalo_identity",
    "revoke_zalo_identity",
    "update_alert_subscriptions",
    "reload_v4_config",
    "run_v4_retention",
}


class _MockLabOpsService:
    def __init__(self) -> None:
        self.calls: List[tuple[str, Any]] = []
        self.audit_logs: List[Dict[str, Any]] = []
        self.members: List[Dict[str, Any]] = [
            {
                "id": "mem-khuongnd",
                "username": "khuongnd",
                "fullName": "Nguyen Duy Khuong",
                "role": "RESEARCHER",
                "status": "ACTIVE",
                "provisioning_state": "WEB_ACTIVE",
                "ssh_access_state": "VERIFIED_READY",
                "ssh_status": "VERIFIED_READY",
                "ssh_port": 22005,
                "assigned_server": "workstation",
                "workspace_name": "ws-khuongnd",
                "storage_quota_gb": 100,
                "storage_pool_id": "pool-sdd",
                "version": 1,
                "wireguard": {"devices": [{"id": "dev-k1", "name": "Laptop", "status": "ACTIVE"}]},
            },
            {
                "id": "mem-khuongtran",
                "username": "khuongtran",
                "fullName": "Tran Van Khuong",
                "role": "RESEARCHER",
                "status": "ACTIVE",
                "provisioning_state": "WEB_ACTIVE",
                "ssh_access_state": "NONE",
                "ssh_status": "NONE",
                "assigned_server": "workstation",
                "workspace_name": "ws-khuongtran",
                "storage_quota_gb": 50,
                "version": 1,
                "wireguard": {"devices": []},
            },
        ]
        self.ssh_key_registry = SimpleNamespace(
            list_keys=lambda username=None: [{"id": "sshkey-1", "username": username or "khuongnd", "desired_state": "ACTIVE"}],
            revoke_key=lambda key_id, *, actor, is_admin: self._rec("revoke_key", {"key_id": key_id, "actor": actor}),
        )
        self.security_scanner = SimpleNamespace(
            list_scans=lambda: [{"id": "scan-1", "state": "REVIEW_REQUIRED"}],
            decide=lambda scan_id, decision, reviewer: self._rec("decide_scan", {"scan_id": scan_id, "decision": decision}),
        )
        self.ssh_access_manager = SimpleNamespace(vault=self)

    def _rec(self, op: str, data: Any) -> Dict[str, Any]:
        self.calls.append((op, data))
        return {"ok": True, "op": op, **(data if isinstance(data, dict) else {"value": data})}

    def ensure_members_fresh(self) -> None:
        return None

    def _save_persistent_data(self, *, audit_only: bool = False) -> None:
        return None

    def save_audit_logs(self) -> None:
        return None

    def _audit(self, actor: str, action: str, target_type: str, target_id: str, result: str, details: str) -> None:
        self.audit_logs.append({"actor": actor, "action": action, "target_id": target_id, "result": result, "details": details})

    def _find_member(self, ident: str) -> Dict[str, Any]:
        il = str(ident or "").strip().lstrip("@").lower()
        for m in self.members:
            if m["id"].lower() == il or m["username"].lower() == il:
                return m
        raise LookupError(f"Member '{ident}' not found")

    def store_reissued_temp_password(self, **kw) -> Dict[str, Any]:
        return {"secret_id": "sec-web-vault-01", "expires_at": "2026-11-28T00:00:00Z"}

    def reissue_member_temp_password(self, ident: str, *, actor: str, ttl_seconds: int = 86400) -> Dict[str, Any]:
        m = self._find_member(ident)
        self.calls.append(("reissue_member_temp_password", m["username"]))
        return {
            "member_id": m["id"],
            "username": m["username"],
            "activation_status": "PENDING_ACTIVATION",
            "password_status": "CHANGE_REQUIRED",
            "temp_password_expires_at": "2026-11-28T09:00:00+00:00",
            "one_time_secrets": {"secret_id": "sec-ssh-vault-01", "channel_secrets_available": True},
        }

    def claim_member_secrets(self, ident: str, *, actor: str) -> Dict[str, Any]:
        m = self._find_member(ident)
        self.calls.append(("claim_member_secrets", m["username"]))
        return {
            "secret_id": "sec-ssh-vault-01",
            "username": m["username"],
            "temporary_password": "SshTempSecret!5678",
            "key_passphrase": "SshPassphrase!9012",
        }

    def get_member_ssh_access_status(self, ident: str) -> Dict[str, Any]:
        m = self._find_member(ident)
        return {"member_id": m["id"], "username": m["username"], "one_time_package": {"available": True, "secret_id": "sec-ssh-vault-01"}}

    def rotate_member_ssh_keypair(self, ident: str, *, actor: str, require_passphrase: bool = True) -> Dict[str, Any]:
        m = self._find_member(ident)
        self.calls.append(("rotate_member_ssh_keypair", m["username"]))
        return {
            "username": m["username"],
            "ssh_status": "VERIFIED_READY",
            "ssh_fingerprint": "SHA256:newfp",
            "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETKEYBODY\n-----END OPENSSH PRIVATE KEY-----",
            "passphrase": "KeyPassphrase!Secret99",
        }

    def reverify_member_workspace(self, ident: str, *, actor: str) -> Dict[str, Any]:
        m = self._find_member(ident)
        self.calls.append(("reverify_member_workspace", m["username"]))
        return {"username": m["username"], "status": "VERIFIED_READY"}

    def suspend_member_lifecycle(self, ident: str, *, actor: str = "admin", reason: str = "") -> Dict[str, Any]:
        m = self._find_member(ident)
        self.calls.append(("suspend_member_lifecycle", m["username"]))
        return {"username": m["username"], "status": "SUSPENDED"}

    def preview_member_deletion(self, ident: str, *, actor: str = "admin", dry_run: bool = True) -> Dict[str, Any]:
        m = self._find_member(ident)
        return {"username": m["username"], "blockers": []}

    def delete_member_permanently(self, ident: str, *, actor: str = "admin", remove_files: bool = False, confirm_username: str = "", step_up_verified: bool = False, enforce_lifecycle: bool = True) -> Dict[str, Any]:
        m = self._find_member(ident)
        self.calls.append(("delete_member_permanently", {"username": m["username"], "confirm_username": confirm_username}))
        return {"username": m["username"], "deleted": True}

    def add_member_device(self, member_id: str, device_name: str, *, actor: str = "admin") -> Dict[str, Any]:
        m = self._find_member(member_id)
        self.calls.append(("add_member_device", {"username": m["username"], "device": device_name}))
        return {
            "id": "dev-new-1",
            "name": device_name,
            "vpn_ip": "10.8.0.42",
            "configuration": "[Interface]\nPrivateKey = WgSecretPrivKeyXYZ=\nAddress = 10.8.0.42/24",
        }

    def update_storage_quota(self, member_id: str, new_quota_gb: int) -> Dict[str, Any]:
        m = self._find_member(member_id)
        m["storage_quota_gb"] = new_quota_gb
        return self._rec("update_storage_quota", {"member_id": m["id"], "quota_gb": new_quota_gb})

    def restore_member(self, member_id: str) -> Dict[str, Any]:
        m = self._find_member(member_id)
        return self._rec("restore_member", {"member_id": m["id"], "status": "PENDING_ACTIVATION"})

    def archive_member_lifecycle(self, ident: str, actor: str = "admin", *, retain_backup: bool = True) -> Dict[str, Any]:
        m = self._find_member(ident)
        return self._rec("archive_member_lifecycle", {"username": m["username"], "retain_backup": retain_backup})

    def provision_member_ssh_access(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._rec("provision_member_ssh_access", {"username": payload.get("username"), "ssh_status": "PENDING_ACTIVATION"})

    def revoke_member_ssh_access(self, ident: str, actor: str = "admin") -> Dict[str, Any]:
        m = self._find_member(ident)
        return self._rec("revoke_member_ssh_access", {"username": m["username"], "ssh_status": "REVOKED"})

    def terminate_member_ssh_sessions(self, ident: str, actor: str = "admin", confirm: bool = False) -> Dict[str, Any]:
        m = self._find_member(ident)
        return self._rec("terminate_member_ssh_sessions", {"username": m["username"], "confirm": confirm, "terminated": 1})

    def claim_member_key_bundle(self, ident: str, actor: str = "admin") -> Dict[str, Any]:
        m = self._find_member(ident)
        return self._rec("claim_member_key_bundle", {"username": m["username"], "fingerprint": "SHA256:bundle", "private_key": "PRIVATEKEYBUNDLE"})

    def toggle_member_device(self, member_id: str, device_id: str, enable: bool, actor: str = "admin") -> Dict[str, Any]:
        return self._rec("toggle_member_device", {"member_id": member_id, "device_id": device_id, "enabled": enable})

    def revoke_member_device_v2(self, member_id: str, device_id: str, actor: str = "admin") -> Dict[str, Any]:
        return self._rec("revoke_member_device_v2", {"member_id": member_id, "device_id": device_id, "revoked": True})

    def allocate_gpu(
        self,
        gpu_id: str,
        username: str,
        mode: str,
        requested_vram_gb: int = 0,
        job_name: str = "",
        actor: str | None = None,
        idempotency_key: str | None = None,
        ttl_minutes: int = 240,
        is_admin: bool = True,
        enforce_permissions: bool = False,
    ) -> Dict[str, Any]:
        return self._rec("allocate_gpu", {"gpu_id": gpu_id, "username": username, "mode": mode, "job_name": job_name})

    def stop_job(self, job_id: str, actor: str | None = None, is_admin: bool = True) -> Dict[str, Any]:
        return self._rec("stop_job", {"job_id": job_id, "status": "STOPPED"})

    def terminate_process(
        self,
        ref: str,
        timeout_seconds: int = 10,
        terminate_tree: bool = True,
        actor: str = "admin",
        is_admin: bool = True,
    ) -> Dict[str, Any]:
        return self._rec("terminate_process", {"ref": ref, "status": "TERMINATED"})

    def kill_process(
        self,
        ref: str,
        force: bool = True,
        kill_tree: bool = True,
        actor: str = "admin",
        is_admin: bool = True,
    ) -> Dict[str, Any]:
        return self._rec("kill_process", {"ref": ref, "status": "KILLED"})


class _MockMramService:
    def __init__(self, calls: List[tuple[str, Any]]) -> None:
        self.calls = calls

    def create_web_member_account(self, payload: Dict[str, Any], actor: str = "admin") -> Dict[str, Any]:
        self.calls.append(("create_web_member_account", payload.get("username")))
        return {"id": "mem-new-web", "username": payload.get("username"), "status": "ACTIVE"}

    def assign_supervisor(
        self,
        *,
        student_member_id: str,
        primary_supervisor_id: str,
        direct_manager_id: str | None = None,
        actor: str = "admin",
        change_reason: str = "",
    ) -> Dict[str, Any]:
        self.calls.append(("assign_supervisor", (student_member_id, primary_supervisor_id)))
        return {"student_member_id": student_member_id, "primary_supervisor_id": primary_supervisor_id}

    def decide_ssh_request(
        self,
        *,
        request_id: str,
        decision: str,
        approver_username: str,
        reason: str = "",
        config: Dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> Dict[str, Any]:
        self.calls.append(("decide_ssh_request", {"request_id": request_id, "decision": decision}))
        return {"id": request_id, "status": decision}

    def notify_pending_ssh_request_zalo(self, request_id: str, *, actor: str = "admin") -> Dict[str, Any]:
        self.calls.append(("notify_pending_ssh_request_zalo", request_id))
        return {"id": request_id, "notified": True}

    def admin_reissue_member_web_password(self, *, member_id: str, new_password: str | None = None, actor: str = "admin") -> Dict[str, Any]:
        self.calls.append(("admin_reissue_member_web_password", member_id))
        return {
            "success": True,
            "member_id": "mem-khuongnd",
            "username": "khuongnd",
            "full_name": "Nguyen Duy Khuong",
            "reissued_web_password": new_password or "SuperSecretWeb!1234",
            "must_change_password": True,
        }

    def expire_overdue_requests(self) -> List[str]:
        self.calls.append(("expire_overdue_requests", True))
        return []

    def sweep_expired_ssh_access(self) -> List[str]:
        self.calls.append(("sweep_expired_ssh_access", True))
        return []

    def get_request_detail(self, request_id: str, *, viewer_username: str, is_admin: bool) -> Dict[str, Any]:
        return {"id": request_id, "username": "khuongnd", "status": "PENDING_APPROVAL", "project_title": "AI Lab"}


def _build_parity_service(
    tmp_path: Path,
    *,
    delivery_mode: str = "vault_only",
    r3_enabled: bool = True,
    r4_enabled: bool = True,
    short_otp: bool = True,
) -> tuple[ZaloAssistantService, _MockLabOpsService, _MockMramService]:
    svc = ZaloAssistantService(str(tmp_path))
    lab_mock = _MockLabOpsService()
    mram_mock = _MockMramService(lab_mock.calls)
    repo_calls: List[Any] = []

    def _mock_repo_agent(*, path: str, method: str, data: Any, actor: str) -> Dict[str, Any]:
        lab_mock.calls.append(("repo_agent", {"path": path, "method": method}))
        repo_calls.append(path)
        return {"id": "req-repo-1", "status": "RESCANNED"}

    ctx = {
        "labops_service": lab_mock,
        "mram_service": mram_mock,
        "report_generator": svc.report_generator,
        "repo_alert_monitor": svc.repo_alert_monitor,
        "repository_agent_call": _mock_repo_agent,
    }
    cfg = AssistantV4Config(
        enabled=True,
        natural_language=True,
        task_manager=True,
        write_actions=True,
        zalo_r3_actions_enabled=r3_enabled,
        zalo_r4_actions_enabled=r4_enabled,
        zalo_secret_delivery_mode=delivery_mode,
        zalo_short_otp_confirm=short_otp,
    )
    svc.identity_rbac.admin_bind_identity(
        zalo_user_id="zalo-adm",
        username="admin",
        member_id="mem-adm",
        role="ADMIN",
        display_name="Admin",
        admin_actor="test",
    )
    svc.identity_rbac.admin_bind_identity(
        zalo_user_id="zalo-res",
        username="khuongnd",
        member_id="mem-khuongnd",
        role="RESEARCHER",
        display_name="Khuong",
        admin_actor="test",
    )
    svc.bot_engine.authorized_users = ["zalo-adm", "zalo-res"]
    svc.bot_engine.allowed_chat_ids = ["zalo-adm", "zalo-res", "group-lab-1"]

    svc.v4 = AssistantV4Facade(
        data_dir=str(tmp_path),
        db_manager=svc.db,
        ai_client=svc.ai_client,
        identity_rbac=svc.identity_rbac,
        telemetry_collector=svc.telemetry_collector,
        incident_engine=svc.incident_engine,
        authorized_users=svc.bot_engine.authorized_users,
        config=cfg,
        service_context=ctx,
    )
    svc.bot_engine.v4_facade = svc.v4
    return svc, lab_mock, mram_mock


def test_contract_42_actions_and_tool_schema_coverage(tmp_path: Path):
    assert len(ADMIN_ACTION_CATALOG) == 42
    assert set(ADMIN_ACTION_CATALOG.keys()) == EXPECTED_42_ACTION_IDS
    for aid, spec in ADMIN_ACTION_CATALOG.items():
        assert "/admin/members/{user_id}/rotate-key" not in spec.endpoint_ref
        assert "/reset-password" not in spec.endpoint_ref
        assert "/ssh-port" not in spec.endpoint_ref

    svc, _, _ = _build_parity_service(tmp_path)
    tools = svc.v4.tools
    admin_p = ZaloPrincipal("zalo-adm", "admin", "mem-adm", "ADMIN", "Admin", True, True)

    assert "clarify_admin_action" in tools._specs
    for aid in EXPECTED_42_ACTION_IDS:
        assert f"propose_{aid}" in tools._specs
        assert f"exec_{aid}" not in tools._specs

    # FORBIDDEN capabilities remain blocked
    for bad_cap in SecureToolExecutor.FORBIDDEN_CAPABILITIES:
        res = tools.execute_tool(bad_cap, {}, admin_p, source="zalo", chat_id="zalo-adm")
        assert res["status"] == "DENIED"
        assert res["error_code"] == "TOOL_NOT_ALLOWED"

    # Pydantic validation rejects unknown fields
    bad_args_res = tools.execute_tool(
        "propose_update_storage_quota",
        {"target_id": "khuongnd", "quota_gb": 200, "unexpected": "hack"},
        admin_p,
        source="zalo",
        chat_id="zalo-adm",
    )
    assert bad_args_res["status"] == "ERROR"
    assert bad_args_res["error_code"] == "INVALID_ARGUMENTS"


def test_disambiguation_gate_and_r1_mandatory_confirmation(tmp_path: Path):
    svc, lab_mock, _ = _build_parity_service(tmp_path)
    bot = svc.bot_engine
    admin_p = ZaloPrincipal("zalo-adm", "admin", "mem-adm", "ADMIN", "Admin", True, True)

    # 1. Ambiguous password reset ("cấp lại mật khẩu cho khuongnd") -> CLARIFYING 1/2/3, 0 mutations
    r_ambig = bot.handle_command("cấp lại mật khẩu cho khuongnd", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert r_ambig["v4_intent"] == "ADMIN_DISAMBIGUATION"
    assert "reissue_ssh_temp_password" in r_ambig["reply"]
    assert "reissue_web_password" in r_ambig["reply"]
    assert "cả hai" in r_ambig["reply"].lower()
    assert lab_mock.calls == []
    bot.handle_command("Hủy", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")

    # 2. Target ambiguity ("tạm ngưng @khuong") matching 2 users -> CLARIFYING_TARGET, 0 mutations
    r_target = bot.handle_command("tạm ngưng @khuong", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert r_target["v4_intent"] == "ADMIN_DISAMBIGUATION"
    assert "khuongnd" in r_target["reply"] and "khuongtran" in r_target["reply"]
    assert lab_mock.calls == []
    bot.handle_command("Hủy", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")

    # 3. R1 idempotent actions STILL require Phiếu Xác Nhận Hiểu Đúng and NEVER auto-execute
    r_r1 = bot.handle_command("xác minh workspace cho khuongnd", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert r_r1["v4_intent"] == "ADMIN_CONFIRMATION_CARD"
    assert "PHIẾU XÁC NHẬN HIỂU ĐÚNG [R1]" in r_r1["reply"]
    assert lab_mock.calls == []

    # R1 repo rescan tool also creates proposal without executing
    rescan_tool = svc.v4.tools.execute_tool(
        "propose_rescan_repository_request",
        {"target_id": "req-repo-1"},
        admin_p,
        source="zalo",
        chat_id="zalo-adm",
    )
    assert rescan_tool["status"] == "PROPOSAL_CREATED"
    assert lab_mock.calls == []

    # Confirming first R1 now runs verification cleanly
    pending = svc.v4.approvals.list_pending_for_actor("admin", chat_id="zalo-adm")
    # Clear newer rescan proposal to keep single-proposal short-OTP unambiguous, or use /confirm
    otp_match = re.search(r"/confirm (act4-[a-f0-9]+) ([A-F0-9]{6})", r_r1["reply"])
    assert otp_match is not None
    conf_r1 = bot.handle_command(
        f"/confirm {otp_match.group(1)} {otp_match.group(2)}",
        sender_id="zalo-adm",
        chat_id="zalo-adm",
        source="zalo",
    )
    assert ("reverify_member_workspace", "khuongnd") in lab_mock.calls
    assert "VERIFIED_READY" in conf_r1["reply"] or "reverify" in str(lab_mock.calls)

    # 4. ĐỔI 2 switches proposal cleanly; HỦY cancels without mutation
    lab_mock.calls.clear()
    bot._cmd_v4_cancel  # sanity check unchanged
    p_ssh = bot.handle_command("reset mật khẩu ssh tạm cho khuongnd", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert "reissue_ssh_temp_password" in p_ssh["reply"]
    assert "ĐỔI" in p_ssh["reply"]
    aid_1 = re.search(r"(act4-[a-f0-9]+)", p_ssh["reply"]).group(1)

    p_sw = bot.handle_command("ĐỔI 2", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert "reissue_web_password" in p_sw["reply"]
    assert svc.v4.approvals.get_proposal(aid_1)["state"] == "SWITCHED"
    aid_2 = re.search(r"(act4-[a-f0-9]+)", p_sw["reply"]).group(1)
    assert aid_2 != aid_1

    p_can = bot.handle_command("HỦY", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert "Đã hủy phiếu" in p_can["reply"]
    assert svc.v4.approvals.get_proposal(aid_2)["state"] == "CANCELLED"
    assert lab_mock.calls == []


def test_rbac_group_dm_isolation_otp_cas_r4_and_forbidden(tmp_path: Path):
    svc, lab_mock, _ = _build_parity_service(tmp_path)
    bot = svc.bot_engine
    admin_p = ZaloPrincipal("zalo-adm", "admin", "mem-adm", "ADMIN", "Admin", True, True)
    res_p = ZaloPrincipal("zalo-res", "khuongnd", "mem-khuongnd", "RESEARCHER", "Khuong", True, True)

    # RESEARCHER blocked
    r_forbid = bot.handle_command("cấp lại mật khẩu ssh cho khuongnd", sender_id="zalo-res", chat_id="zalo-res", source="zalo")
    assert r_forbid["success"] is False
    assert r_forbid["error"] == "FORBIDDEN"

    # Tool-level RBAC blocked for RESEARCHER
    res_tool = svc.v4.tools.execute_tool("propose_reissue_ssh_temp_password", {"target_id": "khuongnd"}, res_p, source="zalo", chat_id="zalo-res")
    assert res_tool["status"] == "DENIED"

    # Group chat isolation: OTP NOT leaked in group reply; routed to DM 1-1
    grp_res = bot.handle_command(
        "cấp lại mật khẩu ssh tạm cho khuongnd",
        sender_id="zalo-adm",
        chat_id="group-lab-1",
        source="zalo",
    )
    assert grp_res.get("v4_intent") == "ADMIN_CONFIRMATION_CARD"
    assert "/confirm" not in grp_res["reply"]
    dm_notifs = grp_res.get("dm_notifications") or []
    assert len(dm_notifs) == 1
    assert dm_notifs[0]["chat_id"] == "zalo-adm"
    m_otp = re.search(r"/confirm (act4-[a-f0-9]+) ([A-F0-9]{6})", dm_notifs[0]["text"])
    assert m_otp is not None
    aid, otp = m_otp.group(1), m_otp.group(2)

    # Confirming from group chat MUST fail
    grp_conf = bot.handle_command(f"/confirm {aid} {otp}", sender_id="zalo-adm", chat_id="group-lab-1", source="zalo")
    assert "Không chấp nhận" in grp_conf["reply"]
    assert lab_mock.calls == []

    # Generic OK/YES/CÓ rejected in DM without consuming proposal
    ok_rej = bot.handle_command("OK", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert "từ chối xác nhận chung chung" in ok_rej["reply"]
    assert svc.v4.approvals.get_proposal(aid)["state"] == "PENDING_CONFIRMATION"

    # Concurrent target change invalidates via CAS
    lab_mock.members[0]["version"] = 99
    cas_fail = bot.handle_command(f"/confirm {aid} {otp}", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert "TARGET_STATE_CHANGED" in cas_fail["reply"]

    # R4 delete_member_permanent requires OTP + CONFIRM DELETE <username>
    r4_card = bot.handle_command("xóa vĩnh viễn tài khoản @khuongnd", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert "[R4]" in r4_card["reply"]
    m_r4 = re.search(r"/confirm (act4-[a-f0-9]+) ([A-F0-9]{6}) (CONFIRM DELETE khuongnd)", r4_card["reply"])
    assert m_r4 is not None
    r4_aid, r4_otp, r4_phrase = m_r4.group(1), m_r4.group(2), m_r4.group(3)

    bad_r4 = bot.handle_command(f"/confirm {r4_aid} {r4_otp}", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert "R4_TYPED_CONFIRMATION_REQUIRED" in bad_r4["reply"]

    good_r4 = bot.handle_command(f"/confirm {r4_aid} {r4_otp} {r4_phrase}", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert "Đã thực thi" in good_r4["reply"]
    assert ("delete_member_permanently", {"username": "khuongnd", "confirm_username": "khuongnd"}) in lab_mock.calls

    # FORBIDDEN_ACTIONS still blocked
    for bad_act in FORBIDDEN_ACTIONS:
        assert classify_action_risk(bad_act) == "FORBIDDEN"
        with pytest.raises(PermissionError, match="FORBIDDEN_ACTION"):
            svc.v4.approvals.propose_action(
                actor_id="admin",
                actor_role="ADMIN",
                chat_id="zalo-adm",
                session_id="s1",
                action_type=bad_act,
                target_type="MEMBER",
                target_id="khuongnd",
            )


def test_secret_redaction_across_vault_only_and_dm_once(tmp_path: Path):
    # 1. vault_only mode: preserves Vault and never persists secrets
    svc_v, lab_v, _ = _build_parity_service(tmp_path / "vault", delivery_mode="vault_only")
    admin_p = ZaloPrincipal("zalo-adm", "admin", "mem-adm", "ADMIN", "Admin", True, True)

    claim_prop = svc_v.v4.approvals.propose_action(
        actor_id="admin",
        actor_role="ADMIN",
        chat_id="zalo-adm",
        session_id="s-v",
        action_type="claim_member_secrets_once",
        target_type="MEMBER",
        target_id="khuongnd",
    )
    claim_res = svc_v.v4.approvals.confirm_zalo_action(
        action_id=claim_prop["action_id"],
        code=claim_prop["confirmation_code"],
        principal=admin_p,
        chat_id="zalo-adm",
    )
    assert claim_res["state"] == "SUCCEEDED"
    assert ("claim_member_secrets", "khuongnd") not in lab_v.calls
    assert "vault_only" in claim_res["summary_reply"]

    # 2. dm_once mode: displays secret ephemerally in DM 1-1 reply while DB/audit/memory remain redacted
    svc_d, _, _ = _build_parity_service(tmp_path / "dmonce", delivery_mode="dm_once")
    for act in ("reissue_web_password", "rotate_ssh_key", "add_vpn_device"):
        p = svc_d.v4.approvals.propose_action(
            actor_id="admin",
            actor_role="ADMIN",
            chat_id="zalo-adm",
            session_id="s-d",
            action_type=act,
            target_type="MEMBER",
            target_id="khuongnd",
            canonical_args={"target_id": "khuongnd"},
        )
        res = svc_d.v4.approvals.confirm_zalo_action(
            action_id=p["action_id"],
            code=p["confirmation_code"],
            principal=admin_p,
            chat_id="zalo-adm",
        )
        assert res["state"] == "SUCCEEDED"
        if act == "reissue_web_password":
            assert "SuperSecretWeb!1234" in res["summary_reply"]

    with svc_d.db.session_scope() as session:
        auds = session.execute(select(AuditEventRecord)).scalars().all()
        props = session.execute(select(V4ActionProposalRecord)).scalars().all()
        msgs = session.execute(select(V4ChatMessageRecord)).scalars().all()
        blob = json.dumps(
            {
                "aud": [(a.action, a.details) for a in auds],
                "props": [(p.action_id, p.core_result_ref, p.canonical_args_json) for p in props],
                "msgs": [m.content_redacted for m in msgs],
            },
            ensure_ascii=False,
        )
    for forbidden_secret in ("SuperSecretWeb!1234", "SshTempSecret!5678", "SECRETKEYBODY", "WgSecretPrivKeyXYZ="):
        assert forbidden_secret not in blob


def test_e2e_khuongnd_zalo_bot_engine_conversation(tmp_path: Path):
    svc, lab_mock, _ = _build_parity_service(tmp_path, delivery_mode="vault_only", short_otp=True)
    bot = svc.bot_engine

    # Turn 1: Ambiguous password reset request for khuongnd
    t1 = bot.handle_command(
        "cấp lại mật khẩu cho khuongnd",
        sender_id="zalo-adm",
        chat_id="zalo-adm",
        source="zalo",
    )
    assert t1["success"] is True
    assert "1." in t1["reply"] and "2." in t1["reply"] and "3." in t1["reply"]
    assert lab_mock.calls == []

    # Turn 2: Admin chooses option 1 (reissue_ssh_temp_password)
    t2 = bot.handle_command("1", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert t2["success"] is True
    assert "PHIẾU XÁC NHẬN HIỂU ĐÚNG [R3]" in t2["reply"]
    assert "reissue_ssh_temp_password" in t2["reply"]
    assert "reissue_web_password" in t2["reply"]
    assert lab_mock.calls == []

    m_otp = re.search(r"/confirm (act4-[a-f0-9]+) ([A-F0-9]{6})", t2["reply"])
    assert m_otp is not None
    otp = m_otp.group(2)

    # Turn 3: Admin confirms with Short Reply OTP in DM 1-1
    t3 = bot.handle_command(otp, sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert t3["success"] is True
    assert "reissue_ssh_temp_password" in t3["reply"]
    assert ("reissue_member_temp_password", "khuongnd") in lab_mock.calls

    # Bonus Turn: Bundle choice ("3") creates 2 separate proposals (SSH + Web) cleanly
    t_b1 = bot.handle_command("reset mật khẩu cho khuongnd", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert "cả hai" in t_b1["reply"].lower()
    t_b2 = bot.handle_command("3", sender_id="zalo-adm", chat_id="zalo-adm", source="zalo")
    assert "reissue_ssh_temp_password" in t_b2["reply"]
    assert "reissue_web_password" in t_b2["reply"]
    assert len(svc.v4.approvals.list_pending_for_actor("admin", chat_id="zalo-adm")) == 2


def test_all_42_action_dispatchers_end_to_end(tmp_path: Path):
    svc, lab_mock, _ = _build_parity_service(tmp_path, delivery_mode="vault_only")
    admin_p = ZaloPrincipal("zalo-adm", "admin", "mem-adm", "ADMIN", "Admin", True, True)
    now = datetime.now(timezone.utc)

    # Seed real IncidentRecord + ServerAccessRequestRecord + disposable Zalo link for dispatch parity
    with svc.db.session_scope() as session:
        session.add(
            IncidentRecord(
                incident_id="inc-parity-1",
                event_type="gpu.high_temp",
                severity="P1",
                confidence="CONFIRMED",
                source="telemetry",
                resource_id="workstation-gpu-01",
                owner_id="admin",
                fingerprint="fp-parity-1",
                title="GPU Temp Warning",
                status="OPEN",
                occurrence_count=1,
                reopen_count=0,
                detected_at=now,
                last_observed_at=now,
                updated_at=now,
            )
        )
        session.add(
            ServerAccessRequestRecord(
                id="req-ssh-1",
                request_type="INITIAL",
                member_id="mem-khuongnd",
                username="khuongnd",
                member_category="RESEARCHER",
                project_title="Lab Parity",
                status="PENDING_APPROVAL",
                version=1,
                submitted_at=now,
                review_expires_at=now + timedelta(days=3),
            )
        )
    svc.identity_rbac.admin_bind_identity(
        zalo_user_id="zalo-disposable",
        username="khuongtran",
        member_id="mem-khuongtran",
        role="RESEARCHER",
        display_name="Disposable",
        admin_actor="test",
    )

    action_cases: List[tuple[str, str, Dict[str, Any], str]] = [
        # Group 1 (9)
        ("create_web_account", "newuser", {"target_id": "newuser", "username": "newuser", "full_name": "New Member", "member_code": "SV001"}, ""),
        ("reissue_web_password", "khuongnd", {"target_id": "khuongnd"}, ""),
        ("reissue_ssh_temp_password", "khuongnd", {"target_id": "khuongnd", "ttl_seconds": 3600}, ""),
        ("assign_supervisor", "khuongnd", {"target_id": "khuongnd", "primary_supervisor_id": "khuongtran"}, ""),
        ("update_storage_quota", "khuongnd", {"target_id": "khuongnd", "quota_gb": 150}, ""),
        ("suspend_member", "khuongnd", {"target_id": "khuongnd", "reason": "Audit check"}, ""),
        ("restore_member", "khuongnd", {"target_id": "khuongnd"}, ""),
        ("archive_member", "khuongnd", {"target_id": "khuongnd", "retain_backup": True}, ""),
        ("delete_member_permanent", "khuongnd", {"target_id": "khuongnd", "confirm_username": "khuongnd", "remove_files": False}, "CONFIRM DELETE khuongnd"),
        # Group 2 (7)
        ("provision_member_ssh", "khuongnd", {"target_id": "khuongnd", "assigned_server": "workstation"}, ""),
        ("rotate_ssh_key", "khuongnd", {"target_id": "khuongnd", "require_passphrase": True}, ""),
        ("revoke_ssh_access", "khuongnd", {"target_id": "khuongnd"}, ""),
        ("revoke_ssh_key", "sshkey-1", {"target_id": "sshkey-1"}, ""),
        ("terminate_ssh_sessions", "khuongnd", {"target_id": "khuongnd"}, ""),
        ("verify_member_workspace", "khuongnd", {"target_id": "khuongnd"}, ""),
        ("claim_member_secrets_once", "khuongnd", {"target_id": "khuongnd", "secret_type": "channel_secrets"}, ""),
        # Group 3 (5)
        ("approve_ssh_request", "req-ssh-1", {"target_id": "req-ssh-1", "reason": "Hợp lệ"}, ""),
        ("reject_ssh_request", "req-ssh-1", {"target_id": "req-ssh-1", "reason": "Không đủ thông tin"}, ""),
        ("request_info_ssh_request", "req-ssh-1", {"target_id": "req-ssh-1", "reason": "Cần bổ sung đề tài"}, ""),
        ("resend_ssh_zalo_invite", "req-ssh-1", {"target_id": "req-ssh-1"}, ""),
        ("sweep_mram_sla", "mram-queue", {"target_id": "mram-queue"}, ""),
        # Group 4 (3)
        ("add_vpn_device", "khuongnd", {"target_id": "khuongnd", "device_name": "ThinkPad"}, ""),
        ("toggle_vpn_device", "dev-k1", {"target_id": "dev-k1", "member_id": "mem-khuongnd", "enable": False}, ""),
        ("revoke_vpn_device", "dev-k1", {"target_id": "dev-k1", "member_id": "mem-khuongnd"}, ""),
        # Group 5 (5)
        ("allocate_gpu", "workstation-gpu-01", {"target_id": "workstation-gpu-01", "username": "khuongnd", "mode": "FULL_GPU_EXCLUSIVE"}, ""),
        ("release_gpu_reservation", "job-gpu-1", {"target_id": "job-gpu-1"}, ""),
        ("stop_job", "job-1", {"target_id": "job-1"}, ""),
        ("stop_process", "proc-101", {"target_id": "proc-101", "timeout_seconds": 5}, ""),
        ("kill_process", "proc-102", {"target_id": "proc-102", "kill_tree": True}, ""),
        # Group 6 (3)
        ("decide_repository_request", "req-repo-1", {"target_id": "req-repo-1", "decision": "APPROVED", "reason": "Clean repo"}, ""),
        ("rescan_repository_request", "req-repo-1", {"target_id": "req-repo-1"}, ""),
        ("decide_security_scan", "scan-1", {"target_id": "scan-1", "decision": "APPROVED", "reason": "Reviewed"}, ""),
        # Group 7 (10) — run reload_v4_config last so test flags stay enabled for preceding actions
        ("ack_incident", "inc-parity-1", {"target_id": "inc-parity-1", "reason": "Đã tiếp nhận"}, ""),
        ("resolve_incident", "inc-parity-1", {"target_id": "inc-parity-1", "reason": "Nhiệt độ GPU đã trở lại bình thường"}, ""),
        ("evaluate_incidents", "system", {"target_id": "system"}, ""),
        ("generate_report", "daily", {"target_id": "daily", "force": True}, ""),
        ("create_zalo_link_code", "khuongnd", {"target_id": "khuongnd", "role": "RESEARCHER", "ttl_minutes": 15}, ""),
        ("bind_zalo_identity", "khuongnd", {"target_id": "khuongnd", "zalo_user_id": "zalo-res-2", "role": "RESEARCHER"}, ""),
        ("revoke_zalo_identity", "zalo-disposable", {"target_id": "zalo-disposable"}, ""),
        ("update_alert_subscriptions", "zalo-adm", {"target_id": "zalo-adm", "updates": {"quiet_hours_enabled": False}}, ""),
        ("run_v4_retention", "v4-retention", {"target_id": "v4-retention", "dry_run": True, "force": True}, ""),
        ("reload_v4_config", "v4-runtime", {"target_id": "v4-runtime"}, ""),
    ]

    assert {row[0] for row in action_cases} == EXPECTED_42_ACTION_IDS

    succeeded_actions: List[str] = []
    for action_id, target_id, args_dict, typed_phrase in action_cases:
        spec = ADMIN_ACTION_CATALOG[action_id]
        prop = svc.v4.approvals.propose_action(
            actor_id="admin",
            actor_role="ADMIN",
            chat_id="zalo-adm",
            session_id="s-42",
            action_type=action_id,
            target_type=spec.target_type,
            target_id=target_id,
            canonical_args=args_dict,
            reason=str(args_dict.get("reason") or "Parity 42-action check"),
        )
        assert prop["state"] == "PENDING_CONFIRMATION"
        assert prop.get("confirmation_code")
        res = svc.v4.approvals.confirm_zalo_action(
            action_id=prop["action_id"],
            code=prop["confirmation_code"],
            principal=admin_p,
            chat_id="zalo-adm",
            session_id="s-42",
            typed_confirmation=typed_phrase,
        )
        assert res["state"] == "SUCCEEDED", f"Action {action_id} failed: {res}"
        assert action_id in res["summary_reply"]
        succeeded_actions.append(action_id)

    assert len(succeeded_actions) == 42
    assert set(succeeded_actions) == EXPECTED_42_ACTION_IDS
    assert any(op == "repo_agent" for op, _ in lab_mock.calls)


def test_all_8_ambiguity_groups_trigger_clarifying(tmp_path: Path):
    svc, _, _ = _build_parity_service(tmp_path)
    ctx = svc.v4.service_context

    # 1..5 detected from underspecified Vietnamese phrases + 6..8 triggered when explicit_action=False & empty text
    cases = [
        ("password_reset", {"action_type": None, "raw_text": "cấp lại mật khẩu cho khuongnd", "target_query": "khuongnd"}),
        ("member_lifecycle", {"action_type": None, "raw_text": "khóa tài khoản khuongnd", "target_query": "khuongnd"}),
        ("ssh_access_ops", {"action_type": None, "raw_text": "reset ssh của khuongnd", "target_query": "khuongnd"}),
        ("compute_termination", {"action_type": None, "raw_text": "dừng job train của khuongnd", "target_query": "khuongnd"}),
        ("vpn_device_ops", {"action_type": None, "raw_text": "tắt vpn cho khuongnd", "target_query": "khuongnd"}),
        ("mram_review_decision", {"action_type": "approve_ssh_request", "raw_text": "", "target_query": "req-ssh-1"}),
        ("incident_ops", {"action_type": "ack_incident", "raw_text": "", "target_query": "inc-1"}),
        ("zalo_identity_ops", {"action_type": "create_zalo_link_code", "raw_text": "", "target_query": "khuongnd"}),
    ]
    assert {g for g, _ in cases} == set(AMBIGUITY_GROUPS.keys())

    for group_name, params in cases:
        res = check_disambiguation(explicit_action=False, service_context=ctx, **params)
        assert res is not None, f"Group {group_name} did not trigger disambiguation"
        assert res["status"] == "CLARIFYING"
        assert res["ambiguity_group"] == group_name
        assert len(res["options"]) == len(AMBIGUITY_GROUPS[group_name]["options"])


def test_worker_group_chat_forwards_confirmation_to_admin_dm(tmp_path: Path):
    svc, lab_mock, _ = _build_parity_service(tmp_path)
    worker = ZaloPollingWorker(service_instance=svc)
    sent_messages: List[tuple[str, str]] = []

    class _FakeBot:
        async def send_message(self, *, chat_id: str, text: str):
            sent_messages.append((chat_id, text))
            return SimpleNamespace(message_id=f"prov-{len(sent_messages)}")

    res = asyncio.run(
        worker.process_incoming_message(
            _FakeBot(),
            raw_text="cấp lại mật khẩu ssh tạm cho khuongnd",
            chat_id="group-lab-1",
            sender_id="zalo-adm",
            sender_name="Admin",
            message_id="msg-grp-1",
        )
    )
    assert res["success"] is True
    assert lab_mock.calls == []

    group_texts = [t for c, t in sent_messages if c == "group-lab-1"]
    dm_texts = [t for c, t in sent_messages if c == "zalo-adm"]
    assert len(group_texts) == 1
    assert len(dm_texts) == 1
    assert "/confirm" not in group_texts[0]
    assert not re.search(r"\b[A-F0-9]{6}\b", group_texts[0])
    assert "DM 1-1" in group_texts[0]
    assert "PHIẾU XÁC NHẬN HIỂU ĐÚNG" in dm_texts[0]
    assert re.search(r"/confirm act4-[a-f0-9]+ [A-F0-9]{6}", dm_texts[0]) is not None


def test_native_agent_runtime_tool_calling_creates_proposal_only(tmp_path: Path):
    svc, lab_mock, _ = _build_parity_service(tmp_path)
    admin_p = ZaloPrincipal("zalo-adm", "admin", "mem-adm", "ADMIN", "Admin", True, True)
    seen_tools_list: List[str] = []

    class _FakeToolCallingAI:
        def __init__(self) -> None:
            self.step = 0

        def generate_with_tools(self, messages, tools=None, temperature=0.1, max_tokens=700):
            nonlocal seen_tools_list
            seen_tools_list = [t["function"]["name"] for t in (tools or [])]
            self.step += 1
            if self.step == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "function": {
                                "name": "inspect_member_detail",
                                "arguments": json.dumps({"target_id": "khuongnd"}),
                            },
                        },
                        {
                            "id": "tc-2",
                            "function": {
                                "name": "propose_reissue_ssh_temp_password",
                                "arguments": json.dumps({"target_id": "khuongnd", "reason": "Cấp lại SSH tạm thời theo yêu cầu"}),
                            },
                        },
                    ],
                }
            return {
                "content": "Đã tạo Phiếu Xác Nhận Hiểu Đúng để cấp lại mật khẩu SSH tạm cho khuongnd. Vui lòng xác nhận trong DM 1-1.",
                "tool_calls": [],
            }

        def generate_chat_response(self, *args, **kwargs):
            return "OK"

    svc.v4.runtime.ai_client = _FakeToolCallingAI()
    turn_res = svc.v4.runtime.run_turn(
        question="Kiểm tra hồ sơ rồi đề xuất cấp lại mật khẩu SSH tạm cho khuongnd",
        principal=admin_p,
        session_id="sess-llm-1",
        channel="zalo",
        chat_id="zalo-adm",
    )
    assert turn_res["ai_used"] is True
    assert turn_res["tool_calls_used"] == 2
    assert "propose_reissue_ssh_temp_password" in seen_tools_list
    assert [t["tool"] for t in turn_res["tool_traces"]] == [
        "inspect_member_detail",
        "propose_reissue_ssh_temp_password",
    ]
    assert [t["status"] for t in turn_res["tool_traces"]] == ["OK", "PROPOSAL_CREATED"]
    # Zero silent execution
    assert lab_mock.calls == []

    pending = svc.v4.approvals.list_pending_for_actor("admin", chat_id="zalo-adm")
    assert len(pending) == 1
    assert pending[0]["action_type"] == "reissue_ssh_temp_password"

    with svc.db.session_scope() as session:
        auds = session.execute(select(AgentToolAuditRecord)).scalars().all()
        assert len(auds) >= 2


def test_worker_sends_parity_release_announcement_once_to_admins_only(tmp_path: Path):
    svc, _, _ = _build_parity_service(tmp_path)
    worker = ZaloPollingWorker(service_instance=svc)
    sent_messages: List[tuple[str, str]] = []

    class _FakeBot:
        async def send_message(self, *, chat_id: str, text: str):
            sent_messages.append((chat_id, text))
            return SimpleNamespace(message_id=f"rel-{len(sent_messages)}")

    bot = _FakeBot()
    # First run sends DM 1-1 to verified ADMIN only (never RESEARCHER)
    first = asyncio.run(worker._maybe_send_parity_release_announcement(bot))
    assert first["sent"] is True
    assert first["reason"] == "DELIVERED"
    assert first["delivered_to"] == ["zalo-adm"]
    assert all(cid == "zalo-adm" for cid, _ in sent_messages)
    combined = "\n".join(txt for _, txt in sent_messages)
    assert "42 THAO TÁC / 7 PHÂN HỆ" in combined
    assert "ĐỔI" in combined and "HỦY" in combined
    assert "vault_only" in combined
    assert "zalo-res" not in first["delivered_to"]

    # Second run is idempotent and does not duplicate
    sent_messages.clear()
    second = asyncio.run(worker._maybe_send_parity_release_announcement(bot))
    assert second["sent"] is False
    assert second["reason"] == "ALREADY_SENT"
    assert sent_messages == []

    # Forced run resends explicitly when requested
    forced = asyncio.run(worker._maybe_send_parity_release_announcement(bot, force=True))
    assert forced["sent"] is True
    assert forced["delivered_to"] == ["zalo-adm"]
    assert len(sent_messages) >= 1
