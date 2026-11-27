from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Dict, List, Optional

from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.core.security import issue_token
from app.db.models import AuditEventRecord, TelemetryHistorySampleRecord
from app.db.session import DatabaseManager
from app.main import app
from app.schemas.workload_v31 import V31ProcessActionRequest
from app.services.labops_service import service
from app.services.workload_intelligence_service import (
    LiveProcIdentity,
    WorkloadIntelligenceService,
    export_workloads_csv,
    get_workload_intelligence_service,
    validate_contract_invariants,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_ROOT = REPO_ROOT / "docs" / "workload-intelligence-v3.1" / "reference-package"


class FakeProcessBackend:
    def __init__(self, *, pidfd_enosys: bool = False) -> None:
        self.procs: Dict[int, LiveProcIdentity] = {}
        self.children: Dict[int, List[LiveProcIdentity]] = {}
        self.pidfd_enosys = pidfd_enosys
        self.signal_calls: List[int] = []
        self.stay_alive: set[int] = set()
        self.signal_errors: Dict[int, Exception] = {}

    def inspect_pid(self, pid: int) -> Optional[LiveProcIdentity]:
        return self.procs.get(pid)

    def list_children(self, root_pid: int) -> List[LiveProcIdentity]:
        return list(self.children.get(root_pid, []))

    def send_signal_pidfd(
        self,
        pid: int,
        expected_ticks: int,
        expected_uid: int,
        sig: int,
    ) -> None:
        if self.pidfd_enosys:
            raise RuntimeError("PIDFD_UNSUPPORTED_FAIL_CLOSED: pidfd_open ENOSYS")
        if pid in self.signal_errors:
            raise self.signal_errors[pid]
        proc = self.procs.get(pid)
        if not proc:
            raise LookupError(f"PROCESS_NOT_FOUND: PID {pid}")
        if proc.start_ticks != expected_ticks:
            raise ValueError("PID_RECYCLED")
        if proc.uid != expected_uid:
            raise PermissionError("UID_MISMATCH")
        self.signal_calls.append(pid)
        if pid not in self.stay_alive:
            self.procs.pop(pid, None)


def _base_servers(now_iso: str) -> List[Dict]:
    return [
        {
            "id": "workstation",
            "status": "online",
            "freshness_status": "ONLINE",
            "telemetry_source": "go_node_agent",
            "collected_at": now_iso,
            "age_seconds": 1.0,
        },
        {
            "id": "track",
            "status": "unknown",
            "freshness_status": "OFFLINE",
            "telemetry_source": "remote_unverified",
            "collected_at": now_iso,
            "age_seconds": 999999.0,
        },
    ]


def _base_gpus(now_iso: str) -> List[Dict]:
    return [
        {
            "id": "workstation-gpu-01",
            "uuid": "GPU-WS-01",
            "server_id": "workstation",
            "display_label": "WS-GPU-01",
            "model": "NVIDIA A30",
            "vram_used_mb": 14600,
            "vram_total_mb": 24576,
            "gpu_util_percent": 65,
            "freshness_status": "ONLINE",
            "collected_at": now_iso,
        }
    ]


class _CoreStub:
    def __init__(self, tmp_path: Path) -> None:
        self.db = DatabaseManager(str(tmp_path), database_url=f"sqlite:///{tmp_path / 'v31_test.sqlite3'}")
        self.members = [
            {
                "id": "mem-vinh",
                "username": "vinh_test",
                "full_name": "Vinh Test",
                "research_group": "AI Systems",
                "status": "READY",
            },
            {
                "id": "mem-nhan",
                "username": "nhannv",
                "full_name": "Nhan Nguyen",
                "research_group": None,
                "status": "READY",
            },
        ]
        self.servers = _base_servers("2026-11-25T14:14:00Z")
        self.gpus = _base_gpus("2026-11-25T14:14:00Z")
        self.audit_logs: List[Dict] = []

    def _audit(self, actor: str, action: str, target_type: str, target_id: str, result: str, details: str) -> None:
        self.audit_logs.insert(
            0,
            {
                "id": f"aud-test-{len(self.audit_logs) + 1}",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "actor": actor,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "result": result,
                "correlation_id": "corr-test",
                "details": details,
            },
        )


def test_d13_validate_all_10_synthetic_fixtures_and_negative_kpi() -> None:
    snapshot_names = [
        "healthy",
        "partial-coverage",
        "offline-node",
        "stale-snapshot",
        "empty-workloads",
        "multi-gpu",
    ]
    for name in snapshot_names:
        payload = json.loads((PKG_ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))
        validate_contract_invariants(payload, "workload-snapshot")

    for name, kind in (
        ("analytics", "workload-analytics"),
        ("history", "workload-history"),
        ("events", "workload-events"),
        ("action-partial", "process-action-result"),
    ):
        payload = json.loads((PKG_ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))
        validate_contract_invariants(payload, kind)

    bad = json.loads((PKG_ROOT / "fixtures" / "partial-coverage.json").read_text(encoding="utf-8"))
    bad["kpis"]["observed_workloads"] = 999
    with pytest.raises(AssertionError):
        validate_contract_invariants(bad, "workload-snapshot")


def test_g1_partial_stale_empty_offline_and_taxonomy_invariants(tmp_path: Path) -> None:
    core = _CoreStub(tmp_path)
    svc = WorkloadIntelligenceService(core)
    now = datetime(2026, 11, 25, 14, 14, 0, tzinfo=timezone.utc)
    now_iso = "2026-11-25T14:14:00Z"

    # D01 & D05 & D09: Partial rack offline, unmapped member, gpustat MONITORING vs substring trap, python GENERAL_COMPUTE
    obs = {
        "workstation": {
            "collector_status": "OK",
            "actual_observations": [
                {
                    "pid": 1001,
                    "username": "vinh_test",
                    "name": "python3",
                    "script_display": "python3",
                    "cmdline": "python3 /data/gpustat_archive/eval.py",
                    "start_time_ticks": 500,
                    "boot_id": "boot-ws-1",
                    "ref": "workstation:host:1001:500",
                    "is_root_workload": True,
                    "is_gpu_process": True,
                    "total_vram_mb": 7100,
                    "total_memory_rss_mb": 4096.0,
                    "total_cpu_percent": 15.0,
                    "children_count": 2,
                    "elapsed_seconds": 3600,
                    "gpu_allocations": [{"gpu_uuid": "GPU-WS-01", "value_bytes": 7100 * 1048576}],
                },
                {
                    "pid": 1002,
                    "username": "unregistered_user",
                    "name": "gpustat",
                    "script_display": "gpustat",
                    "cmdline": "gpustat -i 2",
                    "start_time_ticks": 600,
                    "boot_id": "boot-ws-1",
                    "ref": "workstation:host:1002:600",
                    "is_root_workload": True,
                    "is_gpu_process": False,
                    "vram_mb": 0,
                    "memory_rss_mb": 64.0,
                    "cpu_percent": 0.2,
                    "children_count": 0,
                    "elapsed_seconds": 900,
                },
            ],
        },
        "track": {"collector_status": "NOT_CONFIGURED", "actual_observations": []},
    }

    snap = svc.get_snapshot(
        raw_observations_override=obs,
        servers_override=_base_servers(now_iso),
        gpus_override=_base_gpus(now_iso),
        now_dt=now,
    )
    snap_dict = snap.model_dump(mode="json")
    validate_contract_invariants(snap_dict, "workload-snapshot")

    assert snap.quality.status == "PARTIAL"
    assert snap.kpis.gpu_coverage.fresh == 1
    assert snap.kpis.gpu_coverage.expected == 4
    assert snap.kpis.observed_workloads == 2
    assert snap.kpis.observed_members == 1
    assert snap.kpis.unmapped_member_workloads == 1
    assert snap.composition is not None
    assert snap.composition.monitoring == 1
    assert snap.composition.gpu_non_monitoring == 1

    # D05 substring trap: eval.py inside gpustat_archive must NOT become MONITORING or TRAINING
    w_eval = next(w for w in snap.workloads if w.host_pid == 1001)
    assert w_eval.workload_kind == "GENERAL_COMPUTE"
    assert w_eval.memory.method == "TREE_RSS_SUM"
    assert w_eval.memory.unique_memory is False

    # D04: Member filter never filters device_vram
    snap_mem = svc.get_snapshot(
        member_id="mem-vinh",
        raw_observations_override=obs,
        servers_override=_base_servers(now_iso),
        gpus_override=_base_gpus(now_iso),
        now_dt=now,
    )
    assert snap_mem.kpis.observed_workloads == 1
    assert snap_mem.kpis.device_vram_used_bytes == 14600 * 1048576
    assert snap_mem.device_vram.member_filter_applied is False

    # D04 stale transition when collector fails after fresh observation
    snap_stale = svc.get_snapshot(
        node_id="workstation",
        raw_observations_override={"workstation": {"collector_status": "ERROR", "actual_observations": []}},
        servers_override=_base_servers(now_iso),
        gpus_override=_base_gpus(now_iso),
        now_dt=now + timedelta(seconds=45),
    )
    validate_contract_invariants(snap_stale.model_dump(mode="json"), "workload-snapshot")
    assert snap_stale.quality.status == "STALE"
    assert snap_stale.kpis.observed_workloads is None
    assert snap_stale.kpis.last_known_workloads == 2
    assert all(w.actions_allowed is False for w in snap_stale.workloads)

    # D03: Fresh node with 0 workloads -> observed_workloads == 0 (valid zero)
    snap_empty = svc.get_snapshot(
        node_id="workstation",
        raw_observations_override={"workstation": {"collector_status": "OK", "actual_observations": []}},
        servers_override=[_base_servers(now_iso)[0]],
        gpus_override=_base_gpus(now_iso),
        now_dt=now + timedelta(seconds=60),
    )
    validate_contract_invariants(snap_empty.model_dump(mode="json"), "workload-snapshot")
    assert snap_empty.kpis.observed_workloads == 0

    # D02: Offline node -> observed_workloads is None
    snap_off = svc.get_snapshot(
        node_id="track",
        raw_observations_override={"track": {"collector_status": "NOT_CONFIGURED", "actual_observations": []}},
        servers_override=[_base_servers(now_iso)[1]],
        gpus_override=[],
        now_dt=now,
    )
    validate_contract_invariants(snap_off.model_dump(mode="json"), "workload-snapshot")
    assert snap_off.quality.status == "OFFLINE"
    assert snap_off.kpis.observed_workloads is None


def test_d06_multi_gpu_dedup_and_d08_vram_mismatch(tmp_path: Path) -> None:
    core = _CoreStub(tmp_path)
    svc = WorkloadIntelligenceService(core)
    now = datetime(2026, 11, 25, 14, 14, 0, tzinfo=timezone.utc)
    now_iso = "2026-11-25T14:14:00Z"
    two_gpus = [
        {
            "id": "ws-gpu-01",
            "uuid": "GPU-A",
            "server_id": "workstation",
            "vram_used_mb": 4000,
            "vram_total_mb": 24576,
            "gpu_util_percent": 70,
            "freshness_status": "ONLINE",
            "collected_at": now_iso,
        },
        {
            "id": "ws-gpu-02",
            "uuid": "GPU-B",
            "server_id": "workstation",
            "vram_used_mb": 6000,
            "vram_total_mb": 24576,
            "gpu_util_percent": 80,
            "freshness_status": "ONLINE",
            "collected_at": now_iso,
        },
    ]
    obs = {
        "workstation": {
            "collector_status": "OK",
            "actual_observations": [
                {
                    "pid": 4401,
                    "username": "vinh_test",
                    "name": "torchrun",
                    "cmdline": "torchrun --nproc_per_node=2 train.py",
                    "start_time_ticks": 900,
                    "boot_id": "boot-1",
                    "ref": "workstation:host:4401:900",
                    "is_root_workload": True,
                    "is_gpu_process": True,
                    "total_vram_mb": 2500,
                    "gpu_allocations": [
                        {"gpu_uuid": "GPU-A", "value_bytes": 1000 * 1048576},
                        {"gpu_uuid": "GPU-B", "value_bytes": 1500 * 1048576},
                    ],
                }
            ],
        }
    }
    snap = svc.get_snapshot(
        node_id="workstation",
        raw_observations_override=obs,
        servers_override=[_base_servers(now_iso)[0]],
        gpus_override=two_gpus,
        now_dt=now,
    )
    validate_contract_invariants(snap.model_dump(mode="json"), "workload-snapshot")
    assert snap.workloads[0].workload_kind == "TRAINING"
    assert snap.workloads[0].vram.attribution == "PER_DEVICE_VERIFIED"
    assert len(snap.workloads[0].gpu_allocations or []) == 2
    assert snap.device_vram.reconciliation == "MISMATCH"
    assert any(w.code == "VRAM_RECONCILIATION_MISMATCH" for w in snap.warnings)


def test_p04_csv_formula_injection_protection() -> None:
    core_dir = REPO_ROOT / "docs"
    _ = core_dir
    snap_raw = json.loads((PKG_ROOT / "fixtures" / "healthy.json").read_text(encoding="utf-8"))
    from app.schemas.workload_v31 import WorkloadSnapshotEnvelope

    env = WorkloadSnapshotEnvelope.model_validate(snap_raw)
    env.workloads[0].display_name = "=CMD('calc')"
    env.workloads[1].workspace = "+HYPERLINK(1)"
    csv_text = export_workloads_csv(env.workloads, scope=env.scope)
    assert "'=CMD('calc')" in csv_text
    assert "'+HYPERLINK(1)" in csv_text


def test_g4_historical_analytics_gaps_cursor_and_capabilities(tmp_path: Path) -> None:
    core = _CoreStub(tmp_path)
    t0 = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)
    with core.db.session_scope() as s:
        s.add_all(
            [
                TelemetryHistorySampleRecord(
                    id="th-1",
                    host_id="workstation",
                    collected_at=t0,
                    freshness_status="ONLINE",
                    source="go_node_agent",
                    cpu_percent=20.0,
                    ram_used_gb=30.0,
                    ram_total_gb=125.0,
                    gpus_json=[{"gpu_uuid": "GPU-WS-01", "host_online": True, "utilization_gpu": 70.0, "vram_used_mb": 8192.0, "temp_celsius": 65.0}],
                    jobs_json=[{"id": "proc-111", "owner": "vinh_test", "name": "python", "host_id": "workstation"}],
                ),
                TelemetryHistorySampleRecord(
                    id="th-2",
                    host_id="workstation",
                    collected_at=t0 + timedelta(minutes=1),
                    freshness_status="ONLINE",
                    source="go_node_agent",
                    cpu_percent=25.0,
                    ram_used_gb=31.0,
                    ram_total_gb=125.0,
                    gpus_json=[{"gpu_uuid": "GPU-WS-01", "host_online": True, "utilization_gpu": 80.0, "vram_used_mb": 9216.0, "temp_celsius": 68.0}],
                    jobs_json=[{"id": "proc-111", "owner": "vinh_test", "name": "python", "host_id": "workstation"}],
                ),
                # Gap of 10 minutes (> 2 * bucket=60) before next sample
                TelemetryHistorySampleRecord(
                    id="th-3",
                    host_id="workstation",
                    collected_at=t0 + timedelta(minutes=12),
                    freshness_status="ONLINE",
                    source="go_node_agent",
                    cpu_percent=10.0,
                    ram_used_gb=28.0,
                    ram_total_gb=125.0,
                    gpus_json=[{"gpu_uuid": "GPU-WS-01", "host_online": True, "utilization_gpu": 40.0, "vram_used_mb": 4096.0, "temp_celsius": 58.0}],
                    jobs_json=[{"id": "proc-222", "owner": "nhannv", "name": "python", "host_id": "workstation"}],
                ),
            ]
        )

    svc = WorkloadIntelligenceService(core)
    an = svc.get_analytics(
        node_id="workstation",
        from_ts="2026-11-25T10:00:00Z",
        to_ts="2026-11-25T11:00:00Z",
        bucket_seconds=60,
    )
    an_dict = an.model_dump(mode="json", by_alias=True)
    validate_contract_invariants(an_dict, "workload-analytics")
    assert an.capabilities["per_workload_cpu_history"].status == "UNSUPPORTED"
    assert an.capabilities["research_group_attribution"].status == "UNSUPPORTED"
    gpu_util = next(sr for sr in an.series if sr.metric == "gpu_utilization_percent")
    assert any(pt.quality == "NO_DATA" and pt.value is None for pt in gpu_util.points)
    vram_util = next(sr for sr in an.series if sr.metric == "gpu_vram_utilization_percent")
    assert any(pt.quality == "FRESH" and pt.value == 37.5 for pt in vram_util.points)
    assert any(pt.quality == "NO_DATA" and pt.value is None for pt in vram_util.points)

    # Paginated history with keyset cursor
    h1 = svc.get_history(from_ts="2026-11-25T10:00:00Z", to_ts="2026-11-25T11:00:00Z", limit=1)
    validate_contract_invariants(h1.model_dump(mode="json", by_alias=True), "workload-history")
    assert len(h1.items) == 1
    assert h1.next_cursor is not None
    h2 = svc.get_history(from_ts="2026-11-25T10:00:00Z", to_ts="2026-11-25T11:00:00Z", limit=1, cursor=h1.next_cursor)
    validate_contract_invariants(h2.model_dump(mode="json", by_alias=True), "workload-history")
    assert len(h2.items) == 1
    assert h1.items[0].id != h2.items[0].id


