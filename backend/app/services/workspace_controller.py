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
import stat
import tempfile
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
IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_POLICY_MODES = {"UNENFORCED", "REPOSITORY_ENFORCED"}


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
        self._persistence_failed = False
        os.makedirs(data_dir, exist_ok=True)

    @contextmanager
    def _locked(self):
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "r+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                state = self._load()
                yield state
                try:
                    self._save(state)
                except OSError as exc:
                    self._persistence_failed = True
                    raise WorkspaceSecurityError(
                        "STATE_PERSIST_FAILED",
                        "Không thể lưu trạng thái workspace bền vững; thao tác bị fail-closed.",
                        503,
                    ) from exc
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _load(self) -> Dict[str, Any]:
        if self._persistence_failed:
            raise WorkspaceSecurityError("STATE_PERSIST_FAILED", "Workspace controller đang fail-closed.", 503)
        if not os.path.exists(self.state_path):
            return {"workspaces": [], "idempotency": {}, "events": []}
        try:
            info = os.lstat(self.state_path)
        except OSError as exc:
            raise WorkspaceSecurityError("STATE_READ_FAILED", "Không thể kiểm tra state workspace.", 503) from exc
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceSecurityError("STATE_INVALID", "State workspace phải là regular file.", 503)
        with open(self.state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("workspaces"), list)
            or not isinstance(state.get("idempotency"), dict)
        ):
            raise WorkspaceSecurityError("STATE_INVALID", "Schema state workspace không hợp lệ.", 503)
        if "events" not in state:
            state["events"] = []
        if not isinstance(state["events"], list):
            raise WorkspaceSecurityError("STATE_INVALID", "Workspace audit state không hợp lệ.", 503)
        return state

    def _save(self, state: Dict[str, Any]) -> None:
        parent = os.path.dirname(os.path.abspath(self.state_path))
        fd, tmp = tempfile.mkstemp(prefix=".v2_workspaces.", suffix=".tmp", dir=parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fd = -1
                json.dump(state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
            dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    @staticmethod
    def _request_fingerprint(request: Dict[str, Any]) -> str:
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def remove_workspace(self, owner: str) -> bool:
        with self._locked() as state:
            workspaces = state.get("workspaces", [])
            initial_count = len(workspaces)
            state["workspaces"] = [w for w in workspaces if w.get("owner") != owner]
            if len(state["workspaces"]) != initial_count:
                return True
            return False

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

    def record_runtime_observation(
        self, workspace_id: str, node_id: str, observation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Bind a Node Agent's verified Docker inspection to one pending workspace.

        The authenticated node identity is supplied separately from the request body.
        Caller-provided labels are accepted only as values observed by that node and
        must exactly match the backend-owned workspace record.
        """
        if not isinstance(workspace_id, str) or not workspace_id or len(workspace_id) > 80:
            raise WorkspaceSecurityError("INVALID_WORKSPACE_ID", "Workspace ID không hợp lệ.", 400)
        if not isinstance(node_id, str) or not IDENTITY_RE.fullmatch(node_id):
            raise WorkspaceSecurityError("INVALID_NODE_ID", "Node ID không hợp lệ.", 400)
        if not isinstance(observation, dict):
            raise WorkspaceSecurityError("INVALID_RUNTIME_OBSERVATION", "Runtime observation không hợp lệ.", 400)

        container_id = observation.get("container_id")
        if not isinstance(container_id, str) or not CONTAINER_ID_RE.fullmatch(container_id):
            raise WorkspaceSecurityError("INVALID_CONTAINER_ID", "Canonical Docker container ID không hợp lệ.", 400)
        labels = observation.get("labels")
        if not isinstance(labels, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in labels.items()):
            raise WorkspaceSecurityError("INVALID_CONTAINER_LABELS", "Docker labels không hợp lệ.", 400)

        def positive_int(field: str) -> int:
            value = observation.get(field)
            if type(value) is not int or value <= 0:
                raise WorkspaceSecurityError("INVALID_RUNTIME_LIMIT", f"Runtime limit {field} không hợp lệ.", 400)
            return value

        if observation.get("state") != "running":
            raise WorkspaceSecurityError("WORKSPACE_NOT_RUNNING", "Chỉ container đang chạy mới có thể được xác minh ACTIVE.", 409)
        network_mode = observation.get("network_mode", "UNKNOWN")
        if not isinstance(network_mode, str) or not network_mode or len(network_mode) > 128 or any(c in network_mode for c in "\r\n\x00"):
            raise WorkspaceSecurityError("INVALID_NETWORK_MODE", "Docker network mode không hợp lệ.", 400)
        if observation.get("privileged") is not False:
            raise WorkspaceSecurityError("PRIVILEGED_CONTAINER_FORBIDDEN", "Container privileged không được đăng ký workspace.", 403)
        if observation.get("docker_socket_mounted") is not False:
            raise WorkspaceSecurityError("DOCKER_SOCKET_FORBIDDEN", "Container gắn Docker socket không được đăng ký workspace.", 403)
        cpu_cores = positive_int("cpu_cores")
        ram_bytes = positive_int("ram_bytes")
        pids_limit = positive_int("pids_limit")
        container_name = observation.get("container_name")
        if not isinstance(container_name, str) or not container_name or len(container_name) > 128:
            raise WorkspaceSecurityError("INVALID_CONTAINER_NAME", "Container name không hợp lệ.", 400)

        with self._locked() as state:
            workspace = next((item for item in state["workspaces"] if item.get("id") == workspace_id), None)
            if workspace is None:
                raise WorkspaceSecurityError("WORKSPACE_NOT_FOUND", "Workspace không tồn tại.", 404)
            if workspace.get("host_id") != node_id:
                raise WorkspaceSecurityError("WRONG_WORKSPACE_NODE", "Node Agent không sở hữu workspace này.", 403)
            if workspace.get("runtime_type") != "DOCKER" or workspace.get("desired_state") != "ACTIVE":
                raise WorkspaceSecurityError("WORKSPACE_NOT_ELIGIBLE", "Workspace không đủ điều kiện runtime registration.", 409)
            repository_policy_mode = workspace.get("repository_policy_mode", "UNENFORCED")
            if repository_policy_mode not in REPOSITORY_POLICY_MODES:
                raise WorkspaceSecurityError("INVALID_REPOSITORY_POLICY", "Workspace repository policy không hợp lệ.", 503)
            if repository_policy_mode == "REPOSITORY_ENFORCED" and network_mode != "none":
                raise WorkspaceSecurityError(
                    "WORKSPACE_EGRESS_NOT_ISOLATED",
                    "Workspace có chính sách REPOSITORY_ENFORCED nhưng Docker network mode không phải none.",
                    403,
                )
            expected_labels = {
                "labops.managed": "true",
                "labops.workspace_id": workspace["id"],
                "labops.owner_id": workspace["owner"],
                "labops.node_id": workspace["host_id"],
            }
            if any(labels.get(key) != value for key, value in expected_labels.items()):
                raise WorkspaceSecurityError("WORKSPACE_LABEL_MISMATCH", "Container labels không khớp trusted workspace registry.", 403)
            expected_name = workspace.get("container_name")
            if not expected_name or container_name.lstrip("/") != expected_name:
                raise WorkspaceSecurityError("WORKSPACE_NAME_MISMATCH", "Container name không khớp workspace record.", 403)
            if cpu_cores > int(workspace["cpu_cores_limit"]):
                raise WorkspaceSecurityError("CPU_LIMIT_EXCEEDED", "Container CPU limit vượt workspace quota.", 403)
            if ram_bytes > int(workspace["ram_mb_limit"]) * 1024 * 1024:
                raise WorkspaceSecurityError("RAM_LIMIT_EXCEEDED", "Container RAM limit vượt workspace quota.", 403)
            if pids_limit > int(workspace["pids_limit"]):
                raise WorkspaceSecurityError("PIDS_LIMIT_EXCEEDED", "Container PID limit vượt workspace quota.", 403)
            current_id = workspace.get("container_id")
            if current_id and current_id != container_id:
                raise WorkspaceSecurityError("CONTAINER_REPLACEMENT", "Container ID đã đăng ký khác; cần quy trình recovery riêng.", 409)

            now = _now()
            workspace.update({
                "container_id": container_id,
                "container_name": expected_name,
                "runtime_labels": expected_labels,
                "runtime_verified_at": now,
                "runtime_verification": "VERIFIED",
                "observed_network_mode": network_mode,
                "egress_verification": "VERIFIED_NO_NETWORK" if repository_policy_mode == "REPOSITORY_ENFORCED" else "NOT_ENFORCED",
                "observed_state": "ONLINE",
                "lifecycle_state": "ACTIVE",
                "verification_reason": "Node Agent verified canonical Docker identity, ownership labels, limits, and running state.",
                "updated_at": now,
            })
            state["events"].append({
                "event": "WORKSPACE_RUNTIME_VERIFIED",
                "workspace_id": workspace_id,
                "container_id": container_id,
                "node_id": node_id,
                "result": "SUCCESS",
                "timestamp": now,
            })
            state["events"] = state["events"][-5000:]
            return self._workspace_registry_record(workspace)

    def lookup_container_record(self, container_id: str, node_id: str) -> Optional[Dict[str, Any]]:
        if not isinstance(container_id, str) or not CONTAINER_ID_RE.fullmatch(container_id):
            return None
        if not isinstance(node_id, str) or not IDENTITY_RE.fullmatch(node_id):
            return None
        state = self._load()
        workspace = next((item for item in state["workspaces"] if item.get("container_id") == container_id), None)
        if workspace is None or workspace.get("host_id") != node_id:
            return None
        if workspace.get("lifecycle_state") != "ACTIVE" or workspace.get("observed_state") != "ONLINE":
            return None
        return self._workspace_registry_record(workspace)

    @staticmethod
    def _workspace_registry_record(workspace: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "workspace_id": workspace["id"],
            "container_id": workspace["container_id"],
            "container_name": workspace["container_name"],
            "owner_id": workspace["owner"],
            "node_id": workspace["host_id"],
            "state": "ACTIVE",
            "repository_policy_mode": workspace.get("repository_policy_mode", "UNENFORCED"),
            "observed_network_mode": workspace.get("observed_network_mode", "UNKNOWN"),
            "egress_verification": workspace.get("egress_verification", "NOT_ENFORCED"),
        }

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
        storage_quota_gb: int = 200,
    ) -> None:
        if not isinstance(owner, str) or not IDENTITY_RE.fullmatch(owner) or owner in {".", ".."}:
            raise WorkspaceSecurityError("INVALID_OWNER", "Workspace owner không hợp lệ.", 400)
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
        if not (1 <= int(cpu_cores) <= 48) or not (1024 <= int(ram_mb) <= 131072) or not (64 <= int(pids_limit) <= 32768) or not (1 <= int(storage_quota_gb) <= 2048):
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
        repository_policy_mode: str = "UNENFORCED",
        privileged: bool = False,
        mount_docker_socket: bool = False,
        extra_mounts: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(host_id, str) or not IDENTITY_RE.fullmatch(host_id):
            raise WorkspaceSecurityError("INVALID_HOST", "Workspace host ID không hợp lệ.", 400)
        if repository_policy_mode not in REPOSITORY_POLICY_MODES:
            raise WorkspaceSecurityError("INVALID_REPOSITORY_POLICY", "Repository policy mode không hợp lệ.", 400)
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 200):
            raise WorkspaceSecurityError("INVALID_IDEMPOTENCY_KEY", "Idempotency key không hợp lệ.", 400)
        request_fingerprint = self._request_fingerprint({
            "owner": owner, "host_id": host_id, "ssh_public_key": ssh_public_key,
            "image_ref": image_ref, "cpu_cores": cpu_cores, "ram_mb": ram_mb,
            "pids_limit": pids_limit, "storage_quota_gb": storage_quota_gb,
            "gpu_uuid": gpu_uuid, "privileged": privileged,
            "repository_policy_mode": repository_policy_mode,
            "mount_docker_socket": mount_docker_socket, "extra_mounts": extra_mounts or [],
        })
        with self._locked() as state:
            idem_index = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest() if idempotency_key else None
            if idem_index and idem_index in state["idempotency"]:
                wid = state["idempotency"][idem_index]
                existing = next((w for w in state["workspaces"] if w.get("id") == wid), None)
                if existing is None:
                    raise WorkspaceSecurityError("STATE_INVALID", "Idempotency index tham chiếu workspace không tồn tại.", 503)
                if existing.get("request_fingerprint") != request_fingerprint:
                    raise WorkspaceSecurityError("IDEMPOTENCY_KEY_REUSED", "Idempotency key đã được dùng cho workspace request khác.", 409)
                return existing

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
                storage_quota_gb=storage_quota_gb,
            )

            used_ports = {w.get("ssh_host_port") for w in state["workspaces"] if w.get("host_id") == host_id}
            ssh_port = next(p for p in range(22010, 29999) if p not in used_ports)
            workspace_id = f"ws-{uuid.uuid4().hex[:10]}"

            # Trong môi trường không có quyền chạm Docker daemon thực tế, giữ ở trạng thái
            # NOT_VERIFIED / PENDING_RETRY thay vì báo ACTIVE giả.
            ws = {
                "id": workspace_id,
                "name": f"ws-{owner}",
                "container_name": f"labops-{workspace_id}",
                "owner": owner,
                "host_id": host_id,
                "runtime_type": "DOCKER",
                "image_ref": image_ref,
                "ssh_host_port": ssh_port,
                "cpu_cores_limit": int(cpu_cores),
                "ram_mb_limit": int(ram_mb),
                "pids_limit": int(pids_limit),
                "storage_quota_gb": int(storage_quota_gb),
                "storage_path": f"/tank/workspaces/{owner}",
                "gpu_uuid": gpu_uuid,
                "repository_policy_mode": repository_policy_mode,
                "observed_network_mode": "UNKNOWN",
                "egress_verification": "NOT_ENFORCED" if repository_policy_mode == "UNENFORCED" else "PENDING_RUNTIME_ATTESTATION",
                "request_fingerprint": request_fingerprint,
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
            if idem_index:
                state["idempotency"][idem_index] = ws["id"]
            return ws

