"""Final Hardening Adversarial & Rollout Readiness Tests for LabOps Personal AI Agent V4."""
from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ENV", "development")

import app.api.v1.endpoints.ai_assistant as ai_ep  # noqa: E402
from app.core.security import issue_token  # noqa: E402
from app.db.models import (  # noqa: E402
    AuditEventRecord,
    IncidentRecord,
    MemberProfileRecord,
    MemberRecord,
    NotificationOutboxRecord,
    ReportRunRecord,
    RequestResourceRecommendationRecord,
    ServerAccessRequestRecord,
    V4ActionProposalRecord,
    V4AssistantTaskRecord,
    V4ChatMessageRecord,
    V4ChatSessionRecord,
    V4DeliveryAttemptRecord,
    V4EventConsumerReceiptRecord,
    V4EventStoreRecord,
    V4IncidentMetaRecord,
    V4KnowledgeDocumentRecord,
    V4MessageInboxRecord,
    V4NotificationOutboxRecord,
    V4UserPreferenceRecord,
    V4WatchRuleRecord,
    V4WebhookDeliveryRecord,
    V4WorkerCursorRecord,
    WorkspaceRecord,
)
from app.db.session import DatabaseManager, resolve_sqlite_abs_path  # noqa: E402
from app.main import app  # noqa: E402
from app.services.assistant_v4.config import (  # noqa: E402
    AssistantV4Config,
    NotificationsV4Config,
    RetentionV4Config,
    WatchdogV4Config,
)
from app.services.assistant_v4.event_store import EventEnvelope, EventStoreService  # noqa: E402
from app.services.assistant_v4.notification_policy import (  # noqa: E402
    SHADOW_METRICS_KEY,
    resolve_notification_owner,
)
from app.services.assistant_v4.retention_service import RetentionMaintenanceService  # noqa: E402
from app.services.assistant_v4.skills_and_connectors import SSRFBlockedError, SafeOutboundGuard  # noqa: E402
from app.services.assistant_v4.task_scheduler import TaskService, WatchSpec  # noqa: E402
from app.services.zalo_assistant.identity_rbac import ZaloPrincipal  # noqa: E402
from app.services.zalo_assistant.service import ZaloAssistantService  # noqa: E402
from app.services.zalo_assistant.worker import ZaloPollingWorker  # noqa: E402


class _RecordingBot:
    def __init__(self, fail_for=None, timeout_for=None):
        self.sent = []
        self.fail_for = set(fail_for or [])
        self.timeout_for = set(timeout_for or [])
        self._seq = 0

    async def send_message(self, *, chat_id: str, text: str):
        if chat_id in self.timeout_for:
            raise TimeoutError("Zalo transport timeout")
        if chat_id in self.fail_for:
            raise RuntimeError("Zalo transport 502 error")
        self._seq += 1
        self.sent.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(message_id=f"zalo-rec-{self._seq}")


def _suppress_wallclock_digests(svc: ZaloAssistantService) -> None:
    """Prevent real wall-clock morning/evening/weekly digests from firing during non-digest event tests."""
    now_utc = datetime.now(timezone.utc)
    local_ict = now_utc.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    day_key = local_ict.strftime("%Y-%m-%d")
    wk_period = local_ict.strftime("%Y-W%W")
    with svc.db.session_scope() as s:
        if s.get(ReportRunRecord, f"rep-v2-done-{day_key}") is None:
            s.add(
                ReportRunRecord(
                    id=f"rep-v2-done-{day_key}",
                    report_type="DAILY_EXECUTIVE",
                    period_key=day_key,
                    window_start_utc=now_utc - timedelta(hours=24),
                    window_end_utc=now_utc,
                    status="SENT",
                    title="Wall-clock digest already covered in test setup",
                    markdown_content="Covered",
                )
            )
        if s.get(V4WorkerCursorRecord, f"digest:weekly:{wk_period}") is None:
            s.add(V4WorkerCursorRecord(cursor_name=f"digest:weekly:{wk_period}", cursor_value="COVERED_IN_TEST", metadata_json={"type": "test_guard"}))
        if s.get(V4WorkerCursorRecord, f"digest:evening:{day_key}") is None:
            s.add(V4WorkerCursorRecord(cursor_name=f"digest:evening:{day_key}", cursor_value="COVERED_IN_TEST", metadata_json={"type": "test_guard"}))


