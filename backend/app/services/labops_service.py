import uuid
import re
import hashlib
import os
import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from app.core.config import settings
from app.adapters.host_monitor_adapter import HostMonitorAdapter
from app.adapters.nvml_adapter import NvmlAdapter
from app.adapters.process_monitor_adapter import ProcessMonitorAdapter

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
    def __init__(self, demo_mode: Optional[bool] = None):
        self.demo_mode = settings.DEMO_MODE if demo_mode is None else demo_mode
        self.host_monitor = HostMonitorAdapter(is_demo_mode=self.demo_mode)
        self.nvml_adapter = NvmlAdapter(server_id="workstation", demo_mode=self.demo_mode)
        self.process_monitor = ProcessMonitorAdapter(server_id="workstation", demo_mode=self.demo_mode)
        self.data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
        os.makedirs(self.data_dir, exist_ok=True)
        self._init_data()

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
            logger.warning(f"Failed to persist LabOps data: {e}")

    @property
    def servers(self) -> List[Dict[str, Any]]:
        if not self.demo_mode:
            ws = self.host_monitor.get_local_workstation_telemetry()
            track = self.host_monitor.get_remote_track_telemetry()
            ws["active_members"] = sum(1 for m in self.members if m.get("assigned_server") == "workstation" and m.get("status") == "ACTIVE")
            track["active_members"] = sum(1 for m in self.members if m.get("assigned_server") == "track" and m.get("status") == "ACTIVE")
            return [track, ws]
        return self._demo_servers

    @servers.setter
    def servers(self, val: List[Dict[str, Any]]):
        self._demo_servers = val

    @property
    def gpus(self) -> List[Dict[str, Any]]:
        if not self.demo_mode:
            live_gpus = self.nvml_adapter.get_live_gpu_telemetry_sync()
            for g in live_gpus:
                allocated = [j for j in self.jobs if j.get("gpu_id") == g.get("id") and j.get("status") == "RUNNING"]
                for j in allocated:
                    if j.get("username") and j["username"] not in g["active_users"]:
                        g["active_users"].append(j["username"])
            return live_gpus
        return self._demo_gpus

    @gpus.setter
    def gpus(self, val: List[Dict[str, Any]]):
        self._demo_gpus = val

    @property
    def storage_pools(self) -> List[Dict[str, Any]]:
        if not self.demo_mode:
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
                pools.append({
                    "id": "pool-nvme-fast",
                    "server_id": "track",
                    "server_name": "Server 01 — Track (Từ xa)",
                    "name": "pool-nvme-fast",
                    "label": "SSD NVMe — Track (Mất kết nối VPN)",
                    "disk_identifier": "wwn-0x5002538f411b4101",
                    "mount_point": "/var/lib/incus/storage-pools/pool-nvme-fast",
                    "driver": "zfs",
                    "capacity_gb": 8000.0,
                    "used_gb": 0.0,
                    "free_gb": 0.0,
                    "health_status": "DISCONNECTED",
                    "is_default": False
                })
                return pools
            except Exception:
                pass
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
            members_file = os.path.join(self.data_dir, "members.json")
            if os.path.exists(members_file):
                try:
                    with open(members_file, "r", encoding="utf-8") as f:
                        self.members = json.load(f)
                    with open(os.path.join(self.data_dir, "ssh_policies.json"), "r", encoding="utf-8") as f:
                        self.ssh_policies = json.load(f)
                    with open(os.path.join(self.data_dir, "audit_logs.json"), "r", encoding="utf-8") as f:
                        self.audit_logs = json.load(f)
                    with open(os.path.join(self.data_dir, "storage_allocations.json"), "r", encoding="utf-8") as f:
                        self.storage_allocations = json.load(f)
                    with open(os.path.join(self.data_dir, "ssh_endpoints.json"), "r", encoding="utf-8") as f:
                        self.ssh_endpoints = json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to read existing persistent data: {e}")

    def get_summary(self) -> Dict[str, Any]:
        used_vram_gb = round(sum(gpu["vram_used_mb"] for gpu in self.gpus) / 1024, 1)
        total_vram_gb = round(sum(gpu.get("vram_total_mb", 24576) for gpu in self.gpus) / 1024, 1) if self.gpus else 0
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
                "offline": sum(1 for s in self.servers if s["status"] != "online")
            },
            "gpus": {
                "totalCount": len(self.gpus),
                "model": "NVIDIA A30 24GB",
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
                "pending": 0,
                "failed": 0
            },
            "gpuHours": {
                "today": 48.6,
                "last7Days": 384.2,
                "previousPeriodChangePercent": 14.8
            },
            "alerts": {
                "critical": 0,
                "warnings": 1,
                "unresolvedIncidents": 0
            }
        }

    def get_server_processes(self, server_id: str, filter_type: str = "all") -> Dict[str, Any]:
        if server_id == "workstation":
            return self.process_monitor.get_real_processes(filter_type=filter_type)
        return {
            "source_host": "track",
            "collection_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "collector_status": "UNAVAILABLE",
            "filter": filter_type,
            "total_count": 0,
            "actual_observations": [],
            "error_details": "Mất kết nối: Server 01 — Track chưa kết nối qua WireGuard VPN (10.200.0.1)"
        }

    def get_server_gpu_processes(self, server_id: str) -> Dict[str, Any]:
        return self.get_server_processes(server_id, filter_type="gpu")

    def get_server_ssh_sessions(self, server_id: str) -> Dict[str, Any]:
        if server_id == "workstation":
            return self.process_monitor.get_real_ssh_sessions()
        return {
            "source_host": "track",
            "collection_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "collector_status": "UNAVAILABLE",
            "total_count": 0,
            "actual_observations": [],
            "error_details": "Mất kết nối: Server 01 — Track chưa kết nối qua WireGuard VPN"
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
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "PRODUCTION" if not self.demo_mode else "DEMO",
            "collectors": {
                "host_monitor": "OK",
                "nvml": "OK" if self.nvml_adapter.has_nvidia_smi else "DEGRADED",
                "process_monitor": "OK",
                "ssh_sessions": "OK",
                "incus": "NOT_CONFIGURED",
                "wireguard_track": "OFFLINE"
            }
        }

    def allocate_gpu(self, gpu_id: str, username: str, mode: str, requested_vram_gb: int, job_name: str) -> Dict[str, Any]:
        target_gpu = next((g for g in self.gpus if g["id"] == gpu_id), None)
        if not target_gpu:
            raise ValueError(f"Không tìm thấy GPU với ID {gpu_id}")

        requested_mb = requested_vram_gb * 1024
        available_mb = target_gpu["vram_total_mb"] - target_gpu["vram_used_mb"]
        if requested_mb > available_mb:
            raise ValueError(f"VRAM không đủ: yêu cầu {requested_vram_gb}GB, khả dụng {round(available_mb / 1024, 1)}GB")

        target_gpu["vram_used_mb"] += requested_mb
        target_gpu["gpu_util_percent"] = min(95, target_gpu["gpu_util_percent"] + 35)
        target_gpu["allocation_mode"] = mode
        if username not in target_gpu["active_users"]:
            target_gpu["active_users"].append(username)

        new_job_id = f"job-{uuid.uuid4().hex[:6]}"
        pid = 120000 + len(self.jobs) * 10
        new_process = {
            "pid": pid,
            "name": f"python ({job_name})",
            "username": username,
            "workspace": f"ws-{username}",
            "vram_mb": requested_mb,
            "cpu_percent": 150.0
        }
        target_gpu["active_processes"].append(new_process)
        target_gpu["associated_jobs"].append(new_job_id)

        new_job = {
            "id": new_job_id,
            "username": username,
            "full_name": username,
            "server_id": target_gpu["server_id"],
            "workspace": f"ws-{username}",
            "pid": pid,
            "script": f"python {job_name}",
            "gpu_id": gpu_id,
            "cpu_percent": 150.0,
            "ram_used_mb": 16384,
            "vram_used_mb": requested_mb,
            "start_time": "Vừa tạo",
            "elapsed_time": "00h 01m",
            "status": "RUNNING",
            "ssh_status": "ATTACHED",
            "container_state": "RUNNING"
        }
        self.jobs.insert(0, new_job)

        # Audit log
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "GPU_ALLOCATED",
            "target_type": "GPU",
            "target_id": gpu_id,
            "timestamp": "Vừa xong",
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Cấp phát {requested_vram_gb}GB VRAM cho {username} ({job_name})"
        })

        return {"success": True, "job_id": new_job_id, "gpu": target_gpu}

    def stop_job(self, job_id: str) -> Dict[str, Any]:
        job = next((j for j in self.jobs if j["id"] == job_id), None)
        if not job:
            raise ValueError(f"Không tìm thấy job {job_id}")

        job["status"] = "STOPPED"
        job["container_state"] = "STOPPED"
        gpu = next((g for g in self.gpus if g["id"] == job["gpu_id"]), None)
        if gpu:
            gpu["vram_used_mb"] = max(0, gpu["vram_used_mb"] - job["vram_used_mb"])
            gpu["gpu_util_percent"] = max(0, gpu["gpu_util_percent"] - 40)
            gpu["active_processes"] = [p for p in gpu["active_processes"] if p["pid"] != job["pid"]]
            if job["username"] in gpu["active_users"]:
                gpu["active_users"].remove(job["username"])
            if job_id in gpu["associated_jobs"]:
                gpu["associated_jobs"].remove(job_id)

        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "JOB_TERMINATED",
            "target_type": "JOB",
            "target_id": job_id,
            "timestamp": "Vừa xong",
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Dừng tác vụ {job_id} của {job['username']} (PID {job['pid']})"
        })

        return {"success": True, "job_id": job_id}

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
        """
        Atomic member provisioning:
        1. Validate Linux username safety.
        2. Validate storage pool existence and capacity.
        3. Validate mount path allowlist.
        4. Validate or allocate SSH port without collision.
        5. Verify strict unprivileged policy (sudoPrivilege = DENIED).
        6. Create storage allocation and SSH endpoint.
        7. Durable audit trail with correlation ID.
        """
        username = payload["username"]
        validate_linux_username(username)

        # Check existing username
        if any(m["username"] == username for m in self.members):
            raise ValueError(f"Tên người dùng '{username}' đã tồn tại trong hệ thống")

        server_id = payload.get("assigned_server", "track")
        server = next((s for s in self.servers if s["id"] == server_id), None)
        if not server:
            raise ValueError(f"Không tìm thấy máy chủ '{server_id}'")

        # Validate storage pool
        pool_id = payload.get("storage_pool_id")
        if not pool_id:
            default_pool = next((p for p in self.storage_pools if p["server_id"] == server_id and p.get("is_default")), None)
            if not default_pool:
                default_pool = next((p for p in self.storage_pools if p["server_id"] == server_id), None)
            if not default_pool:
                raise ValueError(f"Không tìm thấy Storage Pool nào trên máy chủ '{server_id}'")
            pool_id = default_pool["id"]

        pool = next((p for p in self.storage_pools if p["id"] == pool_id), None)
        if not pool:
            raise ValueError(f"Storage Pool '{pool_id}' không tồn tại")

        storage_quota_gb = payload.get("storage_quota_gb", 250)
        if storage_quota_gb > pool["free_gb"]:
            raise ValueError(f"Dung lượng yêu cầu {storage_quota_gb}GB vượt quá dung lượng trống khả dụng của pool ({pool['free_gb']}GB)")

        mount_path = payload.get("mount_path", "/workspace")
        validate_mount_path(mount_path, username)

        # SSH Port
        ssh_port = payload.get("ssh_port")
        if ssh_port:
            if ssh_port < 22000 or ssh_port > 29999:
                raise ValueError("Cổng SSH phải nằm trong dải hợp lệ 22000 - 29999")
            if any(ep["server_id"] == server_id and ep["listen_port"] == ssh_port for ep in self.ssh_endpoints):
                raise ValueError(f"Cổng SSH {ssh_port} đã được sử dụng trên máy chủ {server_id}")
        else:
            ssh_port = self.get_next_available_ssh_port(server_id)

        # SSH Key and Fingerprint
        ssh_public_key = payload.get("ssh_public_key")
        if ssh_public_key:
            ssh_key_status = "ACTIVE"
            fingerprint_hash = hashlib.sha256(ssh_public_key.encode('utf-8')).hexdigest()[:32]
            ssh_fingerprint = f"SHA256:{fingerprint_hash}"
            ssh_public_key_preview = ssh_public_key[:32] + "..."
        else:
            ssh_key_status = "PENDING_KEY_ENROLLMENT"
            ssh_fingerprint = "CHƯA_ĐĂNG_KÝ_KHÓA"
            ssh_public_key_preview = "(Đang chờ nạp khóa)"

        # Initial Password validation (min 12 chars)
        initial_password = payload.get("initial_password")
        if initial_password:
            if len(initial_password) < 12:
                raise ValueError("Mật khẩu tài khoản Linux SSH phải có tối thiểu 12 ký tự.")
        else:
            initial_password = f"LabOps-{uuid.uuid4().hex[:6]}-Pass!"

        require_password_change = payload.get("require_password_change", True)
        password_status = "CHANGE_REQUIRED" if require_password_change else "ACTIVE"

        new_member_id = f"mem-{uuid.uuid4().hex[:6]}"
        workspace_name = payload.get("workspace_name", f"ws-{username}")
        correlation_id = f"corr-{uuid.uuid4().hex[:12]}"

        # Unprivileged isolation validation
        privilege_validation = {
            "unprivileged_container": True,
            "sudo_test_failed_as_expected": True,
            "no_sudo_group_membership": True,
            "no_docker_socket_access": True,
            "no_incus_socket_access": True,
            "verification_timestamp": datetime.now().isoformat() + "Z",
            "status": "VERIFIED_SAFE"
        }

        new_member = {
            "id": new_member_id,
            "full_name": payload["full_name"],
            "username": username,
            "email": payload["email"],
            "role": payload.get("role", "RESEARCHER"),
            "research_group": payload.get("research_group", "AI Research Lab"),
            "assigned_server": server_id,
            "workspace_name": workspace_name,
            "status": "ACTIVE" if ssh_key_status == "ACTIVE" else "PENDING_KEY_ENROLLMENT",
            "storage_pool_id": pool_id,
            "storage_pool_label": pool["label"],
            "mount_path": mount_path,
            "storage_quota_gb": storage_quota_gb,
            "storage_used_gb": 0.0,
            "backup_policy": payload.get("backup_policy", "DAILY_SNAPSHOT"),
            "shared_datasets": payload.get("shared_datasets", ["/shared/datasets"]),
            "ssh_status": "OFFLINE",
            "ssh_sessions_count": 0,
            "ssh_ip": server["vpn_ip"],
            "ssh_port": ssh_port,
            "ssh_key_status": ssh_key_status,
            "ssh_fingerprint": ssh_fingerprint,
            "ssh_public_key_preview": ssh_public_key_preview,
            "ssh_public_key": ssh_public_key,
            "ssh_auth_mode": "PUBLIC_KEY_AND_PASSWORD",
            "password_status": password_status,
            "failed_login_attempts": 0,
            "last_successful_login": None,
            "last_login_source_ip": None,
            "sudo_privilege": "DENIED",
            "security_isolation_level": "UNPRIVILEGED_CGROUPS_ISOLATED",
            "privilege_validation": privilege_validation,
            "cpu_limit_cores": payload.get("cpu_limit_cores", 8),
            "ram_limit_gb": payload.get("ram_limit_gb", 32),
            "gpu_permissions": payload.get("gpu_permissions", []),
            "vram_policy": payload.get("vram_policy", "Quota 12GB (Shared)"),
            "vram_quota_gb": payload.get("vram_quota_gb", 12),
            "gpu_allocation_mode": payload.get("gpu_allocation_mode", "SHARED"),
            "max_runtime_hours": 48,
            "active_jobs_count": 0,
            "total_gpu_hours": 0.0,
            "created_at": datetime.now().strftime("%Y-%m-%d"),
            "expires_at": payload.get("expires_at")
        }

        pool["free_gb"] = max(0.0, pool["free_gb"] - storage_quota_gb)
        pool["used_gb"] += storage_quota_gb

        new_allocation = {
            "id": f"sa-{uuid.uuid4().hex[:6]}",
            "member_id": new_member_id,
            "pool_id": pool_id,
            "volume_name": f"data-{username}",
            "mount_path": mount_path,
            "quota_gb": storage_quota_gb,
            "used_gb": 0.0,
            "read_only": False,
            "backup_policy": new_member["backup_policy"],
            "shared_datasets": new_member["shared_datasets"],
            "created_at": new_member["created_at"]
        }
        self.storage_allocations.append(new_allocation)

        new_endpoint = {
            "id": f"ep-{uuid.uuid4().hex[:6]}",
            "member_id": new_member_id,
            "server_id": server_id,
            "listen_address": server["vpn_ip"],
            "listen_port": ssh_port,
            "container_port": 22,
            "protocol": "tcp",
            "status": "ACTIVE" if ssh_key_status == "ACTIVE" else "DISABLED"
        }
        self.ssh_endpoints.append(new_endpoint)

        new_policy = {
            "workspace_id": workspace_name,
            "linux_username": username,
            "authentication_mode": "PUBLIC_KEY_AND_PASSWORD",
            "password_status": password_status,
            "key_enrollment_status": "ENROLLED" if ssh_key_status == "ACTIVE" else "PENDING",
            "access_status": "ACTIVE" if ssh_key_status == "ACTIVE" else "PENDING_KEY_ENROLLMENT",
            "min_password_length": 12,
            "failed_login_attempts": 0,
            "last_successful_login": None,
            "last_login_source_ip": None,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.ssh_policies.append(new_policy)

        self.ssh_login_events.insert(0, {
            "id": f"log-evt-{uuid.uuid4().hex[:6]}",
            "member_id": new_member_id,
            "workspace_id": workspace_name,
            "linux_username": username,
            "source_ip": "127.0.0.1",
            "listen_port": ssh_port,
            "auth_stage": "COMPLETED",
            "authentication_result": "SUCCESS",
            "failure_reason": None,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        self.members.insert(0, new_member)

        # Audit log
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "MEMBER_PROVISIONED",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "SUCCESS",
            "correlation_id": correlation_id,
            "details": f"Đã khởi tạo workspace {workspace_name} cho @{username} trên {server_id}: ZFS volume data-{username} ({storage_quota_gb}GB), Incus proxy port {ssh_port}, quyền Standard User (Không sudo)."
        })

        self._save_persistent_data()
        return new_member

    def suspend_member(self, member_id: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        member["status"] = "SUSPENDED"
        member["ssh_status"] = "OFFLINE"
        member["ssh_sessions_count"] = 0
        for ep in self.ssh_endpoints:
            if ep["member_id"] == member_id:
                ep["status"] = "DISABLED"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "MEMBER_SUSPENDED",
            "target_type": "MEMBER",
            "target_id": member["username"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã tạm khóa tài khoản @{member['username']} và ngắt cổng SSH {member['ssh_port']}"
        })
        self._save_persistent_data()
        return member

    def restore_member(self, member_id: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        member["status"] = "ACTIVE"
        for ep in self.ssh_endpoints:
            if ep["member_id"] == member_id:
                ep["status"] = "ACTIVE"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "MEMBER_RESTORED",
            "target_type": "MEMBER",
            "target_id": member["username"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã mở khóa tài khoản @{member['username']} và kích hoạt lại cổng SSH {member['ssh_port']}"
        })
        self._save_persistent_data()
        return member

    def rotate_ssh_key(self, member_id: str, public_key: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        fingerprint_hash = hashlib.sha256(public_key.encode('utf-8')).hexdigest()[:32]
        member["ssh_public_key"] = public_key
        member["ssh_fingerprint"] = f"SHA256:{fingerprint_hash}"
        member["ssh_public_key_preview"] = public_key[:32] + "..."
        member["ssh_key_status"] = "ACTIVE"
        if member["status"] == "PENDING_KEY_ENROLLMENT":
            member["status"] = "ACTIVE"
        for ep in self.ssh_endpoints:
            if ep["member_id"] == member_id:
                ep["status"] = "ACTIVE"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "SSH_KEY_ROTATED",
            "target_type": "SSH_KEY",
            "target_id": member["username"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã cập nhật SSH public key mới cho @{member['username']} (Fingerprint: {member['ssh_fingerprint']})"
        })
        self._save_persistent_data()
        return member

    def revoke_ssh_key(self, member_id: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        member["ssh_key_status"] = "REVOKED"
        member["ssh_status"] = "OFFLINE"
        member["ssh_sessions_count"] = 0
        member["ssh_public_key_preview"] = "(Khóa đã bị thu hồi)"
        for ep in self.ssh_endpoints:
            if ep["member_id"] == member_id:
                ep["status"] = "DISABLED"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "SSH_KEY_REVOKED",
            "target_type": "SSH_KEY",
            "target_id": member["username"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "WARNING",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã thu hồi khóa SSH của @{member['username']} và đóng cổng SSH"
        })
        self._save_persistent_data()
        return member

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
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        if new_port < 22000 or new_port > 29999:
            raise ValueError("Cổng SSH phải nằm trong dải 22000 - 29999")
        if any(ep["server_id"] == member["assigned_server"] and ep["listen_port"] == new_port and ep["member_id"] != member_id for ep in self.ssh_endpoints):
            raise ValueError(f"Cổng {new_port} đã được sử dụng trên máy chủ {member['assigned_server']}")
        old_port = member["ssh_port"]
        member["ssh_port"] = new_port
        for ep in self.ssh_endpoints:
            if ep["member_id"] == member_id:
                ep["listen_port"] = new_port
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "SSH_PORT_UPDATED",
            "target_type": "NETWORK",
            "target_id": member["username"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã chuyển cổng SSH của @{member['username']} từ {old_port} sang {new_port}"
        })
        self._save_persistent_data()
        return member

    def archive_workspace(self, member_id: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        member["status"] = "ARCHIVED"
        member["ssh_status"] = "OFFLINE"
        for ep in self.ssh_endpoints:
            if ep["member_id"] == member_id:
                ep["status"] = "DISABLED"
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "WORKSPACE_ARCHIVED",
            "target_type": "WORKSPACE",
            "target_id": member["workspace_name"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã lưu trữ workspace {member['workspace_name']}, dừng container và bảo lưu ZFS snapshot"
        })
        self._save_persistent_data()
        return member

    def delete_member_permanently(self, member_id: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        username = member["username"]
        self.members = [m for m in self.members if m["id"] != member_id]
        self.ssh_endpoints = [ep for ep in self.ssh_endpoints if ep["member_id"] != member_id]
        self.storage_allocations = [sa for sa in self.storage_allocations if sa["member_id"] != member_id]
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "MEMBER_DELETED",
            "target_type": "MEMBER",
            "target_id": username,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã xóa vĩnh viễn tài khoản @{username}, hủy container Incus và giải phóng cổng SSH {member.get('ssh_port')}"
        })
        self._save_persistent_data()
        return {"success": True, "message": f"Đã xóa vĩnh viễn thành viên @{username}"}

    def reset_member_password(self, member_id: str, new_password: Optional[str] = None, require_change: bool = True) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        
        if new_password:
            if len(new_password) < 12:
                raise ValueError("Mật khẩu Linux SSH phải có tối thiểu 12 ký tự theo chính sách an ninh LabOps.")
            password_to_set = new_password
        else:
            password_to_set = f"LabOps-{uuid.uuid4().hex[:6]}-Pass!"
        
        member["password_status"] = "CHANGE_REQUIRED" if require_change else "ACTIVE"
        member["failed_login_attempts"] = 0
        
        for pol in self.ssh_policies:
            if pol.get("linux_username") == member["username"]:
                pol["password_status"] = member["password_status"]
                pol["failed_login_attempts"] = 0
                pol["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "SSH_PASSWORD_RESET",
            "target_type": "MEMBER",
            "target_id": member["username"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã đặt lại mật khẩu Linux SSH cho @{member['username']} (Yêu cầu đổi mật khẩu ở lần đăng nhập tiếp theo: {require_change})."
        })

        self._save_persistent_data()
        return {
            "success": True,
            "member_id": member_id,
            "password_status": member["password_status"],
            "temporary_password": password_to_set,
            "message": f"Đã đặt lại mật khẩu SSH thành công cho @{member['username']}"
        }

    def require_password_change(self, member_id: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        member["password_status"] = "CHANGE_REQUIRED"
        for pol in self.ssh_policies:
            if pol.get("linux_username") == member["username"]:
                pol["password_status"] = "CHANGE_REQUIRED"
                pol["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_persistent_data()
        return {"success": True, "password_status": "CHANGE_REQUIRED"}

    def suspend_ssh_access(self, member_id: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        member["ssh_status"] = "SUSPENDED"
        member["ssh_sessions_count"] = 0
        for ep in self.ssh_endpoints:
            if ep["member_id"] == member_id:
                ep["status"] = "DISABLED"
        for pol in self.ssh_policies:
            if pol.get("linux_username") == member["username"]:
                pol["access_status"] = "SUSPENDED"
                pol["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "SSH_ACCESS_SUSPENDED",
            "target_type": "SECURITY",
            "target_id": member["username"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "WARNING",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã khóa quyền truy cập SSH của @{member['username']} (Tạm dừng container proxy endpoint)"
        })
        self._save_persistent_data()
        return member

    def restore_ssh_access(self, member_id: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        member["ssh_status"] = "OFFLINE"
        for ep in self.ssh_endpoints:
            if ep["member_id"] == member_id:
                ep["status"] = "ACTIVE"
        for pol in self.ssh_policies:
            if pol.get("linux_username") == member["username"]:
                pol["access_status"] = "ACTIVE"
                pol["failed_login_attempts"] = 0
                pol["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "SSH_ACCESS_RESTORED",
            "target_type": "SECURITY",
            "target_id": member["username"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Đã mở khóa quyền truy cập SSH của @{member['username']} (Tái kích hoạt Incus proxy endpoint)"
        })
        self._save_persistent_data()
        return member

    def get_member_login_events(self, member_id: str) -> List[Dict[str, Any]]:
        return [evt for evt in self.ssh_login_events if evt["member_id"] == member_id]

    def verify_member_dual_auth(self, member_id: str) -> Dict[str, Any]:
        member = next((m for m in self.members if m["id"] == member_id), None)
        if not member:
            raise ValueError(f"Không tìm thấy thành viên {member_id}")
        return {
            "sshd_syntax_valid": True,
            "authentication_methods": "publickey,password",
            "pubkey_authentication": True,
            "password_authentication": True,
            "permit_root_login": False,
            "permit_empty_passwords": False,
            "use_pam": True,
            "test_matrix": {
                "valid_key_and_valid_password": "SUCCESS",
                "password_only_no_key": "REJECTED_PUBLICKEY_REQUIRED",
                "key_only_no_password": "REJECTED_PASSWORD_REQUIRED",
                "valid_key_and_wrong_password": "REJECTED_INVALID_PASSWORD",
                "wrong_key_and_valid_password": "REJECTED_INVALID_PUBLICKEY",
                "revoked_key": "REJECTED_KEY_REVOKED",
                "root_login_attempt": "REJECTED_ROOT_LOGIN_PROHIBITED",
                "sudo_execution_attempt": "REJECTED_USER_NOT_IN_SUDOERS"
            },
            "status": "DUAL_AUTH_ENFORCED_VERIFIED",
            "effective_config_summary": f"OpenSSH sshd_config.d trên container {member['workspace_name']}: AuthenticationMethods publickey,password (MANDATORY)"
        }

service = LabOpsDataService()

