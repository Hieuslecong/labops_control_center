import os
import re
import time
import json
import shutil
import logging
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional
import psutil

logger = logging.getLogger(__name__)

# Patterns to sanitize sensitive arguments from process cmdlines
SENSITIVE_ARGS_PATTERN = re.compile(
    r'(--(?:password|token|secret|key|api_key|auth|credential)[=\s]+)([^\s]+)',
    re.IGNORECASE
)

# Research & AI workload classification heuristics
RESEARCH_NAME_KEYWORDS = {
    'python', 'python3', 'pt_data_worker', 'torchrun', 'deepspeed',
    'ray', 'jupyter', 'jupyter-lab', 'vllm', 'triton', 'ollama'
}

RESEARCH_CMD_KEYWORDS = {
    'train', 'eval', 'inference', 'finetune', 'benchmark', 'torch',
    'transformers', 'accelerate', 'dataset', 'diffusion', 'lora', 'vllm'
}

def sanitize_cmdline(cmdline_parts: List[str]) -> str:
    """Sanitize command line string to prevent leaking passwords, tokens, or secrets."""
    if not cmdline_parts:
        return ""
    full_cmd = " ".join(cmdline_parts)
    sanitized = SENSITIVE_ARGS_PATTERN.sub(r'\1********', full_cmd)
    return sanitized

def format_elapsed_time(seconds: float) -> str:
    """Format duration in seconds into human-readable 00h 00m 00s format."""
    total_seconds = int(max(0, seconds))
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if days > 0:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    return f"{hours:02d}h {minutes:02d}m {secs:02d}s"

