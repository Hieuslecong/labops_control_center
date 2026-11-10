import os
import time
import shutil
import platform
import subprocess
import logging
from typing import Dict, Any, List, Optional
import psutil

logger = logging.getLogger(__name__)

class HostMonitorAdapter:
    """
    Lightweight, read-only host telemetry adapter for LabOps Control Center.
    Directly queries host Linux metrics via /proc, psutil, and nvidia-smi without
    requiring third-party monitoring daemons.
    """

    def __init__(self, is_demo_mode: bool = False):
        self.is_demo_mode = is_demo_mode
        self.is_containerized = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")

    def _get_cpu_model(self) -> str:
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return platform.processor() or "Unknown CPU"

    def _get_uptime_string(self) -> str:
        try:
            uptime_seconds = time.time() - psutil.boot_time()
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            mins = int((uptime_seconds % 3600) // 60)
            if days > 0:
                return f"{days} ngày {hours:02d} giờ"
            return f"{hours:02d} giờ {mins:02d} phút"
        except Exception:
            return "Không xác định"

    def _get_nvidia_versions(self) -> Dict[str, str]:
        if not shutil.which("nvidia-smi"):
            return {"driver": "Chưa cài đặt", "cuda": "Chưa cài đặt"}
        try:
            cmd = ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"]
            driver = subprocess.check_output(cmd, text=True, timeout=2).strip().split("\n")[0]
            # Get CUDA version from nvidia-smi banner
            out = subprocess.check_output(["nvidia-smi"], text=True, timeout=2)
            cuda = "Unknown"
            for line in out.split("\n"):
                if "CUDA Version:" in line:
                    parts = line.split("CUDA Version:")
                    if len(parts) > 1:
                        cuda = parts[1].split()[0].replace("|", "").strip()
            return {"driver": driver, "cuda": cuda}
        except Exception as e:
            logger.warning(f"Failed to query NVIDIA versions: {e}")
            return {"driver": "Unavailable", "cuda": "Unavailable"}

    def _get_disk_telemetry(self) -> Dict[str, Any]:
        """Inspect real host disk partitions and usage"""
        try:
            root_usage = psutil.disk_usage("/")
            total_bytes = root_usage.total
            used_bytes = root_usage.used

            # Check secondary partition /dev/sdb mounted on /hdd1/hieulc/tmp if exists
            sdb_path = "/hdd1/hieulc/tmp"
            if os.path.exists(sdb_path):
                try:
                    sdb_usage = psutil.disk_usage(sdb_path)
                    total_bytes += sdb_usage.total
                    used_bytes += sdb_usage.used
                except Exception:
                    pass

            total_tb = round(total_bytes / (1024**4), 2)
            used_tb = round(used_bytes / (1024**4), 2)
            pct = round((used_bytes / total_bytes) * 100, 1) if total_bytes > 0 else 0.0

            return {
                "disk_total_tb": total_tb,
                "disk_used_tb": used_tb,
                "disk_usage_percent": pct,
                "disk_type": "LVM Ext4 (Primary) + Ext4 (Secondary Data)"
            }
        except Exception as e:
            logger.warning(f"Failed to query disk telemetry: {e}")
            return {
                "disk_total_tb": 0.0,
                "disk_used_tb": 0.0,
                "disk_usage_percent": 0.0,
                "disk_type": "Unavailable"
            }

    def _get_gpu_count(self) -> int:
        nvidia_smi = shutil.which("nvidia-smi")
        if not nvidia_smi:
            return 0
        try:
            output = subprocess.check_output(
                [nvidia_smi, "--query-gpu=index", "--format=csv,noheader"], text=True, timeout=2
            )
            return sum(bool(line.strip()) for line in output.splitlines())
        except Exception as exc:
            logger.warning("Failed to query GPU count: %s", exc)
            return 0

    def get_local_workstation_telemetry(self) -> Dict[str, Any]:
        """
        Produce real-time telemetry for the local Ubuntu host (Server 02 — Workstation).
        """
        cpu_model = self._get_cpu_model()
        logical_cpus = psutil.cpu_count(logical=True) or 0
        physical_cores = psutil.cpu_count(logical=False) or 0
        cpu_pct = psutil.cpu_percent(interval=None)

        mem = psutil.virtual_memory()
        ram_total_gb = round(mem.total / (1024**3), 1)
        ram_used_gb = round(mem.used / (1024**3), 1)
        ram_pct = mem.percent

        disk_info = self._get_disk_telemetry()
        nv_versions = self._get_nvidia_versions()
        uptime_str = self._get_uptime_string()

        # Check incus installation
        incus_bin = shutil.which("incus")
        incus_status = "Chưa cài đặt"
        if incus_bin:
            try:
                res = subprocess.check_output([incus_bin, "version"], text=True, timeout=2).strip()
                incus_status = f"v{res}"
            except Exception:
                incus_status = "Đã cài đặt (Dịch vụ chưa chạy)"

        return {
            "id": "workstation",
            "name": "Server 02 — Workstation (Cục bộ)",
            "hostname": platform.node() or "unknown",
            "location": "Phòng Lab TP.HCM (Khu B)",
            "status": "degraded" if self.is_containerized else "online",
            "ip_address": "127.0.0.1",
            "vpn_ip": "",
            "uptime": uptime_str,
            "cpu_model": f"{cpu_model} ({logical_cpus} vCPUs, {physical_cores} Cores)",
            "cpu_cores": physical_cores,
            "cpu_usage_percent": round(cpu_pct, 1),
            "ram_total_gb": ram_total_gb,
            "ram_used_gb": ram_used_gb,
            "ram_usage_percent": round(ram_pct, 1),
            "disk_total_tb": disk_info["disk_total_tb"],
            "disk_used_tb": disk_info["disk_used_tb"],
            "disk_usage_percent": disk_info["disk_usage_percent"],
            "disk_type": disk_info["disk_type"],
            "network_in_mbps": 0,
            "network_out_mbps": 0,
            "gpu_count": self._get_gpu_count(),
            "active_members": 0,
            "active_jobs": 0,
            "incus_version": incus_status,
            "driver_version": nv_versions["driver"],
            "cuda_version": nv_versions["cuda"],
            "last_updated": "Container telemetry; host not verified" if self.is_containerized else "Live host telemetry",
            "is_real_host": not self.is_containerized
        }

    def get_remote_track_telemetry(self) -> Dict[str, Any]:
        """
        Telemetry for Server 01 — Track.
        In production mode, if WireGuard / remote API is unreachable, report honestly as offline.
        """
        # In Demo Mode, realistic simulated values may be returned for development only.
        if self.is_demo_mode:
            return {
                "id": "track",
                "name": "Server 01 — Track (Demo)",
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
                "last_updated": "Vừa xong",
                "is_real_host": False
            }

        # No remote Track telemetry agent or Gateway API is configured here.
        return {
            "id": "track",
            "name": "Server 01 — Track (Từ xa)",
            "hostname": "Chưa xác minh",
            "location": "Chưa xác minh",
            "status": "unknown",
            "ip_address": "",
            "vpn_ip": "",
            "uptime": "Chưa xác minh",
            "cpu_model": "Chưa xác minh",
            "cpu_cores": 0,
            "cpu_usage_percent": 0.0,
            "ram_total_gb": 0.0,
            "ram_used_gb": 0.0,
            "ram_usage_percent": 0.0,
            "disk_total_tb": 0.0,
            "disk_used_tb": 0.0,
            "disk_usage_percent": 0.0,
            "disk_type": "Chưa xác minh",
            "network_in_mbps": 0,
            "network_out_mbps": 0,
            "gpu_count": 0,
            "active_members": 0,
            "active_jobs": 0,
            "incus_version": "Chưa xác minh",
            "driver_version": "Chưa xác minh",
            "cuda_version": "Chưa xác minh",
            "last_updated": "Remote telemetry chưa được cấu hình",
            "is_real_host": False
        }

