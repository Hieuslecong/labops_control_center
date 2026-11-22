import uuid
import re
import base64
import hashlib
import os
import json
import logging
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.core.security import get_revocation_store
from app.db.atomic_json import atomic_write_json, safe_load_json
from app.db.idempotency import IdempotencyStore
from app.db.models import AuditEventRecord, DeletionOperationRecord, MemberRecord, PersistenceRecoveryRecord
from app.db.session import DatabaseManager
from app.adapters.host_monitor_adapter import HostMonitorAdapter, get_real_root_mount_info
from app.adapters.nvml_adapter import NvmlAdapter
from app.services.gpu_scheduler import ACTIVE_STATES as GPU_ACTIVE_STATES, GpuScheduler, SchedulerError
from app.services.member_lifecycle import MemberLifecycleManager
from app.services.security_scanner import SecurityScanner
from app.services.host_registry import HostRegistry
from app.adapters.node_agent_adapter import NodeAgentClient, NodeAgentError
from app.adapters.arcane_adapter import ArcaneAdapter
from app.adapters.beszel_adapter import BeszelAdapter
from app.services.workspace_controller import WorkspaceController
from app.services.vpn_ssh_service import SshKeyRegistry, compute_ssh_fingerprint
from app.services.ssh_access_manager import SshAccessManager
from app.services.ssh_authorization_gate import evaluate_ssh_authorization_gate
from app.adapters.process_monitor_adapter import ProcessMonitorAdapter
from app.adapters.teleport_adapter import TeleportAdapter
from app.services.vpn_manager import VPNManager
from app.services.storage_inventory_service import storage_service

logger = logging.getLogger(__name__)

SAFE_USERNAME_REGEX = re.compile(r'^[a-z_][a-z0-9_-]{2,31}$')
RESERVED_USERNAMES = {
    "root", "bin", "daemon", "sys", "adm", "sync", "games", "man", "lp", "mail",
    "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats", "nobody",
    "systemd-network", "systemd-resolve", "messagebus", "sshd", "incus", "docker",
    "sudo", "wheel", "admin", "operator"
}
APPROVED_MOUNT_PATHS = {"/workspace", "/home", "/data"}

_GPU_ID_ALIASES = {
    "ws-gpu-01": "workstation-gpu-01",
    "workstation-gpu-01": "workstation-gpu-01",
    "rack-gpu-01": "track-gpu-01",
    "track-gpu-01": "track-gpu-01",
    "rack-gpu-02": "track-gpu-02",
    "track-gpu-02": "track-gpu-02",
    "rack-gpu-03": "track-gpu-03",
    "track-gpu-03": "track-gpu-03",
}

CLUSTER_GPU_CATALOG: Dict[str, Dict[str, Any]] = {
    "workstation-gpu-01": {
        "gpu_label": "WS-GPU-01",
        "gpu_model": "NVIDIA A30 (24GB HBM2)",
        "server_id": "workstation",
        "server_name": "Server 02 — Workstation (aiot-ws1 • Cục bộ)",
        "pci_bus_id": "00000000:D8:00.0",
        "vram_total_mb": 24576,
    },
    "track-gpu-01": {
        "gpu_label": "RACK-GPU-01",
        "gpu_model": "NVIDIA A30 (24GB HBM2)",
        "server_id": "track",
        "server_name": "Server 01 — Server Rack (aiotlab-rack)",
        "pci_bus_id": "0000:3b:00.0",
        "vram_total_mb": 24576,
    },
    "track-gpu-02": {
        "gpu_label": "RACK-GPU-02",
        "gpu_model": "NVIDIA A30 (24GB HBM2)",
        "server_id": "track",
        "server_name": "Server 01 — Server Rack (aiotlab-rack)",
        "pci_bus_id": "0000:5e:00.0",
        "vram_total_mb": 24576,
    },
    "track-gpu-03": {
        "gpu_label": "RACK-GPU-03",
        "gpu_model": "NVIDIA A30 (24GB HBM2)",
        "server_id": "track",
        "server_name": "Server 01 — Server Rack (aiotlab-rack)",
        "pci_bus_id": "0000:86:00.0",
        "vram_total_mb": 24576,
    },
}

_USER_DISPLAY_NAMES = {
    "vinh_test": "Nguyễn Thế Vinh",
    "nhannv": "Nguyễn Văn Nhân",
    "hieulc": "Lê Công Hiếu",
}


def canonicalize_gpu_id(raw_gpu_id: Optional[str]) -> str:
    """Chuẩn hóa định danh GPU (VD: WS-GPU-01 / RACK-GPU-01 -> ID vật lý chuẩn)."""
    if not raw_gpu_id:
        return ""
    cleaned = str(raw_gpu_id).strip()
    return _GPU_ID_ALIASES.get(cleaned.lower(), cleaned)


def validate_linux_username(username: str) -> None:
    if not SAFE_USERNAME_REGEX.match(username):
        raise ValueError(
            f"Tên người dùng '{username}' không hợp lệ. Phải từ 3-32 ký tự, bắt đầu bằng chữ thường hoặc gạch dưới, chỉ chứa ký tự a-z, 0-9, gạch dưới và gạch ngang."
        )
    if username.lower() in RESERVED_USERNAMES:
        raise ValueError(f"Tên người dùng '{username}' là tên tài khoản hệ thống đã được bảo lưu.")

def validate_mount_path(path: str, username: str) -> None:
    allowed = {"/workspace", f"/home/{username}", "/data"}
    if path not in allowed:
        raise ValueError(
            f"Thư mục mount '{path}' không hợp lệ. Chỉ chấp nhận các đường dẫn được phê duyệt: /workspace, /home/{username}, hoặc /data."
        )


class MemberConflictError(ValueError):
    """Typed domain conflict (HTTP 409) inheriting ValueError for backward compatibility."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 409,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}


class MemberPersistenceError(RuntimeError):
    """Typed fail-closed persistence/schema error (HTTP 503 / 409)."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 503,
        recovery_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.recovery_id = recovery_id


def _member_dict_from_db_row(row: MemberRecord) -> Dict[str, Any]:
    payload = dict(row.payload_json) if isinstance(row.payload_json, dict) else {}
    payload["id"] = row.id
    payload["username"] = row.username
    payload["email"] = row.email
    payload["full_name"] = row.full_name
    payload["role"] = row.role
    payload["status"] = row.status
    payload["provisioning_state"] = row.provisioning_state
    payload["assigned_server"] = row.assigned_server
    payload["workspace_name"] = row.workspace_name
    payload["workspace_dir"] = row.workspace_dir
    payload["ssh_port"] = row.ssh_port
    payload["storage_quota_gb"] = row.storage_quota_gb
    payload["cpu_limit_cores"] = row.cpu_limit_cores
    payload["ram_limit_gb"] = row.ram_limit_gb
    payload["vram_quota_gb"] = row.vram_quota_gb
    payload["gpu_permissions"] = list(row.gpu_permissions_json or payload.get("gpu_permissions") or [])
    payload["archived_at"] = row.archived_at
    if row.archive_manifest_json is not None:
        payload["archive_manifest"] = row.archive_manifest_json
    payload["version"] = row.version
    payload["created_at"] = row.created_at
    return payload