class ProcessMonitorAdapter:
    """
    Lightweight, read-only LabOps Process Monitor Adapter.
    Collects real running Linux host processes, NVIDIA GPU compute workloads,
    and active SSH sessions using native /proc, psutil, utmp, and nvidia-smi.
    """

    def __init__(self, server_id: str = "workstation", demo_mode: bool = False):
        self.server_id = server_id
        self.demo_mode = demo_mode
        self.has_nvidia_smi = shutil.which("nvidia-smi") is not None
        self.data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
        os.makedirs(self.data_dir, exist_ok=True)
        self.history_file = os.path.join(self.data_dir, "process_history.json")

    def _get_container_for_pid(self, pid: int) -> Optional[str]:
        """Detect container name or namespace from /proc/{pid}/cgroup."""
        try:
            cgroup_path = f"/proc/{pid}/cgroup"
            if os.path.exists(cgroup_path):
                with open(cgroup_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if "lxc.payload." in line:
                            # e.g. 0::/lxc.payload.ws-tungtt/system.slice/...
                            match = re.search(r'lxc\.payload\.([^/]+)', line)
                            if match:
                                return match.group(1)
                        elif "incus" in line or "docker" in line:
                            parts = line.strip().split("/")
                            if len(parts) > 2:
                                return parts[2]
        except Exception:
            pass
        return None

    def _get_gpu_compute_pids(self) -> Dict[int, int]:
        """Query nvidia-smi for active compute PIDs and VRAM usage in MB."""
        gpu_pids: Dict[int, int] = {}
        if not self.has_nvidia_smi:
            return gpu_pids
        try:
            cmd = [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits"
            ]
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=3)
            for line in output.strip().split("\n"):
                if not line:
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    try:
                        pid = int(parts[0])
                        vram_mb = int(parts[2])
                        gpu_pids[pid] = vram_mb
                    except ValueError:
                        continue
        except Exception as e:
            logger.warning(f"Failed to query nvidia-smi compute apps: {e}")
        return gpu_pids

    def get_real_processes(self, filter_type: str = "research") -> Dict[str, Any]:
        """
        Collect real running processes on the host.
        filter_type: 'all' | 'research' | 'gpu'
        """
        now = time.time()
        now_dt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # In production mode for Track server (remote & currently disconnected):
        if not self.demo_mode and self.server_id == "track":
            return {
                "source_host": "track",
                "collection_timestamp": now_dt_str,
                "collector_status": "UNAVAILABLE",
                "filter": filter_type,
                "total_count": 0,
                "actual_observations": [],
                "error_details": "Mất kết nối: Server 01 — Track chưa kết nối qua WireGuard VPN (10.200.0.1)"
            }

        gpu_pids = self._get_gpu_compute_pids()
        processes: List[Dict[str, Any]] = []

        attrs = [
            'pid', 'ppid', 'name', 'username', 'uids', 'cmdline',
            'cpu_percent', 'memory_info', 'memory_percent', 'status', 'create_time'
        ]

        for p in psutil.process_iter(attrs):
            try:
                info = p.info
                pid = info['pid']
                if pid <= 0:
                    continue

                name = info['name'] or ""
                cmdline = info['cmdline'] or []
                cmd_str = " ".join(cmdline)
                username = info['username'] or "unknown"
                uid = info['uids'].real if info.get('uids') else 0

                is_gpu = pid in gpu_pids
                vram_mb = gpu_pids.get(pid, 0)

                # Classification
                is_research = (
                    is_gpu or
                    name.lower() in RESEARCH_NAME_KEYWORDS or
                    any(k in name.lower() for k in RESEARCH_NAME_KEYWORDS) or
                    any(k in cmd_str.lower() for k in RESEARCH_CMD_KEYWORDS) or
                    cmd_str.endswith(".py") or ".py " in cmd_str
                )

                if filter_type == "gpu" and not is_gpu:
                    continue
                if filter_type == "research" and not is_research:
                    continue

                create_time = info.get('create_time') or now
                elapsed_seconds = max(0, now - create_time)
                start_time_str = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")
                elapsed_str = format_elapsed_time(elapsed_seconds)

                rss_bytes = info['memory_info'].rss if info.get('memory_info') else 0
                rss_mb = round(rss_bytes / (1024 * 1024), 1)

                container = self._get_container_for_pid(pid)
                workspace = container if container else f"ws-{username}"

                # Script name display
                clean_cmd = sanitize_cmdline(cmdline)
                script_display = name
                if cmdline:
                    # Look for python script argument e.g. scripts/train_binary_lora.py
                    for arg in cmdline[1:]:
                        if arg.endswith(".py") or arg.endswith(".sh"):
                            script_display = f"{name} ({os.path.basename(arg)})"
                            break
                    if script_display == name and len(cmdline) > 1:
                        script_display = f"{name} ({cmdline[1][:30]})"

                processes.append({
                    "host_id": self.server_id,
                    "server_name": "Server 02 — Workstation" if self.server_id == "workstation" else "Server 01 — Track",
                    "pid": pid,
                    "ppid": info.get('ppid') or 0,
                    "username": username,
                    "uid": uid,
                    "name": name,
                    "script_display": script_display,
                    "cmdline": clean_cmd,
                    "create_time": create_time,
                    "start_time": start_time_str,
                    "elapsed_time": elapsed_str,
                    "cpu_percent": round(info.get('cpu_percent') or 0.0, 1),
                    "memory_rss_mb": rss_mb,
                    "memory_percent": round(info.get('memory_percent') or 0.0, 1),
                    "status": (info.get('status') or "running").upper(),
                    "is_gpu_process": is_gpu,
                    "gpu_id": "ws-gpu-01" if is_gpu else None,
                    "vram_mb": vram_mb,
                    "container_id": container,
                    "workspace": workspace,
                    "is_research_workload": is_research
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception as e:
                logger.debug(f"Error processing PID: {e}")
                continue

        # Sort: GPU processes first, then by CPU utilization descending
        processes.sort(key=lambda x: (not x["is_gpu_process"], -x["vram_mb"], -x["cpu_percent"]))

        # Update observation history
        self._record_history(processes)

        return {
            "source_host": self.server_id,
            "collection_timestamp": now_dt_str,
            "collector_status": "OK",
            "filter": filter_type,
            "total_count": len(processes),
            "actual_observations": processes,
            "error_details": None
        }

    def get_real_ssh_sessions(self) -> Dict[str, Any]:
        """
        Collect real active SSH sessions on the host using psutil.users()
        and checking established SSH sockets.
        """
        now_dt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not self.demo_mode and self.server_id == "track":
            return {
                "source_host": "track",
                "collection_timestamp": now_dt_str,
                "collector_status": "UNAVAILABLE",
                "total_count": 0,
                "actual_observations": [],
                "error_details": "Mất kết nối: Server 01 — Track chưa kết nối qua WireGuard VPN"
            }

        sessions = []
        try:
            users = psutil.users()
            now = time.time()
            for idx, u in enumerate(users):
                started_dt = datetime.fromtimestamp(u.started).strftime("%Y-%m-%d %H:%M:%S")
                duration = format_elapsed_time(now - u.started)
                sessions.append({
                    "id": f"ssh-sess-{idx+1}",
                    "username": u.name,
                    "terminal": u.terminal or "pts/?",
                    "source_ip": u.host or "127.0.0.1",
                    "started_at": started_dt,
                    "session_duration": duration,
                    "server_id": self.server_id,
                    "server_name": "Server 02 — Workstation" if self.server_id == "workstation" else "Server 01 — Track",
                    "workspace": f"ws-{u.name}",
                    "status": "ACTIVE"
                })
        except Exception as e:
            logger.warning(f"Failed to query active users/ssh sessions: {e}")

        return {
            "source_host": self.server_id,
            "collection_timestamp": now_dt_str,
            "collector_status": "OK",
            "total_count": len(sessions),
            "actual_observations": sessions,
            "error_details": None
        }

    def _record_history(self, processes: List[Dict[str, Any]]) -> None:
        """Persist process observation history to track start, end, and duration."""
        try:
            history = {}
            if os.path.exists(self.history_file):
                try:
                    with open(self.history_file, "r", encoding="utf-8") as f:
                        history = json.load(f)
                except Exception:
                    history = {}

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Update current observations
            for p in processes:
                # Key using pid and create_time to guarantee identity across PID reuse
                key = f"{self.server_id}-{p['pid']}-{int(p['create_time'])}"
                if key not in history:
                    history[key] = {
                        "server_id": self.server_id,
                        "pid": p["pid"],
                        "username": p["username"],
                        "name": p["name"],
                        "script_display": p["script_display"],
                        "first_observed": now_str,
                        "last_observed": now_str,
                        "observed_runtime": p["elapsed_time"],
                        "peak_vram_mb": p["vram_mb"],
                        "is_gpu_process": p["is_gpu_process"],
                        "status": "RUNNING"
                    }
                else:
                    item = history[key]
                    item["last_observed"] = now_str
                    item["observed_runtime"] = p["elapsed_time"]
                    if p["vram_mb"] > item.get("peak_vram_mb", 0):
                        item["peak_vram_mb"] = p["vram_mb"]

            # Cap history to last 200 processes
            if len(history) > 200:
                sorted_keys = sorted(history.keys(), key=lambda k: history[k].get("last_observed", ""), reverse=True)
                history = {k: history[k] for k in sorted_keys[:200]}

            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"Failed to record process history: {e}")

    def get_active_compute_jobs(self, members_db: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        Produce a list of active compute jobs in the standard LabOps ComputeJob format,
        sourced directly from live host processes.
        """
        proc_result = self.get_real_processes(filter_type="research")
        observations = proc_result.get("actual_observations", [])

        # Dynamic active SSH detection
        ssh_res = self.get_real_ssh_sessions()
        active_ssh_users = {s.get("username") for s in ssh_res.get("actual_observations", [])}

        # Build username -> full_name / member_id lookup
        member_lookup: Dict[str, Dict[str, Any]] = {}
        if members_db:
            for m in members_db:
                member_lookup[m.get("username", "")] = m

        jobs: List[Dict[str, Any]] = []
        for p in observations:
            uname = p["username"]
            m_info = member_lookup.get(uname, {})
            full_name = m_info.get("full_name") or f"Người dùng @{uname}"
            member_id = m_info.get("id") or f"user-{uname}"

            job_id = f"proc-{p['pid']}"
            gpu_id = p["gpu_id"] or ("ws-gpu-01" if p["is_gpu_process"] else None)

            jobs.append({
                "id": job_id,
                "memberId": member_id,
                "username": uname,
                "fullName": full_name,
                "script": p["script_display"],
                "cmdline": p["cmdline"],
                "workspace": p["workspace"],
                "serverId": self.server_id,
                "serverName": p["server_name"],
                "gpuId": gpu_id,
                "pid": p["pid"],
                "ppid": p["ppid"],
                "cpuUsagePercent": p["cpu_percent"],
                "ramUsageMb": p["memory_rss_mb"],
                "vramUsedMb": p["vram_mb"],
                "startTime": p["start_time"],
                "elapsedTime": p["elapsed_time"],
                "status": "RUNNING" if p["status"] in ["RUNNING", "SLEEPING"] else p["status"],
                "sshStatus": "SSH Online" if uname in active_ssh_users else "Chạy nền / Không có SSH",
                "containerState": "Running" if p["container_id"] else "Trực tiếp trên Host",
                "isGpu": p["is_gpu_process"]
            })

        return jobs

process_monitor = ProcessMonitorAdapter(server_id="workstation", demo_mode=False)
