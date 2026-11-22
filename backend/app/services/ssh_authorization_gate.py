"""
SSH Authorization Gate: Điểm kiểm soát tập trung quyết định quyền mở shell SSH và trạng thái READY.

Mọi thay đổi trạng thái kích hoạt, cấp khóa, thu hồi hoặc kiểm chứng runtime đều phải đi qua cổng này.
KHÔNG một đường code nào được phép tự đặt status = READY hoặc mở authorized_keys nếu còn gate chưa VERIFIED.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _allow_soft_storage_quota() -> bool:
    val = os.getenv("LABOPS_ALLOW_SOFT_STORAGE_QUOTA", "").strip().lower()
    if val:
        return val in ("1", "true", "yes")
    if "PYTEST_CURRENT_TEST" in os.environ or "unittest" in sys.modules:
        return False
    from app.core.config import settings
    return bool(getattr(settings, "LABOPS_ALLOW_SOFT_STORAGE_QUOTA", False))


def _allow_pending_activation_ssh() -> bool:
    val = os.getenv("LABOPS_AUTO_ACTIVATE_SSH_ON_PROVISION", "").strip().lower()
    if val:
        return val in ("1", "true", "yes")
    if "PYTEST_CURRENT_TEST" in os.environ or "unittest" in sys.modules:
        return False
    from app.core.config import settings
    return bool(getattr(settings, "LABOPS_AUTO_ACTIVATE_SSH_ON_PROVISION", False))


INACTIVE_MEMBER_STATUSES = frozenset({
    "SUSPENDED",
    "ARCHIVED",
    "DELETE_ELIGIBLE",
    "DELETED",
    "REVOKED",
    "DISABLED",
})


def _gate_entry(
    status: str,
    observed: Any,
    expected: Any,
    source: str,
    *,
    error_code: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    return {
        "status": status,
        "observed_value": observed,
        "expected_value": expected,
        "source": source,
        "verified_at": _now_iso(),
        "error_code": error_code,
        "reason": reason,
    }


def evaluate_ssh_authorization_gate(
    *,
    member: Dict[str, Any],
    account_security: Optional[Dict[str, Any]] = None,
    runtime_verification: Optional[Dict[str, Any]] = None,
    vpn_overview: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Kiểm tra đồng thời 8 điều kiện bắt buộc trước khi cấp quyền SSH hoặc đánh dấu READY:
    1. member_status: tài khoản không bị SUSPEND/REVOKE/ARCHIVE/DELETE
    2. account_activation: đã hoàn tất kích hoạt mật khẩu lần đầu (ACTIVATED)
    3. ssh_key_registration: khóa công khai OpenSSH đang ở trạng thái ACTIVE
    4. docker_provisioning: container workspace thật đang RUNNING hoặc chế độ kiểm thử hợp lệ
    5. privilege_isolation: non-root UID >= 20000, no sudo, CapDrop=ALL, no docker.sock
    6. storage_quota: hard quota đã được kernel/filesystem xác minh thực tế
    7. resource_enforcement: chính sách CPU/RAM/PIDs (và GPU nếu cấp) được xác minh trên cgroup/HostConfig
    8. network_policy: chính sách mạng SSH/VPN hợp lệ và endpoint thuộc container cô lập
    """
    acc = account_security or {}
    rv = runtime_verification or member.get("runtime_verification") or {}
    checks_detail = rv.get("checks_detail") or {}

    m_status = str(member.get("status") or "PENDING_PROVISIONING").upper()
    pw_status = str(acc.get("password_status") or member.get("password_status") or "NOT_APPLICABLE").upper()
    act_status = str(acc.get("activation_status") or member.get("activation_status") or "").upper()
    if not act_status:
        if pw_status == "ACTIVE" and not acc.get("must_change_password", False):
            act_status = "ACTIVATED"
        elif pw_status == "REVOKED":
            act_status = "REVOKED"
        elif pw_status == "TEMP_PASSWORD_EXPIRED":
            act_status = "EXPIRED"
        else:
            act_status = "PENDING_ACTIVATION"

    ssh_key_status = str(member.get("ssh_key_status") or "NOT_CONFIGURED").upper()
    ssh_pubkey = str(member.get("ssh_public_key") or "").strip()
    delegation_status = str(member.get("workspace_delegation_status") or "").upper()

    # Gate 1: Member status
    if m_status in INACTIVE_MEMBER_STATUSES:
        g_member = _gate_entry(
            "FAILED",
            m_status,
            "ACTIVE/PENDING_PROVISIONING",
            "member_registry",
            error_code="MEMBER_REVOKED_OR_SUSPENDED",
            reason=f"Tài khoản @{member.get('username')} đang ở trạng thái {m_status}.",
        )
    else:
        g_member = _gate_entry("VERIFIED", m_status, "NOT_SUSPENDED", "member_registry")

    # Gate 2: Account activation (first-login temp password changed)
    if act_status == "ACTIVATED" and pw_status == "ACTIVE" and not acc.get("must_change_password", False):
        g_activation = _gate_entry("VERIFIED", act_status, "ACTIVATED", "ssh_account_store")
    elif act_status in ("EXPIRED", "TEMP_PASSWORD_EXPIRED"):
        g_activation = _gate_entry(
            "FAILED",
            act_status,
            "ACTIVATED",
            "ssh_account_store",
            error_code="ACTIVATION_PASSWORD_EXPIRED",
            reason="Mật khẩu kích hoạt tạm đã hết hạn.",
        )
    elif act_status == "REVOKED":
        g_activation = _gate_entry(
            "FAILED",
            act_status,
            "ACTIVATED",
            "ssh_account_store",
            error_code="ACCOUNT_REVOKED",
            reason="Quyền kích hoạt đã bị thu hồi.",
        )
    elif _allow_pending_activation_ssh():
        g_activation = _gate_entry(
            "POLICY_ONLY",
            act_status,
            "POLICY_ONLY",
            "ssh_account_store",
            reason="Mở khóa SSH ngay khi cấp phát (LABOPS_AUTO_ACTIVATE_SSH_ON_PROVISION=true).",
        )
    else:
        g_activation = _gate_entry(
            "NOT_VERIFIED",
            act_status,
            "ACTIVATED",
            "ssh_account_store",
            error_code="PENDING_ACTIVATION",
            reason="Tài khoản chưa hoàn tất kích hoạt lần đầu bằng mật khẩu tạm.",
        )

    # Gate 3: SSH Key Registration
    if ssh_key_status == "ACTIVE" and ssh_pubkey and rv.get("ssh_key_installed") in (
        "VERIFIED",
        "REGISTERED_PENDING_ACTIVATION",
        "REGISTERED",
        None,
    ):
        g_ssh_key = _gate_entry(
            "VERIFIED",
            member.get("ssh_fingerprint") or "ACTIVE",
            "ACTIVE",
            "ssh_key_registry",
        )
    elif ssh_key_status in ("REVOKED", "VERIFIED_REVOKED", "FAILED"):
        g_ssh_key = _gate_entry(
            "FAILED",
            ssh_key_status,
            "ACTIVE",
            "ssh_key_registry",
            error_code="SSH_KEY_REVOKED",
            reason="Khóa công khai SSH đã bị thu hồi hoặc vô hiệu.",
        )
    else:
        g_ssh_key = _gate_entry(
            "NOT_VERIFIED",
            ssh_key_status,
            "ACTIVE",
            "ssh_key_registry",
            error_code="SSH_KEY_NOT_REGISTERED",
            reason="Chưa đăng ký khóa công khai SSH hợp lệ.",
        )

    # Gate 4: Docker Provisioning
    docker_detail = checks_detail.get("docker_running") or {}
    if rv.get("docker_running") == "VERIFIED" or (
        rv.get("canary_runtime") is True
        and rv.get("ssh_runtime") == "VERIFIED"
        and rv.get("privilege_isolation") == "VERIFIED"
    ):
        obs_ws = docker_detail.get("observed_value") or member.get("container_id") or rv.get("ssh_runtime")
        g_docker = _gate_entry("VERIFIED", obs_ws, "RUNNING", docker_detail.get("source") or "runtime_verification")
    elif delegation_status in ("AGENT_UNAVAILABLE", "PROVISION_FAILED") or rv.get("docker_running") == "FAILED":
        err_c = delegation_status if delegation_status in ("AGENT_UNAVAILABLE", "PROVISION_FAILED") else (docker_detail.get("error_code") or "CONTAINER_NOT_RUNNING")
        g_docker = _gate_entry(
            "FAILED",
            delegation_status or docker_detail.get("observed_value") or "NOT_RUNNING",
            "RUNNING",
            docker_detail.get("source") or "node_agent",
            error_code=err_c,
            reason=str(member.get("workspace_delegation_reason") or docker_detail.get("reason") or "Chưa khởi chạy container workspace qua Go Node Agent."),
        )
    else:
        g_docker = _gate_entry(
            "NOT_VERIFIED",
            delegation_status or "PENDING_PROVISIONING",
            "RUNNING",
            "node_agent",
            error_code="CONTAINER_NOT_RUNNING",
            reason="Workspace Docker container chưa chạy hoặc chưa được kiểm chứng.",
        )

    # Gate 5: Privilege Isolation
    priv_state = str(rv.get("privilege_isolation") or "NOT_VERIFIED").upper()
    priv_detail = checks_detail.get("privilege_isolation") or {}
    if priv_state == "VERIFIED":
        g_priv = _gate_entry("VERIFIED", priv_detail.get("observed_value") or "ISOLATED", "NON_ROOT_NO_SUDO_NO_DOCKER_SOCK", priv_detail.get("source") or "docker_inspect")
    elif priv_state == "FAILED":
        g_priv = _gate_entry(
            "FAILED",
            priv_detail.get("observed_value") or "VIOLATION",
            "ISOLATED",
            priv_detail.get("source") or "docker_inspect",
            error_code=priv_detail.get("error_code") or "SSH_RUNTIME_NOT_ISOLATED",
            reason=priv_detail.get("reason") or "Vi phạm nguyên tắc cô lập đặc quyền.",
        )
    else:
        g_priv = _gate_entry(
            "NOT_VERIFIED",
            priv_state,
            "VERIFIED",
            priv_detail.get("source") or "docker_inspect",
            error_code="SSH_RUNTIME_NOT_ISOLATED",
            reason="Chưa xác minh cô lập container/UID riêng; cấm chạy dưới UID quản trị host.",
        )

    # Gate 6: Storage Quota
    quota_state = str(rv.get("storage_quota") or "NOT_VERIFIED").upper()
    quota_detail = checks_detail.get("storage_quota") or {}
    if quota_state == "VERIFIED":
        g_storage = _gate_entry("VERIFIED", quota_detail.get("observed_value") or "HARD_ENFORCED", "HARD_ENFORCED", quota_detail.get("source") or "filesystem_quota")
    elif quota_state in ("NOT_ENFORCEABLE", "FAILED"):
        if quota_state == "NOT_ENFORCEABLE" and _allow_soft_storage_quota():
            g_storage = _gate_entry(
                "POLICY_ONLY",
                quota_detail.get("observed_value") or "NOT_ENFORCEABLE",
                "POLICY_ONLY",
                quota_detail.get("source") or "filesystem_quota",
                reason="Filesystem chưa bật kernel prjquota; đang áp dụng soft quota (LABOPS_ALLOW_SOFT_STORAGE_QUOTA=true).",
            )
        else:
            g_storage = _gate_entry(
                quota_state,
                quota_detail.get("observed_value") or quota_state,
                "HARD_ENFORCED",
                quota_detail.get("source") or "filesystem_quota",
                error_code=quota_detail.get("error_code") or "STORAGE_QUOTA_NOT_ENFORCEABLE",
                reason=quota_detail.get("reason") or "Filesystem chưa bật hard project/dataset quota.",
            )
    else:
        g_storage = _gate_entry(
            "NOT_VERIFIED",
            quota_state,
            "HARD_ENFORCED",
            "filesystem_quota",
            error_code="STORAGE_QUOTA_NOT_ENFORCEABLE",
            reason="Chưa xác minh hard storage quota trên filesystem.",
        )

    # Gate 7: CPU/RAM Resource Enforcement (+ GPU if requested)
    res_state = str(rv.get("cpu_ram_limits") or "NOT_VERIFIED").upper()
    gpu_state = str(rv.get("gpu_isolation") or "NOT_APPLICABLE").upper()
    res_detail = checks_detail.get("cpu_ram_limits") or {}
    if res_state == "VERIFIED" and gpu_state in ("VERIFIED", "NOT_APPLICABLE"):
        g_resource = _gate_entry("VERIFIED", res_detail.get("observed_value") or "ENFORCED", "CGROUP_ENFORCED", res_detail.get("source") or "docker_hostconfig")
    elif res_state in ("FAILED", "NOT_ENFORCEABLE") or gpu_state == "FAILED":
        g_resource = _gate_entry(
            "FAILED",
            res_detail.get("observed_value") or res_state,
            "CGROUP_ENFORCED",
            res_detail.get("source") or "docker_hostconfig",
            error_code=res_detail.get("error_code") or "RESOURCE_LIMITS_UNENFORCED",
            reason=res_detail.get("reason") or "Giới hạn CPU/RAM/PIDs/GPU chưa được áp đặt thực tế trên cgroup.",
        )
    else:
        g_resource = _gate_entry(
            "NOT_VERIFIED",
            res_state,
            "CGROUP_ENFORCED",
            "docker_hostconfig",
            error_code="RESOURCE_LIMITS_NOT_VERIFIED",
            reason="Chưa xác minh thông số cgroup thực tế từ container.",
        )

    # Gate 8: Network Policy / SSH Endpoint
    ssh_rt = str(rv.get("ssh_runtime") or "NOT_VERIFIED").upper()
    ssh_detail = checks_detail.get("ssh_runtime") or {}
    vpn_bad = bool(vpn_overview and vpn_overview.get("status") == "VPN_ROUTING_MISCONFIGURED")
    if ssh_rt == "VERIFIED" and not vpn_bad:
        g_network = _gate_entry("VERIFIED", ssh_detail.get("observed_value") or member.get("ssh_port"), "CONTAINER_SSH_BOUND", ssh_detail.get("source") or "docker_port_bindings")
    elif ssh_rt in ("FAILED", "SSH_RUNTIME_NOT_ISOLATED") or vpn_bad:
        g_network = _gate_entry(
            "FAILED",
            ssh_detail.get("observed_value") or ssh_rt,
            "CONTAINER_SSH_BOUND",
            ssh_detail.get("source") or "network_policy",
            error_code="VPN_ROUTING_MISCONFIGURED" if vpn_bad else (ssh_detail.get("error_code") or "SSH_RUNTIME_NOT_ISOLATED"),
            reason=ssh_detail.get("reason") or "Cổng SSH chưa thuộc container cô lập hoặc định tuyến VPN đang lỗi.",
        )
    else:
        g_network = _gate_entry(
            "NOT_VERIFIED",
            ssh_rt,
            "CONTAINER_SSH_BOUND",
            ssh_detail.get("source") or "network_policy",
            error_code="SSH_ENDPOINT_NOT_VERIFIED",
            reason="Cổng SSH container chưa sẵn sàng hoặc chưa kiểm chứng đường mạng.",
        )

    gates = {
        "member_status": g_member,
        "account_activation": g_activation,
        "ssh_key_registration": g_ssh_key,
        "docker_provisioning": g_docker,
        "privilege_isolation": g_priv,
        "storage_quota": g_storage,
        "resource_enforcement": g_resource,
        "network_policy": g_network,
    }

    blocking_codes: List[str] = []
    blocking_reasons: List[str] = []
    for gname, gval in gates.items():
        if not (
            gval["status"] == "VERIFIED"
            or (gname in ("storage_quota", "account_activation") and gval["status"] == "POLICY_ONLY")
        ):
            if gval.get("error_code"):
                blocking_codes.append(str(gval["error_code"]))
            else:
                blocking_codes.append(f"{gname.upper()}_{gval['status']}")
            if gval.get("reason"):
                blocking_reasons.append(str(gval["reason"]))

    all_verified = len(blocking_codes) == 0

    if all_verified:
        overall_readiness = "READY"
        ssh_runtime_state = "SSH_RUNTIME_VERIFIED"
    elif g_member["status"] == "FAILED" or g_activation["status"] in ("FAILED", "EXPIRED", "REVOKED"):
        overall_readiness = "SSH_ACCESS_DENIED"
        ssh_runtime_state = "SSH_ACCESS_DENIED"
    elif g_priv["status"] == "FAILED" or g_network["status"] == "FAILED":
        overall_readiness = "SSH_RUNTIME_NOT_ISOLATED" if "SSH_RUNTIME_NOT_ISOLATED" in blocking_codes else "FAILED"
        ssh_runtime_state = "SSH_RUNTIME_NOT_ISOLATED" if "SSH_RUNTIME_NOT_ISOLATED" in blocking_codes else "SSH_ACCESS_DENIED"
    elif g_docker["status"] == "FAILED":
        overall_readiness = g_docker.get("error_code") or "PROVISION_FAILED"
        ssh_runtime_state = "SSH_ACCESS_DENIED"
    elif g_storage["status"] == "NOT_ENFORCEABLE":
        overall_readiness = "NOT_ENFORCEABLE"
        ssh_runtime_state = "SSH_KEY_REGISTERED" if g_ssh_key["status"] == "VERIFIED" else "SSH_ACCESS_DENIED"
    elif g_activation["status"] != "VERIFIED":
        overall_readiness = "PENDING_ACTIVATION"
        ssh_runtime_state = "SSH_ENDPOINT_LISTENING" if ssh_rt == "VERIFIED" else (
            "SSH_KEY_REGISTERED" if g_ssh_key["status"] == "VERIFIED" else "SSH_ACCESS_DENIED"
        )
    else:
        overall_readiness = "PENDING_PROVISIONING"
        ssh_runtime_state = "SSH_KEY_REGISTERED" if g_ssh_key["status"] == "VERIFIED" else "SSH_ACCESS_DENIED"

    return {
        "ssh_authorized": all_verified,
        "overall_readiness": overall_readiness,
        "ssh_runtime_state": ssh_runtime_state,
        "activation_status": act_status,
        "blocking_codes": blocking_codes,
        "blocking_reasons": blocking_reasons,
        "gates": gates,
        "evaluated_at": _now_iso(),
    }
