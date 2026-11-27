from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
import re
import signal
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple
import uuid

from app.adapters.process_monitor_adapter import (
    PROTECTED_CMD_KEYWORDS,
    PROTECTED_SYSTEM_NAMES,
    classify_workload_kind,
)
from app.core.config import settings
from app.db.models import AuditEventRecord, IdempotencyRecord, TelemetryHistorySampleRecord
from app.schemas.workload_v31 import (
    AnalyticsPoint,
    AnalyticsSeries,
    CapabilityEntry,
    DeviceVramMeta,
    GpuCoveragePair,
    HistoryWorkloadItem,
    ProcessActionResultEnvelope,
    ScopeBase,
    SnapshotGpuItem,
    SnapshotKpis,
    SnapshotNodeItem,
    SnapshotQuality,
    SnapshotScope,
    SnapshotWarning,
    SnapshotWorkloadItem,
    V31ProcessActionRequest,
    WorkloadAnalyticsEnvelope,
    WorkloadComposition,
    WorkloadCpuMetric,
    WorkloadDetailResponse,
    WorkloadEventItem,
    WorkloadEventsEnvelope,
    WorkloadGpuAllocation,
    WorkloadHistoryEnvelope,
    WorkloadMemoryMetric,
    WorkloadSnapshotEnvelope,
    WorkloadVramMetric,
)

MIB_BYTES = 1024 * 1024
DEFAULT_STALE_INTERVAL_SECONDS = 30.0
MAX_HISTORY_RANGE_DAYS = 90
DEFAULT_SNAPSHOT_MAX_ROWS = 2000

CLUSTER_NODE_INVENTORY: Dict[str, Dict[str, Any]] = {
    "workstation": {
        "name": "Server 02 — Workstation",
        "expected_gpus": ["workstation-gpu-01"],
    },
    "track": {
        "name": "Server 01 — Rack Server",
        "expected_gpus": ["track-gpu-01", "track-gpu-02", "track-gpu-03"],
    },
}

SENSITIVE_CMD_RE = re.compile(
    r"(--(?:password|token|secret|key|api_key|auth|credential)[=\s]+)([^\s]+)",
    re.IGNORECASE,
)


def utc_rfc3339(dt: Optional[datetime] = None) -> str:
    cur = dt or datetime.now(timezone.utc)
    if cur.tzinfo is None:
        cur = cur.replace(tzinfo=timezone.utc)
    return cur.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_iso(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("INVALID_TIMESTAMP: empty timestamp")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"INVALID_TIMESTAMP: '{raw}' is not valid ISO-8601") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_ELAPSED_PART_RE = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)


def _parse_elapsed_and_started(p: Dict[str, Any]) -> tuple[Optional[int], Optional[str]]:
    """Resolve numeric elapsed_seconds and UTC started_at, deriving from start_time/elapsed_time if omitted."""
    elapsed_sec = p.get("elapsed_seconds")
    elapsed_int: Optional[int] = int(max(0, int(elapsed_sec))) if elapsed_sec is not None else None

    started_dt: Optional[datetime] = None
    started_iso: Optional[str] = None
    raw_utc = p.get("started_at_utc") or p.get("started_at")
    if raw_utc:
        try:
            started_dt = parse_utc_iso(str(raw_utc))
            started_iso = utc_rfc3339(started_dt)
        except ValueError:
            started_dt = None
            started_iso = None

    if started_dt is None and p.get("start_time"):
        raw_st = str(p["start_time"]).strip()
        try:
            local_tz = datetime.now().astimezone().tzinfo or timezone.utc
            naive = datetime.strptime(raw_st[:19], "%Y-%m-%d %H:%M:%S")
            started_dt = naive.replace(tzinfo=local_tz).astimezone(timezone.utc)
            started_iso = utc_rfc3339(started_dt)
        except ValueError:
            started_dt = None

    if elapsed_int is None and p.get("elapsed_time"):
        parts = _ELAPSED_PART_RE.findall(str(p["elapsed_time"]))
        if parts:
            mult = {"d": 86400, "h": 3600, "m": 60, "s": 1}
            elapsed_int = sum(int(val) * mult[unit.lower()] for val, unit in parts)

    now_dt = datetime.now(timezone.utc)
    if elapsed_int is None and started_dt is not None:
        elapsed_int = max(0, int((now_dt - started_dt).total_seconds()))
    if started_iso is None and elapsed_int is not None:
        started_iso = utc_rfc3339(now_dt - timedelta(seconds=elapsed_int))
    return elapsed_int, started_iso


def sanitize_message(text: Optional[str], max_len: int = 1000) -> str:
    if not text:
        return ""
    cleaned = SENSITIVE_CMD_RE.sub(r"\1********", str(text))
    cleaned = cleaned.replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:max_len]


def encode_cursor(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: Optional[str]) -> Optional[Dict[str, Any]]:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        obj = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception as exc:
        raise ValueError("INVALID_CURSOR: malformed pagination cursor") from exc


def export_workloads_csv(
    workloads: List[SnapshotWorkloadItem],
    *,
    scope: SnapshotScope,
    max_rows: int = DEFAULT_SNAPSHOT_MAX_ROWS,
) -> str:
    """Export safe scoped CSV with spreadsheet formula injection protection and zero raw cmdline."""
    import csv
    import io

    def _safe_cell(val: Any) -> str:
        s = "" if val is None else str(val)
        s = s.replace("\r", " ").replace("\n", " ")
        if s and s[0] in ("=", "+", "-", "@", "\t"):
            return "'" + s
        return s

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "workload_id",
            "node_id",
            "username",
            "member_id",
            "group_id",
            "workspace",
            "host_pid",
            "display_name",
            "workload_kind",
            "gpu_associated",
            "cpu_percent",
            "memory_bytes",
            "memory_method",
            "vram_bytes",
            "vram_attribution",
            "elapsed_seconds",
            "state",
            "quality",
            "observed_at_utc",
            "scope_node_id",
            "scope_member_id",
        ]
    )
    for row in workloads[:max_rows]:
        writer.writerow(
            [
                _safe_cell(row.id),
                _safe_cell(row.node_id),
                _safe_cell(row.username),
                _safe_cell(row.member_id),
                _safe_cell(row.group_id),
                _safe_cell(row.workspace),
                _safe_cell(row.host_pid),
                _safe_cell(row.display_name),
                _safe_cell(row.workload_kind),
                _safe_cell(row.gpu_associated),
                _safe_cell(row.cpu.value_percent),
                _safe_cell(row.memory.value_bytes),
                _safe_cell(row.memory.method),
                _safe_cell(row.vram.value_bytes),
                _safe_cell(row.vram.attribution),
                _safe_cell(row.elapsed_seconds),
                _safe_cell(row.state),
                _safe_cell(row.quality),
                _safe_cell(row.observed_at),
                _safe_cell(scope.node_id),
                _safe_cell(scope.member_id),
            ]
        )
    return out.getvalue()


def validate_contract_invariants(payload: Dict[str, Any], kind: str) -> None:
    """Executable contract and cross-field invariant check matching V3.1 JSON schemas & validate_contracts.py."""
    if kind == "workload-snapshot":
        WorkloadSnapshotEnvelope.model_validate(payload)
        rows = payload["workloads"]
        q = payload["quality"]
        k = payload["kpis"]
        nodes = payload["nodes"]
        gpus = payload["gpus"]

        assert len({r["id"] for r in rows}) == len(rows), "Duplicate workload IDs"
        assert len({n["id"] for n in nodes}) == len(nodes), "Duplicate node IDs"
        assert len({g["gpu_uuid"] for g in gpus}) == len(gpus), "Duplicate GPU UUIDs"
        node_ids = {n["id"] for n in nodes}
        assert all(r["node_id"] in node_ids for r in rows), "Workload references unknown node"
        assert q["fresh_nodes"] == sum(n["quality"] == "FRESH" for n in nodes)
        assert q["fresh_workload_nodes"] == sum(n["workload_quality"] == "FRESH" for n in nodes)

        fresh = [r for r in rows if r["quality"] == "FRESH"]
        stale = [r for r in rows if r["quality"] == "STALE"]
        assert all(r["quality"] != "FRESH" or r["observed_at"] for r in rows)
        assert k["last_known_workloads"] == (len(stale) if stale else None)

        if not q["fresh_workload_nodes"]:
            for m in (
                "observed_workloads",
                "gpu_associated_workloads",
                "gpu_association_unknown",
                "observed_members",
                "unmapped_member_workloads",
            ):
                assert k[m] is None, f"Expected {m} to be null when no fresh workload nodes"
            assert payload["composition"] is None and not fresh
        else:
            assert k["observed_workloads"] == len(fresh)
            assert k["gpu_associated_workloads"] == sum(r["gpu_associated"] is True for r in fresh)
            assert k["gpu_association_unknown"] == sum(r["gpu_associated"] is None for r in fresh)
            assert k["observed_members"] == len({r["member_id"] for r in fresh if r["member_id"]})
            assert k["unmapped_member_workloads"] == sum(r["member_id"] is None for r in fresh)
            comp = dict.fromkeys(
                ("gpu_non_monitoring", "non_gpu_compute", "monitoring", "other_unknown"), 0
            )
            for r in fresh:
                wk = r["workload_kind"]
                ga = r["gpu_associated"]
                key = (
                    "monitoring"
                    if wk == "MONITORING"
                    else "gpu_non_monitoring"
                    if ga is True
                    else "non_gpu_compute"
                    if ga is False and wk in {"TRAINING", "INFERENCE", "NOTEBOOK", "GENERAL_COMPUTE"}
                    else "other_unknown"
                )
                comp[key] += 1
            assert payload["composition"] == comp

        assert q["fresh_gpus"] == sum(g["quality"] == "FRESH" for g in gpus) == k["gpu_coverage"]["fresh"]
        assert q["expected_gpus"] == k["gpu_coverage"]["expected"]
        for a, b in (("fresh_gpus", "expected_gpus"), ("fresh_nodes", "expected_nodes")):
            assert q[b] is None or q[a] <= q[b]
        pairs = [
            g
            for g in gpus
            if g["quality"] == "FRESH"
            and g["vram_used_bytes"] is not None
            and g["vram_capacity_bytes"] is not None
        ]
        assert k["device_vram_used_bytes"] == (sum(g["vram_used_bytes"] for g in pairs) if pairs else None)
        assert k["device_vram_capacity_bytes"] == (sum(g["vram_capacity_bytes"] for g in pairs) if pairs else None)
        gpu_uuids = {g["gpu_uuid"] for g in gpus}
        for r in rows:
            allocs = r["gpu_allocations"]
            if allocs is not None:
                assert len({a["gpu_uuid"] for a in allocs}) == len(allocs)
                assert all(a["gpu_uuid"] in gpu_uuids for a in allocs)
                assert sum(a["value_bytes"] for a in allocs) == r["vram"]["value_bytes"]
    elif kind == "workload-analytics":
        WorkloadAnalyticsEnvelope.model_validate(payload)
        from_dt = parse_utc_iso(payload["from"])
        to_dt = parse_utc_iso(payload["to"])
        for series in payload["series"]:
            times = [parse_utc_iso(pt["ts"]) for pt in series["points"]]
            assert times == sorted(set(times))
            assert all(from_dt <= t < to_dt for t in times)
            assert all(pt["quality"] != "NO_DATA" or pt["value"] is None for pt in series["points"])
    elif kind == "workload-history":
        WorkloadHistoryEnvelope.model_validate(payload)
        for item in payload["items"]:
            assert item["state"] == "EXIT_CONFIRMED" or item["exit_code"] is None
            assert item["actionable"] is False
    elif kind == "workload-events":
        WorkloadEventsEnvelope.model_validate(payload)
    elif kind == "process-action-result":
        ProcessActionResultEnvelope.model_validate(payload)
    else:
        raise ValueError(f"Unknown contract kind: {kind}")


