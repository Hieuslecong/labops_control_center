"""Comprehensive automated tests for LabOps MRAM v1.0 (P0-P7)."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ADMIN_USERNAME", "admin")
os.environ.setdefault("LABOPS_ADMIN_PASSWORD", "TestAdminSecret123!")
os.environ.setdefault("LABOPS_AUTH_SECRET", "test-mram-auth-secret-32bytes-minimum-key")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.security import issue_token  # noqa: E402
from app.db.models import (  # noqa: E402
    ApprovalInvitationRecord,
    CapacityReservationRecord,
    MemberProfileRecord,
    MemberWebCredentialRecord,
    ServerAccessRequestRecord,
)
from app.db.session import DatabaseManager, upgrade_database_schema  # noqa: E402
from app.main import app  # noqa: E402
from app.services.labops_service import service as labops_service  # noqa: E402
from app.services.mram_service import (  # noqa: E402
    MramConflictError,
    MramService,
    MramValidationError,
)
from app.services.storage_inventory_service import storage_service  # noqa: E402
from app.services.zalo_assistant.service import zalo_assistant_service  # noqa: E402


@pytest.fixture()
def mram_env(tmp_path, monkeypatch):
    """Set up an isolated SQLite DB + in-memory host/pool state for each MRAM test."""
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "test_mram.db")
    db_url = f"sqlite:///{db_path}"
    upgrade_database_schema(db_url)
    db_mgr = DatabaseManager(data_dir, database_url=db_url)

    # Save & restore globals cleanly
    orig_members = list(labops_service.members)
    orig_servers = list(labops_service.servers)
    orig_gpus = list(labops_service.gpus)
    orig_custom_pools = dict(storage_service._custom_pools)
    orig_res = dict(storage_service._reservations)
    orig_db = labops_service.db
    orig_zalo_db = zalo_assistant_service.db
    orig_zalo_rbac_db = zalo_assistant_service.identity_rbac.db
    orig_zalo_disp_db = zalo_assistant_service.notification_dispatcher.db

    labops_service.members = []
    labops_service.db = db_mgr
    zalo_assistant_service.db = db_mgr
    zalo_assistant_service.identity_rbac.db = db_mgr
    zalo_assistant_service.notification_dispatcher.db = db_mgr

    # Configure online workstation host + eligible 500 GiB pool + 1 GPU
    storage_service._custom_pools = {}
    storage_service._reservations = {}
    storage_service.register_custom_pool(
        "workstation",
        {
            "host_id": "workstation",
            "storage_id": "pool-hdd3",
            "mount_point": "/mnt/HDD3",
            "filesystem": "ext4",
            "filesystem_uuid": "uuid-hdd3-1234",
            "capacity_gib": 1000.0,
            "used_gib": 400.0,
            "free_gib": 550.0,
            "allocatable_gib": 500.0,
            "eligible": True,
            "read_only": False,
            "mounted": True,
            "quota_enforced": True,
            "quota_backend": "PROJECT_QUOTA",
            "is_default": True,
        },
    )
    labops_service.servers = [
        {
            "id": "workstation",
            "hostname": "aiot-ws1",
            "display_name": "Workstation Primary",
            "status": "ONLINE",
            "freshness_status": "ONLINE",
            "ip_address": "192.168.167.251",
            "role": "COMPUTE_NODE",
        },
        {
            "id": "track",
            "hostname": "aiotlab-rack",
            "display_name": "Rack Server",
            "status": "OFFLINE",
            "freshness_status": "OFFLINE",
            "ip_address": "192.168.167.250",
            "role": "COMPUTE_NODE",
        },
    ]
    mock_gpus = [
        {
            "id": "ws-gpu-0",
            "uuid": "GPU-WS-A30-0001",
            "name": "NVIDIA A30",
            "server_id": "workstation",
            "vram_total_mb": 24576,
        }
    ]
    monkeypatch.setattr(labops_service.nvml_adapter, "get_live_gpu_telemetry_sync", lambda: list(mock_gpus))
    monkeypatch.setattr(labops_service, "get_v2_hosts", lambda: list(labops_service.servers))

    test_mram = MramService(data_dir=data_dir, db_manager=db_mgr)
    test_mram._test_dry_run_provisioning = True

    import app.services.mram_service as mram_mod
    import app.api.v1.endpoints.mram as mram_ep
    import app.api.v1.endpoints.auth as auth_ep

    orig_mod_svc = mram_mod.mram_service
    mram_mod.mram_service = test_mram
    mram_ep.mram_service = test_mram
    auth_ep.mram_service = test_mram
    auth_ep._FAILED_ATTEMPTS.clear()

    client = TestClient(app)

    yield {
        "mram": test_mram,
        "db": db_mgr,
        "client": client,
        "admin_token": issue_token("admin", "ADMIN", ttl=3600),
    }

    mram_mod.mram_service = orig_mod_svc
    mram_ep.mram_service = orig_mod_svc
    auth_ep.mram_service = orig_mod_svc
    labops_service.members = orig_members
    labops_service.servers = orig_servers
    labops_service.gpus = orig_gpus
    labops_service.db = orig_db
    zalo_assistant_service.db = orig_zalo_db
    zalo_assistant_service.identity_rbac.db = orig_zalo_rbac_db
    zalo_assistant_service.notification_dispatcher.db = orig_zalo_disp_db
    storage_service._custom_pools = orig_custom_pools
    storage_service._reservations = orig_res


def _admin_headers(token: str, step_up: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    if step_up:
        h["X-Step-Up-Token"] = step_up
    return h


def test_01_public_web_registration_guard_and_web_only_login_no_ssh(mram_env):
    client = mram_env["client"]
    admin_tok = mram_env["admin_token"]

    # Self-assigning ADMIN role via public registration fails closed (403)
    bad_reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "hacker_admin",
            "password": "StrongPassword123!",
            "full_name": "Hacker",
            "member_code": "H001",
            "role": "ADMIN",
        },
    )
    assert bad_reg.status_code == 403

    # Valid public self-registration creates a WEB_ONLY RESEARCHER with ssh_access_state=NONE
    good_reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "sv_tu_dangky",
            "password": "StrongPassword123!",
            "full_name": "SV Tự Đăng Ký",
            "member_code": "SV999",
            "member_category": "STUDENT",
        },
    )
    assert good_reg.status_code == 200, good_reg.text
    rj = good_reg.json()
    assert rj["role"] == "RESEARCHER"
    assert rj["ssh_access_state"] == "NONE"
    assert rj["must_change_password"] is False

    # Admin creates web-only lecturer & student (zero SSH/GPU/Storage/Workspace allocated)
    lec = client.post(
        "/api/v1/admin/member-web-accounts",
        headers=_admin_headers(admin_tok),
        json={
            "username": "gv_hieu",
            "full_name": "TS. Lê Công Hiếu",
            "member_code": "GV001",
            "member_category": "LECTURER",
            "department_class": "AIoT Lab",
        },
    )
    assert lec.status_code == 200, lec.text
    lec_data = lec.json()
    assert lec_data["profile"]["ssh_access_state"] == "NONE"
    assert lec_data["member"]["cpu_limit_cores"] == 0
    assert lec_data["member"]["storage_quota_gb"] == 0
    assert lec_data["member"]["ssh_port"] is None

    stu = client.post(
        "/api/v1/admin/member-web-accounts",
        headers=_admin_headers(admin_tok),
        json={
            "username": "sv_minh",
            "full_name": "Nguyễn Minh",
            "member_code": "1571020099",
            "member_category": "STUDENT",
            "department_class": "CNTT K16",
            "primary_supervisor_id": lec_data["profile"]["member_id"],
        },
    )
    assert stu.status_code == 200
    stu_pw = stu.json()["initial_web_password"]

    # Member can log in web portal WITHOUT Linux/SSH/Workspace provisioned
    login_r = client.post("/api/v1/auth/login", json={"username": "sv_minh", "password": stu_pw})
    assert login_r.status_code == 200
    lj = login_r.json()
    assert lj["role"] == "RESEARCHER"
    assert lj["member_category"] == "STUDENT"
    assert lj["ssh_access_state"] == "NONE"
    assert lj["must_change_password"] is True

    # Member changes initial web password without needing SSH workspace
    tok = lj["access_token"]
    chg = client.post(
        "/api/v1/auth/change-web-password",
        headers={"Authorization": f"Bearer {tok}"},
        json={"current_password": stu_pw, "new_password": "NewStrongWebPass123!"},
    )
    assert chg.status_code == 200
    assert chg.json()["must_change_password"] is False

    # Old password fails, new password succeeds
    assert client.post("/api/v1/auth/login", json={"username": "sv_minh", "password": stu_pw}).status_code == 401
    assert (
        client.post("/api/v1/auth/login", json={"username": "sv_minh", "password": "NewStrongWebPass123!"}).status_code
        == 200
    )


def test_02_legacy_login_compat_and_lockout(mram_env):
    client = mram_env["client"]
    # Legacy member without MemberWebCredentialRecord row falls back to workspace password verifier
    legacy_m = {
        "id": "mem-legacy-1",
        "username": "legacy_user",
        "fullName": "Legacy User",
        "role": "RESEARCHER",
        "status": "ACTIVE",
        "assigned_server": "workstation",
        "ssh_port": 22011,
    }
    labops_service.members.append(legacy_m)
    legacy_pw, _ = labops_service.ssh_access_manager.issue_temporary_password(
        member_id="mem-legacy-1",
        username="legacy_user",
        actor="admin",
        ttl_seconds=86400,
    )
    r = client.post("/api/v1/auth/login", json={"username": "legacy_user", "password": legacy_pw})
    assert r.status_code == 200
    assert r.json()["username"] == "legacy_user"

    # Brute-force lockout test on MRAM credential
    mram = mram_env["mram"]
    created = mram.create_web_member_account(
        {"username": "lock_test", "full_name": "Lock Test", "member_code": "SV02", "member_category": "STUDENT"}
    )
    for _ in range(5):
        bad = client.post("/api/v1/auth/login", json={"username": "lock_test", "password": "WrongPassword1!"})
        assert bad.status_code in (401, 429)
    locked = client.post(
        "/api/v1/auth/login", json={"username": "lock_test", "password": created["initial_web_password"]}
    )
    assert locked.status_code == 429


def test_03_student_vs_lecturer_forms_supervisor_grouping_and_rbac(mram_env):
    client = mram_env["client"]
    admin_tok = mram_env["admin_token"]
    mram: MramService = mram_env["mram"]

    lec = mram.create_web_member_account(
        {"username": "gv_an", "full_name": "PGS. Nguyễn An", "member_code": "GV10", "member_category": "LECTURER"}
    )
    lec_id = lec["profile"]["member_id"]
    stu = mram.create_web_member_account(
        {
            "username": "sv_linh",
            "full_name": "Trần Linh",
            "member_code": "SV10",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec_id,
        }
    )
    unassigned_stu = mram.create_web_member_account(
        {"username": "sv_chua_gan", "full_name": "Phạm Chưa Gán", "member_code": "SV11", "member_category": "STUDENT"}
    )
    stu_tok = issue_token("sv_linh", "RESEARCHER", member_id=stu["profile"]["member_id"], ttl=3600)
    lec_tok = issue_token("gv_an", "RESEARCHER", member_id=lec_id, ttl=3600)

    # Reject forbidden client overrides (role / member_id)
    bad_override = client.post(
        "/api/v1/me/ssh-requests",
        headers={"Authorization": f"Bearer {stu_tok}"},
        json={
            "member_id": "mem-someone-else",
            "primary_supervisor_id": lec_id,
            "project_title": "Project AIoT",
            "registration_reason": "Cần tài nguyên chạy thử nghiệm mô hình CNN quy mô vừa.",
            "ssh_purpose": "Chạy PyTorch training qua Remote-SSH",
            "expected_tasks": "train, eval",
            "policy_accepted": True,
        },
    )
    assert bad_override.status_code == 400

    # Student missing registration_reason fails
    short_reason = client.post(
        "/api/v1/me/ssh-requests",
        headers={"Authorization": f"Bearer {stu_tok}"},
        json={
            "primary_supervisor_id": lec_id,
            "project_title": "Project AIoT",
            "registration_reason": "Ngắn quá",
            "ssh_purpose": "Chạy PyTorch training qua Remote-SSH",
            "expected_tasks": "train, eval",
            "policy_accepted": True,
        },
    )
    assert short_reason.status_code == 400

    # Student valid submit succeeds (default recommendation 4C/16G/100G/NONE/30d)
    ok_stu = client.post(
        "/api/v1/me/ssh-requests",
        headers={"Authorization": f"Bearer {stu_tok}"},
        json={
            "primary_supervisor_id": lec_id,
            "project_title": "Hệ thống IoT Edge AI",
            "registration_reason": "Cần máy chủ Lab để huấn luyện mô hình YOLOv8 với tập ảnh 40GB.",
            "ssh_purpose": "Kết nối VS Code Remote-SSH phát triển mô hình",
            "expected_tasks": "preprocessing, training, evaluation",
            "policy_accepted": True,
        },
    )
    assert ok_stu.status_code == 200, ok_stu.text
    req = ok_stu.json()["request"]
    assert req["recommendation"]["cpu_cores"] == 4
    assert req["recommendation"]["ram_gb"] == 16
    assert req["recommendation"]["storage_quota_gb"] == 50
    assert req["recommendation"]["gpu_mode"] == "NONE"
    assert req["recommendation"]["duration_days"] == 30

    # Duplicate open initial request blocked (409)
    dup = client.post(
        "/api/v1/me/ssh-requests",
        headers={"Authorization": f"Bearer {stu_tok}"},
        json={
            "primary_supervisor_id": lec_id,
            "project_title": "Hệ thống IoT Edge AI 2",
            "registration_reason": "Thử gửi trùng yêu cầu thứ hai khi đơn đầu đang mở.",
            "ssh_purpose": "Kết nối VS Code Remote-SSH",
            "expected_tasks": "training",
            "policy_accepted": True,
        },
    )
    assert dup.status_code == 409

    # Lecturer submit requires NO duration_days
    ok_lec = client.post(
        "/api/v1/me/ssh-requests",
        headers={"Authorization": f"Bearer {lec_tok}"},
        json={
            "project_title": "Nghiên cứu LLM Nhúng",
            "registration_reason": "Triển khai thử nghiệm fine-tuning LLM cho bài báo nghiên cứu.",
            "ssh_purpose": "Chạy inference và hướng dẫn nhóm sinh viên AIoT",
            "expected_tasks": "benchmark, model evaluation",
            "policy_accepted": True,
        },
    )
    assert ok_lec.status_code == 200
    assert ok_lec.json()["request"]["requested_duration_days"] is None

    # Admin supervisor grouping includes UNASSIGNED & lecturer stats
    grp_res = client.get("/api/v1/admin/supervisor-groups", headers=_admin_headers(admin_tok))
    assert grp_res.status_code == 200
    groups = {g["supervisor_id"]: g for g in grp_res.json()["groups"]}
    assert groups[lec_id]["total_students"] == 1
    assert groups[lec_id]["pending_requests"] == 1
    assert groups["UNASSIGNED"]["total_students"] == 1

    # Verified Lecturer sees ONLY their assigned students (secret-free); Student receives 403
    sup_view = client.get("/api/v1/me/supervised-students", headers={"Authorization": f"Bearer {lec_tok}"})
    assert sup_view.status_code == 200
    assert [s["username"] for s in sup_view.json()["students"]] == ["sv_linh"]
    assert "initial_web_password" not in str(sup_view.json())

    stu_sup_view = client.get("/api/v1/me/supervised-students", headers={"Authorization": f"Bearer {stu_tok}"})
    assert stu_sup_view.status_code == 403


def test_04_24h_sla_expiry_late_approval_denial_and_reapply(mram_env):
    mram: MramService = mram_env["mram"]
    lec = mram.create_web_member_account(
        {"username": "gv_sla", "full_name": "GV SLA", "member_code": "GV20", "member_category": "LECTURER"}
    )
    stu = mram.create_web_member_account(
        {
            "username": "sv_sla",
            "full_name": "SV SLA",
            "member_code": "SV20",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec["profile"]["member_id"],
        }
    )
    sub = mram.submit_ssh_request(
        username="sv_sla",
        payload={
            "primary_supervisor_id": lec["profile"]["member_id"],
            "project_title": "Project SLA 24h",
            "registration_reason": "Cần xác minh cơ chế tự động hết hạn đơn sau 24 giờ chờ.",
            "ssh_purpose": "Huấn luyện mô hình",
            "expected_tasks": "train",
            "policy_accepted": True,
        },
    )
    req_id = sub["request"]["id"]

    # Force clock past 24h SLA -> sweep expires request & revokes invitation
    expired = mram.expire_overdue_requests(now_override=datetime.now(timezone.utc) + timedelta(hours=25))
    assert req_id in expired

    # Late approval MUST be rejected
    with pytest.raises(MramConflictError) as exc_info:
        mram.decide_ssh_request(
            request_id=req_id,
            decision="APPROVED",
            approver_username="admin",
            config={
                "host_id": "workstation",
                "pool_id": "pool-hdd3",
                "cpu_cores": 4,
                "ram_gb": 16,
                "storage_quota_gb": 100,
                "gpu_mode": "NONE",
                "duration_days": 30,
            },
        )
    assert exc_info.value.code == "REQUEST_REVIEW_EXPIRED"

    # Student can re-apply with new immutable ID and fresh 24h SLA window
    reapp = mram.reapply_ssh_request(username="sv_sla", request_id=req_id)
    new_req = reapp["request"]
    assert new_req["id"] != req_id
    assert new_req["parent_request_id"] == req_id
    assert new_req["status"] == "PENDING_APPROVAL"


def test_05_capacity_reservation_host_switch_and_pool_validation(mram_env):
    mram: MramService = mram_env["mram"]
    lec = mram.create_web_member_account(
        {"username": "gv_cap", "full_name": "GV Cap", "member_code": "GV30", "member_category": "LECTURER"}
    )
    # 500 GiB pool available -> first student gets approved 350 GiB, second asking 200 GiB MUST fail capacity check!
    s1 = mram.create_web_member_account(
        {
            "username": "sv_cap1",
            "full_name": "SV Cap 1",
            "member_code": "SV31",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec["profile"]["member_id"],
        }
    )
    s2 = mram.create_web_member_account(
        {
            "username": "sv_cap2",
            "full_name": "SV Cap 2",
            "member_code": "SV32",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec["profile"]["member_id"],
        }
    )
    r1 = mram.submit_ssh_request(
        username="sv_cap1",
        payload={
            "primary_supervisor_id": lec["profile"]["member_id"],
            "project_title": "Big Data Set 1",
            "registration_reason": "Xử lý tập dữ liệu ảnh độ phân giải cao chiếm nhiều dung lượng.",
            "ssh_purpose": "Lưu trữ và huấn luyện trên GPU",
            "expected_tasks": "training",
            "policy_accepted": True,
        },
    )["request"]["id"]
    r2 = mram.submit_ssh_request(
        username="sv_cap2",
        payload={
            "primary_supervisor_id": lec["profile"]["member_id"],
            "project_title": "Big Data Set 2",
            "registration_reason": "Kiểm thử không cho cấp vượt dung lượng khả dụng của pool.",
            "ssh_purpose": "Huấn luyện mô hình",
            "expected_tasks": "training",
            "policy_accepted": True,
        },
    )["request"]["id"]

    # Reject cross-host GPU or root pool validation
    bad_root = mram.validate_request_config(
        r1,
        {"host_id": "workstation", "pool_id": "pool-root", "cpu_cores": 4, "ram_gb": 16, "storage_quota_gb": 100},
    )
    assert bad_root["valid"] is False

    # Approve r1 with 350 GiB + EXCLUSIVE GPU
    d1 = mram.decide_ssh_request(
        request_id=r1,
        decision="APPROVED",
        approver_username="admin",
        expected_version=1,
        config={
            "host_id": "workstation",
            "pool_id": "pool-hdd3",
            "cpu_cores": 4,
            "ram_gb": 16,
            "storage_quota_gb": 350,
            "gpu_mode": "EXCLUSIVE",
            "gpu_uuid": "GPU-WS-A30-0001",
            "duration_days": 30,
        },
    )
    assert d1["provisioning"]["status"] == "VERIFIED_READY"

    # r2 asking 200 GiB (only 150 GiB left) + same EXCLUSIVE GPU MUST fail pre-validation
    val2 = mram.validate_request_config(
        r2,
        {
            "host_id": "workstation",
            "pool_id": "pool-hdd3",
            "cpu_cores": 4,
            "ram_gb": 16,
            "storage_quota_gb": 200,
            "gpu_mode": "SHARED",
            "gpu_uuid": "GPU-WS-A30-0001",
            "duration_days": 30,
        },
    )
    assert val2["valid"] is False
    assert any("vượt quá dung lượng" in e for e in val2["errors"])
    assert any("EXCLUSIVE" in e for e in val2["errors"])


def test_06_zalo_invitation_security_handover_claim_reissue_and_ssh_expiry(mram_env):
    client = mram_env["client"]
    admin_tok = mram_env["admin_token"]
    mram: MramService = mram_env["mram"]
    db = mram_env["db"]

    # Setup verified Admin Zalo link strictly for Hiếu Lê (0c00ed813eccd7928edd)
    zalo_assistant_service.identity_rbac.bind_daily_brief_admin(
        zalo_user_id="0c00ed813eccd7928edd",
        username="admin",
        member_id="mem-admin",
        verified_chat_id="0c00ed813eccd7928edd",
    )

    lec = mram.create_web_member_account(
        {"username": "gv_zalo", "full_name": "GV Zalo", "member_code": "GV40", "member_category": "LECTURER"}
    )
    stu = mram.create_web_member_account(
        {
            "username": "sv_zalo",
            "full_name": "SV Zalo",
            "member_code": "SV40",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec["profile"]["member_id"],
            "initial_password": "WebLoginStrong123!",
        }
    )
    stu_id = stu["profile"]["member_id"]
    stu_tok = issue_token("sv_zalo", "RESEARCHER", member_id=stu_id, ttl=3600)

    # Handover not ready before approval
    pre_handover = client.get("/api/v1/me/ssh-access", headers={"Authorization": f"Bearer {stu_tok}"})
    assert pre_handover.status_code == 200
    assert pre_handover.json()["ready"] is False

    sub = mram.submit_ssh_request(
        username="sv_zalo",
        payload={
            "primary_supervisor_id": lec["profile"]["member_id"],
            "project_title": "Zalo Security Flow",
            "registration_reason": "Xác minh cơ chế gửi thông báo Zalo cho đúng Admin Hiếu Lê.",
            "ssh_purpose": "Phát triển dự án AIoT",
            "expected_tasks": "train",
            "policy_accepted": True,
        },
    )
    req_id = sub["request"]["id"]
    assert sub["zalo_notification"]["delivery_status"] == "QUEUED_FOR_VERIFIED_ADMIN"
    assert sub["zalo_notification"]["target_zalo_id"] == "0c00ed813eccd7928edd"

    # Verify DB stores hash (no plaintext token) & GET verify does not consume invitation
    with db.session_scope() as session:
        inv = session.execute(
            select(ApprovalInvitationRecord).where(ApprovalInvitationRecord.request_id == req_id)
        ).scalar_one()
        assert len(inv.token_hash) == 64

    # Step-up approve via API (without step-up header MUST fail 403)
    no_step = client.post(
        f"/api/v1/admin/ssh-requests/{req_id}/approve",
        headers=_admin_headers(admin_tok),
        json={"host_id": "workstation", "pool_id": "pool-hdd3", "cpu_cores": 4, "ram_gb": 16, "storage_quota_gb": 100, "duration_days": 30},
    )
    assert no_step.status_code == 403

    step = client.post(
        "/api/v1/auth/step-up",
        headers=_admin_headers(admin_tok),
        json={"password": "TestAdminSecret123!", "action": "APPROVE_SSH_REQUEST", "target_id": req_id},
    )
    assert step.status_code == 200
    step_tok = step.json()["step_up_token"]

    app_res = client.post(
        f"/api/v1/admin/ssh-requests/{req_id}/approve",
        headers=_admin_headers(admin_tok, step_tok),
        json={
            "expected_version": 1,
            "reason": "Đồng ý cấp phát 4C/16G/100G",
            "config": {
                "host_id": "workstation",
                "pool_id": "pool-hdd3",
                "cpu_cores": 4,
                "ram_gb": 16,
                "storage_quota_gb": 100,
                "gpu_mode": "NONE",
                "duration_days": 30,
            },
        },
    )
    assert app_res.status_code == 200, app_res.text
    assert app_res.json()["provisioning"]["status"] == "VERIFIED_READY"

    # Idempotent replay of provisioning returns same op without duplicating
    replay = mram.provision_approved_request(request_id=req_id, approval_id=app_res.json()["approval_id"])
    assert replay["idempotent_replay"] is True

    # Handover is now READY; Claim requires member Step-Up and succeeds ONLY ONCE (410 on replay)
    handover = client.get("/api/v1/me/ssh-access", headers={"Authorization": f"Bearer {stu_tok}"})
    assert handover.status_code == 200
    assert handover.json()["ready"] is True

    m_step = client.post(
        "/api/v1/auth/step-up",
        headers={"Authorization": f"Bearer {stu_tok}"},
        json={"password": "WebLoginStrong123!", "action": "CLAIM_SSH_SECRET", "target_id": "sv_zalo"},
    )
    assert m_step.status_code == 200
    m_step_tok = m_step.json()["step_up_token"]

    claim1 = client.post(
        "/api/v1/me/ssh-access/claim",
        headers={"Authorization": f"Bearer {stu_tok}", "X-Step-Up-Token": m_step_tok},
    )
    assert claim1.status_code == 200
    assert claim1.json()["temporary_password"]

    m_step2 = client.post(
        "/api/v1/auth/step-up",
        headers={"Authorization": f"Bearer {stu_tok}"},
        json={"password": "WebLoginStrong123!", "action": "CLAIM_SSH_SECRET", "target_id": "sv_zalo"},
    )
    claim2 = client.post(
        "/api/v1/me/ssh-access/claim",
        headers={"Authorization": f"Bearer {stu_tok}", "X-Step-Up-Token": m_step2.json()["step_up_token"]},
    )
    assert claim2.status_code == 410

    # Reissue works cleanly with fresh member step-up
    m_step3 = client.post(
        "/api/v1/auth/step-up",
        headers={"Authorization": f"Bearer {stu_tok}"},
        json={"password": "WebLoginStrong123!", "action": "REISSUE_SSH_SECRET", "target_id": "sv_zalo"},
    )
    reissue = client.post(
        "/api/v1/me/ssh-access/reissue",
        headers={"Authorization": f"Bearer {stu_tok}", "X-Step-Up-Token": m_step3.json()["step_up_token"]},
    )
    assert reissue.status_code == 200
    assert reissue.json()["reissued"] is True

    # Sweep student SSH expiry -> SSH becomes EXPIRED while WEB login STILL works!
    expired_users = mram.sweep_expired_ssh_access(now_override=datetime.now(timezone.utc) + timedelta(days=31))
    assert "sv_zalo" in expired_users
    web_after_exp = client.post("/api/v1/auth/login", json={"username": "sv_zalo", "password": "WebLoginStrong123!"})
    assert web_after_exp.status_code == 200
    assert web_after_exp.json()["ssh_access_state"] == "EXPIRED"

    # Renewal request can be submitted after SSH expired
    renew_r = client.post(
        f"/api/v1/me/ssh-requests/{req_id}/renew",
        headers={"Authorization": f"Bearer {stu_tok}"},
        json={
            "primary_supervisor_id": lec["profile"]["member_id"],
            "project_title": "Zalo Security Flow (Gia hạn)",
            "registration_reason": "Cần gia hạn thêm 30 ngày để hoàn thiện báo cáo khóa luận.",
            "ssh_purpose": "Tiếp tục sử dụng workspace hiện tại",
            "expected_tasks": "evaluation, report",
            "requested_duration_days": 30,
            "policy_accepted": True,
        },
    )
    assert renew_r.status_code == 200
    assert renew_r.json()["request"]["request_type"] == "RENEWAL"
