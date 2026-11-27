"""
Unified 42-Action Web Admin Parity Catalog, Mandatory Intent-Confirmation & Disambiguation Gate,
Deterministic Confirmation Card Builder, and Recursive Secret Redaction for Assistant V4.

Security invariants:
- Arbitrary shell, SSH, sudo, raw SQL, network modification, self_grant_admin remain FORBIDDEN.
- ZERO silent execution: 100% of admin actions (R1..R4) require explicit Intent Confirmation.
- Ambiguous intents or multi-match targets enter CLARIFYING instead of guessing defaults.
- All plaintext secrets (web passwords, SSH temp passwords, private keys, passphrases, VPN configs,
  claim tokens) are redacted before persistence in AuditEventRecord / AgentToolAuditRecord / Memory.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field

from app.services.assistant_v4.event_store import redact_text

RiskTier = Literal["R1", "R2", "R3", "R4"]

# Secret keys redacted everywhere before persistence / group messages
SENSITIVE_FIELD_KEYS = frozenset(
    {
        "password",
        "temp_password",
        "temporary_password",
        "new_password",
        "initial_password",
        "reissued_web_password",
        "secret",
        "client_secret",
        "private_key",
        "encrypted_private_key",
        "encrypted_private_key_for_probe",
        "passphrase",
        "key_passphrase",
        "passphrase_for_probe",
        "wireguard_conf",
        "wireguard_config",
        "configuration",
        "preshared_key",
        "config_text",
        "claim_token",
        "link_code",
        "raw_token",
        "one_time_code",
        "confirmation_code",
        "ephemeral_secret",
    }
)


def redact_sensitive_payload(obj: Any) -> Any:
    """Recursively redact sensitive credential fields before logging, audit, or group replies."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            kl = str(k).strip().lower()
            if kl in SENSITIVE_FIELD_KEYS:
                out[k] = "[REDACTED]" if v not in (None, "") else v
            elif kl == "code" and isinstance(v, str) and v.strip():
                # Link / confirmation codes must not remain in audit logs; preserve error_code/member_code
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_sensitive_payload(v)
        return out
    if isinstance(obj, list):
        return [redact_sensitive_payload(item) for item in obj]
    if isinstance(obj, tuple):
        return [redact_sensitive_payload(item) for item in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


# ---------------- Typed Pydantic Input Schemas (extra='forbid') ----------------


class _BaseAdminArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetOnlyArgs(_BaseAdminArgs):
    target_id: str = Field(default="default", min_length=1, max_length=160)
    reason: str = Field(default="", max_length=300)


class CreateWebAccountArgs(_BaseAdminArgs):
    target_id: str = Field(default="new-member", min_length=1, max_length=64)
    username: str = Field(min_length=3, max_length=32)
    full_name: str = Field(min_length=2, max_length=120)
    member_code: str = Field(min_length=1, max_length=64)
    email: str = Field(default="", max_length=160)
    department_class: str = Field(default="", max_length=120)
    member_category: Literal["STUDENT", "LECTURER"] = "STUDENT"
    primary_supervisor_id: Optional[str] = Field(default=None, max_length=64)
    direct_manager_id: Optional[str] = Field(default=None, max_length=64)
    initial_password: Optional[str] = Field(default=None, max_length=128)
    reason: str = Field(default="", max_length=300)


class ReissueWebPasswordArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    new_password: Optional[str] = Field(default=None, min_length=12, max_length=128)
    reason: str = Field(default="", max_length=300)


class ReissueTempPasswordArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    reason: str = Field(default="", max_length=300)


class AssignSupervisorArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    primary_supervisor_id: str = Field(min_length=1, max_length=64)
    direct_manager_id: Optional[str] = Field(default=None, max_length=64)
    reason: str = Field(default="Admin cập nhật GVHD qua Zalo", max_length=300)


class UpdateQuotaArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    quota_gb: int = Field(ge=1, le=20000)
    reason: str = Field(default="", max_length=300)


class ArchiveMemberArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    retain_backup: bool = True
    reason: str = Field(default="", max_length=300)


class DeleteMemberPermanentArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    confirm_username: Optional[str] = Field(default=None, max_length=64)
    remove_files: bool = False
    reason: str = Field(default="", max_length=300)


class ProvisionSshArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    assigned_server: Optional[str] = Field(default=None, max_length=64)
    storage_pool_id: Optional[str] = Field(default=None, max_length=64)
    cpu_limit_cores: Optional[int] = Field(default=None, ge=1, le=128)
    ram_limit_gb: Optional[int] = Field(default=None, ge=1, le=1024)
    storage_quota_gb: Optional[int] = Field(default=None, ge=5, le=20000)
    gpu_permissions: Optional[List[str]] = None
    require_passphrase: bool = True
    reason: str = Field(default="", max_length=300)


class RotateSshKeyArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    require_passphrase: bool = True
    reason: str = Field(default="", max_length=300)


class ClaimSecretsArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    secret_type: Literal["channel_secrets", "key_bundle"] = "channel_secrets"
    reason: str = Field(default="", max_length=300)


class SshRequestDecisionArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=300)
    expected_version: Optional[int] = Field(default=None, ge=1)
    config: Optional[Dict[str, Any]] = None


class AddVpnDeviceArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    device_name: str = Field(default="New Device", min_length=1, max_length=64)
    reason: str = Field(default="", max_length=300)


class ToggleVpnDeviceArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    member_id: Optional[str] = Field(default=None, max_length=128)
    enable: bool = False
    reason: str = Field(default="", max_length=300)


class RevokeVpnDeviceArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    member_id: Optional[str] = Field(default=None, max_length=128)
    reason: str = Field(default="", max_length=300)


class AllocateGpuArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=64)
    mode: Literal["FULL_GPU_EXCLUSIVE", "SHARED"] = "FULL_GPU_EXCLUSIVE"
    requested_vram_gb: int = Field(default=0, ge=0, le=256)
    job_name: str = Field(default="Zalo-Admin-GPU", min_length=1, max_length=120)
    ttl_minutes: int = Field(default=240, ge=5, le=10080)
    reason: str = Field(default="", max_length=300)


class StopProcessArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=160)
    timeout_seconds: int = Field(default=10, ge=1, le=300)
    terminate_tree: bool = True
    reason: str = Field(default="", max_length=300)


class KillProcessArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=160)
    kill_tree: bool = True
    reason: str = Field(default="", max_length=300)


class DecideRepoOrScanArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    decision: Literal["APPROVED", "REJECTED"]
    reason: str = Field(default="Admin duyệt từ Zalo AI Agent", max_length=300)


class GenerateReportArgs(_BaseAdminArgs):
    target_id: Literal["daily", "weekly"] = "daily"
    force: bool = True
    reason: str = Field(default="", max_length=300)


class CreateZaloLinkCodeArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=64)
    member_id: Optional[str] = Field(default=None, max_length=64)
    role: Literal["ADMIN", "RESEARCHER", "STUDENT"] = "RESEARCHER"
    display_name: str = Field(default="", max_length=120)
    ttl_minutes: int = Field(default=15, ge=1, le=1440)
    reason: str = Field(default="", max_length=300)


class BindZaloIdentityArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=64)
    zalo_user_id: str = Field(min_length=1, max_length=128)
    member_id: Optional[str] = Field(default=None, max_length=64)
    role: Literal["ADMIN", "RESEARCHER", "STUDENT"] = "RESEARCHER"
    display_name: str = Field(default="", max_length=120)
    reason: str = Field(default="", max_length=300)


class UpdateSubscriptionsArgs(_BaseAdminArgs):
    target_id: str = Field(min_length=1, max_length=128)
    updates: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=300)


class ReloadV4ConfigArgs(_BaseAdminArgs):
    target_id: str = Field(default="v4-runtime", min_length=1, max_length=128)
    config_path: Optional[str] = Field(default=None, max_length=255)
    reason: str = Field(default="", max_length=300)


class RunV4RetentionArgs(_BaseAdminArgs):
    target_id: str = Field(default="v4-retention", min_length=1, max_length=128)
    dry_run: bool = True
    force: bool = True
    reason: str = Field(default="", max_length=300)


@dataclass(frozen=True)
class AdminActionSpec:
    action_id: str
    subsystem: str
    risk_tier: RiskTier
    target_type: str
    title_vi: str
    summary_vi: str
    args_model: Type[BaseModel]
    reversible: bool = True
    contains_secret: bool = False
    requires_typed_phrase: bool = False
    ambiguity_group: Optional[str] = None
    similar_actions: Tuple[str, ...] = ()
    required_role: str = "ADMIN"
    endpoint_ref: str = ""