@dataclass
class LiveProcIdentity:
    pid: int
    ppid: int
    uid: int
    username: str
    name: str
    cmdline: str
    start_ticks: int
    boot_id: Optional[str]
    container_id: str
    state: str = "R"


class ProcessControlBackend(Protocol):
    def inspect_pid(self, pid: int) -> Optional[LiveProcIdentity]:
        ...

    def list_children(self, root_pid: int) -> List[LiveProcIdentity]:
        ...

    def send_signal_pidfd(
        self,
        pid: int,
        expected_ticks: int,
        expected_uid: int,
        sig: int,
    ) -> None:
        ...


class OSPidfdProcessBackend:
    """Fail-closed /proc + pidfd backend for local host verification (no numeric PID fallback on ENOSYS)."""

    @staticmethod
    def _read_boot_id() -> Optional[str]:
        try:
            with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as bf:
                return bf.read().strip() or None
        except Exception:
            return None

    @staticmethod
    def _read_container(pid: int) -> str:
        try:
            with open(f"/proc/{pid}/cgroup", "r", encoding="utf-8", errors="ignore") as cf:
                for line in cf:
                    if "lxc.payload." in line:
                        m = re.search(r"lxc\.payload\.([^/]+)", line)
                        if m:
                            return m.group(1)
                    if "docker-" in line and ".scope" in line:
                        m = re.search(r"docker-([a-f0-9]{12})[a-f0-9]*\.scope", line)
                        if m:
                            return m.group(1)
                    if "/docker/" in line:
                        parts = line.strip().split("/docker/")
                        if len(parts) > 1 and parts[1]:
                            return parts[1][:12]
        except Exception:
            pass
        return "host"

    def inspect_pid(self, pid: int) -> Optional[LiveProcIdentity]:
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as sf:
                raw = sf.read()
        except OSError:
            return None
        lp, rp = raw.find("("), raw.rfind(")")
        if lp == -1 or rp == -1 or rp <= lp:
            return None
        name = raw[lp + 1 : rp]
        fields = raw[rp + 2 :].split()
        if len(fields) < 20:
            return None
        state = fields[0]
        ppid = int(fields[1])
        start_ticks = int(fields[19])
        uid = -1
        try:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8", errors="ignore") as stf:
                for line in stf:
                    if line.startswith("Uid:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            uid = int(parts[1])
                        break
        except OSError:
            return None
        cmdline = name
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as cf:
                cmd_raw = cf.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
                if cmd_raw:
                    cmdline = sanitize_message(cmd_raw, 2000)
        except OSError:
            pass
        uname = str(uid)
        try:
            import pwd

            uname = pwd.getpwuid(uid).pw_name
        except Exception:
            pass
        return LiveProcIdentity(
            pid=pid,
            ppid=ppid,
            uid=uid,
            username=uname,
            name=name,
            cmdline=cmdline,
            start_ticks=start_ticks,
            boot_id=self._read_boot_id(),
            container_id=self._read_container(pid),
            state=state,
        )

    def list_children(self, root_pid: int) -> List[LiveProcIdentity]:
        by_ppid: Dict[int, List[LiveProcIdentity]] = {}
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            cpid = int(entry)
            if cpid <= 1:
                continue
            ident = self.inspect_pid(cpid)
            if ident is not None:
                by_ppid.setdefault(ident.ppid, []).append(ident)
        out: List[LiveProcIdentity] = []
        queue = [root_pid]
        seen = {root_pid}
        while queue:
            cur = queue.pop(0)
            for ch in by_ppid.get(cur, []):
                if ch.pid not in seen:
                    seen.add(ch.pid)
                    out.append(ch)
                    queue.append(ch.pid)
        return out

    def send_signal_pidfd(
        self,
        pid: int,
        expected_ticks: int,
        expected_uid: int,
        sig: int,
    ) -> None:
        pidfd_open = getattr(os, "pidfd_open", None)
        pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
        if not callable(pidfd_open) or not callable(pidfd_send_signal):
            raise RuntimeError("PIDFD_UNSUPPORTED_FAIL_CLOSED: kernel/runtime lacks pidfd_open")
        try:
            fd = pidfd_open(pid, 0)
        except OSError as exc:
            if exc.errno == errno.ENOSYS:
                raise RuntimeError("PIDFD_UNSUPPORTED_FAIL_CLOSED: pidfd_open ENOSYS") from exc
            if exc.errno == errno.ESRCH:
                raise LookupError(f"PROCESS_NOT_FOUND: PID {pid} not found") from exc
            if exc.errno in (errno.EPERM, errno.EACCES):
                raise PermissionError(f"PERMISSION_DENIED: cannot open pidfd for PID {pid}") from exc
            raise RuntimeError(f"PIDFD_OPEN_FAILED: {exc}") from exc

        try:
            live = self.inspect_pid(pid)
            if live is None:
                raise LookupError(f"PROCESS_NOT_FOUND: PID {pid} exited before signal")
            if live.start_ticks != expected_ticks:
                raise ValueError("PID_RECYCLED: start_ticks changed while holding pidfd")
            if live.uid != expected_uid:
                raise PermissionError("UID_MISMATCH: process UID changed before signal")
            pidfd_send_signal(fd, sig)
        except OSError as exc:
            if exc.errno == errno.ENOSYS:
                raise RuntimeError("PIDFD_UNSUPPORTED_FAIL_CLOSED: pidfd_send_signal ENOSYS") from exc
            if exc.errno == errno.ESRCH:
                raise LookupError(f"PROCESS_NOT_FOUND: PID {pid} exited") from exc
            if exc.errno in (errno.EPERM, errno.EACCES):
                raise PermissionError(f"PERMISSION_DENIED: {exc}") from exc
            raise RuntimeError(f"SIGNAL_FAILED: {exc}") from exc
        finally:
            try:
                os.close(fd)
            except OSError:
                pass


def is_identity_protected(ident: LiveProcIdentity) -> bool:
    if ident.pid <= 1 or ident.pid == 2 or ident.ppid == 2 or ident.pid == os.getpid():
        return True
    name_low = (ident.name or "").lower()
    cmd_low = (ident.cmdline or "").lower()
    if name_low in PROTECTED_SYSTEM_NAMES:
        return True
    if any(kw in cmd_low for kw in PROTECTED_CMD_KEYWORDS):
        return True
    if "sshd" in name_low and ident.container_id in ("", "host"):
        return True
    if ident.uid == 0 or (0 <= ident.uid < 1000 and ident.container_id in ("", "host")):
        return True
    return False


class WorkloadIntelligenceService:
    """
    Isolated Workload Intelligence V3.1 read/analytics/security service.
    Reads existing LabOps collectors and `telemetry_history_samples` read-only without
    touching Zalo AI workers, queues, prompts, or notification state.
    """

    def __init__(self, core_service: Any, *, action_backend: Optional[ProcessControlBackend] = None) -> None:
        self._core = core_service
        self._lock = threading.RLock()
        self._last_good_nodes: Dict[str, Dict[str, Any]] = {}
        self._last_good_workloads: Dict[str, List[Dict[str, Any]]] = {}
        self._last_good_gpus: Dict[str, List[Dict[str, Any]]] = {}
        self._last_success_at: Dict[str, str] = {}
        self._action_backend: ProcessControlBackend = action_backend or OSPidfdProcessBackend()
        self._action_target_locks: Dict[str, threading.Lock] = {}
        self._in_flight_refs: set[str] = set()
        self._operation_store: Dict[Tuple[str, str], Tuple[str, ProcessActionResultEnvelope]] = {}
        self._telemetry_collector: Optional[Any] = None
        self.collector_call_count = 0

    def _maybe_capture_live_analytics_sample(self, to_dt: datetime) -> None:
        """Auto-record a fresh telemetry sample when querying up to 'now' and DB history is stale."""
        try:
            now_dt = datetime.now(timezone.utc)
            if (now_dt - to_dt).total_seconds() >= 300:
                return
            data_dir = getattr(self._core, "data_dir", None)
            if not data_dir or getattr(self._core, "host_monitor", None) is None:
                return
            if bool(getattr(self._core, "demo_mode", False)):
                return
            with self._core.db.session_scope() as session:
                latest = (
                    session.query(TelemetryHistorySampleRecord.collected_at)
                    .order_by(TelemetryHistorySampleRecord.collected_at.desc())
                    .limit(1)
                    .scalar()
                )
            if latest is not None:
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=timezone.utc)
                if (now_dt - latest).total_seconds() < 45.0:
                    return
            if self._telemetry_collector is None:
                from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector

                self._telemetry_collector = UnifiedTelemetryCollector(
                    data_dir,
                    db_manager=self._core.db,
                    core_service=self._core,
                )
            self._telemetry_collector.record_sample(min_interval_seconds=45.0)
        except Exception:
            pass

    def is_actions_enabled(self) -> bool:
        return os.getenv("LABOPS_WORKLOAD_V31_ACTIONS_ENABLED", "false").strip().lower() == "true"

    def _db_ready(self) -> bool:
        db = getattr(self._core, "db", None)
        if db is None:
            return False
        enabled_attr = getattr(db, "enabled", None)
        if enabled_attr is not None:
            return bool(enabled_attr)
        return hasattr(db, "session_scope") and hasattr(db, "engine")

    @staticmethod
    def _normalize_node_quality(server_dict: Optional[Dict[str, Any]], node_id: str, now_dt: datetime) -> Tuple[str, Optional[str]]:
        if not server_dict:
            return "OFFLINE", None
        freshness = str(server_dict.get("freshness_status") or server_dict.get("status") or "").upper()
        source = str(server_dict.get("telemetry_source") or "").lower()
        collected_raw = server_dict.get("collected_at")
        age_sec = server_dict.get("age_seconds")

        if source == "remote_unverified" or freshness in ("OFFLINE", "UNKNOWN"):
            return "OFFLINE", None
        if freshness == "NOT_CONFIGURED":
            return "NOT_CONFIGURED", None

        observed_iso: Optional[str] = None
        if collected_raw:
            try:
                obs_dt = parse_utc_iso(str(collected_raw))
                observed_iso = utc_rfc3339(obs_dt)
                computed_age = (now_dt - obs_dt).total_seconds()
                if age_sec is None:
                    age_sec = computed_age
            except ValueError:
                observed_iso = None

        if age_sec is not None and float(age_sec) > DEFAULT_STALE_INTERVAL_SECONDS:
            return "STALE", observed_iso
        if freshness in ("STALE", "DEGRADED"):
            return "STALE", observed_iso
        if freshness in ("ONLINE", "FRESH"):
            return "FRESH", observed_iso or utc_rfc3339(now_dt)
        return "UNKNOWN", observed_iso

    def _member_indexes(self) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        by_username: Dict[str, Dict[str, Any]] = {}
        by_id: Dict[str, Dict[str, Any]] = {}
        for m in list(getattr(self._core, "members", []) or []):
            status = str(m.get("status") or "").upper()
            if status in ("DELETED", "ARCHIVED"):
                continue
            mid = str(m.get("id") or "").strip()
            uname = str(m.get("username") or "").strip()
            if mid:
                by_id[mid] = m
            if uname:
                by_username[uname] = m
        return by_username, by_id

    @staticmethod
    def _normalize_group_id(member: Optional[Dict[str, Any]]) -> Optional[str]:
        if not member:
            return None
        rg = member.get("research_group")
        if isinstance(rg, str) and rg.strip():
            return rg.strip()
        payload = member.get("payload_json")
        if isinstance(payload, dict):
            prg = payload.get("research_group")
            if isinstance(prg, str) and prg.strip():
                return prg.strip()
        return None

    def _get_host_raw_processes(self, node_id: str) -> Dict[str, Any]:
        self.collector_call_count += 1
        local_id = getattr(settings, "LOCAL_HOST_ID", "workstation")
        if node_id == local_id:
            return self._core.process_monitor.get_real_processes(filter_type="workloads")
        # Remote nodes must never fall back to local /proc
        return {
            "source_host": node_id,
            "collection_timestamp": utc_rfc3339(),
            "collector_status": "NOT_CONFIGURED",
            "actual_observations": [],
            "error_details": "Remote node collector is offline or not configured.",
        }

    def validate_scope_params(
        self,
        *,
        node_id: Optional[str] = None,
        gpu_uuid: Optional[str] = None,
        member_id: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> SnapshotScope:
        if node_id is not None and node_id not in CLUSTER_NODE_INVENTORY:
            raise ValueError(f"INVALID_SCOPE: unknown node_id '{node_id}'")
        if node_id and gpu_uuid:
            # Check known cluster catalog contradictions
            for nid, cfg in CLUSTER_NODE_INVENTORY.items():
                if nid != node_id and gpu_uuid in cfg["expected_gpus"]:
                    raise ValueError(
                        f"INVALID_SCOPE_COMBINATION: GPU '{gpu_uuid}' belongs to node '{nid}', not '{node_id}'"
                    )
        return SnapshotScope(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            member_id=member_id,
            group_id=group_id,
            mode="workloads",
        )

    def get_snapshot(
        self,
        *,
        node_id: Optional[str] = None,
        gpu_uuid: Optional[str] = None,
        member_id: Optional[str] = None,
        group_id: Optional[str] = None,
        raw_observations_override: Optional[Dict[str, Dict[str, Any]]] = None,
        servers_override: Optional[List[Dict[str, Any]]] = None,
        gpus_override: Optional[List[Dict[str, Any]]] = None,
        now_dt: Optional[datetime] = None,
    ) -> WorkloadSnapshotEnvelope:
        scope = self.validate_scope_params(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            member_id=member_id,
            group_id=group_id,
        )
        now = now_dt or datetime.now(timezone.utc)
        received_iso = utc_rfc3339(now)
        actions_flag = self.is_actions_enabled()
        members_by_user, members_by_id = self._member_indexes()

        target_nodes = [scope.node_id] if scope.node_id else list(CLUSTER_NODE_INVENTORY.keys())
        raw_servers = servers_override if servers_override is not None else list(self._core.servers or [])
        server_map = {str(s.get("id") or ""): s for s in raw_servers if s.get("id")}
        raw_gpus = gpus_override if gpus_override is not None else list(self._core.gpus or [])

        nodes_out: List[SnapshotNodeItem] = []
        gpus_out: List[SnapshotGpuItem] = []
        gpu_alias_to_uuid: Dict[str, str] = {}
        workloads_unfiltered: List[SnapshotWorkloadItem] = []
        warnings: List[SnapshotWarning] = []
        fresh_observed_times: List[str] = []
        expected_gpu_count = 0

        for nid in target_nodes:
            node_cfg = CLUSTER_NODE_INVENTORY[nid]
            srv = server_map.get(nid)
            node_q, node_obs_iso = self._normalize_node_quality(srv, nid, now)

            if raw_observations_override is not None and nid in raw_observations_override:
                proc_res = raw_observations_override[nid]
            else:
                try:
                    proc_res = self._get_host_raw_processes(nid)
                except Exception as exc:
                    proc_res = {
                        "collector_status": "ERROR",
                        "actual_observations": [],
                        "error_details": sanitize_message(str(exc), 200),
                    }

            col_status = str(proc_res.get("collector_status") or "UNKNOWN").upper()
            wk_obs_iso: Optional[str] = None
            raw_obs_list = list(proc_res.get("actual_observations") or [])

            if col_status == "OK":
                wk_q = "FRESH"
                wk_obs_iso = node_obs_iso or received_iso
                with self._lock:
                    self._last_success_at[nid] = wk_obs_iso
                    self._last_good_workloads[nid] = list(raw_obs_list)
            elif col_status == "NOT_CONFIGURED":
                wk_q = "OFFLINE" if node_q == "OFFLINE" else "NOT_CONFIGURED"
            elif col_status in ("STALE", "ERROR", "FAILED", "UNAVAILABLE"):
                with self._lock:
                    cached_rows = self._last_good_workloads.get(nid)
                    prev_ok = self._last_success_at.get(nid)
                if cached_rows is not None and col_status != "NOT_CONFIGURED":
                    wk_q = "STALE"
                    node_q = "STALE" if node_q == "FRESH" else node_q
                    raw_obs_list = cached_rows
                    wk_obs_iso = prev_ok
                    warnings.append(
                        SnapshotWarning(
                            code=f"STALE_SOURCE_{nid.upper()}",
                            message=f"Bộ thu tiến trình trên node '{nid}' tạm gián đoạn; đang hiển thị dữ liệu quan sát gần nhất ({prev_ok or 'unknown'}).",
                        )
                    )
                else:
                    wk_q = "OFFLINE" if node_q == "OFFLINE" else "ERROR"
                    raw_obs_list = []
                    warnings.append(
                        SnapshotWarning(
                            code=f"COLLECTOR_{col_status}_{nid.upper()}",
                            message=f"Không thể đọc tiến trình trên node '{nid}' ({col_status}).",
                        )
                    )
            else:
                wk_q = "OFFLINE" if node_q == "OFFLINE" else "UNKNOWN"

            with self._lock:
                last_ok = self._last_success_at.get(nid)
            if node_q == "FRESH" and node_obs_iso:
                last_ok = node_obs_iso
                with self._lock:
                    self._last_success_at[nid] = node_obs_iso
            if wk_q == "FRESH" and wk_obs_iso:
                fresh_observed_times.append(wk_obs_iso)

            nodes_out.append(
                SnapshotNodeItem(
                    id=nid,
                    quality=node_q,  # type: ignore[arg-type]
                    workload_quality=wk_q,  # type: ignore[arg-type]
                    observed_at=node_obs_iso if node_q == "FRESH" else None,
                    last_success_at=last_ok,
                )
            )

            # Build GPUs for this node
            node_raw_gpus = [g for g in raw_gpus if str(g.get("server_id") or "") == nid]
            node_expected = len(node_cfg["expected_gpus"])
            if scope.gpu_uuid:
                matching = [
                    g
                    for g in node_raw_gpus
                    if g.get("uuid") == scope.gpu_uuid or g.get("id") == scope.gpu_uuid
                ]
                if scope.gpu_uuid in node_cfg["expected_gpus"] or matching:
                    expected_gpu_count += 1
                node_raw_gpus = matching
            else:
                expected_gpu_count += max(node_expected, len(node_raw_gpus))

            seen_gpu_uuids: set[str] = set()
            for g in node_raw_gpus:
                guuid = str(g.get("uuid") or g.get("id") or "").strip()
                if not guuid or guuid in seen_gpu_uuids:
                    continue
                seen_gpu_uuids.add(guuid)
                gpu_alias_to_uuid[guuid] = guuid
                if g.get("id"):
                    gpu_alias_to_uuid[str(g["id"])] = guuid
                gf_raw = str(g.get("freshness_status") or g.get("freshness") or "").upper()
                g_obs = None
                if node_q == "FRESH" and gf_raw in ("ONLINE", "FRESH", ""):
                    g_quality = "FRESH"
                    g_obs = utc_rfc3339(parse_utc_iso(str(g.get("collected_at") or received_iso)))
                    fresh_observed_times.append(g_obs)
                elif node_q == "STALE" or gf_raw == "STALE":
                    g_quality = "STALE"
                elif node_q == "OFFLINE":
                    g_quality = "OFFLINE"
                else:
                    g_quality = "UNKNOWN"

                used_mb = g.get("vram_used_mb")
                cap_mb = g.get("vram_total_mb")
                util_pct = g.get("gpu_util_percent")
                gpus_out.append(
                    SnapshotGpuItem(
                        gpu_uuid=guuid,
                        node_id=nid,
                        quality=g_quality,  # type: ignore[arg-type]
                        observed_at=g_obs,
                        vram_used_bytes=int(round(float(used_mb) * MIB_BYTES))
                        if used_mb is not None and g_quality in ("FRESH", "STALE")
                        else None,
                        vram_capacity_bytes=int(round(float(cap_mb) * MIB_BYTES))
                        if cap_mb is not None and float(cap_mb) > 0
                        else None,
                        utilization_percent=min(100.0, max(0.0, float(util_pct)))
                        if util_pct is not None and g_quality in ("FRESH", "STALE")
                        else None,
                    )
                )

            # Build root workload rows for this node
            seen_wl_ids: set[str] = set()
            known_node_gpu_uuids = {g.gpu_uuid for g in gpus_out if g.node_id == nid}
            for p in raw_obs_list:
                if not p.get("is_root_workload", True):
                    continue
                pid = int(p.get("pid") or 0)
                if pid <= 0:
                    continue
                uname = str(p.get("username") or "").strip() or None
                container_id = str(p.get("container_id") or "").strip() or None
                if uname in ("root", "aiot-ws1", "systemd", "daemon") and not container_id:
                    continue

                is_prot = bool(p.get("is_protected") or p.get("classification") == "SYSTEM_PROCESS")
                if is_prot:
                    continue

                start_ticks = p.get("start_time_ticks")
                start_ticks_int = int(start_ticks) if start_ticks is not None else None
                boot_id = str(p.get("boot_id") or "").strip() or None
                digest_src = f"{nid}:{boot_id or 'noboot'}:{container_id or 'host'}:{pid}:{start_ticks_int or 0}"
                wl_id = "wl-" + hashlib.sha256(digest_src.encode("utf-8")).hexdigest()[:20]
                if wl_id in seen_wl_ids:
                    continue
                seen_wl_ids.add(wl_id)

                m_rec = members_by_user.get(uname or "")
                mapped_member_id = str(m_rec["id"]) if (m_rec and m_rec.get("id")) else None
                mapped_group_id = self._normalize_group_id(m_rec)

                # Taxonomy conservative + independent from GPU
                wk_kind = p.get("workload_kind")
                wk_conf = p.get("kind_confidence") or "HEURISTIC"
                wk_ev = list(p.get("kind_evidence") or [])
                total_vram_mb = p.get("total_vram_mb", p.get("vram_mb", 0))
                is_gpu_proc = p.get("tree_is_gpu")
                if is_gpu_proc is None:
                    is_gpu_proc = p.get("is_gpu_process")
                if not wk_kind:
                    wk_kind, wk_conf, wk_ev = classify_workload_kind(
                        str(p.get("name") or p.get("script_display") or ""),
                        str(p.get("cmdline") or ""),
                        is_gpu=bool(is_gpu_proc or (total_vram_mb or 0) > 0),
                        vram_mb=int(total_vram_mb or 0),
                        is_protected=False,
                    )
                if wk_kind == "SYSTEM":
                    continue

                gpu_associated: Optional[bool]
                if p.get("gpu_associated") is not None:
                    gpu_associated = bool(p["gpu_associated"])
                elif is_gpu_proc is True or (total_vram_mb or 0) > 0:
                    gpu_associated = True
                elif wk_q == "FRESH" and is_gpu_proc is False and int(total_vram_mb or 0) == 0:
                    gpu_associated = False
                else:
                    gpu_associated = None

                # GPU allocations per device (avoid double counting or inventing multi-GPU split)
                raw_allocs = p.get("gpu_allocations")
                alloc_items: Optional[List[WorkloadGpuAllocation]] = None
                vram_bytes: Optional[int] = (
                    int(round(float(total_vram_mb) * MIB_BYTES))
                    if total_vram_mb is not None
                    else None
                )
                vram_attr = "UNKNOWN"
                if isinstance(raw_allocs, list) and len(raw_allocs) > 0:
                    dedup_allocs: Dict[str, int] = {}
                    for a in raw_allocs:
                        auuid = str(a.get("gpu_uuid") or "").strip()
                        if not auuid or auuid not in known_node_gpu_uuids:
                            continue
                        abytes = int(
                            a.get("value_bytes")
                            if a.get("value_bytes") is not None
                            else round(float(a.get("vram_mb") or 0) * MIB_BYTES)
                        )
                        dedup_allocs[auuid] = dedup_allocs.get(auuid, 0) + max(0, abytes)
                    if dedup_allocs:
                        alloc_items = [
                            WorkloadGpuAllocation(
                                gpu_uuid=u,
                                value_bytes=b,
                                quality="STALE" if wk_q == "STALE" else "FRESH",
                            )
                            for u, b in sorted(dedup_allocs.items())
                        ]
                        vram_bytes = sum(x.value_bytes for x in alloc_items)
                        vram_attr = "PER_DEVICE_VERIFIED"
                elif gpu_associated:
                    rep_uuid = str(p.get("tree_gpu_uuid") or p.get("gpu_uuid") or "").strip()
                    if rep_uuid and rep_uuid in known_node_gpu_uuids and len(node_raw_gpus) == 1 and vram_bytes is not None:
                        alloc_items = [
                            WorkloadGpuAllocation(
                                gpu_uuid=rep_uuid,
                                value_bytes=vram_bytes,
                                quality="STALE" if wk_q == "STALE" else "FRESH",
                            )
                        ]
                        vram_attr = "LEGACY_AGGREGATE"
                    else:
                        vram_attr = "LEGACY_AGGREGATE"
                elif gpu_associated is False:
                    alloc_items = []
                    vram_bytes = 0
                    vram_attr = "PER_DEVICE_VERIFIED"

                # CPU & RAM metrics
                cpu_val = p.get("total_cpu_percent", p.get("cpu_percent"))
                ram_mb = p.get("total_memory_rss_mb", p.get("memory_rss_mb"))
                children_cnt = int(p.get("children_count") or 0)
                mem_method = "TREE_RSS_SUM" if children_cnt > 0 else "PROCESS_RSS"

                # Runtime elapsed_seconds numeric
                elapsed_int, started_iso = _parse_elapsed_and_started(p)

                # Fail-closed action gating
                block_reason: Optional[str] = None
                if not actions_flag:
                    block_reason = "V3.1 process actions are disabled by feature flag."
                elif wk_q != "FRESH":
                    block_reason = f"Workload state is {wk_q}; destructive controls disabled on stale data."
                elif not boot_id or not start_ticks_int or not p.get("ref"):
                    block_reason = "Trusted boot_id, ProcessRef, or start_ticks identity unavailable."

                row_quality = "STALE" if wk_q == "STALE" else "FRESH"
                row_state = "STALE" if wk_q == "STALE" else "OBSERVED"
                row_obs_at = (
                    wk_obs_iso
                    if row_quality == "FRESH"
                    else (wk_obs_iso or last_ok)
                )

                workloads_unfiltered.append(
                    SnapshotWorkloadItem(
                        id=wl_id,
                        ref=str(p.get("ref")) if p.get("ref") else None,
                        node_id=nid,
                        boot_id=boot_id,
                        host_pid=pid,
                        start_ticks=start_ticks_int,
                        container_id=container_id,
                        uid=int(p["uid"]) if p.get("uid") is not None else None,
                        member_id=mapped_member_id,
                        username=uname,
                        group_id=mapped_group_id,
                        workspace=str(p.get("workspace") or "").strip() or None,
                        display_name=str(p.get("script_display") or p.get("name") or f"pid-{pid}")[:256],
                        workload_kind=wk_kind,  # type: ignore[arg-type]
                        kind_confidence=wk_conf,  # type: ignore[arg-type]
                        kind_evidence=[str(e)[:2048] for e in wk_ev[:12]],
                        gpu_associated=gpu_associated,
                        cpu=WorkloadCpuMetric(
                            value_percent=round(float(cpu_val), 1) if cpu_val is not None else None,
                            basis="HOST_CAPACITY_PERCENT" if cpu_val is not None else "UNKNOWN",
                        ),
                        memory=WorkloadMemoryMetric(
                            value_bytes=int(round(float(ram_mb) * MIB_BYTES)) if ram_mb is not None else None,
                            method=mem_method if ram_mb is not None else "UNKNOWN",
                            unique_memory=False if ram_mb is not None else None,
                        ),
                        vram=WorkloadVramMetric(
                            value_bytes=vram_bytes,
                            attribution=vram_attr,  # type: ignore[arg-type]
                        ),
                        gpu_allocations=alloc_items,
                        elapsed_seconds=elapsed_int,
                        started_at=started_iso,
                        state=row_state,  # type: ignore[arg-type]
                        quality=row_quality,  # type: ignore[arg-type]
                        observed_at=row_obs_at,
                        is_protected=False,
                        actions_allowed=(block_reason is None),
                        action_block_reason=block_reason,
                    )
                )

        # Apply GPU, member, and group filters strictly per contract
        target_gpu_uuid = gpu_alias_to_uuid.get(scope.gpu_uuid, scope.gpu_uuid) if scope.gpu_uuid else None
        filtered_workloads: List[SnapshotWorkloadItem] = []
        for w in workloads_unfiltered:
            if target_gpu_uuid:
                alloc_match = any(a.gpu_uuid == target_gpu_uuid for a in (w.gpu_allocations or []))
                if not alloc_match:
                    continue
            if scope.member_id:
                if w.member_id != scope.member_id and w.username != scope.member_id:
                    continue
            if scope.group_id:
                if w.group_id != scope.group_id:
                    continue
            filtered_workloads.append(w)

        fresh_nodes_cnt = sum(1 for n in nodes_out if n.quality == "FRESH")
        fresh_wk_nodes_cnt = sum(1 for n in nodes_out if n.workload_quality == "FRESH")
        fresh_gpus_cnt = sum(1 for g in gpus_out if g.quality == "FRESH")
        expected_nodes_cnt = len(target_nodes)

        fresh_rows = [w for w in filtered_workloads if w.quality == "FRESH"]
        stale_rows = [w for w in filtered_workloads if w.quality == "STALE"]

        paired_fresh_gpus = [
            g
            for g in gpus_out
            if g.quality == "FRESH"
            and g.vram_used_bytes is not None
            and g.vram_capacity_bytes is not None
        ]
        device_vram_used = (
            sum(int(g.vram_used_bytes or 0) for g in paired_fresh_gpus)
            if paired_fresh_gpus
            else None
        )
        device_vram_cap = (
            sum(int(g.vram_capacity_bytes or 0) for g in paired_fresh_gpus)
            if paired_fresh_gpus
            else None
        )

        # Device vs workload reconciliation without forcing sums
        reconcile = "UNKNOWN"
        if any((g.vram_used_bytes or 0) > (g.vram_capacity_bytes or 0) for g in paired_fresh_gpus):
            reconcile = "MISMATCH"
            warnings.append(
                SnapshotWarning(
                    code="VRAM_EXCEEDS_CAPACITY",
                    message="VRAM sử dụng báo cáo trên GPU vượt quá dung lượng vật lý; kiểm tra telemetry thiết bị.",
                )
            )
        elif scope.member_id or scope.group_id or not paired_fresh_gpus or not fresh_wk_nodes_cnt:
            reconcile = "NOT_COMPARABLE"
        else:
            unfiltered_fresh = [w for w in workloads_unfiltered if w.quality == "FRESH"]
            if any(w.vram.value_bytes is None or w.vram.attribution == "UNKNOWN" for w in unfiltered_fresh):
                reconcile = "NOT_COMPARABLE"
            else:
                wl_vram_sum = sum(int(w.vram.value_bytes or 0) for w in unfiltered_fresh)
                dev_used = int(device_vram_used or 0)
                tol = max(64 * MIB_BYTES, int(0.05 * max(dev_used, 1)))
                if abs(dev_used - wl_vram_sum) <= tol:
                    reconcile = "CONSISTENT"
                else:
                    reconcile = "MISMATCH"
                    warnings.append(
                        SnapshotWarning(
                            code="VRAM_RECONCILIATION_MISMATCH",
                            message="Tổng VRAM báo cáo theo tiến trình khác với mức sử dụng cấp thiết bị (chênh lệch thời điểm lấy mẫu hoặc tiến trình ngoài bộ lọc).",
                        )
                    )

        # Composition & KPIs
        if fresh_wk_nodes_cnt == 0:
            observed_wk: Optional[int] = None
            gpu_assoc_cnt: Optional[int] = None
            gpu_unknown_cnt: Optional[int] = None
            observed_mem_cnt: Optional[int] = None
            unmapped_cnt: Optional[int] = None
            composition: Optional[WorkloadComposition] = None
        else:
            observed_wk = len(fresh_rows)
            gpu_assoc_cnt = sum(1 for w in fresh_rows if w.gpu_associated is True)
            gpu_unknown_cnt = sum(1 for w in fresh_rows if w.gpu_associated is None)
            observed_mem_cnt = len({w.member_id for w in fresh_rows if w.member_id})
            unmapped_cnt = sum(1 for w in fresh_rows if w.member_id is None)
            comp_counts = {
                "gpu_non_monitoring": 0,
                "non_gpu_compute": 0,
                "monitoring": 0,
                "other_unknown": 0,
            }
            for w in fresh_rows:
                if w.workload_kind == "MONITORING":
                    comp_counts["monitoring"] += 1
                elif w.gpu_associated is True:
                    comp_counts["gpu_non_monitoring"] += 1
                elif w.gpu_associated is False and w.workload_kind in {
                    "TRAINING",
                    "INFERENCE",
                    "NOTEBOOK",
                    "GENERAL_COMPUTE",
                }:
                    comp_counts["non_gpu_compute"] += 1
                else:
                    comp_counts["other_unknown"] += 1
            composition = WorkloadComposition(**comp_counts)

        attention_conditions = sum(
            1
            for n in nodes_out
            if n.quality in ("OFFLINE", "STALE", "ERROR") or n.workload_quality in ("OFFLINE", "STALE", "ERROR")
        )
        if reconcile == "MISMATCH":
            attention_conditions += 1

        if any(n.quality == "OFFLINE" or n.workload_quality == "OFFLINE" for n in nodes_out) and fresh_nodes_cnt == 0:
            snap_status = "OFFLINE"
        elif any(n.quality == "STALE" or n.workload_quality == "STALE" for n in nodes_out) and fresh_nodes_cnt == 0 and fresh_wk_nodes_cnt == 0:
            snap_status = "STALE"
        elif fresh_nodes_cnt < expected_nodes_cnt or fresh_wk_nodes_cnt < expected_nodes_cnt:
            snap_status = "PARTIAL"
        elif fresh_nodes_cnt == expected_nodes_cnt and fresh_wk_nodes_cnt == expected_nodes_cnt:
            snap_status = "FRESH"
        else:
            snap_status = "UNKNOWN"

        snapshot_observed_at = max(fresh_observed_times) if fresh_observed_times else None
        all_last_ok = [n.last_success_at for n in nodes_out if n.last_success_at]
        snapshot_last_ok = max(all_last_ok) if all_last_ok else None

        envelope = WorkloadSnapshotEnvelope(
            schema_version=1,
            snapshot_id=f"snap-{uuid.uuid4().hex[:16]}",
            observed_at=snapshot_observed_at,
            received_at=received_iso,
            scope=scope,
            quality=SnapshotQuality(
                status=snap_status,  # type: ignore[arg-type]
                expected_nodes=expected_nodes_cnt,
                fresh_nodes=fresh_nodes_cnt,
                fresh_workload_nodes=fresh_wk_nodes_cnt,
                expected_gpus=expected_gpu_count,
                fresh_gpus=fresh_gpus_cnt,
                last_success_at=snapshot_last_ok,
            ),
            nodes=nodes_out,
            gpus=gpus_out,
            kpis=SnapshotKpis(
                observed_workloads=observed_wk,
                last_known_workloads=len(stale_rows) if stale_rows else None,
                gpu_associated_workloads=gpu_assoc_cnt,
                gpu_association_unknown=gpu_unknown_cnt,
                observed_members=observed_mem_cnt,
                unmapped_member_workloads=unmapped_cnt,
                device_vram_used_bytes=device_vram_used,
                device_vram_capacity_bytes=device_vram_cap,
                attention_count=attention_conditions,
                gpu_coverage=GpuCoveragePair(fresh=fresh_gpus_cnt, expected=expected_gpu_count),
            ),
            composition=composition,
            workloads=filtered_workloads[:DEFAULT_SNAPSHOT_MAX_ROWS],
            device_vram=DeviceVramMeta(
                scope="DEVICE_WIDE",
                member_filter_applied=False,
                reconciliation=reconcile,  # type: ignore[arg-type]
            ),
            warnings=warnings[:100],
        )
        return envelope

    def get_workload_detail(self, workload_id: str) -> WorkloadDetailResponse:
        snap = self.get_snapshot()
        match = next((w for w in snap.workloads if w.id == workload_id or w.ref == workload_id), None)
        if match is None:
            raise LookupError(f"WORKLOAD_NOT_FOUND: '{workload_id}' not present in current snapshot")

        _, members_by_id = self._member_indexes()
        member_full_name = None
        if match.member_id and match.member_id in members_by_id:
            member_full_name = str(members_by_id[match.member_id].get("full_name") or "") or None

        gpu_labels: Dict[str, str] = {}
        for g in list(self._core.gpus or []):
            guuid = str(g.get("uuid") or g.get("id") or "").strip()
            if guuid:
                gpu_labels[guuid] = str(g.get("display_label") or g.get("model") or guuid)

        cmdline_sanitized: Optional[str] = None
        children_count = 0
        if match.node_id == getattr(settings, "LOCAL_HOST_ID", "workstation"):
            proc_res = self._get_host_raw_processes(match.node_id)
            for raw_p in proc_res.get("actual_observations", []):
                if int(raw_p.get("pid") or 0) == match.host_pid:
                    cmdline_sanitized = sanitize_message(raw_p.get("cmdline"), 1024) or None
                    children_count = int(raw_p.get("children_count") or 0)
                    break

        return WorkloadDetailResponse(
            schema_version=1,
            workload=match,
            node_name=CLUSTER_NODE_INVENTORY.get(match.node_id, {}).get("name", match.node_id),
            member_full_name=member_full_name,
            gpu_labels=gpu_labels,
            cmdline_sanitized=cmdline_sanitized,
            children_count=children_count,
            pss_bytes=None,
            pss_status="UNSUPPORTED",
            group_attribution_mode="CURRENT_MEMBER_PROFILE" if match.group_id else "UNVERIFIED",
            metric_definitions={
                "memory": "RSS cộng dồn có thể tính trùng trang nhớ chia sẻ giữa tiến trình cha-con; PSS chỉ đo theo yêu cầu.",
                "cpu": "Phần trăm tổng công suất CPU cấp host theo khoảng đo của Go Collector / psutil.",
                "vram": "Dữ liệu thiết bị lấy từ NVML; phân bổ theo PID chỉ hiển thị per-device khi xác minh từng GPU.",
            },
            actions_enabled_by_flag=self.is_actions_enabled(),
        )

    @staticmethod
    def _validate_time_range(from_str: Optional[str], to_str: Optional[str]) -> Tuple[datetime, datetime]:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        to_dt = parse_utc_iso(to_str) if to_str else now
        from_dt = parse_utc_iso(from_str) if from_str else (to_dt - timedelta(hours=24))
        if from_dt >= to_dt:
            raise ValueError("INVALID_TIME_RANGE: 'from' must be strictly less than 'to'")
        if (to_dt - from_dt) > timedelta(days=MAX_HISTORY_RANGE_DAYS):
            raise ValueError(f"INVALID_TIME_RANGE: maximum supported range is {MAX_HISTORY_RANGE_DAYS} days")
        return from_dt, to_dt

    @staticmethod
    def _choose_bucket_seconds(total_seconds: float, requested_bucket: Optional[int]) -> int:
        allowed = (60, 300, 900, 3600, 86400)
        if requested_bucket is not None and requested_bucket not in allowed:
            raise ValueError(f"INVALID_BUCKET: bucket_seconds must be one of {allowed}")
        min_bucket = max(60, requested_bucket or 60)
        for b in allowed:
            if b >= min_bucket and (total_seconds / b) <= 600:
                return b
        return 86400

    def get_analytics(
        self,
        *,
        node_id: Optional[str] = None,
        gpu_uuid: Optional[str] = None,
        member_id: Optional[str] = None,
        group_id: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        bucket_seconds: Optional[int] = None,
    ) -> WorkloadAnalyticsEnvelope:
        scope_snap = self.validate_scope_params(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            member_id=member_id,
            group_id=group_id,
        )
        scope = ScopeBase(
            node_id=scope_snap.node_id,
            gpu_uuid=scope_snap.gpu_uuid,
            member_id=scope_snap.member_id,
            group_id=scope_snap.group_id,
        )
        from_dt, to_dt = self._validate_time_range(from_ts, to_ts)
        total_span = (to_dt - from_dt).total_seconds()
        bucket = self._choose_bucket_seconds(total_span, bucket_seconds)

        capabilities: Dict[str, CapabilityEntry] = {
            "per_workload_cpu_history": CapabilityEntry(
                status="UNSUPPORTED",
                reason="telemetry_history_samples.jobs_json hiện chỉ ghi nhận vram_used_mb và trạng thái cơ bản, chưa có CPU/RAM theo từng workload.",
            ),
            "per_workload_ram_history": CapabilityEntry(
                status="UNSUPPORTED",
                reason="telemetry_history_samples.jobs_json chưa ghi nhận bộ nhớ RSS/PSS theo từng workload.",
            ),
            "research_group_attribution": CapabilityEntry(
                status="UNSUPPORTED",
                reason="Dữ liệu lịch sử chưa lưu group_id tại thời điểm quan sát; không suy diễn nhóm từ username.",
            ),
            "member_device_hours": CapabilityEntry(
                status="DEFERRED",
                reason="Cần bảng đối soát phân bổ GPU độc quyền/chia sẻ theo thời gian từ nguồn không thuộc Zalo trước khi tính GPU-hours cá nhân.",
            ),
            "history_v2_durable_collector": CapabilityEntry(
                status="DEFERRED",
                reason="Tránh can thiệp bộ thu telemetry của nhánh Zalo đang phát triển song song.",
            ),
        }

        if not self._db_ready():
            capabilities["host_and_gpu_history"] = CapabilityEntry(
                status="NO_DATA",
                reason="Cơ sở dữ liệu lịch sử chưa được kích hoạt trong phiên này.",
            )
            return WorkloadAnalyticsEnvelope(
                schema_version=1,
                scope=scope,
                from_ts=utc_rfc3339(from_dt),
                to_ts=utc_rfc3339(to_dt),
                bucket_seconds=bucket,
                coverage_ratio=None,
                series=[],
                capabilities=capabilities,
            )

        self._maybe_capture_live_analytics_sample(to_dt)

        with self._core.db.session_scope() as session:
            q = session.query(TelemetryHistorySampleRecord).filter(
                TelemetryHistorySampleRecord.collected_at >= from_dt,
                TelemetryHistorySampleRecord.collected_at < to_dt,
            )
            if scope.node_id:
                q = q.filter(TelemetryHistorySampleRecord.host_id == scope.node_id)
            rows = (
                q.order_by(
                    TelemetryHistorySampleRecord.host_id.asc(),
                    TelemetryHistorySampleRecord.collected_at.asc(),
                )
                .limit(50000)
                .all()
            )

        if not rows:
            capabilities["host_and_gpu_history"] = CapabilityEntry(
                status="NO_DATA",
                reason="Không có mẫu telemetry lịch sử nào trong khoảng thời gian đã chọn.",
            )
            return WorkloadAnalyticsEnvelope(
                schema_version=1,
                scope=scope,
                from_ts=utc_rfc3339(from_dt),
                to_ts=utc_rfc3339(to_dt),
                bucket_seconds=bucket,
                coverage_ratio=0.0,
                series=[],
                capabilities=capabilities,
            )

        capabilities["host_and_gpu_history"] = CapabilityEntry(
            status="SUPPORTED",
            reason="Đọc chỉ-đọc từ telemetry_history_samples qua chỉ mục (host_id, collected_at).",
        )

        # Bucket boundaries aligned cleanly from from_dt
        from_epoch = int(from_dt.timestamp())
        to_epoch = int(to_dt.timestamp())

        def _bucket_dt(dt_val: datetime) -> datetime:
            if dt_val.tzinfo is None:
                dt_val = dt_val.replace(tzinfo=timezone.utc)
            ep = int(dt_val.timestamp())
            idx = max(0, (ep - from_epoch) // bucket)
            b_ep = from_epoch + idx * bucket
            if b_ep >= to_epoch:
                b_ep = max(from_epoch, to_epoch - bucket)
            return datetime.fromtimestamp(b_ep, tz=timezone.utc)

        # Aggregate values into (metric, entity_id, unit, agg) -> {bucket_dt: [vals / qualities]}
        buckets: Dict[Tuple[str, str, str, str], Dict[datetime, List[Tuple[Optional[float], str]]]] = {}
        host_online_samples: Dict[str, List[datetime]] = {}
        gpu_samples: Dict[str, List[Tuple[datetime, float, bool]]] = {}

        for row in rows:
            ts = row.collected_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if not (from_dt <= ts < to_dt):
                continue
            b_ts = _bucket_dt(ts)
            hid = str(row.host_id)
            fresh_str = str(row.freshness_status or "UNKNOWN").upper()
            is_online = fresh_str in ("ONLINE", "FRESH")

            if row.cpu_percent is not None:
                k_cpu = ("host_cpu_percent", hid, "percent", "mean")
                buckets.setdefault(k_cpu, {}).setdefault(b_ts, []).append(
                    (float(row.cpu_percent) if is_online else None, "FRESH" if is_online else "OFFLINE")
                )
            if row.ram_used_gb is not None:
                k_ram = ("host_ram_used_gib", hid, "GiB", "mean")
                buckets.setdefault(k_ram, {}).setdefault(b_ts, []).append(
                    (float(row.ram_used_gb) if is_online else None, "FRESH" if is_online else "OFFLINE")
                )
            if is_online:
                host_online_samples.setdefault(hid, []).append(ts)

            for g in list(row.gpus_json or []):
                guuid = str(g.get("gpu_uuid") or g.get("gpu_id") or "").strip()
                if not guuid:
                    continue
                if scope.gpu_uuid and guuid != scope.gpu_uuid:
                    continue
                g_online = bool(g.get("host_online", is_online))
                util = g.get("utilization_gpu")
                vram_mb = g.get("vram_used_mb")
                vram_total_mb = g.get("vram_total_mb")
                vram_pct = g.get("vram_percent")
                if vram_pct is None and vram_mb is not None:
                    denom_mb = float(vram_total_mb) if vram_total_mb else 24576.0
                    vram_pct = round((float(vram_mb) / denom_mb) * 100.0, 1) if denom_mb > 0 else 0.0
                temp_c = g.get("temp_celsius")

                if util is not None or not g_online:
                    k_gu = ("gpu_utilization_percent", guuid, "percent", "mean")
                    val = float(util) if (g_online and util is not None) else None
                    buckets.setdefault(k_gu, {}).setdefault(b_ts, []).append(
                        (val, "FRESH" if val is not None else "NO_DATA")
                    )
                    if g_online and util is not None:
                        active_occ = float(util) > 0 or any(
                            str(a.get("status") or "").upper() == "IN_USE"
                            for a in list(g.get("allocations") or [])
                        )
                        gpu_samples.setdefault(guuid, []).append((ts, float(util), active_occ))
                if vram_pct is not None or not g_online:
                    k_gp = ("gpu_vram_utilization_percent", guuid, "percent", "mean")
                    val = float(vram_pct) if (g_online and vram_pct is not None) else None
                    buckets.setdefault(k_gp, {}).setdefault(b_ts, []).append(
                        (val, "FRESH" if val is not None else "NO_DATA")
                    )
                if vram_mb is not None or not g_online:
                    k_gm = ("gpu_vram_used_gib", guuid, "GiB", "mean")
                    val = round(float(vram_mb) / 1024.0, 2) if (g_online and vram_mb is not None) else None
                    buckets.setdefault(k_gm, {}).setdefault(b_ts, []).append(
                        (val, "FRESH" if val is not None else "NO_DATA")
                    )
                if temp_c is not None or not g_online:
                    k_gt = ("gpu_temperature_celsius", guuid, "celsius", "mean")
                    val = float(temp_c) if (g_online and temp_c is not None) else None
                    buckets.setdefault(k_gt, {}).setdefault(b_ts, []).append(
                        (val, "FRESH" if val is not None else "NO_DATA")
                    )

        # Compute observed device occupancy hours & compute-equivalent hours (bounded intervals <= 180s)
        for guuid, pts in gpu_samples.items():
            pts.sort(key=lambda x: x[0])
            occ_per_bucket: Dict[datetime, float] = {}
            comp_per_bucket: Dict[datetime, float] = {}
            for idx in range(1, len(pts)):
                prev_ts, prev_util, prev_occ = pts[idx - 1]
                cur_ts, _, _ = pts[idx]
                dt_s = (cur_ts - prev_ts).total_seconds()
                if 0 < dt_s <= 180.0:
                    b_ts = _bucket_dt(prev_ts)
                    if prev_occ:
                        occ_per_bucket[b_ts] = occ_per_bucket.get(b_ts, 0.0) + (dt_s / 3600.0)
                    comp_per_bucket[b_ts] = comp_per_bucket.get(b_ts, 0.0) + (dt_s * (prev_util / 100.0) / 3600.0)
            k_occ = ("device_occupancy_hours", guuid, "hours", "sum")
            for b_ts, hrs in occ_per_bucket.items():
                buckets.setdefault(k_occ, {}).setdefault(b_ts, []).append((round(hrs, 4), "FRESH"))
            k_comp = ("device_compute_equivalent_hours", guuid, "hours", "sum")
            for b_ts, hrs in comp_per_bucket.items():
                buckets.setdefault(k_comp, {}).setdefault(b_ts, []).append((round(hrs, 4), "FRESH"))

        # Insert explicit null gap points when consecutive bucket gap > 2 * bucket
        expected_total_buckets = max(1, int(total_span // bucket))
        series_list: List[AnalyticsSeries] = []
        coverages: List[float] = []

        # Prioritize GPU series first, then host CPU/RAM, capped at 20 series
        priority = {
            "gpu_utilization_percent": 0,
            "gpu_vram_utilization_percent": 1,
            "gpu_vram_used_gib": 2,
            "gpu_temperature_celsius": 3,
            "host_cpu_percent": 4,
            "host_ram_used_gib": 5,
            "device_occupancy_hours": 6,
            "device_compute_equivalent_hours": 7,
        }
        ordered_keys = sorted(buckets.keys(), key=lambda k: (priority.get(k[0], 99), k[1]))[:20]

        for k in ordered_keys:
            metric, entity_id, unit, agg = k
            bmap = buckets[k]
            sorted_ts = sorted(bmap.keys())
            pts_out: List[AnalyticsPoint] = []
            valid_cnt = 0
            prev_valid_ts: Optional[datetime] = None

            for b_ts in sorted_ts:
                if prev_valid_ts is not None and (b_ts - prev_valid_ts).total_seconds() > (2 * bucket):
                    gap_ts = prev_valid_ts + timedelta(seconds=bucket)
                    if gap_ts < b_ts and len(pts_out) < 599:
                        pts_out.append(AnalyticsPoint(ts=utc_rfc3339(gap_ts), value=None, quality="NO_DATA"))
                entries = bmap[b_ts]
                numeric_vals = [v for v, q_st in entries if v is not None and q_st == "FRESH"]
                if numeric_vals:
                    agg_val = sum(numeric_vals) if agg == "sum" else (sum(numeric_vals) / len(numeric_vals))
                    pts_out.append(
                        AnalyticsPoint(
                            ts=utc_rfc3339(b_ts),
                            value=round(agg_val, 3),
                            quality="FRESH",
                        )
                    )
                    valid_cnt += 1
                    prev_valid_ts = b_ts
                else:
                    pts_out.append(AnalyticsPoint(ts=utc_rfc3339(b_ts), value=None, quality="NO_DATA"))
                if len(pts_out) >= 600:
                    break

            cov = min(1.0, round(valid_cnt / expected_total_buckets, 4))
            coverages.append(cov)
            series_list.append(
                AnalyticsSeries(
                    metric=metric,
                    entity_id=entity_id,
                    unit=unit,
                    aggregation=agg,
                    coverage_ratio=cov,
                    points=pts_out,
                )
            )

        agg_cov = round(sum(coverages) / len(coverages), 4) if coverages else 0.0
        return WorkloadAnalyticsEnvelope(
            schema_version=1,
            scope=scope,
            from_ts=utc_rfc3339(from_dt),
            to_ts=utc_rfc3339(to_dt),
            bucket_seconds=bucket,
            coverage_ratio=agg_cov,
            series=series_list,
            capabilities=capabilities,
        )

    def get_history(
        self,
        *,
        node_id: Optional[str] = None,
        gpu_uuid: Optional[str] = None,
        member_id: Optional[str] = None,
        group_id: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> WorkloadHistoryEnvelope:
        scope_snap = self.validate_scope_params(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            member_id=member_id,
            group_id=group_id,
        )
        scope = ScopeBase(
            node_id=scope_snap.node_id,
            gpu_uuid=scope_snap.gpu_uuid,
            member_id=scope_snap.member_id,
            group_id=scope_snap.group_id,
        )
        from_dt, to_dt = self._validate_time_range(from_ts, to_ts)
        if limit < 1 or limit > 100:
            raise ValueError("INVALID_LIMIT: limit must be between 1 and 100")
        cursor_obj = decode_cursor(cursor)

        if not self._db_ready():
            return WorkloadHistoryEnvelope(
                schema_version=1,
                scope=scope,
                from_ts=utc_rfc3339(from_dt),
                to_ts=utc_rfc3339(to_dt),
                items=[],
                next_cursor=None,
                capability="NO_DATA",
            )

        members_by_user, _ = self._member_indexes()
        with self._core.db.session_scope() as session:
            q = session.query(TelemetryHistorySampleRecord).filter(
                TelemetryHistorySampleRecord.collected_at >= from_dt,
                TelemetryHistorySampleRecord.collected_at < to_dt,
            )
            if scope.node_id:
                q = q.filter(TelemetryHistorySampleRecord.host_id == scope.node_id)
            rows = (
                q.order_by(
                    TelemetryHistorySampleRecord.host_id.asc(),
                    TelemetryHistorySampleRecord.collected_at.asc(),
                )
                .limit(20000)
                .all()
            )

        if not rows:
            return WorkloadHistoryEnvelope(
                schema_version=1,
                scope=scope,
                from_ts=utc_rfc3339(from_dt),
                to_ts=utc_rfc3339(to_dt),
                items=[],
                next_cursor=None,
                capability="NO_DATA",
            )

        # Aggregate observed workload intervals conservatively without fabricating exits on offline gaps
        tracks: Dict[str, Dict[str, Any]] = {}
        latest_online_ts_by_host: Dict[str, datetime] = {}

        for row in rows:
            ts = row.collected_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            hid = str(row.host_id)
            if str(row.freshness_status or "").upper() in ("ONLINE", "FRESH"):
                latest_online_ts_by_host[hid] = max(latest_online_ts_by_host.get(hid, ts), ts)
            for j in list(row.jobs_json or []):
                owner = str(j.get("owner") or j.get("username") or "").strip()
                jid = str(j.get("id") or j.get("name") or "").strip()
                if not jid:
                    continue
                key_raw = f"{hid}:{jid}:{owner}"
                obs_id = "hist-" + hashlib.sha256(key_raw.encode("utf-8")).hexdigest()[:18]
                m_rec = members_by_user.get(owner)
                mapped_mid = str(m_rec["id"]) if (m_rec and m_rec.get("id")) else None
                mapped_gid = self._normalize_group_id(m_rec)
                if scope.member_id and mapped_mid != scope.member_id and owner != scope.member_id:
                    continue
                if scope.group_id and mapped_gid != scope.group_id:
                    continue
                if scope.gpu_uuid and str(j.get("gpu_key") or "") != scope.gpu_uuid:
                    continue

                entry = tracks.get(obs_id)
                if not entry:
                    tracks[obs_id] = {
                        "id": obs_id,
                        "node_id": hid,
                        "member_id": mapped_mid,
                        "group_id": None,  # Legacy JSON lacks observation-time group_id
                        "first_seen": ts,
                        "last_seen": ts,
                        "samples": 1,
                        "raw_id": jid,
                    }
                else:
                    entry["first_seen"] = min(entry["first_seen"], ts)
                    entry["last_seen"] = max(entry["last_seen"], ts)
                    entry["samples"] += 1

        all_items: List[HistoryWorkloadItem] = []
        for entry in tracks.values():
            hid = entry["node_id"]
            last_dt = entry["last_seen"]
            first_dt = entry["first_seen"]
            host_latest = latest_online_ts_by_host.get(hid)
            still_observed = bool(host_latest and (host_latest - last_dt).total_seconds() <= 120.0)
            obs_secs = max(0, int((last_dt - first_dt).total_seconds()))
            all_items.append(
                HistoryWorkloadItem(
                    id=entry["id"],
                    node_id=hid,
                    member_id=entry["member_id"],
                    group_id=entry["group_id"],
                    first_seen_at=utc_rfc3339(first_dt),
                    last_seen_at=utc_rfc3339(last_dt),
                    observed_seconds=obs_secs,
                    state="OBSERVED" if still_observed else "MISSING_UNCONFIRMED",
                    exit_code=None,
                    exit_reason=None,
                    evidence=(
                        f"Quan sát {entry['samples']} mẫu telemetry ({entry['raw_id']}); dữ liệu legacy không lưu exit code."
                    ),
                    actionable=False,
                )
            )

        all_items.sort(key=lambda x: (x.last_seen_at or "", x.id), reverse=True)
        if cursor_obj:
            c_ts = str(cursor_obj.get("last_seen_at") or "")
            c_id = str(cursor_obj.get("id") or "")
            all_items = [it for it in all_items if (it.last_seen_at or "", it.id) < (c_ts, c_id)]

        page_items = all_items[:limit]
        next_cur = None
        if len(all_items) > limit and page_items:
            last_it = page_items[-1]
            next_cur = encode_cursor({"last_seen_at": last_it.last_seen_at or "", "id": last_it.id})

        return WorkloadHistoryEnvelope(
            schema_version=1,
            scope=scope,
            from_ts=utc_rfc3339(from_dt),
            to_ts=utc_rfc3339(to_dt),
            items=page_items,
            next_cursor=next_cur,
            capability="PARTIAL" if page_items else "NO_DATA",
        )

    def get_events(
        self,
        *,
        node_id: Optional[str] = None,
        gpu_uuid: Optional[str] = None,
        member_id: Optional[str] = None,
        group_id: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> WorkloadEventsEnvelope:
        scope_snap = self.validate_scope_params(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            member_id=member_id,
            group_id=group_id,
        )
        scope = ScopeBase(
            node_id=scope_snap.node_id,
            gpu_uuid=scope_snap.gpu_uuid,
            member_id=scope_snap.member_id,
            group_id=scope_snap.group_id,
        )
        from_dt, to_dt = self._validate_time_range(from_ts, to_ts)
        if limit < 1 or limit > 100:
            raise ValueError("INVALID_LIMIT: limit must be between 1 and 100")
        cursor_obj = decode_cursor(cursor)

        workload_actions = {
            "PROCESS_SIGTERM",
            "PROCESS_SIGKILL",
            "V31_PROCESS_ACTION",
            "V31_PROCESS_ACTION_DENIED",
            "JOB_STOP",
        }
        items: List[WorkloadEventItem] = []
        raw_logs = list(getattr(self._core, "audit_logs", []) or [])

        if self._db_ready():
            try:
                with self._core.db.session_scope() as session:
                    db_rows = (
                        session.query(AuditEventRecord)
                        .filter(AuditEventRecord.action.in_(workload_actions))
                        .order_by(AuditEventRecord.timestamp.desc(), AuditEventRecord.id.desc())
                        .limit(500)
                        .all()
                    )
                    seen_ids = set()
                    combined = []
                    for r in db_rows:
                        seen_ids.add(r.id)
                        combined.append(
                            {
                                "id": r.id,
                                "timestamp": r.timestamp,
                                "actor": r.actor,
                                "action": r.action,
                                "target_id": r.target_id,
                                "result": r.result,
                                "correlation_id": r.correlation_id,
                                "details": r.details,
                            }
                        )
                    for mem_entry in raw_logs:
                        if mem_entry.get("id") not in seen_ids and mem_entry.get("action") in workload_actions:
                            combined.append(mem_entry)
                    raw_logs = combined
            except Exception:
                pass

        for entry in raw_logs:
            action = str(entry.get("action") or "")
            if action not in workload_actions:
                continue
            ts_raw = str(entry.get("timestamp") or "")
            try:
                ts_dt = parse_utc_iso(ts_raw)
            except ValueError:
                continue
            if not (from_dt <= ts_dt < to_dt):
                continue
            res_id = str(entry.get("target_id") or "unknown")
            if scope.node_id and not res_id.startswith(f"{scope.node_id}:") and scope.node_id not in res_id:
                continue
            items.append(
                WorkloadEventItem(
                    id=str(entry.get("id") or f"evt-{uuid.uuid4().hex[:12]}"),
                    occurred_at=utc_rfc3339(ts_dt),
                    event_type=action,
                    resource_id=res_id[:256],
                    actor_id=str(entry.get("actor") or "")[:256] or None,
                    result=str(entry.get("result") or "UNKNOWN")[:256],
                    message=sanitize_message(entry.get("details"), 1024),
                    evidence="Verified audit_events record",
                    correlation_id=str(entry.get("correlation_id") or "")[:256] or None,
                )
            )

        items.sort(key=lambda x: (x.occurred_at, x.id), reverse=True)
        if cursor_obj:
            c_ts = str(cursor_obj.get("occurred_at") or "")
            c_id = str(cursor_obj.get("id") or "")
            items = [it for it in items if (it.occurred_at, it.id) < (c_ts, c_id)]

        page_items = items[:limit]
        next_cur = None
        if len(items) > limit and page_items:
            last_it = page_items[-1]
            next_cur = encode_cursor({"occurred_at": last_it.occurred_at, "id": last_it.id})

        return WorkloadEventsEnvelope(
            schema_version=1,
            scope=scope,
            from_ts=utc_rfc3339(from_dt),
            to_ts=utc_rfc3339(to_dt),
            items=page_items,
            next_cursor=next_cur,
            capability="PARTIAL" if page_items else "NO_DATA",
        )

    def _audit_action(
        self,
        actor: str,
        action: str,
        target_ref: str,
        result: str,
        correlation_id: str,
        message: str,
    ) -> None:
        safe_msg = sanitize_message(message, 1000)
        if hasattr(self._core, "_audit"):
            self._core._audit(actor, action, "PROCESS", target_ref, result, safe_msg)

    def _persist_idempotency(
        self,
        *,
        actor: str,
        endpoint: str,
        key: str,
        req_hash: str,
        envelope: ProcessActionResultEnvelope,
    ) -> None:
        with self._lock:
            self._operation_store[(actor, key)] = (req_hash, envelope)
        if self._db_ready():
            try:
                with self._core.db.session_scope() as session:
                    existing = (
                        session.query(IdempotencyRecord)
                        .filter_by(actor=actor, endpoint=endpoint, idempotency_key=key)
                        .first()
                    )
                    if existing is None:
                        session.add(
                            IdempotencyRecord(
                                actor=actor,
                                endpoint=endpoint,
                                idempotency_key=key,
                                request_hash=req_hash,
                                status_code=200,
                                response_json=envelope.model_dump(mode="json"),
                                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                            )
                        )
            except Exception:
                pass

    def _lookup_idempotency(
        self,
        *,
        actor: str,
        endpoint: str,
        key: str,
        req_hash: str,
    ) -> Optional[ProcessActionResultEnvelope]:
        with self._lock:
            mem = self._operation_store.get((actor, key))
            if mem is not None:
                prev_hash, prev_env = mem
                if prev_hash != req_hash:
                    raise RuntimeError("IDEMPOTENCY_CONFLICT: operation_key reused with different payload")
                return prev_env

        if self._db_ready():
            with self._core.db.session_scope() as session:
                rec = (
                    session.query(IdempotencyRecord)
                    .filter_by(actor=actor, endpoint=endpoint, idempotency_key=key)
                    .first()
                )
                if rec is not None:
                    if rec.request_hash != req_hash:
                        raise RuntimeError("IDEMPOTENCY_CONFLICT: operation_key reused with different payload")
                    return ProcessActionResultEnvelope.model_validate(rec.response_json)
        return None

    def execute_v31_process_action(
        self,
        *,
        ref: str,
        req: V31ProcessActionRequest,
        actor: str,
        is_admin: bool,
        allow_when_flag_off: bool = False,
    ) -> ProcessActionResultEnvelope:
        """Hardened V3.1 process action with durable idempotency, target lock, identity re-check, and fail-closed pidfd."""
        correlation_id = f"corr-v31-{uuid.uuid4().hex[:12]}"
        action_id = f"act-{uuid.uuid4().hex[:12]}"
        if not is_admin:
            self._audit_action(actor, "V31_PROCESS_ACTION_DENIED", ref, "DENIED", correlation_id, "Non-admin actor denied")
            raise PermissionError("Chỉ quản trị viên mới có quyền điều khiển tiến trình V3.1")

        if not allow_when_flag_off and not self.is_actions_enabled():
            self._audit_action(actor, "V31_PROCESS_ACTION_DENIED", ref, "DENIED", correlation_id, "Actions disabled by feature flag")
            raise PermissionError("V31_ACTIONS_DISABLED: Thao tác điều khiển tiến trình V3.1 đang bị khóa theo feature flag")

        parts = str(ref or "").split(":", 3)
        if len(parts) != 4:
            raise ValueError(f"INVALID_PROCESS_REF: '{ref}' must follow server:container:pid:start_ticks")
        ref_node, ref_container, pid_raw, ticks_raw = parts
        try:
            pid = int(pid_raw)
            ref_ticks = int(ticks_raw)
        except ValueError as exc:
            raise ValueError("INVALID_PROCESS_REF: pid and start_ticks must be integers") from exc

        if pid <= 1:
            self._audit_action(actor, "V31_PROCESS_ACTION_DENIED", ref, "DENIED", correlation_id, f"Protected PID {pid}")
            raise PermissionError(f"PROTECTED_PROCESS_DENIED: PID {pid} is protected")

        if req.action == "SIGKILL":
            if not req.force_confirmed:
                raise ValueError("FORCE_CONFIRMATION_REQUIRED: SIGKILL requires force_confirmed=true")
            expected_confirm = f"KILL {pid}"
            if (req.typed_confirmation or "").strip() not in (expected_confirm, ref):
                raise ValueError(f"TYPED_CONFIRMATION_REQUIRED: expected '{expected_confirm}'")

        endpoint_key = f"v31_action:{ref}"
        canonical_req = {
            "ref": ref,
            "action": req.action,
            "reason": req.reason.strip(),
            "terminate_tree": bool(req.terminate_tree),
            "expected_boot_id": req.expected_boot_id,
            "expected_start_ticks": req.expected_start_ticks,
            "expected_uid": req.expected_uid,
            "expected_container_id": req.expected_container_id,
        }
        req_hash = hashlib.sha256(
            json.dumps(canonical_req, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        prev = self._lookup_idempotency(
            actor=actor,
            endpoint=endpoint_key,
            key=req.operation_key,
            req_hash=req_hash,
        )
        if prev is not None:
            return prev

        with self._lock:
            if ref in self._in_flight_refs:
                raise RuntimeError(f"ACTION_IN_FLIGHT: An operation for '{ref}' is already in progress")
            self._in_flight_refs.add(ref)

        try:
            local_host_id = getattr(settings, "LOCAL_HOST_ID", "workstation")
            if ref_node != local_host_id and self._action_backend.__class__ is OSPidfdProcessBackend:
                raise RuntimeError(f"REMOTE_UNSUPPORTED_FAIL_CLOSED: remote node '{ref_node}' has no live pidfd channel")

            root_live = self._action_backend.inspect_pid(pid)
            if root_live is None:
                raise LookupError(f"PROCESS_NOT_FOUND: PID {pid} no longer exists")

            expected_ticks = req.expected_start_ticks if req.expected_start_ticks is not None else ref_ticks
            if root_live.start_ticks != expected_ticks:
                raise ValueError("PID_RECYCLED: start_ticks mismatch on live verification")
            if not req.expected_boot_id or not root_live.boot_id or root_live.boot_id != req.expected_boot_id:
                raise ValueError("BOOT_IDENTITY_MISMATCH: missing or changed host boot_id")
            expected_cid = req.expected_container_id if req.expected_container_id is not None else ref_container
            actual_cid = root_live.container_id or "host"
            if actual_cid != (expected_cid or "host"):
                raise PermissionError(f"CONTAINER_MISMATCH: expected '{expected_cid}', got '{actual_cid}'")
            if req.expected_uid is not None and root_live.uid != req.expected_uid:
                raise PermissionError(f"UID_MISMATCH: expected uid {req.expected_uid}, got {root_live.uid}")
            if is_identity_protected(root_live):
                raise PermissionError(f"PROTECTED_PROCESS_DENIED: target '{root_live.name}' (PID {pid}) is protected")

            targets: List[LiveProcIdentity] = [root_live]
            skipped_desc: List[str] = []
            if req.terminate_tree:
                for ch in self._action_backend.list_children(root_live.pid):
                    if ch.pid == root_live.pid or ch.pid <= 2:
                        continue
                    if ch.uid != root_live.uid:
                        skipped_desc.append(f"pid={ch.pid} owner_mismatch({ch.username}!={root_live.username})")
                        continue
                    ch_cid = ch.container_id or "host"
                    if ch_cid != actual_cid:
                        skipped_desc.append(f"pid={ch.pid} container_mismatch({ch_cid}!={actual_cid})")
                        continue
                    if is_identity_protected(ch):
                        skipped_desc.append(f"pid={ch.pid} protected({ch.name})")
                        continue
                    targets.append(ch)

            sig = signal.SIGKILL if req.action == "SIGKILL" else signal.SIGTERM
            signal_errors: Dict[int, str] = {}
            for t in reversed(targets):
                try:
                    self._action_backend.send_signal_pidfd(t.pid, t.start_ticks, t.uid, int(sig))
                except LookupError:
                    continue
                except PermissionError as pe:
                    if t.pid == root_live.pid:
                        raise
                    signal_errors[t.pid] = str(pe)
                except Exception as exc:
                    if t.pid == root_live.pid and "PIDFD_UNSUPPORTED_FAIL_CLOSED" in str(exc):
                        raise
                    signal_errors[t.pid] = sanitize_message(str(exc), 200)

            # Bounded exit verification without optimistic success
            wait_budget = 0.25 if req.action == "SIGKILL" else min(5.0, float(req.timeout_seconds))
            deadline = time.monotonic() + wait_budget
            remaining: List[int] = []
            while True:
                remaining = []
                for t in targets:
                    after = self._action_backend.inspect_pid(t.pid)
                    if after is not None and after.start_ticks == t.start_ticks and after.state != "Z":
                        remaining.append(t.pid)
                if not remaining or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)

            executed_iso = utc_rfc3339()
            root_alive = root_live.pid in remaining
            if root_alive:
                status = "FAILED_STILL_RUNNING" if req.action == "SIGKILL" else "TIMEOUT_STILL_RUNNING"
                verif = "STILL_RUNNING"
                msg = f"Tiến trình gốc PID {root_live.pid} vẫn đang chạy sau thời gian chờ xác minh."
            elif remaining or skipped_desc or signal_errors:
                status = "PARTIAL_FAILURE"
                verif = "PARTIAL_FAILURE"
                msg = (
                    f"Tiến trình gốc PID {root_live.pid} đã thoát nhưng còn {len(remaining)} tiến trình con hoặc {len(skipped_desc)} tiến trình bỏ qua."
                )
            else:
                status = "KILLED" if req.action == "SIGKILL" else "TERMINATED"
                verif = "VERIFIED"
                msg = f"Đã {req.action} và xác minh thoát hoàn toàn cho PID {root_live.pid}."

            env = ProcessActionResultEnvelope(
                schema_version=1,
                action_id=action_id,
                action=req.action,
                status=status,  # type: ignore[arg-type]
                verification=verif,  # type: ignore[arg-type]
                pids_remaining=sorted(set(remaining)),
                skipped_descendants=skipped_desc,
                message=sanitize_message(msg, 500),
                correlation_id=correlation_id,
                executed_at=executed_iso,
            )
            self._persist_idempotency(
                actor=actor,
                endpoint=endpoint_key,
                key=req.operation_key,
                req_hash=req_hash,
                envelope=env,
            )
            self._audit_action(
                actor,
                "V31_PROCESS_ACTION",
                ref,
                env.status,
                correlation_id,
                f"action={req.action} verification={env.verification} remaining={env.pids_remaining} reason={sanitize_message(req.reason, 200)}",
            )
            return env
        finally:
            with self._lock:
                self._in_flight_refs.discard(ref)


_WORKLOAD_SERVICE: Optional[WorkloadIntelligenceService] = None


def get_workload_intelligence_service(core_service: Optional[Any] = None) -> WorkloadIntelligenceService:
    global _WORKLOAD_SERVICE
    if core_service is not None:
        if _WORKLOAD_SERVICE is None or _WORKLOAD_SERVICE._core is not core_service:
            _WORKLOAD_SERVICE = WorkloadIntelligenceService(core_service)
        return _WORKLOAD_SERVICE
    if _WORKLOAD_SERVICE is None:
        from app.services.labops_service import service

        _WORKLOAD_SERVICE = WorkloadIntelligenceService(service)
    return _WORKLOAD_SERVICE
