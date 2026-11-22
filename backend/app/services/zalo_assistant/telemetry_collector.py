"""
Unified Telemetry & Historical Metrics Collector for LabOps AI SRE Agent v2
Normalizes telemetry from HostRegistry, HostMonitorAdapter, labops_service, GPUs,
Docker/Workspaces, Jobs, Beszel/Arcane (if configured), and external daemons.
Never fabricates missing/stale hosts as ONLINE; persists periodic samples for
24h KPIs, 7d trends, and disk exhaustion forecasting.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from app.db.models import TelemetryHistorySampleRecord
from app.db.session import DatabaseManager
from app.services.host_registry import HEARTBEAT_OFFLINE_SECONDS, HEARTBEAT_STALE_SECONDS

logger = logging.getLogger("labops.sre.telemetry")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def classify_freshness(
    last_heartbeat_iso: Optional[str],
    *,
    raw_status: str = "",
    stale_seconds: int = HEARTBEAT_STALE_SECONDS,
    offline_seconds: int = HEARTBEAT_OFFLINE_SECONDS,
) -> str:
    raw_up = str(raw_status or "").strip().upper()
    dt = _parse_iso(last_heartbeat_iso)
    if dt is None:
        if raw_up in {"OFFLINE", "DISCONNECTED", "UNREACHABLE"}:
            return "OFFLINE"
        return "UNKNOWN"
    age = max(0.0, (_utcnow() - dt).total_seconds())
    if age <= stale_seconds:
        if raw_up == "DEGRADED":
            return "DEGRADED"
        return "ONLINE"
    if age <= offline_seconds:
        return "STALE"
    return "OFFLINE"


class UnifiedTelemetryCollector:
    def __init__(
        self,
        data_dir: str,
        *,
        db_manager: Optional[DatabaseManager] = None,
        core_service: Optional[Any] = None,
    ):
        self.data_dir = os.path.abspath(data_dir)
        self.db = db_manager or DatabaseManager(self.data_dir)
        self._core_service = core_service
        self._last_sample_mono: float = 0.0

    def _service(self) -> Any:
        if self._core_service is not None:
            return self._core_service
        from app.services.labops_service import service

        return service

    def collect_snapshot(
        self,
        *,
        servers_override: Optional[List[Dict[str, Any]]] = None,
        gpus_override: Optional[List[Dict[str, Any]]] = None,
        jobs_override: Optional[List[Dict[str, Any]]] = None,
        workspaces_override: Optional[List[Dict[str, Any]]] = None,
        services_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = _utcnow()
        now_iso = now.isoformat()
        svc = self._service()

        # 1. Servers (HostRegistry + live HostMonitorAdapter merged truthfully)
        raw_servers = list(servers_override) if servers_override is not None else list(getattr(svc, "servers", []) or [])
        host_registry_rows: Dict[str, Dict[str, Any]] = {}
        try:
            for hr in getattr(svc, "host_registry", None).list_hosts(live_servers=raw_servers) or []:
                if isinstance(hr, dict) and hr.get("id"):
                    host_registry_rows[str(hr["id"])] = hr
        except Exception:
            pass

        normalized_hosts: List[Dict[str, Any]] = []
        seen_host_ids = set()
        for srv in raw_servers:
            hid = str(srv.get("id") or srv.get("host_id") or "").strip()
            if not hid or hid in seen_host_ids:
                continue
            seen_host_ids.add(hid)
            reg = host_registry_rows.get(hid, {})

            collected_at = srv.get("collected_at") or reg.get("collected_at")
            last_hb = srv.get("last_heartbeat_at") or reg.get("last_heartbeat_at") or collected_at
            raw_st = str(srv.get("status") or reg.get("observed_state") or "").upper()
            explicit_freshness = str(srv.get("freshness_status") or "").upper()
            if explicit_freshness in {"ONLINE", "DEGRADED", "STALE", "OFFLINE", "UNKNOWN"}:
                freshness = explicit_freshness
            elif srv.get("last_heartbeat_at") or srv.get("collected_at") or not reg.get("freshness"):
                freshness = classify_freshness(last_hb, raw_status=raw_st)
            else:
                freshness = str(reg.get("freshness") or "").upper()
                if freshness not in {"ONLINE", "DEGRADED", "STALE", "OFFLINE", "UNKNOWN"}:
                    freshness = classify_freshness(last_hb, raw_status=raw_st)

            # Never mask STALE/OFFLINE/UNKNOWN as ONLINE
            if freshness in {"OFFLINE", "UNKNOWN"}:
                effective_status = freshness
            elif freshness == "STALE":
                effective_status = "STALE"
            elif raw_st in {"DEGRADED", "MAINTENANCE"}:
                effective_status = raw_st
            else:
                effective_status = "ONLINE"

            telemetry_error = srv.get("error")
            if effective_status in {"OFFLINE", "UNKNOWN"} and not telemetry_error:
                telemetry_error = str(srv.get("last_updated") or "Telemetry unavailable or unreachable")

            raw_hostname = str(srv.get("hostname") or "").strip()
            reg_hostname = str(reg.get("hostname") or "").strip()
            resolved_hostname = raw_hostname
            if not resolved_hostname or resolved_hostname == "Chưa xác minh":
                if reg_hostname and reg_hostname != "Chưa xác minh":
                    resolved_hostname = reg_hostname
                elif hid == "track":
                    resolved_hostname = "aiotlab-rack"
                elif hid == "workstation":
                    resolved_hostname = "aiot-ws1"
                else:
                    resolved_hostname = hid

            normalized_hosts.append(
                {
                    "host_id": hid,
                    "hostname": resolved_hostname,
                    "display_name": str(srv.get("name") or reg.get("display_name") or hid),
                    "status": effective_status,
                    "desired_state": str(reg.get("desired_state") or srv.get("desired_state") or "ACTIVE"),
                    "collected_at": collected_at,
                    "last_heartbeat_at": last_hb,
                    "freshness_status": freshness,
                    "source": str(srv.get("telemetry_source") or reg.get("telemetry_source") or srv.get("source") or "unknown"),
                    "error": telemetry_error,
                    "cpu_usage_percent": srv.get("cpu_usage_percent"),
                    "load_avg_1m": srv.get("load_avg_1m"),
                    "ram_used_gb": srv.get("ram_used_gb"),
                    "ram_total_gb": srv.get("ram_total_gb"),
                    "ram_usage_percent": srv.get("ram_usage_percent"),
                    "swap_used_gb": srv.get("swap_used_gb"),
                    "disk_used_tb": srv.get("disk_used_tb"),
                    "disk_total_tb": srv.get("disk_total_tb"),
                    "disk_usage_percent": srv.get("disk_usage_percent"),
                    "disk_io_errors": int(srv.get("disk_io_errors") or 0),
                    "filesystem_readonly": bool(srv.get("filesystem_readonly") or False),
                    "partitions": list(srv.get("partitions") or []),
                    "net_rx_mbps": srv.get("network_in_mbps"),
                    "net_tx_mbps": srv.get("network_out_mbps"),
                    "net_errors": int(srv.get("net_errors") or 0),
                    "uptime": srv.get("uptime"),
                }
            )

        # 2. GPUs keyed by host_id + gpu_uuid
        raw_gpus = list(gpus_override) if gpus_override is not None else list(getattr(svc, "gpus", []) or [])
        normalized_gpus: List[Dict[str, Any]] = []
        seen_gpu_keys = set()
        allocations_by_gpu: Dict[str, List[Dict[str, Any]]] = {}
        try:
            for dev in svc.get_gpu_allocations_by_device() or []:
                g_key = str(dev.get("gpu_uuid") or dev.get("gpu_id") or "")
                if g_key:
                    allocations_by_gpu[g_key] = list(dev.get("accounts") or [])
        except Exception:
            pass

        host_status_map = {h["host_id"]: h for h in normalized_hosts}
        for g in raw_gpus:
            hid = str(g.get("server_id") or g.get("host_id") or getattr(svc, "local_host_id", "workstation"))
            uuid_str = str(g.get("uuid") or g.get("gpu_uuid") or "").strip()
            gpu_idx = g.get("index", 0)
            unique_key = f"{hid}:{uuid_str or g.get('id') or f'gpu-{gpu_idx}'}"
            if unique_key in seen_gpu_keys:
                continue
            seen_gpu_keys.add(unique_key)

            host_info = host_status_map.get(hid, {})
            host_online = host_info.get("status") in {"ONLINE", "DEGRADED"}
            v_used = float(g.get("vram_used_mb") or 0)
            v_tot = float(g.get("vram_total_mb") or 0)
            util = float(g.get("gpu_util_percent") or g.get("utilization_gpu") or 0)
            temp = float(g.get("temp_celsius") or 0)
            power = float(g.get("power_watts") or 0)

            owners = allocations_by_gpu.get(uuid_str, []) or allocations_by_gpu.get(str(g.get("id") or ""), [])
            normalized_gpus.append(
                {
                    "gpu_key": unique_key,
                    "host_id": hid,
                    "gpu_uuid": uuid_str or str(g.get("id") or ""),
                    "gpu_id": str(g.get("id") or unique_key),
                    "index": gpu_idx,
                    "model": str(g.get("name") or g.get("model") or "NVIDIA GPU"),
                    "host_online": host_online,
                    "utilization_gpu": util if host_online else None,
                    "vram_used_mb": v_used if host_online else None,
                    "vram_total_mb": v_tot if v_tot > 0 else None,
                    "vram_percent": round((v_used / v_tot) * 100.0, 1) if (host_online and v_tot > 0) else None,
                    "temp_celsius": temp if host_online else None,
                    "power_watts": power if host_online else None,
                    "xid_errors": int(g.get("xid_errors") or 0),
                    "gpu_fault": bool(g.get("gpu_fault") or False),
                    "ecc_uncorrectable": int(g.get("ecc_uncorrectable") or 0),
                    "allocations": [
                        {
                            "username": a.get("username"),
                            "mode": a.get("allocation_mode"),
                            "status": a.get("status"),
                            "vram_quota_gb": a.get("vram_quota_gb"),
                            "vram_used_gb": a.get("vram_used_gb"),
                        }
                        for a in owners
                    ],
                }
            )

        # 3. Workspaces / Docker containers
        if workspaces_override is not None:
            raw_ws = list(workspaces_override)
        else:
            raw_ws = []
            try:
                raw_ws = list(svc.workspace_controller.list_workspaces() or [])
            except Exception:
                pass
        normalized_workspaces: List[Dict[str, Any]] = []
        for ws in raw_ws:
            limits = ws.get("limits") or {}
            normalized_workspaces.append(
                {
                    "id": ws.get("id"),
                    "name": ws.get("name"),
                    "owner": ws.get("owner"),
                    "host_id": ws.get("host_id"),
                    "desired_state": ws.get("desired_state"),
                    "lifecycle_state": ws.get("lifecycle_state"),
                    "observed_state": ws.get("observed_state"),
                    "container_state": ws.get("container_state") or ws.get("observed_state"),
                    "exit_code": ws.get("exit_code"),
                    "restart_count": int(ws.get("restart_count") or 0),
                    "oom_killed": bool(ws.get("oom_killed") or False),
                    "cpu_cores_limit": ws.get("cpu_cores_limit") or limits.get("cpu_cores_limit"),
                    "ram_mb_limit": ws.get("ram_mb_limit") or limits.get("ram_mb_limit"),
                    "storage_quota_gb": ws.get("storage_quota_gb") or limits.get("storage_quota_gb"),
                    "storage_used_gb": ws.get("storage_used_gb"),
                    "isolation_verified": bool(ws.get("non_privileged", True)) and not bool(ws.get("docker_socket_mounted", False)),
                    "docker_socket_mounted": bool(ws.get("docker_socket_mounted", False)),
                }
            )

        # 4. Jobs
        if jobs_override is not None:
            raw_jobs = list(jobs_override)
        else:
            raw_jobs = []
            try:
                raw_jobs = list(getattr(svc, "jobs", []) or [])
            except Exception:
                pass
        normalized_jobs: List[Dict[str, Any]] = []
        for j in raw_jobs:
            normalized_jobs.append(
                {
                    "id": j.get("id") or j.get("job_id") or str(j.get("pid") or ""),
                    "owner": j.get("owner") or j.get("user") or j.get("username") or "unknown",
                    "name": j.get("name") or j.get("script") or "compute-job",
                    "status": str(j.get("status") or "UNKNOWN").upper(),
                    "host_id": j.get("server_id") or j.get("host_id") or "workstation",
                    "gpu_key": j.get("gpu_uuid") or j.get("gpu_id"),
                    "vram_used_mb": j.get("vram_used_mb") or j.get("vram_mb") or 0,
                    "started_at": j.get("started_at"),
                    "ended_at": j.get("ended_at"),
                    "duration_seconds": j.get("duration_seconds"),
                    "failure_reason": j.get("failure_reason") or j.get("error"),
                    "stalled": bool(j.get("stalled") or False),
                    "cuda_oom": bool(j.get("cuda_oom") or False),
                }
            )

        # 5. Services (Daemons + Beszel/Arcane + LabOps core)
        if services_override is not None:
            services_health = dict(services_override)
        else:
            services_health = {}
            try:
                ext = svc.host_adapter.get_external_daemons_telemetry()
                if isinstance(ext, dict):
                    services_health.update(ext)
            except Exception:
                pass
            try:
                beszel = svc.beszel_adapter.get_monitoring_overview(live_hosts=raw_servers)
                services_health["beszel"] = {"status": beszel.get("status", "NOT_CONFIGURED"), "source": "beszel_adapter"}
            except Exception:
                pass
            try:
                arcane = svc.arcane_adapter.get_integration_summary()
                services_health["arcane"] = {"status": arcane.get("status", "NOT_CONFIGURED"), "source": "arcane_adapter"}
            except Exception:
                pass

        # Compute overall cluster health truthfully
        primary_id = str(getattr(svc, "local_host_id", "workstation"))
        primary_host = host_status_map.get(primary_id)
        if not normalized_hosts:
            overall_health = "UNKNOWN"
        elif primary_host and primary_host["status"] in {"OFFLINE", "UNKNOWN"}:
            overall_health = "CRITICAL"
        elif any(h["status"] in {"OFFLINE", "STALE", "DEGRADED", "UNKNOWN"} for h in normalized_hosts):
            overall_health = "DEGRADED"
        else:
            overall_health = "HEALTHY"

        return {
            "collected_at": now_iso,
            "overall_health": overall_health,
            "hosts": normalized_hosts,
            "gpus": normalized_gpus,
            "workspaces": normalized_workspaces,
            "jobs": normalized_jobs,
            "services": services_health,
        }

    def record_sample(self, snapshot: Optional[Dict[str, Any]] = None, *, min_interval_seconds: float = 0.0) -> int:
        import time

        now_mono = time.monotonic()
        if min_interval_seconds > 0 and (now_mono - self._last_sample_mono) < min_interval_seconds:
            return 0
        self._last_sample_mono = now_mono

        snap = snapshot or self.collect_snapshot()
        ts = _parse_iso(snap.get("collected_at")) or _utcnow()
        hosts = snap.get("hosts") or []
        gpus = snap.get("gpus") or []
        workspaces = snap.get("workspaces") or []
        jobs = snap.get("jobs") or []
        services = snap.get("services") or {}

        inserted = 0
        with self.db.session_scope() as session:
            for h in hosts:
                hid = str(h.get("host_id") or "unknown")
                host_gpus = [g for g in gpus if g.get("host_id") == hid]
                host_ws = [w for w in workspaces if w.get("host_id") == hid]
                host_jobs = [j for j in jobs if j.get("host_id") == hid]
                session.add(
                    TelemetryHistorySampleRecord(
                        id=f"th-{uuid.uuid4().hex[:12]}",
                        host_id=hid,
                        collected_at=ts,
                        freshness_status=str(h.get("freshness_status") or "UNKNOWN"),
                        source=str(h.get("source") or "unknown"),
                        cpu_percent=h.get("cpu_usage_percent"),
                        ram_used_gb=h.get("ram_used_gb"),
                        ram_total_gb=h.get("ram_total_gb"),
                        disk_used_tb=h.get("disk_used_tb"),
                        disk_total_tb=h.get("disk_total_tb"),
                        gpus_json=host_gpus,
                        services_json=services,
                        workspaces_json=host_ws,
                        jobs_json=host_jobs,
                    )
                )
                inserted += 1
        return inserted

    def get_history_kpis(
        self,
        *,
        host_id: Optional[str] = None,
        window_hours: int = 24,
        end_utc: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        end_dt = end_utc or _utcnow()
        start_dt = end_dt - timedelta(hours=max(1, int(window_hours)))
        prev_start_dt = start_dt - timedelta(hours=max(1, int(window_hours)))

        with self.db.session_scope() as session:
            stmt = select(TelemetryHistorySampleRecord).where(
                TelemetryHistorySampleRecord.collected_at >= prev_start_dt,
                TelemetryHistorySampleRecord.collected_at <= end_dt,
            )
            if host_id:
                stmt = stmt.where(TelemetryHistorySampleRecord.host_id == host_id)
            stmt = stmt.order_by(TelemetryHistorySampleRecord.collected_at.asc())
            rows = session.execute(stmt).scalars().all()

        curr_rows = [r for r in rows if (_as_utc(r.collected_at) or end_dt) >= start_dt]
        prev_rows = [r for r in rows if (_as_utc(r.collected_at) or end_dt) < start_dt]

        if not curr_rows:
            return {
                "host_id": host_id or "all",
                "window_hours": window_hours,
                "sample_count": 0,
                "availability_percent": None,
                "cpu_avg_percent": None,
                "cpu_peak_percent": None,
                "ram_avg_gb": None,
                "ram_peak_gb": None,
                "gpu_util_avg_percent": None,
                "gpu_vram_peak_mb": None,
                "disk_growth_gb": None,
                "disk_forecast_days": None,
                "prev_cpu_avg_percent": None,
                "data_limit_note": "Chưa đủ mẫu lịch sử trong cửa sổ thời gian yêu cầu.",
            }

        online_samples = sum(1 for r in curr_rows if str(r.freshness_status).upper() in {"ONLINE", "DEGRADED"})
        avail_pct = round((online_samples / len(curr_rows)) * 100.0, 2)

        cpus = [float(r.cpu_percent) for r in curr_rows if r.cpu_percent is not None]
        rams = [float(r.ram_used_gb) for r in curr_rows if r.ram_used_gb is not None]
        disks = [(float(r.disk_used_tb), float(r.disk_total_tb), _as_utc(r.collected_at)) for r in curr_rows if r.disk_used_tb is not None and r.disk_total_tb is not None]

        gpu_utils: List[float] = []
        gpu_vrams: List[float] = []
        for r in curr_rows:
            for g in (r.gpus_json or []):
                if g.get("utilization_gpu") is not None:
                    gpu_utils.append(float(g["utilization_gpu"]))
                if g.get("vram_used_mb") is not None:
                    gpu_vrams.append(float(g["vram_used_mb"]))

        disk_growth_gb: Optional[float] = None
        disk_forecast_days: Optional[float] = None
        if len(disks) >= 2:
            first_used, _, first_ts = disks[0]
            last_used, last_tot, last_ts = disks[-1]
            disk_growth_gb = round((last_used - first_used) * 1024.0, 2)
            if first_ts and last_ts and last_used > first_used and last_tot > last_used:
                elapsed_days = max(1e-4, (last_ts - first_ts).total_seconds() / 86400.0)
                rate_tb_per_day = (last_used - first_used) / elapsed_days
                if rate_tb_per_day > 0:
                    disk_forecast_days = round((last_tot - last_used) / rate_tb_per_day, 1)

        prev_cpus = [float(r.cpu_percent) for r in prev_rows if r.cpu_percent is not None]

        return {
            "host_id": host_id or "all",
            "window_hours": window_hours,
            "sample_count": len(curr_rows),
            "availability_percent": avail_pct,
            "cpu_avg_percent": round(sum(cpus) / len(cpus), 1) if cpus else None,
            "cpu_peak_percent": round(max(cpus), 1) if cpus else None,
            "ram_avg_gb": round(sum(rams) / len(rams), 1) if rams else None,
            "ram_peak_gb": round(max(rams), 1) if rams else None,
            "gpu_util_avg_percent": round(sum(gpu_utils) / len(gpu_utils), 1) if gpu_utils else None,
            "gpu_vram_peak_mb": round(max(gpu_vrams), 1) if gpu_vrams else None,
            "disk_growth_gb": disk_growth_gb,
            "disk_forecast_days": disk_forecast_days,
            "prev_cpu_avg_percent": round(sum(prev_cpus) / len(prev_cpus), 1) if prev_cpus else None,
            "data_limit_note": None if len(curr_rows) >= 3 else "Số mẫu lịch sử còn ít (<3); xu hướng/dự báo ổ đĩa mang tính sơ bộ.",
        }