# 8 Semantic Ambiguity Groups for Mandatory Intent Disambiguation
AMBIGUITY_GROUPS: Dict[str, Dict[str, Any]] = {
    "password_reset": {
        "prompt_vi": "Bạn muốn thao tác loại mật khẩu nào?",
        "options": [
            {"key": "reissue_ssh_temp_password", "label": "Cấp lại mật khẩu SSH tạm thời / lần đầu (reissue_ssh_temp_password — R3)", "hint": "Dùng cho đăng nhập SSH / đổi pass lần đầu vào container"},
            {"key": "reissue_web_password", "label": "Cấp lại mật khẩu đăng nhập Web Portal (reissue_web_password — R3)", "hint": "Dùng cho đăng nhập dashboard Web LabOps"},
            {"key": "BUNDLE:reissue_ssh_temp_password+reissue_web_password", "label": "Cấp lại cả hai (SSH tạm + Web Portal)", "hint": "Tạo phiếu xác nhận lần lượt cho cả SSH và Web"},
        ],
    },
    "member_lifecycle": {
        "prompt_vi": "Bạn muốn thực hiện mức độ khóa / đóng tài khoản nào?",
        "options": [
            {"key": "suspend_member", "label": "Tạm ngưng tài khoản (suspend_member — R3)", "hint": "Khóa tạm thời SSH & truy cập, có thể khôi phục sau"},
            {"key": "revoke_ssh_access", "label": "Chỉ thu hồi quyền SSH (revoke_ssh_access — R3)", "hint": "Ngắt SSH trong container nhưng vẫn giữ tài khoản Web"},
            {"key": "archive_member", "label": "Lưu trữ thành viên nghỉ lab (archive_member — R3)", "hint": "Đóng endpoint hoạt động, lưu backup & lịch sử"},
            {"key": "delete_member_permanent", "label": "Xóa vĩnh viễn thành viên (delete_member_permanent — R4)", "hint": "KHÔNG HOÀN TÁC — yêu cầu gõ OTP + CONFIRM DELETE <username>"},
        ],
    },
    "ssh_access_ops": {
        "prompt_vi": "Bạn muốn thao tác cụ thể nào đối với truy cập / khóa SSH?",
        "options": [
            {"key": "terminate_ssh_sessions", "label": "Ngắt các phiên SSH đang mở (terminate_ssh_sessions — R3)", "hint": "Đóng session hiện tại, vẫn giữ khóa SSH"},
            {"key": "revoke_ssh_key", "label": "Thu hồi một SSH public key cụ thể (revoke_ssh_key — R3)", "hint": "Chỉ vô hiệu hóa 1 khóa theo key_id"},
            {"key": "rotate_ssh_key", "label": "Xoay vòng cấp cặp khóa SSH mới (rotate_ssh_key — R3)", "hint": "Thu hồi khóa cũ và tạo cặp Ed25519 mới vào Vault"},
            {"key": "revoke_ssh_access", "label": "Thu hồi toàn bộ quyền truy cập SSH (revoke_ssh_access — R3)", "hint": "Gỡ toàn bộ authorized_keys và khóa cổng SSH"},
        ],
    },
    "compute_termination": {
        "prompt_vi": "Bạn muốn dừng tác vụ / giải phóng tài nguyên theo mức nào?",
        "options": [
            {"key": "stop_job", "label": "Dừng compute job / reservation (stop_job — R2)", "hint": "Dừng gọn theo job_id qua GPU scheduler"},
            {"key": "stop_process", "label": "Dừng tiến trình mềm SIGTERM (stop_process — R2)", "hint": "Cho phép tiến trình lưu checkpoint trước khi thoát"},
            {"key": "kill_process", "label": "Hủy cưỡng bức tiến trình SIGKILL (kill_process — R3)", "hint": "Chấm dứt ngay lập tức tiến trình đang treo"},
            {"key": "release_gpu_reservation", "label": "Giải phóng đặt chỗ GPU (release_gpu_reservation — R2)", "hint": "Thu hồi GPU đang giữ chỗ"},
        ],
    },
    "vpn_device_ops": {
        "prompt_vi": "Bạn muốn tạm tắt hay thu hồi vĩnh viễn thiết bị VPN?",
        "options": [
            {"key": "toggle_vpn_device", "label": "Bật / Tạm tắt thiết bị VPN (toggle_vpn_device — R2)", "hint": "Tạm vô hiệu hóa hoặc bật lại, giữ nguyên peer"},
            {"key": "revoke_vpn_device", "label": "Thu hồi vĩnh viễn thiết bị VPN (revoke_vpn_device — R3)", "hint": "Xóa peer khỏi WireGuard, không khôi phục peer cũ"},
        ],
    },
    "mram_review_decision": {
        "prompt_vi": "Bạn muốn xử lý đơn xin cấp SSH theo hướng nào?",
        "options": [
            {"key": "request_info_ssh_request", "label": "Yêu cầu bổ sung thông tin (NEED_INFO — R2)", "hint": "Mở lại để thành viên cập nhật lý do/đề tài"},
            {"key": "reject_ssh_request", "label": "Từ chối đơn xin SSH (REJECTED — R2)", "hint": "Đóng đơn từ chối kèm lý do"},
            {"key": "approve_ssh_request", "label": "Phê duyệt & cấp phát SSH (APPROVED — R3)", "hint": "Tạo workspace/container & cổng SSH thực"},
        ],
    },
    "incident_ops": {
        "prompt_vi": "Bạn muốn ghi nhận tiếp nhận hay đóng hẳn sự cố?",
        "options": [
            {"key": "ack_incident", "label": "Xác nhận đã tiếp nhận sự cố (ACK — R2)", "hint": "Chuyển sang ACKNOWLEDGED khi đang điều tra/xử lý"},
            {"key": "resolve_incident", "label": "Đóng sự cố đã xử lý xong (RESOLVE — R2)", "hint": "Chuyển sang RESOLVED kèm ghi chú khắc phục"},
        ],
    },
    "zalo_identity_ops": {
        "prompt_vi": "Bạn muốn thao tác nào với liên kết danh tính Zalo?",
        "options": [
            {"key": "create_zalo_link_code", "label": "Tạo mã tự liên kết /link CODE (R2)", "hint": "Sinh mã 1 lần gửi người dùng tự gắn"},
            {"key": "bind_zalo_identity", "label": "Gán trực tiếp Zalo User ID (R2)", "hint": "Admin gắn thẳng zalo_user_id vào tài khoản"},
            {"key": "revoke_zalo_identity", "label": "Gỡ liên kết Zalo hiện tại (R3)", "hint": "Thu hồi ngay quyền gọi bot của Zalo ID này"},
        ],
    },
}