def test_g5_process_action_security_idempotency_and_partial(tmp_path: Path) -> None:
    core = _CoreStub(tmp_path)
    fake = FakeProcessBackend()
    fake.procs[3001] = LiveProcIdentity(
        pid=3001,
        ppid=1,
        uid=1011,
        username="vinh_test",
        name="python3",
        cmdline="python3 train.py",
        start_ticks=777,
        boot_id="boot-abc",
        container_id="host",
    )
    foreign_child = LiveProcIdentity(
        pid=3002,
        ppid=3001,
        uid=1012,
        username="nhannv",
        name="python3",
        cmdline="python3 other.py",
        start_ticks=778,
        boot_id="boot-abc",
        container_id="host",
    )
    fake.procs[3002] = foreign_child
    fake.children[3001] = [foreign_child]

    svc = WorkloadIntelligenceService(core, action_backend=fake)
    ref = "workstation:host:3001:777"

    # S01: Non-admin denied
    with pytest.raises(PermissionError):
        svc.execute_v31_process_action(
            ref=ref,
            req=V31ProcessActionRequest(action="SIGTERM", reason="stop test", operation_key="op-key-11111111"),
            actor="nhannv",
            is_admin=False,
            allow_when_flag_off=True,
        )

    # Boot ID mismatch rejected
    with pytest.raises(ValueError, match="BOOT_IDENTITY_MISMATCH"):
        svc.execute_v31_process_action(
            ref=ref,
            req=V31ProcessActionRequest(
                action="SIGTERM",
                reason="stop test",
                operation_key="op-key-22222222",
                expected_boot_id="wrong-boot",
                expected_start_ticks=777,
            ),
            actor="admin",
            is_admin=True,
            allow_when_flag_off=True,
        )

    # S05: pidfd ENOSYS fails closed with zero signal fallback
    fake.pidfd_enosys = True
    with pytest.raises(RuntimeError, match="PIDFD_UNSUPPORTED_FAIL_CLOSED"):
        svc.execute_v31_process_action(
            ref=ref,
            req=V31ProcessActionRequest(
                action="SIGTERM",
                reason="stop test",
                operation_key="op-key-33333333",
                expected_boot_id="boot-abc",
                expected_start_ticks=777,
            ),
            actor="admin",
            is_admin=True,
            allow_when_flag_off=True,
        )
    assert fake.signal_calls == []

    # Valid SIGTERM with foreign descendant skipped -> PARTIAL_FAILURE (never green VERIFIED)
    fake.pidfd_enosys = False
    res = svc.execute_v31_process_action(
        ref=ref,
        req=V31ProcessActionRequest(
            action="SIGTERM",
            reason="stop tree safely",
            operation_key="op-key-44444444",
            expected_boot_id="boot-abc",
            expected_start_ticks=777,
        ),
        actor="admin",
        is_admin=True,
        allow_when_flag_off=True,
    )
    validate_contract_invariants(res.model_dump(mode="json"), "process-action-result")
    assert res.status == "PARTIAL_FAILURE"
    assert res.verification == "PARTIAL_FAILURE"
    assert len(res.skipped_descendants) == 1
    assert 3002 in fake.procs  # Foreign descendant was NOT killed

    # S06: Same operation_key + same payload returns cached result without resending signal
    res_replay = svc.execute_v31_process_action(
        ref=ref,
        req=V31ProcessActionRequest(
            action="SIGTERM",
            reason="stop tree safely",
            operation_key="op-key-44444444",
            expected_boot_id="boot-abc",
            expected_start_ticks=777,
        ),
        actor="admin",
        is_admin=True,
        allow_when_flag_off=True,
    )
    assert res_replay.action_id == res.action_id
    assert len(fake.signal_calls) == 1

    # S06: Same operation_key + different payload -> conflict
    with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
        svc.execute_v31_process_action(
            ref=ref,
            req=V31ProcessActionRequest(
                action="SIGTERM",
                reason="different reason payload",
                operation_key="op-key-44444444",
                expected_boot_id="boot-abc",
                expected_start_ticks=777,
            ),
            actor="admin",
            is_admin=True,
            allow_when_flag_off=True,
        )


