import uuid
import re
import hashlib
import os
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple
from app.core.config import settings
from app.adapters.host_monitor_adapter import HostMonitorAdapter, get_real_root_mount_info
from app.adapters.nvml_adapter import NvmlAdapter
from app.services.gpu_scheduler import GpuScheduler, SchedulerError
from app.services.security_scanner import SecurityScanner
from app.services.host_registry import HostRegistry
from app.adapters.node_agent_adapter import NodeAgentClient
from app.adapters.arcane_adapter import ArcaneAdapter
from app.adapters.beszel_adapter import BeszelAdapter
from app.services.workspace_controller import WorkspaceController
from app.services.vpn_ssh_service import SshKeyRegistry
from app.services.ssh_access_manager import SshAccessManager
from app.adapters.process_monitor_adapter import ProcessMonitorAdapter
from app.adapters.wireguard_adapter import WireGuardAdapter
from app.adapters.teleport_adapter import TeleportAdapter
from app.services.vpn_manager import VPNManager

logger = logging.getLogger(__name__)

SAFE_USERNAME_REGEX = re.compile(r'^[a-z_][a-z0-9_-]{2,31}$')
RESERVED_USERNAMES = {
    "root", "bin", "daemon", "sys", "adm", "sync", "games", "man", "lp", "mail",
    "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats", "nobody",
    "systemd-network", "systemd-resolve", "messagebus", "sshd", "incus", "docker",
    "sudo", "wheel", "admin", "operator"
}
APPROVED_MOUNT_PATHS = {"/workspace", "/home", "/data"}

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