class LabOpsDataService:
    """
    Core data service for LabOps Control Center.
    Manages in-memory state for Demo Mode and interfaces with real host telemetry & persistent storage in Production Mode.
    """
    def __init__(self, demo_mode: Optional[bool] = None, data_dir: Optional[str] = None):
        self.demo_mode = settings.DEMO_MODE if demo_mode is None else demo_mode
        self._provision_lock = threading.RLock()
        self._persist_lock = threading.RLock()
        self._inflight_usernames: set[str] = set()
        self._legacy_unsynced_member_ids: set[str] = set()
        self.host_monitor = HostMonitorAdapter(is_demo_mode=self.demo_mode)
        self.nvml_adapter = NvmlAdapter(server_id=settings.LOCAL_HOST_ID, demo_mode=self.demo_mode)
        self.process_monitor = ProcessMonitorAdapter(server_id=settings.LOCAL_HOST_ID, demo_mode=self.demo_mode)
        resolved_dir = data_dir or os.getenv("LABOPS_DATA_DIR") or getattr(settings, "LABOPS_DATA_DIR", "") or os.path.join(os.path.dirname(__file__), "..", "..", "data")
        self.data_dir = os.path.abspath(resolved_dir)
        default_backend_data = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
        self._is_live_production_data_dir = (
            self.data_dir == default_backend_data
            and not self.demo_mode
            and "unittest" not in sys.modules
            and "pytest" not in sys.modules
        )
        os.makedirs(self.data_dir, exist_ok=True)
        # Đặt lại bộ đếm giữ chỗ tạm và dọn custom pools local không còn tồn tại khi khởi tạo service mới
        storage_service._reservations.clear()
        for h_key, h_pools in list(storage_service._custom_pools.items()):
            kept = [
                p for p in h_pools
                if h_key != settings.LOCAL_HOST_ID
                or not p.get("mounted", True)
                or (p.get("mount_point") and os.path.exists(p["mount_point"]))
            ]
            if kept:
                storage_service._custom_pools[h_key] = kept
            else:
                storage_service._custom_pools.pop(h_key, None)
        self.db = DatabaseManager(self.data_dir)
        self.idempotency = IdempotencyStore(self.db)
        get_revocation_store().bind_db(self.db)
        self.vpn_manager = VPNManager()
        self.gpu_scheduler = GpuScheduler(os.path.join(self.data_dir, "gpu_reservations.json"),
                                          self.nvml_adapter.get_live_gpu_telemetry_sync)
        self.security_scanner = SecurityScanner(os.path.join(self.data_dir, "security_scans.json"))
        self.host_registry = HostRegistry(self.data_dir, local_host_id=settings.LOCAL_HOST_ID)
        self.node_agent = NodeAgentClient(local_host_id=settings.LOCAL_HOST_ID)
        self.arcane_adapter = ArcaneAdapter()
        self.beszel_adapter = BeszelAdapter()
        self.workspace_controller = WorkspaceController(self.data_dir)
        self.ssh_key_registry = SshKeyRegistry(self.data_dir)
        self.ssh_access_manager = SshAccessManager(
            self.data_dir, self.ssh_key_registry, local_host_id=settings.LOCAL_HOST_ID
        )
        self.teleport_adapter = TeleportAdapter()
        self._init_data()
        self.lifecycle = MemberLifecycleManager(self)
        self._normalize_loaded_members()
        self._sync_initial_db_state()
        self._normalize_loaded_members()
        for entry in self.audit_logs:
            if isinstance(entry, dict) and not entry.get("correlation_id"):
                entry["correlation_id"] = entry.get("correlationId") or f"corr-{entry.get('id', 'legacy')}"
        # Bảo mật P0-1: TUYỆT ĐỐI không tự khởi chạy host SSHD dưới UID quản trị khi khởi động Control Plane.
        if self._is_live_production_data_dir:
            for member in self.members:
                pid_file = os.path.join(
                    str(member.get("workspace_dir") or ""), ".ssh", "canary_sshd.pid"
                )
                if pid_file and os.path.isfile(pid_file):
                    logger.warning(
                        "Phát hiện tệp PID host SSHD cũ cho @%s (%s) — từ chối tự khởi chạy; yêu cầu chuyển sang Docker workspace cô lập.",
                        member.get("username"),
                        pid_file,
                    )

    def _normalize_loaded_members(self) -> None:
        """Strip legacy inline WireGuard secrets and demote unverified legacy ACTIVE records."""
        for member in self.members:
            profile = member.get("wireguard") or {}
            profile.pop("config_content", None)
            profile.pop("private_key", None)
            if profile and not profile.get("devices"):
                if profile.get("status") == "ACTIVE":
                    profile["status"] = "NOT_VERIFIED"
                for field in ("server_public_key", "server_endpoint", "allowed_subnets", "vpn_ip", "client_ip"):
                    profile.pop(field, None)
            if not self.demo_mode and member.get("status") == "ACTIVE" and not member.get("access_verified_at"):
                member["legacy_access_status"] = "ACTIVE_UNVERIFIED"
                member["status"] = "PENDING_PROVISIONING"
                member["provisioning_state"] = "LEGACY_ACCESS_RETAINED_UNVERIFIED"
                member["workspace_status"] = "NOT_VERIFIED"
                member["teleport_status"] = "NOT_CONFIGURED"
                member["ssh_status"] = "NOT_VERIFIED"
                member["ssh_sessions_count"] = 0
                member["ssh_key_status"] = "NOT_CONFIGURED"
                member["password_status"] = "LEGACY_NOT_VERIFIED"
                member["security_isolation_level"] = "NOT_VERIFIED"
                if isinstance(member.get("privilege_validation"), dict):
                    member["privilege_validation"]["status"] = "NOT_VERIFIED"

    def _sync_initial_db_state(self) -> None:
        if self.demo_mode:
            return
        self.reconcile_persistence_state()

    def reconcile_persistence_state(self) -> Dict[str, Any]:
        """Reconcile RAM, JSON mirrors, DB state, and pending persistence recovery records."""
        summary: Dict[str, Any] = {
            "pruned_deleted": 0,
            "recovered_from_db": 0,
            "resolved_conflicts": 0,
            "synced_legacy_to_db": 0,
            "recovered_records": 0,
        }
        if self.demo_mode or not hasattr(self, "db") or self.db is None:
            return summary

        self._legacy_unsynced_member_ids = set()
        needs_json_rewrite = False
        try:
            with self.db.session_scope() as s:
                rows = s.query(MemberRecord).all()
                db_deleted_ids = {
                    row.id for row in rows
                    if row.status == "DELETED" and bool((row.deleted_at or "").strip())
                }
                db_active_by_id = {row.id: row for row in rows if row.id not in db_deleted_ids}
                db_active_by_uname = {
                    (row.username or "").strip().lower(): row
                    for row in db_active_by_id.values()
                }

                # 1. Never revive DB-deleted tombstones from stale JSON
                kept_members: List[Dict[str, Any]] = []
                for m in self.members:
                    if m.get("id") in db_deleted_ids:
                        summary["pruned_deleted"] += 1
                        needs_json_rewrite = True
                        continue
                    kept_members.append(m)
                self.members = kept_members

                # 2. Recover DB-committed active/pending members missing from JSON (e.g. prior JSON write failure)
                ram_by_id = {m.get("id"): m for m in self.members if m.get("id")}
                for mid, row in db_active_by_id.items():
                    if mid not in ram_by_id:
                        recovered = _member_dict_from_db_row(row)
                        self.members.insert(0, recovered)
                        ram_by_id[mid] = recovered
                        summary["recovered_from_db"] += 1
                        needs_json_rewrite = True
                        if row.status not in {
                            "SUSPENDED", "ARCHIVED", "DELETE_ELIGIBLE",
                            "DELETION_IN_PROGRESS", "DELETION_PARTIAL_FAILURE", "DELETED", "REVOKED"
                        }:
                            get_revocation_store().register_active_member(row.username, row.id)
                    else:
                        cur_m = ram_by_id[mid]
                        if int(row.version or 1) > int(cur_m.get("version") or 1):
                            rebuilt = _member_dict_from_db_row(row)
                            cur_m.clear()
                            cur_m.update(rebuilt)
                            needs_json_rewrite = True

                # 3. Sync legacy JSON-only members into DB (or quarantine stale duplicate JSON against DB owner)
                cleaned_after_conflict: List[Dict[str, Any]] = []
                for m in self.members:
                    mid = m.get("id")
                    uname = (m.get("username") or "").strip().lower()
                    if not mid or not uname:
                        cleaned_after_conflict.append(m)
                        continue
                    if mid in db_active_by_id:
                        cleaned_after_conflict.append(m)
                        continue
                    existing_owner = db_active_by_uname.get(uname)
                    if existing_owner is not None:
                        summary["resolved_conflicts"] += 1
                        needs_json_rewrite = True
                        if self.db.has_recovery_table:
                            s.add(
                                PersistenceRecoveryRecord(
                                    id=f"rec-{uuid.uuid4().hex[:12]}",
                                    entity_type="MEMBER",
                                    entity_id=mid,
                                    username=m.get("username"),
                                    operation="CONFLICT_PRUNE_STALE_JSON",
                                    status="RECOVERED",
                                    error_detail=f"Removed duplicate JSON record {mid}; DB active record {existing_owner.id} retained.",
                                    payload_json={"kept_id": existing_owner.id, "removed_id": mid},
                                    resolved_at=datetime.now(timezone.utc),
                                )
                            )
                        continue

                    if any(r.username.lower() == uname for r in rows if r.id in db_deleted_ids) and self.db.has_legacy_username_unique_index:
                        # Legacy DB still holds unconditional unique index for a deleted member; do not crash startup or wipe JSON.
                        logger.warning(
                            "Phát hiện bản ghi JSON chưa đồng bộ DB (%s / @%s) trong khi DB vẫn dùng unique index cũ.",
                            mid,
                            m.get("username"),
                        )
                        self._legacy_unsynced_member_ids.add(mid)
                        cleaned_after_conflict.append(m)
                        continue

                    s.add(
                        MemberRecord(
                            id=mid,
                            username=m["username"],
                            email=str(m.get("email") or ""),
                            full_name=str(m.get("full_name") or ""),
                            role=str(m.get("role") or "RESEARCHER"),
                            status=str(m.get("status") or "ACTIVE"),
                            provisioning_state=str(m.get("provisioning_state") or "PENDING"),
                            assigned_server=str(m.get("assigned_server") or settings.LOCAL_HOST_ID),
                            workspace_name=str(m.get("workspace_name") or f"ws-{m['username']}"),
                            workspace_dir=m.get("workspace_dir"),
                            ssh_port=m.get("ssh_port"),
                            storage_quota_gb=int(m.get("storage_quota_gb") or 100),
                            cpu_limit_cores=int(m.get("cpu_limit_cores") or 8),
                            ram_limit_gb=int(m.get("ram_limit_gb") or 16),
                            vram_quota_gb=int(m.get("vram_quota_gb") or 12),
                            gpu_permissions_json=list(m.get("gpu_permissions") or []),
                            archived_at=m.get("archived_at"),
                            archive_manifest_json=m.get("archive_manifest"),
                            deleted_at=m.get("deleted_at"),
                            version=int(m.get("version") or 1),
                            payload_json=dict(m),
                            created_at=str(m.get("created_at") or ""),
                        )
                    )
                    summary["synced_legacy_to_db"] += 1
                    cleaned_after_conflict.append(m)
                self.members = cleaned_after_conflict

                pending_recs: List[PersistenceRecoveryRecord] = []
                if self.db.has_recovery_table:
                    pending_recs = (
                        s.query(PersistenceRecoveryRecord)
                        .filter(PersistenceRecoveryRecord.status == "PENDING_RECOVERY")
                        .all()
                    )

            if not self.demo_mode and (needs_json_rewrite or pending_recs):
                atomic_write_json(os.path.join(self.data_dir, "members.json"), self.members, mode=0o600)
                if pending_recs and self.db.has_recovery_table:
                    now_dt = datetime.now(timezone.utc)
                    with self.db.session_scope() as s2:
                        for pr in pending_recs:
                            row_pr = s2.get(PersistenceRecoveryRecord, pr.id)
                            if row_pr is not None and row_pr.status == "PENDING_RECOVERY":
                                row_pr.status = "RECOVERED"
                                row_pr.resolved_at = now_dt
                                summary["recovered_records"] += 1
        except Exception as exc:
            logger.warning("Initial DB sync / reconcile skipped cleanly: %s", exc)
        return summary

    def _save_persistent_data(self, *, audit_only: bool = False) -> None:
        """
        Persist DB FIRST, then write atomic JSON mirrors.
        - If DB fails (e.g. IntegrityError / outage), JSON files are NEVER mutated.
        - If DB succeeds and JSON write fails, record a durable PersistenceRecoveryRecord in DB
          and raise MemberPersistenceError(503) with recovery_id (never silently succeed).
        """
        with self._persist_lock:
            has_db = hasattr(self, "db") and self.db is not None
            if has_db:
                try:
                    with self.db.session_scope() as s:
                        if not audit_only:
                            legacy_idx = getattr(self.db, "has_legacy_username_unique_index", False)
                            legacy_ids = getattr(self, "_legacy_unsynced_member_ids", set())
                            for m in self.members:
                                mid = m.get("id")
                                uname = m.get("username")
                                if not mid or not uname:
                                    continue
                                if mid in legacy_ids and legacy_idx:
                                    continue
                                row = s.get(MemberRecord, mid)
                                if row is None:
                                    row = MemberRecord(id=mid, username=uname)
                                    s.add(row)
                                elif row.status == "DELETED" and row.deleted_at:
                                    raise MemberConflictError(
                                        "DELETED_TOMBSTONE_IMMUTABLE",
                                        f"Bản ghi lịch sử '{mid}' (@{uname}) đã bị xóa vĩnh viễn và không thể bị ghi đè.",
                                        409,
                                    )
                                row.email = str(m.get("email") or "")
                                row.full_name = str(m.get("full_name") or "")
                                row.role = str(m.get("role") or "RESEARCHER")
                                row.status = str(m.get("status") or "ACTIVE")
                                row.provisioning_state = str(m.get("provisioning_state") or "PENDING")
                                row.assigned_server = str(m.get("assigned_server") or settings.LOCAL_HOST_ID)
                                row.workspace_name = str(m.get("workspace_name") or f"ws-{uname}")
                                row.workspace_dir = m.get("workspace_dir")
                                row.ssh_port = m.get("ssh_port")
                                row.storage_quota_gb = int(m.get("storage_quota_gb") or 100)
                                row.cpu_limit_cores = int(m.get("cpu_limit_cores") or 8)
                                row.ram_limit_gb = int(m.get("ram_limit_gb") or 16)
                                row.vram_quota_gb = int(m.get("vram_quota_gb") or 12)
                                row.gpu_permissions_json = list(m.get("gpu_permissions") or [])
                                row.archived_at = m.get("archived_at")
                                row.archive_manifest_json = m.get("archive_manifest")
                                row.deleted_at = m.get("deleted_at")
                                row.version = int(m.get("version") or 1)
                                row.payload_json = dict(m)
                                row.created_at = str(m.get("created_at") or "")

                        for entry in self.audit_logs[:25]:
                            eid = entry.get("id")
                            if not eid or s.get(AuditEventRecord, eid) is not None:
                                continue
                            s.add(
                                AuditEventRecord(
                                    id=eid,
                                    timestamp=str(entry.get("timestamp") or ""),
                                    actor=str(entry.get("actor") or "system"),
                                    action=str(entry.get("action") or "EVENT"),
                                    target_type=str(entry.get("target_type") or "SYSTEM"),
                                    target_id=str(entry.get("target_id") or ""),
                                    result=str(entry.get("result") or "SUCCESS"),
                                    correlation_id=str(entry.get("correlation_id") or "corr-legacy"),
                                    details=str(entry.get("details") or ""),
                                )
                            )
                except MemberConflictError:
                    raise
                except IntegrityError as ie:
                    logger.warning("Database IntegrityError during persistence (JSON untouched): %s", ie)
                    err_lower = str(ie).lower()
                    if any(tok in err_lower for tok in ("members.username", "uq_members_active_username", "ix_members_username", "unique constraint")):
                        raise MemberConflictError(
                            "USERNAME_CONFLICT",
                            "Tên người dùng hoặc workspace đã tồn tại trong cơ sở dữ liệu.",
                            409,
                        ) from ie
                    raise MemberPersistenceError(
                        "DB_INTEGRITY_ERROR",
                        "Xung đột ràng buộc cơ sở dữ liệu khi lưu trạng thái.",
                        409,
                    ) from ie
                except Exception as db_exc:
                    logger.exception("Database persistence failed before writing JSON files")
                    raise MemberPersistenceError(
                        "DB_PERSISTENCE_FAILED",
                        "Không thể ghi dữ liệu vào cơ sở dữ liệu; chưa ghi thay đổi ra tệp JSON.",
                        503,
                    ) from db_exc

            try:
                if audit_only:
                    atomic_write_json(os.path.join(self.data_dir, "audit_logs.json"), self.audit_logs, mode=0o600)
                else:
                    atomic_write_json(os.path.join(self.data_dir, "members.json"), self.members, mode=0o600)
                    atomic_write_json(os.path.join(self.data_dir, "ssh_policies.json"), self.ssh_policies, mode=0o600)
                    atomic_write_json(os.path.join(self.data_dir, "audit_logs.json"), self.audit_logs, mode=0o600)
                    atomic_write_json(os.path.join(self.data_dir, "storage_allocations.json"), self.storage_allocations, mode=0o600)
                    atomic_write_json(os.path.join(self.data_dir, "ssh_endpoints.json"), self.ssh_endpoints, mode=0o600)
            except Exception as json_exc:
                recovery_id = f"rec-{uuid.uuid4().hex[:12]}"
                logger.exception("JSON mirror write failed after DB commit (recovery_id=%s)", recovery_id)
                if has_db and getattr(self.db, "has_recovery_table", False):
                    try:
                        with self.db.session_scope() as s_rec:
                            s_rec.add(
                                PersistenceRecoveryRecord(
                                    id=recovery_id,
                                    entity_type="PERSISTENCE",
                                    entity_id=recovery_id,
                                    username=None,
                                    operation="SAVE_PERSISTENT_DATA",
                                    status="PENDING_RECOVERY",
                                    error_detail=str(json_exc),
                                    payload_json={
                                        "failed_stage": "json_write",
                                        "member_ids": [m.get("id") for m in self.members if m.get("id")],
                                    },
                                )
                            )
                    except Exception:
                        logger.exception("Failed to write PersistenceRecoveryRecord %s", recovery_id)
                raise MemberPersistenceError(
                    "JSON_PERSISTENCE_FAILED",
                    f"Cơ sở dữ liệu đã lưu nhưng đồng bộ tệp JSON thất bại (recovery_id={recovery_id}). Hệ thống sẽ tự hòa giải khi khởi động lại hoặc đồng bộ lại.",
                    503,
                    recovery_id=recovery_id,
                ) from json_exc

            if has_db and getattr(self.db, "has_recovery_table", False):
                try:
                    with self.db.session_scope() as s_ok:
                        pending = (
                            s_ok.query(PersistenceRecoveryRecord)
                            .filter(
                                PersistenceRecoveryRecord.operation == "SAVE_PERSISTENT_DATA",
                                PersistenceRecoveryRecord.status == "PENDING_RECOVERY",
                            )
                            .all()
                        )
                        if pending:
                            now_dt = datetime.now(timezone.utc)
                            for pr in pending:
                                pr.status = "RECOVERED"
                                pr.resolved_at = now_dt
                except Exception:
                    pass

    def save_audit_logs(self) -> None:
        """Persist audit logs cleanly without rewriting unrelated member/endpoint mirrors."""
        self._save_persistent_data(audit_only=True)

    @property
    def workspaces(self) -> List[Dict[str, Any]]:
        """Danh sách workspaces động từ WorkspaceController kết hợp legacy SSH endpoints."""
        if hasattr(self, "workspace_controller") and self.workspace_controller is not None:
            return self.workspace_controller.list_workspaces(legacy_endpoints=getattr(self, "ssh_endpoints", []))
        return []

    def _collect_servers(self, jobs_snapshot: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        if not self.demo_mode:
            ws = self.host_monitor.get_local_workstation_telemetry()
            track = self.host_monitor.get_remote_track_telemetry()
            ws["active_members"] = sum(1 for m in self.members if m.get("assigned_server") == "workstation" and m.get("status") == "ACTIVE")
            track["active_members"] = sum(1 for m in self.members if m.get("assigned_server") == "track" and m.get("status") == "ACTIVE")
            jobs_by_server = jobs_snapshot if jobs_snapshot is not None else self.jobs
            ws["active_jobs"] = sum(1 for job in jobs_by_server if job.get("serverId", job.get("server_id")) == "workstation")
            track["active_jobs"] = sum(1 for job in jobs_by_server if job.get("serverId", job.get("server_id")) == "track")
            return [track, ws]
        return self._demo_servers

    @property
    def servers(self) -> List[Dict[str, Any]]:
        return self._collect_servers()

    @servers.setter
    def servers(self, val: List[Dict[str, Any]]):
        self._demo_servers = val

    @property
    def gpus(self) -> List[Dict[str, Any]]:
        if not self.demo_mode:
            live_gpus = self.nvml_adapter.get_live_gpu_telemetry_sync()
            try:
                reservations = self.gpu_scheduler.list_reservations()
            except Exception:
                reservations = []
            for g in live_gpus:
                active = next((r for r in reservations if r["gpu_uuid"] == g.get("uuid")
                               and r["state"] in ("RESERVED", "STARTING", "RUNNING", "STOP_REQUESTED")), None)
                g["reservation"] = active
                g["scheduler_state"] = active["state"] if active else ("BUSY_EXTERNAL" if g.get("active_processes") else "FREE")
            return live_gpus
        return self._demo_gpus

    @gpus.setter
    def gpus(self, val: List[Dict[str, Any]]):
        self._demo_gpus = val

    @property
    def storage_pools(self) -> List[Dict[str, Any]]:
        if not self.demo_mode:
            if self.host_monitor.is_containerized:
                return []
            import psutil
            try:
                local_id = settings.LOCAL_HOST_ID
                local_name = "Server 02 — Workstation (aiot-ws1 • Cục bộ)" if local_id == "workstation" else "Server 01 — Server Rack (aiotlab-rack)"
                pools = []
                seen_devices = set()

                # Quét tất cả phân vùng đĩa vật lý thực tế trên máy chủ
                try:
                    parts = psutil.disk_partitions(all=False)
                except Exception:
                    parts = []

                for p in parts:
                    dev = p.device
                    mnt = p.mountpoint
                    fstype = p.fstype
                    if not dev or not mnt or not fstype:
                        continue
                    if dev.startswith("/dev/loop") or fstype in ("vfat", "iso9660", "squashfs", "tmpfs", "devtmpfs", "swap"):
                        continue
                    if not os.path.exists(mnt):
                        continue
                    if dev in seen_devices:
                        continue
                    seen_devices.add(dev)

                    try:
                        u = psutil.disk_usage(mnt)
                        cap = round(u.total / (1024**3), 1)
                        used = round(u.used / (1024**3), 1)
                        free = round(u.free / (1024**3), 1)

                        dev_base = os.path.basename(dev)
                        if mnt == "/" or dev_base == "sda2":
                            pool_id = "pool-root"
                            pool_label = f"Ổ đĩa Hệ thống Root ({fstype.upper()} {dev} — {cap} GB)"
                            is_def = False
                            health = "ONLINE_LOCAL_ROOT"
                        elif "HDD3" in mnt or dev_base == "sdc":
                            pool_id = "pool-hdd3"
                            if os.path.ismount("/mnt/HDD3"):
                                mnt = "/mnt/HDD3"
                            pool_label = f"Ổ đĩa Nghiên cứu AI HDD3 (/mnt/HDD3 — {cap} GB)"
                            is_def = True
                            health = "ONLINE_PRIMARY_DATA_ARRAY"
                        else:
                            pool_id = f"pool-{dev_base}"
                            pool_label = f"Ổ đĩa Lưu trữ Thứ cấp {dev_base.upper()} ({dev} — {cap} GB)"
                            is_def = False
                            health = "ONLINE_SECONDARY_ARRAY"

                        pools.append({
                            "id": pool_id,
                            "server_id": local_id,
                            "server_name": local_name,
                            "name": pool_id,
                            "label": pool_label,
                            "disk_identifier": dev,
                            "mount_point": mnt,
                            "driver": fstype,
                            "capacity_gb": cap,
                            "used_gb": used,
                            "free_gb": free,
                            "health_status": health,
                            "is_default": is_def
                        })
                    except Exception:
                        continue

                # Đảm bảo luôn có ít nhất pool-root nếu môi trường sandbox không cho quét disk_partitions
                if not any(p["id"] == "pool-root" for p in pools):
                    mnt_info = get_real_root_mount_info()
                    root_u = psutil.disk_usage("/etc" if psutil.disk_usage("/").total < 100 * (1024**3) and os.path.exists("/etc") else "/")
                    pools.append({
                        "id": "pool-root",
                        "server_id": local_id,
                        "server_name": local_name,
                        "name": "pool-root",
                        "label": f"Ổ đĩa Hệ thống Root ({mnt_info['fstype'].upper()} {mnt_info['device']})",
                        "disk_identifier": mnt_info["device"],
                        "mount_point": "/",
                        "driver": "dir",
                        "capacity_gb": round(root_u.total / (1024**3), 1),
                        "used_gb": round(root_u.used / (1024**3), 1),
                        "free_gb": round(root_u.free / (1024**3), 1),
                        "health_status": "ONLINE_LOCAL_ROOT",
                        "is_default": not any(p.get("is_default") for p in pools)
                    })

                # Sắp xếp ưu tiên pool mặc định (HDD3) lên đầu, tiếp đến pool-root, rồi các ổ khác
                pools.sort(key=lambda x: (not x.get("is_default", False), x["id"] != "pool-root", x["id"]))
                return pools
            except Exception:
                return []
        return self._demo_storage_pools

    @storage_pools.setter
    def storage_pools(self, val: List[Dict[str, Any]]):
        self._demo_storage_pools = val

    @property
    def jobs(self) -> List[Dict[str, Any]]:
        if not self.demo_mode:
            return self.process_monitor.get_active_compute_jobs(members_db=self.members)
        return self._demo_jobs

    @jobs.setter
    def jobs(self, val: List[Dict[str, Any]]):
        self._demo_jobs = val

    def _init_data(self):
        self._demo_servers = [
            {
                "id": "track",
                "name": "Server 01 — Track",
                "hostname": "track.lab.internal",
                "location": "Phòng Lab Hà Nội (Khu A)",
                "status": "online",
                "ip_address": "10.10.1.10",
                "vpn_ip": "10.200.0.1",
                "uptime": "45 ngày 12 giờ",
                "cpu_model": "AMD EPYC 7763 64-Core Processor (128 vCPUs)",
                "cpu_cores": 64,
                "cpu_usage_percent": 42.5,
                "ram_total_gb": 256.0,
                "ram_used_gb": 174.2,
                "ram_usage_percent": 68.0,
                "disk_total_tb": 8.0,
                "disk_used_tb": 4.32,
                "disk_usage_percent": 54.0,
                "disk_type": "ZFS RAID-Z2 (NVMe Pools)",
                "network_in_mbps": 620,
                "network_out_mbps": 580,
                "gpu_count": 3,
                "active_members": 4,
                "active_jobs": 5,
                "incus_version": "6.0.2 LTS",
                "driver_version": "550.120",
                "cuda_version": "12.4",
                "last_updated": "Vừa xong"
            },
            {
                "id": "workstation",
                "name": "Server 02 — Workstation",
                "hostname": "ws.lab.internal",
                "location": "Phòng Lab TP.HCM (Khu B)",
                "status": "online",
                "ip_address": "10.10.2.20",
                "vpn_ip": "10.200.0.2",
                "uptime": "18 ngày 06 giờ",
                "cpu_model": "Intel Xeon Gold 6338 32-Core Processor (64 vCPUs)",
                "cpu_cores": 32,
                "cpu_usage_percent": 28.3,
                "ram_total_gb": 128.0,
                "ram_used_gb": 57.6,
                "ram_usage_percent": 45.0,
                "disk_total_tb": 4.0,
                "disk_used_tb": 1.52,
                "disk_usage_percent": 38.0,
                "disk_type": "ZFS Mirror (Enterprise SSD)",
                "network_in_mbps": 210,
                "network_out_mbps": 180,
                "gpu_count": 1,
                "active_members": 1,
                "active_jobs": 1,
                "incus_version": "6.0.2 LTS",
                "driver_version": "550.120",
                "cuda_version": "12.4",
                "last_updated": "Vừa xong"
            }
        ]

        self._demo_gpus = [
            {
                "id": "track-gpu-01",
                "display_label": "TRACK-GPU-01",
                "server_id": "track",
                "server_name": "Server Track",
                "model": "NVIDIA A30 24GB HBM2",
                "pci_bus_id": "0000:3b:00.0",
                "uuid": "GPU-9b48a1c2-3e4f-4a89-91a2-7b1c4e8d0001",
                "vram_total_mb": 24576,
                "vram_used_mb": 18432,
                "gpu_util_percent": 88,
                "temp_celsius": 64,
                "power_watts": 142,
                "power_limit_watts": 165,
                "allocation_mode": "SHARED",
                "enforcement_state": "DRIVER_ENFORCED_VERIFIED",
                "active_processes": [
                    {"pid": 104231, "name": "python (train_llama3.py)", "username": "hieulc", "workspace": "ws-hieulc", "vram_mb": 12288, "cpu_percent": 380.0},
                    {"pid": 105892, "name": "python (eval_bench.py)", "username": "namnv", "workspace": "ws-namnv", "vram_mb": 6144, "cpu_percent": 110.0}
                ],
                "active_users": ["hieulc", "namnv"],
                "associated_jobs": ["job-101", "job-102"]
            },
            {
                "id": "track-gpu-02",
                "display_label": "TRACK-GPU-02",
                "server_id": "track",
                "server_name": "Server Track",
                "model": "NVIDIA A30 24GB HBM2",
                "pci_bus_id": "0000:5e:00.0",
                "uuid": "GPU-9b48a1c2-3e4f-4a89-91a2-7b1c4e8d0002",
                "vram_total_mb": 24576,
                "vram_used_mb": 12288,
                "gpu_util_percent": 62,
                "temp_celsius": 58,
                "power_watts": 115,
                "power_limit_watts": 165,
                "allocation_mode": "EXCLUSIVE",
                "enforcement_state": "HARDWARE_ENFORCED",
                "active_processes": [
                    {"pid": 109314, "name": "python (qwen_finetune.py)", "username": "tuanpm", "workspace": "ws-tuanpm", "vram_mb": 12288, "cpu_percent": 240.0}
                ],
                "active_users": ["tuanpm"],
                "associated_jobs": ["job-103"]
            },
            {
                "id": "track-gpu-03",
                "display_label": "TRACK-GPU-03",
                "server_id": "track",
                "server_name": "Server Track",
                "model": "NVIDIA A30 24GB HBM2",
                "pci_bus_id": "0000:86:00.0",
                "uuid": "GPU-9b48a1c2-3e4f-4a89-91a2-7b1c4e8d0003",
                "vram_total_mb": 24576,
                "vram_used_mb": 0,
                "gpu_util_percent": 0,
                "temp_celsius": 38,
                "power_watts": 32,
                "power_limit_watts": 165,
                "allocation_mode": "EXCLUSIVE",
                "enforcement_state": "DRIVER_ENFORCED_VERIFIED",
                "active_processes": [],
                "active_users": [],
                "associated_jobs": []
            },
            {
                "id": "ws-gpu-01",
                "display_label": "WS-GPU-01",
                "server_id": "workstation",
                "server_name": "Server Workstation",
                "model": "NVIDIA A30 24GB HBM2",
                "pci_bus_id": "0000:18:00.0",
                "uuid": "GPU-8c37b2d1-2f3e-4b78-8091-6a0b3d7c0004",
                "vram_total_mb": 24576,
                "vram_used_mb": 16384,
                "gpu_util_percent": 75,
                "temp_celsius": 61,
                "power_watts": 128,
                "power_limit_watts": 165,
                "allocation_mode": "SHARED",
                "enforcement_state": "MONITOR_ONLY",
                "active_processes": [
                    {"pid": 20412, "name": "python (diffusion_train.py)", "username": "anhlt", "workspace": "ws-anhlt", "vram_mb": 16384, "cpu_percent": 310.0}
                ],
                "active_users": ["anhlt"],
                "associated_jobs": ["job-104"]
            }
        ]

        self._demo_storage_pools = [
            {
                "id": "pool-nvme-fast",
                "server_id": "track",
                "server_name": "Server 01 — Track",
                "name": "pool-nvme-fast",
                "label": "SSD NVMe Siêu tốc — Storage Pool A",
                "disk_identifier": "wwn-0x5002538f411b4101",
                "mount_point": "/var/lib/incus/storage-pools/pool-nvme-fast",
                "driver": "zfs",
                "capacity_gb": 8000.0,
                "used_gb": 4320.0,
                "free_gb": 3680.0,
                "health_status": "HEALTHY",
                "is_default": True
            },
            {
                "id": "pool-zfs-bulk",
                "server_id": "track",
                "server_name": "Server 01 — Track",
                "name": "pool-zfs-bulk",
                "label": "HDD Lưu trữ Lớn — Storage Pool B",
                "disk_identifier": "wwn-0x5000cca268e31249",
                "mount_point": "/var/lib/incus/storage-pools/pool-zfs-bulk",
                "driver": "zfs",
                "capacity_gb": 16000.0,
                "used_gb": 4600.0,
                "free_gb": 11400.0,
                "health_status": "HEALTHY",
                "is_default": False
            },
            {
                "id": "pool-workstation-ssd",
                "server_id": "workstation",
                "server_name": "Server 02 — Workstation",
                "name": "pool-workstation-ssd",
                "label": "SSD Doanh nghiệp — Storage Pool A",
                "disk_identifier": "wwn-0x5002538f411b5202",
                "mount_point": "/var/lib/incus/storage-pools/pool-workstation-ssd",
                "driver": "zfs",
                "capacity_gb": 4000.0,
                "used_gb": 1520.0,
                "free_gb": 2480.0,
                "health_status": "HEALTHY",
                "is_default": True
            }
        ]

        self.ssh_endpoints = [
            {"id": "ep-1", "member_id": "mem-1", "server_id": "track", "listen_address": "10.200.0.1", "listen_port": 22001, "container_port": 22, "protocol": "tcp", "status": "ACTIVE"},
            {"id": "ep-2", "member_id": "mem-2", "server_id": "track", "listen_address": "10.200.0.1", "listen_port": 22002, "container_port": 22, "protocol": "tcp", "status": "ACTIVE"},
            {"id": "ep-3", "member_id": "mem-3", "server_id": "track", "listen_address": "10.200.0.1", "listen_port": 22003, "container_port": 22, "protocol": "tcp", "status": "ACTIVE"},
            {"id": "ep-4", "member_id": "mem-4", "server_id": "workstation", "listen_address": "10.200.0.2", "listen_port": 22001, "container_port": 22, "protocol": "tcp", "status": "ACTIVE"}
        ]

        self.storage_allocations = [
            {"id": "sa-1", "member_id": "mem-1", "pool_id": "pool-nvme-fast", "volume_name": "data-hieulc", "mount_path": "/workspace", "quota_gb": 500, "used_gb": 182, "read_only": False, "backup_policy": "DAILY_SNAPSHOT", "shared_datasets": ["/shared/datasets", "/data/models"], "created_at": "2024-01-15"},
            {"id": "sa-2", "member_id": "mem-2", "pool_id": "pool-nvme-fast", "volume_name": "data-namnv", "mount_path": "/workspace", "quota_gb": 200, "used_gb": 85, "read_only": False, "backup_policy": "DAILY_SNAPSHOT", "shared_datasets": ["/shared/datasets"], "created_at": "2024-02-01"},
            {"id": "sa-3", "member_id": "mem-3", "pool_id": "pool-nvme-fast", "volume_name": "data-tuanpm", "mount_path": "/workspace", "quota_gb": 300, "used_gb": 120, "read_only": False, "backup_policy": "WEEKLY_SYNC", "shared_datasets": ["/shared/datasets"], "created_at": "2024-02-10"},
            {"id": "sa-4", "member_id": "mem-4", "pool_id": "pool-workstation-ssd", "volume_name": "data-anhlt", "mount_path": "/workspace", "quota_gb": 250, "used_gb": 64, "read_only": False, "backup_policy": "DAILY_SNAPSHOT", "shared_datasets": ["/shared/datasets"], "created_at": "2024-03-01"}
        ]

        self.ssh_policies = [
            {
                "workspace_id": "ws-hieulc",
                "linux_username": "hieulc",
                "authentication_mode": "PUBLIC_KEY_AND_PASSWORD",
                "password_status": "ACTIVE",
                "key_enrollment_status": "ENROLLED",
                "access_status": "ACTIVE",
                "min_password_length": 12,
                "failed_login_attempts": 0,
                "last_successful_login": "2026-11-03 21:15:30",
                "last_login_source_ip": "10.200.1.45",
                "updated_at": "2026-11-03 21:15:30"
            },
            {
                "workspace_id": "ws-namnv",
                "linux_username": "namnv",
                "authentication_mode": "PUBLIC_KEY_AND_PASSWORD",
                "password_status": "ACTIVE",
                "key_enrollment_status": "ENROLLED",
                "access_status": "ACTIVE",
                "min_password_length": 12,
                "failed_login_attempts": 0,
                "last_successful_login": "2026-11-03 20:30:15",
                "last_login_source_ip": "10.200.1.52",
                "updated_at": "2026-11-03 20:30:15"
            },
            {
                "workspace_id": "ws-tuanpm",
                "linux_username": "tuanpm",
                "authentication_mode": "PUBLIC_KEY_AND_PASSWORD",
                "password_status": "ACTIVE",
                "key_enrollment_status": "ENROLLED",
                "access_status": "ACTIVE",
                "min_password_length": 12,
                "failed_login_attempts": 1,
                "last_successful_login": "2026-11-03 19:43:02",
                "last_login_source_ip": "10.200.1.88",
                "updated_at": "2026-11-03 19:43:02"
            },
            {
                "workspace_id": "ws-anhlt",
                "linux_username": "anhlt",
                "authentication_mode": "PUBLIC_KEY_AND_PASSWORD",
                "password_status": "CHANGE_REQUIRED",
                "key_enrollment_status": "ENROLLED",
                "access_status": "ACTIVE",
                "min_password_length": 12,
                "failed_login_attempts": 0,
                "last_successful_login": "2026-11-03 17:10:00",
                "last_login_source_ip": "10.200.2.14",
                "updated_at": "2026-11-03 17:10:00"
            }
        ]

        self.ssh_login_events = [
            {
                "id": "log-evt-101",
                "member_id": "mem-1",
                "workspace_id": "ws-hieulc",
                "linux_username": "hieulc",
                "source_ip": "10.200.1.45",
                "listen_port": 22001,
                "auth_stage": "COMPLETED",
                "authentication_result": "SUCCESS",
                "failure_reason": None,
                "timestamp": "2026-11-03 21:15:30"
            },
            {
                "id": "log-evt-102",
                "member_id": "mem-1",
                "workspace_id": "ws-hieulc",
                "linux_username": "hieulc",
                "source_ip": "10.200.1.45",
                "listen_port": 22001,
                "auth_stage": "STAGE_2_PASSWORD",
                "authentication_result": "SUCCESS",
                "failure_reason": None,
                "timestamp": "2026-11-03 21:15:28"
            },
            {
                "id": "log-evt-103",
                "member_id": "mem-1",
                "workspace_id": "ws-hieulc",
                "linux_username": "hieulc",
                "source_ip": "10.200.1.45",
                "listen_port": 22001,
                "auth_stage": "STAGE_1_PUBLICKEY",
                "authentication_result": "SUCCESS",
                "failure_reason": None,
                "timestamp": "2026-11-03 21:15:25"
            },
            {
                "id": "log-evt-104",
                "member_id": "mem-3",
                "workspace_id": "ws-tuanpm",
                "linux_username": "tuanpm",
                "source_ip": "10.200.1.88",
                "listen_port": 22003,
                "auth_stage": "STAGE_2_PASSWORD",
                "authentication_result": "PASSWORD_FAILED",
                "failure_reason": "Sai mật khẩu Linux (1/5 lần thử)",
                "timestamp": "2026-11-03 19:42:10"
            },
            {
                "id": "log-evt-105",
                "member_id": "mem-3",
                "workspace_id": "ws-tuanpm",
                "linux_username": "tuanpm",
                "source_ip": "10.200.1.88",
                "listen_port": 22003,
                "auth_stage": "COMPLETED",
                "authentication_result": "SUCCESS",
                "failure_reason": None,
                "timestamp": "2026-11-03 19:43:02"
            }
        ]

        self.members = [
            {
                "id": "mem-1",
                "full_name": "Lê Cảnh Hiếu",
                "username": "hieulc",
                "email": "hieulc@lab.ai.vn",
                "role": "SUPER_ADMIN",
                "research_group": "NLP & Large Language Models",
                "assigned_server": "track",
                "workspace_name": "ws-hieulc",
                "status": "ACTIVE",
                "storage_pool_id": "pool-nvme-fast",
                "storage_pool_label": "SSD NVMe Siêu tốc (Pool A)",
                "mount_path": "/workspace",
                "storage_quota_gb": 500,
                "storage_used_gb": 182,
                "backup_policy": "DAILY_SNAPSHOT",
                "shared_datasets": ["/shared/datasets", "/data/models"],
                "ssh_status": "ONLINE",
                "ssh_sessions_count": 2,
                "ssh_ip": "10.200.0.1",
                "ssh_port": 22001,
                "ssh_key_status": "ACTIVE",
                "ssh_fingerprint": "SHA256:4X7x9fQm8zP1vL0k2j3h4g5f6e7d8c9b0a1s2d3f4e",
                "ssh_public_key_preview": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH... (hieu@macbook)",
                "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH8q9v8nZ+u8d/2X8s6mXvYq9s7t6r5e4w3q2a1s0d9 hieu@macbook",
                "sudo_privilege": "DENIED",
                "security_isolation_level": "UNPRIVILEGED_CGROUPS_ISOLATED",
                "privilege_validation": {
                    "unprivileged_container": True,
                    "sudo_test_failed_as_expected": True,
                    "no_sudo_group_membership": True,
                    "no_docker_socket_access": True,
                    "no_incus_socket_access": True,
                    "verification_timestamp": "2024-01-15T08:00:00Z",
                    "status": "VERIFIED_SAFE"
                },
                "cpu_limit_cores": 16,
                "ram_limit_gb": 64,
                "gpu_permissions": ["track-gpu-01", "track-gpu-02", "track-gpu-03"],
                "vram_policy": "Full 24GB (Shared)",
                "vram_quota_gb": 24,
                "gpu_allocation_mode": "SHARED",
                "max_runtime_hours": 72,
                "active_jobs_count": 1,
                "total_gpu_hours": 342.5,
                "created_at": "2024-01-15",
                "expires_at": "2027-01-15"
            },
            {
                "id": "mem-2",
                "full_name": "Nguyễn Văn Nam",
                "username": "namnv",
                "email": "namnv@lab.ai.vn",
                "role": "RESEARCHER",
                "research_group": "Speech & Audio Intelligence",
                "assigned_server": "track",
                "workspace_name": "ws-namnv",
                "status": "ACTIVE",
                "storage_pool_id": "pool-nvme-fast",
                "storage_pool_label": "SSD NVMe Siêu tốc (Pool A)",
                "mount_path": "/workspace",
                "storage_quota_gb": 200,
                "storage_used_gb": 85,
                "backup_policy": "DAILY_SNAPSHOT",
                "shared_datasets": ["/shared/datasets"],
                "ssh_status": "ONLINE",
                "ssh_sessions_count": 1,
                "ssh_ip": "10.200.0.1",
                "ssh_port": 22002,
                "ssh_key_status": "ACTIVE",
                "ssh_fingerprint": "SHA256:7K9m1nO3p5q7r9s1t3u5v7w9x1y3z5a7b9c1d3e5f7",
                "ssh_public_key_preview": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE... (nam@thinkpad)",
                "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r9s nam@thinkpad",
                "sudo_privilege": "DENIED",
                "security_isolation_level": "UNPRIVILEGED_CGROUPS_ISOLATED",
                "privilege_validation": {
                    "unprivileged_container": True,
                    "sudo_test_failed_as_expected": True,
                    "no_sudo_group_membership": True,
                    "no_docker_socket_access": True,
                    "no_incus_socket_access": True,
                    "verification_timestamp": "2024-02-01T08:00:00Z",
                    "status": "VERIFIED_SAFE"
                },
                "cpu_limit_cores": 8,
                "ram_limit_gb": 32,
                "gpu_permissions": ["track-gpu-01"],
                "vram_policy": "Quota 12GB (Shared)",
                "vram_quota_gb": 12,
                "gpu_allocation_mode": "SHARED",
                "max_runtime_hours": 24,
                "active_jobs_count": 1,
                "total_gpu_hours": 128.0,
                "created_at": "2024-02-01"
            },
            {
                "id": "mem-3",
                "full_name": "Phạm Minh Tuấn",
                "username": "tuanpm",
                "email": "tuanpm@lab.ai.vn",
                "role": "RESEARCHER",
                "research_group": "Multimodal AI & Robotics",
                "assigned_server": "track",
                "workspace_name": "ws-tuanpm",
                "status": "ACTIVE",
                "storage_pool_id": "pool-nvme-fast",
                "storage_pool_label": "SSD NVMe Siêu tốc (Pool A)",
                "mount_path": "/workspace",
                "storage_quota_gb": 300,
                "storage_used_gb": 120,
                "backup_policy": "WEEKLY_SYNC",
                "shared_datasets": ["/shared/datasets"],
                "ssh_status": "ONLINE",
                "ssh_sessions_count": 1,
                "ssh_ip": "10.200.0.1",
                "ssh_port": 22003,
                "ssh_key_status": "ACTIVE",
                "ssh_fingerprint": "SHA256:9Z8y7x6w5v4u3t2s1r0q9p8o7n6m5l4k3j2i1h0g9f",
                "ssh_public_key_preview": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAB... (tuan@desktop)",
                "ssh_public_key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAB... (tuan@desktop)",
                "sudo_privilege": "DENIED",
                "security_isolation_level": "UNPRIVILEGED_CGROUPS_ISOLATED",
                "privilege_validation": {
                    "unprivileged_container": True,
                    "sudo_test_failed_as_expected": True,
                    "no_sudo_group_membership": True,
                    "no_docker_socket_access": True,
                    "no_incus_socket_access": True,
                    "verification_timestamp": "2024-02-10T08:00:00Z",
                    "status": "VERIFIED_SAFE"
                },
                "cpu_limit_cores": 16,
                "ram_limit_gb": 64,
                "gpu_permissions": ["track-gpu-02"],
                "vram_policy": "Full 24GB (Exclusive)",
                "vram_quota_gb": 24,
                "gpu_allocation_mode": "EXCLUSIVE",
                "max_runtime_hours": 48,
                "active_jobs_count": 1,
                "total_gpu_hours": 215.3,
                "created_at": "2024-02-10"
            },
            {
                "id": "mem-4",
                "full_name": "Lê Tuấn Anh",
                "username": "anhlt",
                "email": "anhlt@lab.ai.vn",
                "role": "STUDENT",
                "research_group": "AI Capstone Projects",
                "assigned_server": "workstation",
                "workspace_name": "ws-anhlt",
                "status": "ACTIVE",
                "storage_pool_id": "pool-workstation-ssd",
                "storage_pool_label": "SSD Doanh nghiệp (Pool A)",
                "mount_path": "/workspace",
                "storage_quota_gb": 250,
                "storage_used_gb": 64,
                "backup_policy": "DAILY_SNAPSHOT",
                "shared_datasets": ["/shared/datasets"],
                "ssh_status": "ONLINE",
                "ssh_sessions_count": 1,
                "ssh_ip": "10.200.0.2",
                "ssh_port": 22001,
                "ssh_key_status": "ACTIVE",
                "ssh_fingerprint": "SHA256:2A4b6c8d0e2f4a6c8e0a2c4e6a8c0e2f4a6c8e0a2b",
                "ssh_public_key_preview": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIM... (anh@linux)",
                "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIM6x7y8z9a0b1c2d3e4f5g6h7i8j9k0l anh@linux",
                "sudo_privilege": "DENIED",
                "security_isolation_level": "UNPRIVILEGED_CGROUPS_ISOLATED",
                "privilege_validation": {
                    "unprivileged_container": True,
                    "sudo_test_failed_as_expected": True,
                    "no_sudo_group_membership": True,
                    "no_docker_socket_access": True,
                    "no_incus_socket_access": True,
                    "verification_timestamp": "2024-03-01T08:00:00Z",
                    "status": "VERIFIED_SAFE"
                },
                "cpu_limit_cores": 12,
                "ram_limit_gb": 48,
                "gpu_permissions": ["ws-gpu-01"],
                "vram_policy": "Full 24GB (Shared)",
                "vram_quota_gb": 24,
                "gpu_allocation_mode": "SHARED",
                "max_runtime_hours": 36,
                "active_jobs_count": 1,
                "total_gpu_hours": 184.2,
                "created_at": "2024-03-01"
            }
        ]

        self._demo_jobs = [
            {
                "id": "job-101",
                "username": "hieulc",
                "full_name": "Lê Cảnh Hiếu",
                "server_id": "track",
                "workspace": "ws-hieulc",
                "pid": 104231,
                "script": "python train_llama3_lora.py --batch_size=8 --lr=2e-4",
                "gpu_id": "track-gpu-01",
                "cpu_percent": 380.0,
                "ram_used_mb": 28672,
                "vram_used_mb": 12288,
                "start_time": "Hôm nay 08:30",
                "elapsed_time": "05h 42m",
                "status": "RUNNING",
                "ssh_status": "ATTACHED",
                "container_state": "RUNNING"
            },
            {
                "id": "job-102",
                "username": "namnv",
                "full_name": "Nguyễn Văn Nam",
                "server_id": "track",
                "workspace": "ws-namnv",
                "pid": 105892,
                "script": "python eval_benchmarks.py --model_dir=/data/models",
                "gpu_id": "track-gpu-01",
                "cpu_percent": 110.0,
                "ram_used_mb": 12288,
                "vram_used_mb": 6144,
                "start_time": "Hôm nay 11:15",
                "elapsed_time": "02h 57m",
                "status": "RUNNING",
                "ssh_status": "ATTACHED",
                "container_state": "RUNNING"
            },
            {
                "id": "job-103",
                "username": "tuanpm",
                "full_name": "Phạm Minh Tuấn",
                "server_id": "track",
                "workspace": "ws-tuanpm",
                "pid": 109314,
                "script": "python finetune_qwen.py --deepspeed=ds_config.json",
                "gpu_id": "track-gpu-02",
                "cpu_percent": 240.0,
                "ram_used_mb": 34816,
                "vram_used_mb": 12288,
                "start_time": "Hôm nay 06:00",
                "elapsed_time": "08h 12m",
                "status": "RUNNING",
                "ssh_status": "DETACHED (TMUX)",
                "container_state": "RUNNING"
            },
            {
                "id": "job-104",
                "username": "anhlt",
                "full_name": "Lê Tuấn Anh",
                "server_id": "workstation",
                "workspace": "ws-anhlt",
                "pid": 20412,
                "script": "python diffusion_train.py --steps=50000 --res=512",
                "gpu_id": "ws-gpu-01",
                "cpu_percent": 310.0,
                "ram_used_mb": 24576,
                "vram_used_mb": 16384,
                "start_time": "Hôm qua 22:00",
                "elapsed_time": "16h 12m",
                "status": "RUNNING",
                "ssh_status": "DETACHED (TMUX)",
                "container_state": "RUNNING"
            }
        ]

        self.security_reviews = [
            {
                "id": "sec-1",
                "repo_url": "https://github.com/vllm-project/vllm.git",
                "repo_name": "vllm-project/vllm",
                "commit_sha": "a7b3c9d8e2f1",
                "submitted_by": "hieulc",
                "submitted_at": "Hôm nay 10:15",
                "status": "APPROVED",
                "risk_score": 12,
                "findings": {
                    "high": 0,
                    "medium": 1,
                    "low": 2,
                    "crypto_mining": False,
                    "suspicious_networking": False,
                    "privilege_escalation": False,
                    "verified_clean": True
                },
                "summary": "Kho mã nguồn hợp lệ từ upstream vLLM. Đã quét không phát hiện mã độc.",
                "reviewed_by": "Admin (Tự động)"
            },
            {
                "id": "sec-2",
                "repo_url": "https://github.com/research-internal/custom-flash-attn.git",
                "repo_name": "custom-flash-attn",
                "commit_sha": "4f9d8a1c3b5e",
                "submitted_by": "namnv",
                "submitted_at": "Hôm nay 13:40",
                "status": "PENDING_REVIEW",
                "risk_score": 35,
                "findings": {
                    "high": 0,
                    "medium": 2,
                    "low": 4,
                    "crypto_mining": False,
                    "suspicious_networking": True,
                    "privilege_escalation": False,
                    "verified_clean": False
                },
                "summary": "Phát hiện gọi urllib tới IP lạ trong build script. Cần Admin xem xét trước khi duyệt clone vào workspace.",
                "reviewed_by": None
            }
        ]

        self.events = [
            {
                "id": "ev-1",
                "title": "Seminar: Tối ưu hóa suy luận LLM với vLLM & AWQ",
                "type": "SEMINAR",
                "date": "2024-11-08",
                "start_time": "14:00",
                "end_time": "16:00",
                "location": "Phòng Lab Hà Nội & Google Meet",
                "organizer": "Lê Cảnh Hiếu",
                "related_server": None,
                "status": "SCHEDULED",
                "description": "Báo cáo tiến độ triển khai kỹ thuật lượng tử hóa AWQ trên cụm GPU A30."
            },
            {
                "id": "ev-2",
                "title": "Bảo trì định kỳ ZFS Pool & Cập nhật Kernel",
                "type": "MAINTENANCE",
                "date": "2024-11-10",
                "start_time": "22:00",
                "end_time": "02:00",
                "location": "Server 01 — Track",
                "organizer": "System Admin",
                "related_server": "track",
                "status": "SCHEDULED",
                "description": "Scrub ZFS pool và kiểm tra tính toàn vẹn snapshot tự động."
            }
        ]

        self.audit_logs = [
            {
                "id": "aud-1",
                "actor": "admin@labops.local",
                "action": "GPU_ALLOCATED",
                "target_type": "GPU",
                "target_id": "track-gpu-01",
                "timestamp": "Hôm nay 11:15",
                "result": "SUCCESS",
                "correlation_id": "corr-98f21a4e-3c01",
                "details": "Cấp phát 6GB VRAM cho thành viên namnv (job-102)"
            },
            {
                "id": "aud-2",
                "actor": "admin@labops.local",
                "action": "WORKSPACE_CREATED",
                "target_type": "CONTAINER",
                "target_id": "ws-anhlt",
                "timestamp": "Hôm nay 09:30",
                "result": "SUCCESS",
                "correlation_id": "corr-17b84c3d-9a82",
                "details": "Khởi tạo thành công Incus container ws-anhlt trên Server Workstation"
            }
        ]

        # Load persistent storage overrides if present and not in unit testing mode
        if not self.demo_mode:
            self.members = []
            self.ssh_policies = []
            self.audit_logs = []
            self.storage_allocations = []
            self.ssh_endpoints = []
            self.ssh_login_events = []
            self.security_reviews = []
            persistent_files = {
                "members": "members.json",
                "ssh_policies": "ssh_policies.json",
                "audit_logs": "audit_logs.json",
                "storage_allocations": "storage_allocations.json",
                "ssh_endpoints": "ssh_endpoints.json",
            }
            for attribute, filename in persistent_files.items():
                path = os.path.join(self.data_dir, filename)
                if not os.path.exists(path):
                    continue
                value = safe_load_json(path, list)
                if not isinstance(value, list):
                    raise ValueError(f"{filename} must contain a JSON array")
                setattr(self, attribute, value)

    def get_summary(self) -> Dict[str, Any]:
        gpus_snapshot = self.gpus
        jobs_snapshot = self.jobs
        servers_snapshot = self._collect_servers(jobs_snapshot=jobs_snapshot)

        used_vram_gb = round(sum(gpu["vram_used_mb"] for gpu in gpus_snapshot) / 1024, 1)
        total_vram_gb = round(sum(gpu.get("vram_total_mb", 0) for gpu in gpus_snapshot) / 1024, 1)
        active_gpus = sum(1 for gpu in gpus_snapshot if gpu["vram_used_mb"] > 0)

        if not self.demo_mode:
            real_ssh = self.process_monitor.get_real_ssh_sessions()
            online_ssh = real_ssh.get("total_count", 0)
            active_sessions = online_ssh
        else:
            online_ssh = sum(1 for m in self.members if m["ssh_status"] == "ONLINE")
            active_sessions = sum(m["ssh_sessions_count"] for m in self.members)

        running_jobs = sum(1 for j in jobs_snapshot if j.get("status") == "RUNNING")

        return {
            "servers": {
                "total": len(servers_snapshot),
                "online": sum(1 for s in servers_snapshot if s["status"] == "online"),
                "offline": sum(1 for s in servers_snapshot if s["status"] == "offline"),
                "unknown": sum(1 for s in servers_snapshot if s["status"] in {"unknown", "degraded"})
            },
            "gpus": {
                "totalCount": len(gpus_snapshot),
                "model": ", ".join(sorted({gpu.get("model", "") for gpu in gpus_snapshot if gpu.get("model")})) or "UNKNOWN",
                "aggregateVramGb": int(total_vram_gb),
                "activeCount": active_gpus,
                "usedVramGb": int(used_vram_gb)
            },
            "members": {
                "onlineSsh": online_ssh,
                "activeSessions": active_sessions,
                "totalRegistered": len(self.members)
            },
            "jobs": {
                "running": running_jobs,
                "pending": sum(1 for job in jobs_snapshot if job.get("status") == "PENDING"),
                "failed": sum(1 for job in jobs_snapshot if job.get("status") == "FAILED")
            },
            "gpuHours": {"today": None, "last7Days": None, "previousPeriodChangePercent": None},
            "alerts": {"critical": None, "warnings": None, "unresolvedIncidents": None}
        }

    def get_server_processes(self, server_id: str, filter_type: str = "all") -> Dict[str, Any]:
        if server_id == settings.LOCAL_HOST_ID:
            return self.process_monitor.get_real_processes(filter_type=filter_type)
        return {
            "source_host": server_id,
            "collection_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "collector_status": "NOT_CONFIGURED",
            "filter": filter_type,
            "total_count": 0,
            "actual_observations": [],
            "error_details": "Remote process collector is not configured; connection state is not verified."
        }

    def get_server_gpu_processes(self, server_id: str) -> Dict[str, Any]:
        return self.get_server_processes(server_id, filter_type="gpu")

    def get_server_ssh_sessions(self, server_id: str) -> Dict[str, Any]:
        if server_id == settings.LOCAL_HOST_ID:
            return self.process_monitor.get_real_ssh_sessions()
        return {
            "source_host": server_id,
            "collection_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "collector_status": "NOT_CONFIGURED",
            "total_count": 0,
            "actual_observations": [],
            "error_details": "Remote SSH session collector is not configured; connection state is not verified."
        }

    def get_workspace_processes(self, workspace_id: str) -> Dict[str, Any]:
        all_procs = self.get_server_processes(settings.LOCAL_HOST_ID, filter_type="all")
        obs = [
            p for p in all_procs.get("actual_observations", [])
            if p.get("workspace") == workspace_id or p.get("container_id") == workspace_id
        ]
        return {
            "workspace_id": workspace_id,
            "collection_timestamp": all_procs.get("collection_timestamp"),
            "collector_status": all_procs.get("collector_status"),
            "total_count": len(obs),
            "actual_observations": obs,
            "error_details": None
        }

    def get_monitoring_health(self) -> Dict[str, Any]:
        if self.demo_mode:
            return {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "mode": "DEMO",
                "collectors": {"host_monitor": "DEMO", "nvml": "DEMO", "process_monitor": "DEMO", "ssh_sessions": "NOT_CONFIGURED", "incus": "DEMO", "wireguard_track": "NOT_VERIFIED"},
            }
        try:
            gpu_status = "LIVE" if self.nvml_adapter.get_live_gpu_telemetry_sync() else "UNAVAILABLE"
        except Exception:
            gpu_status = "UNAVAILABLE"
        process_status = self.process_monitor.get_real_processes("all").get("collector_status", "UNAVAILABLE")
        if self.host_monitor.is_containerized:
            process_status = "CONTAINER_ONLY"
        daemons = self.host_monitor.get_external_daemons_telemetry()
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "PRODUCTION",
            "collectors": {
                "host_monitor": "CONTAINER_ONLY" if self.host_monitor.is_containerized else "LIVE",
                "nvml": gpu_status,
                "process_monitor": process_status,
                "ssh_sessions": "LIVE" if not self.host_monitor.is_containerized else "NOT_CONFIGURED",
                "docker": daemons["docker"]["status"],
                "trivy": daemons["trivy"]["status"],
                "postgresql": daemons["postgresql"]["status"],
                "wireguard_track": daemons["wireguard"]["status"],
            },
            "daemons": daemons,
        }

    # ------------------------------------------------------------------
    # GPU scheduling (V2): production đi qua GpuScheduler, không giả lập.
    # ------------------------------------------------------------------
    def _audit(
        self,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        result: str,
        details: str,
        *,
        correlation_id: Optional[str] = None,
    ) -> None:
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:8]}",
            "actor": actor,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": result,
            "correlation_id": correlation_id or f"corr-{uuid.uuid4().hex[:12]}",
            "details": details,
        })

    def allocate_gpu(
        self,
        gpu_id: str,
        username: str,
        mode: str,
        requested_vram_gb: int = 0,
        job_name: str = "",
        actor: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        ttl_minutes: int = 240,
        is_admin: bool = True,
        enforce_permissions: bool = False,
    ) -> Dict[str, Any]:
        """Giữ độc quyền 1 GPU (theo UUID), kiểm tra gpu_permissions & TTL."""
        target = next((g for g in self.gpus if g["id"] == gpu_id or g.get("uuid") == gpu_id), None)
        if not target:
            raise ValueError(f"Không tìm thấy GPU với ID {gpu_id}")
        if not target.get("uuid"):
            raise SchedulerError("GPU_UUID_UNKNOWN", "GPU không có UUID quan sát được; từ chối cấp phát.", 503)
        actor = actor or username

        member = next((m for m in self.members if m.get("username") == username), None)
        if member is not None:
            if member.get("status") in {"SUSPENDED", "ARCHIVED", "DELETE_ELIGIBLE", "DELETION_IN_PROGRESS", "DELETED", "REVOKED"}:
                self._audit(actor, "GPU_RESERVE", "GPU", target["uuid"], "DENIED", f"Member @{username} suspended/deleted")
                raise SchedulerError("ACCOUNT_SUSPENDED", f"Tài khoản @{username} hiện bị khóa hoặc đang lưu trữ.", 403)
            allowed_gpus = list(member.get("gpu_permissions") or [])
            if (not is_admin or enforce_permissions) and target["id"] not in allowed_gpus and target["uuid"] not in allowed_gpus:
                self._audit(actor, "GPU_RESERVE", "GPU", target["uuid"], "DENIED", f"Missing GPU permission for @{username}")
                raise SchedulerError("GPU_PERMISSION_DENIED", "Tài khoản chưa được cấp quyền sử dụng thiết bị GPU này.", 403)
        elif enforce_permissions or not is_admin:
            self._audit(actor, "GPU_RESERVE", "GPU", target["uuid"], "DENIED", f"Unknown member @{username}")
            raise SchedulerError("GPU_PERMISSION_DENIED", "Tài khoản chưa được đăng ký quyền sử dụng GPU.", 403)

        try:
            r = self.gpu_scheduler.reserve(
                gpu_uuid=target["uuid"],
                owner=username,
                actor=actor,
                job_name=job_name,
                mode=mode,
                requested_vram_gb=int(requested_vram_gb or 0),
                ttl_minutes=int(ttl_minutes or 240),
                idempotency_key=idempotency_key,
            )
        except SchedulerError as e:
            self._audit(actor, "GPU_RESERVE", "GPU", target["uuid"], "DENIED", e.message)
            raise
        self._audit(actor, "GPU_RESERVE", "GPU", target["uuid"], "RESERVED",
                    f"Giữ độc quyền GPU {target['uuid']} cho {username} ({job_name}); runtime chưa xác minh")
        return {"success": True, "state": r["state"], "verification": "PENDING_VERIFICATION",
                "reservation": r,
                "message": "Đã giữ GPU. Tác vụ chỉ được coi là đang chạy khi Node Agent xác minh process."}

    def stop_job(self, job_id: str, actor: Optional[str] = None, is_admin: bool = True) -> Dict[str, Any]:
        """Dừng tác vụ/reservation. Chỉ ghi nhận yêu cầu khi chưa có xác minh runtime."""
        actor = actor or "unknown"
        r = self.gpu_scheduler.request_stop(job_id, actor=actor, is_admin=is_admin)
        verification = "VERIFIED" if r["state"] == "RELEASED" else "PENDING_VERIFICATION"
        self._audit(actor, "JOB_STOP", "JOB", job_id, r["state"], f"Yêu cầu dừng {job_id}; trạng thái {r['state']}")
        return {"success": r["state"] in {"RELEASED", "STOP_REQUESTED"}, "state": r["state"],
                "verification": verification, "reservation": r}

    def _resolve_gpu_metadata(
        self,
        gpu_id: str,
        gpu_obj: Optional[Dict[str, Any]],
        fallback_server: str = "workstation",
    ) -> Dict[str, Any]:
        """Trích xuất metadata chuẩn cho GPU (ưu tiên telemetry trực tiếp, fallback danh mục cụm)."""
        canonical_id = canonicalize_gpu_id(gpu_id)
        catalog = CLUSTER_GPU_CATALOG.get(canonical_id, {})
        if gpu_obj:
            raw_label = gpu_obj.get("displayLabel") or gpu_obj.get("display_label") or ""
            if canonical_id.startswith("track-gpu-") and raw_label.upper().startswith("TRACK-GPU-"):
                gpu_label = catalog.get("gpu_label") or raw_label.replace("TRACK-", "RACK-")
            else:
                gpu_label = raw_label or catalog.get("gpu_label") or canonical_id
            return {
                "gpu_id": canonical_id,
                "gpu_label": gpu_label,
                "gpu_model": gpu_obj.get("model") or catalog.get("gpu_model") or "NVIDIA A30",
                "server_id": gpu_obj.get("serverId") or gpu_obj.get("server_id") or catalog.get("server_id") or fallback_server,
                "server_name": gpu_obj.get("serverName") or gpu_obj.get("server_name") or catalog.get("server_name") or "Workstation",
                "pci_bus_id": gpu_obj.get("pciBusId") or gpu_obj.get("pci_bus_id") or catalog.get("pci_bus_id") or "",
                "uuid": gpu_obj.get("uuid") or "",
                "vram_total_mb": int(gpu_obj.get("vramTotalMb") or gpu_obj.get("vram_total_mb") or catalog.get("vram_total_mb") or 24576),
                "vram_used_mb": int(gpu_obj.get("vramUsedMb") or gpu_obj.get("vram_used_mb") or 0),
                "gpu_util_percent": int(gpu_obj.get("gpuUtilPercent") or gpu_obj.get("gpu_util_percent") or 0),
                "temp_celsius": int(round(float(gpu_obj.get("tempCelsius") or gpu_obj.get("temp_celsius") or 0))),
                "allocation_mode": gpu_obj.get("allocationMode") or gpu_obj.get("allocation_mode") or "SHARED",
                "is_online": True,
            }
        srv_id = catalog.get("server_id") or ("track" if ("rack" in canonical_id or "track" in canonical_id) else fallback_server)
        return {
            "gpu_id": canonical_id,
            "gpu_label": catalog.get("gpu_label") or canonical_id.upper(),
            "gpu_model": catalog.get("gpu_model") or "NVIDIA A30 (24GB HBM2)",
            "server_id": srv_id,
            "server_name": catalog.get("server_name") or (
                "Server 01 — Server Rack (aiotlab-rack)" if srv_id == "track" else "Server 02 — Workstation (aiot-ws1 • Cục bộ)"
            ),
            "pci_bus_id": catalog.get("pci_bus_id") or "",
            "uuid": "",
            "vram_total_mb": int(catalog.get("vram_total_mb") or 24576),
            "vram_used_mb": 0,
            "gpu_util_percent": 0,
            "temp_celsius": 0,
            "allocation_mode": "SHARED",
            "is_online": False,
        }

    def _summarize_gpu_workload(
        self,
        live_work: Optional[Dict[str, Any]],
        fallback_empty: str = "Chưa có tiến trình",
    ) -> Dict[str, Any]:
        """Tổng hợp tên script chính và tổng số worker DataLoader cho các PID GPU của một tài khoản."""
        if not live_work:
            return {
                "worker_count": 0,
                "script_summary": fallback_empty,
                "primary_pid": None,
                "active_processes": [],
            }
        import psutil

        pids = [int(p) for p in live_work.get("pids", []) if p]
        scripts = live_work.get("scripts", [])
        procs_count = int(live_work.get("processes_count") or len(pids))
        unique_worker_pids = set(pids)
        script_name = ""

        for pid in pids:
            try:
                proc = psutil.Process(pid)
                cmd = proc.cmdline()
                if not script_name:
                    for arg in cmd:
                        if arg.endswith(".py") and not arg.startswith("-"):
                            script_name = os.path.basename(arg)
                            break
                for child in proc.children(recursive=True):
                    unique_worker_pids.add(child.pid)
            except Exception:
                continue

        if not script_name and scripts:
            raw_script = str(scripts[0])
            # Extract filename inside parentheses if format is "python (11_train_rgb.py)"
            if "(" in raw_script and raw_script.endswith(")"):
                inner = raw_script.split("(", 1)[1][:-1].strip()
                script_name = os.path.basename(inner) if inner else raw_script
            else:
                script_name = os.path.basename(raw_script)

        worker_count = max(procs_count, len(unique_worker_pids))
        if worker_count > 1:
            script_summary = f"{script_name} ({worker_count} workers)" if script_name else f"{worker_count} workers"
        else:
            script_summary = script_name or fallback_empty

        return {
            "worker_count": worker_count,
            "script_summary": script_summary,
            "primary_pid": pids[0] if pids else None,
            "active_processes": live_work.get("active_processes", []),
        }

    def _build_gpu_allocation_entry(
        self,
        *,
        member_id: Optional[str],
        username: str,
        full_name: str,
        role: str,
        gpu_meta: Dict[str, Any],
        allocation_mode: str,
        vram_quota_gb: int,
        live_work: Optional[Dict[str, Any]],
        reservation: Optional[Dict[str, Any]] = None,
        fallback_script: str = "Chưa có tiến trình",
    ) -> Dict[str, Any]:
        cur_vram = int(live_work["vram_mb"]) if live_work else 0
        workload = self._summarize_gpu_workload(live_work, fallback_empty=fallback_script)
        is_gpu_online = bool(gpu_meta.get("is_online"))
        effective_mode = "EXCLUSIVE" if str(allocation_mode).upper() in ("EXCLUSIVE", "FULL_GPU_EXCLUSIVE") else str(allocation_mode or "SHARED").upper()

        if reservation and reservation.get("job_name") and workload["worker_count"] == 0:
            workload["script_summary"] = f"[Giữ chỗ] {reservation['job_name']}"

        if cur_vram > 0:
            status = "IN_USE"
            status_label = "Đang huấn luyện"
        elif reservation and is_gpu_online:
            status = "RESERVED"
            status_label = "Đang giữ chỗ độc quyền"
        elif not is_gpu_online:
            status = "OFFLINE_HOST"
            status_label = "Máy chủ Ngoại tuyến"
        else:
            status = "IDLE"
            status_label = "Sẵn sàng (Rảnh)"

        vram_gb = round(cur_vram / 1024, 1)
        return {
            "id": f"alloc-{username}-{gpu_meta['gpu_id']}",
            "member_id": member_id,
            "username": username,
            "full_name": full_name,
            "role": role,
            "gpu_id": gpu_meta["gpu_id"],
            "gpu_label": gpu_meta["gpu_label"],
            "gpu_model": gpu_meta["gpu_model"],
            "server_id": gpu_meta["server_id"],
            "server_name": gpu_meta["server_name"],
            "server_status": "online" if is_gpu_online else "offline",
            "allocation_mode": effective_mode,
            "vram_quota_gb": int(vram_quota_gb),
            "vram_used_mb": cur_vram,
            "current_vram_mb": cur_vram,
            "vram_used_gb": vram_gb,
            "current_vram_gb": vram_gb,
            "process_count": workload["worker_count"],
            "active_processes_count": workload["worker_count"],
            "primary_pid": workload["primary_pid"],
            "primary_script": workload["script_summary"],
            "workload_description": workload["script_summary"],
            "active_processes": workload["active_processes"],
            "status": status,
            "status_label": status_label,
            "is_active": status in ("IN_USE", "RESERVED"),
        }

    def get_gpu_account_allocations(self) -> List[Dict[str, Any]]:
        """
        Thống kê chi tiết tài khoản nào đang được cấp quyền cho GPU nào:
        Kết hợp cấu hình quyền thành viên (gpu_permissions, vram_quota, allocation_mode),
        reservation từ GPU Scheduler và tiến trình / VRAM chiếm dụng thực tế thời gian thực.
        """
        allocations: List[Dict[str, Any]] = []
        gpus = self.gpus
        gpu_map: Dict[str, Dict[str, Any]] = {}
        uuid_to_gpu_id: Dict[str, str] = {}
        for g in gpus:
            canon_id = canonicalize_gpu_id(g.get("id"))
            if canon_id:
                gpu_map[canon_id] = g
                if g.get("uuid"):
                    uuid_to_gpu_id[g["uuid"]] = canon_id

        # Danh sách người dùng hệ thống đang chiếm VRAM thực tế
        active_gpu_workloads: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for g in gpus:
            g_id = canonicalize_gpu_id(g.get("id"))
            for proc in g.get("active_processes", []):
                u = proc.get("username", "unknown")
                key = (u, g_id)
                if key not in active_gpu_workloads:
                    active_gpu_workloads[key] = {
                        "username": u,
                        "gpu_id": g_id,
                        "vram_mb": 0,
                        "processes_count": 0,
                        "scripts": [],
                        "pids": [],
                        "active_processes": [],
                    }
                proc_vram = int(proc.get("vramMb") or proc.get("vram_mb") or 0)
                active_gpu_workloads[key]["vram_mb"] += proc_vram
                active_gpu_workloads[key]["processes_count"] += 1
                if proc.get("pid"):
                    active_gpu_workloads[key]["pids"].append(proc.get("pid"))
                s_name = proc.get("name", "")
                if s_name and s_name not in active_gpu_workloads[key]["scripts"]:
                    active_gpu_workloads[key]["scripts"].append(s_name)
                active_gpu_workloads[key]["active_processes"].append({
                    "pid": proc.get("pid", 0),
                    "name": s_name,
                    "vramMb": proc_vram,
                    "cpuPercent": float(proc.get("cpuPercent") or proc.get("cpu_percent") or 0.0),
                })

        # Reservations đang hoạt động từ GpuScheduler
        active_reservations_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
        try:
            for r in self.gpu_scheduler.list_reservations():
                if r.get("state") in ("RESERVED", "STARTING", "RUNNING", "STOP_REQUESTED"):
                    r_gpu_id = canonicalize_gpu_id(r.get("gpu_id") or uuid_to_gpu_id.get(r.get("gpu_uuid", ""), ""))
                    owner = r.get("owner") or ""
                    if owner and r_gpu_id:
                        active_reservations_by_key[(owner, r_gpu_id)] = r
        except Exception:
            pass

        member_by_username = {m.get("username", ""): m for m in self.members if m.get("username")}
        allocated_keys = set()

        for m in self.members:
            username = m.get("username", "")
            if not username:
                continue
            full_name = m.get("full_name") or username
            role = m.get("role", "RESEARCHER")
            assigned_server = m.get("assigned_server", "workstation")
            perms = m.get("gpu_permissions") or []
            vram_quota = int(m.get("vram_quota_gb") or 12)
            mode = m.get("gpu_allocation_mode") or "SHARED"

            for raw_gpu_id in perms:
                gpu_id = canonicalize_gpu_id(raw_gpu_id)
                if not gpu_id or (username, gpu_id) in allocated_keys:
                    continue
                allocated_keys.add((username, gpu_id))
                gpu_meta = self._resolve_gpu_metadata(gpu_id, gpu_map.get(gpu_id), fallback_server=assigned_server)
                live_work = active_gpu_workloads.get((username, gpu_id))
                reservation = active_reservations_by_key.get((username, gpu_id))
                effective_mode = reservation.get("mode", mode) if reservation else mode

                allocations.append(self._build_gpu_allocation_entry(
                    member_id=m.get("id"),
                    username=username,
                    full_name=full_name,
                    role=role,
                    gpu_meta=gpu_meta,
                    allocation_mode=effective_mode,
                    vram_quota_gb=vram_quota,
                    live_work=live_work,
                    reservation=reservation,
                    fallback_script="Chưa có tiến trình",
                ))

        # Bổ sung tài khoản có reservation trên GpuScheduler nhưng chưa có trong gpu_permissions
        for (owner, res_gpu_id), res_obj in active_reservations_by_key.items():
            if (owner, res_gpu_id) in allocated_keys:
                continue
            allocated_keys.add((owner, res_gpu_id))
            m = member_by_username.get(owner, {})
            full_name = m.get("full_name") or _USER_DISPLAY_NAMES.get(owner, f"Nghiên cứu viên @{owner}")
            gpu_meta = self._resolve_gpu_metadata(res_gpu_id, gpu_map.get(res_gpu_id), fallback_server=m.get("assigned_server", "workstation"))
            live_work = active_gpu_workloads.get((owner, res_gpu_id))

            allocations.append(self._build_gpu_allocation_entry(
                member_id=m.get("id") or f"user-{owner}",
                username=owner,
                full_name=full_name,
                role=m.get("role", "RESEARCHER"),
                gpu_meta=gpu_meta,
                allocation_mode=res_obj.get("mode", "EXCLUSIVE"),
                vram_quota_gb=int(m.get("vram_quota_gb") or 24),
                live_work=live_work,
                reservation=res_obj,
                fallback_script="Đang chờ khởi chạy tác vụ",
            ))

        # Bổ sung các tài khoản hệ thống đang chạy GPU thực tế nhưng chưa có trong danh sách phân quyền tĩnh
        for (u, g_id), live_work in active_gpu_workloads.items():
            if not u or u.lower() in {"root", "systemd", "daemon", "aiot-ws1", "nobody", "unknown", "chưa xác định"}:
                continue
            if (u, g_id) in allocated_keys:
                continue
            allocated_keys.add((u, g_id))
            m = member_by_username.get(u, {})
            full_name = m.get("full_name") or _USER_DISPLAY_NAMES.get(u, f"Nghiên cứu viên @{u}")
            gpu_meta = self._resolve_gpu_metadata(g_id, gpu_map.get(g_id))
            cur_vram = int(live_work["vram_mb"])

            allocations.append(self._build_gpu_allocation_entry(
                member_id=m.get("id") or f"user-{u}",
                username=u,
                full_name=full_name,
                role=m.get("role", "RESEARCHER"),
                gpu_meta=gpu_meta,
                allocation_mode="SHARED",
                vram_quota_gb=int(m.get("vram_quota_gb") or (16 if cur_vram > 10000 else 12)),
                live_work=live_work,
                reservation=None,
                fallback_script="Python Workload",
            ))

        allocations.sort(key=lambda x: (not x["is_active"], x["status"] == "OFFLINE_HOST", x["gpu_label"], x["username"]))
        return allocations

    def get_gpu_allocations_by_device(self) -> List[Dict[str, Any]]:
        """
        Nhóm danh sách phân bổ quyền tài khoản theo từng thiết bị GPU vật lý:
        "GPU này đang có những tài khoản này, GPU kia đang có các tài khoản kia".
        """
        live_gpus = self.gpus
        flat_allocs = self.get_gpu_account_allocations()
        groups_by_id: Dict[str, Dict[str, Any]] = {}

        def _ensure_group(gpu_meta: Dict[str, Any]) -> Dict[str, Any]:
            gid = gpu_meta["gpu_id"]
            if gid not in groups_by_id:
                vram_total_mb = int(gpu_meta.get("vram_total_mb") or 24576)
                vram_used_mb = int(gpu_meta.get("vram_used_mb") or 0)
                groups_by_id[gid] = {
                    "gpu_id": gid,
                    "gpu_label": gpu_meta["gpu_label"],
                    "gpu_model": gpu_meta["gpu_model"],
                    "pci_bus_id": gpu_meta.get("pci_bus_id", ""),
                    "uuid": gpu_meta.get("uuid", ""),
                    "server_id": gpu_meta["server_id"],
                    "server_name": gpu_meta["server_name"],
                    "server_status": "online" if gpu_meta["is_online"] else "offline",
                    "is_online": bool(gpu_meta["is_online"]),
                    "allocation_mode": gpu_meta.get("allocation_mode", "SHARED"),
                    "vram_total_mb": vram_total_mb,
                    "vram_total_gb": round(vram_total_mb / 1024, 1),
                    "vram_used_mb": vram_used_mb,
                    "vram_used_gb": round(vram_used_mb / 1024, 1),
                    "vram_usage_percent": round((vram_used_mb / vram_total_mb) * 100) if vram_total_mb > 0 else 0,
                    "gpu_util_percent": int(gpu_meta.get("gpu_util_percent") or 0),
                    "temp_celsius": int(gpu_meta.get("temp_celsius") or 0),
                    "total_quota_allocated_gb": 0,
                    "accounts_count": 0,
                    "active_accounts_count": 0,
                    "accounts": [],
                }
            return groups_by_id[gid]

        for g in live_gpus:
            gid = canonicalize_gpu_id(g.get("id"))
            if gid:
                meta = self._resolve_gpu_metadata(gid, g)
                _ensure_group(meta)

        include_cluster_catalog = (
            not live_gpus
            or any((g.get("serverId") or g.get("server_id")) in ("workstation", "track") for g in live_gpus)
            or any(m.get("assigned_server") in ("workstation", "track") for m in self.members)
        )
        if include_cluster_catalog:
            for cat_gid in CLUSTER_GPU_CATALOG:
                if cat_gid not in groups_by_id:
                    _ensure_group(self._resolve_gpu_metadata(cat_gid, None))

        for alloc in flat_allocs:
            gid = canonicalize_gpu_id(alloc.get("gpu_id"))
            if not gid:
                continue
            if gid not in groups_by_id:
                meta = self._resolve_gpu_metadata(gid, None, fallback_server=alloc.get("server_id", "workstation"))
                _ensure_group(meta)
            groups_by_id[gid]["accounts"].append(alloc)

        status_rank = {"IN_USE": 0, "RESERVED": 1, "IDLE": 2, "OFFLINE_HOST": 3}
        result: List[Dict[str, Any]] = []
        for grp in groups_by_id.values():
            grp["accounts"].sort(key=lambda a: (status_rank.get(a.get("status", "IDLE"), 4), a.get("username", "")))
            grp["accounts_count"] = len(grp["accounts"])
            grp["active_accounts_count"] = sum(
                1 for a in grp["accounts"] if a.get("status") in ("IN_USE", "RESERVED")
            )
            grp["total_quota_allocated_gb"] = sum(int(a.get("vram_quota_gb") or 0) for a in grp["accounts"])
            result.append(grp)

        result.sort(key=lambda g: (not g["is_online"], g["accounts_count"] == 0, g["gpu_id"]))
        return result

    def list_processes(
        self,
        server_id: Optional[str] = None,
        classification: Optional[str] = None,
        username: Optional[str] = None,
        workspace_id: Optional[str] = None,
        gpu_id: Optional[str] = None,
        search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách tiến trình chi tiết từ Go Node Agent kết hợp danh bạ thành viên."""
        filter_type = "all"
        if classification:
            if classification in ("WORKLOAD", "USER_WORKLOAD", "workloads"):
                filter_type = "workloads"
            elif classification == "RESEARCH_JOB":
                filter_type = "research"
            elif classification == "GPU_PROCESS":
                filter_type = "gpu"
            elif classification == "SYSTEM_PROCESS":
                filter_type = "system"
            elif classification == "MEMBER_PROCESS":
                filter_type = "member"

        res = self.process_monitor.get_real_processes(filter_type=filter_type)
        obs = res.get("actual_observations", [])

        member_lookup = {m.get("username", ""): m for m in self.members}

        items = []
        for p in obs:
            u = p.get("username", "")
            m = member_lookup.get(u, {})
            p_copy = dict(p)
            p_copy["member_id"] = m.get("id") or (f"user-{u}" if u else None)
            p_copy["full_name"] = m.get("full_name") or (f"Người dùng @{u}" if u else "Hệ thống")
            p_copy["avatar"] = m.get("avatar") or f"https://api.dicebear.com/7.x/avataaars/svg?seed={u}"
            items.append(p_copy)

        if server_id and server_id != "all":
            items = [x for x in items if x.get("host_id") == server_id or x.get("server_id") == server_id]
        if username and username != "all":
            items = [x for x in items if x.get("username") == username]
        if workspace_id and workspace_id != "all":
            items = [x for x in items if x.get("workspace") == workspace_id or x.get("container_id") == workspace_id]
        if gpu_id and gpu_id != "all":
            items = [x for x in items if str(x.get("gpu_index")) == str(gpu_id) or x.get("gpu_id") == gpu_id]
        if search:
            q = search.lower()
            items = [
                x for x in items
                if q in str(x.get("pid", "")).lower()
                or q in str(x.get("name", "")).lower()
                or q in str(x.get("cmdline", "")).lower()
                or q in str(x.get("username", "")).lower()
                or q in str(x.get("workspace", "")).lower()
            ]

        return items

    def get_process_tree(self, ref: str) -> Dict[str, Any]:
        """Lấy cây tiến trình đầy đủ."""
        return self.process_monitor.get_process_tree(ref)

    def terminate_process(self, ref: str, timeout_seconds: int = 10, terminate_tree: bool = True, actor: str = "admin", is_admin: bool = True) -> Dict[str, Any]:
        """Dừng an toàn tiến trình (SIGTERM) với thời gian ân hạn."""
        if not is_admin:
            raise PermissionError("Chỉ quản trị viên (Admin) mới có quyền dừng tiến trình")
        try:
            res = self.process_monitor.terminate_process(ref, timeout_seconds=timeout_seconds, terminate_tree=terminate_tree)
            self._audit(actor, "PROCESS_SIGTERM", "PROCESS", ref, res.get("status", "SUCCESS"), f"Dừng an toàn {ref} (PID {res.get('pid')})")
            return res
        except Exception as e:
            self._audit(actor, "PROCESS_SIGTERM", "PROCESS", ref, "FAILED", str(e))
            raise

    def kill_process(self, ref: str, force: bool = True, kill_tree: bool = True, actor: str = "admin", is_admin: bool = True) -> Dict[str, Any]:
        """Buộc dừng cưỡng chế tiến trình (SIGKILL) khi có xác nhận riêng."""
        if not is_admin:
            raise PermissionError("Chỉ quản trị viên (Admin) mới có quyền buộc dừng tiến trình")
        try:
            res = self.process_monitor.kill_process(ref, force=force, kill_tree=kill_tree)
            self._audit(actor, "PROCESS_SIGKILL", "PROCESS", ref, res.get("status", "SUCCESS"), f"Buộc dừng cưỡng chế {ref} (PID {res.get('pid')})")
            return res
        except Exception as e:
            self._audit(actor, "PROCESS_SIGKILL", "PROCESS", ref, "FAILED", str(e))
            raise

    def get_next_available_ssh_port(self, server_id: str) -> int:
        """Find lowest free port in range 22000-29999 for given server, checking endpoints, members and live sockets"""
        import socket
        used_ports = {
            int(ep["listen_port"]) for ep in self.ssh_endpoints if ep.get("listen_port") and ep.get("server_id") == server_id
        }
        for m in self.members:
            if m.get("ssh_port"):
                try:
                    used_ports.add(int(m["ssh_port"]))
                except (ValueError, TypeError):
                    pass

        for port in range(22001, 29999):
            if port in used_ports:
                continue
            # Kiểm tra xem cổng có đang bị chiếm dụng trên máy chủ thật không (bind check)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('0.0.0.0', port))
            except OSError:
                used_ports.add(port)
                continue
            return port
        raise ValueError(f"Không còn cổng SSH khả dụng trên máy chủ {server_id}")

    @contextmanager
    def _username_creation_guard(self, username: str):
        uname = (username or "").strip().lower()
        with self._provision_lock:
            if uname in self._inflight_usernames:
                raise MemberConflictError(
                    "USERNAME_PROVISIONING_IN_PROGRESS",
                    f"Tên người dùng '{username}' đang được khởi tạo bởi một yêu cầu đồng thời khác.",
                    409,
                )
            self._inflight_usernames.add(uname)
        try:
            yield
        finally:
            with self._provision_lock:
                self._inflight_usernames.discard(uname)

    def _verify_username_available_for_creation(
        self,
        username: str,
        *,
        workspace_name: Optional[str] = None,
    ) -> None:
        """
        Verify strictly BEFORE creating any side effects that `username` may be provisioned cleanly:
        - Rejects any existing active/suspended/archived/incomplete member record (RAM or DB).
        - Allows reuse ONLY when every historical DB record for `username` has status='DELETED',
          provisioning_state='DELETED', non-empty deleted_at, a verified COMPLETED deletion operation
          (when non-dry-run deletion ops exist), and the DB schema supports deleted-member reuse.
        - Rejects residual uncleaned containers, active GPU reservations, or active unrevoked SSH keys.
        """
        uname = (username or "").strip().lower()
        incomplete_states = {"DELETION_IN_PROGRESS", "DELETION_PARTIAL_FAILURE"}

        for existing in self.members:
            if (existing.get("username") or "").strip().lower() == uname:
                st = str(existing.get("status") or "UNKNOWN")
                if st in incomplete_states:
                    raise MemberConflictError(
                        "MEMBER_DELETION_INCOMPLETE",
                        f"Tên người dùng '{username}' có quá trình xóa chưa hoàn tất ({st}); chưa thể tạo lại.",
                        409,
                        {"existing_member_id": existing.get("id"), "status": st},
                    )
                raise MemberConflictError(
                    "USERNAME_ALREADY_EXISTS",
                    f"Tên người dùng '{username}' đã tồn tại trong hệ thống",
                    409,
                    {"existing_member_id": existing.get("id"), "status": st},
                )
            if workspace_name and existing.get("workspace_name") == workspace_name:
                raise MemberConflictError(
                    "WORKSPACE_NAME_EXISTS",
                    f"Workspace '{workspace_name}' đã tồn tại",
                    409,
                )

        if hasattr(self, "db") and self.db is not None:
            try:
                with self.db.session_scope() as s:
                    rows = (
                        s.query(MemberRecord)
                        .filter(func.lower(MemberRecord.username) == uname)
                        .all()
                    )
                    has_deleted_rows = False
                    for row in rows:
                        is_deleted_tombstone = (
                            row.status == "DELETED"
                            and row.provisioning_state == "DELETED"
                            and bool((row.deleted_at or "").strip())
                        )
                        if not is_deleted_tombstone:
                            if row.status in incomplete_states:
                                raise MemberConflictError(
                                    "MEMBER_DELETION_INCOMPLETE",
                                    f"Tên người dùng '{username}' có quá trình xóa chưa hoàn tất trong DB ({row.status}, ID: {row.id}).",
                                    409,
                                    {"existing_member_id": row.id, "status": row.status},
                                )
                            raise MemberConflictError(
                                "USERNAME_ALREADY_EXISTS",
                                f"Tên người dùng '{username}' đã tồn tại trong cơ sở dữ liệu (ID: {row.id}, trạng thái: {row.status}).",
                                409,
                                {"existing_member_id": row.id, "status": row.status},
                            )

                        has_deleted_rows = True
                        ops_for_member = (
                            s.query(DeletionOperationRecord)
                            .filter(
                                DeletionOperationRecord.member_id == row.id,
                                DeletionOperationRecord.dry_run.is_(False),
                            )
                            .order_by(DeletionOperationRecord.created_at.asc())
                            .all()
                        )
                        if ops_for_member:
                            latest_op = ops_for_member[-1]
                            if latest_op.status != "COMPLETED" or latest_op.completed_at is None:
                                raise MemberConflictError(
                                    "MEMBER_DELETION_UNVERIFIED",
                                    f"Xóa tài khoản cũ '{row.id}' (@{username}) chưa hoàn tất xác minh ({latest_op.status}); không thể tạo lại.",
                                    409,
                                    {"existing_member_id": row.id, "operation_id": latest_op.op_id},
                                )
                            steps = latest_op.steps_json if isinstance(latest_op.steps_json, dict) else {}
                            bad_steps = [
                                k for k, v in steps.items()
                                if isinstance(v, dict)
                                and v.get("status") in {"FAILED", "PARTIAL_FAILURE", "BLOCKED_BY_INFRASTRUCTURE"}
                            ]
                            if bad_steps:
                                raise MemberConflictError(
                                    "MEMBER_DELETION_UNVERIFIED",
                                    f"Thao tác xóa tài khoản cũ '{row.id}' còn bước lỗi ({', '.join(bad_steps)}); không thể tạo lại.",
                                    409,
                                    {"existing_member_id": row.id, "failed_steps": bad_steps},
                                )

                    # Check DeletionOperationRecord by username for any unfinished deletion even if MemberRecord is missing/mid-flight
                    all_ops = (
                        s.query(DeletionOperationRecord)
                        .filter(
                            func.lower(DeletionOperationRecord.username) == uname,
                            DeletionOperationRecord.dry_run.is_(False),
                        )
                        .order_by(DeletionOperationRecord.created_at.asc())
                        .all()
                    )
                    latest_by_member: Dict[str, DeletionOperationRecord] = {}
                    for op in all_ops:
                        latest_by_member[op.member_id] = op
                    for mid, op in latest_by_member.items():
                        if op.status != "COMPLETED" or op.completed_at is None:
                            raise MemberConflictError(
                                "MEMBER_DELETION_INCOMPLETE",
                                f"Phát hiện tác vụ xóa chưa hoàn tất cho @{username} (ID: {mid}, trạng thái: {op.status}).",
                                409,
                                {"existing_member_id": mid, "operation_id": op.op_id},
                            )

                    if workspace_name:
                        ws_conflict = (
                            s.query(MemberRecord)
                            .filter(
                                MemberRecord.workspace_name == workspace_name,
                                MemberRecord.status != "DELETED",
                            )
                            .first()
                        )
                        if ws_conflict is not None:
                            raise MemberConflictError(
                                "WORKSPACE_NAME_EXISTS",
                                f"Workspace '{workspace_name}' đã tồn tại",
                                409,
                            )

                    if has_deleted_rows and getattr(self.db, "has_legacy_username_unique_index", False):
                        raise MemberPersistenceError(
                            "SCHEMA_MIGRATION_REQUIRED",
                            f"Cơ sở dữ liệu vẫn dùng ràng buộc UNIQUE cũ trên members.username cho bản ghi đã xóa của '{username}'; cần chạy migration 0002 trước khi tạo lại.",
                            503,
                        )
            except (MemberConflictError, MemberPersistenceError):
                raise
            except Exception as db_exc:
                raise MemberPersistenceError(
                    "DB_PRECHECK_FAILED",
                    "Không thể truy vấn cơ sở dữ liệu để xác minh tính khả dụng của username.",
                    503,
                ) from db_exc

        active_gpu = [
            r for r in self.gpu_scheduler.list_reservations(owner=uname)
            if r.get("state") in GPU_ACTIVE_STATES
        ]
        if active_gpu:
            raise MemberConflictError(
                "ACTIVE_GPU_RESERVATION_EXISTS",
                f"Thành viên @{username} cũ vẫn còn giữ chỗ GPU đang hoạt động ({len(active_gpu)}).",
                409,
            )

        active_containers = [
            w for w in self.workspace_controller.list_workspaces(owner=uname)
            if w.get("owner") == uname and w.get("runtime_type") == "DOCKER" and w.get("container_id")
        ]
        if active_containers:
            raise MemberConflictError(
                "RESIDUAL_WORKSPACE_CONTAINER_EXISTS",
                f"Vẫn còn workspace container chưa thu hồi gắn với @{username}; hãy dọn dẹp an toàn trước khi tạo lại.",
                409,
            )

        active_keys = [
            k for k in self.ssh_key_registry.list_keys(username=uname)
            if k.get("desired_state") == "ACTIVE"
        ]
        if active_keys:
            raise MemberConflictError(
                "RESIDUAL_SSH_KEY_EXISTS",
                f"Vẫn còn khóa SSH đang ACTIVE chưa thu hồi cho @{username}; chưa thể tạo lại tài khoản mới.",
                409,
            )

    def _compensate_failed_provisioning(
        self,
        fx: Dict[str, Any],
        pre_members: List[Dict[str, Any]],
        pre_endpoints: List[Dict[str, Any]],
        pre_audits: List[Dict[str, Any]],
        *,
        pre_account: Optional[Dict[str, Any]] = None,
        username: Optional[str] = None,
        member_id: Optional[str] = None,
    ) -> None:
        """
        Compensate partial provisioning side effects without EVER deleting user files on disk.
        """
        self.members[:] = pre_members
        self.ssh_endpoints[:] = pre_endpoints
        self.audit_logs[:] = pre_audits

        if member_id:
            try:
                self.ssh_access_manager.vault.invalidate_member(member_id)
            except Exception:
                pass

        resv = fx.get("storage_reserved")
        if isinstance(resv, tuple) and len(resv) == 2:
            pool_id, quota_gib = resv
            cur = storage_service._reservations.get(pool_id, 0)
            new_val = max(0, int(cur) - int(quota_gib))
            if new_val > 0:
                storage_service._reservations[pool_id] = new_val
            else:
                storage_service._reservations.pop(pool_id, None)

        created_key_id = fx.get("created_key_id")
        if created_key_id:
            try:
                self.ssh_key_registry.mark_provisioning_rolled_back(created_key_id)
            except Exception:
                pass

        created_ws_id = fx.get("created_ws_id")
        has_container = bool(fx.get("has_container"))
        if created_ws_id and not has_container:
            try:
                self.workspace_controller.remove_workspace_by_id(created_ws_id, require_no_container=True)
            except Exception:
                pass
        elif created_ws_id and has_container:
            # Never lose track of a real Docker container if DB commit failed afterward
            try:
                rec_path = os.path.join(self.data_dir, "provisioning_recovery.json")
                existing = safe_load_json(rec_path, list)
                if not isinstance(existing, list):
                    existing = []
                existing.append({
                    "id": f"provrec-{uuid.uuid4().hex[:12]}",
                    "member_id": member_id,
                    "username": username,
                    "workspace_id": created_ws_id,
                    "container_id": fx.get("container_id"),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "reason": "DB/persistence failure after container creation; preserved for reconciliation.",
                })
                atomic_write_json(rec_path, existing, mode=0o600)
            except Exception:
                pass

        if pre_account is not None and username:
            try:
                self.ssh_access_manager.restore_account_state(
                    username,
                    pre_account,
                    expected_new_member_id=member_id,
                )
            except Exception:
                pass

    def provision_member(
        self,
        payload: Dict[str, Any],
        *,
        _defer_persist: bool = False,
        _skip_username_lock: bool = False,
        _side_effects: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record an access request; external workspace and identity services must verify activation."""
        if not _defer_persist and (payload.get("provision_ssh_access") or payload.get("provisionSshAccess")):
            return self.provision_member_ssh_access(payload)

        username = payload.get("username", "")
        validate_linux_username(username)

        email = (payload.get("email") or "").strip()
        if email and "@" not in email and re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", email):
            email = f"{email}@lab.ai.vn"
        elif not email:
            email = f"{username}@lab.ai.vn"
        if not isinstance(email, str) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Email không hợp lệ")
        full_name = payload.get("full_name") or payload.get("fullName") or ""
        if not isinstance(full_name, str) or not full_name.strip() or len(full_name.strip()) > 120:
            raise ValueError("Tên hiển thị phải có từ 1 đến 120 ký tự")
        role = payload.get("role", "RESEARCHER")
        if role not in {"RESEARCHER", "STUDENT", "ADMIN"}:
            raise ValueError("Role chỉ được là RESEARCHER, STUDENT hoặc ADMIN")

        server_id = payload.get("assigned_server") or payload.get("assignedServer") or settings.LOCAL_HOST_ID
        server = next((item for item in self.servers if item["id"] == server_id), None)
        if not server:
            raise ValueError(f"Không tìm thấy máy chủ '{server_id}'")

        pool_id = payload.get("storage_pool_id") or payload.get("storagePoolId") or payload.get("storage_id") or payload.get("storageId")
        if not pool_id:
            server_pools = [item for item in self.storage_pools if item.get("server_id") == server_id]
            has_custom = bool(storage_service._custom_pools.get(server_id))
            inv_pools = storage_service.get_storage_pools(server_id) if (server_pools or has_custom) else []
            pool = next((item for item in inv_pools if item.get("eligible") and item.get("is_default")), None)
            pool = pool or next((item for item in inv_pools if item.get("eligible")), None)
            if not pool and server_pools:
                pool = next((item for item in server_pools if item.get("is_default") and item.get("id") != "pool-root"), None)
                pool = pool or next((item for item in server_pools if item.get("id") != "pool-root"), None)
        else:
            pool = next((item for item in self.storage_pools if (item.get("id") == pool_id or item.get("storage_id") == pool_id)), None)
            if not pool:
                pool = storage_service.get_storage_pool_by_id(server_id, pool_id)

        if pool and pool.get("server_id", pool.get("host_id")) != server_id:
            raise ValueError(f"Không tìm thấy storage pool trên máy chủ '{server_id}'")
        if pool_id and not pool:
            raise ValueError(f"Không tìm thấy storage pool trên máy chủ '{server_id}'")

        storage_quota_gb = payload.get("storage_quota_gb", payload.get("storageQuotaGb", 250))
        if not isinstance(storage_quota_gb, int) or storage_quota_gb <= 0:
            raise ValueError("Dung lượng yêu cầu phải là số nguyên dương")

        free_limit = pool.get("free_gb", pool.get("free_gib", pool.get("allocatable_gib", 0.0))) if pool else 0.0
        if pool and storage_quota_gb > free_limit:
            raise ValueError("Dung lượng yêu cầu vượt quá dung lượng khả dụng")

        cpu_limit = payload.get("cpu_limit_cores", payload.get("cpuLimitCores", 8))
        ram_limit = payload.get("ram_limit_gb", payload.get("ramLimitGb", 32))
        vram_quota = payload.get("vram_quota_gb", payload.get("vramQuotaGb", 12))
        gpu_permissions = payload.get("gpu_permissions", payload.get("gpuPermissions", []))
        if not isinstance(cpu_limit, int) or not 1 <= cpu_limit <= 512:
            raise ValueError("CPU quota phải từ 1 đến 512 cores")
        if not isinstance(ram_limit, int) or not 1 <= ram_limit <= 4096:
            raise ValueError("RAM quota phải từ 1 đến 4096 GB")
        if vram_quota is not None and (not isinstance(vram_quota, int) or not 0 <= vram_quota <= 256):
            raise ValueError("VRAM quota phải từ 0 đến 256 GB")
        if not isinstance(gpu_permissions, list) or any(not isinstance(gpu, str) or len(gpu) > 64 for gpu in gpu_permissions):
            raise ValueError("Danh sách GPU không hợp lệ")

        gpu_mode = payload.get("gpu_allocation_mode", payload.get("gpuAllocationMode", "EXCLUSIVE")) if gpu_permissions else "NONE"
        mount_path = payload.get("mount_path", payload.get("mountPath", "/workspace"))
        validate_mount_path(mount_path, username)

        workspace_name = payload.get("workspace_name") or payload.get("workspaceName") or f"ws-{username}"
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", workspace_name):
            raise ValueError("Workspace name không hợp lệ")

        raw_ssh_key = (payload.get("ssh_public_key") or payload.get("sshPublicKey") or "").strip()
        ssh_key_label = (payload.get("ssh_key_label") or payload.get("sshKeyLabel") or "Khóa SSH mặc định").strip()
        actor_id = payload.get("_actor_id", "labops-api")
        if raw_ssh_key:
            compute_ssh_fingerprint(raw_ssh_key)
            WorkspaceController.validate_security_constraints(
                owner=username,
                ssh_public_key=raw_ssh_key,
                cpu_cores=min(cpu_limit, 48) if cpu_limit else 8,
                ram_mb=max(1024, min(ram_limit * 1024, 131072)),
            )

        guard_cm = contextmanager(lambda: (yield))() if _skip_username_lock else self._username_creation_guard(username)
        with guard_cm:
            if not (_defer_persist and _skip_username_lock):
                self._verify_username_available_for_creation(username, workspace_name=workspace_name)

            if gpu_permissions and gpu_mode == "EXCLUSIVE":
                for existing in self.members:
                    if existing.get("assigned_server") == server_id:
                        existing_gpus = existing.get("gpu_permissions", [])
                        existing_mode = existing.get("gpu_allocation_mode", "SHARED")
                        if existing_mode == "EXCLUSIVE" and any(g in existing_gpus for g in gpu_permissions):
                            conflict_gpus = [g for g in gpu_permissions if g in existing_gpus]
                            raise ValueError(f"Xung đột cấp phát GPU: GPU '{conflict_gpus}' đã được cấp phát độc quyền (EXCLUSIVE) cho thành viên @{existing.get('username')}")

            pre_members = list(self.members)
            pre_endpoints = list(self.ssh_endpoints)
            pre_audits = list(self.audit_logs)
            member_id = f"mem-{uuid.uuid4().hex[:12]}"
            fx: Dict[str, Any] = {
                "member_id": member_id,
                "storage_reserved": None,
                "created_key_id": None,
                "created_ws_id": None,
                "has_container": False,
            }

            try:
                storage_result: Dict[str, Any] = {}
                if pool:
                    s_id = pool.get("storage_id") or pool.get("id")
                    if not storage_service.get_storage_pool_by_id(server_id, s_id):
                        pool_mnt = pool.get("mount_point", "/mnt/HDD3")
                        pool_fs = pool.get("driver", "ext4")
                        q_supp = bool(pool.get("quota_supported", False))
                        q_enf = bool(pool.get("quota_enforced", False))
                        storage_service.register_custom_pool(server_id, {
                            "host_id": server_id,
                            "storage_id": s_id,
                            "device": pool.get("disk_identifier", "/dev/sdc"),
                            "filesystem_uuid": hashlib.sha256(f"{server_id}:{s_id}".encode()).hexdigest()[:36],
                            "mount_point": pool_mnt,
                            "filesystem": pool_fs,
                            "capacity_gib": float(pool.get("capacity_gb", pool.get("capacity_gib", 1000.0))),
                            "used_gib": float(pool.get("used_gb", pool.get("used_gib", 100.0))),
                            "free_gib": float(pool.get("free_gb", pool.get("free_gib", 900.0))),
                            "allocatable_gib": float(pool.get("free_gb", pool.get("allocatable_gib", 900.0))),
                            "reserved_gib": 100.0,
                            "quota_backend": "PROJECT_QUOTA" if q_supp else "NONE",
                            "quota_supported": q_supp,
                            "quota_enforced": q_enf,
                            "read_only": False,
                            "mounted": True,
                            "eligible": pool_mnt != "/",
                            "ineligibility_reason": "Root filesystem bị cấm" if pool_mnt == "/" else None,
                            "last_verified_at": datetime.now(timezone.utc).isoformat(),
                            "verification_status": "VERIFIED" if q_enf else "QUOTA_NOT_ENFORCEABLE",
                        })
                    storage_result = storage_service.provision_member_storage(
                        host_id=server_id,
                        storage_id=s_id,
                        username=username,
                        quota_gib=storage_quota_gb,
                    )
                    fx["storage_reserved"] = (s_id, storage_quota_gb)

                registered_key = None
                allocated_port = None
                ssh_ip = None
                workspace_status = "NOT_VERIFIED"

                if raw_ssh_key:
                    registered_key = self.ssh_key_registry.register_key(
                        username=username,
                        label=ssh_key_label,
                        public_key=raw_ssh_key,
                        actor=actor_id,
                        member_id=member_id,
                    )
                    fx["created_key_id"] = registered_key["id"]
                    allocated_port = self.get_next_available_ssh_port(server_id)
                    ssh_ip = (
                        server.get("vpn_ip")
                        or server.get("ip_address")
                        or ("192.168.167.251" if server_id == "workstation" else "192.168.167.250")
                    )
                    ws_record = self.workspace_controller.request_workspace(
                        owner=username,
                        host_id=server_id,
                        ssh_public_key=raw_ssh_key,
                        cpu_cores=min(cpu_limit, 48),
                        ram_mb=max(1024, min(ram_limit * 1024, 131072)),
                        storage_quota_gb=min(storage_quota_gb, 2000),
                        storage_path=storage_result.get("member_storage_directory"),
                        member_storage_dir=storage_result.get("member_storage_directory"),
                        member_id=member_id,
                    )
                    fx["created_ws_id"] = ws_record["id"]
                    workspace_status = ws_record.get("verification", "PENDING_VERIFICATION")
            except Exception:
                self._compensate_failed_provisioning(
                    fx, pre_members, pre_endpoints, pre_audits, username=username, member_id=member_id
                )
                raise

            if allocated_port is not None:
                self.ssh_endpoints.append({
                    "id": f"ep-{uuid.uuid4().hex[:8]}",
                    "member_id": member_id,
                    "server_id": server_id,
                    "listen_address": ssh_ip,
                    "listen_port": allocated_port,
                    "container_port": 22,
                    "protocol": "tcp",
                    "status": "RESERVED",
                })

            now = datetime.now().isoformat(timespec="seconds")
            member = {
                "id": member_id,
                "full_name": full_name.strip(),
                "username": username,
                "email": email,
                "role": role,
                "research_group": payload.get("research_group") or payload.get("researchGroup") or "AI Research Lab",
                "assigned_server": server_id,
                "workspace_name": workspace_name,
                "status": "PENDING_PROVISIONING",
                "provisioning_state": "SSH_KEY_ENROLLED" if registered_key else "PENDING",
                "workspace_status": workspace_status,
                "teleport_status": "NOT_CONFIGURED",
                "storage_pool_id": pool.get("storage_id") or pool.get("id") if pool else None,
                "storage_pool_label": pool.get("label", pool.get("mount_point", "NOT_VERIFIED")) if pool else "NOT_VERIFIED",
                "storage_id": pool.get("storage_id") or pool.get("id") if pool else None,
                "host_storage_root": storage_result.get("host_storage_root"),
                "member_storage_directory": storage_result.get("member_storage_directory"),
                "workspace_dir": storage_result.get("member_storage_directory"),
                "container_mount_path": "/workspace",
                "storage_quota_backend": storage_result.get("quota_backend", "PROJECT_QUOTA"),
                "storage_quota_enforcement": storage_result.get("verification_status", "VERIFIED"),
                "mount_path": mount_path,
                "storage_quota_gb": storage_quota_gb,
                "storage_used_gb": 0.0,
                "backup_policy": payload.get("backup_policy", payload.get("backupPolicy", "DAILY_SNAPSHOT")),
                "shared_datasets": payload.get("shared_datasets", payload.get("sharedDatasets", ["/shared/datasets"])),
                "ssh_status": "READY_PENDING_RUNTIME" if registered_key else "OFFLINE",
                "ssh_sessions_count": 0,
                "ssh_ip": ssh_ip,
                "ssh_port": allocated_port,
                "ssh_key_status": "ACTIVE" if registered_key else "NOT_CONFIGURED",
                "ssh_fingerprint": registered_key["fingerprint"] if registered_key else None,
                "ssh_public_key_preview": f"{raw_ssh_key[:42]}..." if registered_key else "Chưa đăng ký khóa OpenSSH",
                "ssh_public_key": raw_ssh_key if registered_key else None,
                "ssh_auth_mode": "OPENSSH_PUBLIC_KEY" if registered_key else "TELEPORT_CERTIFICATE_PENDING",
                "password_status": "NOT_APPLICABLE",
                "failed_login_attempts": 0,
                "last_successful_login": None,
                "last_login_source_ip": None,
                "sudo_privilege": "DENIED",
                "security_isolation_level": "NOT_VERIFIED",
                "privilege_validation": {
                    "unprivileged_container": False,
                    "sudo_test_failed_as_expected": False,
                    "no_sudo_group_membership": False,
                    "no_docker_socket_access": False,
                    "no_incus_socket_access": False,
                    "verification_timestamp": None,
                    "status": "NOT_VERIFIED",
                },
                "cpu_limit_cores": cpu_limit,
                "ram_limit_gb": ram_limit,
                "cpu_policy": payload.get("cpu_policy") or payload.get("cpuPolicy") or "SHARED_WEIGHTED",
                "cpu_weight": int(payload.get("cpu_weight") or payload.get("cpuWeight") or 100),
                "memory_policy": payload.get("memory_policy") or payload.get("memoryPolicy") or "SHARED_HOST_PROTECTED",
                "vram_enforcement_status": "POLICY_ONLY",
                "gpu_permissions": gpu_permissions,
                "vram_policy": payload.get("vram_policy", payload.get("vramPolicy", "Quota 12GB (Shared)")),
                "vram_quota_gb": vram_quota,
                "gpu_allocation_mode": payload.get("gpu_allocation_mode", payload.get("gpuAllocationMode", "SHARED")),
                "max_runtime_hours": 48,
                "active_jobs_count": 0,
                "total_gpu_hours": 0.0,
                "created_at": datetime.now().strftime("%Y-%m-%d"),
                "expires_at": payload.get("expires_at", payload.get("expiresAt")),
                "wireguard": {"status": "NOT_CONFIGURED", "devices": []},
            }
            self.members.insert(0, member)
            self.audit_logs.insert(0, {
                "id": f"aud-{uuid.uuid4().hex[:12]}",
                "actor": actor_id,
                "action": "ACCESS_REQUEST_CREATED",
                "target_type": "MEMBER",
                "target_id": username,
                "timestamp": now,
                "result": "SSH_ENROLLED" if registered_key else "PENDING",
                "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
                "details": (
                    f"Access request recorded for workspace {workspace_name} on {server_id} (member_id: {member_id})"
                    + (f" with OpenSSH port {allocated_port} ({registered_key['fingerprint']})." if registered_key else "; provisioning is not verified.")
                ),
            })

            if _defer_persist:
                if _side_effects is not None:
                    _side_effects.update(fx)
                return member

            try:
                self._save_persistent_data()
            except MemberPersistenceError as mp_err:
                if mp_err.code == "JSON_PERSISTENCE_FAILED":
                    # DB already committed this member_id; keep it active in revocation tracking & prevent duplicate retries
                    get_revocation_store().register_active_member(username, member_id)
                    raise
                self._compensate_failed_provisioning(
                    fx, pre_members, pre_endpoints, pre_audits, username=username, member_id=member_id
                )
                raise
            except Exception:
                self._compensate_failed_provisioning(
                    fx, pre_members, pre_endpoints, pre_audits, username=username, member_id=member_id
                )
                raise

            get_revocation_store().register_active_member(username, member_id)
            return member

    def enroll_member_ssh_key(self, username: str, label: str, public_key: str, actor: str) -> Dict[str, Any]:
        """Đăng ký khóa OpenSSH cho thành viên và tự động cấp cổng/workspace SSH nếu thành viên đã tồn tại."""
        key_rec = self.ssh_key_registry.register_key(
            username=username,
            label=label,
            public_key=public_key,
            actor=actor,
        )
        member = next((m for m in self.members if m.get("username") == username or m.get("id") == username), None)
        if member:
            server_id = member.get("assigned_server") or settings.LOCAL_HOST_ID
            server = next((s for s in self.servers if s["id"] == server_id), None) or {}
            if not member.get("ssh_port"):
                port = self.get_next_available_ssh_port(server_id)
                ssh_ip = (
                    server.get("vpn_ip")
                    or server.get("ip_address")
                    or ("192.168.167.251" if server_id == "workstation" else "192.168.167.250")
                )
                member["ssh_port"] = port
                member["ssh_ip"] = ssh_ip
                self.ssh_endpoints.append({
                    "id": f"ep-{uuid.uuid4().hex[:8]}",
                    "member_id": member["id"],
                    "server_id": server_id,
                    "listen_address": ssh_ip,
                    "listen_port": port,
                    "container_port": 22,
                    "protocol": "tcp",
                    "status": "RESERVED",
                })
            member["ssh_key_status"] = "ACTIVE"
            member["ssh_fingerprint"] = key_rec["fingerprint"]
            member["ssh_public_key"] = public_key.strip()
            member["ssh_public_key_preview"] = f"{public_key.strip()[:42]}..."
            member["ssh_auth_mode"] = "OPENSSH_PUBLIC_KEY"
            member["ssh_status"] = "READY_PENDING_RUNTIME"
            member["provisioning_state"] = "SSH_KEY_ENROLLED"
            ws_list = self.workspace_controller.list_workspaces(owner=member["username"])
            if not ws_list:
                ws_rec = self.workspace_controller.request_workspace(
                    owner=member["username"],
                    host_id=server_id,
                    ssh_public_key=public_key.strip(),
                    cpu_cores=min(int(member.get("cpu_limit_cores") or 8), 64),
                    ram_mb=min(int(member.get("ram_limit_gb") or 16) * 1024, 262144),
                    storage_quota_gb=min(int(member.get("storage_quota_gb") or 100), 2000),
                )
                member["workspace_status"] = ws_rec.get("verification", "PENDING_VERIFICATION")
            self.audit_logs.insert(0, {
                "id": f"aud-{uuid.uuid4().hex[:12]}",
                "actor": actor,
                "action": "SSH_PUBLIC_KEY_ENROLLED",
                "target_type": "MEMBER",
                "target_id": member["username"],
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "result": "ACTIVE",
                "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
                "details": f"Đã đăng ký khóa OpenSSH ({key_rec['fingerprint']}) cho @{member['username']} tại {member.get('ssh_ip')}:{member.get('ssh_port')}",
            })
            self._save_persistent_data()
            key_rec["member"] = member
            key_rec["ssh_endpoint"] = f"ssh -p {member.get('ssh_port')} {member['username']}@{member.get('ssh_ip')}"
        return key_rec

    def register_wireguard_device(self, member_id: str, public_key: str, device_name: str, actor: str = "labops-api") -> Dict[str, Any]:
        """Tạo thiết bị WireGuard mới cho thành viên qua wg-easy (Single Source of Truth)."""
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id}")
        if member.get("workspace_status") != "VERIFIED":
            raise RuntimeError("Workspace provisioning must be verified before device enrollment")
        if self._network_policy_status() != "VERIFIED":
            raise RuntimeError("VPN-to-Teleport network policy must be verified before device enrollment")
        return self.add_member_device(member_id, device_name, actor=actor)

    def revoke_wireguard_device(self, member_id: str, device_id: str, actor: str = "labops-api") -> Dict[str, Any]:
        """Thu hồi thiết bị WireGuard của thành viên qua wg-easy."""
        return self.revoke_member_device_v2(member_id, device_id, actor=actor)

    def _revoke_member_wireguard(self, member: Dict[str, Any]) -> Dict[str, Any]:
        profile = member.get("wireguard") or {}
        devices = profile.get("devices", [])
        for device in devices:
            if device.get("status") == "REVOKED":
                continue
            if self._is_pending_placeholder_device(device):
                device["status"] = "REVOKED"
                device["enabled"] = False
                continue
            try:
                ext_id = str(device.get("external_id") or device.get("device_id") or device.get("id"))
                self.vpn_manager.revoke_device(ext_id, device.get("provider", "wg-easy"))
                device["status"] = "REVOKED"
                device["enabled"] = False
            except Exception as exc:
                logger.warning(f"Lỗi thu hồi thiết bị {device} trên wg-easy: {exc}")
        profile["status"] = "REVOKED" if devices else "NOT_CONFIGURED"
        member["wireguard"] = profile
        return profile

    def suspend_member(self, member_id: str) -> Dict[str, Any]:
        raise RuntimeError("Use revoke_member_access so WireGuard and Teleport are processed together")

    def restore_member(self, member_id: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        member["status"] = "PENDING_PROVISIONING"
        member["provisioning_state"] = "PENDING"
        member["teleport_status"] = "NOT_CONFIGURED"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}", "actor": "labops-api",
            "action": "ACCESS_RESTORE_REQUESTED", "target_type": "MEMBER", "target_id": member["username"],
            "timestamp": datetime.now().isoformat(timespec="seconds"), "result": "PENDING",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": "Access remains pending until workspace, Teleport identity, VPN, and SSH are verified.",
        })
        self._save_persistent_data()
        return member

    def rotate_ssh_key(self, member_id: str, public_key: str) -> Dict[str, Any]:
        raise RuntimeError("Legacy SSH key management is disabled; use Teleport-issued certificates")

    def revoke_ssh_key(self, member_id: str) -> Dict[str, Any]:
        raise RuntimeError("Legacy SSH key management is disabled; revoke the Teleport identity instead")

    def update_storage_quota(self, member_id: str, new_quota_gb: int) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        pool = next((p for p in self.storage_pools if p["id"] == member["storage_pool_id"]), None)
        if pool:
            diff = new_quota_gb - member["storage_quota_gb"]
            if diff > pool["free_gb"]:
                raise ValueError(f"Pool chỉ còn trống {pool['free_gb']}GB, không đủ cấp phát thêm {diff}GB")
            pool["free_gb"] = max(0.0, pool["free_gb"] - diff)
            pool["used_gb"] += diff
        old_quota = member["storage_quota_gb"]
        member["storage_quota_gb"] = new_quota_gb
        for sa in self.storage_allocations:
            if sa["member_id"] == member_id:
                sa["quota_gb"] = new_quota_gb
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "STORAGE_QUOTA_UPDATED",
            "target_type": "STORAGE",
            "target_id": member["username"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đổi quota ZFS cho @{member['username']} từ {old_quota}GB sang {new_quota_gb}GB"
        })
        self._save_persistent_data()
        return member

    def update_ssh_port(self, member_id: str, new_port: int) -> Dict[str, Any]:
        raise RuntimeError("Per-user SSH port allocation is disabled")

    def archive_workspace(self, member_id: str) -> Dict[str, Any]:
        raise RuntimeError("Workspace archival is unavailable until the workspace controller is integrated")

    def suspend_member_lifecycle(self, member_id_or_username: str, actor: str = "admin", reason: str = "") -> Dict[str, Any]:
        return self.lifecycle.suspend_member(member_id_or_username, actor=actor, reason=reason)

    def archive_member_lifecycle(self, member_id_or_username: str, actor: str = "admin", *, retain_backup: bool = True) -> Dict[str, Any]:
        return self.lifecycle.archive_member(member_id_or_username, actor=actor, retain_backup=retain_backup)

    def preview_member_deletion(self, member_id_or_username: str, actor: str = "admin", *, dry_run: bool = True) -> Dict[str, Any]:
        return self.lifecycle.preview_deletion(member_id_or_username, actor=actor, dry_run=dry_run)

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
        return self.lifecycle.delete_member_permanently(
            member_id_or_username,
            actor=actor,
            remove_files=remove_files,
            confirm_username=confirm_username,
            step_up_verified=step_up_verified,
            enforce_lifecycle=enforce_lifecycle,
        )

    def get_wireguard_config(self, user_id: str) -> str:
        raise RuntimeError("Client WireGuard private keys and profiles are not generated or stored by LabOps")

    def regenerate_wireguard_profile(self, user_id: str, public_key: Optional[str] = None) -> Dict[str, Any]:
        raise RuntimeError("Use per-device enrollment with a client-generated public key")

    def revoke_wireguard(self, user_id: str, actor: str = "labops-api") -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == user_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {user_id}")
        profile = self._revoke_member_wireguard(member)
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}", "actor": actor,
            "action": "WIREGUARD_REVOKED", "target_type": "MEMBER", "target_id": member["username"],
            "timestamp": datetime.now().isoformat(timespec="seconds"), "result": profile["status"],
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": "WireGuard peer removal was checked against the running interface.",
        })
        self._save_persistent_data()
        return profile

    def restore_wireguard(self, user_id: str) -> Dict[str, Any]:
        raise RuntimeError("Revoked WireGuard device keys cannot be restored; enroll a new device key")

    def refresh_wireguard_devices(self, member_id: str) -> List[Dict[str, Any]]:
        member = next((item for item in self.members if item["id"] == member_id), None)
        if not member:
            raise ValueError("Member not found")
        devices = self.get_member_devices_synced(member_id)
        profile = member.setdefault("wireguard", {})
        profile["devices"] = devices
        statuses = {device.get("status") for device in devices}
        profile["status"] = (
            "REVOKED" if statuses and statuses == {"REVOKED"}
            else "ACTIVE" if "ACTIVE" in statuses
            else "PARTIAL_FAILURE" if "PARTIAL_FAILURE" in statuses
            else "PENDING_HANDSHAKE" if devices
            else "NOT_CONFIGURED"
        )
        self._save_persistent_data()
        return devices

    def activate_teleport_user(self, member_id: str, actor: str = "labops-api") -> Dict[str, Any]:
        member = next((item for item in self.members if item["id"] == member_id), None)
        if not member:
            raise ValueError("Member not found")
        if member.get("workspace_status") != "VERIFIED":
            raise RuntimeError("Workspace is not verified; Teleport invitation remains pending")
        if self._network_policy_status() != "VERIFIED":
            raise RuntimeError("VPN-to-Teleport network policy is not verified")
        devices = self.refresh_wireguard_devices(member_id)
        if not any(device.get("status") == "ACTIVE" for device in devices):
            raise RuntimeError("No WireGuard device has a recent verified handshake")
        invitation = self.teleport_adapter.provision_user(
            member["username"], member["email"], member["workspace_name"], member["role"]
        )
        member["teleport_status"] = invitation["status"]
        member["provisioning_state"] = "INVITED" if invitation["status"] == "INVITED" else "PENDING_VERIFICATION"
        member["status"] = "PENDING_PROVISIONING"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}", "actor": actor,
            "action": "TELEPORT_USER_INVITED", "target_type": "MEMBER", "target_id": member["username"],
            "timestamp": datetime.now().isoformat(timespec="seconds"), "result": invitation["status"],
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": "Teleport invitation was delivered through configured email; WebAuthn and SSH verification remain pending.",
        })
        self._save_persistent_data()
        return {"status": invitation["status"], "member": member}

    async def get_access_health(self) -> Dict[str, Any]:
        return {
            "wireguard": self.vpn_manager.get_vpn_system_overview(),
            "teleport": self.teleport_adapter.health(),
            "workspace_agents": "NOT_CONFIGURED",
            "network_policy": self._network_policy_status(),
        }

    @staticmethod
    def _network_policy_status() -> str:
        # No Gateway firewall/controller integration exists in this deployment.
        return "NOT_VERIFIED"

    async def revoke_member_access(self, member_id: str, actor: str = "labops-api") -> Dict[str, Any]:
        member = next((item for item in self.members if item["id"] == member_id), None)
        if not member:
            raise ValueError("Member not found")
        failures = []
        if member.get("teleport_status") not in {None, "", "NOT_CONFIGURED", "REVOKED"}:
            try:
                result = self.teleport_adapter.revoke_user(member["username"])
                member["teleport_status"] = "REVOKED_SESSION_STATUS_UNKNOWN"
                failures.append(result.get("reason", "Teleport sessions were not verified as terminated"))
            except Exception:
                member["teleport_status"] = "PARTIAL_FAILURE"
                failures.append("Teleport identity revocation was not verified")
        try:
            self._revoke_member_wireguard(member)
        except Exception:
            failures.append("WireGuard peer revocation was not verified")
        if member.get("ssh_port") or any(
            ep.get("member_id") == member_id and ep.get("status") == "ACTIVE" for ep in self.ssh_endpoints
        ):
            failures.append("Legacy SSH endpoint is still present")
        state = "PARTIAL_FAILURE" if failures else "SUSPENDED"
        member["status"] = state
        member["provisioning_state"] = state
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}", "actor": actor,
            "action": "ACCESS_REVOKED", "target_type": "MEMBER", "target_id": member["username"],
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "result": state, "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": "Research workspace data was retained. " + "; ".join(failures or ["All configured access revocation steps were verified."]),
        })
        self._save_persistent_data()
        return {"status": state, "member": member, "failures": failures}

    def reset_member_password(self, member_id: str, new_password: Optional[str] = None, require_change: bool = True) -> Dict[str, Any]:
        raise RuntimeError("Long-lived Linux SSH passwords are disabled")

    def require_password_change(self, member_id: str) -> Dict[str, Any]:
        raise RuntimeError("Long-lived Linux SSH passwords are disabled")

    def suspend_ssh_access(self, member_id: str) -> Dict[str, Any]:
        raise RuntimeError("Use revoke_member_access so WireGuard and Teleport are processed together")

    def restore_ssh_access(self, member_id: str) -> Dict[str, Any]:
        raise RuntimeError("Legacy SSH restoration is disabled; access must be reprovisioned and verified")

    def get_member_login_events(self, member_id: str) -> List[Dict[str, Any]]:
        raise RuntimeError("Legacy SSH login events are not a verified Teleport audit source")

    def verify_member_dual_auth(self, member_id: str) -> Dict[str, Any]:
        raise RuntimeError("Legacy SSH dual-auth verification was simulated and is disabled")




    # ------------------------------------------------------------------
    # LabOps V2 Platform Methods (Host Registry, SSH Keys, Researcher Portal)
    # ------------------------------------------------------------------
    def get_v2_hosts(self) -> List[Dict[str, Any]]:
        return self.host_registry.list_hosts(live_servers=self.servers)

    def get_researcher_portal_snapshot(self, username: str) -> Dict[str, Any]:
        """Dữ liệu phạm vi hẹp chỉ dành riêng cho nghiên cứu viên đang đăng nhập."""
        member = next((m for m in self.members if m.get("username") == username), None)
        my_workspaces = self.workspace_controller.list_workspaces(owner=username, legacy_endpoints=self.ssh_endpoints)
        my_reservations = [
            {**r, **GpuScheduler.gpu_hours(r)}
            for r in self.gpu_scheduler.list_reservations(owner=username)
        ]
        my_ssh_keys = self.ssh_key_registry.list_keys(username=username)
        my_scans = self.security_scanner.list_scans(submitter=username)
        my_jobs = [j for j in self.jobs if j.get("username") == username]
        total_allocated_hours = round(
            sum((r.get("allocated_hours") or 0.0) for r in my_reservations), 3
        )
        total_runtime_hours = round(
            sum((r.get("runtime_hours") or 0.0) for r in my_reservations if r.get("runtime_hours") is not None), 3
        )
        return {
            "username": username,
            "member": member,
            "workspaces": my_workspaces,
            "ssh_keys": my_ssh_keys,
            "reservations": my_reservations,
            "jobs": my_jobs,
            "security_scans": my_scans,
            "usage": {
                "allocated_gpu_hours": total_allocated_hours,
                "verified_runtime_gpu_hours": total_runtime_hours,
                "active_reservations": sum(
                    1 for r in my_reservations if r.get("state") in ("RESERVED", "STARTING", "RUNNING", "STOP_REQUESTED")
                ),
            },
            "ssh_connection_guide": {
                "architecture": "WireGuard VPN -> Allocated Endpoint -> OpenSSH Public Key -> Isolated Workspace",
                "host_legacy_ssh_protected": True,
                "sudo_inside_workspace": "DENIED",
                "docker_socket_mounted": False,
            },
            "account_security": self.ssh_access_manager.get_account_security_state(
                username, expected_member_id=member["id"] if member else None
            ),
        }

    # ------------------------------------------------------------------
    # Admin-Managed Member SSH Lifecycle (Tạo, Bàn giao 1 lần, Đổi mật khẩu tạm, Thu hồi)
    # ------------------------------------------------------------------
    def _find_member(self, member_id_or_username: str) -> Dict[str, Any]:
        member = next(
            (m for m in self.members if m.get("id") == member_id_or_username or m.get("username") == member_id_or_username),
            None,
        )
        if not member:
            raise LookupError(f"Không tìm thấy thành viên '{member_id_or_username}'")
        return member

    def _is_host_online(self, server_id: str) -> bool:
        srv = next((s for s in self.servers if s.get("id") == server_id), None)
        if not srv:
            return False
        return str(srv.get("status", "")).lower() == "online"

    def _resolve_effective_ssh_login_user(self, username: str, server_id: str) -> str:
        """
        Trả về đúng Linux username của thành viên bên trong container workspace cô lập.
        TUYỆT ĐỐI không fallback sang OS user quản trị host (aiot-ws1) hoặc getpass.getuser().
        """
        _ = server_id
        return str(username or "").strip()

    def _resolve_member_workspace_dirs(self, member: Dict[str, Any]) -> Tuple[str, str]:
        """Trả về (primary_ws_dir, fallback_ws_dir) có thể ghi thực tế trên host cho thành viên."""
        server_id = member.get("assigned_server") or member.get("server_id") or settings.LOCAL_HOST_ID
        username = member["username"]
        fallback_dir = self.ssh_access_manager.member_workspace_dir(server_id, username)
        candidate = str(member.get("member_storage_directory") or member.get("workspace_dir") or "").strip()
        if candidate and os.path.abspath(candidate) != os.path.abspath(fallback_dir):
            try:
                os.makedirs(os.path.join(candidate, ".ssh"), mode=0o700, exist_ok=True)
                if os.access(candidate, os.W_OK | os.X_OK):
                    return os.path.abspath(candidate), os.path.abspath(fallback_dir)
            except OSError:
                pass
        os.makedirs(os.path.join(fallback_dir, ".ssh"), mode=0o700, exist_ok=True)
        return os.path.abspath(fallback_dir), os.path.abspath(fallback_dir)

    def _ensure_local_workspace_sshd_running(self, member: Dict[str, Any], *, restart: bool = False) -> bool:
        """
        Vô hiệu hóa vĩnh viễn fallback host SSHD (P0-1 Security Hotfix).
        Không bao giờ khởi chạy /usr/sbin/sshd trên host quản trị cho tài khoản Researcher.
        """
        _ = restart
        logger.warning(
            "Từ chối khởi chạy host SSHD fallback cho @%s — yêu cầu Docker workspace container cô lập qua Go Node Agent.",
            member.get("username"),
        )
        return False

    @staticmethod
    def _is_pending_placeholder_device(device: Dict[str, Any]) -> bool:
        dev_id = str(device.get("device_id") or device.get("id") or "")
        ext_id = str(device.get("external_id") or "")
        return dev_id.startswith("dev-pending-") or (
            device.get("status") == "PENDING_PROVISIONING" and not ext_id
        )

    @staticmethod
    def _annotate_unregistered_wg_conf(conf_text: str, username: str, error_reason: Optional[str]) -> str:
        reason_line = f"# Lý do kỹ thuật: {error_reason}\n" if error_reason else ""
        banner = (
            "# ==============================================================================\n"
            "# [CẢNH BÁO — CHƯA ĐĂNG KÝ TRÊN WG-EASY VPN GATEWAY]\n"
            "# wg-easy Gateway không phản hồi khi tạo tệp này (peer chưa tồn tại trên server).\n"
            f"{reason_line}"
            f"# - Nếu đang trong mạng LAN phòng Lab: KHÔNG bật WireGuard, kết nối thẳng: ssh labops-{username}\n"
            "# - Nếu cần VPN từ xa qua Internet: Admin cần khởi động labops-wg-easy và cấp lại thiết bị VPN.\n"
            "# ==============================================================================\n\n"
        )
        return banner + conf_text.lstrip()

    def _get_persisted_wg_server_public_key(self) -> str:
        """Đọc khóa công khai máy chủ WireGuard từ wireguard_server_keys.json (kiểm tra đúng 32-byte Base64)."""
        candidates = [
            os.path.join(self.data_dir, "wireguard_server_keys.json"),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "wireguard_server_keys.json")),
        ]
        for path in candidates:
            if not path or not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                key = str(data.get("server_public_key") or "").strip()
                if key:
                    raw = base64.b64decode(key, validate=True)
                    if len(raw) == 32:
                        return key
            except Exception:
                continue
        return ""

    def _resolve_wg_server_public_key(self, member: Dict[str, Any]) -> str:
        """Lấy khóa công khai WireGuard theo thứ tự: wg-easy live -> member record -> env -> wireguard_server_keys.json."""
        try:
            live_key = self.vpn_manager.get_server_public_key()
            if live_key:
                return str(live_key).strip()
        except Exception:
            pass
        member_key = str((member.get("wireguard") or {}).get("server_public_key") or "").strip()
        if member_key:
            return member_key
        env_key = str(os.getenv("LABOPS_WG_SERVER_PUBLIC_KEY") or "").strip()
        if env_key:
            return env_key
        return self._get_persisted_wg_server_public_key()

    @staticmethod
    def _is_valid_vpn_client_ip(ip_val: Optional[str]) -> Optional[str]:
        if not ip_val:
            return None
        clean = str(ip_val).strip().split("/")[0]
        m = re.fullmatch(r"10\.88\.40\.(\d{1,3})", clean)
        if not m:
            return None
        octet = int(m.group(1))
        if 2 <= octet <= 254:
            return clean
        return None

    def _allocate_unique_wg_client_ip(self, member: Dict[str, Any]) -> str:
        """Cấp IP WireGuard 10.88.40.x duy nhất, không trùng lặp giữa các thành viên."""
        member_id = member.get("id")
        used_ips = set()
        for other in self.members:
            if other.get("id") == member_id:
                continue
            owg = other.get("wireguard") or {}
            for candidate in (owg.get("client_ip"), owg.get("vpn_ip")):
                valid = self._is_valid_vpn_client_ip(candidate)
                if valid:
                    used_ips.add(valid)
            for dev in owg.get("devices") or []:
                for candidate in (dev.get("vpn_ip"), dev.get("client_ip")):
                    valid = self._is_valid_vpn_client_ip(candidate)
                    if valid:
                        used_ips.add(valid)

        mwg = member.get("wireguard") or {}
        own_candidates = [mwg.get("client_ip"), mwg.get("vpn_ip")]
        for dev in mwg.get("devices") or []:
            own_candidates.extend([dev.get("vpn_ip"), dev.get("client_ip")])
        for candidate in own_candidates:
            valid = self._is_valid_vpn_client_ip(candidate)
            if valid and valid not in used_ips:
                return valid

        for host_octet in range(10, 250):
            candidate = f"10.88.40.{host_octet}"
            if candidate not in used_ips:
                return candidate
        raise RuntimeError("Hết dải IP WireGuard 10.88.40.10-249 khả dụng")

    def _build_gpu_uuid_map(self, server_id: str) -> Dict[str, str]:
        """Ánh xạ ID GPU nội bộ (VD: workstation-gpu-01) sang UUID phần cứng NVIDIA (GPU-...)."""
        gpu_map: Dict[str, str] = {}
        for g in self.gpus:
            if g.get("server_id") != server_id:
                continue
            uuid_val = str(g.get("uuid") or "").strip()
            if not uuid_val.startswith(("GPU-", "MIG-")):
                continue
            gid = str(g.get("id") or "").strip()
            if gid:
                gpu_map[gid] = uuid_val
                gpu_map[gid.lower()] = uuid_val
                canon = canonicalize_gpu_id(gid)
                if canon:
                    gpu_map[canon] = uuid_val
                    gpu_map[canon.lower()] = uuid_val
            gpu_map[uuid_val] = uuid_val
        if server_id == settings.LOCAL_HOST_ID and "workstation-gpu-01" not in gpu_map:
            try:
                inspected = self.node_agent.local_executor._inspect_gpus()
                for idx, gpu in enumerate(inspected.get("gpus") or []):
                    uuid_val = str(gpu.get("uuid") or "").strip()
                    if not uuid_val.startswith(("GPU-", "MIG-")):
                        continue
                    g_idx = int(gpu.get("index", idx)) + 1
                    canon_id = f"{server_id}-gpu-{g_idx:02d}"
                    gpu_map.setdefault(canon_id, uuid_val)
                    gpu_map.setdefault(canon_id.lower(), uuid_val)
                    if g_idx == 1 and server_id == "workstation":
                        gpu_map.setdefault("ws-gpu-01", uuid_val)
            except Exception:
                pass
        return gpu_map

    def _sync_member_ssh_authorization(
        self,
        member: Dict[str, Any],
        *,
        actor: str,
        allow_canary_local_sync: bool = False,
        save: bool = True,
    ) -> Dict[str, Any]:
        """
        Cổng kiểm soát tập trung (Central SSH Authorization Gate):
        - Đánh giá đồng thời cả 8 cổng bảo mật trước khi mở authorized_keys hoặc đánh dấu READY.
        - Đồng bộ authorized_keys vào Docker runtime (qua Go Node Agent) hoặc canary test dir.
        """
        username = member["username"]
        server_id = member.get("assigned_server") or settings.LOCAL_HOST_ID
        server = next((s for s in self.servers if s["id"] == server_id), None)
        host_online = self._is_host_online(server_id)
        self.ssh_access_manager.reconcile_container_activation_marker(
            username,
            expected_member_id=member.get("id"),
            member_storage_dir=member.get("member_storage_directory"),
        )
        account_security = self.ssh_access_manager.get_account_security_state(
            username, expected_member_id=member.get("id")
        )
        vpn_overview = {"status": (member.get("wireguard") or {}).get("status")}

        gate = evaluate_ssh_authorization_gate(
            member=member,
            account_security=account_security,
            runtime_verification=member.get("runtime_verification"),
            vpn_overview=vpn_overview,
        )

        keys_to_sync = [member["ssh_public_key"]] if (gate["ssh_authorized"] and member.get("ssh_public_key")) else []
        synced_ok = False

        if member.get("workspace_id") and member.get("container_id"):
            if host_online and server is not None:
                try:
                    self.node_agent.dispatch(
                        server,
                        "SYNC_SSH_AUTHORIZED_KEYS",
                        params={
                            "workspace_id": str(member["workspace_id"]),
                            "member_username": username,
                            "keys_json": json.dumps(keys_to_sync),
                        },
                        idempotency_key=f"sync-keys-{member['workspace_id']}-{uuid.uuid4().hex[:8]}",
                        actor_id=actor,
                        actor_role="ADMIN",
                    )
                    synced_ok = True
                    if server_id == settings.LOCAL_HOST_ID:
                        self.ssh_access_manager.sync_container_first_login_gate(
                            workspace_id=str(member["workspace_id"]),
                            username=username,
                            member_id=str(member.get("id") or ""),
                            keys=keys_to_sync,
                            member_storage_dir=member.get("member_storage_directory"),
                        )
                except Exception as sync_exc:
                    if keys_to_sync:
                        gate["ssh_authorized"] = False
                        gate["overall_readiness"] = "AGENT_UNAVAILABLE"
                        gate["ssh_runtime_state"] = "SSH_ACCESS_DENIED"
                        gate["blocking_codes"].append("SYNC_SSH_KEYS_FAILED")
                        gate["blocking_reasons"].append(
                            f"Không thể đồng bộ authorized_keys qua Go Node Agent: {getattr(sync_exc, 'message', str(sync_exc))}"
                        )
            elif keys_to_sync:
                gate["ssh_authorized"] = False
                gate["overall_readiness"] = "AGENT_UNAVAILABLE"
                gate["ssh_runtime_state"] = "SSH_ACCESS_DENIED"
                gate["blocking_codes"].append("AGENT_UNAVAILABLE")
        elif (
            not self._is_live_production_data_dir
            and member.get("workspace_dir")
            and (allow_canary_local_sync or not keys_to_sync)
        ):
            ws_dir = str(member["workspace_dir"])
            if os.path.isdir(os.path.join(ws_dir, ".ssh")) or os.path.isdir(ws_dir):
                synced_ok = self.ssh_access_manager.write_authorized_keys_state(ws_dir, keys_to_sync)

        if gate["ssh_authorized"] and synced_ok:
            rv = member.get("runtime_verification")
            if isinstance(rv, dict):
                rv["ssh_key_installed"] = "VERIFIED"
                quota_gate_status = (gate.get("gates") or {}).get("storage_quota", {}).get("status")
                storage_ok = rv.get("storage_quota") == "VERIFIED" or (
                    rv.get("storage_quota") == "NOT_ENFORCEABLE" and quota_gate_status == "POLICY_ONLY"
                )
                if all(
                    rv.get(k) == "VERIFIED"
                    for k in ("docker_running", "directory_isolation", "privilege_isolation", "cpu_ram_limits", "ssh_runtime")
                ) and storage_ok and rv.get("gpu_isolation") in ("VERIFIED", "NOT_APPLICABLE"):
                    rv["overall_status"] = "READY"

        member["activation_status"] = gate["activation_status"]
        member["authorization_gate"] = gate
        member["overall_readiness"] = gate["overall_readiness"]

        terminal_or_revoked = {"DELETE_ELIGIBLE", "ARCHIVED", "DELETED", "SUSPENDED", "REVOKED"}
        if member.get("ssh_key_status") in ("VERIFIED_REVOKED", "PENDING_REVOKE"):
            member["ssh_status"] = member["ssh_key_status"]
            member["provisioning_state"] = member.get("provisioning_state") or member["ssh_key_status"]
        else:
            member["ssh_status"] = gate["ssh_runtime_state"]
            member["provisioning_state"] = gate["overall_readiness"]
            member["workspace_status"] = gate["overall_readiness"]
            if member.get("status") not in terminal_or_revoked:
                member["status"] = "READY" if gate["overall_readiness"] == "READY" else "PENDING_PROVISIONING"

        if save:
            self._save_persistent_data()
        return gate

    def provision_member_ssh_access(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Luồng Admin tạo và cấp quyền truy cập SSH tự động cho thành viên:
        1. Kiểm tra username, host, tài nguyên.
        2. Dùng client-enrolled OpenSSH public key (nếu có) hoặc sinh cặp khóa Ed25519 mã hóa passphrase.
        3. Cấp mật khẩu tạm riêng cho lần đăng nhập đầu tiên (scrypt hash, CHANGE_REQUIRED, PENDING_ACTIVATION).
        4. Tạo thư mục workspace riêng (0700) và giữ .ssh/authorized_keys RỖNG trước khi kích hoạt.
        5. Ủy quyền cấp phát Docker container + kiểm chứng runtime qua Go Node Agent.
        6. Chỉ mở authorized_keys và chuyển sang READY khi tất cả 8 cổng SSH Authorization Gate đều VERIFIED.
        7. Lưu gói kết nối (encrypted private key nếu server sinh) và bí mật (kênh riêng) vào One-Time Vault.
        """
        actor_id = payload.get("_actor_id") or "admin"
        enforce_canary_runtime = bool(payload.get("enforce_canary_runtime") or payload.get("enforceCanaryRuntime"))
        temp_password_ttl_seconds = int(payload.get("temp_password_ttl_seconds") or 86400)

        username = (payload.get("username") or "").strip().lower()
        validate_linux_username(username)

        existing_member = next(
            (m for m in self.members if (m.get("username") or "").lower() == username), None
        )
        allow_existing = bool(payload.get("allow_existing_member"))
        if existing_member and not allow_existing:
            raise MemberConflictError(
                "USERNAME_ALREADY_EXISTS",
                f"Tên người dùng '{username}' đã tồn tại trong hệ thống",
                409,
                {"existing_member_id": existing_member.get("id"), "status": existing_member.get("status")},
            )

        server_id = payload.get("assigned_server") or payload.get("assignedServer") or settings.LOCAL_HOST_ID
        server = next((item for item in self.servers if item["id"] == server_id), None)
        if not server:
            raise ValueError(f"Không tìm thấy máy chủ '{server_id}'")

        cpu_limit = int(payload.get("cpu_limit_cores", payload.get("cpuLimitCores", 8)))
        ram_limit = int(payload.get("ram_limit_gb", payload.get("ramLimitGb", 32)))
        storage_quota_gb = int(payload.get("storage_quota_gb", payload.get("storageQuotaGb", 250)))
        gpu_permissions = list(payload.get("gpu_permissions", payload.get("gpuPermissions", [])) or [])
        workspace_name = payload.get("workspace_name") or payload.get("workspaceName") or f"ws-{username}"
        cpu_policy = payload.get("cpu_policy") or payload.get("cpuPolicy") or "SHARED_WEIGHTED"
        cpu_weight = int(payload.get("cpu_weight") or payload.get("cpuWeight") or 100)

        client_pubkey = str(payload.get("ssh_public_key") or payload.get("sshPublicKey") or "").strip()
        if client_pubkey:
            compute_ssh_fingerprint(client_pubkey)

        guard_cm = contextmanager(lambda: (yield))() if (existing_member and allow_existing) else self._username_creation_guard(username)
        with guard_cm:
            if not existing_member:
                self._verify_username_available_for_creation(username, workspace_name=workspace_name)

            pre_members = list(self.members)
            pre_endpoints = list(self.ssh_endpoints)
            pre_audits = list(self.audit_logs)
            pre_account = (
                self.ssh_access_manager.snapshot_account_state(username)
                if not existing_member
                else None
            )
            fx: Dict[str, Any] = {
                "storage_reserved": None,
                "created_key_id": None,
                "created_ws_id": None,
                "has_container": False,
            }

            if client_pubkey:
                meta = compute_ssh_fingerprint(client_pubkey)
                public_key = client_pubkey
                fingerprint = meta["fingerprint_sha256"]
                encrypted_private_key = ""
                passphrase = ""
                ssh_key_enrollment_mode = "CLIENT_ENROLLED_PUBKEY"
                ssh_key_passphrase_mode = "CLIENT_ENROLLED_KEY"
            else:
                require_passphrase = payload.get("require_passphrase")
                if require_passphrase is None:
                    require_passphrase = payload.get("requirePassphrase", True)
                require_passphrase = bool(require_passphrase)
                generated = self.ssh_access_manager.generate_encrypted_ed25519_keypair(
                    username, workspace_name, require_passphrase=require_passphrase
                )
                public_key = generated["public_key"]
                fingerprint = generated["fingerprint"]
                encrypted_private_key = generated["encrypted_private_key"]
                passphrase = generated["passphrase"]
                ssh_key_enrollment_mode = "SERVER_GENERATED_ENCRYPTED_KEY"
                ssh_key_passphrase_mode = (
                    "PASSPHRASE_VERIFIED" if require_passphrase else "NO_PASSPHRASE_SERVER_GENERATED"
                )

            if not existing_member:
                base_payload = dict(payload)
                base_payload.pop("provision_ssh_access", None)
                base_payload.pop("provisionSshAccess", None)
                base_payload["ssh_public_key"] = public_key
                base_payload["ssh_key_label"] = (
                    payload.get("ssh_key_label")
                    or payload.get("sshKeyLabel")
                    or ("Client-Enrolled Public Key" if client_pubkey else "Admin-Managed Ed25519 Key")
                )
                member = self.provision_member(
                    base_payload,
                    _defer_persist=True,
                    _skip_username_lock=True,
                    _side_effects=fx,
                )
            else:
                member = existing_member
                key_rec = self.ssh_key_registry.register_key(
                    username=username,
                    label="Client-Enrolled Public Key" if client_pubkey else "Admin-Managed Ed25519 Key",
                    public_key=public_key,
                    actor=actor_id,
                    member_id=member["id"],
                )
                if not member.get("ssh_port"):
                    member["ssh_port"] = self.get_next_available_ssh_port(server_id)
                    member["ssh_ip"] = (
                        server.get("vpn_ip")
                        or server.get("ip_address")
                        or ("192.168.167.251" if server_id == "workstation" else "192.168.167.250")
                    )
                member["ssh_fingerprint"] = key_rec["fingerprint"]
                member["ssh_public_key"] = public_key
                member["ssh_public_key_preview"] = f"{public_key[:42]}..."
                member["ssh_key_status"] = "ACTIVE"
                for storage_field in (
                    "storage_pool_id",
                    "storage_id",
                    "host_storage_root",
                    "member_storage_directory",
                    "storage_pool_label",
                    "mount_path",
                ):
                    if payload.get(storage_field) and not member.get(storage_field):
                        member[storage_field] = payload[storage_field]

            created_new_member = not existing_member
            try:
                # Tạo thư mục workspace cô lập (0700) nhưng GIỮ authorized_keys RỖNG tới khi kích hoạt & pass gate
                ws_fs = self.ssh_access_manager.setup_isolated_workspace_filesystem(
                    host_id=server_id,
                    username=username,
                    workspace_name=workspace_name,
                    public_key=public_key,
                    cpu_limit_cores=cpu_limit,
                    ram_limit_gb=ram_limit,
                    storage_quota_gb=storage_quota_gb,
                    gpu_permissions=gpu_permissions,
                    extra_mounts=payload.get("extra_mounts"),
                    authorize_ssh_key=False,
                )

                member["ssh_key_enrollment_mode"] = ssh_key_enrollment_mode
                member["ssh_key_passphrase_mode"] = ssh_key_passphrase_mode
                member["_canary_mode"] = enforce_canary_runtime
                member["cpu_policy"] = cpu_policy
                member["cpu_weight"] = cpu_weight

                # Cấp mật khẩu tạm cho lần đăng nhập đầu tiên (chỉ lưu scrypt hash, trạng thái PENDING_ACTIVATION)
                temp_password, pw_meta = self.ssh_access_manager.issue_temporary_password(
                    member_id=member["id"],
                    username=username,
                    actor=actor_id,
                    ttl_seconds=temp_password_ttl_seconds,
                )
                member["activation_status"] = "PENDING_ACTIVATION"
                member["password_status"] = "CHANGE_REQUIRED"
                member["temp_password_expires_at"] = pw_meta["temp_password_expires_at"]

                host_online = self._is_host_online(server_id)
                agent_verification_rec: Optional[Dict[str, Any]] = None
                if enforce_canary_runtime:
                    is_test_ctx = "PYTEST_CURRENT_TEST" in os.environ or os.getenv("LABOPS_ALLOW_TEST_CANARY_SSHD") == "1"
                    if self._is_live_production_data_dir or not is_test_ctx:
                        raise PermissionError(
                            "CANARY_SSHD_FORBIDDEN_IN_PRODUCTION: Không được phép bật host canary SSHD trong môi trường production."
                        )
                    if host_online:
                        canary = self.ssh_access_manager.start_canary_sshd_for_workspace(
                            server_id,
                            username,
                            port=int(member.get("ssh_port") or 0),
                            workspace_dir=ws_fs.get("workspace_dir"),
                            listen_address="127.0.0.1",
                            workspace_name=workspace_name,
                        )
                        member["ssh_port"] = canary["port"]
                else:
                    # Ủy quyền cấp phát Docker workspace qua Go Node Agent/Gateway; FastAPI không tự chạy docker run trực tiếp.
                    existing_ws = [
                        w for w in self.workspace_controller.list_workspaces(owner=username)
                        if w.get("owner") == username
                        and w.get("member_id") in (member["id"], None, "")
                        and w.get("runtime_type") == "DOCKER"
                    ]
                    ws_rec = existing_ws[0] if existing_ws else None
                    if ws_rec is None:
                        try:
                            ws_rec = self.workspace_controller.request_workspace(
                                owner=username,
                                host_id=server_id,
                                ssh_public_key=public_key,
                                cpu_cores=cpu_limit,
                                ram_mb=ram_limit * 1024,
                                storage_quota_gb=storage_quota_gb,
                                gpu_uuid=(gpu_permissions[0] if gpu_permissions else None),
                                extra_mounts=payload.get("extra_mounts"),
                                storage_path=member.get("member_storage_directory") or ws_fs.get("workspace_dir"),
                                member_storage_dir=member.get("member_storage_directory") or ws_fs.get("workspace_dir"),
                                container_mount_path="/workspace",
                                resource_mode=cpu_policy,
                                cpu_weight=cpu_weight,
                                member_id=member["id"],
                            )
                            fx["created_ws_id"] = fx.get("created_ws_id") or ws_rec["id"]
                        except Exception as ws_exc:
                            logger.warning("Workspace controller validation note for %s: %s", username, ws_exc)
                            ws_rec = None

                    if ws_rec is not None and host_online:
                        try:
                            spec = self.workspace_controller.build_provision_workspace_spec(
                                member=member,
                                workspace=ws_rec,
                                correlation_id=f"prov-{uuid.uuid4().hex[:12]}",
                                actor=actor_id,
                                image_ref=payload.get("image_ref") or payload.get("imageRef"),
                                image_digest=payload.get("image_digest") or payload.get("imageDigest"),
                                gpu_id_to_uuid=self._build_gpu_uuid_map(server_id),
                            )
                            member["uid"] = spec["uid"]
                            member["gid"] = spec["gid"]
                            member["workspace_id"] = spec["workspace_id"]
                            agent_res = self.node_agent.dispatch(
                                server,
                                "PROVISION_DOCKER_WORKSPACE",
                                params={
                                    "spec_json": json.dumps(spec),
                                    "workspace_id": spec["workspace_id"],
                                    "name": spec["name"],
                                    "owner": username,
                                    "member_id": member["id"],
                                    "ssh_port": str(spec["ssh_port"]),
                                },
                                idempotency_key=f"ws-prov-{spec['workspace_id']}-gen{spec['generation']}",
                                actor_id=actor_id,
                                actor_role="ADMIN",
                            )
                            agent_rec = agent_res.get("result") or {}
                            member["workspace_delegation_status"] = (
                                "VERIFIED" if agent_rec.get("observed_state") == "ACTIVE" else "PARTIAL_PROVISIONING"
                            )
                            member["workspace_agent_request_id"] = agent_res.get("request_id")
                            if agent_rec.get("canonical_container_id"):
                                member["container_id"] = agent_rec["canonical_container_id"]
                                fx["has_container"] = True
                                fx["container_id"] = agent_rec["canonical_container_id"]
                                try:
                                    ver_res = self.node_agent.dispatch(
                                        server,
                                        "VERIFY_DOCKER_WORKSPACE",
                                        params={
                                            "workspace_id": spec["workspace_id"],
                                            "container_id": agent_rec["canonical_container_id"],
                                            "uid": str(spec["uid"]),
                                            "gid": str(spec["gid"]),
                                            "ssh_port": str(spec["ssh_port"]),
                                            "resource_mode": spec.get("resource_mode") or cpu_policy,
                                            "vpn_bind_ip": spec.get("vpn_bind_ip") or "",
                                        },
                                        idempotency_key=f"ws-ver-{spec['workspace_id']}-{uuid.uuid4().hex[:8]}",
                                        actor_id=actor_id,
                                        actor_role="ADMIN",
                                    )
                                    ver_payload = ver_res.get("result") or {}
                                    if isinstance(ver_payload, dict) and ver_payload.get("checks"):
                                        agent_verification_rec = ver_payload
                                except Exception as ver_err:
                                    logger.warning("VERIFY_DOCKER_WORKSPACE failed for %s: %s", username, ver_err)
                        except Exception as prov_err:
                            # Fail-closed tuyệt đối: KHÔNG fallback về host SSHD khi Node Agent hoặc Spec lỗi
                            err_code = getattr(prov_err, "code", "")
                            err_msg = getattr(prov_err, "message", str(prov_err))
                            delegation_status = (
                                "AGENT_UNAVAILABLE"
                                if err_code in ("AGENT_UNREACHABLE", "AGENT_NOT_CONFIGURED", "BLOCKED_BY_INFRASTRUCTURE")
                                else "PROVISION_FAILED"
                            )
                            member["workspace_delegation_status"] = delegation_status
                            member["workspace_delegation_reason"] = err_msg
                    else:
                        member["workspace_delegation_status"] = "AGENT_UNAVAILABLE" if not host_online else "PROVISION_FAILED"

                known_gpu_ids = [g["id"] for g in self.gpus if g.get("server_id") == server_id] + [
                    g["uuid"] for g in self.gpus if g.get("server_id") == server_id and g.get("uuid")
                ]
                candidate_ws_dir = str(member.get("workspace_dir") or "").strip()
                if candidate_ws_dir and os.path.isfile(os.path.join(candidate_ws_dir, ".ssh", "authorized_keys")):
                    effective_ws_dir = candidate_ws_dir
                else:
                    effective_ws_dir = ws_fs["workspace_dir"]

                verification = self.ssh_access_manager.verify_workspace_and_runtime(
                    host_id=server_id,
                    host_online=host_online,
                    username=username,
                    workspace_name=workspace_name,
                    expected_public_key=public_key,
                    ssh_port=int(member.get("ssh_port") or 22001),
                    cpu_limit_cores=cpu_limit,
                    ram_limit_gb=ram_limit,
                    storage_quota_gb=storage_quota_gb,
                    gpu_permissions=gpu_permissions,
                    known_gpu_ids=known_gpu_ids,
                    enforce_canary_runtime=enforce_canary_runtime,
                    workspace_dir=effective_ws_dir,
                    agent_verification=agent_verification_rec,
                    cpu_policy=cpu_policy,
                    cpu_weight=cpu_weight,
                    expected_uid=member.get("uid"),
                    require_authorized_key_present=False,
                    storage_dir=member.get("member_storage_directory") or effective_ws_dir,
                )

                member["ssh_key_status"] = "ACTIVE" if host_online else "PENDING_SYNC"
                member["workspace_dir"] = effective_ws_dir
                member["runtime_verification"] = verification
                member["privilege_validation"] = {
                    "unprivileged_container": verification["privilege_isolation"] == "VERIFIED",
                    "sudo_test_failed_as_expected": verification["privilege_isolation"] == "VERIFIED",
                    "no_sudo_group_membership": verification["privilege_isolation"] == "VERIFIED",
                    "no_docker_socket_access": verification["privilege_isolation"] == "VERIFIED",
                    "no_incus_socket_access": verification["privilege_isolation"] == "VERIFIED",
                    "verification_timestamp": verification["verified_at"],
                    "status": "VERIFIED_SAFE" if verification["privilege_isolation"] == "VERIFIED" else "NOT_VERIFIED",
                }

                ssh_host = (
                    member.get("ssh_ip")
                    or server.get("ip_address")
                    or ("192.168.167.251" if server_id == "workstation" else "192.168.167.250")
                )
                ssh_port = int(member.get("ssh_port") or 22001)

                # WireGuard VPN Configuration: Tích hợp tự động qua wg-easy v15 & Dynamic Route Resolver
                wg_profile = None
                wg_registered_on_gateway = False
                wg_gateway_error: Optional[str] = None
                assigned_host = member.get("assigned_server") or member.get("server_id") or member.get("host_id") or "workstation"
                matching_workspaces = [
                    w for w in self.workspaces
                    if (w.get("owner") == username or w.get("username") == username)
                    and (w.get("host_id") == assigned_host or w.get("server_id") == assigned_host)
                ]
                resolved_route = self.vpn_manager.route_resolver.resolve_routes_for_member(
                    member=member,
                    actor_role="ADMIN",
                    workspaces=matching_workspaces,
                    requested_host_id=assigned_host,
                )
                target_server_id = resolved_route.target_hosts[0].id if resolved_route.target_hosts else assigned_host
                dynamic_allowed_ips = resolved_route.allowed_ips

                if getattr(settings, "WG_EASY_ENABLED", True):
                    try:
                        gw_status = self.vpn_manager.wg_easy.get_connection_status()
                        if gw_status.get("status") == "UNAVAILABLE":
                            raise RuntimeError(gw_status.get("error") or "VPN_PROVIDER_UNAVAILABLE")
                        if gw_status.get("service_health") == "VPN_ROUTING_MISCONFIGURED" or gw_status.get("routing_issues"):
                            raise RuntimeError(
                                f"VPN_ROUTING_MISCONFIGURED: {'; '.join(gw_status.get('routing_issues') or [])}"
                            )
                        wg_dev = self.vpn_manager.create_member_device(username, "Thiết bị chính")
                        profile_res = self.vpn_manager.build_dynamic_client_profile(
                            actor={"username": username, "role": member.get("role", "RESEARCHER")},
                            member=member,
                            device=wg_dev,
                            workspaces=matching_workspaces,
                        )
                        custom_conf = profile_res.get("wireguard_conf", "")
                        if custom_conf and "[Interface]" in custom_conf:
                            wg_registered_on_gateway = True
                            wg_client_ip = profile_res.get("vpn_ip") or wg_dev.get("vpn_ip") or ""
                            wg_server_endpoint = profile_res.get("server_endpoint") or ""
                            dynamic_allowed_ips = profile_res.get("allowed_ips") or dynamic_allowed_ips
                            wg_profile = {
                                "wireguard_conf": custom_conf,
                                "client_ip": wg_client_ip,
                                "wg_public_key": wg_dev.get("public_key") or "",
                                "server_endpoint": wg_server_endpoint,
                                "allowed_ips": dynamic_allowed_ips,
                            }
                            wg_profile_data = member.setdefault("wireguard", {"status": "ACTIVE", "devices": []})
                            existing_devs = [
                                d for d in (wg_profile_data.get("devices") or [])
                                if not self._is_pending_placeholder_device(d)
                            ]
                            existing_devs.append({
                                "device_id": f"dev-{wg_dev.get('external_id') or wg_dev['id']}",
                                "external_id": wg_dev.get("external_id") or wg_dev["id"],
                                "provider": "wg-easy",
                                "name": "Thiết bị chính",
                                "client_ip": wg_client_ip,
                                "vpn_ip": wg_client_ip,
                                "public_key": wg_dev.get("public_key"),
                                "server_id": target_server_id,
                                "server_public_key": profile_res.get("server_public_key") or self.vpn_manager.get_server_public_key(),
                                "server_endpoint": wg_server_endpoint,
                                "allowed_subnets": dynamic_allowed_ips,
                                "status": "ACTIVE",
                                "enabled": True,
                                "enrolled_at": datetime.now(timezone.utc).isoformat(),
                            })
                            wg_profile_data["devices"] = existing_devs
                            wg_profile_data["status"] = "ACTIVE"
                    except Exception as e:
                        wg_gateway_error = str(e)
                        logger.error(f"Lỗi tạo peer wg-easy tự động: {e}")

                if wg_profile is None or not (wg_profile.get("wireguard_conf") and "[Interface]" in wg_profile["wireguard_conf"]):
                    if not wg_gateway_error and not getattr(settings, "WG_EASY_ENABLED", True):
                        wg_gateway_error = "WG_EASY_DISABLED: Tính năng wg-easy đang tắt trên hệ thống."
                    wg_status_label = (
                        "VPN_ROUTING_MISCONFIGURED"
                        if wg_gateway_error and "VPN_ROUTING_MISCONFIGURED" in wg_gateway_error
                        else "PENDING_PROVISIONING"
                    )
                    wg_profile = {
                        "wireguard_conf": "",
                        "client_ip": "",
                        "wg_public_key": "",
                        "server_endpoint": "",
                        "allowed_ips": dynamic_allowed_ips,
                        "status": wg_status_label,
                    }
                existing_devices = (member.get("wireguard") or {}).get("devices") or []
                has_active_gateway_dev = any(
                    d.get("status") == "ACTIVE" and not self._is_pending_placeholder_device(d)
                    for d in existing_devices
                )
                member["wireguard"] = {
                    **(member.get("wireguard") or {}),
                    "client_ip": wg_profile["client_ip"],
                    "vpn_ip": wg_profile["client_ip"],
                    "public_key": wg_profile["wg_public_key"],
                    "server_endpoint": wg_profile["server_endpoint"],
                    "allowed_subnets": wg_profile["allowed_ips"],
                    "status": (
                        "ACTIVE"
                        if (wg_registered_on_gateway or has_active_gateway_dev)
                        else wg_profile.get("status", "PENDING_PROVISIONING")
                    ),
                    "registered_on_gateway": wg_registered_on_gateway or has_active_gateway_dev,
                    "gateway_error": None if (wg_registered_on_gateway or has_active_gateway_dev) else wg_gateway_error,
                }

                effective_ssh_user = self._resolve_effective_ssh_login_user(username, server_id)
                member["ssh_login_user"] = effective_ssh_user
                vpn_guide = (
                    f"1. [VPN WireGuard] Mở ứng dụng WireGuard -> 'Import tunnel(s) from file' -> chọn tệp '{username}-labops-wg0.conf' -> Bật kết nối (Activate).\n"
                    f"2. [Tự động cài SSH Config] Chạy lệnh: bash setup-ssh-{username}.sh (tự động cài khóa riêng và ~/.ssh/config).\n"
                    f"3. [Kết nối SSH nhanh] Gõ trực tiếp: ssh labops-{username} (hoặc ssh -i ~/.ssh/id_ed25519_{username} -p {ssh_port} {effective_ssh_user}@{ssh_host}).\n"
                    f"4. Nhập Passphrase mở khóa riêng (do Admin gửi qua kênh riêng) và đổi mật khẩu tạm ở lần đăng nhập đầu tiên."
                )

                vault_info = self.ssh_access_manager.vault.store_provisioned_package(
                    member_id=member["id"],
                    username=username,
                    ssh_host=ssh_host,
                    ssh_port=ssh_port,
                    workspace_name=workspace_name,
                    workspace_path=ws_fs["work_dir"],
                    fingerprint=fingerprint,
                    encrypted_private_key=encrypted_private_key,
                    passphrase=passphrase,
                    temporary_password=temp_password,
                    temp_password_expires_at=pw_meta["temp_password_expires_at"],
                    vpn_guide=vpn_guide,
                    wireguard_conf=wg_profile["wireguard_conf"],
                    wireguard_client_ip=wg_profile["client_ip"],
                    wireguard_public_key=wg_profile["wg_public_key"],
                    wireguard_server_endpoint=wg_profile["server_endpoint"],
                    ssh_login_user=effective_ssh_user,
                )
                member["one_time_package"] = vault_info

                gate = self._sync_member_ssh_authorization(
                    member,
                    actor=actor_id,
                    allow_canary_local_sync=enforce_canary_runtime,
                    save=False,
                )

                op_id = f"op-prov-{uuid.uuid4().hex[:12]}"
                self.audit_logs.insert(0, {
                    "id": f"aud-{uuid.uuid4().hex[:12]}",
                    "actor": actor_id,
                    "action": "SSH_ACCESS_PROVISIONED",
                    "target_type": "MEMBER",
                    "target_id": username,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "result": gate["overall_readiness"],
                    "correlation_id": op_id,
                    "details": (
                        f"Đã khởi tạo khóa OpenSSH ({ssh_key_enrollment_mode}, {fingerprint}) và mật khẩu tạm lần đầu "
                        f"cho @{username} (ID: {member['id']}) trên host '{server_id}' (cổng {ssh_port}). Trạng thái tổng thể: {gate['overall_readiness']}."
                    ),
                })
                self._save_persistent_data()
            except MemberPersistenceError as mp_err:
                if created_new_member and mp_err.code == "JSON_PERSISTENCE_FAILED":
                    get_revocation_store().register_active_member(username, member["id"])
                    raise
                if created_new_member:
                    self._compensate_failed_provisioning(
                        fx,
                        pre_members,
                        pre_endpoints,
                        pre_audits,
                        pre_account=pre_account,
                        username=username,
                        member_id=member["id"],
                    )
                raise
            except Exception:
                if created_new_member:
                    self._compensate_failed_provisioning(
                        fx,
                        pre_members,
                        pre_endpoints,
                        pre_audits,
                        pre_account=pre_account,
                        username=username,
                        member_id=member["id"],
                    )
                raise

            if created_new_member:
                get_revocation_store().register_active_member(username, member["id"])
            return member

    def rotate_member_ssh_keypair(
        self, member_id_or_username: str, actor: str, require_passphrase: bool = True
    ) -> Dict[str, Any]:
        """
        Cấp lại cặp khóa SSH mới (khi khóa cũ bị mất hoặc lộ) và thu hồi toàn bộ khóa cũ của thành viên.
        Mặc định require_passphrase = True để tuân thủ chính sách bảo mật Ed25519 có passphrase.
        """
        member = self._find_member(member_id_or_username)
        username = member["username"]
        server_id = member.get("assigned_server") or settings.LOCAL_HOST_ID

        # Thu hồi các khóa cũ thuộc đúng member_id trong SshKeyRegistry
        for old_key in self.ssh_key_registry.list_keys(username=username, member_id=member["id"]):
            if old_key.get("desired_state") != "REVOKED":
                self.ssh_key_registry.revoke_key(old_key["id"], actor=actor, is_admin=True)

        # Tạo cặp khóa mới và đồng bộ qua Central SSH Authorization Gate
        updated = self.provision_member_ssh_access({
            "username": username,
            "full_name": member.get("full_name", username),
            "email": member.get("email", f"{username}@lab.ai.vn"),
            "role": member.get("role", "RESEARCHER"),
            "assigned_server": server_id,
            "workspace_name": member.get("workspace_name") or f"ws-{username}",
            "storage_pool_id": member.get("storage_pool_id"),
            "storage_id": member.get("storage_id") or member.get("storage_pool_id"),
            "host_storage_root": member.get("host_storage_root"),
            "member_storage_directory": member.get("member_storage_directory"),
            "storage_pool_label": member.get("storage_pool_label"),
            "mount_path": member.get("mount_path"),
            "cpu_limit_cores": member.get("cpu_limit_cores", 8),
            "ram_limit_gb": member.get("ram_limit_gb", 32),
            "storage_quota_gb": member.get("storage_quota_gb", 250),
            "gpu_permissions": member.get("gpu_permissions", []),
            "require_passphrase": require_passphrase,
            "allow_existing_member": True,
            "_actor_id": actor,
        })
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "SSH_KEYPAIR_ROTATED",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": updated["ssh_status"],
            "correlation_id": f"op-rot-{uuid.uuid4().hex[:12]}",
            "details": f"Đã thu hồi khóa cũ và cấp cặp khóa Ed25519 mới ({updated.get('ssh_fingerprint')}) cho @{username}.",
        })
        self._save_persistent_data()
        return updated

    def reissue_member_temp_password(
        self, member_id_or_username: str, actor: str, ttl_seconds: int = 86400
    ) -> Dict[str, Any]:
        """
        Cấp lại mật khẩu tạm mới cho thành viên (vô hiệu hóa ngay lập tức mật khẩu tạm cũ
        và đưa trạng thái về PENDING_ACTIVATION cho tới khi người dùng đổi mật khẩu lần đầu).
        """
        member = self._find_member(member_id_or_username)
        username = member["username"]
        temp_password, pw_meta = self.ssh_access_manager.issue_temporary_password(
            member_id=member["id"],
            username=username,
            actor=actor,
            ttl_seconds=ttl_seconds,
        )
        member["activation_status"] = "PENDING_ACTIVATION"
        member["password_status"] = "CHANGE_REQUIRED"
        member["temp_password_expires_at"] = pw_meta["temp_password_expires_at"]
        vault_meta = self.ssh_access_manager.vault.store_reissued_temp_password(
            member_id=member["id"],
            username=username,
            temporary_password=temp_password,
            temp_password_expires_at=pw_meta["temp_password_expires_at"],
        )
        member["one_time_package"] = self.ssh_access_manager.vault.get_availability(member["id"])
        gate = self._sync_member_ssh_authorization(
            member,
            actor=actor,
            allow_canary_local_sync=bool(member.get("_canary_mode")),
            save=False,
        )
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "TEMP_PASSWORD_REISSUED",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": "CHANGE_REQUIRED",
            "correlation_id": f"op-pw-{uuid.uuid4().hex[:12]}",
            "details": f"Đã cấp lại mật khẩu tạm đăng nhập lần đầu cho @{username} (hết hạn lúc {pw_meta['temp_password_expires_at']}); mật khẩu tạm cũ đã bị hủy hiệu lực.",
        })
        self._save_persistent_data()
        return {
            "member_id": member["id"],
            "username": username,
            "activation_status": "PENDING_ACTIVATION",
            "password_status": "CHANGE_REQUIRED",
            "temp_password_expires_at": pw_meta["temp_password_expires_at"],
            "authorization_gate": gate,
            "one_time_secrets": vault_meta,
        }

    def claim_member_access_zip(self, member_id_or_username: str, actor: str) -> Tuple[str, bytes]:
        """
        Xuất file ZIP trọn gói LabOps_Access_<username>.zip chứa:
        - README.md & Hướng dẫn tiếng Việt
        - wireguard/labops.conf
        - ssh/config, ssh/id_ed25519_<username>.pub và ssh/id_ed25519_<username> (nếu có khóa riêng cấp phát 1 lần)
        - connection/ (JSON, TXT, SSH_COMMAND.txt)
        - scripts/ (setup-macos.sh, setup-linux.sh, setup-windows.ps1)
        """
        member = self._find_member(member_id_or_username)
        username = member["username"]
        assigned_host = member.get("assigned_server") or member.get("server_id") or member.get("host_id") or settings.LOCAL_HOST_ID or "workstation"
        effective_ssh_user = self._resolve_effective_ssh_login_user(username, assigned_host)
        server = next((s for s in self.servers if s["id"] == assigned_host), {})
        matching_workspaces = [
            w for w in self.workspaces
            if (w.get("owner") == username or w.get("username") == username)
            and (w.get("host_id") == assigned_host or w.get("server_id") == assigned_host)
        ]
        resolved_route = self.vpn_manager.route_resolver.resolve_routes_for_member(
            member=member,
            actor_role="ADMIN",
            workspaces=matching_workspaces,
            requested_host_id=assigned_host,
        )
        ssh_host = str(member.get("ssh_ip") or server.get("ip_address") or "").strip()
        if not ssh_host or ssh_host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            ssh_host = resolved_route.ssh_host
        ssh_port = int(member.get("ssh_port") or resolved_route.ssh_port or 22001)
        public_key = member.get("ssh_public_key") or ""

        # Lấy thông tin gói 1 lần nếu còn trong vault
        bundle = {}
        try:
            bundle = self.ssh_access_manager.vault.claim_key_bundle_once(member["id"], peek=True)
        except Exception:
            pass

        # Tuyệt đối KHÔNG đóng gói passphrase hoặc temporary password vào file ZIP (bắt buộc qua kênh riêng)
        temp_pw_exp = member.get("temp_password_expires_at")
        priv_key = bundle.get("encrypted_private_key") or None
        wg_conf = bundle.get("wireguard_conf")
        wg_client_ip = (
            bundle.get("wireguard_client_ip")
            or self._is_valid_vpn_client_ip((member.get("wireguard") or {}).get("client_ip"))
            or ""
        )
        wg_ep = bundle.get("wireguard_server_endpoint") or (member.get("wireguard") or {}).get("server_endpoint") or ""

        if not wg_conf:
            # 1. Thử lấy cấu hình từ thiết bị wg-easy đã đăng ký qua WireGuardConfigBuilder
            profile = member.get("wireguard") or {}
            devices = profile.get("devices") or []
            wg_easy_dev = next(
                (
                    d
                    for d in devices
                    if d.get("provider") == "wg-easy"
                    and d.get("status") == "ACTIVE"
                    and not self._is_pending_placeholder_device(d)
                ),
                None,
            )
            wg_fallback_err: Optional[str] = None
            if wg_easy_dev:
                try:
                    profile_res = self.vpn_manager.build_dynamic_client_profile(
                        actor={"username": username, "role": member.get("role", "RESEARCHER")},
                        member=member,
                        device=wg_easy_dev,
                        workspaces=matching_workspaces,
                    )
                    wg_conf = profile_res["wireguard_conf"]
                    wg_client_ip = profile_res["vpn_ip"] or wg_client_ip
                    wg_ep = profile_res["server_endpoint"]
                except Exception as e:
                    wg_fallback_err = str(e)
                    logger.warning(f"Không thể sinh cấu hình động wg-easy cho device {wg_easy_dev}: {e}")
            elif getattr(settings, "WG_EASY_ENABLED", True):
                # 1b. Nếu chưa có peer thực trên wg-easy, tự thử đăng ký khi xuất gói kết nối
                try:
                    new_dev = self.add_member_device(member["id"], "Thiết bị chính", actor=actor)
                    if new_dev.get("configuration") and "[Interface]" in new_dev["configuration"]:
                        wg_conf = new_dev["configuration"]
                        wg_client_ip = new_dev.get("vpn_ip") or wg_client_ip
                        wg_ep = new_dev.get("server_endpoint") or wg_ep
                except Exception as e:
                    wg_fallback_err = str(e)
                    logger.warning(f"wg-easy chưa khả dụng khi tự động nâng cấp VPN cho {username}: {e}")

            # 2. Tuyệt đối KHÔNG tự sinh file .conf giả chưa đăng ký trên wg-easy Gateway
            if not wg_conf:
                wg_state = member.setdefault("wireguard", {"status": "PENDING_PROVISIONING", "devices": []})
                wg_state["registered_on_gateway"] = False
                wg_state["status"] = (
                    "VPN_ROUTING_MISCONFIGURED"
                    if wg_fallback_err and "VPN_ROUTING_MISCONFIGURED" in wg_fallback_err
                    else "PENDING_PROVISIONING"
                )
                if wg_fallback_err:
                    wg_state["gateway_error"] = wg_fallback_err

        zip_bytes = self.ssh_access_manager.build_access_zip_package(
            username=username,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            workspace_name=member.get("workspace_name") or f"ws-{username}",
            public_key=public_key,
            private_key=priv_key,
            wireguard_conf=wg_conf,
            wireguard_client_ip=wg_client_ip,
            wireguard_endpoint=wg_ep,
            key_passphrase=None,
            temporary_password=None,
            temp_password_expires_at=temp_pw_exp,
            ssh_login_user=effective_ssh_user,
        )

        # Sau khi tải gói zip trọn gói, giải phóng khóa riêng một lần khỏi RAM nếu có
        try:
            self.ssh_access_manager.vault.claim_key_bundle_once(member["id"], peek=False)
        except Exception:
            pass
        member["one_time_package"] = self.ssh_access_manager.vault.get_availability(member["id"])

        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "ACCESS_ZIP_PACKAGE_DOWNLOADED",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": "SUCCESS",
            "correlation_id": f"op-zip-{uuid.uuid4().hex[:12]}",
            "details": f"Admin '{actor}' đã tải bộ tệp kết nối trọn gói LabOps_Access_{username}.zip (dung lượng: {len(zip_bytes)} bytes).",
        })
        self._save_persistent_data()
        filename = f"LabOps_Access_{username}.zip"
        return filename, zip_bytes

    def claim_member_key_bundle(self, member_id_or_username: str, actor: str) -> Dict[str, Any]:
        """
        Tải gói kết nối & Private key đã mã hóa bằng passphrase (CHỈ 1 LẦN DUY NHẤT).
        """
        member = self._find_member(member_id_or_username)
        bundle = self.ssh_access_manager.vault.claim_key_bundle_once(member["id"])
        member["one_time_package"] = self.ssh_access_manager.vault.get_availability(member["id"])
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "SSH_KEY_BUNDLE_CLAIMED",
            "target_type": "MEMBER",
            "target_id": member["username"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": "CLAIMED_ONCE",
            "correlation_id": bundle.get("bundle_id") or f"op-bnd-{uuid.uuid4().hex[:12]}",
            "details": f"Admin '{actor}' đã tải gói hướng dẫn kết nối và private key mã hóa ({bundle.get('fingerprint')}) của @{member['username']} (1 lần duy nhất).",
        })
        self._save_persistent_data()
        return bundle

    def claim_member_secrets(self, member_id_or_username: str, actor: str) -> Dict[str, Any]:
        """
        Nhận Passphrase mở khóa riêng và Mật khẩu tạm đăng nhập lần đầu qua kênh riêng (CHỈ 1 LẦN DUY NHẤT).
        """
        member = self._find_member(member_id_or_username)
        secrets_pkg = self.ssh_access_manager.vault.claim_channel_secrets_once(member["id"])
        member["one_time_package"] = self.ssh_access_manager.vault.get_availability(member["id"])
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "SSH_CHANNEL_SECRETS_CLAIMED",
            "target_type": "MEMBER",
            "target_id": member["username"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": "CLAIMED_ONCE",
            "correlation_id": secrets_pkg.get("secret_id") or f"op-sec-{uuid.uuid4().hex[:12]}",
            "details": f"Admin '{actor}' đã lấy passphrase & mật khẩu tạm một lần của @{member['username']} để gửi qua kênh riêng.",
        })
        self._save_persistent_data()
        return secrets_pkg

    def revoke_member_ssh_access(
        self,
        member_id_or_username: str,
        actor: str,
        encrypted_private_key_for_probe: Optional[str] = None,
        passphrase_for_probe: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Thu hồi quyền SSH của thành viên và xác minh tại runtime.
        Chỉ báo VERIFIED_REVOKED khi đã gỡ khóa khỏi authorized_keys của container/workspace và xác minh thành công.
        Nếu host offline hoặc Go Node Agent lỗi, giữ ở trạng thái PENDING_REVOKE hoặc FAILED.
        """
        member = self._find_member(member_id_or_username)
        server_id = member.get("assigned_server") or settings.LOCAL_HOST_ID
        server = next((s for s in self.servers if s["id"] == server_id), None)
        host_online = self._is_host_online(server_id)

        container_sync_err: Optional[str] = None
        if host_online and member.get("workspace_id") and member.get("container_id") and server is not None:
            try:
                self.node_agent.dispatch(
                    server,
                    "SYNC_SSH_AUTHORIZED_KEYS",
                    params={
                        "workspace_id": str(member["workspace_id"]),
                        "member_username": member["username"],
                        "keys_json": "[]",
                    },
                    idempotency_key=f"revoke-keys-{member['workspace_id']}-{uuid.uuid4().hex[:8]}",
                    actor_id=actor,
                    actor_role="ADMIN",
                )
            except Exception as sync_exc:
                container_sync_err = getattr(sync_exc, "message", str(sync_exc))

        res = self.ssh_access_manager.revoke_member_ssh(
            member=member,
            host_online=host_online,
            actor=actor,
            encrypted_private_key_for_probe=encrypted_private_key_for_probe,
            passphrase_for_probe=passphrase_for_probe,
            workspace_dir=member.get("workspace_dir"),
        )
        if container_sync_err:
            res["status"] = "FAILED"
            res["ssh_key_status"] = "FAILED"
            res["verified_revoked"] = False
            res["reason"] = f"Không thể đồng bộ xóa authorized_keys vào Docker container: {container_sync_err}"

        member["ssh_key_status"] = res["ssh_key_status"]
        member["ssh_status"] = res["status"]
        member["provisioning_state"] = res["status"]
        member["activation_status"] = "REVOKED"
        member["password_status"] = "REVOKED"
        member["one_time_package"] = self.ssh_access_manager.vault.get_availability(member["id"])
        # Do not demote status if member is already archived, delete-eligible, or deleted
        if member.get("status") not in {"DELETE_ELIGIBLE", "ARCHIVED", "DELETED"}:
            if res["status"] == "VERIFIED_REVOKED":
                member["status"] = "SUSPENDED"
            else:
                member["status"] = res["status"]

        gate = evaluate_ssh_authorization_gate(
            member=member,
            account_security=self.ssh_access_manager.get_account_security_state(member["username"]),
            runtime_verification=member.get("runtime_verification"),
            vpn_overview={"status": (member.get("wireguard") or {}).get("status")},
        )
        member["authorization_gate"] = gate
        member["overall_readiness"] = "SSH_ACCESS_DENIED"
        res["authorization_gate"] = gate

        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "SSH_ACCESS_REVOKED",
            "target_type": "MEMBER",
            "target_id": member["username"],
            "timestamp": res["timestamp"],
            "result": res["status"],
            "correlation_id": res["operation_id"],
            "details": f"[{res['operation_id']}] Thu hồi SSH của @{member['username']} trên host '{server_id}': {res['status']} — {res['reason']}",
        })
        self._save_persistent_data()
        return res

    def terminate_member_ssh_sessions(
        self, member_id_or_username: str, actor: str, confirm: bool = False
    ) -> Dict[str, Any]:
        if not confirm:
            raise ValueError("CONFIRMATION_REQUIRED: Bắt buộc xác nhận (confirm=true) trước khi đóng các phiên SSH đang mở.")
        member = self._find_member(member_id_or_username)
        res = self.ssh_access_manager.terminate_workspace_ssh_sessions(member=member, actor=actor)
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "SSH_ACTIVE_SESSIONS_TERMINATED",
            "target_type": "MEMBER",
            "target_id": member["username"],
            "timestamp": res["timestamp"],
            "result": res["status"],
            "correlation_id": res["operation_id"],
            "details": f"[{res['operation_id']}] Đã đóng {res['terminated_daemons']} tiến trình phiên SSH của workspace @{member['username']}.",
        })
        self._save_persistent_data()
        return res

    def change_member_first_login_password(
        self, username: str, temporary_password: str, new_password: str
    ) -> Dict[str, Any]:
        member = self._find_member(username)
        try:
            res = self.ssh_access_manager.change_first_login_password(
                username=member["username"],
                temporary_password=temporary_password,
                new_password=new_password,
                expected_member_id=member.get("id"),
            )
            member["password_status"] = "ACTIVE"
            member["activation_status"] = "ACTIVATED"
            gate = self._sync_member_ssh_authorization(
                member,
                actor=member["username"],
                allow_canary_local_sync=bool(member.get("_canary_mode")),
                save=False,
            )
            res["activation_status"] = "ACTIVATED"
            res["authorization_gate"] = gate
            res["overall_readiness"] = gate["overall_readiness"]
            self.audit_logs.insert(0, {
                "id": f"aud-{uuid.uuid4().hex[:12]}",
                "actor": member["username"],
                "action": "FIRST_LOGIN_PASSWORD_CHANGED",
                "target_type": "MEMBER",
                "target_id": member["username"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "result": gate["overall_readiness"],
                "correlation_id": f"op-pwchg-{uuid.uuid4().hex[:12]}",
                "details": (
                    f"Thành viên @{member['username']} đã kích hoạt tài khoản và đổi mật khẩu lần đầu; "
                    f"ssh_authorized={gate['ssh_authorized']}, readiness={gate['overall_readiness']}."
                ),
            })
            self._save_persistent_data()
            return res
        except (ValueError, PermissionError) as exc:
            self.audit_logs.insert(0, {
                "id": f"aud-{uuid.uuid4().hex[:12]}",
                "actor": member["username"],
                "action": "FIRST_LOGIN_PASSWORD_CHANGE_FAILED",
                "target_type": "MEMBER",
                "target_id": member["username"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "result": "DENIED",
                "correlation_id": f"op-pwfail-{uuid.uuid4().hex[:12]}",
                "details": f"Đổi mật khẩu lần đầu thất bại cho @{member['username']}: {exc}",
            })
            self._save_persistent_data()
            raise

    def verify_member_workspace_password(self, username: str, password: str) -> Dict[str, Any]:
        member = self._find_member(username)
        res = self.ssh_access_manager.verify_workspace_password_login(
            member["username"],
            password,
            expected_member_id=member.get("id"),
        )
        member["password_status"] = res["password_status"]
        member["activation_status"] = res.get("activation_status") or member.get("activation_status") or "PENDING_ACTIVATION"
        self._save_persistent_data()
        return res

    def get_member_ssh_access_status(self, member_id_or_username: str) -> Dict[str, Any]:
        member = self._find_member(member_id_or_username)
        username = member["username"]
        return {
            "member_id": member["id"],
            "username": username,
            "assigned_server": member.get("assigned_server"),
            "workspace_name": member.get("workspace_name"),
            "ssh_ip": member.get("ssh_ip"),
            "ssh_port": member.get("ssh_port"),
            "ssh_status": member.get("ssh_status"),
            "ssh_key_status": member.get("ssh_key_status"),
            "ssh_key_enrollment_mode": member.get("ssh_key_enrollment_mode"),
            "ssh_key_passphrase_mode": member.get("ssh_key_passphrase_mode"),
            "ssh_fingerprint": member.get("ssh_fingerprint"),
            "activation_status": member.get("activation_status"),
            "password_status": member.get("password_status"),
            "overall_readiness": member.get("overall_readiness"),
            "workspace_delegation_status": member.get("workspace_delegation_status"),
            "authorization_gate": member.get("authorization_gate"),
            "runtime_verification": member.get("runtime_verification"),
            "one_time_package": self.ssh_access_manager.vault.get_availability(member["id"]),
            "account_security": self.ssh_access_manager.get_account_security_state(
                username, expected_member_id=member["id"] if member else None
            ),
        }

    def reverify_member_workspace(self, member_id_or_username: str, actor: str) -> Dict[str, Any]:
        """Kiểm chứng lại runtime Docker container, quota và cổng ủy quyền SSH của thành viên."""
        member = self._find_member(member_id_or_username)
        username = member["username"]
        server_id = member.get("assigned_server") or settings.LOCAL_HOST_ID
        server = next((s for s in self.servers if s["id"] == server_id), None)
        host_online = self._is_host_online(server_id)
        agent_ver_rec: Optional[Dict[str, Any]] = None

        if (
            host_online
            and server is not None
            and not member.get("container_id")
            and not member.get("_canary_mode")
            and member.get("status") not in {"SUSPENDED", "REVOKED", "ARCHIVED", "DELETE_ELIGIBLE", "DELETED"}
        ):
            try:
                existing_ws = [
                    w for w in self.workspace_controller.list_workspaces(owner=username)
                    if w.get("owner") == username
                    and w.get("member_id") in (member.get("id"), None, "")
                    and w.get("runtime_type") == "DOCKER"
                ]
                ws_rec = existing_ws[0] if existing_ws else None
                if ws_rec is None and member.get("ssh_public_key"):
                    ws_rec = self.workspace_controller.request_workspace(
                        owner=username,
                        host_id=server_id,
                        ssh_public_key=str(member.get("ssh_public_key") or ""),
                        cpu_cores=int(member.get("cpu_limit_cores") or 8),
                        ram_mb=int(member.get("ram_limit_gb") or 32) * 1024,
                        storage_quota_gb=int(member.get("storage_quota_gb") or 200),
                        gpu_uuid=(member.get("gpu_permissions") or [None])[0],
                        storage_path=member.get("member_storage_directory") or member.get("workspace_dir"),
                        member_storage_dir=member.get("member_storage_directory") or member.get("workspace_dir"),
                        container_mount_path="/workspace",
                        resource_mode=member.get("cpu_policy") or "SHARED_WEIGHTED",
                        cpu_weight=int(member.get("cpu_weight") or 100),
                        member_id=member.get("id"),
                    )
                if ws_rec is not None:
                    spec = self.workspace_controller.build_provision_workspace_spec(
                        member=member,
                        workspace=ws_rec,
                        correlation_id=f"prov-{uuid.uuid4().hex[:12]}",
                        actor=actor,
                        gpu_id_to_uuid=self._build_gpu_uuid_map(server_id),
                    )
                    member["uid"] = spec["uid"]
                    member["gid"] = spec["gid"]
                    member["workspace_id"] = spec["workspace_id"]
                    agent_res = self.node_agent.dispatch(
                        server,
                        "PROVISION_DOCKER_WORKSPACE",
                        params={
                            "spec_json": json.dumps(spec),
                            "workspace_id": spec["workspace_id"],
                            "name": spec["name"],
                            "owner": username,
                            "member_id": str(member.get("id") or ""),
                            "ssh_port": str(spec["ssh_port"]),
                        },
                        idempotency_key=f"ws-prov-{spec['workspace_id']}-gen{spec['generation']}",
                        actor_id=actor,
                        actor_role="ADMIN",
                    )
                    agent_rec = agent_res.get("result") or {}
                    member["workspace_delegation_status"] = (
                        "VERIFIED" if agent_rec.get("observed_state") == "ACTIVE" else "PARTIAL_PROVISIONING"
                    )
                    member["workspace_delegation_reason"] = None
                    member["workspace_agent_request_id"] = agent_res.get("request_id")
                    if agent_rec.get("canonical_container_id"):
                        member["container_id"] = agent_rec["canonical_container_id"]
            except Exception as exc:
                err_code = getattr(exc, "code", "")
                err_msg = getattr(exc, "message", str(exc))
                member["workspace_delegation_status"] = (
                    "AGENT_UNAVAILABLE"
                    if err_code in ("AGENT_UNREACHABLE", "AGENT_NOT_CONFIGURED", "BLOCKED_BY_INFRASTRUCTURE")
                    else "PROVISION_FAILED"
                )
                member["workspace_delegation_reason"] = err_msg

        if host_online and member.get("workspace_id") and member.get("container_id") and server is not None:
            try:
                ver_res = self.node_agent.dispatch(
                    server,
                    "VERIFY_DOCKER_WORKSPACE",
                    params={
                        "workspace_id": str(member["workspace_id"]),
                        "container_id": str(member["container_id"]),
                        "uid": str(member.get("uid") or 0),
                        "gid": str(member.get("gid") or 0),
                        "ssh_port": str(member.get("ssh_port") or 0),
                        "resource_mode": member.get("cpu_policy") or "SHARED_WEIGHTED",
                        "vpn_bind_ip": str(
                            member.get("ssh_ip")
                            or (
                                getattr(settings, "LABOPS_WORKSPACE_BIND_IP", "192.168.167.251")
                                if server_id == "workstation"
                                else "192.168.167.250"
                            )
                        ),
                    },
                    idempotency_key=f"ws-reverify-{member['workspace_id']}-{uuid.uuid4().hex[:8]}",
                    actor_id=actor,
                    actor_role="ADMIN",
                )
                payload = ver_res.get("result") or {}
                if isinstance(payload, dict) and payload.get("checks"):
                    agent_ver_rec = payload
                    member["workspace_delegation_status"] = "VERIFIED" if payload.get("verified") else "PROVISION_FAILED"
            except Exception as exc:
                err_code = getattr(exc, "code", "")
                err_msg = getattr(exc, "message", str(exc))
                member["workspace_delegation_status"] = (
                    "AGENT_UNAVAILABLE" if err_code in ("AGENT_UNREACHABLE", "AGENT_NOT_CONFIGURED") else "PROVISION_FAILED"
                )
                member["workspace_delegation_reason"] = err_msg

        known_gpu_ids = [g["id"] for g in self.gpus if g.get("server_id") == server_id] + [
            g["uuid"] for g in self.gpus if g.get("server_id") == server_id and g.get("uuid")
        ]
        candidate_ws_dir = str(member.get("workspace_dir") or "").strip()
        if candidate_ws_dir and os.path.isfile(os.path.join(candidate_ws_dir, ".ssh", "authorized_keys")):
            ws_dir = candidate_ws_dir
        else:
            ws_dir = self.ssh_access_manager.member_workspace_dir(server_id, username)
        verification = self.ssh_access_manager.verify_workspace_and_runtime(
            host_id=server_id,
            host_online=host_online,
            username=username,
            workspace_name=member.get("workspace_name") or f"ws-{username}",
            expected_public_key=member.get("ssh_public_key") or "",
            ssh_port=int(member.get("ssh_port") or 22001),
            cpu_limit_cores=int(member.get("cpu_limit_cores") or 8),
            ram_limit_gb=int(member.get("ram_limit_gb") or 32),
            storage_quota_gb=int(member.get("storage_quota_gb") or 250),
            gpu_permissions=list(member.get("gpu_permissions") or []),
            known_gpu_ids=known_gpu_ids,
            enforce_canary_runtime=bool(member.get("_canary_mode")),
            workspace_dir=ws_dir,
            agent_verification=agent_ver_rec,
            cpu_policy=member.get("cpu_policy") or "SHARED_WEIGHTED",
            cpu_weight=int(member.get("cpu_weight") or 100),
            expected_uid=member.get("uid"),
            require_authorized_key_present=False,
            storage_dir=member.get("member_storage_directory") or ws_dir,
        )
        member["runtime_verification"] = verification
        gate = self._sync_member_ssh_authorization(
            member,
            actor=actor,
            allow_canary_local_sync=bool(member.get("_canary_mode")),
            save=False,
        )
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "WORKSPACE_RUNTIME_REVERIFIED",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": gate["overall_readiness"],
            "correlation_id": f"op-ver-{uuid.uuid4().hex[:12]}",
            "details": f"Kiểm chứng lại runtime workspace @{username} trên '{server_id}': {gate['overall_readiness']}.",
        })
        self._save_persistent_data()
        return self.get_member_ssh_access_status(username)

    # ------------------------------------------------------------------
    # VPN Administration & Multi-Device Management (wg-easy v15)
    # ------------------------------------------------------------------
    def get_vpn_system_overview(self) -> Dict[str, Any]:
        return self.vpn_manager.get_vpn_system_overview()

    def list_all_vpn_devices(self) -> List[Dict[str, Any]]:
        try:
            return self.vpn_manager.primary_provider.list_devices()
        except Exception as e:
            logger.debug(f"wg-easy offline khi liệt kê thiết bị VPN, dùng danh sách lưu trữ: {e}")
            cached: List[Dict[str, Any]] = []
            for m in self.members:
                wg = m.get("wireguard") or {}
                for d in wg.get("devices") or []:
                    item = dict(d)
                    item.setdefault("owner", m.get("username"))
                    item["sync_state"] = "CACHED_GATEWAY_OFFLINE"
                    cached.append(item)
            return cached

    def get_member_devices_synced(self, member_id: str) -> List[Dict[str, Any]]:
        member = self._find_member(member_id)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id}")
        profile = member.get("wireguard") or {}
        devices = list(profile.get("devices") or [])
        try:
            live_wg_clients = {str(c["id"]): c for c in self.vpn_manager.wg_easy.list_devices()}
            for d in devices:
                if self._is_pending_placeholder_device(d):
                    d["gateway_sync_state"] = "PENDING_PROVISIONING"
                    continue
                ext_id = str(d.get("external_id") or d.get("id"))
                if ext_id in live_wg_clients:
                    live = live_wg_clients[ext_id]
                    d["status"] = live.get("status", d.get("status"))
                    d["enabled"] = live.get("enabled", d.get("enabled"))
                    d["last_handshake"] = live.get("last_handshake")
                    d["is_online"] = bool(live.get("is_online", False))
                    d["handshake_age_seconds"] = live.get("handshake_age_seconds")
                    d["transfer_rx"] = live.get("transfer_rx", 0)
                    d["transfer_tx"] = live.get("transfer_tx", 0)
                    has_hs = bool(live.get("last_handshake")) and bool(live.get("enabled", True))
                    d["gateway_sync_state"] = "VPN_HANDSHAKE_VERIFIED" if has_hs else "VPN_PEER_REGISTERED"
                else:
                    d["gateway_sync_state"] = "UNREGISTERED_ON_GATEWAY"
        except Exception as e:
            for d in devices:
                d["gateway_sync_state"] = (
                    "PENDING_PROVISIONING" if self._is_pending_placeholder_device(d) else "CACHED_GATEWAY_OFFLINE"
                )
            logger.debug(f"Không thể đồng bộ live handshake từ wg-easy: {e}")
        return devices

    def add_member_device(self, member_id: str, device_name: str, actor: str = "admin") -> Dict[str, Any]:
        member = self._find_member(member_id)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id}")
        username = member["username"]
        assigned_host = member.get("assigned_server") or member.get("server_id") or member.get("host_id") or "workstation"
        matching_workspaces = [
            w for w in self.workspaces
            if (w.get("owner") == username or w.get("username") == username)
            and (w.get("host_id") == assigned_host or w.get("server_id") == assigned_host)
        ]
        route = self.vpn_manager.route_resolver.resolve_routes_for_member(
            member=member,
            actor_role="ADMIN",
            workspaces=matching_workspaces,
            requested_host_id=assigned_host,
        )
        resolved_server_id = route.target_hosts[0].id if route.target_hosts else assigned_host
        dev = self.vpn_manager.create_member_device(username, device_name)
        ext_id = str(dev.get("external_id") or dev.get("id"))
        clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", ext_id)
        server_pub = dev.get("server_public_key") or self.vpn_manager.get_server_public_key()
        server_ep = dev.get("server_endpoint") or self.vpn_manager.wg_easy.public_endpoint
        device_entry = {
            "device_id": f"dev-{clean_id}",
            "external_id": dev.get("external_id") or dev["id"],
            "provider": "wg-easy",
            "name": device_name,
            "client_ip": dev.get("vpn_ip", ""),
            "vpn_ip": dev.get("vpn_ip", ""),
            "public_key": dev.get("public_key", ""),
            "server_id": resolved_server_id,
            "server_public_key": server_pub,
            "server_endpoint": server_ep,
            "allowed_subnets": route.allowed_ips,
            "status": "ACTIVE",
            "enabled": True,
            "enrolled_at": datetime.now(timezone.utc).isoformat(),
        }
        profile = member.setdefault("wireguard", {"status": "ACTIVE", "devices": []})
        cleaned_devices = [
            d for d in (profile.get("devices") or [])
            if not self._is_pending_placeholder_device(d)
        ]
        cleaned_devices.append(device_entry)
        profile["devices"] = cleaned_devices
        profile["status"] = "ACTIVE"
        profile["registered_on_gateway"] = True
        profile["gateway_error"] = None
        profile["client_ip"] = device_entry["client_ip"]
        profile["vpn_ip"] = device_entry["vpn_ip"]
        profile["public_key"] = device_entry["public_key"]
        profile["server_public_key"] = server_pub
        profile["server_endpoint"] = server_ep
        profile["allowed_subnets"] = route.allowed_ips
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "WIREGUARD_DEVICE_REGISTERED",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã đăng ký thiết bị mới '{device_name}' (IP VPN: {dev.get('vpn_ip')}, Route: {route.allowed_ips}) cho @{username} qua wg-easy.",
        })
        self._save_persistent_data()

        conf_text = ""
        try:
            conf_res = self.vpn_manager.build_dynamic_client_profile(
                actor={"username": username, "role": member.get("role", "RESEARCHER")},
                member=member,
                device=device_entry,
                workspaces=matching_workspaces,
            )
            conf_text = conf_res.get("wireguard_conf", "")
        except Exception as exc:
            logger.warning("Không thể sinh cấu hình động ngay khi thêm thiết bị cho @%s: %s", username, exc)

        return {**device_entry, **({"configuration": conf_text} if conf_text else {})}

    def get_member_device_config(self, member_id: str, device_id: str, actor: Optional[Dict[str, Any]] = None) -> str:
        member = self._find_member(member_id)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id}")
        profile = member.get("wireguard") or {}
        devices = profile.get("devices") or []
        dev = next((d for d in devices if d.get("device_id") == device_id or str(d.get("external_id")) == device_id or str(d.get("id")) == device_id), None)
        if not dev:
            raise LookupError(f"Không tìm thấy thiết bị: {device_id}")
        if self._is_pending_placeholder_device(dev):
            raise ValueError(
                "Thiết bị đang ở trạng thái PENDING_PROVISIONING (chưa đăng ký trên wg-easy Gateway). "
                "Hãy khởi động labops-wg-easy và nhấn '+ Cấp thiết bị' để tạo cấu hình VPN hợp lệ."
            )
        assigned_host = member.get("assigned_server") or member.get("server_id") or member.get("host_id") or "workstation"
        username = member["username"]
        matching_workspaces = [
            w for w in self.workspaces
            if (w.get("owner") == username or w.get("username") == username)
            and (w.get("host_id") == assigned_host or w.get("server_id") == assigned_host)
        ]
        act = actor or {"username": username, "role": member.get("role", "RESEARCHER")}
        profile_res = self.vpn_manager.build_dynamic_client_profile(
            actor=act,
            member=member,
            device=dev,
            workspaces=matching_workspaces,
        )
        return profile_res["wireguard_conf"]

    def revoke_member_device_v2(self, member_id: str, device_id: str, actor: str = "admin") -> Dict[str, Any]:
        member = self._find_member(member_id)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id}")
        profile = member.get("wireguard") or {}
        devices = profile.get("devices") or []
        dev = next((d for d in devices if d.get("device_id") == device_id or str(d.get("external_id")) == device_id or str(d.get("id")) == device_id), None)
        if not dev:
            raise LookupError(f"Không tìm thấy thiết bị: {device_id}")
        is_pending = self._is_pending_placeholder_device(dev)
        if not is_pending:
            ext_id = str(dev.get("external_id") or dev.get("id") or device_id)
            self.vpn_manager.revoke_device(ext_id, dev.get("provider", "wg-easy"))
        dev["status"] = "REVOKED"
        dev["enabled"] = False
        remaining = [d for d in devices if d.get("device_id") != dev.get("device_id")]
        profile["devices"] = remaining
        if not remaining:
            profile["status"] = "NOT_CONFIGURED"
            profile["registered_on_gateway"] = False
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "WIREGUARD_DEVICE_REVOKED",
            "target_type": "MEMBER",
            "target_id": member["username"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": (
                f"Đã xóa bản ghi thiết bị chờ '{dev.get('name')}' của @{member['username']}."
                if is_pending
                else f"Đã thu hồi thiết bị '{dev.get('name')}' của @{member['username']} khỏi WireGuard VPN."
            ),
        })
        self._save_persistent_data()
        return {"status": "REVOKED", "device_id": device_id}

    def toggle_member_device(self, member_id: str, device_id: str, enable: bool, actor: str = "admin") -> Dict[str, Any]:
        member = self._find_member(member_id)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id}")
        profile = member.get("wireguard") or {}
        devices = profile.get("devices") or []
        dev = next((d for d in devices if d.get("device_id") == device_id or str(d.get("external_id")) == device_id or str(d.get("id")) == device_id), None)
        if not dev:
            raise LookupError(f"Không tìm thấy thiết bị: {device_id}")
        if self._is_pending_placeholder_device(dev):
            raise ValueError(
                "Không thể bật/tắt thiết bị PENDING_PROVISIONING chưa đăng ký trên wg-easy. "
                "Hãy thu hồi bản ghi chờ và cấp thiết bị mới khi wg-easy trực tuyến."
            )
        ext_id = str(dev.get("external_id") or dev.get("id") or device_id)
        if enable:
            self.vpn_manager.enable_device(ext_id, dev.get("provider", "wg-easy"))
            dev["status"] = "ACTIVE"
            dev["enabled"] = True
        else:
            self.vpn_manager.disable_device(ext_id, dev.get("provider", "wg-easy"))
            dev["status"] = "DISABLED"
            dev["enabled"] = False
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "WIREGUARD_DEVICE_TOGGLED",
            "target_type": "MEMBER",
            "target_id": member["username"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"{'Kích hoạt' if enable else 'Tạm ngưng'} thiết bị '{dev.get('name')}' của @{member['username']}.",
        })
        self._save_persistent_data()
        return dev

service = LabOpsDataService()
