"""Independent Audit Regression Tests for LabOps Personal AI Agent V4."""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ENV", "development")

import app.api.v1.endpoints.ai_assistant as ai_ep  # noqa: E402
from app.core.security import issue_token  # noqa: E402
from app.db.models import (  # noqa: E402
    AuditEventRecord,
    MemberProfileRecord,
    NotificationOutboxRecord,
    ServerAccessRequestRecord,
    V4ActionProposalRecord,
    V4EventStoreRecord,
    V4InvestigationRecord,
    V4KnowledgeDocumentRecord,
)
from app.db.session import DatabaseManager  # noqa: E402
from app.main import app  # noqa: E402
from app.services.assistant_v4.approval_service import ActionProposalService  # noqa: E402
from app.services.assistant_v4.config import AssistantV4Config  # noqa: E402
from app.services.assistant_v4.event_store import EventEnvelope, EventReconciler, EventStoreService  # noqa: E402
from app.services.assistant_v4.inbox_outbox import OutboxV4Service  # noqa: E402
from app.services.assistant_v4.notification_policy import EventPolicyEngine  # noqa: E402
from app.services.assistant_v4.secure_tools import SecureToolExecutor  # noqa: E402
from app.services.assistant_v4.skills_and_connectors import SSRFBlockedError, SafeOutboundGuard  # noqa: E402
from app.services.assistant_v4.task_scheduler import PersistentScheduler, TaskService  # noqa: E402
from app.services.zalo_assistant.identity_rbac import ZaloIdentityRBACService, ZaloPrincipal  # noqa: E402
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine  # noqa: E402
from app.services.zalo_assistant.service import ZaloAssistantService  # noqa: E402
from app.services.zalo_assistant.worker import ZaloPollingWorker  # noqa: E402


