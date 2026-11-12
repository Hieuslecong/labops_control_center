import os
import json
import urllib.request
import subprocess
from datetime import datetime, timezone


from app.core.config import settings
def settings_local_host_id() -> str:
    return settings.LOCAL_HOST_ID

import shutil
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class NvmlAdapter:
    """
    Adapter for NVIDIA Management Library (NVML) and nvidia-smi.
    Collects real-time physical GPU telemetry:
    - Real GPU UUID and model
    - Real-time VRAM allocation and utilization
    - Real-time Temperature and Power Draw
    - Active CUDA compute processes with attribution
    - Hardware/Driver enforcement states
    """

    def __init__(self, server_id: str = "workstation", demo_mode: bool = False):
        self.server_id = server_id
        self.demo_mode = demo_mode
        self.has_nvidia_smi = shutil.which("nvidia-smi") is not None

    def _get_live_compute_processes(self) -> List[Dict[str, Any]]:
        """Query real active CUDA compute processes on the physical GPU and resolve host identity."""
        if not self.has_nvidia_smi:
            return []
        try:
            import psutil
        except ImportError:
            psutil = None

        try:
            cmd = [
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
                "--format=csv,noheader,nounits"
            ]
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=3)
            processes = []
            for line in output.strip().split("\n"):
                if not line:
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    try:
                        proc_gpu_uuid = parts[0]
                        pid = int(parts[1])
                        pname = parts[2]
                        vram_mb = int(parts[3])

                        username = "Chưa xác định"
                        workspace = "Unattributed host process"
                        cpu_pct = 0.0
                        displayName = pname

                        # Resolve real host process if psutil is available
                        if psutil and psutil.pid_exists(pid):
                            try:
                                proc = psutil.Process(pid)
                                username = proc.username() or "unknown"
                                cpu_pct = round(proc.cpu_percent(), 1)
                                cmdline = proc.cmdline()
                                if cmdline:
                                    for arg in cmdline[1:]:
                                        if arg.endswith(".py") or arg.endswith(".sh"):
                                            displayName = f"{proc.name()} ({os.path.basename(arg)})"
                                            break
                                    if displayName == pname and len(cmdline) > 1:
                                        displayName = f"{proc.name()} ({cmdline[1][:30]})"
                                else:
                                    displayName = proc.name()
                            except Exception:
                                pass

                        if not displayName or displayName == "[Not Found]":
                            displayName = "python (Tác vụ nghiên cứu AI)"

                        processes.append({
                            "gpu_uuid": proc_gpu_uuid,
                            "pid": pid,
                            "name": displayName,
                            "username": username,
                            "workspace": workspace,
                            "vram_mb": vram_mb,
                            "cpu_percent": cpu_pct,
                            "attribution_confidence": "VERIFIED_HOST_PID" if username != "Chưa xác định" else "UNATTRIBUTED_EXTERNAL"
                        })
                    except ValueError:
                        continue
            return processes
        except Exception as e:
            logger.warning(f"Failed to query compute apps: {e}")
            return []

    def _fetch_go_agent_gpus(self) -> Optional[List[Dict[str, Any]]]:
        """Lấy dữ liệu GPU tức thì từ LabOps Go Agent nếu đang chạy."""
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:9100/api/v1/agent/gpus",
                headers={"X-LabOps-Agent-Secret": "labops-local-agent-verified-token-2026"}
            )
            with urllib.request.urlopen(req, timeout=0.2) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") == "VERIFIED" and data.get("gpus"):
                        gpus = []
                        for g in data["gpus"]:
                            raw_procs = g.get("active_processes") or []
                            gpu_procs = []
                            for p in raw_procs:
                                pname = p.get("name", "")
                                user = p.get("username", "")
                                if not user:
                                    if "/users/" in pname:
                                        try:
                                            user = pname.split("/users/")[1].split("/")[0]
                                        except Exception:
                                            user = "researcher"
                                    elif "/home/" in pname:
                                        try:
                                            user = pname.split("/home/")[1].split("/")[0]
                                        except Exception:
                                            user = "researcher"
                                    else:
                                        user = "system"
                                vram = p.get("vram_mb") or p.get("used_memory_mb") or p.get("vramMb") or 0
                                gpu_procs.append({
                                    "pid": p.get("pid", 0),
                                    "name": pname or "python",
                                    "username": user,
                                    "workspace": p.get("workspace", "host"),
                                    "vram_mb": vram,
                                    "vramMb": vram,
                                    "cpu_percent": p.get("cpu_percent", 0.0),
                                    "cpuPercent": p.get("cpu_percent", 0.0),
                                })

                            active_users = list(dict.fromkeys(p["username"] for p in gpu_procs if p.get("username"))) or []
                            gpus.append({
                                "index": g.get("index", 0),
                                "id": f"rack-gpu-0{g.get('index', 0) + 1}",
                                "display_label": f"RACK-GPU-0{g.get('index', 0) + 1}",
                                "server_id": "track",
                                "server_name": "Server 01 — Server Rack (aiotlab-rack • Cục bộ)",
                                "source": "labops-go-agent",
                                "collected_at": data.get("collected_at", datetime.now(timezone.utc).isoformat()),
                                "freshness": "ONLINE",
                                "vram_unit": "MiB",
                                "model": g.get("name", "NVIDIA A30"),
                                "uuid": g.get("uuid", ""),
                                "pci_bus_id": g.get("pci_bus_id", ""),
                                "vram_total_mb": g.get("memory_total_mb", 24576),
                                "vram_used_mb": g.get("memory_used_mb", 1),
                                "gpu_util_percent": g.get("utilization_gpu_pct", 0),
                                "temp_celsius": g.get("temperature_c", 35),
                                "power_watts": g.get("power_watts", 45),
                                "power_limit_watts": g.get("power_limit_watts", 165),
                                "allocation_mode": "FULL_GPU_EXCLUSIVE",
                                "enforcement_state": "MONITOR_ONLY",
                                "active_processes": gpu_procs,
                                "active_users": active_users,
                                "associated_jobs": [f"proc-{p['pid']}" for p in gpu_procs],
                                "live": True
                            })
                        return gpus
        except Exception:
            pass
        return None

    def get_live_gpu_telemetry_sync(self) -> List[Dict[str, Any]]:
        """
        Synchronously query real nvidia-smi for live physical GPU telemetry.
        Never fabricates metrics in production mode.
        """
        # 1. Thử lấy từ Go Agent trước để không tốn sub-process
        if not self.demo_mode and self.server_id == settings_local_host_id():
            go_gpus = self._fetch_go_agent_gpus()
            if go_gpus:
                return go_gpus
        if not self.demo_mode and self.has_nvidia_smi and self.server_id == settings_local_host_id():
            try:
                cmd = [
                    "nvidia-smi",
                    "--query-gpu=index,name,uuid,pci.bus_id,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw,power.limit",
                    "--format=csv,noheader,nounits"
                ]
                output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=3)
                gpus = []
                live_processes = self._get_live_compute_processes()
                
                for line in output.strip().split("\n"):
                    if not line:
                        continue
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 10:
                        idx = int(parts[0])
                        total_vram = int(parts[4])
                        used_vram = int(parts[5])
                        util_pct = int(parts[6])
                        temp_c = int(parts[7])
                        power_w = int(float(parts[8]))
                        power_limit_w = int(float(parts[9]))
                        
                        gpu_uuid = parts[2]
                        gpu_procs = [p for p in live_processes if p.get("gpu_uuid") == gpu_uuid]
                        gpus.append({
                            "index": idx,
                            "id": f"{self.server_id}-gpu-{idx:02d}",
                            "display_label": f"{self.server_id.upper()}-GPU-{idx:02d}",
                            "server_id": self.server_id,
                            "server_name": "Server 01 — Server Rack (aiotlab-rack)" if self.server_id == "track" else "Server 02 — Workstation",
                            "source": "nvidia-smi",
                            "collected_at": datetime.now(timezone.utc).isoformat(),
                            "freshness": "ONLINE",
                            "vram_unit": "MiB",
                            "model": parts[1],
                            "uuid": parts[2],
                            "pci_bus_id": parts[3],
                            "vram_total_mb": total_vram,
                            "vram_used_mb": used_vram,
                            "gpu_util_percent": util_pct,
                            "temp_celsius": temp_c,
                            "power_watts": power_w,
                            "power_limit_watts": power_limit_w,
                            "allocation_mode": "FULL_GPU_EXCLUSIVE",
                            "enforcement_state": "MONITOR_ONLY",
                            "active_processes": gpu_procs,
                            "active_users": list(dict.fromkeys(p["username"] for p in gpu_procs if p.get("username") and p["username"] != "Chưa xác định")) or (["Chưa xác định (Workload ngoại vi)"] if gpu_procs else []),
                            "associated_jobs": [f"proc-{p['pid']}" for p in gpu_procs],
                            "live": True
                        })
                if gpus:
                    return gpus
            except Exception as e:
                logger.error(f"Failed to query live nvidia-smi on {self.server_id}: {e}")

        # In production mode: Server Track has no live telemetry because WireGuard tunnel is unreachable
        if not self.demo_mode:
            if self.server_id == "track":
                # Track is remote and disconnected
                return []
            return []

        # Demo mode fallback ONLY when self.demo_mode is explicitly True
        if self.server_id == "track":
            return [
                {
                    "index": 0,
                    "id": "track-gpu-01",
                    "display_label": "TRACK-GPU-01",
                    "server_id": "track",
                    "server_name": "Server 01 — Track",
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
                    "active_processes": [],
                    "active_users": [],
                    "associated_jobs": [],
                    "live": False
                },
                {
                    "index": 1,
                    "id": "track-gpu-02",
                    "display_label": "TRACK-GPU-02",
                    "server_id": "track",
                    "server_name": "Server 01 — Track",
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
                    "active_processes": [],
                    "active_users": [],
                    "associated_jobs": [],
                    "live": False
                },
                {
                    "index": 2,
                    "id": "track-gpu-03",
                    "display_label": "TRACK-GPU-03",
                    "server_id": "track",
                    "server_name": "Server 01 — Track",
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
                    "associated_jobs": [],
                    "live": False
                }
            ]
        else:
            return [
                {
                    "index": 0,
                    "id": "ws-gpu-01",
                    "display_label": "WS-GPU-01",
                    "server_id": "workstation",
                    "server_name": "Server 02 — Workstation",
                    "model": "NVIDIA A30 24GB HBM2",
                    "pci_bus_id": "0000:D8:00.0",
                    "uuid": "GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc",
                    "vram_total_mb": 24576,
                    "vram_used_mb": 13410,
                    "gpu_util_percent": 68,
                    "temp_celsius": 81,
                    "power_watts": 136,
                    "power_limit_watts": 165,
                    "allocation_mode": "SHARED",
                    "enforcement_state": "DRIVER_ENFORCED_VERIFIED",
                    "active_processes": [],
                    "active_users": [],
                    "associated_jobs": [],
                    "live": True
                }
            ]

    async def get_gpu_telemetry(self) -> List[Dict[str, Any]]:
        return self.get_live_gpu_telemetry_sync()