def test_http_endpoints_rbac_and_tree_descendant_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    admin_tok = issue_token("admin_v31", "ADMIN")
    researcher_tok = issue_token("vinh_test", "RESEARCHER")

    # S01: Researcher denied across all new V3.1 routes
    for path in (
        "/api/v1/jobs/snapshot",
        "/api/v1/jobs/snapshot/export.csv",
        "/api/v1/jobs/analytics",
        "/api/v1/jobs/history",
        "/api/v1/jobs/events",
        "/api/v1/jobs/wl-unknown/detail",
    ):
        r = client.get(path, headers={"Authorization": f"Bearer {researcher_tok}"})
        assert r.status_code == 403, f"Expected 403 on {path} for researcher, got {r.status_code}"

    # Admin snapshot returns valid contract-conforming envelope
    r_snap = client.get("/api/v1/jobs/snapshot", headers={"Authorization": f"Bearer {admin_tok}"})
    assert r_snap.status_code == 200
    validate_contract_invariants(r_snap.json(), "workload-snapshot")

    # Validation error on invalid range -> 422
    r_bad = client.get(
        "/api/v1/jobs/analytics?from=2026-11-25T10:00:00Z&to=2026-11-24T10:00:00Z",
        headers={"Authorization": f"Bearer {admin_tok}"},
    )
    assert r_bad.status_code == 422

    # S02: Researcher process tree strips foreign-owned and system descendants
    fake_tree = {
        "ref": f"{settings.LOCAL_HOST_ID}:host:900:1",
        "username": "vinh_test",
        "root": {"pid": 900, "username": "vinh_test", "name": "python3"},
        "children": [
            {"pid": 901, "username": "vinh_test", "name": "pt_worker", "is_protected": False},
            {"pid": 902, "username": "nhannv", "name": "other_job", "is_protected": False},
            {"pid": 903, "username": "vinh_test", "name": "systemd", "classification": "SYSTEM_PROCESS", "is_protected": True},
        ],
    }
    monkeypatch.setattr(service.process_monitor, "get_process_tree", lambda _ref: fake_tree)
    r_tree = client.get(
        f"/api/v1/processes/{settings.LOCAL_HOST_ID}:host:900:1/tree",
        headers={"Authorization": f"Bearer {researcher_tok}"},
    )
    assert r_tree.status_code == 200
    child_pids = [c["pid"] for c in r_tree.json()["children"]]
    assert child_pids == [901]

    # S03/W01: V3.1 action blocked when flag is off
    monkeypatch.setenv("LABOPS_WORKLOAD_V31_ACTIONS_ENABLED", "false")
    r_act = client.post(
        f"/api/v1/processes/{settings.LOCAL_HOST_ID}:host:900:1/v31-action",
        json={"action": "SIGTERM", "reason": "flag disabled test", "operation_key": "op-flag-off-1"},
        headers={"Authorization": f"Bearer {admin_tok}"},
    )
    assert r_act.status_code == 403