# Canonical 42-Action Registry across 7 Subsystems
ADMIN_ACTION_CATALOG: Dict[str, AdminActionSpec] = {
    # Group 1 — Members, Passwords & Lifecycle (9)
    "create_web_account": AdminActionSpec(
        action_id="create_web_account",
        subsystem="Thành viên, Mật khẩu & Vòng đời",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Tạo tài khoản Web Portal cho thành viên",
        summary_vi="Khởi tạo hồ sơ Web-only (chưa cấp Linux/SSH/GPU)",
        args_model=CreateWebAccountArgs,
        reversible=False,
        contains_secret=True,
        similar_actions=("provision_member_ssh", "assign_supervisor"),
        endpoint_ref="POST /api/v1/mram/admin/member-web-accounts",
    ),
    "reissue_web_password": AdminActionSpec(
        action_id="reissue_web_password",
        subsystem="Thành viên, Mật khẩu & Vòng đời",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Cấp lại mật khẩu đăng nhập Web Portal",
        summary_vi="Đặt lại mật khẩu đăng nhập Web Portal và bắt buộc đổi ở lần đăng nhập kế tiếp",
        args_model=ReissueWebPasswordArgs,
        reversible=False,
        contains_secret=True,
        ambiguity_group="password_reset",
        similar_actions=("reissue_ssh_temp_password",),
        endpoint_ref="POST /api/v1/mram/admin/web-accounts/{member_id}/reissue-password",
    ),
    "reissue_ssh_temp_password": AdminActionSpec(
        action_id="reissue_ssh_temp_password",
        subsystem="Thành viên, Mật khẩu & Vòng đời",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Cấp lại mật khẩu SSH tạm thời / lần đầu",
        summary_vi="Hủy mật khẩu SSH tạm cũ, phát hành mật khẩu tạm mới vào Vault và đồng bộ SSH gate",
        args_model=ReissueTempPasswordArgs,
        reversible=False,
        contains_secret=True,
        ambiguity_group="password_reset",
        similar_actions=("reissue_web_password", "rotate_ssh_key"),
        endpoint_ref="POST /api/v1/v2/ssh-access/{member_id}/reissue-temp-password",
    ),
    "assign_supervisor": AdminActionSpec(
        action_id="assign_supervisor",
        subsystem="Thành viên, Mật khẩu & Vòng đời",
        risk_tier="R2",
        target_type="MEMBER",
        title_vi="Gán hoặc đổi Giảng viên hướng dẫn (GVHD)",
        summary_vi="Cập nhật primary_supervisor_id cho sinh viên",
        args_model=AssignSupervisorArgs,
        reversible=True,
        similar_actions=("create_web_account", "update_storage_quota"),
        endpoint_ref="PUT /api/v1/mram/admin/members/{member_id}/supervisor",
    ),
    "update_storage_quota": AdminActionSpec(
        action_id="update_storage_quota",
        subsystem="Thành viên, Mật khẩu & Vòng đời",
        risk_tier="R2",
        target_type="MEMBER",
        title_vi="Điều chỉnh hạn mức dung lượng lưu trữ",
        summary_vi="Cập nhật storage_quota_gb trên pool hiện tại của thành viên",
        args_model=UpdateQuotaArgs,
        reversible=True,
        similar_actions=("provision_member_ssh", "allocate_gpu"),
        endpoint_ref="PATCH /api/v1/admin/members/{user_id}/storage-quota",
    ),
    "suspend_member": AdminActionSpec(
        action_id="suspend_member",
        subsystem="Thành viên, Mật khẩu & Vòng đời",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Tạm ngưng tài khoản thành viên",
        summary_vi="Khóa tạm thời quyền truy cập SSH/workspace/GPU, có thể khôi phục lại",
        args_model=TargetOnlyArgs,
        reversible=True,
        ambiguity_group="member_lifecycle",
        similar_actions=("revoke_ssh_access", "archive_member", "delete_member_permanent"),
        endpoint_ref="POST /api/v1/v2/members/{member_id}/suspend",
    ),
    "restore_member": AdminActionSpec(
        action_id="restore_member",
        subsystem="Thành viên, Mật khẩu & Vòng đời",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Khôi phục tài khoản đang tạm ngưng",
        summary_vi="Đưa thành viên từ trạng thái tạm ngưng về hàng chờ xác minh kích hoạt lại",
        args_model=TargetOnlyArgs,
        reversible=True,
        ambiguity_group="member_lifecycle",
        similar_actions=("suspend_member", "verify_member_workspace"),
        endpoint_ref="POST /api/v1/admin/members/{user_id}/restore",
    ),
    "archive_member": AdminActionSpec(
        action_id="archive_member",
        subsystem="Thành viên, Mật khẩu & Vòng đời",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Lưu trữ (Archive) thành viên nghỉ lab",
        summary_vi="Thu hồi quyền truy cập hoạt động, giữ lại bản sao lưu và lịch sử kiểm toán",
        args_model=ArchiveMemberArgs,
        reversible=False,
        ambiguity_group="member_lifecycle",
        similar_actions=("suspend_member", "delete_member_permanent"),
        endpoint_ref="POST /api/v1/v2/members/{member_id}/archive",
    ),
    "delete_member_permanent": AdminActionSpec(
        action_id="delete_member_permanent",
        subsystem="Thành viên, Mật khẩu & Vòng đời",
        risk_tier="R4",
        target_type="MEMBER",
        title_vi="Xóa vĩnh viễn tài khoản thành viên",
        summary_vi="Xóa vĩnh viễn thành viên sau khi vượt qua pre-check sạch tài nguyên và xác nhận kép",
        args_model=DeleteMemberPermanentArgs,
        reversible=False,
        requires_typed_phrase=True,
        ambiguity_group="member_lifecycle",
        similar_actions=("archive_member", "suspend_member"),
        endpoint_ref="DELETE /api/v1/v2/members/{member_id}",
    ),
    # Group 2 — SSH Keys, Sessions & Container Workspaces (7)
    "provision_member_ssh": AdminActionSpec(
        action_id="provision_member_ssh",
        subsystem="Khóa SSH, Phiên SSH & Container Workspace",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Cấp phát workspace/container & cổng SSH",
        summary_vi="Chạy cấp phát workspace ưu tiên pool sdd/sdb, đồng bộ SSH gate và tạo secrets vào Vault",
        args_model=ProvisionSshArgs,
        reversible=True,
        contains_secret=True,
        ambiguity_group="ssh_access_ops",
        similar_actions=("verify_member_workspace", "create_web_account"),
        endpoint_ref="POST /api/v1/v2/ssh-access/provision",
    ),
    "rotate_ssh_key": AdminActionSpec(
        action_id="rotate_ssh_key",
        subsystem="Khóa SSH, Phiên SSH & Container Workspace",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Xoay vòng cặp khóa SSH Ed25519",
        summary_vi="Thu hồi toàn bộ khóa SSH cũ và tạo cặp khóa mới vào Vault",
        args_model=RotateSshKeyArgs,
        reversible=False,
        contains_secret=True,
        ambiguity_group="ssh_access_ops",
        similar_actions=("revoke_ssh_key", "revoke_ssh_access", "reissue_ssh_temp_password"),
        endpoint_ref="POST /api/v1/v2/ssh-access/{member_id}/rotate-key",
    ),
    "revoke_ssh_access": AdminActionSpec(
        action_id="revoke_ssh_access",
        subsystem="Khóa SSH, Phiên SSH & Container Workspace",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Thu hồi toàn bộ quyền truy cập SSH",
        summary_vi="Gỡ toàn bộ authorized_keys của thành viên và xác minh ngắt SSH tại container",
        args_model=TargetOnlyArgs,
        reversible=True,
        ambiguity_group="ssh_access_ops",
        similar_actions=("terminate_ssh_sessions", "revoke_ssh_key", "suspend_member"),
        endpoint_ref="POST /api/v1/v2/ssh-access/{member_id}/revoke",
    ),
    "revoke_ssh_key": AdminActionSpec(
        action_id="revoke_ssh_key",
        subsystem="Khóa SSH, Phiên SSH & Container Workspace",
        risk_tier="R3",
        target_type="SSH_KEY",
        title_vi="Thu hồi một SSH public key cụ thể",
        summary_vi="Chuyển khóa SSH mục tiêu sang trạng thái REVOKED",
        args_model=TargetOnlyArgs,
        reversible=False,
        ambiguity_group="ssh_access_ops",
        similar_actions=("rotate_ssh_key", "revoke_ssh_access"),
        endpoint_ref="POST /api/v1/v2/ssh-keys/{key_id}/revoke",
    ),
    "terminate_ssh_sessions": AdminActionSpec(
        action_id="terminate_ssh_sessions",
        subsystem="Khóa SSH, Phiên SSH & Container Workspace",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Ngắt toàn bộ phiên SSH đang hoạt động",
        summary_vi="Đóng cưỡng bức các phiên sshd đang kết nối trong workspace thành viên",
        args_model=TargetOnlyArgs,
        reversible=True,
        ambiguity_group="ssh_access_ops",
        similar_actions=("revoke_ssh_access", "stop_process"),
        endpoint_ref="POST /api/v1/v2/ssh-access/{member_id}/terminate-sessions",
    ),
    "verify_member_workspace": AdminActionSpec(
        action_id="verify_member_workspace",
        subsystem="Khóa SSH, Phiên SSH & Container Workspace",
        risk_tier="R1",
        target_type="MEMBER",
        title_vi="Kiểm định lại Verification Gate của workspace",
        summary_vi="Chạy reverify 6 điều kiện cô lập đường dẫn, CPU/RAM, SSH gate và GPU",
        args_model=TargetOnlyArgs,
        reversible=True,
        similar_actions=("provision_member_ssh", "evaluate_incidents"),
        endpoint_ref="POST /api/v1/v2/ssh-access/{member_id}/verify",
    ),
    "claim_member_secrets_once": AdminActionSpec(
        action_id="claim_member_secrets_once",
        subsystem="Khóa SSH, Phiên SSH & Container Workspace",
        risk_tier="R3",
        target_type="MEMBER",
        title_vi="Kiểm tra hoặc nhận gói bí mật một lần (Vault)",
        summary_vi="Kiểm tra Vault TTL hoặc bàn giao 1 lần mật khẩu tạm / passphrase theo policy",
        args_model=ClaimSecretsArgs,
        reversible=False,
        contains_secret=True,
        similar_actions=("reissue_ssh_temp_password", "reissue_web_password"),
        endpoint_ref="POST /api/v1/v2/ssh-access/{member_id}/claim-secrets",
    ),
    # Group 3 — MRAM SSH Request Decisions (5)
    "approve_ssh_request": AdminActionSpec(
        action_id="approve_ssh_request",
        subsystem="Phê duyệt Đơn xin cấp SSH MRAM",
        risk_tier="R3",
        target_type="SSH_REQUEST",
        title_vi="Phê duyệt và cấp phát đơn xin SSH",
        summary_vi="Chuyển đơn sang APPROVED và kích hoạt cấp phát container/SSH/pool",
        args_model=SshRequestDecisionArgs,
        reversible=False,
        ambiguity_group="mram_review_decision",
        similar_actions=("request_info_ssh_request", "reject_ssh_request"),
        endpoint_ref="POST /api/v1/mram/admin/ssh-requests/{request_id}/approve",
    ),
    "reject_ssh_request": AdminActionSpec(
        action_id="reject_ssh_request",
        subsystem="Phê duyệt Đơn xin cấp SSH MRAM",
        risk_tier="R2",
        target_type="SSH_REQUEST",
        title_vi="Từ chối đơn xin cấp SSH",
        summary_vi="Đóng đơn sang REJECTED kèm lý do gửi lại cho ứng viên",
        args_model=SshRequestDecisionArgs,
        reversible=False,
        ambiguity_group="mram_review_decision",
        similar_actions=("request_info_ssh_request", "approve_ssh_request"),
        endpoint_ref="POST /api/v1/mram/admin/ssh-requests/{request_id}/reject",
    ),
    "request_info_ssh_request": AdminActionSpec(
        action_id="request_info_ssh_request",
        subsystem="Phê duyệt Đơn xin cấp SSH MRAM",
        risk_tier="R2",
        target_type="SSH_REQUEST",
        title_vi="Yêu cầu bổ sung thông tin cho đơn SSH",
        summary_vi="Chuyển đơn sang NEED_INFO để ứng viên cập nhật đề tài / lý do",
        args_model=SshRequestDecisionArgs,
        reversible=True,
        ambiguity_group="mram_review_decision",
        similar_actions=("reject_ssh_request", "approve_ssh_request"),
        endpoint_ref="POST /api/v1/mram/admin/ssh-requests/{request_id}/request-info",
    ),
    "resend_ssh_zalo_invite": AdminActionSpec(
        action_id="resend_ssh_zalo_invite",
        subsystem="Phê duyệt Đơn xin cấp SSH MRAM",
        risk_tier="R1",
        target_type="SSH_REQUEST",
        title_vi="Gửi lại thông báo / liên kết duyệt Zalo cho đơn SSH",
        summary_vi="Phát lại thông báo đơn đang chờ duyệt tới Admin Zalo",
        args_model=TargetOnlyArgs,
        reversible=True,
        similar_actions=("approve_ssh_request", "create_zalo_link_code"),
        endpoint_ref="POST /api/v1/mram/admin/ssh-requests/{request_id}/notify-zalo",
    ),
    "sweep_mram_sla": AdminActionSpec(
        action_id="sweep_mram_sla",
        subsystem="Phê duyệt Đơn xin cấp SSH MRAM",
        risk_tier="R1",
        target_type="MRAM_QUEUE",
        title_vi="Quét SLA quá hạn đơn MRAM & hạn dùng SSH",
        summary_vi="Kiểm tra và đánh dấu các đơn quá SLA hoặc tài khoản SSH hết hạn",
        args_model=TargetOnlyArgs,
        reversible=True,
        similar_actions=("evaluate_incidents", "run_v4_retention"),
        endpoint_ref="POST /api/v1/mram/admin/mram/sweep-sla",
    ),
    # Group 4 — WireGuard VPN Devices (3)
    "add_vpn_device": AdminActionSpec(
        action_id="add_vpn_device",
        subsystem="Quản lý Thiết bị WireGuard VPN",
        risk_tier="R2",
        target_type="MEMBER",
        title_vi="Cấp mới thiết bị WireGuard VPN",
        summary_vi="Tạo peer VPN mới trên wg-easy Gateway cho thành viên",
        args_model=AddVpnDeviceArgs,
        reversible=True,
        contains_secret=True,
        ambiguity_group="vpn_device_ops",
        similar_actions=("toggle_vpn_device", "provision_member_ssh"),
        endpoint_ref="POST /api/v1/v2/members/{member_id}/devices",
    ),
    "toggle_vpn_device": AdminActionSpec(
        action_id="toggle_vpn_device",
        subsystem="Quản lý Thiết bị WireGuard VPN",
        risk_tier="R2",
        target_type="VPN_DEVICE",
        title_vi="Bật hoặc tạm tắt một thiết bị VPN",
        summary_vi="Chuyển trạng thái enabled của peer VPN mà vẫn giữ khóa hiện tại",
        args_model=ToggleVpnDeviceArgs,
        reversible=True,
        ambiguity_group="vpn_device_ops",
        similar_actions=("revoke_vpn_device", "add_vpn_device"),
        endpoint_ref="POST /api/v1/v2/members/{member_id}/devices/{device_id}/toggle",
    ),
    "revoke_vpn_device": AdminActionSpec(
        action_id="revoke_vpn_device",
        subsystem="Quản lý Thiết bị WireGuard VPN",
        risk_tier="R3",
        target_type="VPN_DEVICE",
        title_vi="Thu hồi vĩnh viễn peer VPN của thiết bị",
        summary_vi="Xóa vĩnh viễn thiết bị khỏi WireGuard (peer cũ không khôi phục lại được)",
        args_model=RevokeVpnDeviceArgs,
        reversible=False,
        ambiguity_group="vpn_device_ops",
        similar_actions=("toggle_vpn_device", "revoke_ssh_access"),
        endpoint_ref="DELETE /api/v1/v2/members/{member_id}/devices/{device_id}",
    ),
    # Group 5 — GPU, Jobs & Processes (5)
    "allocate_gpu": AdminActionSpec(
        action_id="allocate_gpu",
        subsystem="GPU, Jobs & Processes",
        risk_tier="R2",
        target_type="GPU",
        title_vi="Cấp phát / đặt chỗ GPU",
        summary_vi="Giữ GPU độc quyền hoặc chia sẻ VRAM cho thành viên qua GPU scheduler",
        args_model=AllocateGpuArgs,
        reversible=True,
        similar_actions=("release_gpu_reservation", "update_storage_quota"),
        endpoint_ref="POST /api/v1/gpus/allocate",
    ),
    "release_gpu_reservation": AdminActionSpec(
        action_id="release_gpu_reservation",
        subsystem="GPU, Jobs & Processes",
        risk_tier="R2",
        target_type="JOB",
        title_vi="Giải phóng đặt chỗ / phiên giữ GPU",
        summary_vi="Thu hồi reservation GPU theo reservation_id",
        args_model=TargetOnlyArgs,
        reversible=False,
        ambiguity_group="compute_termination",
        similar_actions=("stop_job", "stop_process"),
        endpoint_ref="POST /api/v1/gpus/reservations/{reservation_id}/release",
    ),
    "stop_job": AdminActionSpec(
        action_id="stop_job",
        subsystem="GPU, Jobs & Processes",
        risk_tier="R2",
        target_type="JOB",
        title_vi="Dừng compute / training job",
        summary_vi="Yêu cầu dừng job đang chạy qua bộ điều phối",
        args_model=TargetOnlyArgs,
        reversible=False,
        ambiguity_group="compute_termination",
        similar_actions=("release_gpu_reservation", "stop_process", "kill_process"),
        endpoint_ref="POST /api/v1/jobs/{job_id}/stop",
    ),
    "stop_process": AdminActionSpec(
        action_id="stop_process",
        subsystem="GPU, Jobs & Processes",
        risk_tier="R2",
        target_type="PROCESS",
        title_vi="Dừng mềm tiến trình hệ thống (SIGTERM)",
        summary_vi="Gửi tín hiệu SIGTERM qua Node Agent với thời gian ân hạn",
        args_model=StopProcessArgs,
        reversible=False,
        ambiguity_group="compute_termination",
        similar_actions=("kill_process", "stop_job", "terminate_ssh_sessions"),
        endpoint_ref="POST /api/v1/processes/{ref}/stop",
    ),
    "kill_process": AdminActionSpec(
        action_id="kill_process",
        subsystem="GPU, Jobs & Processes",
        risk_tier="R3",
        target_type="PROCESS",
        title_vi="Hủy cưỡng bức tiến trình (SIGKILL)",
        summary_vi="Gửi SIGKILL cưỡng bức qua service Processes (không chạy shell tự do)",
        args_model=KillProcessArgs,
        reversible=False,
        ambiguity_group="compute_termination",
        similar_actions=("stop_process", "stop_job"),
        endpoint_ref="POST /api/v1/processes/{ref}/kill",
    ),
    # Group 6 — Repository Guard & Security Scans (3)
    "decide_repository_request": AdminActionSpec(
        action_id="decide_repository_request",
        subsystem="Kiểm duyệt Kho Mã & Quét Bảo mật",
        risk_tier="R2",
        target_type="REPOSITORY_REQUEST",
        title_vi="Phê duyệt hoặc từ chối yêu cầu kho mã",
        summary_vi="Ghi nhận quyết định APPROVED/REJECTED cho Repository Guard request",
        args_model=DecideRepoOrScanArgs,
        reversible=False,
        similar_actions=("rescan_repository_request", "decide_security_scan"),
        endpoint_ref="POST /api/v1/repositories/requests/{request_id}/decision",
    ),
    "rescan_repository_request": AdminActionSpec(
        action_id="rescan_repository_request",
        subsystem="Kiểm duyệt Kho Mã & Quét Bảo mật",
        risk_tier="R1",
        target_type="REPOSITORY_REQUEST",
        title_vi="Kích hoạt quét lại kho mã",
        summary_vi="Gửi yêu cầu quét lại bảo mật tới Repository Guard Agent",
        args_model=TargetOnlyArgs,
        reversible=True,
        similar_actions=("decide_repository_request", "verify_member_workspace"),
        endpoint_ref="POST /api/v1/repositories/requests/{request_id}/rescan",
    ),
    "decide_security_scan": AdminActionSpec(
        action_id="decide_security_scan",
        subsystem="Kiểm duyệt Kho Mã & Quét Bảo mật",
        risk_tier="R2",
        target_type="SECURITY_SCAN",
        title_vi="Quyết định kết quả quét bảo mật",
        summary_vi="Phê duyệt hoặc từ chối bản quét bảo mật theo scan_id",
        args_model=DecideRepoOrScanArgs,
        reversible=False,
        similar_actions=("decide_repository_request", "ack_incident"),
        endpoint_ref="POST /api/v1/security/scans/{scan_id}/decision",
    ),
    # Group 7 — Incidents, Reports, Zalo Identity & V4 Config (10)
    "ack_incident": AdminActionSpec(
        action_id="ack_incident",
        subsystem="Sự cố, Báo cáo, Liên kết Zalo & Cấu hình AI V4",
        risk_tier="R2",
        target_type="INCIDENT",
        title_vi="Xác nhận đang tiếp nhận sự cố (ACK)",
        summary_vi="Đánh dấu sự cố sang ACKNOWLEDGED để theo dõi quá trình xử lý",
        args_model=TargetOnlyArgs,
        reversible=True,
        ambiguity_group="incident_ops",
        similar_actions=("resolve_incident", "evaluate_incidents"),
        endpoint_ref="POST /api/v1/ai-assistant/incidents/{incident_id}/ack",
    ),
    "resolve_incident": AdminActionSpec(
        action_id="resolve_incident",
        subsystem="Sự cố, Báo cáo, Liên kết Zalo & Cấu hình AI V4",
        risk_tier="R2",
        target_type="INCIDENT",
        title_vi="Đánh dấu đã khắc phục xong sự cố (RESOLVE)",
        summary_vi="Đóng sự cố sang RESOLVED kèm ghi chú nguyên nhân/biện pháp xử lý",
        args_model=TargetOnlyArgs,
        reversible=False,
        ambiguity_group="incident_ops",
        similar_actions=("ack_incident", "evaluate_incidents"),
        endpoint_ref="POST /api/v1/ai-assistant/incidents/{incident_id}/resolve",
    ),
    "evaluate_incidents": AdminActionSpec(
        action_id="evaluate_incidents",
        subsystem="Sự cố, Báo cáo, Liên kết Zalo & Cấu hình AI V4",
        risk_tier="R1",
        target_type="SYSTEM",
        title_vi="Đánh giá tức thời các rule cảnh báo sự cố",
        summary_vi="Thu thập telemetry snapshot hiện tại và chạy rule engine đánh giá sự cố",
        args_model=TargetOnlyArgs,
        reversible=True,
        similar_actions=("ack_incident", "sweep_mram_sla"),
        endpoint_ref="POST /api/v1/ai-assistant/incidents/evaluate",
    ),
    "generate_report": AdminActionSpec(
        action_id="generate_report",
        subsystem="Sự cố, Báo cáo, Liên kết Zalo & Cấu hình AI V4",
        risk_tier="R1",
        target_type="REPORT",
        title_vi="Khởi tạo báo cáo vận hành hệ thống",
        summary_vi="Tạo mới báo cáo Daily Executive Briefing hoặc báo cáo tuần",
        args_model=GenerateReportArgs,
        reversible=True,
        similar_actions=("evaluate_incidents", "reload_v4_config"),
        endpoint_ref="POST /api/v1/ai-assistant/reports/generate",
    ),
    "create_zalo_link_code": AdminActionSpec(
        action_id="create_zalo_link_code",
        subsystem="Sự cố, Báo cáo, Liên kết Zalo & Cấu hình AI V4",
        risk_tier="R2",
        target_type="ZALO_IDENTITY",
        title_vi="Tạo mã liên kết tài khoản LabOps <-> Zalo",
        summary_vi="Phát hành mã /link có TTL ngắn để người dùng tự gắn Zalo ID",
        args_model=CreateZaloLinkCodeArgs,
        reversible=True,
        contains_secret=True,
        ambiguity_group="zalo_identity_ops",
        similar_actions=("bind_zalo_identity", "revoke_zalo_identity"),
        endpoint_ref="POST /api/v1/ai-assistant/identity-links/code",
    ),
    "bind_zalo_identity": AdminActionSpec(
        action_id="bind_zalo_identity",
        subsystem="Sự cố, Báo cáo, Liên kết Zalo & Cấu hình AI V4",
        risk_tier="R2",
        target_type="ZALO_IDENTITY",
        title_vi="Gán trực tiếp Zalo User ID cho thành viên",
        summary_vi="Liên kết trực tiếp zalo_user_id vào tài khoản LabOps kèm kiểm tra phân quyền",
        args_model=BindZaloIdentityArgs,
        reversible=True,
        ambiguity_group="zalo_identity_ops",
        similar_actions=("create_zalo_link_code", "revoke_zalo_identity"),
        endpoint_ref="POST /api/v1/ai-assistant/identity-links/bind",
    ),
    "revoke_zalo_identity": AdminActionSpec(
        action_id="revoke_zalo_identity",
        subsystem="Sự cố, Báo cáo, Liên kết Zalo & Cấu hình AI V4",
        risk_tier="R3",
        target_type="ZALO_IDENTITY",
        title_vi="Thu hồi liên kết Zalo của thành viên",
        summary_vi="Gỡ liên kết Zalo ID và thu hồi mọi phiên/đề xuất đang mở trên Zalo",
        args_model=TargetOnlyArgs,
        reversible=True,
        ambiguity_group="zalo_identity_ops",
        similar_actions=("bind_zalo_identity", "suspend_member"),
        endpoint_ref="DELETE /api/v1/ai-assistant/identity-links/{link_id}",
    ),
    "update_alert_subscriptions": AdminActionSpec(
        action_id="update_alert_subscriptions",
        subsystem="Sự cố, Báo cáo, Liên kết Zalo & Cấu hình AI V4",
        risk_tier="R2",
        target_type="ZALO_SUBSCRIPTION",
        title_vi="Cập nhật đăng ký nhận cảnh báo Zalo",
        summary_vi="Chỉnh sửa kênh, mức độ hoặc quiet hours cho người nhận Zalo",
        args_model=UpdateSubscriptionsArgs,
        reversible=True,
        similar_actions=("reload_v4_config", "ack_incident"),
        endpoint_ref="POST /api/v1/ai-assistant/subscriptions",
    ),
    "reload_v4_config": AdminActionSpec(
        action_id="reload_v4_config",
        subsystem="Sự cố, Báo cáo, Liên kết Zalo & Cấu hình AI V4",
        risk_tier="R2",
        target_type="V4_SYSTEM",
        title_vi="Nạp lại cấu hình Assistant V4",
        summary_vi="Đồng bộ lại cờ tính năng YAML/môi trường cho AssistantV4Facade",
        args_model=ReloadV4ConfigArgs,
        reversible=True,
        similar_actions=("run_v4_retention", "evaluate_incidents"),
        endpoint_ref="POST /api/v1/ai-assistant/v4/config/reload",
    ),
    "run_v4_retention": AdminActionSpec(
        action_id="run_v4_retention",
        subsystem="Sự cố, Báo cáo, Liên kết Zalo & Cấu hình AI V4",
        risk_tier="R2",
        target_type="V4_SYSTEM",
        title_vi="Chạy dọn dẹp retention dữ liệu V4",
        summary_vi="Thực thi chu trình retention theo chế độ dry_run hoặc xóa thực",
        args_model=RunV4RetentionArgs,
        reversible=True,
        similar_actions=("reload_v4_config", "sweep_mram_sla"),
        endpoint_ref="POST /api/v1/ai-assistant/v4/retention/run",
    ),
}

