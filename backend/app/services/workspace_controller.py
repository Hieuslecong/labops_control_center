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
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
        if not os.path.exists(self.state_path):
            return {"workspaces": [], "idempotency": {}}
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, state: Dict[str, Any]) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

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
            ws = {
                "id": f"ws-{uuid.uuid4().hex[:10]}",
                "name": f"ws-{owner}",
                "owner": owner,
                "host_id": host_id,
                "runtime_type": "DOCKER",
                "image_ref": image_ref,
                "ssh_host_port": ssh_port,
                "vpn_bind_ip": "10.200.0.2" if host_id == "workstation" else "10.200.0.1",
                "cpu_cores_limit": int(cpu_cores),
                "ram_mb_limit": int(ram_mb),
                "pids_limit": int(pids_limit),
                "storage_quota_gb": int(storage_quota_gb),
                "storage_path": f"/tank/workspaces/{owner}",
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

