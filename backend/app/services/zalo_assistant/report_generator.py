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
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.atomic_json import PersistenceCorruptionError, atomic_write_json, safe_load_json
from app.db.models import DailyReportSnapshotRecord, ReportRunRecord
from app.db.session import DatabaseManager

logger = logging.getLogger(__name__)
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_dashboard_url() -> str:
    base = (
        os.getenv("LABOPS_PUBLIC_DASHBOARD_URL")
        or os.getenv("NEXT_PUBLIC_APP_URL")
        or "https://aiotserver.fitdnu.id.vn"
    ).strip().rstrip("/")
    if "localhost" in base.lower() or "127.0.0.1" in base:
        return "https://aiotserver.fitdnu.id.vn/dashboard/ai-assistant"
    return f"{base}/dashboard/ai-assistant"


class ReportGenerator:
    def __init__(self, data_dir: str, db_manager: Optional[DatabaseManager] = None):
        self.data_dir = data_dir
        self.db = db_manager or DatabaseManager(os.path.abspath(data_dir))
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
            # Fallback reads live normalized telemetry rather than hardcoded status
            try:
                from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector

                live_snap = UnifiedTelemetryCollector(self.data_dir).collect_snapshot()
                for h in live_snap.get("hosts") or []:
                    hid = str(h.get("hostname") or h.get("host_id"))
                    st = str(h.get("status") or "UNKNOWN").upper()
                    if st == "ONLINE":
                        online_hosts.append(hid)
                        md_lines.append(
                            f"- **{h.get('display_name') or hid} ({hid})**: ONLINE — CPU {h.get('cpu_usage_percent')}%, RAM {h.get('ram_used_gb')}/{h.get('ram_total_gb')} GiB."
                        )
                    else:
                        offline_hosts.append(hid)
                        md_lines.append(
                            f"- **{h.get('display_name') or hid} ({hid})**: {st} — Telemetry hiện không khả dụng / gián đoạn kết nối."
                        )
            except Exception:
                pass
            if not online_hosts and not offline_hosts:
                md_lines.append("- Trạng thái node hiện chưa xác minh (UNKNOWN) do thiếu dữ liệu telemetry.")

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

    @staticmethod
    def compute_daily_window(now_utc: Optional[datetime] = None) -> tuple[str, datetime, datetime]:
        """Compute deterministic reporting window: 08:00 yesterday -> 08:00 today (Asia/Ho_Chi_Minh)."""
        ref_utc = now_utc or datetime.now(timezone.utc)
        if ref_utc.tzinfo is None:
            ref_utc = ref_utc.replace(tzinfo=timezone.utc)
        local_now = ref_utc.astimezone(VN_TZ)
        today_08 = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
        if local_now < today_08:
            end_local = today_08 - timedelta(days=1)
        else:
            end_local = today_08
        start_local = end_local - timedelta(days=1)
        period_key = end_local.strftime("%Y-%m-%d")
        return period_key, start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    def get_daily_report_by_period(self, period_key: str) -> Optional[Dict[str, Any]]:
        with self.db.session_scope() as session:
            row = session.execute(
                select(ReportRunRecord).where(
                    ReportRunRecord.report_type == "DAILY_EXECUTIVE",
                    ReportRunRecord.period_key == period_key,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "id": row.id,
                "report_type": row.report_type,
                "period_key": row.period_key,
                "title": row.title,
                "author": row.author,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else _now(),
                "window_start_utc": row.window_start_utc.isoformat() if row.window_start_utc else None,
                "window_end_utc": row.window_end_utc.isoformat() if row.window_end_utc else None,
                "zalo_summary": row.zalo_summary,
                "content": row.markdown_content,
                "ai_insights": dict(row.ai_insights_json or {}),
                "delivery_state": dict(row.delivery_state_json or {}),
            }

    def mark_daily_delivery_state(self, period_key: str, delivery_state: Dict[str, Any]) -> None:
        with self.db.session_scope() as session:
            row = session.execute(
                select(ReportRunRecord).where(
                    ReportRunRecord.report_type == "DAILY_EXECUTIVE",
                    ReportRunRecord.period_key == period_key,
                )
            ).scalar_one_or_none()
            if row is not None:
                merged = dict(row.delivery_state_json or {})
                merged.update(delivery_state)
                row.delivery_state_json = merged

    def generate_daily_executive_briefing(
        self,
        *,
        now_utc: Optional[datetime] = None,
        author: str = "Automated Daily Scheduler (08:00 ICT)",
        force: bool = False,
        ai_client: Optional[Any] = None,
        telemetry_collector: Optional[Any] = None,
        incident_engine: Optional[Any] = None,
        repo_requests: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        period_key, win_start_utc, win_end_utc = self.compute_daily_window(now_utc)
        if not force:
            existing = self.get_daily_report_by_period(period_key)
            if existing is not None:
                return existing

        from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
        from app.services.zalo_assistant.repo_alert_monitor import _fetch_agent_requests_sync
        from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector

        collector = telemetry_collector or UnifiedTelemetryCollector(self.data_dir, db_manager=self.db)
        inc_eng = incident_engine or UnifiedIncidentEngine(self.data_dir, db_manager=self.db)

        snap = collector.collect_snapshot()
        hist_24h = collector.get_history_kpis(window_hours=24, end_utc=win_end_utc)
        all_incidents = inc_eng.list_incidents(limit=200)

        def _in_win(ts_raw: Any) -> bool:
            if not isinstance(ts_raw, str) or not ts_raw.strip():
                return False
            try:
                dt = datetime.fromisoformat(ts_raw.strip().replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_utc = dt.astimezone(timezone.utc)
                return win_start_utc <= dt_utc <= win_end_utc
            except ValueError:
                return False

        # 1. Executive Summary KPIs (deterministic only)
        hosts = snap.get("hosts") or []
        gpus = snap.get("gpus") or []
        workspaces = snap.get("workspaces") or []
        jobs = snap.get("jobs") or []
        services = snap.get("services") or {}

        online_servers = sum(1 for h in hosts if str(h.get("status")).upper() in {"ONLINE", "DEGRADED"})
        disconnected_nodes = [
            f"{h.get('hostname')} ({h.get('status')})"
            for h in hosts
            if str(h.get("status")).upper() not in {"ONLINE", "DEGRADED"}
        ]
        open_incidents = [i for i in all_incidents if str(i.get("status")).upper() != "RESOLVED"]
        crit_incidents = [i for i in open_incidents if str(i.get("severity")).upper() == "P0"]
        action_needed = [i for i in open_incidents if str(i.get("severity")).upper() in {"P0", "P1"}]

        overall_health = snap.get("overall_health") or "UNKNOWN"
        if crit_incidents and overall_health == "HEALTHY":
            overall_health = "CRITICAL"
        elif action_needed and overall_health == "HEALTHY":
            overall_health = "DEGRADED"

        # 2. Infrastructure (current snapshot vs 24h avg vs 24h peak; N/A on missing history)
        avail_24h = f"{hist_24h['availability_percent']}%" if hist_24h.get("availability_percent") is not None else "N/A"
        cpu_peak = f"{hist_24h['cpu_peak_percent']}%" if hist_24h.get("cpu_peak_percent") is not None else "N/A"
        cpu_avg = f"{hist_24h['cpu_avg_percent']}%" if hist_24h.get("cpu_avg_percent") is not None else "N/A"
        ram_peak = f"{hist_24h['ram_peak_gb']} GiB" if hist_24h.get("ram_peak_gb") is not None else "N/A"
        ram_avg = f"{hist_24h['ram_avg_gb']} GiB" if hist_24h.get("ram_avg_gb") is not None else "N/A"

        # 3. GPU & Computing
        online_gpus = [g for g in gpus if g.get("host_online")]
        busy_gpus = [g for g in online_gpus if (g.get("vram_used_mb") or 0) >= 512 or (g.get("utilization_gpu") or 0) >= 10]
        gpu_util_avg = f"{hist_24h['gpu_util_avg_percent']}%" if hist_24h.get("gpu_util_avg_percent") is not None else (
            f"{round(sum(float(g.get('utilization_gpu') or 0) for g in online_gpus) / len(online_gpus), 1)}%" if online_gpus else "N/A"
        )
        jobs_running = sum(1 for j in jobs if str(j.get("status")).upper() == "RUNNING")
        jobs_completed = sum(1 for j in jobs if str(j.get("status")).upper() == "COMPLETED" and (_in_win(j.get("ended_at")) or not j.get("ended_at")))
        jobs_failed = sum(1 for j in jobs if str(j.get("status")).upper() == "FAILED" and (_in_win(j.get("ended_at")) or not j.get("ended_at")))
        gpu_faults = sum(1 for g in gpus if g.get("gpu_fault") or int(g.get("xid_errors") or 0) > 0)

        # 4. Storage & Workspace
        storage_lines = [
            f"{h.get('hostname')}: {h.get('disk_used_tb')}/{h.get('disk_total_tb')} TB ({h.get('disk_usage_percent')}%)"
            for h in hosts
            if h.get("disk_total_tb") is not None
        ]
        disk_cap_str = "; ".join(storage_lines) if storage_lines else "N/A"
        disk_growth = f"{hist_24h['disk_growth_gb']:+.2f} GB" if hist_24h.get("disk_growth_gb") is not None else "N/A"
        ws_active = sum(1 for w in workspaces if str(w.get("observed_state") or w.get("desired_state") or "").upper() == "ACTIVE")
        ws_quota_warn = sum(
            1
            for w in workspaces
            if w.get("storage_used_gb") is not None
            and w.get("storage_quota_gb")
            and float(w["storage_used_gb"]) >= 0.85 * float(w["storage_quota_gb"])
        )

        # 5. Security (windowed)
        if repo_requests is None:
            try:
                repo_requests = _fetch_agent_requests_sync()
            except Exception:
                repo_requests = []
        win_repos = [r for r in (repo_requests or []) if _in_win(r.get("submitted_at") or r.get("updated_at"))]
        repo_clones_24h = len(win_repos)
        repo_review_cnt = sum(
            1
            for r in win_repos
            if str(r.get("review_status") or "").upper() == "REVIEW_REQUIRED" or str(r.get("state") or "").upper() == "REVIEW_REQUIRED"
        )
        repo_scan_failed = sum(
            1
            for r in win_repos
            if str(r.get("review_status") or "").upper() == "SCAN_FAILED" or str(r.get("state") or "").upper() == "FAILED"
        )
        sec_incidents = [i for i in open_incidents if str(i.get("source")) in {"repository_guard", "security"}]
        auth_anomalies = sum(1 for i in all_incidents if "AUTH" in str(i.get("event_type") or "").upper() and _in_win(i.get("last_observed_at")))

        kpis = {
            "overall_health": overall_health,
            "online_servers": online_servers,
            "total_servers": len(hosts),
            "critical_incidents": len(crit_incidents),
            "admin_action_items": len(action_needed),
            "availability_24h": avail_24h,
            "cpu_avg_24h": cpu_avg,
            "cpu_peak_24h": cpu_peak,
            "ram_avg_24h": ram_avg,
            "ram_peak_24h": ram_peak,
            "gpu_busy": len(busy_gpus),
            "gpu_total_online": len(online_gpus),
            "gpu_util_avg": gpu_util_avg,
            "jobs_running": jobs_running,
            "jobs_completed": jobs_completed,
            "jobs_failed": jobs_failed,
            "gpu_faults": gpu_faults,
            "disk_capacity": disk_cap_str,
            "disk_growth_24h": disk_growth,
            "workspaces_active": ws_active,
            "quota_warnings": ws_quota_warn,
            "repo_clones_24h": repo_clones_24h,
            "repo_review_required": repo_review_cnt,
            "repo_scan_failed": repo_scan_failed,
            "auth_anomalies": auth_anomalies,
            "security_incidents_open": len(sec_incidents),
            "history_samples": hist_24h.get("sample_count", 0),
        }

        # 6. Deterministic rule-based insights (max 3) & optional AI phrasing (never invent numbers)
        deterministic_insights: List[str] = []
        if disconnected_nodes:
            deterministic_insights.append(f"Node mất kết nối/thiếu telemetry: {', '.join(disconnected_nodes)}.")
        if repo_review_cnt > 0 or repo_scan_failed > 0:
            deterministic_insights.append(
                f"Trong 24h có {repo_clones_24h} lượt clone kho mã: {repo_review_cnt} cần kiểm duyệt, {repo_scan_failed} lỗi quét."
            )
        if crit_incidents or action_needed:
            deterministic_insights.append(
                f"Hiện có {len(open_incidents)} sự cố chưa đóng ({len(crit_incidents)} P0, {len(action_needed) - len(crit_incidents)} P1)."
            )
        if not deterministic_insights:
            if hist_24h.get("sample_count", 0) < 3:
                deterministic_insights.append("Cụm máy chủ chính ổn định; dữ liệu lịch sử 24h mới thu thập nên xu hướng hiển thị N/A.")
            else:
                deterministic_insights.append(f"Hệ thống vận hành ổn định (Availability 24h: {avail_24h}, CPU peak: {cpu_peak}).")
        deterministic_insights = deterministic_insights[:3]

        ai_insights = list(deterministic_insights)
        ai_used = False
        if ai_client is not None:
            try:
                prompt = (
                    "Từ dữ liệu KPI đã tính sẵn bên dưới, hãy viết tối đa 3 gạch đầu dòng nhận định vận hành ngắn gọn bằng tiếng Việt. "
                    "TUYỆT ĐỐI KHÔNG tự tạo số liệu mới, chỉ diễn giải đúng các số đã cho:\n"
                    + f"KPIs: {kpis}"
                )
                ai_resp = ai_client.generate_chat_response(
                    [{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=260,
                )
                if isinstance(ai_resp, str) and ai_resp.strip() and not ai_resp.startswith(("Không thể kết nối", "Lỗi cổng")):
                    lines = [ln.strip().lstrip("-•* ").strip() for ln in ai_resp.splitlines() if ln.strip()][:3]
                    if lines:
                        ai_insights = lines
                        ai_used = True
            except Exception:
                pass

        # 7. Today's Priorities (max 3)
        priorities: List[str] = []
        for inc in sorted(action_needed, key=lambda x: (0 if x.get("severity") == "P0" else 1))[:2]:
            priorities.append(f"[{inc.get('severity')}] {inc.get('title')} ({inc.get('incident_id')})")
        if repo_review_cnt > 0 and len(priorities) < 3:
            priorities.append(f"Kiểm duyệt hậu kiểm {repo_review_cnt} repository có phát hiện bảo mật.")
        if repo_scan_failed > 0 and len(priorities) < 3:
            priorities.append(f"Kiểm tra & quét lại {repo_scan_failed} repository bị SCAN_FAILED.")
        if disconnected_nodes and len(priorities) < 3:
            priorities.append(f"Kiểm tra kết nối các node ngoại tuyến: {', '.join(disconnected_nodes)}.")
        if not priorities:
            priorities.append("Không có sự cố P0/P1 tồn đọng; tiếp tục duy trì giám sát.")
        priorities = priorities[:3]

        win_label = f"{win_start_utc.astimezone(VN_TZ).strftime('%d/%m %H:%M')} → {win_end_utc.astimezone(VN_TZ).strftime('%d/%m %H:%M')} ICT"
        dash_url = _public_dashboard_url()

        md_lines = [
            f"# BÁO CÁO ĐIỀU HÀNH HẰNG NGÀY LABOPS — {period_key}",
            f"*Kỳ báo cáo: {win_label} • Tạo bởi: {author}*",
            "",
            "## 1. Executive Summary",
            f"- Overall Health: **{overall_health}**",
            f"- Server Online: **{online_servers}/{len(hosts)}**",
            f"- Critical Incidents (P0): **{len(crit_incidents)}**",
            f"- Vấn đề cần Admin xử lý: **{len(action_needed)}**",
            "",
            "## 2. Infrastructure (24h Window & Snapshot)",
            f"- Availability (24h): **{avail_24h}** (mẫu: {hist_24h.get('sample_count', 0)})",
            f"- CPU (24h Avg / Peak): **{cpu_avg} / {cpu_peak}**",
            f"- RAM (24h Avg / Peak): **{ram_avg} / {ram_peak}**",
            f"- Node mất kết nối / bất thường: **{', '.join(disconnected_nodes) if disconnected_nodes else 'Không'}**",
            "- Trạng thái dịch vụ: " + (", ".join(f"{k}={v.get('status', 'UNKNOWN')}" for k, v in services.items() if isinstance(v, dict)) or "N/A"),
            "",
            "## 3. GPU & Computing",
            f"- GPU hoạt động / khả dụng: **{len(busy_gpus)}/{len(online_gpus)}** (tổng cấu hình: {len(gpus)})",
            f"- GPU Utilization trung bình: **{gpu_util_avg}**",
            f"- Jobs (Running / Completed / Failed): **{jobs_running} / {jobs_completed} / {jobs_failed}**",
            f"- GPU Faults / Xid: **{gpu_faults}**",
            "",
            "## 4. Storage & Workspace",
            f"- Dung lượng hiện tại: **{disk_cap_str}**",
            f"- Tăng trưởng 24h: **{disk_growth}**",
            f"- Workspace hoạt động: **{ws_active}/{len(workspaces)}** (cảnh báo quota: {ws_quota_warn})",
            "",
            "## 5. Security",
            f"- Lượt clone repository (24h): **{repo_clones_24h}**",
            f"- Cần kiểm duyệt hậu kiểm: **{repo_review_cnt}** | Lỗi quét: **{repo_scan_failed}**",
            f"- Bất thường xác thực: **{auth_anomalies}** | Sự cố an ninh đang mở: **{len(sec_incidents)}**",
            "",
            "## 6. AI Operational Insights",
        ]
        for ins in ai_insights:
            md_lines.append(f"- {ins}")
        if hist_24h.get("sample_count", 0) == 0:
            md_lines.append("- *Lưu ý giới hạn: Chưa có mẫu lịch sử 24h nên các chỉ số trung bình/đỉnh hiển thị N/A; không mặc định thiếu log là không có sự cố.*")

        md_lines.extend(["", "## 7. Today's Priorities"])
        for idx, pr in enumerate(priorities, 1):
            md_lines.append(f"{idx}. {pr}")
        md_lines.append(f"\nXem chi tiết trên Dashboard: {dash_url}")

        md_content = "\n".join(md_lines)

        # Concise Zalo executive summary strictly under 1,800 characters
        zalo_lines = [
            f"📊 DAILY BRIEF ({period_key} • 08:00 ICT)",
            f"• Health: {overall_health} | Node Online: {online_servers}/{len(hosts)} | P0: {len(crit_incidents)} | Action: {len(action_needed)}",
            f"• Infra 24h: Avail {avail_24h} | CPU Peak {cpu_peak} | RAM Peak {ram_peak}",
            f"• GPU & Jobs: {len(busy_gpus)}/{len(online_gpus)} busy ({gpu_util_avg}) | Run/Ok/Fail: {jobs_running}/{jobs_completed}/{jobs_failed}",
            f"• Storage & WS: Growth {disk_growth} | WS Active {ws_active}/{len(workspaces)} (Quota warn: {ws_quota_warn})",
            f"• Security 24h: Clones {repo_clones_24h} (Review: {repo_review_cnt}, ScanFail: {repo_scan_failed}) | SecInc: {len(sec_incidents)}",
            "💡 Insights:",
        ]
        for ins in ai_insights[:3]:
            zalo_lines.append(f"  - {ins[:140]}")
        zalo_lines.append("🎯 Priorities:")
        for idx, pr in enumerate(priorities[:3], 1):
            zalo_lines.append(f"  {idx}. {pr[:140]}")
        zalo_lines.append(f"👉 {dash_url}")

        zalo_summary = "\n".join(zalo_lines)
        if len(zalo_summary) > 1780:
            zalo_summary = zalo_summary[:1750].rstrip() + f"\n👉 {dash_url}"
        if len(zalo_summary) >= 1800:
            zalo_summary = zalo_summary[:1795]

        report_id = f"daily-{period_key}-{uuid.uuid4().hex[:6]}"
        created_dt = datetime.now(timezone.utc)
        title = f"Daily Executive Briefing — {period_key}"

        with self.db.session_scope() as session:
            existing_row = session.execute(
                select(ReportRunRecord).where(
                    ReportRunRecord.report_type == "DAILY_EXECUTIVE",
                    ReportRunRecord.period_key == period_key,
                )
            ).scalar_one_or_none()
            if existing_row is not None and not force:
                return self.get_daily_report_by_period(period_key) or {}
            if existing_row is not None and force:
                existing_row.author = author
                existing_row.title = title
                existing_row.zalo_summary = zalo_summary
                existing_row.markdown_content = md_content
                existing_row.ai_insights_json = {"insights": ai_insights, "ai_used": ai_used, "priorities": priorities, "kpis": kpis}
                existing_row.window_start_utc = win_start_utc
                existing_row.window_end_utc = win_end_utc
                report_id = existing_row.id
            else:
                session.add(
                    ReportRunRecord(
                        id=report_id,
                        report_type="DAILY_EXECUTIVE",
                        period_key=period_key,
                        window_start_utc=win_start_utc,
                        window_end_utc=win_end_utc,
                        status="GENERATED",
                        author=author,
                        title=title,
                        zalo_summary=zalo_summary,
                        markdown_content=md_content,
                        ai_insights_json={"insights": ai_insights, "ai_used": ai_used, "priorities": priorities, "kpis": kpis},
                        delivery_state_json={},
                        created_at=created_dt,
                    )
                )
                session.add(
                    DailyReportSnapshotRecord(
                        id=f"dsnap-{uuid.uuid4().hex[:10]}",
                        report_run_id=report_id,
                        host_id="cluster",
                        collected_at=created_dt,
                        overall_health=overall_health,
                        kpis_json=kpis,
                        raw_snapshot_json=snap,
                    )
                )

        # Also mirror into zalo_reports.json for backward-compatible UI listing
        entry = {
            "id": report_id,
            "report_type": "DAILY_EXECUTIVE",
            "period_key": period_key,
            "title": title,
            "created_at": created_dt.isoformat(),
            "author": author,
            "status": "GENERATED",
            "zalo_summary": zalo_summary,
            "content": md_content,
            "ai_insights": {"insights": ai_insights, "ai_used": ai_used, "priorities": priorities},
            "summary": {
                "infrastructure": f"Online {online_servers}/{len(hosts)} ({overall_health})",
                "running_jobs": jobs_running,
                "kpis": kpis,
            },
        }
        with self._locked() as state:
            reports = [r for r in state.get("reports", []) if r.get("id") != report_id]
            reports.append(entry)
            if len(reports) > 60:
                reports = reports[-60:]
            state["reports"] = reports
        return entry