class LabOpsDataService:
    """
    Core data service for LabOps Control Center.
    Manages in-memory state for Demo Mode and interfaces with real host telemetry & persistent storage in Production Mode.
    """
    def __init__(self, demo_mode: Optional[bool] = None, data_dir: Optional[str] = None):
        self.demo_mode = settings.DEMO_MODE if demo_mode is None else demo_mode
        self.host_monitor = HostMonitorAdapter(is_demo_mode=self.demo_mode)
        self.nvml_adapter = NvmlAdapter(server_id=settings.LOCAL_HOST_ID, demo_mode=self.demo_mode)
        self.process_monitor = ProcessMonitorAdapter(server_id=settings.LOCAL_HOST_ID, demo_mode=self.demo_mode)
        self.data_dir = os.path.abspath(data_dir or os.path.join(os.path.dirname(__file__), "..", "..", "data"))
        os.makedirs(self.data_dir, exist_ok=True)
        self.wireguard_adapter = WireGuardAdapter(interface="wg0", data_dir=self.data_dir)
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

    def _save_persistent_data(self):
        try:
            with open(os.path.join(self.data_dir, "members.json"), "w", encoding="utf-8") as f:
                json.dump(self.members, f, ensure_ascii=False, indent=2)
            with open(os.path.join(self.data_dir, "ssh_policies.json"), "w", encoding="utf-8") as f:
                json.dump(self.ssh_policies, f, ensure_ascii=False, indent=2)
            with open(os.path.join(self.data_dir, "audit_logs.json"), "w", encoding="utf-8") as f:
                json.dump(self.audit_logs, f, ensure_ascii=False, indent=2)
            with open(os.path.join(self.data_dir, "storage_allocations.json"), "w", encoding="utf-8") as f:
                json.dump(self.storage_allocations, f, ensure_ascii=False, indent=2)
            with open(os.path.join(self.data_dir, "ssh_endpoints.json"), "w", encoding="utf-8") as f:
                json.dump(self.ssh_endpoints, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("Failed to persist LabOps data")
            raise RuntimeError("Could not persist LabOps data") from e

    @property
    def servers(self) -> List[Dict[str, Any]]:
        if not self.demo_mode:
            ws = self.host_monitor.get_local_workstation_telemetry()
            track = self.host_monitor.get_remote_track_telemetry()
            ws["active_members"] = sum(1 for m in self.members if m.get("assigned_server") == "workstation" and m.get("status") == "ACTIVE")
            track["active_members"] = sum(1 for m in self.members if m.get("assigned_server") == "track" and m.get("status") == "ACTIVE")
            jobs_by_server = self.jobs
            ws["active_jobs"] = sum(1 for job in jobs_by_server if job.get("serverId", job.get("server_id")) == "workstation")
            track["active_jobs"] = sum(1 for job in jobs_by_server if job.get("serverId", job.get("server_id")) == "track")
            return [track, ws]
        return self._demo_servers

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
                mnt_info = get_real_root_mount_info()
                root_u = psutil.disk_usage("/etc" if psutil.disk_usage("/").total < 100 * (1024**3) and os.path.exists("/etc") else "/")
                local_id = settings.LOCAL_HOST_ID
                local_name = "Server 01 — Server Rack (aiotlab-rack)" if local_id == "track" else "Server 02 — Workstation (Cục bộ)"
                pools = []
                labops_data_path = getattr(settings, "STORAGE_POOL_DATA_PATH", "/media/LabOps-Data")
                has_labops_data = os.path.exists(labops_data_path)

                if has_labops_data:
                    data_u = psutil.disk_usage(labops_data_path)
                    pools.append({
                        "id": "pool-labops-data",
                        "server_id": local_id,
                        "server_name": local_name,
                        "name": "pool-labops-data",
                        "label": f"Ổ đĩa Dữ liệu Nghiên cứu (/media/LabOps-Data — 37TB)",
                        "disk_identifier": "/dev/sdb",
                        "mount_point": labops_data_path,
                        "driver": "dir",
                        "capacity_gb": round(data_u.total / (1024**3), 1),
                        "used_gb": round(data_u.used / (1024**3), 1),
                        "free_gb": round(data_u.free / (1024**3), 1),
                        "health_status": "ONLINE_PRIMARY_DATA_ARRAY",
                        "is_default": True
                    })

                pools.append({
                    "id": "pool-root",
                    "server_id": local_id,
                    "server_name": local_name,
                    "name": "pool-root",
                    "label": f"Ổ đĩa Hệ thống ({mnt_info['fstype'].upper()} {mnt_info['device']})",
                    "disk_identifier": mnt_info["device"],
                    "mount_point": "/",
                    "driver": "dir",
                    "capacity_gb": round(root_u.total / (1024**3), 1),
                    "used_gb": round(root_u.used / (1024**3), 1),
                    "free_gb": round(root_u.free / (1024**3), 1),
                    "health_status": "ONLINE_LOCAL_FS",
                    "is_default": not has_labops_data
                })
                sdb_path = "/hdd1/hieulc/tmp"
                if os.path.exists(sdb_path):
                    sdb_u = psutil.disk_usage(sdb_path)
                    pools.append({
                        "id": "pool-data-sdb",
                        "server_id": "workstation",
                        "server_name": "Server 02 — Workstation (Cục bộ)",
                        "name": "pool-data-sdb",
                        "label": "Ổ cứng Lưu trữ Thứ cấp (/hdd1/hieulc/tmp)",
                        "disk_identifier": "/dev/sdb",
                        "mount_point": sdb_path,
                        "driver": "dir",
                        "capacity_gb": round(sdb_u.total / (1024**3), 1),
                        "used_gb": round(sdb_u.used / (1024**3), 1),
                        "free_gb": round(sdb_u.free / (1024**3), 1),
                        "health_status": "HEALTHY",
                        "is_default": False
                    })
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
                try:
                    with open(path, "r", encoding="utf-8") as stream:
                        value = json.load(stream)
                    if not isinstance(value, list):
                        raise ValueError(f"{filename} must contain a JSON array")
                    setattr(self, attribute, value)
                except Exception as e:
                    logger.warning(f"Failed to read {filename}: {e}")

    def get_summary(self) -> Dict[str, Any]:
        used_vram_gb = round(sum(gpu["vram_used_mb"] for gpu in self.gpus) / 1024, 1)
        total_vram_gb = round(sum(gpu.get("vram_total_mb", 0) for gpu in self.gpus) / 1024, 1)
        active_gpus = sum(1 for gpu in self.gpus if gpu["vram_used_mb"] > 0)
        
        if not self.demo_mode:
            real_ssh = self.process_monitor.get_real_ssh_sessions()
            online_ssh = real_ssh.get("total_count", 0)
            active_sessions = online_ssh
        else:
            online_ssh = sum(1 for m in self.members if m["ssh_status"] == "ONLINE")
            active_sessions = sum(m["ssh_sessions_count"] for m in self.members)

        running_jobs = sum(1 for j in self.jobs if j.get("status") == "RUNNING")
        
        return {
            "servers": {
                "total": len(self.servers),
                "online": sum(1 for s in self.servers if s["status"] == "online"),
                "offline": sum(1 for s in self.servers if s["status"] == "offline"),
                "unknown": sum(1 for s in self.servers if s["status"] in {"unknown", "degraded"})
            },
            "gpus": {
                "totalCount": len(self.gpus),
            "model": ", ".join(sorted({gpu.get("model", "") for gpu in self.gpus if gpu.get("model")})) or "UNKNOWN",
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
                "pending": sum(1 for job in self.jobs if job.get("status") == "PENDING"),
                "failed": sum(1 for job in self.jobs if job.get("status") == "FAILED")
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
    def _audit(self, actor: str, action: str, target_type: str, target_id: str, result: str, details: str) -> None:
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:8]}",
            "actor": actor,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": result,
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": details,
        })

    def allocate_gpu(self, gpu_id: str, username: str, mode: str, requested_vram_gb: int = 0,
                     job_name: str = "", actor: Optional[str] = None,
                     idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """Giữ độc quyền 1 GPU (theo UUID). Không sinh PID, không giả lập runtime."""
        target = next((g for g in self.gpus if g["id"] == gpu_id or g.get("uuid") == gpu_id), None)
        if not target:
            raise ValueError(f"Không tìm thấy GPU với ID {gpu_id}")
        if not target.get("uuid"):
            raise SchedulerError("GPU_UUID_UNKNOWN", "GPU không có UUID quan sát được; từ chối cấp phát.", 503)
        actor = actor or username
        try:
            r = self.gpu_scheduler.reserve(gpu_uuid=target["uuid"], owner=username, actor=actor,
                                           job_name=job_name, mode=mode, idempotency_key=idempotency_key)
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
            if classification == "RESEARCH_JOB":
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
            # Kiểm tra xem cổng có đang bị chiếm dụng trên máy chủ thật không
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    used_ports.add(port)
                    continue
            return port
        raise ValueError(f"Không còn cổng SSH khả dụng trên máy chủ {server_id}")

    def provision_member(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Record an access request; external workspace and identity services must verify activation."""
        if payload.get("provision_ssh_access") or payload.get("provisionSshAccess"):
            return self.provision_member_ssh_access(payload)
        username = payload.get("username", "")
        validate_linux_username(username)
        if any(member.get("username") == username for member in self.members):
            raise ValueError(f"Tên người dùng '{username}' đã tồn tại trong hệ thống")

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
        if role not in {"RESEARCHER", "ADMIN"}:
            raise ValueError("Role chỉ được là RESEARCHER hoặc ADMIN")

        server_id = payload.get("assigned_server") or payload.get("assignedServer") or settings.LOCAL_HOST_ID
        server = next((item for item in self.servers if item["id"] == server_id), None)
        if not server:
            raise ValueError(f"Không tìm thấy máy chủ '{server_id}'")
        pool_id = payload.get("storage_pool_id") or payload.get("storagePoolId")
        if not pool_id:
            pool = next((item for item in self.storage_pools if item["server_id"] == server_id and item.get("is_default")), None)
            pool = pool or next((item for item in self.storage_pools if item["server_id"] == server_id), None)
        else:
            pool = next((item for item in self.storage_pools if item["id"] == pool_id), None)
        if pool and pool.get("server_id") != server_id:
            raise ValueError(f"Không tìm thấy storage pool trên máy chủ '{server_id}'")
        if pool_id and not pool:
            raise ValueError(f"Không tìm thấy storage pool trên máy chủ '{server_id}'")
        storage_quota_gb = payload.get("storage_quota_gb", payload.get("storageQuotaGb", 250))
        if not isinstance(storage_quota_gb, int) or storage_quota_gb <= 0:
            raise ValueError("Dung lượng yêu cầu phải là số nguyên dương")
        if pool and storage_quota_gb > pool["free_gb"]:
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
        mount_path = payload.get("mount_path", payload.get("mountPath", "/workspace"))
        validate_mount_path(mount_path, username)

        workspace_name = payload.get("workspace_name") or payload.get("workspaceName") or f"ws-{username}"
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", workspace_name):
            raise ValueError("Workspace name không hợp lệ")
        if any(member.get("workspace_name") == workspace_name for member in self.members):
            raise ValueError(f"Workspace '{workspace_name}' đã tồn tại")

        raw_ssh_key = (payload.get("ssh_public_key") or payload.get("sshPublicKey") or "").strip()
        ssh_key_label = (payload.get("ssh_key_label") or payload.get("sshKeyLabel") or "Khóa SSH mặc định").strip()
        actor_id = payload.get("_actor_id", "labops-api")
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
            )
            allocated_port = self.get_next_available_ssh_port(server_id)
            ssh_ip = server.get("vpn_ip") or server.get("ip_address") or "192.168.167.250"
            ws_record = self.workspace_controller.request_workspace(
                owner=username,
                host_id=server_id,
                ssh_public_key=raw_ssh_key,
                cpu_cores=min(cpu_limit, 64),
                ram_mb=min(ram_limit * 1024, 262144),
                storage_quota_gb=min(storage_quota_gb, 2000),
            )
            workspace_status = ws_record.get("verification", "PENDING_VERIFICATION")

        member_id = f"mem-{uuid.uuid4().hex[:12]}"
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
            "storage_pool_id": pool["id"] if pool else None,
            "storage_pool_label": pool["label"] if pool else "NOT_VERIFIED",
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
            "details": f"Access request recorded for workspace {workspace_name} on {server_id}" + (f" with OpenSSH port {allocated_port} ({registered_key['fingerprint']})." if registered_key else "; provisioning is not verified."),
        })
        self._save_persistent_data()
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
                ssh_ip = server.get("vpn_ip") or server.get("ip_address") or "192.168.167.250"
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

    def delete_member_permanently(
        self, member_id_or_username: str, actor: str = "admin", remove_files: bool = True
    ) -> Dict[str, Any]:
        """
        Xóa bỏ hoàn toàn tài khoản thành viên khỏi LabOps:
        1. Dừng và xóa Docker container workspace ws-{username}
        2. Thu hồi và xóa toàn bộ WireGuard VPN peers trên wg-easy
        3. Thu hồi toàn bộ SSH keys trong SshKeyRegistry và dọn dẹp RAM Vault
        4. Xóa workspace controller records
        5. Dọn dẹp thư mục workspace trên đĩa (nếu remove_files=True)
        6. Xóa khỏi danh sách members và ssh_endpoints
        7. Ghi nhận Audit Log chi tiết
        """
        member = self._find_member(member_id_or_username)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id_or_username}")

        member_id = member["id"]
        username = member["username"]
        server_id = member.get("assigned_server") or settings.LOCAL_HOST_ID

        cleanup_summary = {
            "docker_container_removed": False,
            "wireguard_peers_revoked": 0,
            "ssh_keys_revoked": 0,
            "vault_purged": False,
            "workspace_files_removed": False,
        }

        # 1. Dừng và xóa Docker container ws-{username}
        if shutil.which("docker"):
            try:
                cname = f"ws-{username}"
                subprocess.run(["docker", "rm", "-f", cname], capture_output=True, timeout=10)
                cleanup_summary["docker_container_removed"] = True
            except Exception as e:
                logger.warning(f"Lỗi khi xóa container {username}: {e}")

        # 2. Thu hồi và xóa toàn bộ VPN WireGuard peers trên wg-easy
        try:
            profile = member.get("wireguard") or {}
            devices = profile.get("devices") or []
            for d in devices:
                ext_id = str(d.get("external_id") or d.get("id"))
                try:
                    self.vpn_manager.revoke_device(ext_id)
                    cleanup_summary["wireguard_peers_revoked"] += 1
                except Exception as ve:
                    logger.warning(f"Lỗi xóa peer {ext_id}: {ve}")

            # Quét dọn các client còn sót trên wg-easy có tên bắt đầu bằng username
            if hasattr(self.vpn_manager, "wg_easy"):
                for client in self.vpn_manager.wg_easy.list_devices():
                    c_name = client.get("name", "")
                    if c_name == username or c_name.startswith(f"{username}-"):
                        try:
                            self.vpn_manager.wg_easy.revoke_device(str(client["id"]))
                            cleanup_summary["wireguard_peers_revoked"] += 1
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"Lỗi dọn dẹp WireGuard cho {username}: {e}")

        # 3. Thu hồi SSH keys trong SshKeyRegistry
        try:
            keys = self.ssh_key_registry.list_keys(username=username)
            for k in keys:
                self.ssh_key_registry.revoke_key(k["id"], actor=actor, is_admin=True)
                cleanup_summary["ssh_keys_revoked"] += 1
        except Exception as e:
            logger.warning(f"Lỗi thu hồi SSH keys cho {username}: {e}")

        # 4. Dọn dẹp RAM Vault
        try:
            if hasattr(self.ssh_access_manager, "vault"):
                self.ssh_access_manager.vault._bundles.pop(member_id, None)
                self.ssh_access_manager.vault._channel_secrets.pop(member_id, None)
                cleanup_summary["vault_purged"] = True
        except Exception:
            pass

        # 5. Dọn dẹp workspace files trên đĩa
        if remove_files:
            try:
                ws_dir = self.ssh_access_manager.member_workspace_dir(server_id, username)
                if os.path.exists(ws_dir):
                    shutil.rmtree(ws_dir, ignore_errors=True)
                    if os.path.exists(ws_dir) and shutil.which("docker"):
                        parent_d = os.path.dirname(ws_dir)
                        base_n = os.path.basename(ws_dir)
                        subprocess.run(
                            ["docker", "run", "--rm", "-v", f"{parent_d}:/parent", "alpine", "rm", "-rf", f"/parent/{base_n}"],
                            capture_output=True,
                            timeout=10,
                        )
                    cleanup_summary["workspace_files_removed"] = not os.path.exists(ws_dir)
            except Exception as e:
                logger.warning(f"Lỗi xóa thư mục workspace {username}: {e}")

        # 6. Dọn dẹp workspace controller
        try:
            if hasattr(self.workspace_controller, "remove_workspace"):
                self.workspace_controller.remove_workspace(username)
        except Exception:
            pass

        # 7. Xóa khỏi ssh_endpoints và members
        self.ssh_endpoints = [ep for ep in self.ssh_endpoints if ep.get("member_id") != member_id]
        self.members = [m for m in self.members if m["id"] != member_id and m.get("username") != username]

        # 8. Ghi Audit Log
        op_id = f"op-del-{uuid.uuid4().hex[:12]}"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "MEMBER_DELETED_PERMANENTLY",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": "SUCCESS",
            "correlation_id": op_id,
            "details": f"Admin '{actor}' đã xóa vĩnh viễn tài khoản @{username} (ID: {member_id}) cùng toàn bộ container, WireGuard peers và SSH access.",
        })

        self._save_persistent_data()
        return {
            "success": True,
            "member_id": member_id,
            "username": username,
            "cleanup": cleanup_summary,
            "operation_id": op_id,
        }

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
            "wireguard": await self.wireguard_adapter.get_peer_status(),
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
            "account_security": self.ssh_access_manager.get_account_security_state(username),
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

    def provision_member_ssh_access(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Luồng Admin tạo và cấp quyền truy cập SSH tự động cho thành viên:
        1. Kiểm tra username, host, tài nguyên.
        2. Sinh cặp khóa OpenSSH Ed25519 thật được bảo vệ bằng passphrase mạnh (hoặc dùng public key nếu Admin tự dán).
        3. Cấp mật khẩu tạm riêng cho lần đăng nhập đầu tiên (PBKDF2 hash, CHANGE_REQUIRED, có hạn sử dụng).
        4. Tạo thư mục workspace riêng (0700) và .ssh/authorized_keys (0600).
        5. Kiểm chứng thực tế tại runtime (host online, cách ly thư mục, giới hạn CPU/RAM/storage, SSH listener).
        6. Chỉ chuyển sang READY khi tất cả các kiểm tra bắt buộc tại runtime đều VERIFIED.
        7. Lưu gói kết nối (encrypted private key) và bí mật (passphrase + mật khẩu tạm) vào kho bộ nhớ tạm 1 lần.
        """
        actor_id = payload.get("_actor_id") or "admin"
        enforce_canary_runtime = bool(payload.get("enforce_canary_runtime") or payload.get("enforceCanaryRuntime"))
        temp_password_ttl_seconds = int(payload.get("temp_password_ttl_seconds") or 86400)

        username = (payload.get("username") or "").strip().lower()
        validate_linux_username(username)

        existing_member = next((m for m in self.members if m.get("username") == username), None)
        if existing_member and not payload.get("allow_existing_member"):
            raise ValueError(f"Tên người dùng '{username}' đã tồn tại trong hệ thống")

        server_id = payload.get("assigned_server") or payload.get("assignedServer") or settings.LOCAL_HOST_ID
        server = next((item for item in self.servers if item["id"] == server_id), None)
        if not server:
            raise ValueError(f"Không tìm thấy máy chủ '{server_id}'")

        cpu_limit = int(payload.get("cpu_limit_cores", payload.get("cpuLimitCores", 8)))
        ram_limit = int(payload.get("ram_limit_gb", payload.get("ramLimitGb", 32)))
        storage_quota_gb = int(payload.get("storage_quota_gb", payload.get("storageQuotaGb", 250)))
        gpu_permissions = list(payload.get("gpu_permissions", payload.get("gpuPermissions", [])) or [])
        workspace_name = payload.get("workspace_name") or payload.get("workspaceName") or f"ws-{username}"

        require_passphrase = payload.get("require_passphrase")
        if require_passphrase is None:
            require_passphrase = payload.get("requirePassphrase", True)
        require_passphrase = bool(require_passphrase)

        # Tạo cặp khóa OpenSSH Ed25519 (tùy chọn có hoặc không có passphrase để kết nối thẳng 1 chạm)
        generated = self.ssh_access_manager.generate_encrypted_ed25519_keypair(
            username, workspace_name, require_passphrase=require_passphrase
        )
        public_key = generated["public_key"]
        encrypted_private_key = generated["encrypted_private_key"]
        passphrase = generated["passphrase"]

        # Tạo thư mục workspace cô lập (0700) và cài đặt public key vào authorized_keys (0600)
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
        )

        # Nếu là thành viên mới, khởi tạo bản ghi thành viên qua provision_member cơ bản (bỏ cờ provision_ssh_access)
        if not existing_member:
            base_payload = dict(payload)
            base_payload.pop("provision_ssh_access", None)
            base_payload.pop("provisionSshAccess", None)
            base_payload["ssh_public_key"] = public_key
            base_payload["ssh_key_label"] = payload.get("ssh_key_label") or payload.get("sshKeyLabel") or "Admin-Managed Ed25519 Key"
            member = self.provision_member(base_payload)
        else:
            member = existing_member
            key_rec = self.ssh_key_registry.register_key(
                username=username,
                label="Admin-Managed Ed25519 Key",
                public_key=public_key,
                actor=actor_id,
            )
            if not member.get("ssh_port"):
                member["ssh_port"] = self.get_next_available_ssh_port(server_id)
                member["ssh_ip"] = server.get("vpn_ip") or server.get("ip_address") or "192.168.167.250"
            member["ssh_fingerprint"] = key_rec["fingerprint"]
            member["ssh_public_key"] = public_key
            member["ssh_public_key_preview"] = f"{public_key[:42]}..."
            member["ssh_key_status"] = "ACTIVE"

        # Cấp mật khẩu tạm cho lần đăng nhập đầu tiên (chỉ lưu PBKDF2 hash)
        temp_password, pw_meta = self.ssh_access_manager.issue_temporary_password(
            member_id=member["id"],
            username=username,
            actor=actor_id,
            ttl_seconds=temp_password_ttl_seconds,
        )
        member["password_status"] = "CHANGE_REQUIRED"
        member["temp_password_expires_at"] = pw_meta["temp_password_expires_at"]

        host_online = self._is_host_online(server_id)
        if enforce_canary_runtime and host_online:
            canary = self.ssh_access_manager.start_canary_sshd_for_workspace(
                server_id, username, port=int(member.get("ssh_port") or 0)
            )
            member["ssh_port"] = canary["port"]
        elif server_id == settings.LOCAL_HOST_ID and shutil.which("docker"):
            try:
                cname = f"ws-{username}"
                insp = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", cname], capture_output=True, text=True)
                if insp.returncode != 0 or insp.stdout.strip() != "true":
                    subprocess.run(["docker", "rm", "-f", cname], capture_output=True)
                    work_dir = ws_fs.get("workspace_dir") or ws_fs.get("work_dir")
                    ssh_port_to_bind = int(member.get("ssh_port") or 22001)
                    subprocess.run([
                        "docker", "run", "-d",
                        "--name", cname,
                        "--restart", "unless-stopped",
                        "-p", f"{ssh_port_to_bind}:22",
                        "-v", f"{work_dir}:/workspace",
                        "-e", f"USERNAME={username}",
                        "-e", f"PUBKEY={public_key}",
                        "-e", f"TEMPPW={temp_password}",
                        "labops-workspace:latest",
                        "sh", "-c",
                        'adduser -D -s /bin/bash -h /workspace "$USERNAME" 2>/dev/null || true;'
                        'echo "$USERNAME:$TEMPPW" | chpasswd;'
                        'mkdir -p /workspace/.ssh;'
                        'echo "$PUBKEY" > /workspace/.ssh/authorized_keys;'
                        'chmod 770 /workspace;'
                        'chmod 700 /workspace/.ssh;'
                        'chmod 600 /workspace/.ssh/authorized_keys;'
                        'chown -R "$USERNAME:$USERNAME" /workspace;'
                        'chmod 770 /workspace;'
                        'cat << "EOF" > /etc/pam.d/sshd\n'
                        'auth      required   pam_unix.so nullok\n'
                        'account   required   pam_unix.so\n'
                        'password  required   pam_unix.so nullok\n'
                        'session   required   pam_unix.so\n'
                        'EOF\n'
                        'cat << "EOF" > /etc/profile.d/first_login.sh\n'
                        'if [ "$USER" != "root" ] && [ ! -f /workspace/.password_changed ]; then\n'
                        '  echo "\n==================================================================";\n'
                        '  echo "🔒 BẢO MẬT: BẮT BUỘC ĐỔI MẬT KHẨU TẠM Ở LẦN ĐĂNG NHẬP ĐẦU TIÊN";\n'
                        '  echo "==================================================================";\n'
                        '  echo "Tài khoản của bạn vừa đăng nhập bằng Mật khẩu tạm do Admin cấp.";\n'
                        '  echo "Để đảm bảo an toàn, bạn PHẢI đổi sang Mật khẩu bí mật mới ngay bây giờ.";\n'
                        '  echo "------------------------------------------------------------------";\n'
                        '  for attempt in 1 2 3; do\n'
                        '    if passwd; then\n'
                        '      touch /workspace/.password_changed;\n'
                        '      sed -i "s/PubkeyAuthentication no/PubkeyAuthentication yes/" /etc/ssh/sshd_config 2>/dev/null || true;\n'
                        '      pkill -HUP sshd 2>/dev/null || true;\n'
                        '      echo "\n🎉 ĐỔI MẬT KHẨU THÀNH CÔNG! Vui lòng đăng nhập lại bằng Mật khẩu mới hoặc Khóa SSH!\n";\n'
                        '      sleep 2; exit 0;\n'
                        '    fi\n'
                        '    echo "⚠️ Đổi mật khẩu chưa thành công. Vui lòng thử lại...";\n'
                        '  done\n'
                        '  echo "❌ Quá số lần thử. Đóng kết nối.";\n'
                        '  exit 1;\n'
                        'fi\n'
                        'EOF\n'
                        'chmod 755 /etc/profile.d/first_login.sh;'
                        'cat << "EOF" > /etc/ssh/sshd_config\n'
                        'Port 22\nListenAddress 0.0.0.0\nPasswordAuthentication yes\nPubkeyAuthentication no\nStrictModes no\nPrintMotd no\nUsePAM yes\nSubsystem sftp /usr/lib/ssh/sftp-server\n'
                        'EOF\n'
                        'if [ -x /usr/sbin/sshd.pam ]; then exec /usr/sbin/sshd.pam -D -e; else exec /usr/sbin/sshd -D -e; fi'
                    ], capture_output=True, text=True, timeout=15)
            except Exception as e:
                logger.warning(f"Không thể tự động khởi chạy Docker container cho workspace {username}: {e}")

        known_gpu_ids = [g["id"] for g in self.gpus if g.get("server_id") == server_id] + [
            g["uuid"] for g in self.gpus if g.get("server_id") == server_id and g.get("uuid")
        ]
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
        )

        overall_state = verification["overall_status"]
        # Chỉ báo READY khi TẤT CẢ kiểm tra bắt buộc đều VERIFIED
        member["workspace_status"] = "READY" if overall_state == "READY" else overall_state
        member["provisioning_state"] = "READY" if overall_state == "READY" else overall_state
        member["status"] = "READY" if overall_state == "READY" else "PENDING_PROVISIONING"
        member["ssh_status"] = "READY" if overall_state == "READY" else overall_state
        member["ssh_key_status"] = "ACTIVE" if host_online else "PENDING_SYNC"
        member["workspace_dir"] = ws_fs["workspace_dir"]
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

        ssh_host = member.get("ssh_ip") or server.get("ip_address") or "192.168.167.250"
        ssh_port = int(member.get("ssh_port") or 22001)

        # Sinh cấu hình WireGuard (.conf) theo đúng subnet hạ tầng Rack: 10.88.30.0/24
        # Quy hoạch IP: 10.88.30.1 (Server), 10.88.30.2 (Admin MacBook), từ 10.88.30.3 trở đi cấp động cho thành viên
        # WireGuard VPN Configuration: Ưu tiên tích hợp tự động qua wg-easy v15
        wg_profile = None
        if getattr(settings, "WG_EASY_ENABLED", True):
            try:
                if self.vpn_manager.primary_provider.name == "wg-easy":
                    wg_dev = self.vpn_manager.create_member_device(username, "Thiết bị chính", provider_name="wg-easy")
                    wg_conf = self.vpn_manager.get_device_configuration(str(wg_dev.get("external_id") or wg_dev["id"]), "wg-easy")
                    if wg_conf and "[Interface]" in wg_conf:
                        wg_client_ip = wg_dev.get("vpn_ip") or "10.88.40.2"
                        wg_server_endpoint = self.vpn_manager.wg_easy.public_endpoint
                        wg_profile = {
                            "wireguard_conf": wg_conf,
                            "client_ip": wg_client_ip,
                            "wg_public_key": wg_dev.get("public_key") or "",
                            "server_endpoint": wg_server_endpoint,
                            "allowed_ips": "10.88.40.0/24, 192.168.167.250/32",
                        }
                        wg_profile_data = member.setdefault("wireguard", {"status": "ACTIVE", "devices": []})
                        wg_profile_data.setdefault("devices", []).append({
                            "device_id": f"dev-{wg_dev.get('external_id') or wg_dev['id']}",
                            "external_id": wg_dev.get("external_id") or wg_dev["id"],
                            "provider": "wg-easy",
                            "name": "Thiết bị chính",
                            "client_ip": wg_client_ip,
                            "vpn_ip": wg_client_ip,
                            "public_key": wg_dev.get("public_key"),
                            "server_id": "track",
                            "server_public_key": wg_dev.get("server_public_key") or self.vpn_manager.get_server_public_key(),
                            "server_endpoint": wg_server_endpoint,
                            "allowed_subnets": "10.88.40.0/24",
                            "status": "ACTIVE",
                            "enabled": True,
                            "enrolled_at": datetime.now(timezone.utc).isoformat(),
                        })
            except Exception as e:
                logger.error(f"Lỗi tạo peer wg-easy tự động: {e}")

        if wg_profile is None or not (wg_profile.get("wireguard_conf") and "[Interface]" in wg_profile["wireguard_conf"]):
            # Dự phòng an toàn theo chuẩn wg-easy subnet 10.88.40.0/24 & port 2205
            wg_client_ip = (member.get("wireguard") or {}).get("client_ip") or "10.88.40.50"
            wg_server_endpoint = getattr(self.vpn_manager.wg_easy, "public_endpoint", "118.70.169.236:2204")
            wg_server_pubkey = self.vpn_manager.get_server_public_key()
            wg_profile = self.ssh_access_manager.generate_wireguard_client_profile(
                username=username,
                client_ip=str(wg_client_ip).split("/")[0],
                server_endpoint=wg_server_endpoint,
                server_public_key=wg_server_pubkey,
                allowed_ips="10.88.40.0/24, 192.168.167.250/32",
            )
        member["wireguard"] = {
            **(member.get("wireguard") or {}),
            "client_ip": wg_profile["client_ip"],
            "vpn_ip": wg_profile["client_ip"],
            "public_key": wg_profile["wg_public_key"],
            "server_endpoint": wg_profile["server_endpoint"],
            "allowed_subnets": wg_profile["allowed_ips"],
            "status": (member.get("wireguard") or {}).get("status") or "NOT_VERIFIED",
        }

        vpn_guide = (
            f"1. [VPN WireGuard] Mở ứng dụng WireGuard -> 'Import tunnel(s) from file' -> chọn tệp '{username}-labops-wg0.conf' -> Bật kết nối (Activate).\n"
            f"2. [Tự động cài SSH Config] Chạy lệnh: bash setup-ssh-{username}.sh (tự động cài khóa riêng và ~/.ssh/config).\n"
            f"3. [Kết nối SSH nhanh] Gõ trực tiếp: ssh labops-{username} (hoặc ssh -i ~/.ssh/id_ed25519_{username} -p {ssh_port} {username}@{ssh_host}).\n"
            f"4. Nhập Passphrase mở khóa riêng (do Admin gửi qua kênh riêng) và đổi mật khẩu tạm ở lần đăng nhập đầu tiên."
        )

        vault_info = self.ssh_access_manager.vault.store_provisioned_package(
            member_id=member["id"],
            username=username,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            workspace_name=workspace_name,
            workspace_path=ws_fs["work_dir"],
            fingerprint=generated["fingerprint"],
            encrypted_private_key=encrypted_private_key,
            passphrase=passphrase,
            temporary_password=temp_password,
            temp_password_expires_at=pw_meta["temp_password_expires_at"],
            vpn_guide=vpn_guide,
            wireguard_conf=wg_profile["wireguard_conf"],
            wireguard_client_ip=wg_profile["client_ip"],
            wireguard_public_key=wg_profile["wg_public_key"],
            wireguard_server_endpoint=wg_profile["server_endpoint"],
        )
        member["one_time_package"] = vault_info

        op_id = f"op-prov-{uuid.uuid4().hex[:12]}"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor_id,
            "action": "SSH_ACCESS_PROVISIONED",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": overall_state,
            "correlation_id": op_id,
            "details": (
                f"Đã khởi tạo cặp khóa Ed25519 mã hóa passphrase ({generated['fingerprint']}) và mật khẩu tạm lần đầu "
                f"cho @{username} trên host '{server_id}' (cổng {ssh_port}). Trạng thái xác minh runtime: {overall_state}."
            ),
        })
        self._save_persistent_data()
        return member

    def rotate_member_ssh_keypair(
        self, member_id_or_username: str, actor: str, require_passphrase: bool = False
    ) -> Dict[str, Any]:
        """
        Cấp lại cặp khóa SSH mới (khi khóa cũ bị mất hoặc lộ) và thu hồi toàn bộ khóa cũ của thành viên.
        Mặc định require_passphrase = False để kết nối thẳng 1 chạm không cần nhập mật khẩu.
        """
        member = self._find_member(member_id_or_username)
        username = member["username"]
        server_id = member.get("assigned_server") or settings.LOCAL_HOST_ID

        # Thu hồi các khóa cũ trong SshKeyRegistry
        for old_key in self.ssh_key_registry.list_keys(username=username):
            if old_key.get("desired_state") != "REVOKED":
                self.ssh_key_registry.revoke_key(old_key["id"], actor=actor, is_admin=True)

        # Tạo cặp khóa mới và ghi đè authorized_keys của workspace
        updated = self.provision_member_ssh_access({
            "username": username,
            "full_name": member.get("full_name", username),
            "email": member.get("email", f"{username}@lab.ai.vn"),
            "role": member.get("role", "RESEARCHER"),
            "assigned_server": server_id,
            "workspace_name": member.get("workspace_name") or f"ws-{username}",
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
        Cấp lại mật khẩu tạm mới cho thành viên (vô hiệu hóa ngay lập tức mật khẩu tạm cũ).
        """
        member = self._find_member(member_id_or_username)
        username = member["username"]
        temp_password, pw_meta = self.ssh_access_manager.issue_temporary_password(
            member_id=member["id"],
            username=username,
            actor=actor,
            ttl_seconds=ttl_seconds,
        )
        member["password_status"] = "CHANGE_REQUIRED"
        member["temp_password_expires_at"] = pw_meta["temp_password_expires_at"]
        vault_meta = self.ssh_access_manager.vault.store_reissued_temp_password(
            member_id=member["id"],
            username=username,
            temporary_password=temp_password,
            temp_password_expires_at=pw_meta["temp_password_expires_at"],
        )
        member["one_time_package"] = self.ssh_access_manager.vault.get_availability(member["id"])
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
            "password_status": "CHANGE_REQUIRED",
            "temp_password_expires_at": pw_meta["temp_password_expires_at"],
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
        server_id = member.get("assigned_server") or settings.LOCAL_HOST_ID
        server = next((s for s in self.servers if s["id"] == server_id), {})
        ssh_host = member.get("ssh_ip") or server.get("ip_address") or "192.168.167.250"
        ssh_port = int(member.get("ssh_port") or 22001)
        public_key = member.get("ssh_public_key") or ""

        # Lấy thông tin gói 1 lần nếu còn trong vault
        bundle = {}
        try:
            bundle = self.ssh_access_manager.vault.claim_key_bundle_once(member["id"], peek=True)
        except Exception:
            pass

        channel_secrets = self.ssh_access_manager.vault._channel_secrets.get(member["id"]) or {}
        key_passphrase = channel_secrets.get("key_passphrase")
        temp_pw = channel_secrets.get("temporary_password")
        temp_pw_exp = channel_secrets.get("temp_password_expires_at") or member.get("temp_password_expires_at")

        priv_key = bundle.get("encrypted_private_key")
        wg_conf = bundle.get("wireguard_conf")
        wg_client_ip = bundle.get("wireguard_client_ip") or (member.get("wireguard") or {}).get("client_ip")
        wg_ep = bundle.get("wireguard_server_endpoint") or (member.get("wireguard") or {}).get("server_endpoint")

        if not wg_conf:
            # 1. Thử lấy cấu hình từ thiết bị wg-easy đã đăng ký
            profile = member.get("wireguard") or {}
            devices = profile.get("devices") or []
            wg_easy_dev = next((d for d in devices if d.get("provider") == "wg-easy" and d.get("status") == "ACTIVE"), None)
            if wg_easy_dev:
                try:
                    ext_id = str(wg_easy_dev.get("external_id") or wg_easy_dev.get("id"))
                    wg_conf = self.vpn_manager.get_device_configuration(ext_id, "wg-easy")
                    wg_client_ip = wg_easy_dev.get("client_ip") or wg_easy_dev.get("vpn_ip")
                    wg_ep = self.vpn_manager.wg_easy.public_endpoint
                except Exception as e:
                    logger.warning(f"Không thể lấy tệp cấu hình wg-easy cho device {wg_easy_dev}: {e}")

            # 2. Nếu chưa có cấu hình, sinh profile dự phòng
            if not wg_conf:
                wg_server_pubkey = (
                    os.getenv("LABOPS_WG_SERVER_PUBLIC_KEY")
                    or (member.get("wireguard") or {}).get("server_public_key")
                    or "jf+Uw/DFyi5swclp7mhH9mamYjQgV3ulZRlxBIdqlmQ="
                )
                prof = self.ssh_access_manager.generate_wireguard_client_profile(
                    username=username,
                    client_ip=str(wg_client_ip or "10.88.40.2").split("/")[0],
                    server_endpoint=wg_ep or getattr(self.vpn_manager.wg_easy, "public_endpoint", "118.70.169.236:2204"),
                    server_public_key=wg_server_pubkey,
                    allowed_ips="10.88.40.0/24, 192.168.167.250/32",
                )
                wg_conf = prof["wireguard_conf"]
                wg_client_ip = prof["client_ip"]

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
            key_passphrase=key_passphrase,
            temporary_password=temp_pw,
            temp_password_expires_at=temp_pw_exp,
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
        Chỉ báo VERIFIED_REVOKED khi đã gỡ khóa khỏi authorized_keys của workspace và xác minh thành công.
        Nếu host offline hoặc không kiểm chứng được, giữ ở trạng thái PENDING_REVOKE hoặc FAILED.
        """
        member = self._find_member(member_id_or_username)
        server_id = member.get("assigned_server") or settings.LOCAL_HOST_ID
        host_online = self._is_host_online(server_id)

        res = self.ssh_access_manager.revoke_member_ssh(
            member=member,
            host_online=host_online,
            actor=actor,
            encrypted_private_key_for_probe=encrypted_private_key_for_probe,
            passphrase_for_probe=passphrase_for_probe,
        )
        member["ssh_key_status"] = res["ssh_key_status"]
        member["ssh_status"] = res["status"]
        member["provisioning_state"] = res["status"]
        member["password_status"] = "REVOKED"
        member["one_time_package"] = self.ssh_access_manager.vault.get_availability(member["id"])
        if res["status"] == "VERIFIED_REVOKED":
            member["status"] = "SUSPENDED"
        else:
            member["status"] = res["status"]

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
            )
            member["password_status"] = "ACTIVE"
            self.audit_logs.insert(0, {
                "id": f"aud-{uuid.uuid4().hex[:12]}",
                "actor": member["username"],
                "action": "FIRST_LOGIN_PASSWORD_CHANGED",
                "target_type": "MEMBER",
                "target_id": member["username"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "result": "ACTIVE",
                "correlation_id": f"op-pwchg-{uuid.uuid4().hex[:12]}",
                "details": f"Thành viên @{member['username']} đã đổi mật khẩu tạm lần đầu sang mật khẩu chính thức; mật khẩu tạm đã hết hiệu lực.",
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
        res = self.ssh_access_manager.verify_workspace_password_login(member["username"], password)
        member["password_status"] = res["password_status"]
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
            "ssh_fingerprint": member.get("ssh_fingerprint"),
            "password_status": member.get("password_status"),
            "runtime_verification": member.get("runtime_verification"),
            "one_time_package": self.ssh_access_manager.vault.get_availability(member["id"]),
            "account_security": self.ssh_access_manager.get_account_security_state(username),
        }

    # ------------------------------------------------------------------
    # VPN Administration & Multi-Device Management (wg-easy v15)
    # ------------------------------------------------------------------
    def get_vpn_system_overview(self) -> Dict[str, Any]:
        return self.vpn_manager.get_vpn_system_overview()

    def list_all_vpn_devices(self) -> List[Dict[str, Any]]:
        return self.vpn_manager.primary_provider.list_devices()

    def get_member_devices_synced(self, member_id: str) -> List[Dict[str, Any]]:
        member = self._find_member(member_id)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id}")
        profile = member.get("wireguard") or {}
        devices = list(profile.get("devices") or [])
        try:
            live_wg_clients = {str(c["id"]): c for c in self.vpn_manager.wg_easy.list_devices()}
            for d in devices:
                ext_id = str(d.get("external_id") or d.get("id"))
                if ext_id in live_wg_clients:
                    live = live_wg_clients[ext_id]
                    d["status"] = live.get("status", d.get("status"))
                    d["enabled"] = live.get("enabled", d.get("enabled"))
                    d["last_handshake"] = live.get("last_handshake")
                    d["transfer_rx"] = live.get("transfer_rx", 0)
                    d["transfer_tx"] = live.get("transfer_tx", 0)
        except Exception as e:
            logger.debug(f"Không thể đồng bộ live handshake từ wg-easy: {e}")
        return devices

    def add_member_device(self, member_id: str, device_name: str, actor: str = "admin") -> Dict[str, Any]:
        member = self._find_member(member_id)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id}")
        username = member["username"]
        dev = self.vpn_manager.create_member_device(username, device_name)
        ext_id = str(dev.get("external_id") or dev.get("id"))
        clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", ext_id)
        device_entry = {
            "device_id": f"dev-{clean_id}",
            "external_id": dev.get("external_id") or dev["id"],
            "provider": dev.get("provider", "wg-easy"),
            "name": device_name,
            "client_ip": dev.get("vpn_ip", ""),
            "vpn_ip": dev.get("vpn_ip", ""),
            "public_key": dev.get("public_key", ""),
            "server_id": "track",
            "server_public_key": dev.get("server_public_key") or self.vpn_manager.get_server_public_key(),
            "server_endpoint": dev.get("server_endpoint") or self.vpn_manager.wg_easy.public_endpoint,
            "allowed_subnets": dev.get("allowed_subnets") or "10.88.40.0/24",
            "status": "ACTIVE",
            "enabled": True,
            "enrolled_at": datetime.now(timezone.utc).isoformat(),
        }
        profile = member.setdefault("wireguard", {"status": "ACTIVE", "devices": []})
        profile.setdefault("devices", []).append(device_entry)
        profile["status"] = "ACTIVE"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "WIREGUARD_DEVICE_REGISTERED",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": "SUCCESS",
            "details": f"Đã đăng ký thiết bị mới '{device_name}' (IP VPN: {dev.get('vpn_ip')}) cho @{username} qua wg-easy.",
        })
        self._save_persistent_data()
        return device_entry

    def get_member_device_config(self, member_id: str, device_id: str) -> str:
        member = self._find_member(member_id)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id}")
        profile = member.get("wireguard") or {}
        devices = profile.get("devices") or []
        dev = next((d for d in devices if d.get("device_id") == device_id or str(d.get("external_id")) == device_id or str(d.get("id")) == device_id), None)
        if not dev:
            raise LookupError(f"Không tìm thấy thiết bị: {device_id}")
        ext_id = str(dev.get("external_id") or dev.get("id") or device_id)
        return self.vpn_manager.get_device_configuration(ext_id, dev.get("provider", "wg-easy"))

    def revoke_member_device_v2(self, member_id: str, device_id: str, actor: str = "admin") -> Dict[str, Any]:
        member = self._find_member(member_id)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên: {member_id}")
        profile = member.get("wireguard") or {}
        devices = profile.get("devices") or []
        dev = next((d for d in devices if d.get("device_id") == device_id or str(d.get("external_id")) == device_id or str(d.get("id")) == device_id), None)
        if not dev:
            raise LookupError(f"Không tìm thấy thiết bị: {device_id}")
        ext_id = str(dev.get("external_id") or dev.get("id") or device_id)
        self.vpn_manager.revoke_device(ext_id, dev.get("provider", "wg-easy"))
        dev["status"] = "REVOKED"
        dev["enabled"] = False
        profile["devices"] = [d for d in devices if d.get("device_id") != dev.get("device_id")]
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}",
            "actor": actor,
            "action": "WIREGUARD_DEVICE_REVOKED",
            "target_type": "MEMBER",
            "target_id": member["username"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": "SUCCESS",
            "details": f"Đã thu hồi thiết bị '{dev.get('name')}' của @{member['username']} khỏi WireGuard VPN.",
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
            "details": f"{'Kích hoạt' if enable else 'Tạm ngưng'} thiết bị '{dev.get('name')}' của @{member['username']}.",
        })
        self._save_persistent_data()
        return dev

service = LabOpsDataService()
