import os
import time
import json
import shutil
import platform
import subprocess
import logging
import urllib.request
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import psutil
from app.core.config import settings
from app.adapters.telemetry_contract import is_fresh_timestamp

logger = logging.getLogger(__name__)


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


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
    return ""


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

    def _fetch_go_agent_telemetry(self, server_id: str, display_name: str, location: str) -> Optional[Dict[str, Any]]:
        """Read local Go Agent telemetry only when its explicit machine credential is configured."""
        token = os.getenv("LABOPS_NODE_AGENT_TOKEN", "").strip()
        if not token:
            return None
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:9100/api/v1/agent/host",
                headers={"X-LabOps-Agent-Secret": token}
            )
            with urllib.request.urlopen(req, timeout=0.2) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if (
                        not isinstance(data, dict)
                        or data.get("id") != server_id
                        or str(data.get("status", "")).lower() not in {"online", "degraded"}
                        or not is_fresh_timestamp(data.get("collected_at"))
                    ):
                        return None
                    data["name"] = display_name
                    data["location"] = location
                    data["last_updated"] = "Live host telemetry (LabOps Go Agent)"
                    return data
        except Exception:
            pass
        return None

    def _build_local_telemetry(self, server_id: str, display_name: str, location: str) -> Dict[str, Any]:
        # Ưu tiên lấy trực tiếp từ Go Agent nếu đang hoạt động
        go_data = self._fetch_go_agent_telemetry(server_id, display_name, location)
        if go_data:
            return go_data
        if self.is_containerized:
            # Container-local /proc and psutil readings do not prove physical-host state.
            return self._build_unverified_remote(server_id, display_name)

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
            try:
                ver_out = subprocess.check_output([docker_bin, "--version"], text=True, timeout=2).strip()
                # e.g. "Docker version 26.1.5+dfsg1, build a72d7cd" -> "Docker 26.1.5+dfsg1"
                ver_short = ver_out.replace("Docker version ", "").split(",")[0].strip()
                runtime_status = f"Docker {ver_short}"
            except Exception:
                runtime_status = "Docker Engine"
        elif incus_bin:
            try:
                res = subprocess.check_output([incus_bin, "version"], text=True, timeout=2).strip()
                runtime_status = f"Incus v{res}"
            except Exception:
                runtime_status = "Incus (Dịch vụ chưa chạy)"

        return {
            "id": server_id,
            "source": "psutil+nvidia-smi",
            "collected_at": datetime.now(timezone.utc).isoformat(),
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

    def _query_gateway_node_telemetry(self, node_id: str) -> Optional[Dict[str, Any]]:
        gateway_http_url = os.getenv("LABOPS_GATEWAY_HTTP_URL", "http://127.0.0.1:9444")
        control_secret = os.getenv("LABOPS_GATEWAY_CONTROL_SECRET", "").strip()
        if not control_secret or not node_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in node_id):
            return None
        headers = {"Accept": "application/json", "Authorization": f"Bearer {control_secret}"}
        try:
            req = urllib.request.Request(
                f"{gateway_http_url.rstrip('/')}/api/v1/gateway/nodes/{node_id}/telemetry",
                headers=headers,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status != 200:
                    return None
                data = json.loads(resp.read().decode("utf-8"))
                if not isinstance(data, dict) or data.get("node_id") != node_id:
                    return None
                host_info = data.get("host")
                gpu_info = data.get("gpu")
                if not isinstance(host_info, dict) or not isinstance(gpu_info, dict):
                    return None
                if (
                    str(data.get("status", "")).upper() not in {"ONLINE", "DEGRADED"}
                    or host_info.get("node_id") != node_id
                    or gpu_info.get("node_id") != node_id
                    or str(host_info.get("status", "")).lower() not in {"online", "degraded"}
                    or host_info.get("is_real_host") is not True
                    or not is_fresh_timestamp(data.get("host_received_at"))
                    or not is_fresh_timestamp(data.get("gpu_received_at"))
                    or not is_fresh_timestamp(host_info.get("collected_at"))
                    or str(gpu_info.get("status", "")).upper() != "VERIFIED"
                    or not is_fresh_timestamp(gpu_info.get("collected_at"))
                ):
                    return None

                # Gateway currently exposes the generated protobuf JSON contract:
                # byte counters and *_bytes fields. Validate before converting so
                # malformed or non-finite telemetry can never become trusted UI data.
                host_numeric = (
                    "cpu_usage_percent", "ram_total_bytes", "ram_used_bytes",
                    "ram_usage_percent", "disk_total_bytes", "disk_used_bytes",
                    "disk_usage_percent", "uptime_seconds", "net_rx_bytes_sec",
                    "net_tx_bytes_sec",
                )
                if any(not _finite_number(host_info.get(key)) for key in host_numeric):
                    return None
                if (
                    type(host_info.get("cpu_cores")) is not int
                    or host_info["cpu_cores"] <= 0
                    or not isinstance(host_info.get("cpu_model"), str)
                    or not host_info["cpu_model"].strip()
                    or not isinstance(host_info.get("disk_type"), str)
                    or not host_info["disk_type"].strip()
                    or not isinstance(host_info.get("ip_address"), str)
                    or not host_info["ip_address"].strip()
                    or host_info["ram_total_bytes"] <= 0
                    or host_info["ram_used_bytes"] > host_info["ram_total_bytes"]
                    or host_info["disk_total_bytes"] <= 0
                    or host_info["disk_used_bytes"] > host_info["disk_total_bytes"]
                    or any(host_info[key] < 0 for key in host_numeric)
                    or host_info["cpu_usage_percent"] > 100
                    or host_info["cpu_usage_percent"] < 0
                    or host_info["ram_usage_percent"] > 100
                    or host_info["ram_usage_percent"] < 0
                    or host_info["disk_usage_percent"] > 100
                    or host_info["disk_usage_percent"] < 0
                    or host_info["uptime_seconds"] < 0
                ):
                    return None

                raw_gpus = gpu_info.get("gpus")
                gpu_count = gpu_info.get("gpu_count")
                if type(gpu_count) is not int or gpu_count < 0 or not isinstance(raw_gpus, list) or gpu_count != len(raw_gpus):
                    return None
                gpus_list = []
                seen_uuids = set()
                seen_indexes = set()
                for g in raw_gpus:
                    required = (
                        "index", "name", "uuid", "pci_bus_id", "memory_total_mb",
                        "memory_used_mb", "gpu_utilization_percent",
                        "memory_utilization_percent", "temperature_celsius",
                        "power_usage_watts", "power_limit_watts", "enforcement_state",
                    )
                    if not isinstance(g, dict) or any(key not in g for key in required):
                        return None
                    gpu_uuid = g.get("uuid")
                    gpu_index = g.get("index")
                    if (
                        type(gpu_index) is not int or gpu_index < 0
                        or not isinstance(gpu_uuid, str) or not gpu_uuid.strip()
                        or not isinstance(g.get("name"), str) or not g["name"].strip()
                        or not isinstance(g.get("pci_bus_id"), str) or not g["pci_bus_id"].strip()
                        or not isinstance(g.get("enforcement_state"), str)
                        or gpu_uuid in seen_uuids or gpu_index in seen_indexes
                    ):
                        return None
                    seen_uuids.add(gpu_uuid)
                    seen_indexes.add(gpu_index)
                    numeric_gpu_fields = (
                        "memory_total_mb", "memory_used_mb", "gpu_utilization_percent",
                        "memory_utilization_percent", "temperature_celsius",
                        "power_usage_watts", "power_limit_watts",
                    )
                    if any(not _finite_number(g[key]) or g[key] < 0 for key in numeric_gpu_fields):
                        return None
                    if (
                        g["memory_total_mb"] <= 0
                        or g["memory_used_mb"] > g["memory_total_mb"]
                        or g["gpu_utilization_percent"] > 100
                        or g["memory_utilization_percent"] > 100
                    ):
                        return None
                    gpus_list.append({
                        "index": gpu_index,
                        "model": g["name"],
                        "uuid": gpu_uuid,
                        "vram_total_mb": g["memory_total_mb"],
                        "vram_used_mb": g["memory_used_mb"],
                        "gpu_util_percent": g["gpu_utilization_percent"],
                        "memory_utilization_percent": g["memory_utilization_percent"],
                        "temp_celsius": g["temperature_celsius"],
                        "power_watts": g["power_usage_watts"],
                        "power_limit_watts": g["power_limit_watts"],
                        "pci_bus_id": g["pci_bus_id"],
                        "enforcement_state": g["enforcement_state"],
                    })

                ram_total = host_info["ram_total_bytes"] / (1024**3)
                ram_used = host_info["ram_used_bytes"] / (1024**3)
                disk_total = host_info["disk_total_bytes"] / (1024**4)
                disk_used = host_info["disk_used_bytes"] / (1024**4)
                uptime_sec = host_info["uptime_seconds"]
                days = uptime_sec // 86400
                hours = (uptime_sec % 86400) // 3600

                hostname = host_info.get("hostname") or "Unverified"
                disp_name = (
                    f"Server 02 — Workstation ({hostname})"
                    if node_id == "workstation"
                    else f"Server 01 — Server Rack ({hostname})"
                )

                return {
                    "id": node_id,
                    "source": "gateway-mtls",
                    "collected_at": data["host_received_at"],
                    "source_collected_at": host_info["collected_at"],
                    "name": disp_name,
                    "hostname": hostname,
                    "location": host_info.get("location") or "Not provided",
                    "status": "degraded" if (
                        str(data.get("status", "")).upper() == "DEGRADED"
                        or str(host_info.get("status", "")).lower() == "degraded"
                    ) else "online",
                    "ip_address": host_info.get("ip_address") or "",
                    "vpn_ip": "",
                    "uptime": f"{days} ngày {hours:02d} giờ" if days > 0 else f"{hours} giờ",
                    "cpu_model": host_info.get("cpu_model") or "",
                    "cpu_cores": host_info.get("cpu_cores", 0),
                    "cpu_usage_percent": round(host_info.get("cpu_usage_percent", 0.0), 1),
                    "ram_total_gb": round(ram_total, 1),
                    "ram_used_gb": round(ram_used, 1),
                    "ram_usage_percent": round(host_info["ram_usage_percent"], 1),
                    "disk_total_tb": round(disk_total, 2),
                    "disk_used_tb": round(disk_used, 2),
                    "disk_usage_percent": round(host_info["disk_usage_percent"], 1),
                    "disk_type": host_info["disk_type"],
                    "network_in_mbps": round(host_info["net_rx_bytes_sec"] / 125000, 2),
                    "network_out_mbps": round(host_info["net_tx_bytes_sec"] / 125000, 2),
                    "gpu_count": gpu_count,
                    "active_members": host_info.get("active_members", 0),
                    "active_jobs": host_info.get("active_jobs", 0),
                    "incus_version": "N/A",
                    "driver_version": gpu_info.get("driver_version") or "Not provided",
                    "cuda_version": gpu_info.get("cuda_version") or "Not provided",
                    "last_updated": f"Cập nhật trực tiếp qua LabOps Zero-Trust Gateway mTLS ({data.get('status')})",
                    "is_real_host": True,
                    "gpus": gpus_list,
                }
        except Exception:
            return None

    def get_remote_track_telemetry(self) -> Dict[str, Any]:
        """Telemetry for Server 01 — Track / Server-Rack (aiotlab-rack)."""
        if self.local_host_id == "track":
            return self._build_local_telemetry(
                server_id="track",
                display_name="Server 01 — Server Rack (aiotlab-rack • Cục bộ)",
                location="Phòng Máy chủ AIoT Lab (Rack)",
            )
        gw = self._query_gateway_node_telemetry("track")
        if gw:
            return gw
        return self._build_unverified_remote("track", "Server 01 — Server Rack (Từ xa)")

    def get_local_workstation_telemetry(self) -> Dict[str, Any]:
        """Telemetry for Server 02 — Workstation."""
        if self.local_host_id == "workstation":
            return self._build_local_telemetry(
                server_id="workstation",
                display_name="Server 02 — Workstation (Cục bộ)",
                location="Phòng Lab (Workstation)",
            )
        gw = self._query_gateway_node_telemetry("workstation")
        if gw:
            return gw
        return self._build_unverified_remote("workstation", "Server 02 — Workstation (Từ xa)")

    def get_external_daemons_telemetry(self) -> Dict[str, Dict[str, Any]]:
        """Kiểm tra trực tiếp trạng thái thực tế của 4 daemon bên ngoài trên máy chủ: Docker, Trivy, WireGuard, PostgreSQL."""
        daemons: Dict[str, Dict[str, Any]] = {}

        # 1. Docker Engine
        docker_bin = shutil.which("docker")
        if docker_bin:
            version_str = "installed"
            try:
                version_str = subprocess.check_output([docker_bin, "--version"], text=True, timeout=2).strip()
            except Exception:
                pass
            sock_accessible = False
            try:
                chk = subprocess.run([docker_bin, "info"], capture_output=True, text=True, timeout=2)
                sock_accessible = chk.returncode == 0
            except Exception:
                pass
            daemons["docker"] = {
                "installed": True,
                "binary": docker_bin,
                "version": version_str,
                "status": "ACTIVE" if sock_accessible else "INSTALLED_SOCKET_RESTRICTED",
                "note": "Docker daemon đang chạy" if sock_accessible else "Đã cài Docker; cần thêm user vào group 'docker' (sudo usermod -aG docker aiot-1) để mở quyền socket",
            }
        else:
            daemons["docker"] = {"installed": False, "binary": None, "version": None, "status": "NOT_INSTALLED"}

        # 2. Trivy Security Scanner
        trivy_bin = shutil.which("trivy")
        if trivy_bin:
            trivy_ver = "installed"
            try:
                trivy_ver = subprocess.check_output([trivy_bin, "--version"], text=True, timeout=3).splitlines()[0].strip()
            except Exception:
                pass
            daemons["trivy"] = {
                "installed": True,
                "binary": trivy_bin,
                "version": trivy_ver,
                "status": "ACTIVE",
            }
        else:
            daemons["trivy"] = {"installed": False, "binary": None, "version": None, "status": "NOT_INSTALLED"}

        # 3. WireGuard VPN (wg / wg-quick)
        wg_bin = shutil.which("wg")
        if wg_bin:
            wg_ver = "installed"
            try:
                wg_ver = subprocess.check_output([wg_bin, "--version"], text=True, timeout=2).strip()
            except Exception:
                pass
            wg0_up = os.path.exists("/sys/class/net/wg0")
            daemons["wireguard"] = {
                "installed": True,
                "binary": wg_bin,
                "version": wg_ver,
                "wg0_interface_present": wg0_up,
                "status": "ACTIVE" if wg0_up else "INSTALLED_INTERFACE_DOWN",
                "note": "wg0 đang hoạt động" if wg0_up else "Đã cài wireguard-tools; chờ khởi tạo /etc/wireguard/wg0.conf (wg-quick up wg0)",
            }
        else:
            daemons["wireguard"] = {"installed": False, "binary": None, "version": None, "status": "NOT_INSTALLED"}

        # 4. PostgreSQL
        psql_bin = shutil.which("psql")
        pg_isready_bin = shutil.which("pg_isready")
        if psql_bin:
            pg_ver = "installed"
            try:
                pg_ver = subprocess.check_output([psql_bin, "--version"], text=True, timeout=2).strip()
            except Exception:
                pass
            accepting = False
            if pg_isready_bin:
                try:
                    r = subprocess.run([pg_isready_bin, "-h", "127.0.0.1", "-p", "5432"], capture_output=True, text=True, timeout=2)
                    accepting = r.returncode == 0
                except Exception:
                    pass
            daemons["postgresql"] = {
                "installed": True,
                "binary": psql_bin,
                "version": pg_ver,
                "accepting_connections": accepting,
                "status": "ACTIVE" if accepting else "INSTALLED_CHECKING_SOCKET",
            }
        else:
            daemons["postgresql"] = {"installed": False, "binary": None, "version": None, "status": "NOT_INSTALLED"}

        return daemons