def test_import_v4_facade_before_zalo_assistant_no_circular_import():
    res = subprocess.run(
        [sys.executable, "-c", "from app.services.assistant_v4.facade import AssistantV4Facade; assert AssistantV4Facade"],
        cwd=BACKEND_DIR,
        env={**os.environ, "PYTHONPATH": BACKEND_DIR},
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Circular/import error: {res.stderr}"


def test_approval_concurrent_cas_toctou_expiry_tamper_lockout_and_audit(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    inc_eng = UnifiedIncidentEngine(str(tmp_path), db_manager=db)
    rbac = ZaloIdentityRBACService(str(tmp_path), db_manager=db)
    rbac.admin_bind_identity(zalo_user_id="z-adm", username="admin", member_id="m-adm", role="ADMIN")
    inc, _ = inc_eng.upsert_incident(
        event_type="GPU_THERMAL_HIGH", severity="P1", source="gpu", resource_id="ws:gpu0", fingerprint="cas-1", title="Hot GPU"
    )
    admin = ZaloPrincipal("z-adm", "admin", "m-adm", "ADMIN", "Admin", True, True)

    exec_count = 0
    orig_ack = inc_eng.acknowledge_incident

    def counted_ack(incident_id, **kwargs):
        nonlocal exec_count
        exec_count += 1
        return orig_ack(incident_id, **kwargs)

    inc_eng.acknowledge_incident = counted_ack  # type: ignore[method-assign]
    svc = ActionProposalService(
        db,
        write_actions_enabled=True,
        incident_engine=inc_eng,
        identity_reloader=lambda pr: rbac.resolve_zalo_principal(pr.zalo_user_id),
    )

    # 1. Concurrent confirmations must execute core mutation at most ONCE
    prop = svc.propose_action(
        actor_id="admin", actor_role="ADMIN", chat_id="z-adm", session_id="s1", action_type="ack_incident", target_type="INCIDENT", target_id=inc["incident_id"]
    )
    def _confirm_worker():
        try:
            return ("OK", svc.confirm_zalo_action(action_id=prop["action_id"], code=prop["confirmation_code"], principal=admin, chat_id="z-adm", session_id="s1"))
        except Exception as exc:
            return ("ERR", str(exc))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        results = [f.result() for f in [ex.submit(_confirm_worker) for _ in range(4)]]
    assert exec_count == 1
    assert any(kind == "OK" and val.get("state") in {"SUCCEEDED", "EXECUTING"} for kind, val in results)

    # 2. Failed attempt_count lockout actually persists across rollback boundary!
    inc2, _ = inc_eng.upsert_incident(
        event_type="GPU_THERMAL_HIGH", severity="P1", source="gpu", resource_id="ws:gpu1", fingerprint="cas-2", title="Hot GPU 2"
    )
    prop_lock = svc.propose_action(
        actor_id="admin", actor_role="ADMIN", chat_id="z-adm", session_id="s1", action_type="ack_incident", target_type="INCIDENT", target_id=inc2["incident_id"]
    )
    for _ in range(3):
        with pytest.raises(PermissionError, match="INVALID_CONFIRMATION_CODE"):
            svc.confirm_zalo_action(action_id=prop_lock["action_id"], code="BAD000", principal=admin, chat_id="z-adm", session_id="s1")
    # 4th attempt even with CORRECT code MUST be blocked (REJECTED persisted)!
    with pytest.raises(PermissionError):
        svc.confirm_zalo_action(action_id=prop_lock["action_id"], code=prop_lock["confirmation_code"], principal=admin, chat_id="z-adm", session_id="s1")

    # 3. Dashboard confirm MUST reject EXPIRED proposals & tampered canonical args
    prop_exp = svc.propose_action(
        actor_id="admin", actor_role="ADMIN", chat_id="z-adm", session_id="s1", action_type="ack_incident", target_type="INCIDENT", target_id=inc2["incident_id"]
    )
    with pytest.raises(PermissionError, match="EXPIRED"):
        svc.confirm_dashboard_action(
            action_id=prop_exp["action_id"],
            actor_username="admin",
            actor_role="ADMIN",
            step_up_ok=True,
            now_override=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
    assert svc.get_proposal(prop_exp["action_id"])["state"] == "EXPIRED"

    prop_tamper = svc.propose_action(
        actor_id="admin", actor_role="ADMIN", chat_id="z-adm", session_id="s1", action_type="ack_incident", target_type="INCIDENT", target_id=inc2["incident_id"]
    )
    with db.session_scope() as s:
        row = s.get(V4ActionProposalRecord, prop_tamper["action_id"])
        row.canonical_args_json = {"target_id": inc2["incident_id"], "reason": "tampered!"}
    with pytest.raises(PermissionError, match="CANONICAL_ARGS_TAMPERED"):
        svc.confirm_dashboard_action(action_id=prop_tamper["action_id"], actor_username="admin", actor_role="ADMIN", step_up_ok=True)

    # 4. AuditEventRecord must contain PROPOSED, DENIED, CONFIRMED, and EXECUTED entries
    with db.session_scope() as s:
        actions = {r.action for r in s.execute(select(AuditEventRecord)).scalars().all()}
    assert {"V4_ACTION_PROPOSED", "V4_ACTION_DENIED", "V4_ACTION_CONFIRMED", "V4_ACTION_EXECUTED"}.issubset(actions)


def test_private_kb_idor_and_nonadmin_task_recipient_spoof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = DatabaseManager(str(tmp_path))
    now = datetime.now(timezone.utc)
    with db.session_scope() as s:
        s.add(
            V4KnowledgeDocumentRecord(
                doc_id="kb-priv-bob",
                title="Bob Private Notes",
                owner_id="bob",
                scope="private",
                content_markdown="Bob private runbook notes",
                content_hash="abc",
                tags_json=["private"],
                created_at=now,
                updated_at=now,
            )
        )
    tools = SecureToolExecutor(str(tmp_path), db_manager=db)
    alice = ZaloPrincipal("z-alice", "alice", "m-alice", "RESEARCHER", "Alice", True, True)
    bob = ZaloPrincipal("z-bob", "bob", "m-bob", "RESEARCHER", "Bob", True, True)

    assert tools.execute_tool("get_runbook_version", {"target_id": "kb-priv-bob"}, alice)["status"] == "DENIED"
    assert tools.execute_tool("get_runbook_version", {"target_id": "kb-priv-bob"}, bob)["status"] == "OK"

    # API task recipient spoofing test
    svc = ZaloAssistantService(str(tmp_path))
    svc.v4.config.enabled = True
    svc.v4.config.task_manager = True
    secret = "test-v4-audit-secret-0123456789abcdef"
    monkeypatch.setenv("LABOPS_SESSION_SECRET", secret)
    monkeypatch.setattr(ai_ep, "zalo_assistant_service", svc)
    res_tok = issue_token("alice", "RESEARCHER", member_id="m-alice", secret=secret)
    client = TestClient(app)
    r = client.post(
        "/api/v1/ai-assistant/v4/tasks",
        headers={"Authorization": f"Bearer {res_tok}"},
        json={"title": "Spam bob", "run_at": (now + timedelta(hours=1)).isoformat(), "recipient_id": "bob"},
    )
    assert r.status_code == 403


def test_v2_v4_no_double_enqueue_in_worker_and_mram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    svc = ZaloAssistantService(str(tmp_path))
    svc.identity_rbac.admin_bind_identity(zalo_user_id="z-adm", username="admin", member_id="m-adm", role="ADMIN")
    svc.identity_rbac.record_observed_private_chat("z-adm", "z-adm", display_name="Admin")
    svc.bot_engine.authorized_users = ["z-adm"]
    svc.v4.config = AssistantV4Config(enabled=True, event_engine=True, notification_v2=True, shadow_mode=False)
    svc.telemetry_collector._core_service = SimpleNamespace(
        local_host_id="ws",
        servers=[{"id": "ws", "hostname": "aiot-ws1", "status": "OFFLINE", "desired_state": "ACTIVE"}],
        gpus=[],
        jobs=[],
        workspace_controller=SimpleNamespace(list_workspaces=lambda: []),
        get_gpu_allocations_by_device=lambda: [],
    )
    worker = ZaloPollingWorker(service_instance=svc)
    asyncio.run(worker._check_infra_telemetry_and_incidents(suppress_v2_enqueue=True))
    # Incident & V4 event created, V2 outbox left EMPTY!
    with svc.db.session_scope() as s:
        v2_rows = s.execute(select(NotificationOutboxRecord)).scalars().all()
        v4_evs = s.execute(select(V4EventStoreRecord)).scalars().all()
    assert v2_rows == []
    assert any(e.event_type == "incident.created" for e in v4_evs)

    # MRAM _issue_and_notify_zalo_invitation MUST delegate to V4 without V2 outbox duplicate
    import app.services.mram_service as mram_mod
    import app.services.zalo_assistant.service as zalo_svc_mod

    monkeypatch.setenv("MRAM_APPROVER_ZALO_ID", "z-adm")
    mram_inst = mram_mod.MramService(data_dir=str(tmp_path), db_manager=svc.db)
    monkeypatch.setattr(zalo_svc_mod, "zalo_assistant_service", svc)
    now = datetime.now(timezone.utc)
    dummy_req = ServerAccessRequestRecord(
        id="sshreq-nodup-1",
        member_id="m-sv",
        username="sv_nodup",
        project_title="No Dup",
        registration_reason="Check V2/V4 no double enqueue",
        submitted_at=now,
        review_expires_at=now + timedelta(hours=24),
        version=1,
    )
    dummy_prof = MemberProfileRecord(member_id="m-sv", username="sv_nodup", full_name="SV NoDup")
    from app.db.models import RequestResourceRecommendationRecord

    dummy_rec = RequestResourceRecommendationRecord(
        id="reqrec-nodup", request_id="sshreq-nodup-1", recommended_host_id="ws", cpu_cores=4, ram_gb=8, storage_quota_gb=50, gpu_mode="NONE"
    )
    mram_notify_res = mram_inst._issue_and_notify_zalo_invitation(
        req=dummy_req, prof=dummy_prof, rec=dummy_rec, supervisor_name="Thay An"
    )
    assert mram_notify_res["delivery_status"] == "DELEGATED_TO_V4_POLICY"
    with svc.db.session_scope() as s:
        assert s.execute(select(NotificationOutboxRecord)).scalars().all() == []

    # Now verify _v4_auth_check enforces subscription.enabled=False at delivery time
    svc.v4.outbox.enqueue(dedupe_key="sub-off-1", recipient_id="z-adm", event_type="node.unreachable", severity="P0", body_text="Test")
    svc.identity_rbac.update_subscription("z-adm", {"enabled": False})

    async def _fake_run():
        class _FakeBot:
            async def send_message(self, chat_id, text):
                raise AssertionError("Should not send when subscription is disabled!")
        await worker._run_v4_worker_step(svc.v4, bot=_FakeBot())

    asyncio.run(_fake_run())
    assert any(o["status"] == "SUPPRESSED" for o in svc.v4.outbox.list_outbox())


def test_quiet_hours_category_filter_and_cap_rejected_fallback(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    rbac = ZaloIdentityRBACService(str(tmp_path), db_manager=db)
    rbac.admin_bind_identity(zalo_user_id="z-adm-quiet", username="adm1", member_id="m1", role="ADMIN")
    rbac.admin_bind_identity(zalo_user_id="z-adm-nop1", username="adm2", member_id="m2", role="ADMIN")
    rbac.update_subscription("z-adm-quiet", {"quiet_hours_start": "22:00", "quiet_hours_end": "07:00", "bypass_quiet_for_p0": True})
    rbac.update_subscription("z-adm-nop1", {"receive_p1": False, "receive_repo_alerts": False})

    policy = EventPolicyEngine(db)
    # 23:30 ICT is inside quiet hours 22:00-07:00
    t_night_utc = datetime(2026, 11, 25, 23, 30, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")).astimezone(timezone.utc)
    with db.session_scope() as s:
        p1_recips = policy._verified_admins(s, severity="P1", category="p1")
        assert "z-adm-nop1" not in p1_recips
        assert "z-adm-quiet" in p1_recips
        policy._enqueue_for_audience(
            s,
            event={"event_id": "ev-q1", "event_type": "incident.created", "resource_id": "inc-q1", "correlation_id": "c-q1"},
            recipients=p1_recips,
            severity="P1",
            actionable=True,
            dedupe_base="qh-p1",
            body_text="P1 at night",
            category="p1",
            now_override=t_night_utc,
        )
        policy._enqueue_for_audience(
            s,
            event={"event_id": "ev-q0", "event_type": "node.unreachable", "resource_id": "track", "correlation_id": "c-q0"},
            recipients=["z-adm-quiet"],
            severity="P0",
            actionable=True,
            dedupe_base="qh-p0",
            body_text="P0 critical at night",
            category="p0",
            now_override=t_night_utc,
        )

    out_rows = OutboxV4Service(db).list_outbox()
    by_sev = {(r["severity"], r["channel"]) for r in out_rows}
    assert ("P1", "dashboard_inbox") in by_sev  # Quiet hours routed P1 away from Zalo
    assert ("P0", "zalo") in by_sev             # P0 bypassed quiet hours!

    # CAP_REJECTED hard error on Zalo -> automatic dashboard_inbox fallback
    outbox = OutboxV4Service(db)
    outbox.enqueue(dedupe_key="cap-hard-test", recipient_id="z-adm-quiet", event_type="incident.created", severity="P1", body_text="Provider capped")

    async def _cap_fail(rec, body, ch):
        if ch == "zalo":
            raise RuntimeError("CAP_HARD: 429 monthly quota exhausted")
        return "dash-ok"

    res = asyncio.run(outbox.deliver_claimed_batch("worker-cap", _cap_fail, batch_size=20))
    assert res["dlq"] >= 1
    assert res.get("fallback_enqueued", 0) >= 1
    assert any(r["dedupe_key"] == "cap-hard-test:cap-fallback" and r["channel"] == "dashboard_inbox" for r in outbox.list_outbox())


def test_ssrf_redirect_protection(tmp_path: Path):
    class _FakeResp:
        def __init__(self, status_code, headers=None, text=""):
            self.status_code = status_code
            self.headers = headers or {}
            self.text = text

    class _FakeClient:
        def __init__(self, responses):
            self._responses = list(responses)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            return self._responses.pop(0)

    public_dns = lambda h: [(2, 1, 6, "", ("93.184.216.34", 443))]  # noqa: E731

    # Open redirect to localhost/IMDS MUST be blocked!
    with pytest.raises(SSRFBlockedError):
        SafeOutboundGuard.fetch_safe_url(
            "https://arxiv.org/abs/1",
            allowed_domains=["arxiv.org"],
            resolver_fn=public_dns,
            client_factory=lambda: _FakeClient([_FakeResp(302, {"Location": "https://169.254.169.254/latest"})]),
        )

    # Redirect loop > max_redirects blocked
    with pytest.raises(SSRFBlockedError, match="redirects"):
        SafeOutboundGuard.fetch_safe_url(
            "https://arxiv.org/abs/1",
            allowed_domains=["arxiv.org"],
            max_redirects=1,
            resolver_fn=public_dns,
            client_factory=lambda: _FakeClient([
                _FakeResp(302, {"Location": "/abs/2"}),
                _FakeResp(302, {"Location": "/abs/3"}),
            ]),
        )

    # Legit single redirect on allowlisted host succeeds and redacts secrets
    ok = SafeOutboundGuard.fetch_safe_url(
        "https://arxiv.org/abs/1",
        allowed_domains=["arxiv.org"],
        max_redirects=2,
        resolver_fn=public_dns,
        client_factory=lambda: _FakeClient([
            _FakeResp(301, {"Location": "/abs/2"}),
            _FakeResp(200, text="paper ok token=secretvalue123456"),
        ]),
    )
    assert ok["status_code"] == 200
    assert ok["redirects"] == 1
    assert "secretvalue123456" not in ok["body_redacted"]


def test_reconciler_full_fields_per_event_tx_isolation_scheduler_cas_and_facade_wiring(tmp_path: Path):
    svc = ZaloAssistantService(str(tmp_path))
    svc.identity_rbac.admin_bind_identity(zalo_user_id="z-adm-live", username="admin", member_id="m-adm", role="ADMIN")
    svc.v4.config = AssistantV4Config(
        enabled=True,
        event_engine=True,
        notification_v2=True,
        task_manager=True,
        proactive_investigation=True,
        shadow_mode=False,
    )
    tue_0815_utc = datetime(2026, 11, 24, 8, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")).astimezone(timezone.utc)
    now = tue_0815_utc
    with svc.db.session_scope() as s:
        s.add(MemberProfileRecord(member_id="m-sv1", username="sv_1", full_name="SV 1", primary_supervisor_id="m-adm"))
        s.add(
            ServerAccessRequestRecord(
                id="ssh-rec-1",
                member_id="m-sv1",
                username="sv_1",
                primary_supervisor_id="m-adm",
                submitted_at=now - timedelta(hours=13),
                review_expires_at=now + timedelta(hours=11),
                status="PENDING_APPROVAL",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    inc, _ = svc.incident_engine.upsert_incident(
        event_type="GPU_THERMAL_HIGH", severity="P1", source="gpu", resource_id="ws:gpu0", host_id="ws", fingerprint="rec-inc-1", title="Hot GPU"
    )
    rec_res = svc.v4.reconciler.reconcile()
    assert rec_res["backfilled"] >= 1
    req_evs = svc.v4.events.list_events(event_type="request.created")
    assert req_evs and req_evs[0]["payload"]["submitted_at"] and req_evs[0]["payload"]["supervisor_id"] == "m-adm"

    # Worker cycle triggers proactive investigation + resolves SLA recipient to z-adm-live (not "admin") + fires due digest
    cyc = svc.v4.run_worker_cycle(now_override=tue_0815_utc)
    assert cyc.get("investigations_triggered", 0) >= 1
    assert cyc.get("digests_enqueued", 0) >= 1
    with svc.db.session_scope() as s:
        inv = s.execute(select(V4InvestigationRecord).where(V4InvestigationRecord.incident_id == inc["incident_id"])).scalars().first()
        assert inv is not None
    sla_msgs = [o for o in svc.v4.outbox.list_outbox() if o["event_type"] == "task.due"]
    assert sla_msgs and all(m["recipient_id"] == "z-adm-live" for m in sla_msgs)

    # EventPolicyEngine routes repository.risk_found & ai.unavailable instead of IGNORED_UNROUTED
    ev_svc = EventStoreService(svc.db)
    svc.v4.record_repo_decision_event(request_id="r-repo-1", req_info={"canonical_repo": "org/bad", "repo_name": "bad"}, decision="REJECTED", actor="admin")
    ev_svc.emit(EventEnvelope(source="agent-runtime", source_event_id="cb-open-1", event_type="ai.unavailable", resource_id="9router"))
    pol_res = svc.v4.policy.process_events()
    assert pol_res["processed"] >= 2
    out_types = {o["event_type"] for o in svc.v4.outbox.list_outbox(limit=100)}
    assert "repository.risk_found" in out_types
    assert "ai.unavailable" in out_types

    # Per-event isolation: 1st event commits even when 2nd event raises!
    e1, _ = ev_svc.emit(EventEnvelope(source="iso", source_event_id="iso-1", event_type="service.recovered", resource_id="r1"))
    e2, _ = ev_svc.emit(EventEnvelope(source="iso", source_event_id="iso-2", event_type="service.recovered", resource_id="r2"))
    seen = []

    def _flaky_consumer(session, ev, is_late):
        seen.append(ev["event_id"])
        if ev["event_id"] == e2:
            raise RuntimeError("boom on event 2")
        return "PROCESSED"

    with pytest.raises(RuntimeError, match="boom on event 2"):
        ev_svc.process_pending("iso-test-consumer", _flaky_consumer)
    # Re-running after crash processes ONLY event 2 (event 1 receipt was preserved!)
    seen.clear()
    res2 = ev_svc.process_pending("iso-test-consumer", lambda s, ev, l: (seen.append(ev["event_id"]), "PROCESSED")[1])
    assert res2["processed"] == 1
    assert seen == [e2]
