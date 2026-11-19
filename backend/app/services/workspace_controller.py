"""
LabOps Workspace Controller V2 (Docker Engine + NVIDIA Container Toolkit + Incus Legacy Adapter).

Tuân thủ nghiêm ngặt Mục 14 của prompt.md:
- State machine: REQUESTED -> VALIDATING -> PROVISIONING -> VERIFYING -> ACTIVE
  kèm các trạng thái lỗi trung thực: FAILED, PENDING_RETRY, PARTIAL_FAILURE, NOT_VERIFIED.
- Ràng buộc bảo mật bắt buộc cho mọi workspace của Researcher:
  * non_privileged = True (cấm --privileged)
  * docker_socket_mounted = False (cấm mount /var/run/docker.sock)
  * cấm mount đường dẫn nhạy cảm (/etc, /root, /proc, /sys, /var/lib/docker, thư mục thành viên khác)
  * giới hạn CPU cores, RAM MB, pids_limit, storage_quota_gb
  * gán GPU theo UUID (NVIDIA_VISIBLE_DEVICES=<uuid>)
  * xác thực SSH bằng khóa công khai riêng của thành viên (OpenSSH), không mật khẩu yếu.
- Không động chạm hay xóa các workspace Incus hiện có (giữ nguyên qua runtime_type = INCUS_LEGACY).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.db.atomic_json import atomic_write_json, safe_load_json

WORKSPACE_UID_MIN = 20000
WORKSPACE_UID_MAX = 59999
IMAGE_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")

FORBIDDEN_MOUNT_PREFIXES = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/etc",
    "/root",
    "/boot",
    "/proc",
    "/sys",
    "/var/lib/docker",
    "/var/lib/incus",
)
SSH_PUBKEY_RE = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+)\s+[A-Za-z0-9+/=]+(\s+.*)?$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceSecurityError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class WorkspaceController:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.state_path = os.path.join(data_dir, "v2_workspaces.json")
        self.lock_path = self.state_path + ".lock"
        os.makedirs(data_dir, exist_ok=True)

    @contextmanager
    def _locked(self):
        with open(self.lock_path, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                state = self._load()
                yield state
                self._save(state)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _load(self) -> Dict[str, Any]:
        return safe_load_json(self.state_path, lambda: {"workspaces": [], "idempotency": {}})

    def _save(self, state: Dict[str, Any]) -> None:
        atomic_write_json(self.state_path, state, use_lock=False)

    def remove_workspace(self, owner: str) -> bool:
        with self._locked() as state:
            workspaces = state.get("workspaces", [])
            initial_count = len(workspaces)
            state["workspaces"] = [w for w in workspaces if w.get("owner") != owner]
            return len(state["workspaces"]) != initial_count

    def list_workspaces(self, owner: Optional[str] = None, legacy_endpoints: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        state = self._load()
        items = list(state.get("workspaces", []))
        existing_names = {w["name"] for w in items}
        for ep in legacy_endpoints or []:
            ws_name = ep.get("workspace_container") or f"ws-{ep.get('username', 'unknown')}"
            if ws_name in existing_names:
                continue
            items.append({
                "id": f"legacy-{ep.get('id', ws_name)}",
                "name": ws_name,
                "owner": ep.get("username"),
                "host_id": ep.get("server_id", "workstation"),
                "runtime_type": "INCUS_LEGACY",
                "image_ref": "incus/legacy-preserved",
                "ssh_host_port": ep.get("listen_port"),
                "vpn_bind_ip": ep.get("bind_address"),
                "cpu_cores_limit": 8,
                "ram_mb_limit": 32768,
                "pids_limit": 4096,
                "storage_path": f"/tank/workspaces/{ep.get('username', 'unknown')}",
                "non_privileged": True,
                "docker_socket_mounted": False,
                "lifecycle_state": "NOT_VERIFIED",
                "observed_state": ep.get("verification_status", "NOT_VERIFIED"),
                "legacy_preserved": True,
                "created_at": ep.get("created_at", _now()),
            })
        if owner is not None:
            items = [w for w in items if w.get("owner") == owner]
        return items

    @staticmethod
    def validate_security_constraints(
        *,
        owner: str,
        privileged: bool = False,
        mount_docker_socket: bool = False,
        extra_mounts: Optional[List[str]] = None,
        ssh_public_key: str = "",
        cpu_cores: int = 8,
        ram_mb: int = 32768,
        pids_limit: int = 4096,
    ) -> None:
        if privileged:
            raise WorkspaceSecurityError("PRIVILEGED_FORBIDDEN", "Cấm tạo workspace ở chế độ --privileged.", 403)
        if mount_docker_socket:
            raise WorkspaceSecurityError("DOCKER_SOCKET_FORBIDDEN", "Cấm gắn Docker socket vào workspace của nghiên cứu viên.", 403)
        for m in extra_mounts or []:
            norm = os.path.normpath(m.split(":")[0])
            if any(norm == p or norm.startswith(p + "/") for p in FORBIDDEN_MOUNT_PREFIXES):
                raise WorkspaceSecurityError("MOUNT_PATH_FORBIDDEN", f"Đường dẫn mount '{norm}' bị cấm bởi chính sách cách ly.", 403)
            if norm.startswith("/tank/workspaces/") and norm != f"/tank/workspaces/{owner}" and not norm.startswith(f"/tank/workspaces/{owner}/"):
                raise WorkspaceSecurityError("CROSS_MEMBER_MOUNT_FORBIDDEN", "Cấm gắn thư mục lưu trữ của thành viên khác.", 403)
            if norm.startswith("/media/LabOps-Data/users/") and norm != f"/media/LabOps-Data/users/{owner}" and not norm.startswith(f"/media/LabOps-Data/users/{owner}/"):
                raise WorkspaceSecurityError("CROSS_MEMBER_MOUNT_FORBIDDEN", "Cấm gắn thư mục lưu trữ của thành viên khác trong /media/LabOps-Data/users.", 403)
        if not ssh_public_key or not SSH_PUBKEY_RE.match(ssh_public_key.strip()):
            raise WorkspaceSecurityError("SSH_PUBKEY_REQUIRED", "Bắt buộc cung cấp khóa công khai OpenSSH hợp lệ cho workspace.", 400)
        if not (1 <= int(cpu_cores) <= 48) or not (1024 <= int(ram_mb) <= 131072) or not (64 <= int(pids_limit) <= 32768):
            raise WorkspaceSecurityError("INVALID_RESOURCE_LIMITS", "Giới hạn CPU/RAM/PIDs nằm ngoài ngưỡng an toàn.", 400)

    def request_workspace(
        self,
        *,
        owner: str,
        host_id: str,
        ssh_public_key: str,
        image_ref: str = "nvcr.io/nvidia/pytorch:24.08-py3",
        cpu_cores: int = 8,
        ram_mb: int = 32768,
        pids_limit: int = 4096,
        storage_quota_gb: int = 200,
        gpu_uuid: Optional[str] = None,
        privileged: bool = False,
        mount_docker_socket: bool = False,
        extra_mounts: Optional[List[str]] = None,
        storage_path: Optional[str] = None,
        member_storage_dir: Optional[str] = None,
        container_mount_path: Optional[str] = None,
        resource_mode: str = "SHARED_WEIGHTED",
        cpu_weight: int = 100,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._locked() as state:
            if idempotency_key and idempotency_key in state["idempotency"]:
                wid = state["idempotency"][idempotency_key]
                return next(w for w in state["workspaces"] if w["id"] == wid)

            # Bước 1: REQUESTED -> VALIDATING
            self.validate_security_constraints(
                owner=owner,
                privileged=privileged,
                mount_docker_socket=mount_docker_socket,
                extra_mounts=extra_mounts,
                ssh_public_key=ssh_public_key,
                cpu_cores=cpu_cores,
                ram_mb=ram_mb,
                pids_limit=pids_limit,
            )

            used_ports = {w.get("ssh_host_port") for w in state["workspaces"] if w.get("host_id") == host_id}
            ssh_port = next(p for p in range(22010, 29999) if p not in used_ports)

            # Trong môi trường không có quyền chạm Docker daemon thực tế, giữ ở trạng thái
            # NOT_VERIFIED / PENDING_RETRY thay vì báo ACTIVE giả.
            uid, gid = self.allocate_uid_gid(owner, state=state)
            final_storage_path = storage_path or member_storage_dir or (f"/media/LabOps-Data/labops/users/{owner}" if host_id == "workstation" else f"/tank/workspaces/{owner}")
            final_container_mount = container_mount_path or "/workspace"
            default_bind_ip = (
                getattr(settings, "LABOPS_WORKSPACE_BIND_IP", "192.168.167.251")
                if host_id == "workstation"
                else "192.168.167.250"
            )

            ws = {
                "id": f"ws-{uuid.uuid4().hex[:10]}",
                "name": f"ws-{owner}",
                "owner": owner,
                "uid": uid,
                "gid": gid,
                "host_id": host_id,
                "runtime_type": "DOCKER",
                "image_ref": image_ref,
                "ssh_host_port": ssh_port,
                "vpn_bind_ip": default_bind_ip,
                "cpu_cores_limit": int(cpu_cores),
                "ram_mb_limit": int(ram_mb),
                "pids_limit": int(pids_limit),
                "storage_quota_gb": int(storage_quota_gb),
                "storage_path": final_storage_path,
                "member_storage_dir": final_storage_path,
                "container_mount_path": final_container_mount,
                "resource_mode": resource_mode,
                "cpu_weight": int(cpu_weight),
                "gpu_uuid": gpu_uuid,
                "non_privileged": True,
                "docker_socket_mounted": False,
                "no_host_sudo": True,
                "desired_state": "ACTIVE",
                "lifecycle_state": "PENDING_RETRY",
                "observed_state": "NOT_VERIFIED",
                "verification_reason": "Đã kiểm tra hợp lệ chính sách bảo mật (VALIDATING); chờ Node Agent thực thi Docker runtime trên host.",
                "created_at": _now(),
                "updated_at": _now(),
            }
            state["workspaces"].append(ws)
            if idempotency_key:
                state["idempotency"][idempotency_key] = ws["id"]
            return ws

    def allocate_uid_gid(self, owner: str, *, state: Optional[Dict[str, Any]] = None) -> Tuple[int, int]:
        """Cấp phát UID/GID cách ly trong dải 20000-59999 (tuyệt đối tránh root 0 và admin host 1000)."""
        clean_owner = (owner or "").strip().lower()
        data = state if state is not None else self._load()
        workspaces = data.get("workspaces", [])
        used_uids = set()
        for w in workspaces:
            w_uid = w.get("uid")
            if isinstance(w_uid, int) and WORKSPACE_UID_MIN <= w_uid <= WORKSPACE_UID_MAX:
                if w.get("owner") == clean_owner:
                    w_gid = w.get("gid")
                    if isinstance(w_gid, int) and WORKSPACE_UID_MIN <= w_gid <= WORKSPACE_UID_MAX:
                        return w_uid, w_gid
                    return w_uid, w_uid
                used_uids.add(w_uid)

        span = WORKSPACE_UID_MAX - WORKSPACE_UID_MIN + 1
        digest_int = int(hashlib.sha256(clean_owner.encode("utf-8")).hexdigest()[:8], 16)
        candidate = WORKSPACE_UID_MIN + (digest_int % span)
        for offset in range(span):
            uid = WORKSPACE_UID_MIN + ((candidate - WORKSPACE_UID_MIN + offset) % span)
            if uid not in used_uids:
                return uid, uid
        raise WorkspaceSecurityError("UID_SPACE_EXHAUSTED", "Hết dải UID/GID cô lập khả dụng.", 507)

    def build_provision_workspace_spec(
        self,
        *,
        member: Dict[str, Any],
        workspace: Dict[str, Any],
        correlation_id: str,
        actor: str = "admin",
        image_ref: Optional[str] = None,
        image_digest: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Tạo và xác thực nghiêm ngặt hợp đồng ProvisionWorkspaceSpec gửi tới Go Node Agent."""
        username = str(member.get("username") or workspace.get("owner") or "").strip().lower()
        ws_id = str(workspace.get("id") or "").strip()
        ws_name = str(workspace.get("name") or f"ws-{username}").strip()
        if not ws_id or not username:
            raise WorkspaceSecurityError("INVALID_PROVISION_SPEC", "Thiếu workspace_id hoặc username hợp lệ.", 400)
        if workspace.get("owner") and str(workspace.get("owner")).strip().lower() != username:
            raise WorkspaceSecurityError("OWNER_MISMATCH", "Chủ sở hữu workspace không khớp với thành viên.", 400)

        uid = int(member.get("uid") or workspace.get("uid") or 0)
        gid = int(member.get("gid") or workspace.get("gid") or 0)
        if uid < WORKSPACE_UID_MIN or uid > WORKSPACE_UID_MAX or gid < WORKSPACE_UID_MIN or gid > WORKSPACE_UID_MAX:
            uid, gid = self.allocate_uid_gid(username)

        raw_img = str(
            image_ref
            or os.getenv("LABOPS_WORKSPACE_IMAGE_REF", "")
            or getattr(settings, "LABOPS_WORKSPACE_IMAGE_REF", "")
            or workspace.get("image_ref")
            or ""
        ).strip()
        raw_digest = str(
            image_digest
            or os.getenv("LABOPS_WORKSPACE_IMAGE_DIGEST", "")
            or getattr(settings, "LABOPS_WORKSPACE_IMAGE_DIGEST", "")
            or workspace.get("image_digest")
            or ""
        ).strip().lower()
        if "@sha256:" in raw_img and not raw_digest:
            raw_digest = raw_img.split("@", 1)[1].strip().lower()
        if raw_img and "@sha256:" not in raw_img and IMAGE_DIGEST_RE.match(raw_digest):
            raw_img = f"{raw_img}@{raw_digest}"
        if not raw_img or "@sha256:" not in raw_img or not IMAGE_DIGEST_RE.match(raw_digest):
            raise WorkspaceSecurityError(
                "IMAGE_DIGEST_NOT_CONFIGURED",
                "Image workspace bắt buộc được neo cố định theo @sha256:<64hex> (LABOPS_WORKSPACE_IMAGE_REF / DIGEST).",
                400,
            )

        storage_root = str(member.get("host_storage_root") or workspace.get("storage_root") or "").strip()
        member_dir = str(
            member.get("member_storage_directory")
            or workspace.get("member_storage_dir")
            or workspace.get("storage_path")
            or member.get("workspace_dir")
            or ""
        ).strip()
        if not member_dir or ".." in member_dir or not os.path.isabs(member_dir):
            raise WorkspaceSecurityError("INVALID_STORAGE_PATH", f"Đường dẫn storage '{member_dir}' không hợp lệ.", 400)
        norm_dir = os.path.normpath(member_dir)
        if any(norm_dir == p or norm_dir.startswith(p + "/") for p in FORBIDDEN_MOUNT_PREFIXES):
            raise WorkspaceSecurityError("MOUNT_PATH_FORBIDDEN", f"Đường dẫn '{norm_dir}' thuộc vùng cấm hệ thống.", 403)
        if not storage_root:
            storage_root = os.path.dirname(os.path.dirname(norm_dir)) if f"/users/{username}" in norm_dir else os.path.dirname(norm_dir)

        ssh_port = int(member.get("ssh_port") or workspace.get("ssh_host_port") or 0)
        if ssh_port < 22000 or ssh_port > 65000:
            raise WorkspaceSecurityError("INVALID_SSH_PORT", f"Cổng SSH workspace '{ssh_port}' nằm ngoài dải cho phép.", 400)

        host_id = str(member.get("assigned_server") or workspace.get("host_id") or settings.LOCAL_HOST_ID)
        bind_ip = str(
            workspace.get("vpn_bind_ip")
            or (getattr(settings, "LABOPS_WORKSPACE_BIND_IP", "192.168.167.251") if host_id == "workstation" else "192.168.167.250")
        ).strip()
        gpu_uuids = [str(g) for g in (member.get("gpu_permissions") or []) if g]
        if not gpu_uuids and workspace.get("gpu_uuid"):
            gpu_uuids = [str(workspace["gpu_uuid"])]

        return {
            "workspace_id": ws_id,
            "name": ws_name,
            "member_id": str(member.get("id") or ""),
            "member_username": username,
            "owner": username,
            "uid": uid,
            "gid": gid,
            "image_ref": raw_img,
            "image_digest": raw_digest,
            "storage_root": storage_root,
            "member_storage_dir": norm_dir,
            "container_mount_path": str(workspace.get("container_mount_path") or "/workspace"),
            "ssh_port": ssh_port,
            "vpn_bind_ip": bind_ip,
            "resource_mode": str(workspace.get("resource_mode") or "SHARED_WEIGHTED"),
            "cpu_weight": int(workspace.get("cpu_weight") or 100),
            "cpu_limit_cores": int(member.get("cpu_limit_cores") or workspace.get("cpu_cores_limit") or 4),
            "ram_limit_mb": int((member.get("ram_limit_gb") or 8) * 1024),
            "pids_limit": int(workspace.get("pids_limit") or 4096),
            "gpu_uuids": gpu_uuids,
            "network_isolated": False,
            "generation": int(workspace.get("generation") or 1),
            "audit_actor": actor,
            "correlation_id": correlation_id,
        }

