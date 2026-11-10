import uuid
import re
import hashlib
import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from app.core.config import settings
from app.adapters.host_monitor_adapter import HostMonitorAdapter
from app.adapters.nvml_adapter import NvmlAdapter
from app.services.gpu_scheduler import GpuScheduler, SchedulerError
from app.services.security_scanner import SecurityScanner
from app.services.host_registry import HostRegistry
from app.adapters.node_agent_adapter import NodeAgentClient
from app.adapters.arcane_adapter import ArcaneAdapter
from app.adapters.beszel_adapter import BeszelAdapter
from app.services.workspace_controller import WorkspaceController
from app.services.vpn_ssh_service import SshKeyRegistry
from app.adapters.process_monitor_adapter import ProcessMonitorAdapter
from app.adapters.wireguard_adapter import WireGuardAdapter
from app.adapters.teleport_adapter import TeleportAdapter

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
        self.process_monitor = ProcessMonitorAdapter(server_id="workstation", demo_mode=self.demo_mode)
        self.data_dir = os.path.abspath(data_dir or os.path.join(os.path.dirname(__file__), "..", "..", "data"))
        os.makedirs(self.data_dir, exist_ok=True)
        self.wireguard_adapter = WireGuardAdapter(interface="wg0", data_dir=self.data_dir)
        self.gpu_scheduler = GpuScheduler(os.path.join(self.data_dir, "gpu_reservations.json"),
                                          self.nvml_adapter.get_live_gpu_telemetry_sync)
        self.security_scanner = SecurityScanner(os.path.join(self.data_dir, "security_scans.json"))
        self.host_registry = HostRegistry(self.data_dir, local_host_id=settings.LOCAL_HOST_ID)
        self.node_agent = NodeAgentClient(local_host_id=settings.LOCAL_HOST_ID)
        self.arcane_adapter = ArcaneAdapter()
        self.beszel_adapter = BeszelAdapter()
        self.workspace_controller = WorkspaceController(self.data_dir)
        self.ssh_key_registry = SshKeyRegistry(self.data_dir)
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
                root_u = psutil.disk_usage("/")
                pools = [
                    {
                        "id": "pool-root",
                        "server_id": "workstation",
                        "server_name": "Server 02 — Workstation (Cục bộ)",
                        "name": "pool-root",
                        "label": "Ổ đĩa Hệ thống (LVM Ext4 /)",
                        "disk_identifier": "/dev/mapper/ubuntu--vg-ubuntu--lv",
                        "mount_point": "/",
                        "driver": "dir",
                        "capacity_gb": round(root_u.total / (1024**3), 1),
                        "used_gb": round(root_u.used / (1024**3), 1),
                        "free_gb": round(root_u.free / (1024**3), 1),
                        "health_status": "HEALTHY",
                        "is_default": True
                    }
                ]
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
        if server_id == "workstation":
            return self.process_monitor.get_real_processes(filter_type=filter_type)
        return {
            "source_host": "track",
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
        if server_id == "workstation":
            return self.process_monitor.get_real_ssh_sessions()
        return {
            "source_host": "track",
            "collection_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "collector_status": "NOT_CONFIGURED",
            "total_count": 0,
            "actual_observations": [],
            "error_details": "Remote SSH session collector is not configured; connection state is not verified."
        }

    def get_workspace_processes(self, workspace_id: str) -> Dict[str, Any]:
        all_procs = self.get_server_processes("workstation", filter_type="all")
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
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "PRODUCTION",
            "collectors": {
                "host_monitor": "CONTAINER_ONLY" if self.host_monitor.is_containerized else "LIVE",
                "nvml": gpu_status,
                "process_monitor": process_status,
                "ssh_sessions": "NOT_CONFIGURED",
                "incus": "NOT_CONFIGURED",
                "wireguard_track": "NOT_VERIFIED"
            }
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

    def get_next_available_ssh_port(self, server_id: str) -> int:
        """Find lowest free port in range 22000-29999 for given server"""
        used_ports = {
            ep["listen_port"] for ep in self.ssh_endpoints if ep["server_id"] == server_id
        }
        for port in range(22001, 29999):
            if port not in used_ports:
                return port
        raise ValueError(f"Không còn cổng SSH khả dụng trên máy chủ {server_id}")

    def provision_member(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Record an access request; external workspace and identity services must verify activation."""
        username = payload.get("username", "")
        validate_linux_username(username)
        if any(member.get("username") == username for member in self.members):
            raise ValueError(f"Tên người dùng '{username}' đã tồn tại trong hệ thống")

        email = payload.get("email", "")
        if not isinstance(email, str) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Email không hợp lệ")
        full_name = payload.get("full_name") or payload.get("fullName") or ""
        if not isinstance(full_name, str) or not full_name.strip() or len(full_name.strip()) > 120:
            raise ValueError("Tên hiển thị phải có từ 1 đến 120 ký tự")
        role = payload.get("role", "RESEARCHER")
        if role not in {"RESEARCHER", "ADMIN"}:
            raise ValueError("Role chỉ được là RESEARCHER hoặc ADMIN")

        server_id = payload.get("assigned_server") or payload.get("assignedServer") or "workstation"
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

        member_id = f"mem-{uuid.uuid4().hex[:12]}"
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
            "provisioning_state": "PENDING",
            "workspace_status": "NOT_VERIFIED",
            "teleport_status": "NOT_CONFIGURED",
            "storage_pool_id": pool["id"] if pool else None,
            "storage_pool_label": pool["label"] if pool else "NOT_VERIFIED",
            "mount_path": mount_path,
            "storage_quota_gb": storage_quota_gb,
            "storage_used_gb": 0.0,
            "backup_policy": payload.get("backup_policy", payload.get("backupPolicy", "DAILY_SNAPSHOT")),
            "shared_datasets": payload.get("shared_datasets", payload.get("sharedDatasets", ["/shared/datasets"])),
            "ssh_status": "OFFLINE",
            "ssh_sessions_count": 0,
            "ssh_ip": None,
            "ssh_port": None,
            "ssh_key_status": "NOT_CONFIGURED",
            "ssh_fingerprint": None,
            "ssh_public_key_preview": "Teleport certificate pending",
            "ssh_public_key": None,
            "ssh_auth_mode": "TELEPORT_CERTIFICATE_PENDING",
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
                "verification_timestamp": now,
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
            "actor": payload.get("_actor_id", "labops-api"),
            "action": "ACCESS_REQUEST_CREATED",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": now,
            "result": "PENDING",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Access request recorded for workspace {workspace_name} on {server_id}; provisioning is not verified.",
        })
        self._save_persistent_data()
        return member

    def register_wireguard_device(self, member_id: str, public_key: str, device_name: str, actor: str = "labops-api") -> Dict[str, Any]:
        member = next((item for item in self.members if item["id"] == member_id), None)
        if not member:
            raise ValueError("Member not found")
        if member.get("workspace_status") != "VERIFIED":
            raise RuntimeError("Workspace provisioning is not verified")
        if self._network_policy_status() != "VERIFIED":
            raise RuntimeError("VPN-to-Teleport network policy is not verified")
        if not isinstance(device_name, str) or not re.fullmatch(r"[A-Za-z0-9 _.-]{1,64}", device_name):
            raise ValueError("Device name is invalid")
        device_id = f"dev-{uuid.uuid4().hex}"
        try:
            peer = self.wireguard_adapter.register_peer(
                username=member["username"],
                device_id=device_id,
                public_key=public_key,
                server_id=member["assigned_server"],
                existing_members=self.members,
            )
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise
            raise RuntimeError("WireGuard device registration failed") from exc
        device = {**peer, "name": device_name}
        profile = member.setdefault("wireguard", {"status": "NOT_CONFIGURED", "devices": []})
        profile.setdefault("devices", []).append(device)
        profile["status"] = "PENDING_HANDSHAKE"
        audit = {
            "id": f"aud-{uuid.uuid4().hex[:12]}", "actor": actor,
            "action": "WIREGUARD_DEVICE_REGISTERED", "target_type": "MEMBER", "target_id": member["username"],
            "timestamp": datetime.now().isoformat(timespec="seconds"), "result": "PENDING_HANDSHAKE",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Client public key registered for device {device_id}; handshake is not yet verified.",
        }
        self.audit_logs.insert(0, audit)
        try:
            self._save_persistent_data()
        except RuntimeError as exc:
            try:
                self.wireguard_adapter.revoke_peer(device_id)
            except Exception as rollback_error:
                raise RuntimeError("Member persistence failed; WireGuard rollback needs manual verification") from rollback_error
            profile["devices"].remove(device)
            if self.audit_logs and self.audit_logs[0] is audit:
                self.audit_logs.pop(0)
            raise exc
        return device

    def revoke_wireguard_device(self, member_id: str, device_id: str, actor: str = "labops-api") -> Dict[str, Any]:
        member = next((item for item in self.members if item["id"] == member_id), None)
        if not member:
            raise ValueError("Member not found")
        profile = member.get("wireguard") or {}
        device = next((item for item in profile.get("devices", []) if item.get("device_id") == device_id), None)
        if not device:
            raise ValueError("WireGuard device not found")
        try:
            if not self.wireguard_adapter.revoke_peer(device_id):
                raise RuntimeError("WireGuard peer record was not found")
        except Exception as exc:
            raise RuntimeError("WireGuard device revocation was not verified") from exc
        device["status"] = "REVOKED"
        profile["status"] = "REVOKED" if all(item.get("status") == "REVOKED" for item in profile.get("devices", [])) else "PENDING_HANDSHAKE"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:12]}", "actor": actor,
            "action": "WIREGUARD_DEVICE_REVOKED", "target_type": "MEMBER", "target_id": member["username"],
            "timestamp": datetime.now().isoformat(timespec="seconds"), "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"WireGuard device {device_id} was removed from the configured live interface.",
        })
        self._save_persistent_data()
        return device

    def _revoke_member_wireguard(self, member: Dict[str, Any]) -> Dict[str, Any]:
        profile = member.get("wireguard") or {}
        devices = profile.get("devices", [])
        if not devices and profile.get("public_key"):
            profile["status"] = "PARTIAL_FAILURE"
            raise RuntimeError("Legacy WireGuard peer identity is not mapped to a device; revoke requires manual verification")
        for device in devices:
            if device.get("status") == "REVOKED":
                continue
            try:
                if not self.wireguard_adapter.revoke_peer(device["device_id"]):
                    raise RuntimeError("peer record not found")
                device["status"] = "REVOKED"
            except Exception as exc:
                profile["status"] = "PARTIAL_FAILURE"
                member["provisioning_state"] = "PARTIAL_FAILURE"
                self._save_persistent_data()
                raise RuntimeError("WireGuard revocation is incomplete; access state needs review") from exc
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

    def delete_member_permanently(self, member_id: str) -> Dict[str, Any]:
        raise RuntimeError("Permanent deletion is disabled in Secure Access migration; user data is retained")

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
        devices = (member.get("wireguard") or {}).get("devices", [])
        for device in devices:
            status = self.wireguard_adapter.get_device_status(device["device_id"])
            device.update(status)
        profile = member.get("wireguard") or {}
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
        }

service = LabOpsDataService()
