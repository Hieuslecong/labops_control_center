from app.core.config import settings
import os
import re
import signal
try:
    import pwd
except ImportError:  # pragma: no cover - non-POSIX fallback
    pwd = None  # type: ignore[assignment]
import time
import copy
import json
import shutil
import logging
import subprocess
import urllib.request
import urllib.error
import urllib.parse
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
    'labops-agent', 'app.main:app', 'app.main', 'uvicorn app.main', 'uvicorn',
    'next-server', 'next dev', 'next start', 'wg-easy',
    '/usr/sbin/dockerd', '/usr/bin/containerd'
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
        default_data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
        self.data_dir = os.path.abspath(os.getenv("LABOPS_DATA_DIR") or default_data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.history_file = os.path.join(self.data_dir, "process_history.json")
        self._process_cache: Dict[tuple, tuple[float, Dict[str, Any]]] = {}

    @property
    def server_name(self) -> str:
        if self.server_id == "workstation":
            return "Server 02 — Workstation (aiot-ws1 • Cục bộ)"
        return "Server 01 — Server Rack (aiotlab-rack)"

    def _resolve_host_username(
        self,
        uid: Optional[int],
        fallback_user: Optional[str],
        container_id: Optional[str] = None,
    ) -> str:
        """Resolve canonical OS username on local host to prevent stale UID cache drift."""
        if (
            pwd is not None
            and self.server_id == settings.LOCAL_HOST_ID
            and not container_id
            and uid is not None
        ):
            try:
                return pwd.getpwuid(int(uid)).pw_name
            except (KeyError, ValueError, TypeError):
                pass
        return fallback_user or (str(uid) if uid is not None else "unknown")

    def _query_go_agent(self, path: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
        """Gọi nội bộ sang Go Agent khi LABOPS_AGENT_SECRET được cấu hình."""
        secret_token = os.getenv("LABOPS_AGENT_SECRET", "").strip()
        if not secret_token:
            return None

        configured_ports = [int(os.environ.get("LABOPS_AGENT_PORT", 9150)), 9150, 9100]
        ports = []
        for p in configured_ports:
            if p not in ports:
                ports.append(p)

        last_error_res = None
        for port in ports:
            try:
                url = f"http://127.0.0.1:{port}{path}"
                data = json.dumps(payload).encode("utf-8") if payload else None
                req = urllib.request.Request(url, data=data, method=method)
                req.add_header("Content-Type", "application/json")
                req.add_header("X-LabOps-Agent-Secret", secret_token)
                req.add_header("Authorization", f"Bearer {secret_token}")
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
                logger.debug(f"Go Agent offline or unreachable at port {port}{path}: {e}")
                continue
        return last_error_res

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
        cache_key = (self.server_id, self.demo_mode, filter_type)
        now_mono = time.monotonic()
        cached = self._process_cache.get(cache_key)
        if cached and (now_mono - cached[0]) < 2.0:
            return copy.deepcopy(cached[1])
        data = self._collect_real_processes(filter_type=filter_type)
        self._process_cache[cache_key] = (now_mono, copy.deepcopy(data))
        return data

    def _collect_real_processes(self, filter_type: str = "research") -> Dict[str, Any]:
        now = time.time()
        now_dt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Remote server without configured agent
        if not self.demo_mode and self.server_id != settings.LOCAL_HOST_ID:
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
        agent_filter = "all" if filter_type in ("system", "member") else filter_type
        agent_res = self._query_go_agent(f"/api/v1/agent/processes?filter={agent_filter}")
        if agent_res and "processes" in agent_res:
            procs = []
            for p in agent_res.get("processes", []):
                gpu_idx = p.get('gpu_index')
                proc_name = p.get("name") or ""
                cmdline = p.get("command_line", "")
                container_id = p.get("container_id")
                uid = p.get("uid")
                username = self._resolve_host_username(uid, p.get("user"), container_id)

                is_protected_override = (
                    proc_name.lower() in PROTECTED_SYSTEM_NAMES
                    or any(k in cmdline.lower() for k in PROTECTED_CMD_KEYWORDS)
                )
                if is_protected_override:
                    classification = "SYSTEM_PROCESS"
                    attribution = "SYSTEM_PROCESS"
                    is_protected = True
                    is_user_workload = False
                    is_research_workload = False
                else:
                    classification = p.get("classification")
                    attribution = p.get("attribution", "UNKNOWN")
                    is_protected = p.get("is_protected", False)
                    is_user_workload = p.get("is_user_workload", False)
                    is_research_workload = (classification == "RESEARCH_JOB")

                if filter_type == "system" and classification != "SYSTEM_PROCESS":
                    continue
                if filter_type == "member" and not (
                    classification in ("MEMBER_PROCESS", "RESEARCH_JOB", "GPU_PROCESS") or is_user_workload
                ):
                    continue
                if filter_type in ("research", "workloads", "workload") and (
                    is_protected or not (is_user_workload or classification in ("RESEARCH_JOB", "GPU_PROCESS"))
                ):
                    continue

                tree_gpu_idx = p.get("tree_gpu_index") if p.get("tree_gpu_index") is not None else gpu_idx
                total_vram_mb = p.get("total_vram_mb", p.get("vram_mb", 0))
                tree_is_gpu = bool(p.get("tree_is_gpu") or p.get("is_gpu", False) or (total_vram_mb or 0) > 0)

                procs.append({
                    "host_id": self.server_id,
                    "server_name": self.server_name,
                    "ref": p.get("ref", f"{self.server_id}:{container_id or 'host'}:{p.get('pid')}:{p.get('start_time_ticks', 0)}"),
                    "pid": p.get("pid"),
                    "ppid": p.get("ppid"),
                    "pgid": p.get("pgid"),
                    "username": username,
                    "uid": uid,
                    "name": proc_name,
                    "script_display": proc_name,
                    "cmdline": cmdline,
                    "start_time": p.get("start_time"),
                    "start_time_ticks": p.get("start_time_ticks", 0),
                    "elapsed_time": p.get("elapsed_time", "00h 00m 00s"),
                    "cpu_percent": p.get("cpu_percent", 0.0),
                    "memory_rss_mb": p.get("memory_rss_mb", 0.0),
                    "memory_percent": p.get("memory_percent", 0.0),
                    "status": p.get("state", "RUNNING"),
                    "classification": classification,
                    "is_gpu_process": p.get("is_gpu", False),
                    "gpu_id": f"{self.server_id}-gpu-0{int(gpu_idx) + 1}" if gpu_idx is not None else None,
                    "gpu_index": gpu_idx,
                    "vram_mb": p.get("vram_mb", 0),
                    "total_vram_mb": total_vram_mb,
                    "tree_is_gpu": tree_is_gpu,
                    "tree_gpu_index": tree_gpu_idx,
                    "tree_gpu_id": f"{self.server_id}-gpu-0{int(tree_gpu_idx) + 1}" if tree_gpu_idx is not None else None,
                    "container_id": container_id,
                    "workspace": p.get("workspace") or (f"docker-{container_id}" if container_id else None),
                    "is_protected": is_protected,
                    "is_research_workload": is_research_workload,
                    "attribution": attribution,
                    "is_user_workload": is_user_workload,
                    "is_root_workload": p.get("is_root_workload", True),
                    "children_count": p.get("children_count", 0),
                    "total_cpu_percent": p.get("total_cpu_percent", p.get("cpu_percent", 0.0)),
                    "total_memory_rss_mb": p.get("total_memory_rss_mb", p.get("memory_rss_mb", 0.0))
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
                    "server_name": self.server_name,
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
                    "gpu_id": f"{self.server_id}-gpu-01" if is_gpu else None,
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
                    "server_name": self.server_name,
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
        sourced directly from verified member workloads (filter=workloads).
        Only user-launched, active compute tasks are included; background services,
        desktop daemons, and idle shells are excluded.
        """
        proc_result = self.get_real_processes(filter_type="workloads")
        observations = proc_result.get("actual_observations", [])

        ssh_res = self.get_real_ssh_sessions()
        active_ssh_users = {s.get("username") for s in ssh_res.get("actual_observations", [])}

        member_lookup: Dict[str, Dict[str, Any]] = {}
        valid_member_usernames = set()
        if members_db:
            for m in members_db:
                u = m.get("username", "")
                if u:
                    member_lookup[u] = m
                    valid_member_usernames.add(u)

        jobs: List[Dict[str, Any]] = []
        for p in observations:
            # 1. Chỉ nhận tiến trình được đánh dấu là User Workload và là Root Workload
            if not p.get("is_user_workload", False) and not p.get("is_gpu_process", False):
                continue
            if not p.get("is_root_workload", True):
                continue

            uname = p["username"]
            # 2. Loại bỏ các process hệ thống (root, service host account aiot-ws1, systemd, daemon) không phải container workload
            if uname in ("root", "aiot-ws1", "systemd", "daemon") and not p.get("container_id"):
                continue

            # 3. Lấy thông tin thành viên
            m_info = member_lookup.get(uname, {})
            full_name = m_info.get("full_name") or f"Người dùng @{uname}"
            member_id = m_info.get("id") or f"user-{uname}"

            job_id = f"proc-{p['pid']}"
            gpu_id = p.get("gpu_id")

            # Sử dụng CPU, RAM & VRAM gộp của cả nhóm workload (tránh double-counting ở raw process list)
            cpu_val = float(p.get("total_cpu_percent") if p.get("total_cpu_percent") is not None else p.get("cpu_percent", 0.0))
            ram_val = float(p.get("total_memory_rss_mb") if p.get("total_memory_rss_mb") is not None else p.get("memory_rss_mb", 0.0))
            vram_val = int(p.get("total_vram_mb") if p.get("total_vram_mb") is not None else p.get("vram_mb", 0))
            is_gpu_val = bool(p.get("tree_is_gpu") or p.get("is_gpu_process") or vram_val > 0)
            gpu_id = p.get("tree_gpu_id") or p.get("gpu_id")
            if is_gpu_val and not gpu_id:
                gpu_id = f"{self.server_id}-gpu-01"
            classification = "GPU_PROCESS" if is_gpu_val else (p.get("classification") or "RESEARCH_JOB")

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
                "cpuPercent": cpu_val,
                "cpu_percent": cpu_val,
                "cpuUsagePercent": cpu_val,
                "ramUsedMb": ram_val,
                "ram_used_mb": ram_val,
                "ramUsageMb": ram_val,
                "vramUsedMb": vram_val,
                "vram_used_mb": vram_val,
                "startTime": p["start_time"],
                "start_time": p["start_time"],
                "elapsedTime": p["elapsed_time"],
                "elapsed_time": p["elapsed_time"],
                "status": "RUNNING" if p["status"] in ["RUNNING", "SLEEPING", "R", "S"] else p["status"],
                "sshStatus": "SSH Online" if uname in active_ssh_users else "Chạy nền / Không có SSH",
                "ssh_status": "SSH Online" if uname in active_ssh_users else "Chạy nền / Không có SSH",
                "containerState": "Running" if p["container_id"] else "Trực tiếp trên Host",
                "container_state": "Running" if p["container_id"] else "Trực tiếp trên Host",
                "isGpu": is_gpu_val,
                "is_gpu": is_gpu_val,
                "classification": classification,
                "isProtected": bool(p.get("is_protected", False)),
                "is_protected": bool(p.get("is_protected", False)),
                "childrenCount": p.get("children_count", 0),
                "children_count": p.get("children_count", 0),
                "attribution": p.get("attribution", "VERIFIED_USER_WORKLOAD"),
                "isRootWorkload": p.get("is_root_workload", True),
                "is_root_workload": p.get("is_root_workload", True)
            })

        jobs.sort(key=lambda x: (not x["isGpu"], -x["vramUsedMb"], -x["cpuPercent"]))
        return jobs

    def _parse_proc_stat(self, pid: int) -> Dict[str, Any]:
        stat_path = f"/proc/{pid}/stat"
        if not os.path.exists(stat_path):
            raise LookupError(f"PROCESS_NOT_FOUND: PID {pid} không tồn tại")
        with open(stat_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        lparen = raw.find("(")
        rparen = raw.rfind(")")
        if lparen == -1 or rparen == -1:
            raise LookupError(f"PROCESS_NOT_FOUND: Không thể đọc /proc/{pid}/stat")
        name = raw[lparen + 1:rparen]
        fields = raw[rparen + 2:].split()
        ppid = int(fields[1]) if len(fields) > 1 else 0
        start_ticks = int(fields[19]) if len(fields) > 19 else 0

        uid = -1
        status_path = f"/proc/{pid}/status"
        if os.path.exists(status_path):
            with open(status_path, "r", encoding="utf-8", errors="ignore") as sf:
                for line in sf:
                    if line.startswith("Uid:"):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            uid = int(parts[1])
                        break

        cmdline = ""
        cmd_path = f"/proc/{pid}/cmdline"
        if os.path.exists(cmd_path):
            with open(cmd_path, "rb") as cf:
                cmdline = cf.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()

        username = self._resolve_host_username(uid if uid >= 0 else None, None, self._get_container_for_pid(pid))
        return {
            "pid": pid,
            "ppid": ppid,
            "name": name,
            "uid": uid,
            "username": username,
            "cmdline": cmdline,
            "start_time_ticks": start_ticks,
        }

    def _validate_proc_ref(self, ref: str) -> Dict[str, Any]:
        parts = str(ref or "").split(":", 3)
        if len(parts) != 4:
            raise ValueError(f"INVALID_PROCESS_REF: '{ref}' không đúng định dạng server:scope:pid:start_ticks")
        ref_host, scope, pid_raw, ticks_raw = parts
        if ref_host != self.server_id:
            raise ValueError(f"HOST_MISMATCH: ProcessRef thuộc host '{ref_host}', không khớp host hiện tại '{self.server_id}'")
        try:
            pid = int(pid_raw)
            expected_ticks = int(ticks_raw)
        except ValueError as exc:
            raise ValueError(f"INVALID_PROCESS_REF: PID hoặc start_ticks không hợp lệ trong '{ref}'") from exc
        if pid <= 1 or pid == os.getpid():
            raise PermissionError(f"PROTECTED_PROCESS_DENIED: Tiến trình hệ thống hoặc Control Plane (PID {pid}) được bảo vệ")

        info = self._parse_proc_stat(pid)
        cmd_lower = info["cmdline"].lower()
        name_lower = info["name"].lower()
        if (
            info["ppid"] == 2
            or name_lower in PROTECTED_SYSTEM_NAMES
            or any(k in cmd_lower for k in PROTECTED_CMD_KEYWORDS)
            or info["uid"] == 0
        ):
            raise PermissionError(f"PROTECTED_PROCESS_DENIED: Tiến trình '{info['name']}' (PID {pid}) được bảo vệ")

        if expected_ticks > 0 and info["start_time_ticks"] != expected_ticks:
            raise RuntimeError(
                f"PID_RECYCLED: start_time_ticks không khớp cho PID {pid} (expected={expected_ticks}, actual={info['start_time_ticks']})"
            )
        return info

    def _collect_safe_descendants(self, root_pid: int) -> List[Dict[str, Any]]:
        children_by_ppid: Dict[int, List[Dict[str, Any]]] = {}
        for p in psutil.process_iter(["pid", "ppid", "name", "uids", "cmdline"]):
            try:
                inf = p.info
                cpid = inf["pid"]
                if cpid <= 1 or cpid == os.getpid():
                    continue
                stat_inf = self._parse_proc_stat(cpid)
                cmd_lower = stat_inf["cmdline"].lower()
                if (
                    stat_inf["name"].lower() in PROTECTED_SYSTEM_NAMES
                    or any(k in cmd_lower for k in PROTECTED_CMD_KEYWORDS)
                    or stat_inf["uid"] == 0
                ):
                    continue
                children_by_ppid.setdefault(stat_inf["ppid"], []).append(stat_inf)
            except Exception:
                continue

        result: List[Dict[str, Any]] = []
        queue = [root_pid]
        seen = {root_pid}
        while queue:
            curr = queue.pop(0)
            for child in children_by_ppid.get(curr, []):
                if child["pid"] not in seen:
                    seen.add(child["pid"])
                    result.append(child)
                    queue.append(child["pid"])
        result.reverse()
        return result

    def get_process_tree(self, ref: str) -> Dict[str, Any]:
        """Lấy cây tiến trình từ Go Agent hoặc fallback cục bộ qua /proc."""
        res = self._query_go_agent(f"/api/v1/agent/processes/tree?ref={urllib.parse.quote(ref)}")
        if res and "_error_status" not in res:
            return res
        if res and res.get("_error_status") == 404:
            raise LookupError(f"PROCESS_NOT_FOUND: {res.get('_error_body')}")
        info = self._validate_proc_ref(ref)
        descendants = self._collect_safe_descendants(info["pid"])
        return {
            "ref": ref,
            "root": info,
            "children": descendants,
            "source": "local_proc",
        }

    def terminate_process(self, ref: str, timeout_seconds: int = 10, terminate_tree: bool = True) -> Dict[str, Any]:
        """Gửi SIGTERM có ân hạn qua Go Agent hoặc /proc fallback (không dùng -pgid)."""
        payload = {
            "ref": ref,
            "timeout_seconds": timeout_seconds,
            "terminate_tree": terminate_tree,
        }
        res = self._query_go_agent("/api/v1/agent/processes/terminate", method="POST", payload=payload, timeout=float(timeout_seconds + 3))
        if res and "_error_status" not in res:
            return res
        if res and "_error_status" in res:
            code = int(res.get("_error_status") or 500)
            err_body = res.get("_error_body")
            err_msg = err_body.get("error") if isinstance(err_body, dict) else str(err_body)
            if code == 403:
                raise PermissionError(err_msg)
            if code == 404:
                raise LookupError(err_msg)
            raise RuntimeError(f"HTTP_{code}: {err_msg}")

        info = self._validate_proc_ref(ref)
        targets = self._collect_safe_descendants(info["pid"]) if terminate_tree else []
        targets.append(info)
        for t in targets:
            try:
                curr = self._parse_proc_stat(t["pid"])
                if curr["start_time_ticks"] == t["start_time_ticks"]:
                    os.kill(t["pid"], signal.SIGTERM)
            except (LookupError, ProcessLookupError):
                continue

        deadline = time.monotonic() + max(0.5, float(timeout_seconds))
        while time.monotonic() < deadline:
            if not os.path.exists(f"/proc/{info['pid']}"):
                break
            try:
                curr = self._parse_proc_stat(info["pid"])
                if curr["start_time_ticks"] != info["start_time_ticks"]:
                    break
            except LookupError:
                break
            time.sleep(0.05)

        exited = not os.path.exists(f"/proc/{info['pid']}")
        if not exited:
            try:
                p_obj = psutil.Process(info["pid"])
                exited = p_obj.status() == psutil.STATUS_ZOMBIE
                if exited:
                    try:
                        p_obj.wait(timeout=0.1)
                    except Exception:
                        pass
            except psutil.NoSuchProcess:
                exited = True

        return {
            "status": "TERMINATED" if exited else "TIMEOUT_ALIVE",
            "pid": info["pid"],
            "ref": ref,
            "verified_exit": exited,
        }

    def kill_process(self, ref: str, force: bool = True, kill_tree: bool = True) -> Dict[str, Any]:
        """Gửi SIGKILL cưỡng chế qua Go Agent hoặc /proc fallback (không dùng -pgid)."""
        payload = {
            "ref": ref,
            "force": force,
            "kill_tree": kill_tree,
        }
        res = self._query_go_agent("/api/v1/agent/processes/kill", method="POST", payload=payload, timeout=5.0)
        if res and "_error_status" not in res:
            return res
        if res and "_error_status" in res:
            code = int(res.get("_error_status") or 500)
            err_body = res.get("_error_body")
            err_msg = err_body.get("error") if isinstance(err_body, dict) else str(err_body)
            if code == 403:
                raise PermissionError(err_msg)
            if code == 404:
                raise LookupError(err_msg)
            raise RuntimeError(f"HTTP_{code}: {err_msg}")

        info = self._validate_proc_ref(ref)
        targets = self._collect_safe_descendants(info["pid"]) if kill_tree else []
        targets.append(info)
        for t in targets:
            try:
                curr = self._parse_proc_stat(t["pid"])
                if curr["start_time_ticks"] == t["start_time_ticks"]:
                    os.kill(t["pid"], signal.SIGKILL)
            except (LookupError, ProcessLookupError):
                continue

        time.sleep(0.05)
        exited = not os.path.exists(f"/proc/{info['pid']}")
        return {
            "status": "KILLED" if exited else "SURVIVED",
            "pid": info["pid"],
            "ref": ref,
            "verified_exit": exited,
        }

process_monitor = ProcessMonitorAdapter(server_id=settings.LOCAL_HOST_ID, demo_mode=False)