assert len(ADMIN_ACTION_CATALOG) == 42, f"Expected 42 actions, found {len(ADMIN_ACTION_CATALOG)}"

# Keep backward-compatibility alias while mapping unified catalog
ACTION_RISK_TIERS: Dict[str, str] = {k: spec.risk_tier for k, spec in ADMIN_ACTION_CATALOG.items()}


def get_action_spec(action_type: str) -> Optional[AdminActionSpec]:
    return ADMIN_ACTION_CATALOG.get(str(action_type or "").strip().lower())


def validate_action_args(action_type: str, raw_args: Dict[str, Any]) -> Dict[str, Any]:
    spec = get_action_spec(action_type)
    if spec is None:
        return dict(raw_args or {})
    parsed = spec.args_model.model_validate(raw_args or {})
    return parsed.model_dump()


def _get_services(service_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ctx = dict(service_context or {})
    if "labops_service" not in ctx or ctx["labops_service"] is None:
        from app.services.labops_service import service as core_service

        ctx["labops_service"] = core_service
    if "mram_service" not in ctx or ctx["mram_service"] is None:
        from app.services.mram_service import mram_service

        ctx["mram_service"] = mram_service
    return ctx


def _strip_accents(text: str) -> str:
    try:
        from app.services.mram_service import _strip_vn_accents

        return _strip_vn_accents(text)
    except Exception:
        return str(text or "").lower().strip()


def _resolve_member_record(labops_svc: Any, identifier: str) -> Optional[Dict[str, Any]]:
    ident = str(identifier or "").strip().lstrip("@")
    if not ident:
        return None
    if hasattr(labops_svc, "ensure_members_fresh"):
        try:
            labops_svc.ensure_members_fresh()
        except Exception:
            pass
    if hasattr(labops_svc, "_find_member"):
        try:
            return labops_svc._find_member(ident)
        except Exception:
            pass
    members = getattr(labops_svc, "members", None) or []
    il = ident.lower()
    for m in members:
        if str(m.get("id") or "").lower() == il or str(m.get("username") or "").lower() == il:
            return m
    return None


def find_matching_members(labops_svc: Any, query: str) -> List[Dict[str, Any]]:
    """Find exact or partial member candidates; detects multi-match target ambiguity."""
    q = str(query or "").strip().lstrip("@")
    if not q:
        return []
    if hasattr(labops_svc, "ensure_members_fresh"):
        try:
            labops_svc.ensure_members_fresh()
        except Exception:
            pass
    members = list(getattr(labops_svc, "members", None) or [])
    ql = q.lower()
    exact = [
        m for m in members
        if str(m.get("username") or "").lower() == ql or str(m.get("id") or "").lower() == ql
    ]
    if len(exact) == 1:
        return exact
    q_norm = _strip_accents(q)
    partial: List[Dict[str, Any]] = []
    for m in members:
        if str(m.get("status") or "").upper() == "DELETED":
            continue
        uname = str(m.get("username") or "").lower()
        fname = _strip_accents(str(m.get("fullName") or m.get("full_name") or ""))
        mcode = str(m.get("member_code") or "").lower()
        if q_norm in uname or (q_norm and q_norm in fname.split()) or q_norm == mcode:
            partial.append(m)
    return partial


def _detect_ambiguity_group_from_text(raw_text: str) -> Optional[str]:
    norm = _strip_accents(raw_text)
    if not norm:
        return None
    # 1. Password reset without explicit web or ssh discriminator
    if any(w in norm for w in ("mat khau", "password", "pass")) and any(
        w in norm for w in ("reset", "cap lai", "doi", "dat lai", "quen", "khoi phuc")
    ):
        has_web = any(w in norm for w in ("web", "portal", "dashboard", "trang chu"))
        has_ssh = any(w in norm for w in ("ssh", "tam", "lan dau", "container", "terminal", "temp"))
        if not has_web and not has_ssh:
            return "password_reset"

    # 2. Member lock/suspend/disable/delete ambiguity
    if any(w in norm for w in ("khoa", "chan", "tat tai khoan", "vo hieu", "xoa", "dong bang")):
        if not any(w in norm for w in ("tam ngung", "suspend", "chi ssh", "luu tru", "archive", "vinh vien", "permanent", "vpn", "key")):
            return "member_lifecycle"

    # 3. SSH ops ambiguity
    if "ssh" in norm and any(w in norm for w in ("reset", "ngat", "tat", "huy", "thu hoi", "xoay")):
        if not any(w in norm for w in ("phien", "session", "xoay", "rotate", "toan bo", "key_id", "ssh-key")):
            return "ssh_access_ops"

    # 4. Compute stop/kill ambiguity
    if any(w in norm for w in ("dung", "kill", "huy", "nha")) and any(
        w in norm for w in ("job", "tac vu", "tien trinh", "process", "gpu", "train")
    ):
        if not any(w in norm for w in ("sigterm", "sigkill", "cuong buc", "force", "giai phong gpu", "stop_job")):
            return "compute_termination"

    # 5. VPN ambiguity
    if "vpn" in norm and any(w in norm for w in ("tat", "khoa", "xoa", "thu hoi")):
        if not any(w in norm for w in ("tam tat", "toggle", "vinh vien", "revoke_vpn")):
            return "vpn_device_ops"

    return None


def check_disambiguation(
    *,
    action_type: Optional[str] = None,
    raw_text: str = "",
    target_query: Optional[str] = None,
    explicit_action: bool = False,
    resolved_candidates: Optional[List[Dict[str, Any]]] = None,
    service_context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Mandatory Disambiguation Gate:
    - Returns CLARIFYING payload (with numbered choices 1..N) if intent is ambiguous across
      similar admin actions OR multiple target entities match.
    - Returns None when intent and single target are unambiguous.
    """
    svcs = _get_services(service_context)
    labops_svc = svcs.get("labops_service")

    # 1. Resolve target ambiguity first if multiple entities match
    candidates = list(resolved_candidates or [])
    if not candidates and target_query and labops_svc is not None:
        if not action_type or (get_action_spec(action_type) and get_action_spec(action_type).target_type == "MEMBER"):
            candidates = find_matching_members(labops_svc, target_query)

    if len(candidates) > 1:
        options: List[Dict[str, Any]] = []
        lines = [
            f"🔎 Tìm thấy {len(candidates)} đối tượng khớp với `{target_query}`. Vui lòng chọn đúng đối tượng (chắn chắn không đoán nhầm):"
        ]
        for idx, cand in enumerate(candidates[:10], 1):
            cid = str(cand.get("id") or cand.get("target_id") or "")
            uname = str(cand.get("username") or cand.get("name") or cid)
            fname = str(cand.get("fullName") or cand.get("full_name") or cand.get("title") or uname)
            st = str(cand.get("status") or cand.get("ssh_access_state") or "")
            options.append(
                {
                    "index": idx,
                    "kind": "TARGET",
                    "target_id": cid or uname,
                    "username": uname,
                    "label": f"{fname} (@{uname})",
                    "status": st,
                    "action_type": action_type,
                }
            )
            lines.append(f"{idx}. {fname} (@{uname}) — ID: {cid or uname}" + (f" [{st}]" if st else ""))
        lines.append("\n👉 Nhắn số thứ tự (`1`, `2`, ...) để chọn đúng đối tượng, hoặc nhắn `HỦY`.")
        return {
            "status": "CLARIFYING",
            "clarification_kind": "TARGET_AMBIGUITY",
            "target_query": target_query,
            "options": options,
            "message": "\n".join(lines),
        }

    # 2. Check action/intent ambiguity across AMBIGUITY_GROUPS
    detected_group: Optional[str] = None
    if not explicit_action:
        detected_group = _detect_ambiguity_group_from_text(raw_text)
        if not detected_group and action_type:
            spec = get_action_spec(action_type)
            # Only force clarifying on ambiguity_group if raw_text was genuinely underspecified
            if spec and spec.ambiguity_group and not raw_text.strip():
                detected_group = spec.ambiguity_group

    if detected_group and detected_group in AMBIGUITY_GROUPS:
        grp = AMBIGUITY_GROUPS[detected_group]
        single_target = candidates[0] if len(candidates) == 1 else None
        t_label = ""
        t_id = str(target_query or "")
        if single_target:
            uname = str(single_target.get("username") or single_target.get("id") or t_id)
            t_id = uname
            t_label = (
                f"\n• Thành viên: {single_target.get('fullName') or single_target.get('full_name') or uname} (@{uname})"
                f" | Role: {single_target.get('role') or 'RESEARCHER'} | Trạng thái: {single_target.get('status') or 'N/A'}"
                f" | SSH: {single_target.get('ssh_access_state') or single_target.get('ssh_status') or 'N/A'}"
                + (f" (port {single_target.get('ssh_port')})" if single_target.get("ssh_port") else "")
            )
        options = []
        lines = [f"🤔 Tôi cần xác nhận đúng ý của Admin trước khi tạo phiếu thao tác ({detected_group}):{t_label}", grp["prompt_vi"]]
        for idx, opt in enumerate(grp["options"], 1):
            options.append(
                {
                    "index": idx,
                    "kind": "ACTION",
                    "action_key": opt["key"],
                    "target_id": t_id,
                    "label": opt["label"],
                    "hint": opt["hint"],
                }
            )
            lines.append(f"{idx}. {opt['label']}\n   ↳ {opt['hint']}")
        lines.append("\n👉 Nhắn số thứ tự (`1`, `2`, ...) để chọn, hoặc nhắn `HỦY` để dừng.")
        return {
            "status": "CLARIFYING",
            "clarification_kind": "INTENT_AMBIGUITY",
            "ambiguity_group": detected_group,
            "target_id": t_id,
            "options": options,
            "message": "\n".join(lines),
        }

    return None


def inspect_target_preview(
    *,
    action_type: str,
    target_id: str,
    args: Dict[str, Any],
    service_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Read-only inspection of current state, before->after diff, and typed confirmation phrase."""
    spec = get_action_spec(action_type)
    svcs = _get_services(service_context)
    labops_svc = svcs.get("labops_service")
    mram_svc = svcs.get("mram_service")

    target_label = str(target_id or "")
    before_state = "Hiện trạng đã xác minh"
    after_state = spec.summary_vi if spec else action_type
    typed_phrase: Optional[str] = None
    impact_warnings: List[str] = []

    member = _resolve_member_record(labops_svc, target_id) if (labops_svc is not None and spec and spec.target_type == "MEMBER") else None
    if member:
        uname = str(member.get("username") or target_id)
        target_label = f"@{uname} ({member.get('fullName') or member.get('full_name') or uname}, ID={member.get('id')})"
        before_state = (
            f"status={member.get('status')}, web_status={member.get('provisioning_state')}, "
            f"ssh={member.get('ssh_access_state') or member.get('ssh_status')}, "
            f"server={member.get('assigned_server') or 'N/A'}, quota={member.get('storage_quota_gb', 'N/A')}GiB"
        )
        if action_type == "reissue_ssh_temp_password":
            after_state = "Hủy mật khẩu SSH tạm cũ; sinh mật khẩu tạm mới vào Vault; password_status=CHANGE_REQUIRED"
        elif action_type == "reissue_web_password":
            after_state = "Đặt lại mật khẩu đăng nhập Web Portal; must_change_password=True"
        elif action_type == "update_storage_quota":
            after_state = f"Đổi storage_quota_gb: {member.get('storage_quota_gb', '?')}GiB -> {args.get('quota_gb')}GiB"
        elif action_type == "suspend_member":
            after_state = "Tạm ngưng tài khoản (SUSPENDED), ngắt SSH/GPU đang hoạt động"
        elif action_type == "restore_member":
            after_state = "Mở khôi phục tài khoản về hàng chờ xác minh kích hoạt"
        elif action_type == "archive_member":
            after_state = f"Chuyển tài khoản sang ARCHIVED (retain_backup={args.get('retain_backup', True)})"
        elif action_type == "delete_member_permanent":
            typed_phrase = f"CONFIRM DELETE {uname}"
            after_state = f"XÓA VĨNH VIỄN thành viên @{uname} (remove_files={bool(args.get('remove_files', False))})"
            try:
                prev = labops_svc.preview_member_deletion(member["id"], actor="preview", dry_run=True)
                blockers = prev.get("blockers") or prev.get("blocking_resources") or []
                if blockers:
                    impact_warnings.append(f"Cảnh báo tài nguyên liên quan: {blockers}")
            except Exception as exc:
                impact_warnings.append(f"Pre-check xóa thành viên: {exc}")
    elif action_type == "delete_member_permanent":
        clean_u = str(args.get("confirm_username") or target_id).lstrip("@")
        typed_phrase = f"CONFIRM DELETE {clean_u}"
    elif spec and spec.target_type == "SSH_REQUEST" and mram_svc is not None:
        try:
            req = mram_svc.get_request_detail(target_id, viewer_username="admin", is_admin=True)
            uname = req.get("username") or target_id
            target_label = f"Đơn {target_id} (@{uname})"
            before_state = f"status={req.get('status')}, đề tài={req.get('project_title') or 'N/A'}"
        except Exception:
            pass

    return {
        "target_label": target_label,
        "before_state": before_state,
        "after_state": after_state,
        "reversible": bool(spec.reversible) if spec else False,
        "typed_phrase_required": typed_phrase,
        "warnings": impact_warnings,
    }


def get_similar_alternatives_for_action(action_type: str) -> List[Dict[str, str]]:
    spec = get_action_spec(action_type)
    if spec is None:
        return []
    alts: List[Dict[str, str]] = []
    seen = {action_type}
    # First from explicit similar_actions, then from same ambiguity_group
    candidates = list(spec.similar_actions)
    if spec.ambiguity_group and spec.ambiguity_group in AMBIGUITY_GROUPS:
        for opt in AMBIGUITY_GROUPS[spec.ambiguity_group]["options"]:
            k = str(opt.get("key") or "")
            if not k.startswith("BUNDLE:") and k not in seen and k not in candidates:
                candidates.append(k)
    for aid in candidates:
        if aid in seen:
            continue
        seen.add(aid)
        alt_spec = get_action_spec(aid)
        if alt_spec is not None:
            alts.append(
                {
                    "action_id": alt_spec.action_id,
                    "risk_tier": alt_spec.risk_tier,
                    "title_vi": alt_spec.title_vi,
                    "summary_vi": alt_spec.summary_vi,
                }
            )
    return alts[:4]


def build_confirmation_card(
    *,
    action_id: str,
    action_type: str,
    risk_tier: str,
    target_type: str,
    target_id: str,
    preview: Dict[str, Any],
    one_time_code: Optional[str],
    ttl_seconds: int,
    routed_to_dm: bool = False,
    short_otp_enabled: bool = False,
    dashboard_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build deterministic 4-part Intent-Confirmation Card ("Phiếu Xác Nhận Hiểu Đúng"):
    1) System Understanding (action_id + Vietnamese explanation + risk tier)
    2) Target & Impact Diff (Before -> After + Reversible flag)
    3) Similar Alternatives (numbered `[2]...`, `[3]...` with `ĐỔI <số>`)
    4) Confirmation / Switch / Cancel controls (OTP NEVER leaked into group_notice!)
    """
    spec = get_action_spec(action_type)
    title = spec.title_vi if spec else action_type
    rev_txt = "Có (có thể khôi phục/hoàn tác)" if preview.get("reversible", spec.reversible if spec else False) else "KHÔNG (không thể hoàn tác trạng thái cũ)"
    alts = get_similar_alternatives_for_action(action_type)

    base_lines = [
        f"📝 PHIẾU XÁC NHẬN HIỂU ĐÚNG [{risk_tier}] — Mã: `{action_id}`",
        "━━━━━━━━━━━━━━━━━━━━",
        f"1️⃣ Ý hiểu của hệ thống: {title} (`{action_type}`)",
        f"2️⃣ Đối tượng & Tác động: {preview.get('target_label') or target_id}",
        f"   • Trước: {preview.get('before_state') or 'Đã kiểm tra'}",
        f"   • Sau: {preview.get('after_state') or (spec.summary_vi if spec else action_type)}",
        f"   • Hoàn tác (Reversible): {rev_txt}",
    ]
    for w in preview.get("warnings") or []:
        base_lines.append(f"   • ⚠️ {w}")

    if alts:
        base_lines.append("3️⃣ Các lựa chọn tương tự khác (nếu hiểu nhầm ý bạn):")
        for idx, alt in enumerate(alts, 2):
            base_lines.append(f"   [{idx}] {alt['title_vi']} (`{alt['action_id']}`, {alt['risk_tier']})")
        base_lines.append("   👉 Nhắn `ĐỔI <số>` (ví dụ `ĐỔI 2`) để chuyển sang thao tác tương ứng.")
    else:
        base_lines.append("3️⃣ Lựa chọn tương tự: Không có thao tác dễ nhầm tương ứng.")

    dm_lines = list(base_lines)
    typed_phrase = preview.get("typed_phrase_required")

    if one_time_code:
        if typed_phrase:
            confirm_cmd = f"/confirm {action_id} {one_time_code} {typed_phrase}"
            dm_lines.append(
                f"4️⃣ Xác nhận thực thi (hết hạn sau {ttl_seconds}s):\n"
                f"   • Bắt buộc gõ đúng: `{confirm_cmd}`\n"
                "   • Nhắn `ĐỔI <số>` để đổi loại thao tác hoặc `HỦY` để dừng (không dùng OK/YES/CÓ)."
            )
        else:
            short_hint = f" (hoặc nhắn thẳng `{one_time_code}` trong DM 1-1 nếu chỉ có 1 phiếu chờ)" if short_otp_enabled else ""
            dm_lines.append(
                f"4️⃣ Xác nhận thực thi (hết hạn sau {ttl_seconds}s):\n"
                f"   • Gửi đúng lệnh: `/confirm {action_id} {one_time_code}`{short_hint}\n"
                "   • Nhắn `ĐỔI <số>` để chuyển lựa chọn khác hoặc `HỦY` để hủy bỏ (hệ thống chặn OK/YES/CÓ)."
            )
    else:
        dash_url = dashboard_path or "/dashboard/ai-assistant"
        dm_lines.append(
            f"4️⃣ Xác nhận thực thi: Thao tác đang yêu cầu Step-Up Auth trên Dashboard (`{dash_url}`) "
            "(hoặc nhắn `HỦY` để đóng phiếu)."
        )

    dm_card_text = "\n".join(dm_lines)
    group_notice_text: Optional[str] = None
    if routed_to_dm:
        group_lines = list(base_lines)
        group_lines.append(
            "4️⃣ Xác nhận bảo mật: Phiếu xác nhận kèm mã OTP đã được gửi riêng vào **DM 1-1** của Admin "
            "(tuyệt đối không xác nhận hoặc hiển thị OTP trong nhóm chat)."
        )
        group_notice_text = "\n".join(group_lines)

    return {
        "action_id": action_id,
        "action_type": action_type,
        "risk_tier": risk_tier,
        "title_vi": title,
        "reversible": bool(preview.get("reversible", spec.reversible if spec else False)),
        "similar_alternatives": [
            {"switch_number": idx, **alt} for idx, alt in enumerate(alts, 2)
        ],
        "typed_phrase_required": typed_phrase,
        "routed_to_dm": routed_to_dm,
        "dm_card_text": dm_card_text,
        "group_notice_text": group_notice_text,
    }


def _resolve_vpn_member_and_device(labops_svc: Any, target_id: str, member_id: Optional[str]) -> Tuple[str, str]:
    raw_t = str(target_id or "").strip()
    dev_id = raw_t
    resolved_mid = str(member_id or "").strip()
    if ":" in raw_t and not resolved_mid:
        resolved_mid, dev_id = raw_t.split(":", 1)
    if resolved_mid:
        m = _resolve_member_record(labops_svc, resolved_mid)
        if m:
            resolved_mid = str(m["id"])
        return resolved_mid, dev_id
    for m in getattr(labops_svc, "members", None) or []:
        devs = ((m.get("wireguard") or {}).get("devices") or [])
        for d in devs:
            if str(d.get("id") or "") == dev_id:
                return str(m["id"]), dev_id
    raise LookupError(f"Không tìm thấy thiết bị VPN '{dev_id}' trên hệ thống.")


def _call_repository_agent(
    *,
    path: str,
    method: str,
    data: Optional[Dict[str, Any]],
    actor: str,
    service_context: Dict[str, Any],
) -> Any:
    override = service_context.get("repository_agent_call")
    if callable(override):
        return override(path=path, method=method, data=data, actor=actor)
    from app.api.v1.endpoints.repositories import _agent_call
    from app.auth.dependencies import Principal

    admin_p = Principal(kind="user", username=actor, role="ADMIN", member_id="")
    return _agent_call(path, method=method, data=data, principal=admin_p)


def dispatch_confirmed_action(
    *,
    action_type: str,
    target_id: str,
    args: Dict[str, Any],
    actor: str,
    role: str,
    service_context: Optional[Dict[str, Any]] = None,
    incident_engine: Optional[Any] = None,
    secret_delivery_mode: str = "vault_only",
    is_dm_1_on_1: bool = False,
) -> Dict[str, Any]:
    """
    Execute an already-confirmed action via authoritative backend services.
    Returns:
      {
        "raw_result": <redacted safe result dict for persistence>,
        "ephemeral_dm_secret_note": Optional[str] (NEVER stored in DB/audit/memory),
        "summary_reply": <clean Vietnamese summary for Zalo>,
      }
    """
    spec = get_action_spec(action_type)
    if spec is None:
        raise ValueError(f"Unsupported catalog action: {action_type}")

    validated = validate_action_args(action_type, args)
    svcs = _get_services(service_context)
    labops_svc = svcs["labops_service"]
    mram_svc = svcs["mram_service"]
    inc_eng = incident_engine or svcs.get("incident_engine")
    ephemeral_secret_note: Optional[str] = None
    raw_res: Any = {}

    # ---- Group 1: Members, Passwords & Lifecycle ----
    if action_type == "create_web_account":
        payload = {k: v for k, v in validated.items() if k not in {"target_id", "reason"} and v not in (None, "")}
        raw_res = mram_svc.create_web_member_account(payload, actor=actor)
    elif action_type == "reissue_web_password":
        res = mram_svc.admin_reissue_member_web_password(
            member_id=target_id,
            new_password=validated.get("new_password"),
            actor=actor,
        )
        new_pw = str(res.get("reissued_web_password") or "")
        vault_id: Optional[str] = None
        if new_pw and hasattr(labops_svc, "ssh_access_manager") and hasattr(labops_svc.ssh_access_manager, "vault"):
            try:
                vmeta = labops_svc.ssh_access_manager.vault.store_reissued_temp_password(
                    member_id=f"web:{res.get('member_id') or target_id}",
                    username=str(res.get("username") or target_id),
                    temporary_password=new_pw,
                    temp_password_expires_at="24h",
                    ttl_seconds=86400,
                )
                vault_id = vmeta.get("secret_id")
            except Exception:
                vault_id = None
        if new_pw and secret_delivery_mode == "dm_once" and is_dm_1_on_1:
            ephemeral_secret_note = (
                f"🔐 Mật khẩu Web mới (hiển thị 1 lần trong DM 1-1 cho @{res.get('username')}): `{new_pw}`\n"
                "⚠️ Mật khẩu này KHÔNG lưu vào audit log/lịch sử chat. Thành viên sẽ phải đổi ở lần đăng nhập kế tiếp."
            )
        else:
            ephemeral_secret_note = (
                f"🔐 Mật khẩu Web của @{res.get('username')} đã được đặt lại và niêm phong an toàn (`vault_only`"
                + (f", vault_ref={vault_id}" if vault_id else "")
                + ")."
            )
        raw_res = {**res, "vault_ref": vault_id}
    elif action_type == "reissue_ssh_temp_password":
        res = labops_svc.reissue_member_temp_password(
            target_id,
            actor=actor,
            ttl_seconds=int(validated.get("ttl_seconds", 86400)),
        )
        if secret_delivery_mode == "dm_once" and is_dm_1_on_1:
            try:
                claimed = labops_svc.claim_member_secrets(target_id, actor=actor)
                pw_val = claimed.get("temporary_password")
                if pw_val:
                    ephemeral_secret_note = (
                        f"🔐 Mật khẩu SSH tạm thời lần đầu (hiển thị 1 lần trong DM 1-1 cho @{res.get('username')}): `{pw_val}`\n"
                        f"• Hết hạn: {res.get('temp_password_expires_at')} (KHÔNG lưu log/DB)."
                    )
            except Exception:
                pass
        if not ephemeral_secret_note:
            sec_id = (res.get("one_time_secrets") or {}).get("secret_id")
            ephemeral_secret_note = (
                f"🔐 Mật khẩu SSH tạm thời mới cho @{res.get('username')} đã niêm phong vào One-Time Vault"
                + (f" (`secret_id={sec_id}`)" if sec_id else "")
                + ". Admin/thành viên có thể nhận 1 lần qua Dashboard (`/dashboard/researcher/access`) hoặc `claim_member_secrets_once`."
            )
        raw_res = res
    elif action_type == "assign_supervisor":
        member = _resolve_member_record(labops_svc, target_id)
        mid = str(member["id"]) if member else target_id
        sup_id = str(validated["primary_supervisor_id"])
        sup_m = _resolve_member_record(labops_svc, sup_id)
        if sup_m:
            sup_id = str(sup_m["id"])
        raw_res = mram_svc.assign_supervisor(
            student_member_id=mid,
            primary_supervisor_id=sup_id,
            direct_manager_id=validated.get("direct_manager_id"),
            actor=actor,
            change_reason=validated.get("reason") or "Admin cập nhật GVHD qua Zalo",
        )
    elif action_type == "update_storage_quota":
        member = _resolve_member_record(labops_svc, target_id)
        mid = str(member["id"]) if member else target_id
        raw_res = labops_svc.update_storage_quota(mid, int(validated["quota_gb"]))
    elif action_type == "suspend_member":
        raw_res = labops_svc.suspend_member_lifecycle(
            target_id,
            actor=actor,
            reason=validated.get("reason") or "",
        )
    elif action_type == "restore_member":
        member = _resolve_member_record(labops_svc, target_id)
        mid = str(member["id"]) if member else target_id
        raw_res = labops_svc.restore_member(mid)
    elif action_type == "archive_member":
        raw_res = labops_svc.archive_member_lifecycle(
            target_id,
            actor=actor,
            retain_backup=bool(validated.get("retain_backup", True)),
        )
    elif action_type == "delete_member_permanent":
        member = _resolve_member_record(labops_svc, target_id)
        mid = str(member["id"]) if member else target_id
        uname = str(member["username"]) if member else str(validated.get("confirm_username") or target_id).lstrip("@")
        raw_res = labops_svc.delete_member_permanently(
            mid,
            actor=actor,
            remove_files=bool(validated.get("remove_files", False)),
            confirm_username=str(validated.get("confirm_username") or uname),
            step_up_verified=True,
            enforce_lifecycle=True,
        )

    # ---- Group 2: SSH Keys, Sessions & Workspaces ----
    elif action_type == "provision_member_ssh":
        member = _resolve_member_record(labops_svc, target_id)
        uname = str(member["username"]) if member else str(target_id).lstrip("@")
        payload: Dict[str, Any] = {
            "username": uname,
            "full_name": (member or {}).get("full_name") or (member or {}).get("fullName") or uname,
            "email": (member or {}).get("email") or f"{uname}@fitdnu.id.vn",
            "role": (member or {}).get("role") or "RESEARCHER",
            "assigned_server": validated.get("assigned_server") or (member or {}).get("assigned_server") or "workstation",
            "workspace_name": (member or {}).get("workspace_name") or f"ws-{uname}",
            "require_passphrase": bool(validated.get("require_passphrase", True)),
            "allow_existing_member": True,
            "_actor_id": actor,
        }
        for opt_k in ("storage_pool_id", "cpu_limit_cores", "ram_limit_gb", "storage_quota_gb", "gpu_permissions"):
            if validated.get(opt_k) is not None:
                payload[opt_k] = validated[opt_k]
            elif member and member.get(opt_k) is not None:
                payload[opt_k] = member[opt_k]
        raw_res = labops_svc.provision_member_ssh_access(payload)
    elif action_type == "rotate_ssh_key":
        raw_res = labops_svc.rotate_member_ssh_keypair(
            target_id,
            actor=actor,
            require_passphrase=bool(validated.get("require_passphrase", True)),
        )
    elif action_type == "revoke_ssh_access":
        raw_res = labops_svc.revoke_member_ssh_access(target_id, actor=actor)
    elif action_type == "revoke_ssh_key":
        raw_res = labops_svc.ssh_key_registry.revoke_key(target_id, actor=actor, is_admin=True)
    elif action_type == "terminate_ssh_sessions":
        raw_res = labops_svc.terminate_member_ssh_sessions(target_id, actor=actor, confirm=True)
    elif action_type == "verify_member_workspace":
        raw_res = labops_svc.reverify_member_workspace(target_id, actor=actor)
    elif action_type == "claim_member_secrets_once":
        if secret_delivery_mode == "dm_once" and is_dm_1_on_1:
            if validated.get("secret_type") == "key_bundle":
                pkg = labops_svc.claim_member_key_bundle(target_id, actor=actor)
                ephemeral_secret_note = (
                    f"🔑 Gói SSH Key Bundle một lần (DM 1-1): fingerprint `{pkg.get('fingerprint')}` (nội dung private key được redact khỏi audit log)."
                )
            else:
                pkg = labops_svc.claim_member_secrets(target_id, actor=actor)
                ephemeral_secret_note = (
                    f"🔐 Bí mật SSH một lần (DM 1-1 cho @{pkg.get('username') or target_id}): "
                    f"passphrase=`{pkg.get('key_passphrase') or 'N/A'}` | temp_password=`{pkg.get('temporary_password') or 'N/A'}` "
                    "(KHÔNG lưu audit log)."
                )
            raw_res = pkg
        else:
            st = labops_svc.get_member_ssh_access_status(target_id)
            raw_res = {"member_id": st.get("member_id"), "username": st.get("username"), "one_time_package": st.get("one_time_package"), "policy": "vault_only_preserved"}
            ephemeral_secret_note = (
                f"🔐 Chế độ `vault_only`: Giữ nguyên secret chưa tiêu hủy trong Vault cho @{st.get('username')}. "
                f"Trạng thái gói 1 lần: {st.get('one_time_package')}. Thành viên tự nhận trên `/dashboard/researcher/access`."
            )

    # ---- Group 3: MRAM SSH Requests ----
    elif action_type == "approve_ssh_request":
        raw_res = mram_svc.decide_ssh_request(
            request_id=target_id,
            decision="APPROVED",
            approver_username=actor,
            reason=validated.get("reason") or "Đã kiểm tra và phê duyệt cấp phát",
            config=validated.get("config"),
            expected_version=validated.get("expected_version"),
        )
    elif action_type == "reject_ssh_request":
        note = str(validated.get("reason") or "").strip()
        raw_res = mram_svc.decide_ssh_request(
            request_id=target_id,
            decision="REJECTED",
            approver_username=actor,
            reason=note if len(note) >= 5 else "Từ chối theo yêu cầu Admin",
            expected_version=validated.get("expected_version"),
        )
    elif action_type == "request_info_ssh_request":
        note = str(validated.get("reason") or "").strip()
        raw_res = mram_svc.decide_ssh_request(
            request_id=target_id,
            decision="NEED_INFO",
            approver_username=actor,
            reason=note if len(note) >= 5 else "Vui lòng bổ sung thông tin đề tài và mục đích SSH",
            expected_version=validated.get("expected_version"),
        )
    elif action_type == "resend_ssh_zalo_invite":
        raw_res = mram_svc.notify_pending_ssh_request_zalo(target_id, actor=actor)
    elif action_type == "sweep_mram_sla":
        raw_res = {
            "expired_requests": mram_svc.expire_overdue_requests(),
            "expired_ssh_accounts": mram_svc.sweep_expired_ssh_access(),
        }

    # ---- Group 4: WireGuard VPN Devices ----
    elif action_type == "add_vpn_device":
        member = _resolve_member_record(labops_svc, target_id)
        mid = str(member["id"]) if member else target_id
        res = labops_svc.add_member_device(mid, validated.get("device_name", "New Device"), actor=actor)
        if res.get("configuration") and secret_delivery_mode == "dm_once" and is_dm_1_on_1:
            ephemeral_secret_note = "🔐 Cấu hình WireGuard mới đã được tạo (ẩn khỏi audit log; tải file `.conf` an toàn trên Dashboard hoặc gói ZIP)."
        raw_res = res
    elif action_type == "toggle_vpn_device":
        mid, dev_id = _resolve_vpn_member_and_device(labops_svc, target_id, validated.get("member_id"))
        raw_res = labops_svc.toggle_member_device(mid, dev_id, enable=bool(validated.get("enable", False)), actor=actor)
    elif action_type == "revoke_vpn_device":
        mid, dev_id = _resolve_vpn_member_and_device(labops_svc, target_id, validated.get("member_id"))
        raw_res = labops_svc.revoke_member_device_v2(mid, dev_id, actor=actor)

    # ---- Group 5: GPU, Jobs & Processes ----
    elif action_type == "allocate_gpu":
        raw_res = labops_svc.allocate_gpu(
            gpu_id=target_id,
            username=validated["username"],
            mode=validated.get("mode", "FULL_GPU_EXCLUSIVE"),
            requested_vram_gb=int(validated.get("requested_vram_gb", 0)),
            job_name=validated.get("job_name", "Zalo-Admin-GPU"),
            actor=actor,
            ttl_minutes=int(validated.get("ttl_minutes", 240)),
            is_admin=True,
            enforce_permissions=False,
        )
    elif action_type in {"release_gpu_reservation", "stop_job"}:
        raw_res = labops_svc.stop_job(target_id, actor=actor, is_admin=True)
        if hasattr(labops_svc, "_save_persistent_data"):
            labops_svc._save_persistent_data()
    elif action_type == "stop_process":
        raw_res = labops_svc.terminate_process(
            ref=target_id,
            timeout_seconds=int(validated.get("timeout_seconds", 10)),
            terminate_tree=bool(validated.get("terminate_tree", True)),
            actor=actor,
            is_admin=True,
        )
    elif action_type == "kill_process":
        raw_res = labops_svc.kill_process(
            ref=target_id,
            force=True,
            kill_tree=bool(validated.get("kill_tree", True)),
            actor=actor,
            is_admin=True,
        )

    # ---- Group 6: Repository Guard & Security Scans ----
    elif action_type == "decide_repository_request":
        dec = str(validated["decision"]).upper()
        reason_txt = str(validated.get("reason") or "Admin duyệt từ Zalo AI Agent")
        res = _call_repository_agent(
            path=f"requests/{target_id}/decision",
            method="POST",
            data={"decision": dec, "reviewer": actor, "reason": reason_txt},
            actor=actor,
            service_context=svcs,
        )
        labops_svc._audit(
            actor,
            f"REPOSITORY_ADMIN_{dec}",
            "REPOSITORY_REQUEST",
            target_id,
            "SUCCESS",
            f"Admin {actor} đã {dec} yêu cầu {target_id} qua Zalo. Lý do: {reason_txt}",
        )
        getattr(labops_svc, "save_audit_logs", labops_svc._save_persistent_data)()
        if dec == "APPROVED" and isinstance(res, dict):
            mon = svcs.get("repo_alert_monitor")
            if mon is not None and hasattr(mon, "suppress_repo_alerts"):
                try:
                    mon.suppress_repo_alerts(res, actor=actor, reason=reason_txt)
                except Exception:
                    pass
        raw_res = res
    elif action_type == "rescan_repository_request":
        res = _call_repository_agent(
            path=f"requests/{target_id}/rescan",
            method="POST",
            data={},
            actor=actor,
            service_context=svcs,
        )
        labops_svc._audit(
            actor,
            "REPOSITORY_ADMIN_RESCAN",
            "REPOSITORY_REQUEST",
            target_id,
            "SUCCESS",
            f"Admin {actor} yêu cầu quét lại hậu kiểm cho {target_id} qua Zalo.",
        )
        getattr(labops_svc, "save_audit_logs", labops_svc._save_persistent_data)()
        raw_res = res
    elif action_type == "decide_security_scan":
        raw_res = labops_svc.security_scanner.decide(target_id, validated["decision"], actor)

    # ---- Group 7: Incidents, Reports, Zalo Identity & V4 Config ----
    elif action_type == "ack_incident":
        raw_res = inc_eng.acknowledge_incident(
            target_id,
            actor=actor,
            role=role,
            note=validated.get("reason") or "Acknowledged via Zalo Admin",
        )
    elif action_type == "resolve_incident":
        raw_res = inc_eng.resolve_incident(
            target_id,
            actor=actor,
            role=role,
            note=validated.get("reason") or "Resolved via Zalo Admin",
        )
    elif action_type == "evaluate_incidents":
        tel = svcs.get("telemetry_collector")
        if tel is None:
            raise RuntimeError("Telemetry collector unavailable")
        snap = tel.collect_snapshot()
        tel.record_sample(snap)
        evs = inc_eng.evaluate_snapshot(snap)
        raw_res = {"evaluated": True, "events_count": len(evs), "events": evs[:10]}
    elif action_type == "generate_report":
        rg = svcs.get("report_generator")
        if rg is None:
            raise RuntimeError("Report generator unavailable")
        if str(validated.get("target_id") or target_id).lower() == "weekly":
            raw_res = rg.generate_weekly_report(author=f"Admin ({actor})")
        else:
            raw_res = rg.generate_daily_executive_briefing(
                force=bool(validated.get("force", True)),
                author=f"Admin ({actor})",
                telemetry_collector=svcs.get("telemetry_collector"),
                incident_engine=inc_eng,
            )
    elif action_type == "create_zalo_link_code":
        rbac = svcs.get("identity_rbac")
        if rbac is None:
            raise RuntimeError("Identity RBAC unavailable")
        member = _resolve_member_record(labops_svc, target_id)
        mid = str(validated.get("member_id") or (member["id"] if member else ""))
        res = rbac.generate_link_code(
            username=target_id,
            member_id=mid,
            role=validated.get("role", (member or {}).get("role", "RESEARCHER")),
            actor=actor,
            display_name=validated.get("display_name") or ((member or {}).get("fullName") or ""),
            ttl_minutes=int(validated.get("ttl_minutes", 15)),
        )
        if res.get("code") and secret_delivery_mode == "dm_once" and is_dm_1_on_1:
            ephemeral_secret_note = f"🔗 Mã liên kết một lần (DM 1-1): `/link {res['code']}` (hết hạn sau {validated.get('ttl_minutes', 15)} phút)."
        raw_res = res
    elif action_type == "bind_zalo_identity":
        rbac = svcs.get("identity_rbac")
        if rbac is None:
            raise RuntimeError("Identity RBAC unavailable")
        member = _resolve_member_record(labops_svc, target_id)
        mid = str(validated.get("member_id") or (member["id"] if member else ""))
        raw_res = rbac.admin_bind_identity(
            zalo_user_id=validated["zalo_user_id"],
            username=target_id,
            member_id=mid,
            role=validated.get("role", (member or {}).get("role", "RESEARCHER")),
            display_name=validated.get("display_name") or ((member or {}).get("fullName") or ""),
            admin_actor=actor,
        )
    elif action_type == "revoke_zalo_identity":
        rbac = svcs.get("identity_rbac")
        if rbac is None:
            raise RuntimeError("Identity RBAC unavailable")
        ok = rbac.revoke_link(target_id, actor=actor)
        if not ok:
            raise LookupError(f"Không tìm thấy liên kết Zalo '{target_id}' đang hoạt động.")
        raw_res = {"revoked": True, "target_id": target_id}
    elif action_type == "update_alert_subscriptions":
        rbac = svcs.get("identity_rbac")
        if rbac is None:
            raise RuntimeError("Identity RBAC unavailable")
        raw_res = rbac.update_subscription(target_id, dict(validated.get("updates") or {}))
    elif action_type == "reload_v4_config":
        v4 = svcs.get("v4_facade")
        if v4 is None:
            raise RuntimeError("V4 facade unavailable")
        raw_res = v4.reload_config(config_path=validated.get("config_path"), actor=actor)
    elif action_type == "run_v4_retention":
        v4 = svcs.get("v4_facade")
        if v4 is None:
            raise RuntimeError("V4 facade unavailable")
        raw_res = v4.retention.run(
            dry_run=bool(validated.get("dry_run", True)),
            force=bool(validated.get("force", True)),
            actor=actor,
        )
    else:
        raise ValueError(f"Action '{action_type}' chưa có dispatcher.")

    safe_res = redact_sensitive_payload(copy.deepcopy(raw_res))
    summary_parts = [f"✅ Đã thực thi `{spec.title_vi}` (`{action_type}`) cho `{target_id}`."]
    if ephemeral_secret_note:
        summary_parts.append(ephemeral_secret_note)

    return {
        "raw_result": safe_res,
        "ephemeral_dm_secret_note": ephemeral_secret_note,
        "summary_reply": "\n".join(summary_parts),
    }
