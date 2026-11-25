"""Phase 2 tests: Proactive notification policy, correlation, recovery, quota & N01-N07."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ENV", "development")

from app.db.models import (  # noqa: E402
    MemberProfileRecord,
    SupervisorAssignmentRecord,
    V4AssistantTaskRecord,
    V4IncidentMetaRecord,
)
from app.db.session import DatabaseManager  # noqa: E402
from app.services.assistant_v4.config import NotificationsV4Config  # noqa: E402
from app.services.assistant_v4.event_store import EventEnvelope, EventStoreService  # noqa: E402
from app.services.assistant_v4.inbox_outbox import OutboxV4Service  # noqa: E402
from app.services.assistant_v4.notification_policy import (  # noqa: E402
    DigestSchedulerV4,
    EventPolicyEngine,
)
from app.services.zalo_assistant.identity_rbac import ZaloIdentityRBACService  # noqa: E402
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine  # noqa: E402


def _setup_admins_and_users(db: DatabaseManager, tmp_path: Path):
    rbac = ZaloIdentityRBACService(str(tmp_path), db_manager=db)
    rbac.admin_bind_identity(zalo_user_id="zalo-admin-1", username="admin", member_id="mem-admin", role="ADMIN", display_name="Hieu Le")
    rbac.admin_bind_identity(zalo_user_id="zalo-sup-1", username="gv_an", member_id="mem-sup-1", role="RESEARCHER", display_name="Thay An")
    rbac.admin_bind_identity(zalo_user_id="zalo-sv-1", username="sv_hoa", member_id="mem-sv-1", role="RESEARCHER", display_name="Hoa")

    with db.session_scope() as s:
        s.add(MemberProfileRecord(member_id="mem-sv-1", username="sv_hoa", full_name="Nguyen Hoa", member_category="STUDENT", primary_supervisor_id="mem-sup-1"))
        s.add(MemberProfileRecord(member_id="mem-sup-1", username="gv_an", full_name="Thay An", member_category="LECTURER"))
        s.add(SupervisorAssignmentRecord(id="sup-1", student_member_id="mem-sv-1", supervisor_member_id="mem-sup-1", is_active=True))
    return rbac


def test_n01_p0_notifies_independently_of_llm(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    _setup_admins_and_users(db, tmp_path)
    inc_engine = UnifiedIncidentEngine(str(tmp_path), db_manager=db)
    inc_engine.upsert_incident(
        event_type="NODE_HEARTBEAT_LOST",
        severity="P0",
        source="telemetry",
        resource_id="track",
        host_id="track",
        fingerprint="node-down:track",
        title="Rack unreachable (P0)",
    )

    policy = EventPolicyEngine(db)
    res = policy.process_events()
    assert res["processed"] == 1

    outbox = OutboxV4Service(db).list_outbox()
    assert len(outbox) == 1
    assert outbox[0]["severity"] == "P0"
    assert outbox[0]["recipient_id"] == "zalo-admin-1"
    assert "Rack unreachable" in outbox[0]["body_snippet"]


def test_n02_new_request_notifications_sla_tasks_and_approve_cancellation_race(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    _setup_admins_and_users(db, tmp_path)
    ev_store = EventStoreService(db)
    now = datetime.now(timezone.utc)

    ev_store.emit(
        EventEnvelope(
            source="labops-core",
            source_event_id="request:sshreq-99:v1:created",
            event_type="request.created",
            resource_id="sshreq-99",
            actor_id="sv_hoa",
            occurred_at=now,
            observed_at=now,
            payload={
                "request_id": "sshreq-99",
                "username": "sv_hoa",
                "full_name": "Nguyen Hoa",
                "reason_snippet": "Train crack model",
                "supervisor_id": "mem-sup-1",
                "submitted_at": now.isoformat(),
                "review_expires_at": (now + timedelta(hours=24)).isoformat(),
                "review_path": "/dashboard/ssh-requests/sshreq-99/review",
            },
        )
    )

    policy = EventPolicyEngine(db)
    policy.process_events()

    outbox_rows = OutboxV4Service(db).list_outbox()
    recipients = {r["recipient_id"] for r in outbox_rows}
    # Admin + verified supervisor notified; student NOT spammed as reviewer!
    assert "zalo-admin-1" in recipients
    assert "zalo-sup-1" in recipients
    assert "zalo-sv-1" not in recipients
    # No invite/auth secret token in Dashboard URL!
    for r in outbox_rows:
        assert "?invite=" not in r["body_snippet"]

    with db.session_scope() as s:
        sla_tasks = s.execute(select(V4AssistantTaskRecord).where(V4AssistantTaskRecord.task_type == "request_sla")).scalars().all()
        assert len(sla_tasks) == 3  # 12h, 22h, 24h

    # Approve clears SLA reminders and notifies applicant + admin + supervisor
    ev_store.emit(
        EventEnvelope(
            source="labops-core",
            source_event_id="request:sshreq-99:v2:approved",
            event_type="request.approved",
            resource_id="sshreq-99",
            actor_id="admin",
            source_version=2,
            payload={"request_id": "sshreq-99", "username": "sv_hoa", "decision": "APPROVED", "reason": "Approved", "supervisor_id": "mem-sup-1"},
        )
    )
    policy.process_events()

    with db.session_scope() as s:
        sla_tasks = s.execute(select(V4AssistantTaskRecord).where(V4AssistantTaskRecord.task_type == "request_sla")).scalars().all()
        assert all(t.status == "CANCELLED" for t in sla_tasks)

    all_out = OutboxV4Service(db).list_outbox()
    approved_recips = {r["recipient_id"] for r in all_out if r["event_type"] == "request.approved"}
    assert "zalo-sv-1" in approved_recips


def test_n03_node_down_correlates_dependent_symptoms_preserving_unrelated_critical(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    _setup_admins_and_users(db, tmp_path)
    inc_engine = UnifiedIncidentEngine(str(tmp_path), db_manager=db)

    root, _ = inc_engine.upsert_incident(
        event_type="NODE_HEARTBEAT_LOST",
        severity="P0",
        source="telemetry",
        resource_id="track",
        host_id="track",
        fingerprint="hb-track",
        title="Rack offline",
    )
    child, _ = inc_engine.upsert_incident(
        event_type="NODE_HEARTBEAT_STALE",
        severity="P1",
        source="telemetry",
        resource_id="track-stale",
        host_id="track",
        fingerprint="stale-track",
        title="Rack telemetry stale",
    )
    unrelated_sec, _ = inc_engine.upsert_incident(
        event_type="REPO_SECURITY_FINDINGS",
        severity="P0",
        source="repository_guard",
        resource_id="org/repo",
        host_id="track",
        fingerprint="sec-1",
        title="Secret leaked in repo",
    )

    policy = EventPolicyEngine(db)
    policy.process_events()

    with db.session_scope() as s:
        meta = s.get(V4IncidentMetaRecord, child["incident_id"])
        assert meta is not None and meta.parent_incident_id == root["incident_id"]
        sec_meta = s.get(V4IncidentMetaRecord, unrelated_sec["incident_id"])
        assert sec_meta is not None and sec_meta.parent_incident_id is None

    out = OutboxV4Service(db).list_outbox()
    sent_titles = [r["body_snippet"] for r in out]
    assert any("Rack offline" in t for t in sent_titles)
    assert any("Secret leaked" in t for t in sent_titles)
    assert not any("Rack telemetry stale" in t for t in sent_titles)


def test_n04_recovery_rejects_stale_telemetry_and_notifies_on_verified_recovery(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    _setup_admins_and_users(db, tmp_path)
    inc_engine = UnifiedIncidentEngine(str(tmp_path), db_manager=db)
    inc_engine.upsert_incident(
        event_type="GPU_THERMAL_HIGH",
        severity="P1",
        source="gpu",
        resource_id="ws:gpu-0",
        host_id="workstation",
        fingerprint="gpu-hot-ws",
        title="GPU temp 88C",
    )
    policy = EventPolicyEngine(db)
    policy.process_events()

    # Stale telemetry must NOT falsely recover incident!
    stale_snap = {
        "hosts": [{"host_id": "workstation", "status": "ONLINE", "freshness_status": "STALE"}],
        "gpus": [{"gpu_key": "ws:gpu-0", "host_id": "workstation", "host_online": True, "temp_celsius": 50.0}],
    }
    with db.session_scope() as s:
        rec_stale = policy.lifecycle.evaluate_recovery_from_snapshot(s, stale_snap)
        assert rec_stale == []

    # Fresh ONLINE sample below 80C triggers verified recovery notification!
    fresh_snap = {
        "hosts": [{"host_id": "workstation", "status": "ONLINE", "freshness_status": "ONLINE"}],
        "gpus": [{"gpu_key": "ws:gpu-0", "host_id": "workstation", "host_online": True, "temp_celsius": 72.0}],
    }
    with db.session_scope() as s:
        rec_ok = policy.lifecycle.evaluate_recovery_from_snapshot(s, fresh_snap)
        assert len(rec_ok) == 1
    policy.process_events()
    out = OutboxV4Service(db).list_outbox()
    assert any(r["event_type"] == "incident.resolved" for r in out)


def test_n05_quota_soft_shed_reserve_and_provider_cap_fallback(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    _setup_admins_and_users(db, tmp_path)
    cfg = NotificationsV4Config(monthly_budget=100, critical_reserve=20, soft_percent=80, high_percent=90, fallback_channel="dashboard_inbox")
    policy = EventPolicyEngine(db, cfg=cfg)

    with db.session_scope() as s:
        policy.quota.record_usage(s, is_critical=False, units=81)  # 81% -> standard exhausted (>80 std limit) and >=80% soft
        ev = {"event_id": "e1", "event_type": "workspace.expiring", "resource_id": "ws-1", "correlation_id": "c1"}
        policy._enqueue_for_audience(session=s, event=ev, recipients=["zalo-admin-1"], severity="P2", actionable=False, dedupe_base="shed-p2", body_text="P2 expiring")
        ev_p0 = {"event_id": "e2", "event_type": "node.unreachable", "resource_id": "track", "correlation_id": "c2"}
        policy._enqueue_for_audience(session=s, event=ev_p0, recipients=["zalo-admin-1"], severity="P0", actionable=True, dedupe_base="reserve-p0", body_text="P0 critical")

    rows = OutboxV4Service(db).list_outbox()
    channels = {(r["severity"], r["channel"]) for r in rows}
    assert ("P2", "dashboard_inbox") in channels  # shed to fallback
    assert ("P0", "zalo") in channels             # P0 uses reserve!


def test_n06_timeout_marks_unknown_delivery_without_false_success(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    outbox = OutboxV4Service(db)
    outbox.enqueue(dedupe_key="timeout-1", recipient_id="zalo-admin-1", event_type="node.unreachable", severity="P0", body_text="Alert")

    async def timeout_send(rec, body, ch):
        raise TimeoutError("Zalo HTTP 504 timeout")

    stats = asyncio.run(outbox.deliver_claimed_batch("worker-1", timeout_send))
    assert stats["sent"] == 0
    assert stats["unknown"] == 1
    assert outbox.list_outbox()[0]["status"] == "UNKNOWN_DELIVERY"


def test_monday_no_double_send_and_shadow_mode(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    _setup_admins_and_users(db, tmp_path)
    sched = DigestSchedulerV4(db)
    # Monday 2026-11-23 08:15 ICT
    mon_0815_utc = datetime(2026, 11, 23, 8, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")).astimezone(timezone.utc)
    a1 = sched.evaluate_due_digests(now_utc=mon_0815_utc, has_changes=False)
    assert len(a1) == 1 and a1[0]["kind"] == "weekly_monday"
    a2 = sched.evaluate_due_digests(now_utc=mon_0815_utc, has_changes=False)
    assert a2 == []

    # Tuesday 08:05 ICT morning runs once; 17:05 without changes -> empty; 17:05 with changes -> fires
    tue_0805_utc = datetime(2026, 11, 24, 8, 5, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")).astimezone(timezone.utc)
    tue_morning = sched.evaluate_due_digests(now_utc=tue_0805_utc, has_changes=False)
    assert len(tue_morning) == 1 and tue_morning[0]["kind"] == "morning_daily"
    tue_1705_utc = datetime(2026, 11, 24, 17, 5, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")).astimezone(timezone.utc)
    assert sched.evaluate_due_digests(now_utc=tue_1705_utc, has_changes=False) == []
    a3 = sched.evaluate_due_digests(now_utc=tue_1705_utc, has_changes=True)
    assert len(a3) == 1 and a3[0]["kind"] == "evening_summary"

    # Shadow mode produces ZERO real outbox rows
    inc_engine = UnifiedIncidentEngine(str(tmp_path), db_manager=db)
    inc_engine.upsert_incident(event_type="DOCKER_OOM_KILLED", severity="P1", source="docker", resource_id="ws-1", fingerprint="oom-1", title="OOM")
    shadow_policy = EventPolicyEngine(db, shadow_mode=True)
    shadow_policy.process_events()
    assert OutboxV4Service(db).list_outbox() == []
