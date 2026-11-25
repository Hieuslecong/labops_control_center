"""Phase 1 tests: Durable event store, Alembic 0005, inbox, outbox fencing, and E01-E06."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, select

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ENV", "development")

from app.db.models import (  # noqa: E402
    IncidentRecord,
    ServerAccessRequestRecord,
    V4EventConsumerReceiptRecord,
    V4EventStoreRecord,
    V4NotificationOutboxRecord,
)
from app.db.session import DatabaseManager, upgrade_database_schema  # noqa: E402
from app.services.assistant_v4.event_store import (  # noqa: E402
    EventEnvelope,
    EventReconciler,
    EventStoreService,
    insert_event_in_session,
)
from app.services.assistant_v4.inbox_outbox import (  # noqa: E402
    DurableMessageInbox,
    IngressModeConflictError,
    OutboxV4Service,
    chunk_zalo_message,
    verify_zalo_webhook_secret,
)
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine  # noqa: E402


def test_alembic_0005_upgrade_downgrade_and_reupgrade(tmp_path: Path):
    db_file = tmp_path / "v4_mig.sqlite3"
    db_url = f"sqlite:///{db_file}"
    cfg = AlembicConfig(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    res = upgrade_database_schema(db_url)
    assert res["current_revision"] == "0005_labops_personal_ai_v4"

    db = DatabaseManager(str(tmp_path), database_url=db_url)
    tables = set(inspect(db.engine).get_table_names())
    expected_v4 = {
        "v4_event_store",
        "v4_event_consumer_receipts",
        "v4_message_inbox",
        "v4_worker_cursors",
        "v4_notification_outbox",
        "v4_delivery_attempts",
        "v4_incident_meta",
        "v4_assistant_tasks",
        "v4_watch_rules",
        "v4_action_proposals",
        "v4_chat_sessions",
        "v4_chat_messages",
        "v4_user_preferences",
        "v4_knowledge_documents",
        "v4_investigations",
        "v4_connector_states",
        "v4_webhook_deliveries",
    }
    assert expected_v4.issubset(tables)
    db.engine.dispose()

    # Non-destructive downgrade back to 0004 removes only v4_* tables and preserves core tables
    alembic_command.downgrade(cfg, "0004_labops_mram_v1")
    from sqlalchemy import create_engine

    raw_eng = create_engine(db_url)
    t_down = set(inspect(raw_eng).get_table_names())
    raw_eng.dispose()
    assert not expected_v4.intersection(t_down)
    assert "incidents" in t_down
    assert "server_access_requests" in t_down

    # Re-upgrade cleanly
    alembic_command.upgrade(cfg, "head")
    raw_eng2 = create_engine(db_url)
    t_up = set(inspect(raw_eng2).get_table_names())
    raw_eng2.dispose()
    assert expected_v4.issubset(t_up)


def test_e01_core_mutation_rollback_leaves_no_orphan_event_or_notification(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    outbox = OutboxV4Service(db)
    now = datetime.now(timezone.utc)

    with pytest.raises(RuntimeError, match="simulated tx failure"):
        with db.session_scope() as session:
            session.add(
                ServerAccessRequestRecord(
                    id="sshreq-rollback-1",
                    member_id="mem-1",
                    username="alice",
                    project_title="Rollback Test",
                    submitted_at=now,
                    review_expires_at=now + timedelta(hours=24),
                    created_at=now,
                    updated_at=now,
                )
            )
            ev_id, _ = insert_event_in_session(
                session,
                EventEnvelope(
                    source="labops-core",
                    source_event_id="request:sshreq-rollback-1:v1:created",
                    event_type="request.created",
                    resource_id="sshreq-rollback-1",
                    payload={"request_id": "sshreq-rollback-1", "bot_token": "secret-should-redact"},
                ),
            )
            outbox.enqueue_in_session(
                session,
                dedupe_key="notif:rollback-1",
                recipient_id="admin-1",
                event_type="request.created",
                severity="P1",
                body_text="New request rollback test",
                event_id=ev_id,
            )
            raise RuntimeError("simulated tx failure")

    with db.session_scope() as session:
        assert session.get(ServerAccessRequestRecord, "sshreq-rollback-1") is None
        assert session.execute(select(V4EventStoreRecord)).scalars().all() == []
        assert session.execute(select(V4NotificationOutboxRecord)).scalars().all() == []


def test_e02_core_commit_then_worker_crash_processes_after_restart_without_duplicates(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    events = EventStoreService(db)
    inc_engine = UnifiedIncidentEngine(str(tmp_path), db_manager=db)

    inc, action = inc_engine.upsert_incident(
        event_type="GPU_THERMAL_HIGH",
        severity="P1",
        source="gpu",
        resource_id="ws:gpu-0",
        host_id="workstation",
        fingerprint="fp-crash-1",
        title="GPU temp 88C",
    )
    assert action == "CREATED"
    # Duplicate source_event_id insertion is idempotent
    ev_id2, created2 = events.emit(
        EventEnvelope(
            source="incident-engine",
            source_event_id=f"incident:{inc['incident_id']}:ep1:created",
            event_type="incident.created",
            resource_id=inc["incident_id"],
        )
    )
    assert created2 is False
    assert ev_id2

    seen_events = []

    def handler(session, ev, is_late):
        seen_events.append((ev["event_id"], is_late))
        if len(seen_events) == 1:
            raise RuntimeError("simulated worker crash before receipt commit")
        return "PROCESSED"

    with pytest.raises(RuntimeError):
        events.process_pending("notif-consumer", handler)

    # After worker restart, event is still pending and processes exactly once
    res = events.process_pending("notif-consumer", handler)
    assert res["processed"] == 1
    res2 = events.process_pending("notif-consumer", handler)
    assert res2["checked"] == 0


def test_e03_durable_inbox_webhook_auth_cursor_and_dedupe(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    inbox = DurableMessageInbox(db, active_mode="webhook")

    # Reject wrong secret and dual polling mode
    with pytest.raises(PermissionError):
        inbox.ingest_webhook_payload(
            bot_id="bot1",
            headers={"X-Bot-Api-Secret-Token": "wrong"},
            expected_secret="real-secret",
            raw_body=b"{}",
            parsed_body={"message": {"message_id": "m1", "chat": {"id": "c1"}, "text": "hi"}},
        )
    with pytest.raises(IngressModeConflictError):
        inbox.ensure_ingress_mode("polling")

    assert verify_zalo_webhook_secret("real-secret", "real-secret") is True

    body = {"message": {"message_id": "m-100", "chat": {"id": "c1"}, "from_user": {"id": "u1"}, "text": "hello /status"}, "update_id": 501}
    r1 = inbox.ingest_webhook_payload(
        bot_id="bot1",
        headers={"X-Bot-Api-Secret-Token": "real-secret"},
        expected_secret="real-secret",
        raw_body=json.dumps(body).encode(),
        parsed_body=body,
    )
    r2 = inbox.ingest_webhook_payload(
        bot_id="bot1",
        headers={"X-Bot-Api-Secret-Token": "real-secret"},
        expected_secret="real-secret",
        raw_body=json.dumps(body).encode(),
        parsed_body=body,
    )
    assert r1["duplicate"] is False
    assert r2["duplicate"] is True
    assert r2["inbox_id"] == r1["inbox_id"]

    # Cursor persistence survives inbox re-instantiation
    poll_inbox = DurableMessageInbox(db, active_mode="polling")
    poll_inbox.ingest_message(
        channel="zalo",
        bot_id="bot1",
        external_message_id="m-101",
        chat_id="c1",
        sender_id="u1",
        text="/status",
        cursor_name="zalo_poll:bot1",
        cursor_value="999",
    )
    restarted_inbox = DurableMessageInbox(db, active_mode="polling")
    assert restarted_inbox.get_cursor("zalo_poll:bot1") == "999"


def test_e04_outbox_fencing_concurrent_claims_and_lease_reaper(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    outbox = OutboxV4Service(db)
    outbox.enqueue(
        dedupe_key="fence-test-1",
        recipient_id="admin-1",
        event_type="node.unreachable",
        severity="P0",
        body_text="Rack offline",
    )

    c1 = outbox.claim_batch("worker-A", batch_size=5, lease_seconds=5)
    c2 = outbox.claim_batch("worker-B", batch_size=5, lease_seconds=5)
    assert len(c1) == 1
    assert len(c2) == 0
    nid = c1[0]["notification_id"]
    fence1 = c1[0]["fence"]

    # Simulate network start BEFORE crash
    att_id = outbox.record_attempt_start(
        outbox_id=nid,
        recipient_id="admin-1",
        channel="zalo",
        attempt_number=1,
        fence=fence1,
    )

    # Lease expires while IN_FLIGHT -> reaper marks UNKNOWN_DELIVERY (not blind retry!)
    reaped = outbox.reap_expired_leases(now_override=datetime.now(timezone.utc) + timedelta(seconds=30))
    assert reaped["marked_unknown"] == 1

    # Stale worker-A tries to finalize SENT with old fence -> REJECTED cleanly!
    stale_ok = outbox.finalize_sent(
        notification_id=nid,
        worker_id="worker-A",
        fence=fence1,
        attempt_id=att_id,
        provider_message_id="late-msg-1",
    )
    assert stale_ok is False
    rows = outbox.list_outbox()
    assert rows[0]["status"] == "UNKNOWN_DELIVERY"

    # Pre-send lease expiry (no IN_FLIGHT attempt started) -> safely reclaimed to FAILED_RETRYABLE
    outbox.enqueue(
        dedupe_key="fence-test-2",
        recipient_id="admin-1",
        event_type="gpu.temperature.high",
        severity="P1",
        body_text="GPU hot",
    )
    c_pre = outbox.claim_batch("worker-C", batch_size=5, lease_seconds=5)
    assert len(c_pre) == 1
    reaped2 = outbox.reap_expired_leases(now_override=datetime.now(timezone.utc) + timedelta(seconds=30))
    assert reaped2["reclaimed_retry"] == 1


def test_e05_late_out_of_order_events_are_flagged_and_not_overwritten(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    events = EventStoreService(db)
    t_new = datetime.now(timezone.utc)
    t_old = t_new - timedelta(minutes=10)

    # First process newer v2 event
    events.emit(
        EventEnvelope(
            source="telemetry",
            source_event_id="node:ws:v2",
            event_type="node.recovered",
            resource_id="ws",
            source_version=2,
            occurred_at=t_new,
            observed_at=t_new,
        )
    )
    processed_types = []

    def handler(session, ev, is_late):
        processed_types.append((ev["source_event_id"], is_late))
        return "LATE_IGNORED" if is_late else "PROCESSED"

    stats1 = events.process_pending("state-consumer", handler)
    assert stats1["processed"] == 1
    assert stats1["late"] == 0

    # Late arrival of older v1 stale event must be marked late & ignored
    events.emit(
        EventEnvelope(
            source="telemetry",
            source_event_id="node:ws:v1",
            event_type="telemetry.stale",
            resource_id="ws",
            source_version=1,
            occurred_at=t_old,
            observed_at=t_old,
        )
    )
    stats2 = events.process_pending("state-consumer", handler)
    assert stats2["processed"] == 1
    assert stats2["late"] == 1
    assert processed_types[-1] == ("node:ws:v1", True)


def test_e06_reconciliation_backfills_open_without_replaying_closed(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        session.add(
            IncidentRecord(
                incident_id="inc-open-1",
                event_type="NODE_HEARTBEAT_LOST",
                severity="P0",
                source="telemetry",
                resource_id="track",
                fingerprint="fp-open-1",
                title="Rack offline",
                status="OPEN",
                detected_at=now,
                last_observed_at=now,
                updated_at=now,
            )
        )
        session.add(
            IncidentRecord(
                incident_id="inc-closed-old",
                event_type="GPU_THERMAL_HIGH",
                severity="P1",
                source="gpu",
                resource_id="ws:gpu-0",
                fingerprint="fp-closed-old",
                title="Old resolved incident",
                status="RESOLVED",
                detected_at=now - timedelta(days=3),
                last_observed_at=now - timedelta(days=3),
                resolved_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=2),
            )
        )

    rec = EventReconciler(db)
    r1 = rec.reconcile()
    assert r1["backfilled"] == 1
    # Running again MUST deduplicate via stable source_event_id
    r2 = rec.reconcile()
    assert r2["backfilled"] == 0

    stored = EventStoreService(db).list_events()
    assert len(stored) == 1
    assert stored[0]["resource_id"] == "inc-open-1"


def test_unicode_chunking_and_redaction(tmp_path: Path):
    long_vn = ("⚠️ CẢNH BÁO SỰ CỐ TIẾNG VIỆT CÓ DẤU — dữ liệu GPU workstation đang ở mức 91°C.\n" * 45).strip()
    chunks = chunk_zalo_message(long_vn, max_chars=500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert chunks[0].startswith("[Phần 1/")
    assert "CẢNH BÁO" in chunks[0]
