"""Phase 3 tests: Persistent tasks, WatchSpec, stale sample break, revocation & Watchdog."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ENV", "development")

from app.db.session import DatabaseManager  # noqa: E402
from app.services.assistant_v4.config import TasksV4Config, WatchdogV4Config  # noqa: E402
from app.services.assistant_v4.event_store import EventEnvelope, EventStoreService  # noqa: E402
from app.services.assistant_v4.inbox_outbox import OutboxV4Service  # noqa: E402
from app.services.assistant_v4.task_scheduler import (  # noqa: E402
    PersistentScheduler,
    TaskService,
    WatchSpec,
    WatchdogAdapter,
)
from app.services.zalo_assistant.identity_rbac import ZaloIdentityRBACService  # noqa: E402


def test_t01_watch_spec_validation_hold_window_and_stale_sample_reset(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    tasks = TaskService(db)
    sched = PersistentScheduler(db)

    # Reject invalid / out-of-bounds / NaN watch specs
    with pytest.raises(Exception):
        WatchSpec(target_id="ws", metric="gpu_temperature_c", comparator="lt", threshold=80.0, sustained_seconds=10)  # < 60s
    with pytest.raises(Exception):
        WatchSpec(target_id="ws", metric="storage_percent", comparator="gte", threshold=120.0, sustained_seconds=60)
    # Non-admin cannot watch another user's private workspace
    with pytest.raises(PermissionError):
        tasks.create_watch(
            owner_id="sv_a",
            owner_role="RESEARCHER",
            spec=WatchSpec(target_id="ws-sv_b", metric="cpu_percent", comparator="gt", threshold=90.0, sustained_seconds=60),
        )

    spec = WatchSpec(target_id="workstation", metric="gpu_temperature_c", comparator="lt", threshold=80.0, sustained_seconds=300, once=True)
    w = tasks.create_watch(owner_id="admin", owner_role="ADMIN", spec=spec)
    t0 = datetime.now(timezone.utc)

    fresh_below = {
        "hosts": [{"host_id": "workstation", "status": "ONLINE", "freshness_status": "ONLINE"}],
        "gpus": [{"gpu_key": "workstation:gpu-0", "host_id": "workstation", "host_online": True, "temp_celsius": 75.0}],
    }
    stale_below = {
        "hosts": [{"host_id": "workstation", "status": "ONLINE", "freshness_status": "STALE"}],
        "gpus": [{"gpu_key": "workstation:gpu-0", "host_id": "workstation", "host_online": True, "temp_celsius": 50.0}],
    }

    # Starts holding at t0
    r1 = sched.tick(snapshot=fresh_below, now_override=t0)
    assert r1["watches_triggered"] == 0

    # Stale sample at t0 + 200s MUST break the hold window!
    sched.tick(snapshot=stale_below, now_override=t0 + timedelta(seconds=200))
    listed = tasks.list_tasks(owner_id="admin", is_admin=True)
    assert listed[0]["watch_rule"]["condition_since"] is None

    # Even if fresh resumes at t0 + 350s, hold starts fresh (0s held -> NOT triggered yet!)
    r2 = sched.tick(snapshot=fresh_below, now_override=t0 + timedelta(seconds=350))
    assert r2["watches_triggered"] == 0

    # At t0 + 660s (310s sustained continuously with fresh ONLINE samples) -> fires ONCE!
    r3 = sched.tick(snapshot=fresh_below, now_override=t0 + timedelta(seconds=660))
    assert r3["watches_triggered"] == 1
    out = OutboxV4Service(db).list_outbox()
    assert len(out) == 1
    assert out[0]["task_id"] == w["task_id"]


def test_t02_restart_persistence_missed_runs_and_cancellation(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    t_svc = TaskService(db, cfg=TasksV4Config(catch_up_max_runs=2))
    t0 = datetime.now(timezone.utc) - timedelta(hours=5)

    # Create recurring task due 5h ago with cadence 60m & bounded catch-up (max 2)
    t_svc.create_reminder(
        owner_id="admin",
        owner_role="ADMIN",
        title="Check GPU queue",
        run_at=t0,
        cadence_minutes=60,
        missed_run_policy="catch_up_bounded",
    )

    # Simulate worker restart (new PersistentScheduler instance backed by same DB)
    sched_restarted = PersistentScheduler(db, cfg=TasksV4Config(catch_up_max_runs=2))
    res = sched_restarted.tick(snapshot={}, now_override=datetime.now(timezone.utc))
    assert res["fired"] == 2  # Capped at catch_up_max_runs=2

    # Cancel task with skip/coalesce suppresses unsent notifications
    rem2 = t_svc.create_reminder(owner_id="admin", owner_role="ADMIN", title="Cancel me", run_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    sched_restarted.tick(snapshot={})
    t_svc.cancel_task(rem2["task_id"], actor_id="admin", actor_role="ADMIN")
    unsent_for_rem2 = [r for r in OutboxV4Service(db).list_outbox() if r["status"] == "SUPPRESSED"]
    assert len(unsent_for_rem2) == 1


def test_t03_revoked_owner_cancels_tasks_and_stops_delivery(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    rbac = ZaloIdentityRBACService(str(tmp_path), db_manager=db)
    link = rbac.admin_bind_identity(zalo_user_id="zalo-u1", username="sv_u1", member_id="m1", role="RESEARCHER")

    t_svc = TaskService(db)
    task = t_svc.create_reminder(owner_id="sv_u1", owner_role="RESEARCHER", title="My reminder", run_at=datetime.now(timezone.utc) - timedelta(seconds=10))

    # Revoke user link + revoke owner tasks
    rbac.revoke_link(link["id"])
    sched = PersistentScheduler(db)
    res = sched.tick(snapshot={})
    assert res["fired"] == 0
    assert res["revoked"] == 1
    assert t_svc.list_tasks(owner_id="sv_u1", is_admin=True)[0]["status"] == "CANCELLED"


def test_h01_h02_watchdog_progress_detection_and_unconfigured_degraded(tmp_path: Path, monkeypatch):
    db = DatabaseManager(str(tmp_path))
    monkeypatch.delenv("ASSISTANT_V4_WATCHDOG_PING_URL", raising=False)

    # Disabled / missing secret URL -> NOT_CONFIGURED_DEGRADED, never calls HTTP
    pings = []
    wd_unconf = WatchdogAdapter(db, cfg=WatchdogV4Config(enabled=False), http_ping_fn=lambda url: pings.append(url))
    res_unconf = wd_unconf.maybe_ping()
    assert res_unconf["configured"] is False
    assert res_unconf["coverage_status"] == "NOT_CONFIGURED_DEGRADED"
    assert res_unconf["pinged"] is False
    assert pings == []

    # Enabled with URL: simulate stalled outbox item older than grace_seconds=180
    monkeypatch.setenv("ASSISTANT_V4_WATCHDOG_PING_URL", "https://hc-ping.com/secret-uuid-redacted")
    wd = WatchdogAdapter(db, cfg=WatchdogV4Config(enabled=True, grace_seconds=60), http_ping_fn=lambda url: pings.append(url))

    # Record fresh tick & clean DB -> pings successfully!
    PersistentScheduler(db).tick(snapshot={})
    ok_res = wd.maybe_ping()
    assert ok_res["healthy"] is True
    assert ok_res["pinged"] is True
    assert len(pings) == 1
    # Never expose secret ping URL in output
    assert "secret-uuid-redacted" not in str(ok_res)

    # Insert old unprocessed event -> watchdog detects stalled progress even though process is alive!
    old_t = datetime.now(timezone.utc) - timedelta(seconds=300)
    EventStoreService(db).emit(
        EventEnvelope(source="labops-core", source_event_id="stalled:1", event_type="node.unreachable", resource_id="track", occurred_at=old_t, observed_at=old_t)
    )
    stalled_res = wd.maybe_ping()
    assert stalled_res["healthy"] is False
    assert stalled_res["checks"]["event_consumer_progress"]["ok"] is False
    assert stalled_res["pinged"] is False
    assert len(pings) == 1  # No extra ping on stalled worker!
