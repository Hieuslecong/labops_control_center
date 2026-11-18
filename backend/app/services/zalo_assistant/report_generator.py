"""
Weekly Report Generator for LabOps AI Assistant
Synthesizes:
- Completed tasks
- Ongoing research projects
- GitHub activity & commits
- Jobs and GPU workloads
- System infrastructure status (Workstation primary, Rack offline note)
- Incidents & alerts
- Backlog & Next week plans
Default schedule: Friday 17:00 (Asia/Ho_Chi_Minh).
"""
from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.db.atomic_json import PersistenceCorruptionError, atomic_write_json, safe_load_json

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportGenerator:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.reports_path = os.path.join(data_dir, "zalo_reports.json")
        self.lock_path = self.reports_path + ".lock"
        os.makedirs(data_dir, exist_ok=True)
        self._ensure_bootstrap()

    @contextmanager
    def _locked(self):
        with open(self.lock_path, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                state = self._load()
                yield state
                self._save(state)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _default_state(self) -> Dict[str, Any]:
        return {
            "reports": [],
            "schedule": {"day": "Friday", "time": "17:00", "timezone": "Asia/Ho_Chi_Minh"},
            "last_reported_week": None,
        }

    def _load(self) -> Dict[str, Any]:
        state = safe_load_json(self.reports_path, self._default_state)
        if not isinstance(state, dict):
            raise PersistenceCorruptionError(
                f"Invalid reports state structure in '{self.reports_path}': expected object"
            )
        state.setdefault("reports", [])
        state.setdefault("schedule", {"day": "Friday", "time": "17:00", "timezone": "Asia/Ho_Chi_Minh"})
        state.setdefault("last_reported_week", None)
        return state

    def _save(self, state: Dict[str, Any]) -> None:
        atomic_write_json(self.reports_path, state, mode=0o600, use_lock=False)

    def _ensure_bootstrap(self) -> None:
        with self._locked():
            pass

    def get_last_reported_week(self) -> Optional[str]:
        with self._locked() as state:
            val = state.get("last_reported_week")
            return str(val) if val else None

    def set_last_reported_week(self, week_str: str) -> None:
        with self._locked() as state:
            state["last_reported_week"] = week_str

    def _repo_dir(self) -> str:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

    def _get_git_branch(self) -> str:
        try:
            res = subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self._repo_dir(),
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return "unknown"

    def _get_git_activity(self) -> List[Dict[str, str]]:
        repo_dir = self._repo_dir()
        commits: List[Dict[str, str]] = []
        cmds = [
            ["git", "-c", "core.hooksPath=/dev/null", "log", "--since=7.days", "-n", "15", "--pretty=format:%h|%an|%ar|%s"],
            ["git", "-c", "core.hooksPath=/dev/null", "log", "-n", "5", "--pretty=format:%h|%an|%ar|%s"],
        ]
        for cmd in cmds:
            try:
                res = subprocess.run(
                    cmd,
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if res.returncode == 0 and res.stdout.strip():
                    for line in res.stdout.strip().splitlines():
                        parts = line.split("|", 3)
                        if len(parts) == 4:
                            commits.append({
                                "hash": parts[0],
                                "author": parts[1],
                                "time": parts[2],
                                "message": parts[3],
                            })
                    if commits:
                        break
            except Exception as exc:
                logger.debug("Lỗi đọc git activity: %s", exc)
        return commits

    def _get_active_jobs_summary(self) -> Dict[str, Any]:
        """
        Lấy danh sách các tác vụ tính toán thực tế của người dùng (User Workloads).
        Tự động lọc bỏ các tiến trình hệ thống, daemon nền, watch/shell loops
        và gom nhóm các worker PyTorch/DataLoader để dễ theo dõi.
        """
        gpu_pids: Dict[int, int] = {}
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    p = [x.strip() for x in line.split(",")]
                    if len(p) >= 2 and p[0].isdigit():
                        gpu_pids[int(p[0])] = int(p[1])
        except Exception:
            pass

        try:
            import psutil
        except ImportError:
            psutil = None

        if not psutil:
            return {"running_count": len(gpu_pids), "jobs": []}

        SYSTEM_USERS = {"root", "daemon", "systemd", "aiot-ws1"}
        IGNORED_CMDS = {"watch", "top", "htop", "nvtop", "pgrep", "sleep", "tmux", "tail", "less", "sshd", "node"}

        raw_jobs: Dict[tuple, Dict[str, Any]] = {}
        for p in psutil.process_iter(["pid", "name", "cmdline", "username", "uids", "cpu_percent"]):
            try:
                info = p.info
                pid = info["pid"]
                user = info["username"] or "unknown"
                uid = info["uids"].real if info.get("uids") else 0

                if uid < 1000 or user in SYSTEM_USERS:
                    continue

                cmdline = info["cmdline"] or []
                if not cmdline:
                    continue
                name = info["name"] or ""
                if name in IGNORED_CMDS:
                    continue

                is_gpu = pid in gpu_pids
                is_python = "python" in name.lower() or any(k.endswith(".py") for k in cmdline)
                if not (is_gpu or is_python):
                    continue

                # Tìm tên file script thực tế
                script_name = ""
                for arg in cmdline:
                    if arg.endswith(".py") and not arg.startswith("-"):
                        script_name = os.path.basename(arg)
                        break
                if not script_name:
                    script_name = name

                group_key = (user, script_name)
                vram = gpu_pids.get(pid, 0)

                if group_key not in raw_jobs:
                    raw_jobs[group_key] = {
                        "user": user,
                        "script": script_name,
                        "root_pid": pid,
                        "vram_mb": vram,
                        "is_gpu": is_gpu,
                        "count": 1,
                    }
                else:
                    raw_jobs[group_key]["count"] += 1
                    raw_jobs[group_key]["vram_mb"] = max(raw_jobs[group_key]["vram_mb"], vram)
                    if is_gpu:
                        raw_jobs[group_key]["is_gpu"] = True
                        raw_jobs[group_key]["root_pid"] = pid
            except Exception:
                continue

        jobs = []
        for (user, script), item in raw_jobs.items():
            vram_str = f"{item['vram_mb']:,} MiB VRAM (GPU A30)" if item["is_gpu"] else "CPU Compute"
            count_str = f" ({item['count']} tiến trình)" if item["count"] > 1 else ""
            display_title = f"{user} • {script}{count_str}"
            jobs.append({
                "pid": item["root_pid"],
                "name": display_title,
                "user": user,
                "script": script,
                "vram": vram_str,
                "is_gpu": item["is_gpu"],
                "count": item["count"],
                "display": f"{display_title} [{vram_str}]",
            })

        # Sắp xếp ưu tiên GPU lên đầu
        jobs.sort(key=lambda x: (not x["is_gpu"], x["user"]))

        return {
            "running_count": len(jobs),
            "jobs": jobs,
        }

    def _collect_runtime_snapshot(self, system_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {
            "servers": [],
            "gpus": [],
            "audit_logs_7d": [],
            "events": [],
            "quota": {},
        }
        if isinstance(system_status, dict):
            snapshot["servers"] = list(system_status.get("servers") or [])
            snapshot["gpus"] = list(system_status.get("gpus") or [])
            snapshot["events"] = list(system_status.get("events") or [])
            snapshot["audit_logs_7d"] = list(system_status.get("audit_logs") or [])

        if not snapshot["servers"] or not snapshot["gpus"]:
            try:
                from app.services.labops_service import service as core_svc

                if not snapshot["servers"]:
                    snapshot["servers"] = list(getattr(core_svc, "servers", []) or [])
                if not snapshot["gpus"]:
                    snapshot["gpus"] = list(getattr(core_svc, "gpus", []) or [])
                if not snapshot["events"]:
                    snapshot["events"] = list(getattr(core_svc, "events", []) or [])

                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                raw_logs = list(getattr(core_svc, "audit_logs", []) or [])
                recent_logs = []
                for entry in raw_logs:
                    ts = str(entry.get("timestamp") or "")
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt >= cutoff:
                            recent_logs.append(entry)
                    except Exception:
                        continue
                snapshot["audit_logs_7d"] = recent_logs
            except Exception as exc:
                logger.debug("Không thể đọc dữ liệu từ labops_service: %s", exc)

        try:
            from app.services.zalo_assistant.quota_ledger import QuotaLedger
            snapshot["quota"] = QuotaLedger(self.data_dir).get_status()
        except Exception as exc:
            logger.debug("Không thể đọc trạng thái QuotaLedger: %s", exc)

        return snapshot

    def generate_weekly_report(
        self,
        *,
        system_status: Optional[Dict[str, Any]] = None,
        author: str = "LabOps AI Assistant",
    ) -> Dict[str, Any]:
        report_id = f"rpt-{uuid.uuid4().hex[:8]}"
        created_at = _now()
        branch = self._get_git_branch()
        git_commits = self._get_git_activity()
        jobs_summary = self._get_active_jobs_summary()
        snap = self._collect_runtime_snapshot(system_status=system_status)

        date_str = datetime.now().strftime("%d/%m/%Y")
        servers = snap["servers"]
        gpus = snap["gpus"]
        audit_7d = snap["audit_logs_7d"]
        quota = snap["quota"]

        md_lines = [
            f"# BÁO CÁO TUẦN HOẠT ĐỘNG LABOPS — {date_str}",
            f"*Được tạo tự động bởi {author} lúc {datetime.now().strftime('%H:%M %d/%m/%Y')}*",
            "",
            "## 1. TÌNH TRẠNG HẠ TẦNG VÀ HỆ THỐNG",
        ]

        online_hosts: List[str] = []
        offline_hosts: List[str] = []
        if servers:
            for s in servers:
                srv_id = str(s.get("id") or "")
                raw_host = str(s.get("hostname") or "").strip()
                if not raw_host or raw_host.lower() in {"unknown", "chưa xác minh", "not_verified"}:
                    hostname = "aiotlab-rack" if srv_id == "track" else ("aiot-ws1" if srv_id == "workstation" else (srv_id or "unknown"))
                else:
                    hostname = raw_host
                name = s.get("name") or hostname
                raw_st = str(s.get("status") or "").strip().upper()
                cpu = s.get("cpu_usage_percent", s.get("cpuUsagePercent", 0))
                ram_used = s.get("ram_used_gb", s.get("ramUsedGb", 0))
                ram_total = s.get("ram_total_gb", s.get("ramTotalGb", 0))
                if raw_st == "ONLINE":
                    online_hosts.append(hostname)
                    md_lines.append(
                        f"- **{name} ({hostname})**: ONLINE — CPU {cpu}%, RAM {ram_used}/{ram_total} GiB."
                    )
                else:
                    offline_hosts.append(hostname)
                    md_lines.append(
                        f"- **{name} ({hostname})**: OFFLINE — Telemetry hiện không khả dụng / gián đoạn kết nối."
                    )
        else:
            online_hosts.append("aiot-ws1")
            offline_hosts.append("aiotlab-rack")
            md_lines.append("- **Workstation (aiot-ws1)**: ONLINE — Máy chủ điều hành cục bộ.")
            md_lines.append("- **Server Rack (aiotlab-rack)**: OFFLINE — Hiện không có telemetry từ node này.")

        if gpus:
            for g in gpus:
                lbl = g.get("display_label") or g.get("id")
                model = g.get("name") or g.get("model") or "GPU"
                v_used = g.get("vram_used_mb", 0)
                v_tot = g.get("vram_total_mb", 0)
                temp = g.get("temp_celsius", 0)
                srv_id = g.get("server_id", "")
                md_lines.append(
                    f"- **GPU `{lbl}` ({model}, host `{srv_id}`)**: VRAM {v_used:,}/{v_tot:,} MiB, nhiệt độ {temp}°C."
                )

        md_lines.extend([
            "",
            "## 2. DỰ ÁN & TIẾN TRÌNH TÁC VỤ (JOBS)",
            f"- Số lượng tác vụ người dùng đang vận hành: **{jobs_summary['running_count']} tác vụ**.",
        ])

        if jobs_summary["jobs"]:
            for j in jobs_summary["jobs"]:
                name_str = j.get("display") or j.get("name") or j.get("script") or "python"
                md_lines.append(f"  • PID `{j['pid']}` — {name_str}")
        else:
            md_lines.append("  • Hiện không có tác vụ nghiên cứu độc lập đang chạy.")

        md_lines.extend([
            "",
            "## 3. HOẠT ĐỘNG PHÁT TRIỂN & GITHUB",
            f"- **Nhánh hiện tại**: `{branch}`",
            "- Các cập nhật commit gần nhất (7 ngày qua):",
        ])

        if git_commits:
            for c in git_commits:
                md_lines.append(f"  • [`{c['hash']}`] {c['message']} *({c['author']}, {c['time']})*")
        else:
            md_lines.append("  • Chưa phát hiện commit mới trong 7 ngày qua.")

        # Section 4: Dynamic alerts & incidents
        md_lines.extend(["", "## 4. SỰ CỐ & CẢNH BÁO"])
        alerts_added = 0
        for host in offline_hosts:
            md_lines.append(f"- [CẢNH BÁO] Node `{host}` đang ngoại tuyến (`OFFLINE`); dữ liệu từ node này không được nội suy.")
            alerts_added += 1
        for g in gpus:
            temp = float(g.get("temp_celsius") or 0)
            v_used = float(g.get("vram_used_mb") or 0)
            v_tot = float(g.get("vram_total_mb") or 0)
            if temp >= 85:
                md_lines.append(f"- [NHIỆT ĐỘ] GPU `{g.get('id')}` đạt {temp}°C.")
                alerts_added += 1
            if v_tot > 0 and (v_used / v_tot) >= 0.9:
                md_lines.append(f"- [VRAM] GPU `{g.get('id')}` đang dùng {round(v_used / v_tot * 100, 1)}% VRAM.")
                alerts_added += 1
        for ev in snap["events"][:5]:
            ev_msg = str(ev.get("message") or ev.get("title") or ev.get("description") or "").strip()
            if ev_msg:
                md_lines.append(f"- [{str(ev.get('severity') or 'INFO').upper()}] {ev_msg}")
                alerts_added += 1
        if alerts_added == 0:
            md_lines.append("- Không ghi nhận cảnh báo hạ tầng nghiêm trọng trong kỳ báo cáo.")

        # Section 5: 7-day operational audit summary & Zalo quota status
        md_lines.extend(["", "## 5. HOẠT ĐỘNG QUẢN TRỊ & HẠN MỨC VẬN HÀNH (7 NGÀY)"])
        if audit_7d:
            action_counts: Dict[str, int] = {}
            failed_count = 0
            for a in audit_7d:
                act = str(a.get("action") or "EVENT")
                action_counts[act] = action_counts.get(act, 0) + 1
                if str(a.get("result") or "").upper() in {"DENIED", "FAILED", "ERROR", "PARTIAL_FAILURE"}:
                    failed_count += 1
            md_lines.append(f"- Tổng sự kiện audit 7 ngày qua: **{len(audit_7d)}** (thất bại/từ chối: {failed_count}).")
            top_actions = sorted(action_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
            for act, cnt in top_actions:
                md_lines.append(f"  • `{act}`: {cnt} lượt")
        else:
            md_lines.append("- Chưa có sự kiện audit mới được ghi nhận trong 7 ngày qua.")

        if quota:
            md_lines.append(
                f"- Hạn mức Zalo tháng `{quota.get('current_month')}`: "
                f"**{quota.get('total_sent', 0)}/{quota.get('total_limit', 3000)}** tin "
                f"(thông thường còn {quota.get('standard_remaining', 0)}, khẩn cấp còn {quota.get('critical_remaining', 0)})."
            )

        # Section 6: Dynamic backlog based on live status
        md_lines.extend(["", "## 6. KẾ HOẠCH TIẾP THEO & HÀNH ĐỘNG ĐỀ XUẤT"])
        if offline_hosts:
            md_lines.append(f"- [ ] Kiểm tra kết nối vật lý và mTLS/SSH cho node đang ngoại tuyến: {', '.join(offline_hosts)}.")
        else:
            md_lines.append("- [ ] Tiếp tục duy trì giám sát nhiệt độ GPU và phân bổ tài nguyên nghiên cứu.")
        md_lines.append("- [ ] Rà soát hàng đợi phê duyệt Repository Guard và nhật ký bảo mật định kỳ.")

        full_content = "\n".join(md_lines)
        infra_summary = f"Online: {', '.join(online_hosts) or 'none'} / Offline: {', '.join(offline_hosts) or 'none'}"

        report_entry = {
            "id": report_id,
            "title": f"Báo cáo tuần LabOps ({date_str})",
            "created_at": created_at,
            "author": author,
            "status": "GENERATED",
            "content": full_content,
            "summary": {
                "infrastructure": infra_summary,
                "running_jobs": jobs_summary["running_count"],
                "git_commits_count": len(git_commits),
                "audit_events_7d": len(audit_7d),
            },
        }

        with self._locked() as state:
            state.setdefault("reports", []).append(report_entry)
            if len(state["reports"]) > 50:
                state["reports"] = state["reports"][-50:]

        return report_entry

    def list_reports(self) -> List[Dict[str, Any]]:
        with self._locked() as state:
            return list(reversed(state.get("reports", [])))

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        with self._locked() as state:
            for r in state.get("reports", []):
                if r.get("id") == report_id:
                    return r
            return None
