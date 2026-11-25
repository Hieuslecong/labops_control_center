"""Phase 7 tests: Controlled administration, R0-R3 boundaries, W01-W05 & R02 rollback."""
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
from app.services.assistant_v4.approval_service import ActionProposalService, classify_action_risk  # noqa: E402
from app.services.assistant_v4.inbox_outbox import OutboxV4Service  # noqa: E402
from app.services.assistant_v4.task_scheduler import TaskService  # noqa: E402
from app.services.zalo_assistant.identity_rbac import ZaloIdentityRBACService, ZaloPrincipal  # noqa: E402
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine  # noqa: E402


def test_w01_w02_confirmation_binding_expiry_replay_and_role_revoke(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    inc_eng = UnifiedIncidentEngine(str(tmp_path), db_manager=db)
    rbac = ZaloIdentityRBACService(str(tmp_path), db_manager=db)
    link = rbac.admin_bind_identity(zalo_user_id="zalo-adm", username="admin", member_id="mem-adm", role="ADMIN")
    inc, _ = inc_eng.upsert_incident(event_type="GPU_THERMAL_HIGH", severity="P1", source="gpu", resource_id="ws:gpu0", fingerprint="hot-w1", title="Hot GPU")

    # Flag OFF by default blocks execution cleanly
    disabled_app = ActionProposalService(db, write_actions_enabled=False, incident_engine=inc_eng)
    admin = ZaloPrincipal("zalo-adm", "admin", "mem-adm", "ADMIN", "Admin", True, True)
    other_user = ZaloPrincipal("zalo-other", "tranglt", "mem-tr", "RESEARCHER", "Trang", True, True)

    p = disabled_app.propose_action(actor_id="admin", actor_role="ADMIN", chat_id="zalo-adm", session_id="s1", action_type="ack_incident", target_type="INCIDENT", target_id=inc["incident_id"], reason="checking")
    with pytest.raises(RuntimeError, match="WRITE_ACTIONS_DISABLED"):
        disabled_app.confirm_zalo_action(action_id=p["action_id"], code=p["confirmation_code"], principal=admin, chat_id="zalo-adm", session_id="s1")

    # With staging flag enabled: reject broad OK/CÓ, wrong user, wrong chat/group, wrong code, expiry
    app_svc = ActionProposalService(
        db,
        write_actions_enabled=True,
        incident_engine=inc_eng,
        identity_reloader=lambda pr: rbac.resolve_zalo_principal(pr.zalo_user_id),
    )
    prop = app_svc.propose_action(actor_id="admin", actor_role="ADMIN", chat_id="zalo-adm", session_id="s1", action_type="ack_incident", target_type="INCIDENT", target_id=inc["incident_id"], reason="checking")

    with pytest.raises(PermissionError):
        app_svc.confirm_zalo_action(action_id=prop["action_id"], code="OK", principal=admin, chat_id="zalo-adm", session_id="s1")
    with pytest.raises(PermissionError):
        app_svc.confirm_zalo_action(action_id=prop["action_id"], code=prop["confirmation_code"], principal=other_user, chat_id="zalo-other", session_id="s1")
    with pytest.raises(PermissionError):
        app_svc.confirm_zalo_action(action_id=prop["action_id"], code=prop["confirmation_code"], principal=admin, chat_id="group-999", session_id="s1")
    with pytest.raises(PermissionError, match="EXPIRED"):
        app_svc.confirm_zalo_action(action_id=prop["action_id"], code=prop["confirmation_code"], principal=admin, chat_id="zalo-adm", session_id="s1", now_override=datetime.now(timezone.utc) + timedelta(minutes=10))

    # W02: Role revoked after proposal -> rejected at confirm time!
    prop2 = app_svc.propose_action(actor_id="admin", actor_role="ADMIN", chat_id="zalo-adm", session_id="s1", action_type="ack_incident", target_type="INCIDENT", target_id=inc["incident_id"], reason="ack2")
    rbac.revoke_link(link["id"])
    with pytest.raises(PermissionError):
        app_svc.confirm_zalo_action(action_id=prop2["action_id"], code=prop2["confirmation_code"], principal=admin, chat_id="zalo-adm", session_id="s1")


def test_w03_idempotent_confirm_and_target_state_change_rejection(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    inc_eng = UnifiedIncidentEngine(str(tmp_path), db_manager=db)
    inc, _ = inc_eng.upsert_incident(event_type="GPU_THERMAL_HIGH", severity="P1", source="gpu", resource_id="ws:gpu0", fingerprint="hot-w3", title="Hot GPU")
    admin = ZaloPrincipal("zalo-adm", "admin", "mem-adm", "ADMIN", "Admin", True, True)
    app_svc = ActionProposalService(db, write_actions_enabled=True, incident_engine=inc_eng)

    prop = app_svc.propose_action(actor_id="admin", actor_role="ADMIN", chat_id="zalo-adm", session_id="s1", action_type="ack_incident", target_type="INCIDENT", target_id=inc["incident_id"], reason="verified")
    res1 = app_svc.confirm_zalo_action(action_id=prop["action_id"], code=prop["confirmation_code"], principal=admin, chat_id="zalo-adm", session_id="s1")
    assert res1["state"] == "SUCCEEDED"

    # Replaying confirmed proposal returns idempotent_replay without double executing
    res2 = app_svc.confirm_zalo_action(action_id=prop["action_id"], code=prop["confirmation_code"], principal=admin, chat_id="zalo-adm", session_id="s1")
    assert res2.get("idempotent_replay") is True

    # W02: Target changed concurrently after proposal -> rejected!
    prop_res = app_svc.propose_action(actor_id="admin", actor_role="ADMIN", chat_id="zalo-adm", session_id="s1", action_type="resolve_incident", target_type="INCIDENT", target_id=inc["incident_id"], reason="manual close")
    inc_eng.resolve_incident(inc["incident_id"], actor="admin", role="ADMIN", note="closed elsewhere")
    with pytest.raises(PermissionError, match="TARGET_STATE_CHANGED"):
        app_svc.confirm_zalo_action(action_id=prop_res["action_id"], code=prop_res["confirmation_code"], principal=admin, chat_id="zalo-adm", session_id="s1", observed_recovered=True)


def test_w04_w05_forbidden_actions_and_r3_blocked_on_zalo(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    app_svc = ActionProposalService(db, write_actions_enabled=True)
    admin = ZaloPrincipal("zalo-adm", "admin", "m-adm", "ADMIN", "Admin", True, True)

    for bad in ("arbitrary_shell", "remote_ssh", "sudo_cmd", "delete_member", "delete_workspace", "modify_network", "self_grant_admin"):
        assert classify_action_risk(bad) == "FORBIDDEN"
        with pytest.raises(PermissionError, match="FORBIDDEN_ACTION"):
            app_svc.propose_action(actor_id="admin", actor_role="ADMIN", chat_id="zalo-adm", session_id="s1", action_type=bad, target_type="NODE", target_id="workstation")

    # R3 requires Dashboard step-up auth — NEVER executable via Zalo!
    r3_prop = app_svc.propose_action(actor_id="admin", actor_role="ADMIN", chat_id="zalo-adm", session_id="s1", action_type="restart_workspace", target_type="WORKSPACE", target_id="ws-1")
    assert r3_prop["risk_tier"] == "R3"
    assert r3_prop["dashboard_required"] is True
    with pytest.raises(PermissionError, match="R3"):
        app_svc.confirm_zalo_action(action_id=r3_prop["action_id"], code="ANYTHING", principal=admin, chat_id="zalo-adm")


def test_r02_flag_rollback_preserves_pending_tasks_without_duplicates(tmp_path: Path):
    from types import SimpleNamespace
    from app.services.assistant_v4.config import AssistantV4Config
    from app.services.assistant_v4.facade import AssistantV4Facade
    from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector

    db = DatabaseManager(str(tmp_path))
    rbac = ZaloIdentityRBACService(str(tmp_path), db_manager=db)
    rbac.admin_bind_identity(zalo_user_id="zalo-adm", username="admin", member_id="m-adm", role="ADMIN")
    inc_eng = UnifiedIncidentEngine(str(tmp_path), db_manager=db)
    tel = UnifiedTelemetryCollector(str(tmp_path), db_manager=db, core_service=SimpleNamespace(local_host_id="ws", servers=[], gpus=[], jobs=[], workspace_controller=SimpleNamespace(list_workspaces=lambda: []), get_gpu_allocations_by_device=lambda: []))

    cfg_on = AssistantV4Config(enabled=True, event_engine=True, notification_v2=True, task_manager=True)
    v4 = AssistantV4Facade(
        data_dir=str(tmp_path),
        db_manager=db,
        ai_client=None,
        identity_rbac=rbac,
        telemetry_collector=tel,
        incident_engine=inc_eng,
        authorized_users=["zalo-adm"],
        config=cfg_on,
    )
    t = v4.tasks.create_reminder(owner_id="admin", owner_role="ADMIN", title="Pending task", run_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    v4.outbox.enqueue(dedupe_key="handoff:1", recipient_id="zalo-adm", event_type="node.unreachable", severity="P0", body_text="Rack offline")

    # Instant rollback via flags OFF: worker cycle stops V4 execution while task & outbox remain persisted and listable
    cfg_off = AssistantV4Config()
    assert cfg_off.enabled is False
    v4_rolled_back = AssistantV4Facade(
        data_dir=str(tmp_path),
        db_manager=db,
        ai_client=None,
        identity_rbac=rbac,
        telemetry_collector=tel,
        incident_engine=inc_eng,
        authorized_users=["zalo-adm"],
        config=cfg_off,
    )
    res = v4_rolled_back.run_worker_cycle()
    assert res == {"enabled": False}
    assert [x["task_id"] for x in v4_rolled_back.tasks.list_tasks(owner_id="admin", is_admin=True)] == [t["task_id"]]
    assert len(v4_rolled_back.outbox.list_outbox()) == 1
