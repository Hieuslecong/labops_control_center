import os
import time
import shutil
import platform
import subprocess
import logging
from typing import Dict, Any, List, Optional
import psutil
from app.core.config import settings

logger = logging.getLogger(__name__)


def get_real_hostname() -> str:
    try:
        if os.path.exists("/etc/hostname"):
            with open("/etc/hostname", "r", encoding="utf-8") as _hf:
                name = _hf.read().strip()
            if name:
                return name
    except Exception:
        pass
    return platform.node() or "unknown"


def get_real_primary_ip() -> str:
    try:
        for iface, addrs in psutil.net_if_addrs().items():
            if iface == "lo" or iface.startswith(("docker", "veth", "br-")):
                continue
            for addr in addrs:
                if getattr(addr, "family", None) == 2 and addr.address and not addr.address.startswith("127."):
                    return addr.address
    except Exception:
        pass
    return "127.0.0.1"


def get_real_root_mount_info() -> Dict[str, str]:
    """Đọc thiết bị đĩa thực tế từ /proc/mounts thay vì gán cứng LVM Ubuntu."""
    target_path = os.path.abspath(__file__)
    best_mount = "/"
    best_dev = "unknown"
    best_fstype = "ext4"
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    dev, mnt, fstype = parts[0], parts[1], parts[2]
                    if fstype in ("tmpfs", "proc", "sysfs", "devtmpfs", "devpts", "cgroup2", "efivarfs"):
                        continue
                    if target_path.startswith(mnt.rstrip("/") + "/") or mnt == "/":
                        if len(mnt) >= len(best_mount):
                            best_mount = mnt
                            best_dev = dev
                            best_fstype = fstype
    except Exception:
        pass
    return {"device": best_dev, "mount_point": best_mount, "fstype": best_fstype}


class HostMonitorAdapter:
    """
    Lightweight, read-only host telemetry adapter for LabOps Control Center.
    Directly queries host Linux metrics via /proc, psutil, and nvidia-smi.
    """

    def __init__(self, is_demo_mode: bool = False, local_host_id: Optional[str] = None):
        self.is_demo_mode = is_demo_mode
        self.local_host_id = local_host_id or settings.LOCAL_HOST_ID
        self.is_containerized = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")

    def _get_cpu_model(self) -> str:
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
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
        """Inspect real host disk partition containing the workspace/system."""
        try:
            mnt_info = get_real_root_mount_info()
            probe_path = mnt_info["mount_point"] if os.path.exists(mnt_info["mount_point"]) else "/"
            usage = psutil.disk_usage(probe_path)
            # If "/" is tmpfs in a sandbox namespace, fallback to /etc or current directory
            if mnt_info["device"] != "unknown" and usage.total < 100 * (1024**3) and os.path.exists("/etc"):
                etc_usage = psutil.disk_usage("/etc")
                if etc_usage.total > usage.total:
                    usage = etc_usage

            total_bytes = usage.total
            used_bytes = usage.used
            total_tb = round(total_bytes / (1024**4), 2)
            used_tb = round(used_bytes / (1024**4), 2)
            pct = round((used_bytes / total_bytes) * 100, 1) if total_bytes > 0 else 0.0

            return {
                "disk_total_tb": total_tb,
                "disk_used_tb": used_tb,
                "disk_usage_percent": pct,
                "disk_type": f"{mnt_info['fstype'].upper()} ({mnt_info['device']})"
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

    def _build_local_telemetry(self, server_id: str, display_name: str, location: str) -> Dict[str, Any]:
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
        real_hostname = get_real_hostname()
        real_ip = get_real_primary_ip()

        incus_bin = shutil.which("incus")
        docker_bin = shutil.which("docker")
        runtime_status = "Chưa cài đặt Docker/Incus"
        if docker_bin:
            runtime_status = "Docker Engine"
        elif incus_bin:
            try:
                res = subprocess.check_output([incus_bin, "version"], text=True, timeout=2).strip()
                runtime_status = f"Incus v{res}"
            except Exception:
                runtime_status = "Incus (Dịch vụ chưa chạy)"

        return {
            "id": server_id,
            "name": display_name,
            "hostname": real_hostname,
            "location": location,
            "status": "degraded" if self.is_containerized else "online",
            "ip_address": real_ip,
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
            "incus_version": runtime_status,
            "driver_version": nv_versions["driver"],
            "cuda_version": nv_versions["cuda"],
            "last_updated": "Container telemetry; host not verified" if self.is_containerized else "Live host telemetry",
            "is_real_host": not self.is_containerized,
        }

    @staticmethod
    def _build_unverified_remote(server_id: str, display_name: str) -> Dict[str, Any]:
        return {
            "id": server_id,
            "name": display_name,
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
            "last_updated": "Chưa xác minh kết nối tới máy chủ từ xa",
            "is_real_host": False,
        }

    def get_remote_track_telemetry(self) -> Dict[str, Any]:
        """Telemetry for Server 01 — Track / Server-Rack (aiotlab-rack)."""
        if self.local_host_id == "track":
            return self._build_local_telemetry(
                server_id="track",
                display_name="Server 01 — Server Rack (aiotlab-rack • Cục bộ)",
                location="Phòng Máy chủ AIoT Lab (Rack)",
            )
        return self._build_unverified_remote("track", "Server 01 — Server Rack (Từ xa)")

    def get_local_workstation_telemetry(self) -> Dict[str, Any]:
        """Telemetry for Server 02 — Workstation."""
        if self.local_host_id == "workstation":
            return self._build_local_telemetry(
                server_id="workstation",
                display_name="Server 02 — Workstation (Cục bộ)",
                location="Phòng Lab (Workstation)",
            )
        return self._build_unverified_remote("workstation", "Server 02 — Workstation (Từ xa)")
