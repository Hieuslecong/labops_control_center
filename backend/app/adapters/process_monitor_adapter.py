from app.core.config import settings
import os
import re
import time
import json
import shutil
import logging
import subprocess
import urllib.request
import urllib.error
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

PROTECTED_SYSTEM_NAMES = {
    'systemd', 'init', 'kthreadd', 'dockerd', 'containerd', 'docker-proxy',
    'labops-agent', 'postgres', 'postgresql', 'journald', 'systemd-logind', 'systemd-udevd'
}

PROTECTED_CMD_KEYWORDS = {
    'labops-agent', 'app.main:app', 'uvicorn app.main', 'next-server',
    'next dev', 'next start', 'wg-easy', '/usr/sbin/dockerd', '/usr/bin/containerd'
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
    LabOps Process Monitor Adapter (V2).
    Primary source: High-performance Go Node Agent (port 9100).
    Fallback source: Local /proc and psutil with strict whitelist and ownership classification.
    """

    def __init__(self, server_id: str = "track", demo_mode: bool = False, agent_port: int = 9100):
        self.server_id = server_id
        self.demo_mode = demo_mode
        self.agent_url = f"http://127.0.0.1:{agent_port}"
        self.has_nvidia_smi = shutil.which("nvidia-smi") is not None
        self.data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
        os.makedirs(self.data_dir, exist_ok=True)
        self.history_file = os.path.join(self.data_dir, "process_history.json")

    def _query_go_agent(self, path: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
        """Gọi nội bộ sang Go Agent trên port 9100."""
        try:
            url = f"{self.agent_url}{path}"
            data = json.dumps(payload).encode("utf-8") if payload else None
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 201):
                    return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as he:
            try:
                body = json.loads(he.read().decode("utf-8"))
                return {"_error_status": he.code, "_error_body": body}
            except Exception:
                return {"_error_status": he.code, "_error_body": str(he)}
        except Exception as e:
            logger.debug(f"Go Agent offline or unreachable at {path}: {e}")
        return None

    def _get_container_for_pid(self, pid: int) -> Optional[str]:
        """Detect container name or namespace from /proc/{pid}/cgroup."""
        try:
            cgroup_path = f"/proc/{pid}/cgroup"
            if os.path.exists(cgroup_path):
                with open(cgroup_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if "lxc.payload." in line:
                            match = re.search(r'lxc\.payload\.([^/]+)', line)
                            if match:
                                return match.group(1)
                        elif "docker-" in line and ".scope" in line:
                            match = re.search(r'docker-([a-f0-9]{12})[a-f0-9]*\.scope', line)
                            if match:
                                return match.group(1)
                        elif "/docker/" in line:
                            parts = line.strip().split("/docker/")
                            if len(parts) > 1:
                                return parts[1][:12]
        except Exception:
            pass
        return None

    def _get_gpu_compute_pids(self) -> Dict[int, Dict[str, Any]]:
        """Query nvidia-smi for active compute PIDs and VRAM usage."""
        gpu_pids: Dict[int, Dict[str, Any]] = {}
        if not self.has_nvidia_smi:
            return gpu_pids
        try:
            cmd = [
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
                "--format=csv,noheader,nounits"
            ]
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=3)
            for line in output.strip().split("\n"):
                if not line:
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    try:
                        gpu_uuid = parts[0]
                        pid = int(parts[1])
                        proc_name = parts[2]
                        vram_mb = int(parts[3])
                        gpu_pids[pid] = {
                            "gpu_uuid": gpu_uuid,
                            "vram_mb": vram_mb,
                            "process_name": proc_name
                        }
                    except ValueError:
                        continue
        except Exception as e:
            logger.warning(f"Failed to query nvidia-smi compute apps: {e}")
        return gpu_pids

    def get_real_processes(self, filter_type: str = "research") -> Dict[str, Any]:
        """
        Collect real running processes.
        Prioritizes high-performance Go Node Agent; fallbacks to local /proc parser.
        filter_type: 'all' | 'research' | 'gpu' | 'member' | 'system'
        """
        now = time.time()
        now_dt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Remote server without configured agent
        if not self.demo_mode and self.server_id != settings.LOCAL_HOST_ID and self.server_id != "track":
            return {
                "source_host": self.server_id,
                "collection_timestamp": now_dt_str,
                "collector_status": "NOT_CONFIGURED",
                "filter": filter_type,
                "total_count": 0,
                "actual_observations": [],
                "error_details": "Remote process collector is not configured; connection state is not verified."
            }

        # 1. Try Go Node Agent first
        agent_res = self._query_go_agent(f"/api/v1/agent/processes?filter={filter_type}")
        if agent_res and "processes" in agent_res:
            procs = []
            for p in agent_res.get("processes", []):
                procs.append({
                    "host_id": self.server_id,
                    "server_name": "Server 01 — Server Rack (Debian 12)",
                    "ref": p.get("ref", f"{self.server_id}:{p.get('container_id') or 'host'}:{p.get('pid')}:{p.get('start_time_ticks', 0)}"),
                    "pid": p.get("pid"),
                    "ppid": p.get("ppid"),
                    "pgid": p.get("pgid"),
                    "username": p.get("user"),
                    "uid": p.get("uid"),
                    "name": p.get("name"),
                    "script_display": p.get("name"),
                    "cmdline": p.get("command_line", ""),
                    "start_time": p.get("start_time"),
                    "start_time_ticks": p.get("start_time_ticks", 0),
                    "elapsed_time": p.get("elapsed_time", "00h 00m 00s"),
                    "cpu_percent": p.get("cpu_percent", 0.0),
                    "memory_rss_mb": p.get("memory_rss_mb", 0.0),
                    "memory_percent": p.get("memory_percent", 0.0),
                    "status": p.get("state", "RUNNING"),
                    "classification": p.get("classification"),
                    "is_gpu_process": p.get("is_gpu", False),
                    "gpu_id": f"rack-gpu-0{p.get('gpu_index')}" if p.get("gpu_index") is not None else None,
                    "gpu_index": p.get("gpu_index"),
                    "vram_mb": p.get("vram_mb", 0),
                    "container_id": p.get("container_id"),
                    "workspace": p.get("workspace") or (f"docker-{p.get('container_id')}" if p.get("container_id") else None),
                    "is_protected": p.get("is_protected", False),
                    "is_research_workload": p.get("classification") == "RESEARCH_JOB"
                })
            return {
                "source_host": self.server_id,
                "collection_timestamp": agent_res.get("collected_at", now_dt_str),
                "collector_status": "OK",
                "collector_engine": "go-node-agent",
                "filter": filter_type,
                "total_count": len(procs),
                "actual_observations": procs,
                "error_details": None
            }

        # 2. Python Fallback if Go agent is unreachable
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
                ppid = info.get('ppid') or 0

                is_gpu = pid in gpu_pids
                gpu_data = gpu_pids.get(pid, {})
                vram_mb = gpu_data.get("vram_mb", 0)

                container = self._get_container_for_pid(pid)
                workspace = f"docker-{container}" if container else None

                # SAFE CLASSIFICATION
                cmd_lower = cmd_str.lower()
                is_system = (
                    pid <= 1 or ppid == 2 or
                    name.lower() in PROTECTED_SYSTEM_NAMES or
                    any(k in cmd_lower for k in PROTECTED_CMD_KEYWORDS) or
                    (not container and uid < 1000)
                )

                if is_system:
                    classification = "SYSTEM_PROCESS"
                    is_protected = True
                    is_research = False
                elif is_gpu:
                    classification = "GPU_PROCESS" if not container else "RESEARCH_JOB"
                    is_protected = False
                    is_research = True
                elif container:
                    is_ai = (
                        name.lower() in RESEARCH_NAME_KEYWORDS or
                        any(k in name.lower() for k in RESEARCH_NAME_KEYWORDS) or
                        any(k in cmd_lower for k in RESEARCH_CMD_KEYWORDS) or
                        cmd_lower.endswith(".py") or ".py " in cmd_lower
                    )
                    classification = "RESEARCH_JOB" if is_ai else "MEMBER_PROCESS"
                    is_protected = False
                    is_research = is_ai
                else:
                    classification = "MEMBER_PROCESS" if uid >= 1000 else "SYSTEM_PROCESS"
                    is_protected = (uid < 1000)
                    is_research = False

                if filter_type == "gpu" and not is_gpu:
                    continue
                if filter_type == "research" and not is_research:
                    continue
                if filter_type == "system" and classification != "SYSTEM_PROCESS":
                    continue
                if filter_type == "member" and classification not in ("MEMBER_PROCESS", "RESEARCH_JOB"):
                    continue

                create_time = info.get('create_time') or now
                elapsed_seconds = max(0, now - create_time)
                start_time_str = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")
                elapsed_str = format_elapsed_time(elapsed_seconds)

                rss_bytes = info['memory_info'].rss if info.get('memory_info') else 0
                rss_mb = round(rss_bytes / (1024 * 1024), 1)

                clean_cmd = sanitize_cmdline(cmdline)
                script_display = name

                ref = f"{self.server_id}:{container or 'host'}:{pid}:{int(create_time * 100)}"

                processes.append({
                    "host_id": self.server_id,
                    "server_name": "Server 01 — Server Rack (Debian 12)",
                    "ref": ref,
                    "pid": pid,
                    "ppid": ppid,
                    "pgid": pid,
                    "username": username,
                    "uid": uid,
                    "name": name,
                    "script_display": script_display,
                    "cmdline": clean_cmd,
                    "create_time": create_time,
                    "start_time": start_time_str,
                    "start_time_ticks": int(create_time * 100),
                    "elapsed_time": elapsed_str,
                    "cpu_percent": round(info.get('cpu_percent') or 0.0, 1),
                    "memory_rss_mb": rss_mb,
                    "memory_percent": round(info.get('memory_percent') or 0.0, 1),
                    "status": (info.get('status') or "running").upper(),
                    "classification": classification,
                    "is_gpu_process": is_gpu,
                    "gpu_id": "rack-gpu-00" if is_gpu else None,
                    "vram_mb": vram_mb,
                    "container_id": container,
                    "workspace": workspace,
                    "is_protected": is_protected,
                    "is_research_workload": is_research
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception as e:
                logger.debug(f"Error processing PID: {e}")
                continue

        processes.sort(key=lambda x: (not x["is_gpu_process"], -x["vram_mb"], -x["cpu_percent"]))
        self._record_history(processes)

        return {
            "source_host": self.server_id,
            "collection_timestamp": now_dt_str,
            "collector_status": "OK",
            "collector_engine": "python-psutil-fallback",
            "filter": filter_type,
            "total_count": len(processes),
            "actual_observations": processes,
            "error_details": None
        }

    def get_real_ssh_sessions(self) -> Dict[str, Any]:
        """Collect real active SSH sessions on the host using psutil.users()."""
        now_dt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
                    "server_name": "Server 01 — Server Rack (Debian 12)",
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
        """Keep a rolling observation log of process executions."""
        try:
            history = []
            if os.path.exists(self.history_file):
                try:
                    with open(self.history_file, "r", encoding="utf-8") as f:
                        history = json.load(f)
                except Exception:
                    history = []

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for p in processes[:10]:
                history.append({
                    "timestamp": now_str,
                    "pid": p["pid"],
                    "username": p["username"],
                    "name": p["name"],
                    "cpu_percent": p["cpu_percent"],
                    "memory_rss_mb": p["memory_rss_mb"],
                    "is_gpu": p["is_gpu_process"],
                    "vram_mb": p["vram_mb"],
                    "workspace": p.get("workspace")
                })

            history = history[-200:]
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"Failed to record process history: {e}")

    def get_active_compute_jobs(self, members_db: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        Produce a list of active compute jobs in the standard LabOps ComputeJob format,
        sourced directly from verified member and research processes.
        """
        proc_result = self.get_real_processes(filter_type="member")
        observations = proc_result.get("actual_observations", [])
        if not observations:
            proc_result_res = self.get_real_processes(filter_type="research")
            observations = proc_result_res.get("actual_observations", [])

        ssh_res = self.get_real_ssh_sessions()
        active_ssh_users = {s.get("username") for s in ssh_res.get("actual_observations", [])}

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
            gpu_id = p.get("gpu_id")

            jobs.append({
                "id": job_id,
                "ref": p.get("ref"),
                "memberId": member_id,
                "member_id": member_id,
                "username": uname,
                "fullName": full_name,
                "full_name": full_name,
                "script": p["script_display"],
                "cmdline": p["cmdline"],
                "workspace": p.get("workspace") or f"ws-{uname}",
                "serverId": self.server_id,
                "server_id": self.server_id,
                "serverName": p["server_name"],
                "server_name": p["server_name"],
                "gpuId": gpu_id,
                "gpu_id": gpu_id,
                "pid": p["pid"],
                "ppid": p["ppid"],
                "cpuPercent": p["cpu_percent"],
                "cpu_percent": p["cpu_percent"],
                "cpuUsagePercent": p["cpu_percent"],
                "ramUsedMb": p["memory_rss_mb"],
                "ram_used_mb": p["memory_rss_mb"],
                "ramUsageMb": p["memory_rss_mb"],
                "vramUsedMb": p["vram_mb"],
                "vram_used_mb": p["vram_mb"],
                "startTime": p["start_time"],
                "start_time": p["start_time"],
                "elapsedTime": p["elapsed_time"],
                "elapsed_time": p["elapsed_time"],
                "status": "RUNNING" if p["status"] in ["RUNNING", "SLEEPING", "R", "S"] else p["status"],
                "sshStatus": "SSH Online" if uname in active_ssh_users else "Chạy nền / Không có SSH",
                "ssh_status": "SSH Online" if uname in active_ssh_users else "Chạy nền / Không có SSH",
                "containerState": "Running" if p["container_id"] else "Trực tiếp trên Host",
                "container_state": "Running" if p["container_id"] else "Trực tiếp trên Host",
                "isGpu": p["is_gpu_process"],
                "is_gpu": p["is_gpu_process"]
            })

        return jobs

    def get_process_tree(self, ref: str) -> Dict[str, Any]:
        """Lấy cây tiến trình từ Go Agent."""
        res = self._query_go_agent(f"/api/v1/agent/processes/tree?ref={urllib.parse.quote(ref)}")
        if res and "_error_status" not in res:
            return res
        raise RuntimeError(res.get("_error_body", {}).get("error") if res else "Không thể kết nối Go Agent")

    def terminate_process(self, ref: str, timeout_seconds: int = 10, terminate_tree: bool = True) -> Dict[str, Any]:
        """Gửi SIGTERM có ân hạn qua Go Agent."""
        payload = {
            "ref": ref,
            "timeout_seconds": timeout_seconds,
            "terminate_tree": terminate_tree
        }
        res = self._query_go_agent("/api/v1/agent/processes/terminate", method="POST", payload=payload, timeout=float(timeout_seconds + 3))
        if res and "_error_status" not in res:
            return res
        err_msg = res.get("_error_body", {}).get("error") if res else "Go Agent không phản hồi"
        code = res.get("_error_status", 500) if res else 500
        raise RuntimeError(f"HTTP_{code}: {err_msg}")

    def kill_process(self, ref: str, force: bool = True, kill_tree: bool = True) -> Dict[str, Any]:
        """Gửi SIGKILL cưỡng chế qua Go Agent."""
        payload = {
            "ref": ref,
            "force": force,
            "kill_tree": kill_tree
        }
        res = self._query_go_agent("/api/v1/agent/processes/kill", method="POST", payload=payload, timeout=5.0)
        if res and "_error_status" not in res:
            return res
        err_msg = res.get("_error_body", {}).get("error") if res else "Go Agent không phản hồi"
        code = res.get("_error_status", 500) if res else 500
        raise RuntimeError(f"HTTP_{code}: {err_msg}")

import urllib.parse
process_monitor = ProcessMonitorAdapter(server_id=settings.LOCAL_HOST_ID, demo_mode=False)