def test_shadow_mode_zero_external_sends_zero_quota_and_no_cutover_backlog(tmp_path: Path):
    svc = ZaloAssistantService(str(tmp_path))
    svc.identity_rbac.admin_bind_identity(zalo_user_id="z-adm", username="admin", member_id="m-adm", role="ADMIN")
    svc.identity_rbac.record_observed_private_chat("z-adm", "z-adm", display_name="Admin")
    svc.bot_engine.authorized_users = ["z-adm"]

    # Misconfigured flags (both shadow_mode=True AND notification_v2=True): MUST still remain strict shadow!
    svc.v4.config = AssistantV4Config(
        enabled=True,
        event_engine=True,
        notification_v2=True,
        shadow_mode=True,
        task_manager=True,
    )
    assert resolve_notification_owner(svc.v4.config) == "V2_LEGACY_SHADOW"

    # Producer 1: incident upserted during shadow mode
    inc, _ = svc.incident_engine.upsert_incident(
        event_type="GPU_THERMAL_HIGH",
        severity="P1",
        source="gpu",
        resource_id="ws:gpu-0",
        host_id="ws",
        fingerprint="shadow-gpu-hot",
        title="Shadow GPU thermal",
    )
    # Producer 2: scheduled due task during shadow mode
    svc.v4.tasks.create_reminder(
        owner_id="z-adm",
        owner_role="ADMIN",
        title="Shadow due task",
        run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    bot = _RecordingBot()
    worker = ZaloPollingWorker(service_instance=svc)
    asyncio.run(worker._run_v4_worker_step(svc.v4, bot=bot))

    # Zero external sends & zero V4 outbox entries & zero Zalo quota consumed!
    assert bot.sent == []
    assert svc.v4.outbox.list_outbox() == []
    with svc.db.session_scope() as s:
        usage = svc.v4.policy.quota.get_usage(s)
        assert usage["total_sent"] == 0
        rcp = s.execute(select(V4EventConsumerReceiptRecord)).scalars().all()
        assert len(rcp) == 1
        assert rcp[0].result_status == "SHADOW_DRY_RUN"
        sh_cur = s.get(V4WorkerCursorRecord, SHADOW_METRICS_KEY)
        assert sh_cur is not None
        items = sh_cur.metadata_json.get("would_send") or []
        assert len(items) >= 1
        assert items[0]["reason"] == "WOULD_SEND_ZALO"
        assert items[0]["evaluated_at"]
        imeta = s.get(V4IncidentMetaRecord, inc["incident_id"])
        assert imeta is not None and imeta.shadow_only is True
        # Force reminder due in shadow mode and verify shadow reminder also does NOT enqueue
        imeta.next_reminder_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    asyncio.run(worker._run_v4_worker_step(svc.v4, bot=bot))
    assert bot.sent == []
    assert svc.v4.outbox.list_outbox() == []

    # Cutover to live V4_ACTIVE AFTER shadow mode: NO backlog flood and NO immediate shadow-reminder storm!
    _suppress_wallclock_digests(svc)
    svc.v4.config.shadow_mode = False
    svc.v4.config.notification_v2 = True
    asyncio.run(worker._run_v4_worker_step(svc.v4, bot=bot))
    assert bot.sent == []
    assert svc.v4.outbox.list_outbox() == []
    with svc.db.session_scope() as s:
        imeta_after = s.get(V4IncidentMetaRecord, inc["incident_id"])
        assert imeta_after is not None and imeta_after.shadow_only is False


def test_notification_cutover_v2_to_v4_drains_v2_pending_skips_precutover_and_dedupes_repo_alerts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    svc = ZaloAssistantService(str(tmp_path))
    svc.identity_rbac.admin_bind_identity(zalo_user_id="z-adm", username="admin", member_id="m-adm", role="ADMIN")
    svc.identity_rbac.record_observed_private_chat("z-adm", "z-adm", display_name="Admin")
    svc.bot_engine.authorized_users = ["z-adm"]

    t_old = datetime.now(timezone.utc) - timedelta(hours=2)

    # 1. Seed legacy V2 outbox: one PENDING, one DELIVERY_UNKNOWN, and one SENT
    svc.notification_dispatcher.enqueue(
        idempotency_key="v2-pending-1",
        recipient_id="z-adm",
        event_type="INFRA_INCIDENT",
        severity="P1",
        message_text="Legacy V2 pending alert",
    )
    unk = svc.notification_dispatcher.enqueue(
        idempotency_key="v2-unk-1",
        recipient_id="z-adm",
        event_type="INFRA_INCIDENT",
        severity="P0",
        is_critical=True,
        message_text="Legacy V2 unknown delivery",
    )
    with svc.db.session_scope() as s:
        u_row = s.get(NotificationOutboxRecord, unk["id"])
        assert u_row is not None
        u_row.status = "DELIVERY_UNKNOWN"
        # Seed pre-cutover open request in core DB (reconciled later) + pre-cutover V4 event
        s.add(MemberProfileRecord(member_id="m-hist", username="sv_hist", full_name="Hist Student"))
        s.add(
            ServerAccessRequestRecord(
                id="ssh-hist-1",
                member_id="m-hist",
                username="sv_hist",
                project_title="Old Req",
                registration_reason="Historical request before cutover",
                submitted_at=t_old,
                review_expires_at=t_old + timedelta(hours=24),
                status="PENDING_APPROVAL",
                version=1,
                created_at=t_old,
                updated_at=t_old,
            )
        )

    pre_ev_id, _ = svc.v4.events.emit(
        EventEnvelope(
            source="labops-core",
            source_event_id="precutover:req-0",
            event_type="request.created",
            resource_id="ssh-pre-0",
            occurred_at=t_old,
            observed_at=t_old,
            payload={"request_id": "ssh-pre-0", "username": "sv_hist"},
        )
    )

    # 2. Simulate running in V2_LEGACY first, then executing V2 -> V4 cutover
    svc.v4.config = AssistantV4Config(enabled=True, event_engine=True, notification_v2=False, shadow_mode=False)
    svc.v4.run_worker_cycle(now_override=t_old + timedelta(minutes=30))

    # Cutover to V4_ACTIVE via YAML reload
    cfg_file = tmp_path / "cutover.yaml"
    cfg_file.write_text(
        yaml.safe_dump(
            {
                "assistant_v4": {
                    "enabled": True,
                    "event_engine": True,
                    "notification_v2": True,
                    "shadow_mode": False,
                }
            }
        ),
        encoding="utf-8",
    )
    reload_res = svc.v4.reload_config(config_path=str(cfg_file), actor="ops-admin")
    assert reload_res["owner"] == "V4_ACTIVE"
    assert reload_res["cutover"]["skipped_precutover"] == 2

    # Verify V2 PENDING drains via legacy dispatcher while DELIVERY_UNKNOWN is NOT resent!
    bot = _RecordingBot()
    worker = ZaloPollingWorker(service_instance=svc)
    asyncio.run(worker._flush_notification_outbox(bot=bot))
    assert len(bot.sent) == 1
    assert "Legacy V2 pending alert" in bot.sent[0]["text"]
    with svc.db.session_scope() as s:
        u_after = s.get(NotificationOutboxRecord, unk["id"])
        assert u_after is not None and u_after.status == "DELIVERY_UNKNOWN"
        pre_rcp = s.execute(
            select(V4EventConsumerReceiptRecord).where(V4EventConsumerReceiptRecord.event_id == pre_ev_id)
        ).scalar_one()
        assert pre_rcp.result_status == "SKIPPED_PRE_CUTOVER"

    # Ensure legacy V2 daily report marked SENT today prevents wall-clock V4 morning digest double-send
    _suppress_wallclock_digests(svc)

    # Run V4 worker cycle: reconciler scans historical ssh-hist-1 -> marked SKIPPED_PRE_CUTOVER, NO spam!
    bot.sent.clear()
    asyncio.run(worker._run_v4_worker_step(svc.v4, bot=bot))
    assert bot.sent == []

    # RepoSecurityAlertMonitor under V4_ACTIVE: records incident once for V4, skips V2 direct bot send!
    svc.repo_alert_monitor.fetch_requests_fn = lambda: [
        {
            "id": "repo-sec-1",
            "owner": "org",
            "repo_name": "unsafe-repo",
            "canonical_repo": "https://github.com/org/unsafe-repo",
            "submitted_by": "sv_hist",
            "state": "REVIEW_REQUIRED",
            "review_status": "REVIEW_REQUIRED",
            "commit_sha": "abcdef1234567890",
            "report": {"secrets_count": 1, "vulnerabilities": {"critical": 0, "high": 2}},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    asyncio.run(worker._check_repo_security_alerts(bot=bot, suppress_direct_send=True))
    assert bot.sent == []  # NO direct V2 send!
    asyncio.run(worker._run_v4_worker_step(svc.v4, bot=bot))
    repo_msgs = [m for m in bot.sent if "unsafe-repo" in m["text"]]
    assert len(repo_msgs) == 1  # Sent ONCE via V4 outbox!
    # Running another V4 cycle must NOT duplicate either repo alert or digest
    bot.sent.clear()
    asyncio.run(worker._run_v4_worker_step(svc.v4, bot=bot))
    assert bot.sent == []

    # Verify legacy V2 daily report already SENT on 2026-11-26 prevents V4 morning double-send
    day_ict = datetime(2026, 11, 26, 8, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")).astimezone(timezone.utc)
    with svc.db.session_scope() as s:
        existing_rep = (
            s.execute(
                select(ReportRunRecord).where(
                    ReportRunRecord.report_type == "DAILY_EXECUTIVE",
                    ReportRunRecord.period_key == "2026-11-26",
                )
            )
            .scalars()
            .first()
        )
        if existing_rep is None:
            s.add(
                ReportRunRecord(
                    id="rep-v2-done",
                    report_type="DAILY_EXECUTIVE",
                    period_key="2026-11-26",
                    window_start_utc=day_ict - timedelta(hours=24),
                    window_end_utc=day_ict,
                    status="SENT",
                    title="Legacy V2 morning already sent",
                    markdown_content="Already delivered by V2",
                )
            )
    assert svc.v4.digests.evaluate_due_digests(now_utc=day_ict, has_changes=False) == []

    # Ownership audit trail recorded
    with svc.db.session_scope() as s:
        auds = s.execute(
            select(AuditEventRecord).where(AuditEventRecord.action == "V4_NOTIFICATION_OWNERSHIP_CHANGED")
        ).scalars().all()
        assert any(a.target_id == "V4_ACTIVE" for a in auds)


def test_notification_ownership_rollback_v4_to_v2_preserves_state(tmp_path: Path):
    svc = ZaloAssistantService(str(tmp_path))
    svc.identity_rbac.admin_bind_identity(zalo_user_id="z-adm", username="admin", member_id="m-adm", role="ADMIN")
    svc.bot_engine.authorized_users = ["z-adm"]
    _suppress_wallclock_digests(svc)

    svc.v4.config = AssistantV4Config(enabled=True, event_engine=True, notification_v2=True, shadow_mode=False)
    svc.v4.run_worker_cycle()
    svc.v4.outbox.enqueue(
        dedupe_key="v4-before-rb-1",
        recipient_id="z-adm",
        event_type="node.unreachable",
        severity="P0",
        body_text="Before rollback",
    )
    bot = _RecordingBot()
    worker = ZaloPollingWorker(service_instance=svc)
    asyncio.run(worker._run_v4_worker_step(svc.v4, bot=bot))
    assert len(bot.sent) == 1

    # Rollback ownership from V4_ACTIVE -> V2_LEGACY
    svc.v4.config.notification_v2 = False
    rb_res = svc.v4.policy.sync_notification_ownership("V2_LEGACY", actor="ops-rollback")
    assert rb_res["changed"] is True

    # Enqueue another V4 outbox row — V4 worker step MUST NOT send externally after rollback!
    svc.v4.outbox.enqueue(
        dedupe_key="v4-after-rb-1",
        recipient_id="z-adm",
        event_type="node.unreachable",
        severity="P0",
        body_text="Must stay queued in V4 after rollback",
    )
    bot.sent.clear()
    asyncio.run(worker._run_v4_worker_step(svc.v4, bot=bot))
    assert bot.sent == []

    # Verify V4 historical outbox & delivery attempts are completely preserved
    assert len(svc.v4.outbox.list_outbox()) == 2
    with svc.db.session_scope() as s:
        assert len(s.execute(select(V4DeliveryAttemptRecord)).scalars().all()) == 1

    # Meanwhile V2 resumes enqueueing new incidents into V2 NotificationOutboxRecord
    svc.telemetry_collector._core_service = SimpleNamespace(
        local_host_id="track",
        servers=[{"id": "track", "hostname": "rack", "status": "OFFLINE", "desired_state": "ACTIVE"}],
        gpus=[],
        jobs=[],
        workspace_controller=SimpleNamespace(list_workspaces=lambda: []),
        get_gpu_allocations_by_device=lambda: [],
    )
    asyncio.run(worker._check_infra_telemetry_and_incidents(suppress_v2_enqueue=False))
    with svc.db.session_scope() as s:
        v2_rows = s.execute(select(NotificationOutboxRecord)).scalars().all()
        assert len(v2_rows) >= 1


def _hash_tables_deterministic(db_path: str, table_names: list[str]) -> dict[str, tuple[int, str]]:
    conn = sqlite3.connect(db_path)
    out = {}
    for tbl in table_names:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()]
        h = hashlib.sha256()
        h.update(("|".join(cols)).encode("utf-8"))
        h.update(b"\n")
        cnt = 0
        for row in conn.execute(f'SELECT * FROM "{tbl}" ORDER BY rowid ASC'):
            h.update(repr(row).encode("utf-8"))
            h.update(b"\n")
            cnt += 1
        out[tbl] = (cnt, h.hexdigest())
    conn.close()
    return out


def test_sqlite_migration_0005_against_0003_0004_and_create_all_drift(tmp_path: Path):
    db_file = tmp_path / "drifted.sqlite3"
    db_url = f"sqlite:///{db_file.resolve()}"
    env = {**os.environ, "DATABASE_URL": db_url, "PYTHONPATH": BACKEND_DIR}

    # 1. Start cleanly at Alembic revision 0003
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "0003_labops_ai_sre_agent_v2"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
    )
    # 2. Simulate ORM Base.metadata.create_all() side-effect creating 0004 + v4_* tables while stamped 0003
    dm = DatabaseManager(str(tmp_path), database_url=db_url)
    with dm.session_scope() as s:
        s.add(MemberRecord(id="m-drift-1", full_name="Drift User", username="drift_u", role="RESEARCHER"))
    dm.engine.dispose()

    # Deliberately drop two Alembic-specific V4 indexes to simulate ORM-only schema drift
    raw_c = sqlite3.connect(str(db_file))
    raw_c.execute("DROP INDEX IF EXISTS ix_v4_chat_sess_principal")
    raw_c.execute("DROP INDEX IF EXISTS ix_v4_outbox_recipient")
    ver_before = raw_c.execute("SELECT version_num FROM alembic_version").fetchall()
    legacy_tables = sorted(
        r[0]
        for r in raw_c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        if not r[0].startswith("v4_") and r[0] != "alembic_version"
    )
    raw_c.close()
    assert ver_before == [("0003_labops_ai_sre_agent_v2",)]

    before_hashes = _hash_tables_deterministic(str(db_file), legacy_tables)

    # 3. Explicitly migrate to target 0005_labops_personal_ai_v4 (never arbitrary head)
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "0005_labops_personal_ai_v4"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
    )

    after_c = sqlite3.connect(str(db_file))
    ver_after = after_c.execute("SELECT version_num FROM alembic_version").fetchall()
    integ = after_c.execute("PRAGMA integrity_check").fetchall()
    fk_errs = after_c.execute("PRAGMA foreign_key_check").fetchall()
    sess_idxs = {r[1] for r in after_c.execute("PRAGMA index_list('v4_chat_sessions')").fetchall()}
    out_idxs = {r[1] for r in after_c.execute("PRAGMA index_list('v4_notification_outbox')").fetchall()}
    after_c.close()

    after_hashes = _hash_tables_deterministic(str(db_file), legacy_tables)
    assert ver_after == [("0005_labops_personal_ai_v4",)]
    assert integ == [("ok",)]
    assert fk_errs == []
    assert "ix_v4_chat_sess_principal" in sess_idxs
    assert "ix_v4_outbox_recipient" in out_idxs
    assert before_hashes == after_hashes
    assert resolve_sqlite_abs_path(db_url) == str(db_file.resolve())


def test_retention_maintenance_dry_run_batched_deletion_and_safety_preservations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db = DatabaseManager(str(tmp_path))
    now = datetime.now(timezone.utc)
    old_120d = now - timedelta(days=120)
    recent_3d = now - timedelta(days=3)

    with db.session_scope() as s:
        # Protected legacy & KB/preference/investigation records (MUST NEVER be deleted)
        s.add(MemberRecord(id="m-keep", full_name="Keep", username="keep_u", role="ADMIN"))
        s.add(WorkspaceRecord(id="ws-keep", name="ws-keep", owner="keep_u", host_id="ws", ssh_host_port=2201))
        s.add(IncidentRecord(incident_id="inc-keep", event_type="NODE_HEARTBEAT_LOST", severity="P0", source="telemetry", resource_id="track", fingerprint="fp-keep", title="Keep inc", detected_at=old_120d, last_observed_at=old_120d, updated_at=old_120d))
        s.add(AuditEventRecord(id="aud-keep", timestamp=old_120d.isoformat(), actor="admin", action="LOGIN", target_type="USER", target_id="keep_u", result="SUCCESS"))
        s.add(V4UserPreferenceRecord(principal_id="keep_u", updated_at=old_120d))
        s.add(V4KnowledgeDocumentRecord(doc_id="kb-keep", title="Keep KB", content_markdown="# Keep", content_hash="h1", created_at=old_120d, updated_at=old_120d))

        # Aged terminal vs aged ACTIVE/PENDING/UNKNOWN outbox + attempts
        s.add(V4NotificationOutboxRecord(notification_id="out-old-sent", dedupe_key="k-sent", recipient_id="z1", event_type="incident.created", body_text="ok", status="SENT", created_at=old_120d, sent_at=old_120d, updated_at=old_120d))
        s.add(V4DeliveryAttemptRecord(attempt_id="att-old-ok", outbox_id="out-old-sent", recipient_id="z1", started_at=old_120d, finished_at=old_120d, result_class="PROVIDER_ACCEPTED"))

        s.add(V4NotificationOutboxRecord(notification_id="out-old-pending", dedupe_key="k-pend", recipient_id="z1", event_type="incident.created", body_text="keep pending", status="PENDING", created_at=old_120d, not_before=old_120d, updated_at=old_120d))
        s.add(V4NotificationOutboxRecord(notification_id="out-old-retry", dedupe_key="k-retry", recipient_id="z1", event_type="incident.created", body_text="keep retry", status="FAILED_RETRYABLE", created_at=old_120d, not_before=old_120d, updated_at=old_120d))
        s.add(V4NotificationOutboxRecord(notification_id="out-old-unk", dedupe_key="k-unk", recipient_id="z1", event_type="incident.created", body_text="keep unk", status="UNKNOWN_DELIVERY", created_at=old_120d, not_before=old_120d, updated_at=old_120d))
        s.add(V4DeliveryAttemptRecord(attempt_id="att-keep-unk", outbox_id="out-old-unk", recipient_id="z1", started_at=old_120d, result_class="IN_FLIGHT"))

        # Safety window test: SENT 3 days ago MUST stay inside min_dedupe_safety_hours=168 (7d)!
        s.add(V4NotificationOutboxRecord(notification_id="out-recent-sent", dedupe_key="k-recent", recipient_id="z1", event_type="incident.created", body_text="recent", status="SENT", created_at=recent_3d, sent_at=recent_3d, updated_at=recent_3d))

        # Inbox: aged PROCESSED deleted vs aged RECEIVED preserved
        s.add(V4MessageInboxRecord(id="inb-old-ok", external_message_id="ext-1", chat_id="z1", sender_id="z1", state="PROCESSED", received_at=old_120d, updated_at=old_120d))
        s.add(V4MessageInboxRecord(id="inb-old-recv", external_message_id="ext-2", chat_id="z1", sender_id="z1", state="RECEIVED", received_at=old_120d, updated_at=old_120d))

        # Tasks & watches
        s.add(V4AssistantTaskRecord(task_id="tsk-old-done", owner_id="keep_u", task_type="one_time_reminder", title="done", status="COMPLETED", idempotency_key="ik1", created_at=old_120d, updated_at=old_120d))
        s.add(V4AssistantTaskRecord(task_id="tsk-old-active", owner_id="keep_u", task_type="recurring_report", title="active", status="ACTIVE", idempotency_key="ik2", created_at=old_120d, updated_at=old_120d))

        # Proposals: aged EXPIRED deleted vs aged PENDING_CONFIRMATION preserved
        s.add(V4ActionProposalRecord(action_id="act-old-exp", actor_id="keep_u", actor_role="ADMIN", action_type="ack_incident", target_type="INCIDENT", target_id="inc-keep", canonical_args_hash="h", expires_at=old_120d, state="EXPIRED", idempotency_key="pk1", created_at=old_120d, updated_at=old_120d))
        s.add(V4ActionProposalRecord(action_id="act-old-pend", actor_id="keep_u", actor_role="ADMIN", action_type="ack_incident", target_type="INCIDENT", target_id="inc-keep", canonical_args_hash="h", expires_at=old_120d, state="PENDING_CONFIRMATION", idempotency_key="pk2", created_at=old_120d, updated_at=old_120d))

        # Chat sessions & messages
        s.add(V4ChatSessionRecord(session_id="sess-old-closed", scope_key="sk-closed", principal_id="keep_u", status="REVOKED", last_active_at=old_120d, created_at=old_120d))
        s.add(V4ChatMessageRecord(id="cmsg-old", session_id="sess-old-closed", seq=1, role="user", content_redacted="hi", created_at=old_120d))
        s.add(V4ChatSessionRecord(session_id="sess-active-keep", scope_key="sk-act", principal_id="keep_u", status="ACTIVE", last_active_at=recent_3d, created_at=old_120d))
        s.add(V4ChatMessageRecord(id="cmsg-active-keep", session_id="sess-active-keep", seq=1, role="user", content_redacted="active msg", created_at=old_120d))

        # Events & receipts: consumed old deleted; unconsumed old preserved; orphan old receipt deleted
        s.add(V4EventStoreRecord(event_id="ev-old-cons", source="s", source_event_id="se-1", event_type="incident.created", resource_id="r1", occurred_at=old_120d, observed_at=old_120d, payload_redacted_json={}, created_at=old_120d))
        s.add(V4EventConsumerReceiptRecord(id="rcp-old-cons", consumer="notification-policy-v4", event_id="ev-old-cons", source="s", source_event_id="se-1", observed_at=old_120d, processed_at=old_120d))
        s.add(V4EventStoreRecord(event_id="ev-old-uncons", source="s", source_event_id="se-2", event_type="incident.created", resource_id="r2", occurred_at=old_120d, observed_at=old_120d, payload_redacted_json={}, created_at=old_120d))
        s.add(V4EventConsumerReceiptRecord(id="rcp-orphan", consumer="notification-policy-v4", event_id="ev-gone", source="s", source_event_id="se-gone", observed_at=old_120d, processed_at=old_120d))
        s.add(V4WebhookDeliveryRecord(id="wh-old", connector_id="github", delivery_id="d-old", event_type="push", received_at=old_120d))
        for extra_i in range(12):
            s.add(V4WebhookDeliveryRecord(id=f"wh-extra-{extra_i}", connector_id="github", delivery_id=f"d-extra-{extra_i}", event_type="push", received_at=old_120d))

    # 1. Default config stays disabled & dry_run=True
    ret_default = RetentionMaintenanceService(db)
    assert ret_default.cfg.enabled is False and ret_default.cfg.dry_run is True
    assert ret_default.run()["skipped"] is True

    # 2. Dry-run evaluates candidates WITHOUT deleting anything
    ret_cfg = RetentionV4Config(enabled=True, dry_run=True, batch_size=10, max_batches_per_run=5, min_dedupe_safety_hours=168)
    ret_svc = RetentionMaintenanceService(db, cfg=ret_cfg)
    dry_res = ret_svc.run(force=True, now_override=now)
    assert dry_res["dry_run"] is True and dry_res["total_deleted"] == 0 and dry_res["total_would_delete"] >= 6
    assert dry_res["vacuum_executed"] is False

    # 3. Live batched deletion (dry_run=False) removes ONLY eligible aged terminal records across batches
    live_res = ret_svc.run(dry_run=False, force=True, actor="admin-maint", now_override=now)
    assert live_res["dry_run"] is False and live_res["total_deleted"] >= 18
    assert live_res["categories"]["v4_webhook_deliveries"]["batches"] >= 2

    with db.session_scope() as s:
        assert s.get(V4NotificationOutboxRecord, "out-old-sent") is None
        assert s.get(V4DeliveryAttemptRecord, "att-old-ok") is None
        assert s.get(V4NotificationOutboxRecord, "out-old-pending") is not None
        assert s.get(V4NotificationOutboxRecord, "out-old-retry") is not None
        assert s.get(V4NotificationOutboxRecord, "out-old-unk") is not None
        assert s.get(V4DeliveryAttemptRecord, "att-keep-unk") is not None
        assert s.get(V4NotificationOutboxRecord, "out-recent-sent") is not None
        assert s.get(V4MessageInboxRecord, "inb-old-ok") is None
        assert s.get(V4MessageInboxRecord, "inb-old-recv") is not None
        assert s.get(V4AssistantTaskRecord, "tsk-old-done") is None
        assert s.get(V4AssistantTaskRecord, "tsk-old-active") is not None
        assert s.get(V4ActionProposalRecord, "act-old-exp") is None
        assert s.get(V4ActionProposalRecord, "act-old-pend") is not None
        assert s.get(V4ChatSessionRecord, "sess-old-closed") is None
        assert s.get(V4ChatMessageRecord, "cmsg-old") is None
        assert s.get(V4ChatSessionRecord, "sess-active-keep") is not None
        assert s.get(V4ChatMessageRecord, "cmsg-active-keep") is not None
        assert s.get(V4EventStoreRecord, "ev-old-cons") is None
        assert s.get(V4EventConsumerReceiptRecord, "rcp-orphan") is None
        assert s.get(V4EventStoreRecord, "ev-old-uncons") is not None
        # Consumed event receipt removed ONLY after parent gone on next cycle (or if still exists, preserved during parent cycle)
        assert s.get(V4WebhookDeliveryRecord, "wh-old") is None
        # Immutable tables NEVER deleted
        assert s.get(MemberRecord, "m-keep") is not None
        assert s.get(WorkspaceRecord, "ws-keep") is not None
        assert s.get(IncidentRecord, "inc-keep") is not None
        assert s.get(AuditEventRecord, "aud-keep") is not None
        assert s.get(V4UserPreferenceRecord, "keep_u") is not None
        assert s.get(V4KnowledgeDocumentRecord, "kb-keep") is not None
        # Maintenance audit recorded
        maint_aud = s.execute(
            select(AuditEventRecord).where(AuditEventRecord.action == "V4_RETENTION_MAINTENANCE")
        ).scalars().all()
        assert len(maint_aud) >= 2


def test_security_ssrf_ipv6_mapped_cgnat_nat64_approval_crash_recovery_and_revocation(tmp_path: Path):
    # 1. SSRF IPv4-mapped IPv6, CGNAT, NAT64, IPv6 ULA, zone-ID, non-443 port checks
    for bad_ip in [
        "127.0.0.1",
        "100.64.0.1",
        "::1",
        "::ffff:127.0.0.1",
        "::ffff:100.64.0.1",
        "64:ff9b::127.0.0.1",
        "fc00::1",
        "fe80::1%eth0",
    ]:
        with pytest.raises(SSRFBlockedError):
            SafeOutboundGuard.validate_url(
                "https://arxiv.org/abs/1",
                allowed_domains=["arxiv.org"],
                resolver_fn=lambda h, ip=bad_ip: [(2, 1, 6, "", (ip, 443))],
            )
    with pytest.raises(SSRFBlockedError):
        SafeOutboundGuard.validate_url("https://arxiv.org:8443/abs/1", allowed_domains=["arxiv.org"])

    # 2. Approval stuck EXECUTING crash recovery (no blind re-execution)
    svc = ZaloAssistantService(str(tmp_path))
    svc.v4.config = AssistantV4Config(enabled=True, write_actions=True)
    inc1, _ = svc.incident_engine.upsert_incident(
        event_type="GPU_THERMAL_HIGH", severity="P1", source="gpu", resource_id="ws:g0", fingerprint="cr-1", title="Crash 1"
    )
    inc2, _ = svc.incident_engine.upsert_incident(
        event_type="GPU_THERMAL_HIGH", severity="P1", source="gpu", resource_id="ws:g1", fingerprint="cr-2", title="Crash 2"
    )
    # Mark inc1 already acknowledged in core (as if mutation succeeded right before crash), leave inc2 OPEN
    svc.incident_engine.acknowledge_incident(inc1["incident_id"], actor="admin", role="ADMIN", note="pre-crash")
    old_t = datetime.now(timezone.utc) - timedelta(minutes=5)
    with svc.db.session_scope() as s:
        s.add(
            V4ActionProposalRecord(
                action_id="act-stuck-ok",
                actor_id="admin",
                actor_role="ADMIN",
                action_type="ack_incident",
                target_type="INCIDENT",
                target_id=inc1["incident_id"],
                canonical_args_hash="x",
                expires_at=old_t,
                state="EXECUTING",
                idempotency_key="stuck-ok",
                created_at=old_t,
                updated_at=old_t,
            )
        )
        s.add(
            V4ActionProposalRecord(
                action_id="act-stuck-unk",
                actor_id="admin",
                actor_role="ADMIN",
                action_type="ack_incident",
                target_type="INCIDENT",
                target_id=inc2["incident_id"],
                canonical_args_hash="y",
                expires_at=old_t,
                state="EXECUTING",
                idempotency_key="stuck-unk",
                created_at=old_t,
                updated_at=old_t,
            )
        )
    rec_res = svc.v4.approvals.reconcile_stuck_executing(stale_after_seconds=60)
    assert rec_res == {"reconciled_succeeded": 1, "marked_unknown": 1}
    assert svc.v4.approvals.get_proposal("act-stuck-ok")["state"] == "SUCCEEDED"
    assert svc.v4.approvals.get_proposal("act-stuck-unk")["state"] == "UNKNOWN_OUTCOME"

    # 3. Principal revocation clears active V4 chat session, pending approval, watch, and outbox
    link = svc.identity_rbac.admin_bind_identity(zalo_user_id="z-bob", username="bob", member_id="m-bob", role="RESEARCHER")
    inc_bob, _ = svc.incident_engine.upsert_incident(
        event_type="GPU_THERMAL_HIGH", severity="P1", source="gpu", resource_id="ws-bob:g0", fingerprint="cr-bob", title="Bob hot GPU", owner_id="bob"
    )
    sess = svc.v4.memory.get_or_create_session(principal_id="bob", channel="zalo", chat_id="z-bob")
    svc.v4.memory.append_turn_messages(session_id=sess["session_id"], expected_version=1, entries=[{"role": "user", "content": "hello"}])
    with pytest.raises(PermissionError):
        svc.v4.approvals.propose_action(
            actor_id="bob", actor_role="RESEARCHER", chat_id="z-bob", session_id=sess["session_id"], action_type="ack_incident", target_type="INCIDENT", target_id=inc2["incident_id"]
        )
    prop = svc.v4.approvals.propose_action(
        actor_id="bob", actor_role="RESEARCHER", chat_id="z-bob", session_id=sess["session_id"], action_type="ack_incident", target_type="INCIDENT", target_id=inc_bob["incident_id"]
    )
    svc.v4.tasks.create_watch(
        owner_id="bob",
        owner_role="RESEARCHER",
        spec=WatchSpec(target_id="ws-bob", metric="cpu_percent", comparator="gt", threshold=90.0, sustained_seconds=60),
    )
    svc.v4.tasks.create_reminder(owner_id="bob", owner_role="RESEARCHER", title="Bob due", run_at=datetime.now(timezone.utc) - timedelta(seconds=5))
    svc.v4.outbox.enqueue(dedupe_key="bob-msg-1", recipient_id="z-bob", event_type="task.due", body_text="Pending for bob")

    assert svc.identity_rbac.revoke_link(link["id"], actor="admin") is True
    with pytest.raises(PermissionError, match="SESSION_NOT_ACTIVE"):
        svc.v4.memory.get_bounded_context(session_id=sess["session_id"], principal_id="bob", channel="zalo", chat_id="z-bob")
    assert svc.v4.approvals.get_proposal(prop["action_id"])["state"] == "REVOKED"
    assert any(o["status"] == "SUPPRESSED" for o in svc.v4.outbox.list_outbox())
    tick_res = svc.v4.scheduler.tick(snapshot={})
    assert tick_res["fired"] == 0 and tick_res["revoked"] == 1


def test_worker_crash_restart_recovery_and_flag_reload_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 1. Worker crash/restart recovery for inbox cursor + claimed outbox (IN_FLIGHT vs pre-send)
    svc = ZaloAssistantService(str(tmp_path))
    svc.v4.inbox.ingest_message(
        external_message_id="msg-crash-1",
        chat_id="z-adm",
        sender_id="z-adm",
        text="/status",
        update_id=41,
        cursor_name="zalo_polling_offset",
        cursor_value="42",
    )
    svc.v4.outbox.enqueue(dedupe_key="crash-unk", recipient_id="z-adm", event_type="node.unreachable", severity="P0", body_text="In flight")
    svc.v4.outbox.enqueue(dedupe_key="crash-retry", recipient_id="z-adm", event_type="incident.created", severity="P1", body_text="Pre send")
    claimed = svc.v4.outbox.claim_batch("dead-worker", lease_seconds=5)
    assert len(claimed) == 2
    in_flight_id = [c for c in claimed if c["dedupe_key"] == "crash-unk"][0]
    svc.v4.outbox.record_attempt_start(
        outbox_id=in_flight_id["notification_id"],
        recipient_id="z-adm",
        channel="zalo",
        attempt_number=1,
        fence=in_flight_id["fence"],
    )

    # Simulate process restart with new ZaloAssistantService instance on same data_dir
    restarted = ZaloAssistantService(str(tmp_path))
    assert restarted.v4.inbox.get_cursor("zalo_polling_offset") == "42"
    reaped = restarted.v4.outbox.reap_expired_leases(now_override=datetime.now(timezone.utc) + timedelta(minutes=2))
    assert reaped["marked_unknown"] == 1
    assert reaped["reclaimed_retry"] == 1

    # 2. Feature-flag YAML reload & explicit restart semantics + Admin API
    cfg_path = tmp_path / "flags.yaml"
    cfg_path.write_text(yaml.safe_dump({"assistant_v4": {"enabled": True, "shadow_mode": True, "notification_v2": False}}), encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_V4_CONFIG_PATH", str(cfg_path))
    restarted.v4.reload_config(config_path=str(cfg_path), actor="admin")
    assert restarted.v4.config.enabled is True and restarted.v4.config.shadow_mode is True

    # Editing YAML does NOT magically mutate live runtime flags until reload_config() (or restart) is executed!
    cfg_path.write_text(yaml.safe_dump({"assistant_v4": {"enabled": False, "shadow_mode": False}}), encoding="utf-8")
    assert restarted.v4.config.enabled is True

    secret = "final-hardening-test-secret-0123456789"
    monkeypatch.setenv("LABOPS_SESSION_SECRET", secret)
    monkeypatch.setattr(ai_ep, "zalo_assistant_service", restarted)
    adm_tok = issue_token("admin", "ADMIN", member_id="m-adm", secret=secret)
    res_tok = issue_token("alice", "RESEARCHER", member_id="m-alice", secret=secret)
    client = TestClient(app)

    assert client.post("/api/v1/ai-assistant/v4/config/reload", headers={"Authorization": f"Bearer {res_tok}"}).status_code == 403
    r_reload = client.post("/api/v1/ai-assistant/v4/config/reload", headers={"Authorization": f"Bearer {adm_tok}"})
    assert r_reload.status_code == 200
    body = r_reload.json()
    assert body["flags"]["enabled"] is False
    assert body["reload_semantics"]["env_changes_require_restart"] is True
    assert client.post("/api/v1/ai-assistant/v4/retention/run", headers={"Authorization": f"Bearer {adm_tok}"}, json={}).json()["dry_run"] is True


def test_observability_metrics_and_five_state_health_enum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    svc = ZaloAssistantService(str(tmp_path))

    # 1. DISABLED when enabled=False
    assert svc.v4.get_health()["status"] == "DISABLED"

    # 2. NOT_CONFIGURED when enabled=True before any worker cycle/heartbeat
    svc.v4.config = AssistantV4Config(enabled=True, event_engine=True, notification_v2=True, natural_language=True)
    assert svc.v4.get_health()["status"] == "NOT_CONFIGURED"

    # 3. HEALTHY after fresh worker cycle & active healthy watchdog
    monkeypatch.setenv("ASSISTANT_V4_WATCHDOG_PING_URL", "https://hc-ping.com/obs-test-secret")
    svc.v4.config.watchdog = WatchdogV4Config(enabled=True, grace_seconds=180)
    svc.v4.watchdog.http_ping_fn = lambda url: None  # type: ignore[method-assign]
    now = datetime.now(timezone.utc)
    svc.v4.run_worker_cycle(now_override=now)
    h_ok = svc.v4.get_health(now_override=now + timedelta(seconds=5))
    assert h_ok["status"] == "HEALTHY"
    assert set(h_ok["valid_health_states"]) == {"HEALTHY", "DEGRADED", "OFFLINE", "NOT_CONFIGURED", "DISABLED"}

    # 4. DEGRADED when circuit breaker is OPEN or UNKNOWN_DELIVERY exists
    svc.v4.runtime.breaker.record_failure("gw err 1")
    svc.v4.runtime.breaker.record_failure("gw err 2")
    svc.v4.runtime.breaker.record_failure("gw err 3")
    assert svc.v4.get_health(now_override=now + timedelta(seconds=10))["status"] == "DEGRADED"
    svc.v4.runtime.breaker.record_success()

    # 5. OFFLINE when worker heartbeat exceeds grace_seconds
    assert svc.v4.get_health(now_override=now + timedelta(seconds=600))["status"] == "OFFLINE"

    # Simulate SQLite lock error counter & AI timeout telemetry
    with pytest.raises(OperationalError):
        with svc.db.session_scope():
            raise OperationalError("SELECT 1", {}, Exception("database is locked"))
    svc.v4.runtime._record_call_outcome(duration_ms=1200.0, ok=False, timed_out=True)

    metrics = svc.v4.get_metrics()
    for required_key in (
        "worker",
        "events",
        "outbox",
        "delivery_outcomes",
        "shadow",
        "notification_ownership",
        "quota",
        "ai",
        "circuit_breaker",
        "database",
        "retention",
        "watchdog",
    ):
        assert required_key in metrics, f"Missing metric group {required_key}"
    assert metrics["database"]["sqlite_lock_errors"] >= 1
    assert metrics["ai"]["timeout_calls"] == 1 and metrics["ai"]["timeout_rate"] == 1.0
    assert metrics["quota"]["provider_confirmed"] is False
    assert metrics["quota"]["provider_confirmed_usage"] is None
    assert "obs-test-secret" not in str(metrics)
