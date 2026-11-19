"""Safe Multi-Stage Member Deletion Lifecycle & Durable Operation Journal."""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.core.security import get_revocation_store
from app.db.models import DeletionOperationRecord, MemberRecord
from app.services.gpu_scheduler import ACTIVE_STATES as GPU_ACTIVE_STATES

if TYPE_CHECKING:
    from app.services.labops_service import LabOpsDataService

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LifecyclePreconditionError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class MemberLifecycleManager:
    """
    Safe lifecycle enforcement for member teardown:
      ACTIVE / READY / PENDING_PROVISIONING
        -> SUSPENDED (tokens/SSH/VPN disabled)
        -> DELETE_ELIGIBLE / ARCHIVED (workload-free + workspace inventory sealed)
        -> DELETION_IN_PROGRESS
        -> DELETED (or DELETION_PARTIAL_FAILURE)
    """

    ARCHIVED_STATES = {"ARCHIVED", "DELETE_ELIGIBLE"}

    def __init__(self, service: "LabOpsDataService") -> None:
        self.service = service

    def check_active_workloads(self, member: Dict[str, Any]) -> Dict[str, Any]:
        username = member["username"]
        active_procs: List[Dict[str, Any]] = []
        try:
            procs = self.service.list_processes(username=username)
            active_procs = [
                {
                    "pid": p.get("pid"),
                    "ref": p.get("ref"),
                    "name": p.get("name"),
                    "classification": p.get("classification"),
                }
                for p in procs
                if p.get("username") == username and p.get("classification") != "SYSTEM_PROCESS"
            ]
        except Exception as exc:
            logger.warning("Workload check could not list processes cleanly: %s", exc)

        active_rsvs = [
            {
                "id": r.get("id"),
                "gpu_uuid": r.get("gpu_uuid"),
                "state": r.get("state"),
            }
            for r in self.service.gpu_scheduler.list_reservations(owner=username)
            if r.get("state") in GPU_ACTIVE_STATES
        ]

        return {
            "has_active_workloads": bool(active_procs or active_rsvs),
            "active_processes": active_procs,
            "active_reservations": active_rsvs,
        }

    def _allowed_workspace_roots(self) -> List[str]:
        roots = [os.path.realpath(self.service.ssh_access_manager.workspaces_root)]
        env_user_dir = (os.getenv("LABOPS_USER_DATA_DIR") or "").strip()
        if env_user_dir:
            roots.append(os.path.realpath(env_user_dir))
        for p in getattr(self.service, "storage_pools", []):
            mp = p.get("mount_point")
            if mp and os.path.exists(mp):
                roots.append(os.path.realpath(mp))
        for extra in ["/media/LabOps-Data", "/tank/workspaces", "/mnt/labops-data", "/data"]:
            if os.path.exists(extra):
                roots.append(os.path.realpath(extra))
        return roots

    def _resolve_confined_workspace_dir(self, member: Dict[str, Any]) -> Optional[str]:
        server_id = member.get("assigned_server") or "workstation"
        username = member["username"]
        raw_dir = member.get("workspace_dir") or self.service.ssh_access_manager.member_workspace_dir(server_id, username)
        if not raw_dir or not os.path.exists(raw_dir):
            return None
        if os.path.islink(raw_dir):
            raise LifecyclePreconditionError(
                "UNSAFE_WORKSPACE_SYMLINK",
                f"Thư mục workspace '{raw_dir}' là symlink; từ chối thao tác tệp vì lý do an toàn.",
                409,
            )
        real_dir = os.path.realpath(raw_dir)
        allowed_roots = self._allowed_workspace_roots()
        confined = any(
            os.path.commonpath([root, real_dir]) == root and real_dir != root
            for root in allowed_roots
        )
        if not confined:
            raise LifecyclePreconditionError(
                "UNCONFINED_WORKSPACE_PATH",
                f"Đường dẫn workspace '{real_dir}' nằm ngoài vùng cho phép.",
                409,
            )
        return real_dir

    def collect_workspace_inventory(self, member: Dict[str, Any], *, max_hashed_files: int = 500) -> Dict[str, Any]:
        ws_dir = self._resolve_confined_workspace_dir(member)
        if not ws_dir:
            return {
                "workspace_dir": None,
                "exists": False,
                "file_count": 0,
                "total_bytes": 0,
                "file_digests": [],
            }

        file_count = 0
        total_bytes = 0
        digests: List[Dict[str, Any]] = []
        for root, dirs, files in os.walk(ws_dir, followlinks=False):
            dirs[:] = [d for d in sorted(dirs) if not os.path.islink(os.path.join(root, d))]
            for fname in sorted(files):
                fpath = os.path.join(root, fname)
                if os.path.islink(fpath):
                    continue
                try:
                    st = os.lstat(fpath)
                except OSError:
                    continue
                file_count += 1
                total_bytes += st.st_size
                if len(digests) < max_hashed_files:
                    h = hashlib.sha256()
                    try:
                        with open(fpath, "rb") as rf:
                            for chunk in iter(lambda: rf.read(65536), b""):
                                h.update(chunk)
                        digests.append({
                            "path": os.path.relpath(fpath, ws_dir),
                            "size_bytes": st.st_size,
                            "sha256": h.hexdigest(),
                        })
                    except OSError:
                        pass

        return {
            "workspace_dir": ws_dir,
            "exists": True,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "file_digests": digests,
        }

    def suspend_member(self, member_id_or_username: str, actor: str, reason: str = "") -> Dict[str, Any]:
        member = self.service._find_member(member_id_or_username)
        member_id = member["id"]
        username = member["username"]

        get_revocation_store().revoke_subject(username, reason=f"suspended:{actor}")

        # Disable VPN devices owned strictly by this member
        wg_devices = list((member.get("wireguard") or {}).get("devices") or [])
        disabled_vpn = 0
        for d in wg_devices:
            dev_id = str(d.get("external_id") or d.get("id") or "")
            is_pending = str(d.get("device_id") or "").startswith("dev-pending-") or (
                d.get("status") == "PENDING_PROVISIONING" and not dev_id
            )
            if is_pending:
                d["enabled"] = False
                d["status"] = "DISABLED"
                disabled_vpn += 1
                continue
            if not dev_id:
                continue
            try:
                self.service.vpn_manager.disable_device(dev_id, d.get("provider"))
                d["enabled"] = False
                d["status"] = "DISABLED"
                disabled_vpn += 1
            except Exception as exc:
                logger.warning("Could not disable VPN device %s for %s: %s", dev_id, username, exc)

        # Revoke SSH access & invalidate RAM secret vault
        server_id = member.get("assigned_server") or "workstation"
        try:
            ssh_res = self.service.ssh_access_manager.revoke_member_ssh(
                member=member,
                host_online=self.service._is_host_online(server_id),
                actor=actor,
                workspace_dir=member.get("workspace_dir"),
            )
            if ssh_res.get("status") == "VERIFIED_REVOKED":
                member["ssh_key_status"] = "VERIFIED_REVOKED"
        except Exception as exc:
            logger.warning("SSH revocation during suspend encountered issue for %s: %s", username, exc)

        self.service.ssh_access_manager.vault.invalidate_member(member_id)

        member["status"] = "SUSPENDED"
        member["provisioning_state"] = "SUSPENDED"
        member["ssh_status"] = "REVOKED"
        member["version"] = int(member.get("version") or 1) + 1
        member["suspended_at"] = _now_iso()

        self.service._audit(
            actor,
            "MEMBER_SUSPENDED",
            "MEMBER",
            username,
            "SUCCESS",
            f"Admin '{actor}' đã tạm khóa tài khoản @{username} (disabled VPN devices={disabled_vpn}).",
        )
        self.service._save_persistent_data()
        return {
            "success": True,
            "member_id": member_id,
            "username": username,
            "status": member["status"],
            "vpn_devices_disabled": disabled_vpn,
        }

    def archive_member(self, member_id_or_username: str, actor: str, *, retain_backup: bool = True) -> Dict[str, Any]:
        member = self.service._find_member(member_id_or_username)
        member_id = member["id"]
        username = member["username"]

        if member.get("status") not in {"SUSPENDED", "ARCHIVED", "DELETE_ELIGIBLE"}:
            raise LifecyclePreconditionError(
                "LIFECYCLE_PRECONDITION_FAILED",
                f"Thành viên @{username} đang ở trạng thái {member.get('status')}; phải SUSPEND trước khi ARCHIVE.",
                409,
            )

        workload = self.check_active_workloads(member)
        if workload["has_active_workloads"]:
            raise LifecyclePreconditionError(
                "ACTIVE_WORKLOADS_PRESENT",
                f"Không thể archive @{username} vì còn {len(workload['active_processes'])} tiến trình hoặc {len(workload['active_reservations'])} GPU reservation đang hoạt động.",
                409,
            )

        manifest = self.collect_workspace_inventory(member)
        manifest["retain_backup"] = bool(retain_backup)
        archived_at = _now_iso()

        member["archived_at"] = archived_at
        member["archive_manifest"] = manifest
        member["status"] = "DELETE_ELIGIBLE"
        member["provisioning_state"] = "ARCHIVED"
        member["version"] = int(member.get("version") or 1) + 1

        self.service._audit(
            actor,
            "MEMBER_ARCHIVED",
            "MEMBER",
            username,
            "SUCCESS",
            f"Admin '{actor}' đã niêm phong & archive workspace @{username} ({manifest['file_count']} tệp, {manifest['total_bytes']} bytes).",
        )
        self.service._save_persistent_data()
        return {
            "success": True,
            "member_id": member_id,
            "username": username,
            "status": member["status"],
            "archived_at": archived_at,
            "archive_manifest": manifest,
        }

    def preview_deletion(self, member_id_or_username: str, actor: str, *, dry_run: bool = True) -> Dict[str, Any]:
        member = self.service._find_member(member_id_or_username)
        member_id = member["id"]
        username = member["username"]

        # Resilient auto-heal: if member already completed Step 2 (ARCHIVE), heal status to DELETE_ELIGIBLE
        if member.get("status") not in self.ARCHIVED_STATES and member.get("archived_at") and member.get("archive_manifest"):
            member["status"] = "DELETE_ELIGIBLE"
            member["provisioning_state"] = "ARCHIVED"
            self.service._save_persistent_data()

        workload = self.check_active_workloads(member)
        inventory = self.collect_workspace_inventory(member)
        wg_devices = list((member.get("wireguard") or {}).get("devices") or [])
        ssh_keys = self.service.ssh_key_registry.list_keys(username=username)
        workspaces = [
            w for w in self.service.workspace_controller.list_workspaces(owner=username, legacy_endpoints=self.service.ssh_endpoints)
            if w.get("owner") == username
        ]

        reasons: List[str] = []
        if member.get("status") not in self.ARCHIVED_STATES:
            reasons.append(f"Trạng thái hiện tại là '{member.get('status')}'; bắt buộc SUSPEND rồi ARCHIVE trước.")
        if workload["has_active_workloads"]:
            reasons.append("Còn tiến trình hoặc GPU reservation đang hoạt động.")

        preview = {
            "dry_run": dry_run,
            "member_id": member_id,
            "username": username,
            "current_status": member.get("status"),
            "eligible_for_deletion": len(reasons) == 0,
            "blocking_reasons": reasons,
            "active_workloads": workload,
            "resources": {
                "wireguard_devices_count": len(wg_devices),
                "ssh_keys_count": len(ssh_keys),
                "workspaces_count": len(workspaces),
                "workspace_files_count": inventory["file_count"],
                "workspace_total_bytes": inventory["total_bytes"],
            },
        }

        if dry_run and hasattr(self.service, "db") and self.service.db is not None:
            op_id = f"op-dry-{uuid.uuid4().hex[:12]}"
            with self.service.db.session_scope() as s:
                s.add(
                    DeletionOperationRecord(
                        op_id=op_id,
                        member_id=member_id,
                        username=username,
                        actor=actor,
                        dry_run=True,
                        status="DRY_RUN_PREVIEW",
                        precheck_json=preview,
                        steps_json={},
                    )
                )
            preview["operation_id"] = op_id

        return preview

    def delete_member_permanently(
        self,
        member_id_or_username: str,
        actor: str = "admin",
        remove_files: bool = False,
        *,
        confirm_username: Optional[str] = None,
        step_up_verified: bool = False,
        enforce_lifecycle: bool = True,
    ) -> Dict[str, Any]:
        member = self.service._find_member(member_id_or_username)
        member_id = member["id"]
        username = member["username"]
        server_id = member.get("assigned_server") or "workstation"

        if confirm_username is not None and confirm_username != username:
            raise LifecyclePreconditionError(
                "CONFIRMATION_MISMATCH",
                f"Xác nhận username '{confirm_username}' không khớp tài khoản '{username}'.",
                400,
            )

        if enforce_lifecycle:
            if not step_up_verified:
                raise LifecyclePreconditionError(
                    "STEP_UP_REQUIRED",
                    "Yêu cầu xác thực lại mật khẩu quản trị viên (X-LabOps-Step-Up-Token) để xóa thành viên.",
                    403,
                )
            # Resilient auto-heal: if member already completed Step 2 (ARCHIVE), heal status to DELETE_ELIGIBLE
            if member.get("status") not in self.ARCHIVED_STATES and member.get("archived_at") and member.get("archive_manifest"):
                member["status"] = "DELETE_ELIGIBLE"
                member["provisioning_state"] = "ARCHIVED"
                self.service._save_persistent_data()

            if member.get("status") not in self.ARCHIVED_STATES:
                raise LifecyclePreconditionError(
                    "LIFECYCLE_PRECONDITION_FAILED",
                    f"Phải thực hiện SUSPEND -> ARCHIVE trước khi xóa vĩnh viễn (trạng thái hiện tại: {member.get('status')}).",
                    409,
                )

        workload = self.check_active_workloads(member)
        if workload["has_active_workloads"]:
            raise LifecyclePreconditionError(
                "ACTIVE_WORKLOADS_PRESENT",
                f"Từ chối xóa @{username}: còn {len(workload['active_processes'])} tiến trình hoặc {len(workload['active_reservations'])} GPU reservation đang hoạt động.",
                409,
            )

        op_id = f"op-del-{uuid.uuid4().hex[:12]}"
        member["status"] = "DELETION_IN_PROGRESS"
        steps: Dict[str, Dict[str, Any]] = {}
        failures: List[str] = []

        if hasattr(self.service, "db") and self.service.db is not None:
            with self.service.db.session_scope() as s:
                s.add(
                    DeletionOperationRecord(
                        op_id=op_id,
                        member_id=member_id,
                        username=username,
                        actor=actor,
                        dry_run=False,
                        status="IN_PROGRESS",
                        precheck_json={"active_workloads": workload},
                        steps_json={},
                    )
                )

        # 1. Subject token revocation
        try:
            get_revocation_store().revoke_subject(username, reason=f"deleted:{actor}")
            steps["session_revocation"] = {"status": "COMPLETED"}
        except Exception as exc:
            failures.append(f"session_revocation: {exc}")
            steps["session_revocation"] = {"status": "FAILED", "error": str(exc)}

        # 2. RAM vault purge (uses real invalidate_member, never broken _bundles attribute)
        try:
            self.service.ssh_access_manager.vault.invalidate_member(member_id)
            steps["vault_purge"] = {"status": "COMPLETED"}
        except Exception as exc:
            failures.append(f"vault_purge: {exc}")
            steps["vault_purge"] = {"status": "FAILED", "error": str(exc)}

        # 3. SSH keys & account revocation
        ssh_keys_revoked = 0
        try:
            self.service.ssh_access_manager.revoke_member_ssh(
                member=member,
                host_online=self.service._is_host_online(server_id),
                actor=actor,
                workspace_dir=member.get("workspace_dir"),
            )
            keys = self.service.ssh_key_registry.list_keys(username=username)
            for k in keys:
                if k.get("status") != "REVOKED":
                    self.service.ssh_key_registry.revoke_key(k["id"], actor=actor, is_admin=True)
                    ssh_keys_revoked += 1
            steps["ssh_keys_revoke"] = {"status": "COMPLETED", "revoked_count": ssh_keys_revoked}
        except Exception as exc:
            failures.append(f"ssh_keys_revoke: {exc}")
            steps["ssh_keys_revoke"] = {"status": "FAILED", "error": str(exc)}

        # 4. Revoke ONLY WireGuard devices explicitly registered to this member
        wg_revoked = 0
        wg_errors: List[str] = []
        wg_devices = list((member.get("wireguard") or {}).get("devices") or [])
        for d in wg_devices:
            ext_id = str(d.get("external_id") or d.get("id") or "")
            if not ext_id:
                continue
            try:
                self.service.vpn_manager.revoke_device(ext_id, d.get("provider"))
                wg_revoked += 1
            except Exception as exc:
                wg_errors.append(f"{ext_id}: {exc}")
        if wg_errors:
            failures.append(f"wireguard_revoke: {'; '.join(wg_errors)}")
            steps["wireguard_revoke"] = {"status": "PARTIAL_FAILURE", "revoked_count": wg_revoked, "errors": wg_errors}
        else:
            steps["wireguard_revoke"] = {"status": "COMPLETED", "revoked_count": wg_revoked}

        # 5. Delegated Docker workspace container stop (never runs docker rm -f from FastAPI)
        member_workspaces = [
            w for w in self.service.workspace_controller.list_workspaces(owner=username)
            if w.get("owner") == username and w.get("runtime_type") == "DOCKER" and w.get("container_id")
        ]
        docker_stopped = False
        if not member_workspaces:
            steps["workspace_container_stop"] = {"status": "NOT_APPLICABLE", "reason": "No managed container ID provisioned"}
        else:
            host_obj = next((h for h in self.service.host_registry.list_hosts() if h["id"] == server_id), {"id": server_id})
            try:
                for ws in member_workspaces:
                    self.service.node_agent.dispatch(
                        host_obj,
                        "STOP_DOCKER_WORKSPACE",
                        params={"workspace_id": ws["id"], "container_id": ws["container_id"], "username": username},
                        idempotency_key=f"{op_id}-{ws['id']}",
                    )
                docker_stopped = True
                steps["workspace_container_stop"] = {"status": "VERIFIED"}
            except Exception as exc:
                failures.append(f"workspace_container_stop: {exc}")
                steps["workspace_container_stop"] = {"status": "BLOCKED_BY_INFRASTRUCTURE", "error": str(exc)}

        # 6. Confined Python filesystem removal ONLY when remove_files=True
        files_removed = False
        if not remove_files:
            steps["workspace_files_cleanup"] = {"status": "PRESERVED", "remove_files": False}
        else:
            try:
                confined_dir = self._resolve_confined_workspace_dir(member)
                if confined_dir and os.path.exists(confined_dir):
                    shutil.rmtree(confined_dir, ignore_errors=False)
                    if os.path.exists(confined_dir):
                        raise RuntimeError(f"Workspace directory still exists after cleanup: {confined_dir}")
                    files_removed = True
                else:
                    files_removed = True
                steps["workspace_files_cleanup"] = {"status": "COMPLETED", "path": confined_dir}
            except Exception as exc:
                failures.append(f"workspace_files_cleanup: {exc}")
                steps["workspace_files_cleanup"] = {"status": "FAILED", "error": str(exc)}

        # 7. Workspace controller record removal
        try:
            self.service.workspace_controller.remove_workspace(username)
            steps["workspace_registry_cleanup"] = {"status": "COMPLETED"}
        except Exception as exc:
            failures.append(f"workspace_registry_cleanup: {exc}")
            steps["workspace_registry_cleanup"] = {"status": "FAILED", "error": str(exc)}

        if failures:
            member["status"] = "DELETION_PARTIAL_FAILURE"
            member["provisioning_state"] = "DELETION_PARTIAL_FAILURE"
            err_summary = "; ".join(failures)
            if hasattr(self.service, "db") and self.service.db is not None:
                with self.service.db.session_scope() as s:
                    rec = s.get(DeletionOperationRecord, op_id)
                    if rec is not None:
                        rec.status = "PARTIAL_FAILURE"
                        rec.steps_json = steps
                        rec.error_detail = err_summary
            self.service._audit(
                actor,
                "MEMBER_DELETION_PARTIAL_FAILURE",
                "MEMBER",
                username,
                "PARTIAL_FAILURE",
                f"Xóa tài khoản @{username} gặp lỗi một phần: {err_summary}",
                correlation_id=op_id,
            )
            self.service._save_persistent_data()
            raise RuntimeError(f"PARTIAL_DELETION_FAILURE ({op_id}): {err_summary}")

        # All required cleanup steps succeeded -> finalize deletion & DB tombstone
        deleted_at = _now_iso()
        self.service.ssh_endpoints = [ep for ep in self.service.ssh_endpoints if ep.get("member_id") != member_id]
        self.service.members = [m for m in self.service.members if m["id"] != member_id and m.get("username") != username]

        if hasattr(self.service, "db") and self.service.db is not None:
            with self.service.db.session_scope() as s:
                rec = s.get(DeletionOperationRecord, op_id)
                if rec is not None:
                    rec.status = "COMPLETED"
                    rec.steps_json = steps
                    rec.completed_at = datetime.now(timezone.utc)
                m_row = s.get(MemberRecord, member_id)
                if m_row is not None:
                    m_row.status = "DELETED"
                    m_row.provisioning_state = "DELETED"
                    m_row.deleted_at = deleted_at

        self.service._audit(
            actor,
            "MEMBER_DELETED_PERMANENTLY",
            "MEMBER",
            username,
            "SUCCESS",
            f"Admin '{actor}' đã xóa an toàn thành viên @{username} (ID: {member_id}, remove_files={remove_files}).",
            correlation_id=op_id,
        )
        self.service._save_persistent_data()

        return {
            "success": True,
            "member_id": member_id,
            "username": username,
            "status": "DELETED",
            "operation_id": op_id,
            "steps": steps,
            "cleanup": {
                "docker_container_removed": docker_stopped,
                "wireguard_peers_revoked": wg_revoked,
                "ssh_keys_revoked": ssh_keys_revoked,
                "vault_purged": True,
                "workspace_files_removed": files_removed,
            },
        }
