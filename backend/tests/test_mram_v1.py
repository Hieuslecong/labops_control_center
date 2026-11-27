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


def test_07_vietnamese_supervisor_tone_and_unicode_grouping(mram_env):
    import unicodedata

    mram: MramService = mram_env["mram"]
    mram.create_web_member_account(
        {"username": "sv_tone1", "full_name": "SV Tone 1", "member_code": "SV71", "member_category": "STUDENT"}
    )
    mram.create_web_member_account(
        {"username": "sv_tone2", "full_name": "SV Tone 2", "member_code": "SV72", "member_category": "STUDENT"}
    )

    mram.submit_ssh_request(
        username="sv_tone1",
        payload={
            "supervisor_name": unicodedata.normalize("NFD", "TS. Lê Thị Thuỳ Trang"),
            "project_title": "Project Tone 1",
            "registration_reason": "Kiểm tra gộp nhóm giảng viên khi sinh viên gõ dấu kiểu mới.",
            "ssh_purpose": "Train",
            "expected_tasks": "train",
            "policy_accepted": True,
        },
    )
    mram.submit_ssh_request(
        username="sv_tone2",
        payload={
            "supervisor_name": "lê thị thùy trang",
            "project_title": "Project Tone 2",
            "registration_reason": "Kiểm tra gộp nhóm giảng viên khi sinh viên gõ dấu kiểu cũ.",
            "ssh_purpose": "Train",
            "expected_tasks": "train",
            "policy_accepted": True,
        },
    )

    groups_data = mram.get_supervisor_groups()["groups"]
    trang_groups = [g for g in groups_data if "trang" in g["supervisor_name"].lower()]
    assert len(trang_groups) == 1
    assert trang_groups[0]["supervisor_name"] == "Lê Thị Thùy Trang"
    assert trang_groups[0]["total_students"] == 2
    assert {s["username"] for s in trang_groups[0]["students"]} == {"sv_tone1", "sv_tone2"}


def test_08_web_accounts_separation_auto_ssh_link_and_password_recovery(mram_env):
    client: TestClient = mram_env["client"]
    admin_tok = mram_env["admin_token"]

    # Register student account via public web registration -> WEB_ONLY, not SSH-linked
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "sv_sep_link",
            "password": "WebSepPass123456",
            "full_name": "Sinh Viên Tách Biệt",
            "member_code": "1671040099",
            "email": "sep_link@dnu.edu.vn",
            "department_class": "KHMT K16",
            "member_category": "STUDENT",
        },
    )
    assert reg.status_code == 200, reg.text
    web_member_id = reg.json()["profile"]["member_id"]

    # Web accounts list contains this web account with is_ssh_linked = False
    web_list1 = client.get("/api/v1/admin/web-accounts", headers=_admin_headers(admin_tok))
    assert web_list1.status_code == 200
    acc1 = next(a for a in web_list1.json()["accounts"] if a["username"] == "sv_sep_link")
    assert acc1["is_ssh_linked"] is False
    assert acc1["linked_ssh_port"] is None

    # Student self-recovers forgotten Web password: ADMIN must be forbidden (403)
    rec_admin = client.post(
        "/api/v1/auth/recover-web-password",
        json={
            "username": "admin",
            "member_code": "ADMIN",
            "email": "admin@lab.ai",
            "new_password": "HackedAdminPassword123!",
        },
    )
    assert rec_admin.status_code == 403

    # Wrong student code / email rejected (401)
    rec_wrong = client.post(
        "/api/v1/auth/recover-web-password",
        json={
            "username": "sv_sep_link",
            "member_code": "WRONG_CODE",
            "email": "sep_link@dnu.edu.vn",
            "new_password": "RecoveredWebPass999!",
        },
    )
    assert rec_wrong.status_code == 401

    # Matching username + member_code + email resets password cleanly
    rec_ok = client.post(
        "/api/v1/auth/recover-web-password",
        json={
            "username": "sv_sep_link",
            "member_code": "1671040099",
            "email": "sep_link@dnu.edu.vn",
            "new_password": "RecoveredWebPass999!",
        },
    )
    assert rec_ok.status_code == 200, rec_ok.text
    login_rec = client.post(
        "/api/v1/auth/login",
        json={"username": "sv_sep_link", "password": "RecoveredWebPass999!"},
    )
    assert login_rec.status_code == 200, login_rec.text

    # Admin reissue Web password: ADMIN rejected (403), student succeeds
    reissue_admin = client.post(
        "/api/v1/admin/web-accounts/admin/reissue-password",
        headers=_admin_headers(admin_tok),
        json={},
    )
    assert reissue_admin.status_code == 403

    reissue_stu = client.post(
        f"/api/v1/admin/web-accounts/{web_member_id}/reissue-password",
        headers=_admin_headers(admin_tok),
        json={},
    )
    assert reissue_stu.status_code == 200, reissue_stu.text
    reissued_pw = reissue_stu.json()["reissued_web_password"]
    login_reissued = client.post(
        "/api/v1/auth/login",
        json={"username": "sv_sep_link", "password": reissued_pw},
    )
    assert login_reissued.status_code == 200, login_reissued.text
    stu_tok = login_reissued.json()["access_token"]

    # Submit SSH request and approve -> Web account is automatically linked with provisioned SSH account
    sub = client.post(
        "/api/v1/me/ssh-requests",
        headers={"Authorization": f"Bearer {stu_tok}"},
        json={
            "supervisor_name": "Lê Thị Thùy Trang",
            "project_title": "Auto Link Verification",
            "registration_reason": "Xác minh tự động liên kết tài khoản Web và SSH khi duyệt.",
            "ssh_purpose": "Train AI",
            "expected_tasks": "train",
            "policy_accepted": True,
        },
    )
    assert sub.status_code == 200, sub.text
    req_id = sub.json()["request"]["id"]
    rec = sub.json()["request"]["recommendation"]

    a_step = client.post(
        "/api/v1/auth/step-up",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={
            "password": os.environ["LABOPS_ADMIN_PASSWORD"],
            "action": "APPROVE_SSH_REQUEST",
            "target_id": req_id,
        },
    )
    assert a_step.status_code == 200, a_step.text
    app_r = client.post(
        f"/api/v1/admin/ssh-requests/{req_id}/approve",
        headers={"Authorization": f"Bearer {admin_tok}", "X-Step-Up-Token": a_step.json()["step_up_token"]},
        json={
            "config": {
                "host_id": rec["recommended_host_id"],
                "pool_id": rec["recommended_pool_id"],
                "cpu_cores": 4,
                "ram_gb": 16,
                "storage_quota_gb": 50,
                "gpu_mode": "NONE",
                "duration_days": 30,
            }
        },
    )
    assert app_r.status_code == 200, app_r.text

    web_list2 = client.get("/api/v1/admin/web-accounts", headers=_admin_headers(admin_tok))
    acc2 = next(a for a in web_list2.json()["accounts"] if a["username"] == "sv_sep_link")
    assert acc2["is_ssh_linked"] is True
    assert acc2["linked_ssh_username"] == "sv_sep_link"
    assert acc2["linked_ssh_port"] is not None


def test_09_zalo_approver_sync_notify_resend_and_bot_pending_tool(mram_env, monkeypatch):
    client: TestClient = mram_env["client"]
    admin_tok = mram_env["admin_token"]

    from app.services.zalo_assistant.identity_rbac import ZaloPrincipal
    from app.services.zalo_assistant.service import zalo_assistant_service

    # Simulate real .env.local MRAM_APPROVER_ZALO_ID that already had a 1-1 DM chat in observed_private_chats
    approver_zalo = "01d4d2b626ffcfa196ee"
    monkeypatch.setenv("MRAM_APPROVER_ZALO_ID", approver_zalo)
    zalo_assistant_service.identity_rbac.record_observed_private_chat(
        approver_zalo, approver_zalo, display_name="Lê Công Hiếu"
    )

    # Register & submit SSH request -> auto-sync binds approver_zalo as ADMIN & queues outbox message
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": "sv_zalo_sync",
            "password": "WebSyncPass12345!",
            "full_name": "Sinh Viên Zalo Sync",
            "member_code": "1671040100",
            "email": "sync_zalo@dnu.edu.vn",
            "member_category": "STUDENT",
        },
    )
    assert reg.status_code == 200
    stu_tok = reg.json()["access_token"]
    sub = client.post(
        "/api/v1/me/ssh-requests",
        headers={"Authorization": f"Bearer {stu_tok}"},
        json={
            "supervisor_name": "Lê Thị Thùy Trang",
            "project_title": "Zalo Auto-Sync Test",
            "registration_reason": "Kiểm tra tự động đồng bộ Zalo Admin và công cụ Bot tra cứu đơn chờ.",
            "ssh_purpose": "Train AI",
            "expected_tasks": "train",
            "policy_accepted": True,
        },
    )
    assert sub.status_code == 200
    req_id = sub.json()["request"]["id"]
    assert sub.json()["zalo_notification"]["delivery_status"] == "QUEUED_FOR_VERIFIED_ADMIN"
    assert sub.json()["zalo_notification"]["target_zalo_id"] == approver_zalo

    # Admin can re-trigger notify-zalo for pending request
    resend = client.post(
        f"/api/v1/admin/ssh-requests/{req_id}/notify-zalo",
        headers=_admin_headers(admin_tok),
    )
    assert resend.status_code == 200
    assert resend.json()["zalo_notification"]["delivery_status"] == "QUEUED_FOR_VERIFIED_ADMIN"

    # Zalo AI Bot tool get_pending_ssh_requests works for ADMIN and denies non-ADMIN
    admin_principal = ZaloPrincipal(approver_zalo, "admin", "mem-admin", "ADMIN", "Lê Công Hiếu", True, True)
    tool_res = zalo_assistant_service.bot_engine.tool_registry.execute_tool(
        "get_pending_ssh_requests", {}, admin_principal, source="zalo"
    )
    assert tool_res["pending_count"] >= 1
    assert any(r["request_id"] == req_id for r in tool_res["pending_requests"])

    stu_principal = ZaloPrincipal("99999", "sv_zalo_sync", "", "RESEARCHER", "SV", True, True)
    denied = zalo_assistant_service.bot_engine.tool_registry.execute_tool(
        "get_pending_ssh_requests", {}, stu_principal, source="zalo"
    )
    assert denied["error"] == "PERMISSION_DENIED"


def test_10_workstation_fair_storage_pool_load_balancing_and_student_16gb_ram_cap(mram_env):
    from app.services.storage_inventory_service import storage_service

    mram: MramService = mram_env["mram"]
    # Add two more eligible pools on workstation with equal 500 GiB free space to verify fair rotation
    storage_service.register_custom_pool(
        "workstation",
        {
            "host_id": "workstation",
            "storage_id": "pool-sdd",
            "mount_point": "/mnt/SDD",
            "filesystem": "ext4",
            "filesystem_uuid": "uuid-sdd-2222",
            "capacity_gib": 1000.0,
            "used_gib": 450.0,
            "free_gib": 550.0,
            "allocatable_gib": 500.0,
            "eligible": True,
            "read_only": False,
            "mounted": True,
            "is_default": False,
        },
    )
    storage_service.register_custom_pool(
        "workstation",
        {
            "host_id": "workstation",
            "storage_id": "pool-sdb",
            "mount_point": "/mnt/SDB",
            "filesystem": "ext4",
            "filesystem_uuid": "uuid-sdb-3333",
            "capacity_gib": 1000.0,
            "used_gib": 450.0,
            "free_gib": 550.0,
            "allocatable_gib": 500.0,
            "eligible": True,
            "read_only": False,
            "mounted": True,
            "is_default": False,
        },
    )

    lec = mram.create_web_member_account(
        {"username": "gv_bal", "full_name": "GV Balance", "member_code": "GV100", "member_category": "LECTURER"}
    )
    lec_id = lec["profile"]["member_id"]

    req_ids: list[str] = []
    chosen_pools: list[str] = []
    for idx in range(1, 4):
        uname = f"sv_bal_{idx}"
        mram.create_web_member_account(
            {
                "username": uname,
                "full_name": f"SV Balance {idx}",
                "member_code": f"SV10{idx}",
                "member_category": "STUDENT",
                "primary_supervisor_id": lec_id,
            }
        )
        sub = mram.submit_ssh_request(
            username=uname,
            payload={
                "primary_supervisor_id": lec_id,
                "project_title": f"Balanced Disk Project {idx}",
                "registration_reason": "Kiểm tra phân bổ mặc định trên workstation và chia đều ổ cứng.",
                "ssh_purpose": "Train AI",
                "expected_tasks": "train",
                "policy_accepted": True,
            },
        )
        req = sub["request"]
        req_ids.append(req["id"])
        assert req["recommendation"]["recommended_host_id"] == "workstation"
        assert req["recommendation"]["storage_quota_gb"] == 50
        assert req["recommendation"]["ram_gb"] == 16
        assert req["recommendation"]["rationale"]["student_max_ram_gb"] == 16

    # Student RAM > 16 GiB MUST fail pre-validation
    over_ram = mram.validate_request_config(
        req_ids[0],
        {
            "host_id": "workstation",
            "pool_id": "pool-hdd3",
            "cpu_cores": 4,
            "ram_gb": 32,
            "storage_quota_gb": 50,
            "gpu_mode": "NONE",
            "duration_days": 30,
        },
    )
    assert over_ram["valid"] is False
    assert any("16 GiB RAM" in err for err in over_ram["errors"])

    # Approve each student with default resolved config -> distributes evenly across preferred sdd & sdb, keeping pool-hdd3 restricted!
    for rid in req_ids:
        res = mram.zalo_admin_decide_request(
            identifier=rid,
            decision="APPROVED",
            admin_username="admin",
            reason=f"Duyệt mặc định {rid}",
        )
        assert res["provisioning"]["status"] == "VERIFIED_READY"
        cfg = res["request"]["latest_approval"]["approved_config"]
        assert cfg["host_id"] == "workstation"
        assert cfg["ram_gb"] == 16
        assert cfg["storage_quota_gb"] == 50
        chosen_pools.append(cfg["pool_id"])

    assert set(chosen_pools) == {"pool-sdb", "pool-sdd"}
    assert "pool-hdd3" not in chosen_pools
    dist_map = {d["pool_id"]: d for d in mram.get_host_pool_distribution("workstation", required_storage_gb=50.0)}
    assert dist_map["pool-hdd3"]["assigned_member_count"] == 0
    assert dist_map["pool-hdd3"]["net_allocatable_gib"] == 500.0
    assert dist_map["pool-sdb"]["assigned_member_count"] + dist_map["pool-sdd"]["assigned_member_count"] == 3

    # Adding a much larger pool (2000 GiB free, 0 members) makes it preferred next by effective free space per member
    storage_service.register_custom_pool(
        "workstation",
        {
            "host_id": "workstation",
            "storage_id": "pool-huge",
            "mount_point": "/mnt/HUGE",
            "filesystem": "ext4",
            "filesystem_uuid": "uuid-huge-4444",
            "capacity_gib": 3000.0,
            "used_gib": 900.0,
            "free_gib": 2100.0,
            "allocatable_gib": 2000.0,
            "eligible": True,
            "read_only": False,
            "mounted": True,
            "is_default": False,
        },
    )
    rec4 = mram.compute_resource_recommendation(
        member_category="STUDENT",
        requested_duration_days=30,
        gpu_requested=False,
    )
    assert rec4["recommended_host_id"] == "workstation"
    assert rec4["recommended_pool_id"] == "pool-huge"


def test_11_zalo_admin_approve_reject_with_applicant_reason_feedback(mram_env, tmp_path):
    from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
    from app.services.zalo_assistant.bot_core import ZaloBotEngine
    from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
    from app.services.zalo_assistant.quota_ledger import QuotaLedger
    from app.services.zalo_assistant.report_generator import ReportGenerator
    from app.services.zalo_assistant.service import zalo_assistant_service
    from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector
    from app.services.zalo_assistant.tool_registry import SREToolRegistry

    client: TestClient = mram_env["client"]
    mram: MramService = mram_env["mram"]
    db = mram_env["db"]

    lec = mram.create_web_member_account(
        {"username": "gv_zalo_dec", "full_name": "GV Zalo Dec", "member_code": "GV110", "member_category": "LECTURER"}
    )
    lec_id = lec["profile"]["member_id"]

    sv1 = mram.create_web_member_account(
        {
            "username": "sv_zalo_app",
            "full_name": "Sinh Viên Được Duyệt",
            "member_code": "SV111",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec_id,
        }
    )
    sv2 = mram.create_web_member_account(
        {
            "username": "sv_zalo_rej",
            "full_name": "Sinh Viên Bị Từ Chối",
            "member_code": "SV112",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec_id,
        }
    )
    sv3 = mram.create_web_member_account(
        {
            "username": "sv_zalo_nl",
            "full_name": "Sinh Viên NL",
            "member_code": "SV113",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec_id,
        }
    )

    # Link verified Zalo identities for Admin and SV1, SV2
    zalo_assistant_service.identity_rbac.bind_daily_brief_admin(
        zalo_user_id="zalo-adm-01",
        username="admin",
        member_id="mem-admin",
        verified_chat_id="zalo-adm-01",
    )
    zalo_assistant_service.identity_rbac.admin_bind_identity(
        zalo_user_id="zalo-sv1-01",
        username="sv_zalo_app",
        member_id=sv1["profile"]["member_id"],
        role="RESEARCHER",
        display_name="Sinh Viên 1",
    )
    zalo_assistant_service.identity_rbac.admin_bind_identity(
        zalo_user_id="zalo-sv2-02",
        username="sv_zalo_rej",
        member_id=sv2["profile"]["member_id"],
        role="RESEARCHER",
        display_name="Sinh Viên 2",
    )

    bot_dir = str(tmp_path / "bot_isolated")
    os.makedirs(bot_dir, exist_ok=True)
    rep_gen = ReportGenerator(bot_dir, db_manager=db)
    tel_col = UnifiedTelemetryCollector(bot_dir, db_manager=db)
    inc_eng = UnifiedIncidentEngine(bot_dir, db_manager=db)
    tool_reg = SREToolRegistry(
        bot_dir,
        db_manager=db,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        report_generator=rep_gen,
    )
    bot = ZaloBotEngine(
        quota_ledger=QuotaLedger(bot_dir),
        ai_client=AIGatewayClient(),
        report_generator=rep_gen,
        authorized_users=["zalo-adm-01", "zalo-sv1-01", "zalo-sv2-02"],
        allowed_chat_ids=["zalo-adm-01", "zalo-sv1-01", "zalo-sv2-02", "group-lab-99"],
        identity_rbac=zalo_assistant_service.identity_rbac,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        tool_registry=tool_reg,
    )

    req1 = mram.submit_ssh_request(
        username="sv_zalo_app",
        payload={
            "primary_supervisor_id": lec_id,
            "project_title": "AIoT Camera Edge",
            "registration_reason": "Xin cấp máy chủ workstation để train YOLOv8 qua SSH.",
            "ssh_purpose": "Train model",
            "expected_tasks": "train",
            "gpu_requested": True,
            "policy_accepted": True,
        },
    )["request"]["id"]
    req2 = mram.submit_ssh_request(
        username="sv_zalo_rej",
        payload={
            "primary_supervisor_id": lec_id,
            "project_title": "Incomplete Request",
            "registration_reason": "Xin thử nghiệm máy chủ nhưng chưa ghi rõ kế hoạch nghiên cứu.",
            "ssh_purpose": "Test",
            "expected_tasks": "test",
            "policy_accepted": True,
        },
    )["request"]["id"]
    req3 = mram.submit_ssh_request(
        username="sv_zalo_nl",
        payload={
            "primary_supervisor_id": lec_id,
            "project_title": "Natural Language Approval",
            "registration_reason": "Kiểm tra duyệt qua câu tiếng Việt tự nhiên của Admin trên Zalo.",
            "ssh_purpose": "Train LLM",
            "expected_tasks": "train",
            "policy_accepted": True,
        },
    )["request"]["id"]

    # Legacy slash commands (/approve, /reject, /needinfo) removed in favor of conversational NLU Agent
    assert "/approve" not in bot.COMMANDS
    assert "/reject" not in bot.COMMANDS
    assert "/needinfo" not in bot.COMMANDS

    # Non-admin student CANNOT trigger conversational approval
    forbid = bot.handle_command(
        "@sv_zalo_app tài khoản này đồng ý duyệt cấp tài nguyên nhé",
        sender_id="zalo-sv1-01",
        chat_id="zalo-sv1-01",
        source="zalo",
    )
    assert forbid["success"] is False
    assert forbid["error"] == "FORBIDDEN"

    # Admin CANNOT approve inside a group chat
    grp_block = bot.handle_command(
        "@sv_zalo_app đồng ý duyệt cấp tài nguyên nhé",
        sender_id="zalo-adm-01",
        chat_id="group-lab-99",
        source="zalo",
    )
    assert "nhóm chat" in grp_block["reply"]

    # Admin checks /requests and sees balanced workstation recommendation + natural-language prompt hints
    reqs_msg = bot.handle_command("/requests", sender_id="zalo-adm-01", chat_id="zalo-adm-01", source="zalo")
    assert req1 in reqs_msg["reply"]
    assert "workstation" in reqs_msg["reply"]
    assert "không cần dùng lệnh cứng" in reqs_msg["reply"]

    # Scenario 1: Natural-language REJECT ("@name tài khoản tôi từ chối cấp vì lý do ...") -> 1-step confirm -> executed
    rej_reason = "Chưa ghi rõ kế hoạch thí nghiệm và khối lượng dữ liệu sử dụng"
    rej_turn1 = bot.handle_command(
        f"@sv_zalo_rej tài khoản tôi từ chối cấp vì lý do {rej_reason}",
        sender_id="zalo-adm-01",
        chat_id="zalo-adm-01",
        source="zalo",
    )
    assert rej_turn1["success"] is True
    assert "xác nhận lại 1 lần nữa" in rej_turn1["reply"]
    assert rej_reason in rej_turn1["reply"]
    # Request MUST remain PENDING_APPROVAL until Admin confirms
    assert mram.get_request_detail(req2, viewer_username="admin", is_admin=True)["status"] == "PENDING_APPROVAL"

    rej_confirm = bot.handle_command(
        "Đúng rồi, xác nhận nhé",
        sender_id="zalo-adm-01",
        chat_id="zalo-adm-01",
        source="zalo",
    )
    assert rej_confirm["success"] is True
    assert "ĐÃ TỪ CHỐI ĐƠN SSH" in rej_confirm["reply"]

    sv2_tok = issue_token("sv_zalo_rej", "RESEARCHER", member_id=sv2["profile"]["member_id"], ttl=3600)
    sv2_reqs = client.get("/api/v1/me/ssh-requests", headers={"Authorization": f"Bearer {sv2_tok}"}).json()["requests"]
    assert sv2_reqs[0]["status"] == "REJECTED"
    assert sv2_reqs[0]["decision_reason"] == rej_reason

    from app.db.models import NotificationOutboxRecord

    with db.session_scope() as session:
        sv2_notif = session.execute(
            select(NotificationOutboxRecord).where(
                NotificationOutboxRecord.recipient_id == "zalo-sv2-02",
                NotificationOutboxRecord.event_type == "MRAM_SSH_REQUEST_REJECTED",
            )
        ).scalar_one()
        sv2_msg = sv2_notif.message_text
    assert rej_reason in sv2_msg
    assert "/dashboard/researcher/ssh-request" in sv2_msg

    # Scenario 2: Missing prerequisite (reason omitted) -> Agent asks back -> Admin supplies reason -> Edit -> Cancel -> Fuzzy full-name Approve
    prereq_turn1 = bot.handle_command(
        "@sv_zalo_nl tài khoản này tôi từ chối cấp nhé",
        sender_id="zalo-adm-01",
        chat_id="zalo-adm-01",
        source="zalo",
    )
    assert "Điều kiện tiên quyết còn thiếu" in prereq_turn1["reply"]
    assert "lý do từ chối" in prereq_turn1["reply"]

    prereq_turn2 = bot.handle_command(
        "Vì đề tài chưa có xác nhận của giảng viên hướng dẫn",
        sender_id="zalo-adm-01",
        chat_id="zalo-adm-01",
        source="zalo",
    )
    assert "xác nhận lại 1 lần nữa" in prereq_turn2["reply"]
    assert "đề tài chưa có xác nhận của giảng viên hướng dẫn" in prereq_turn2["reply"].lower()

    edit_turn = bot.handle_command(
        "Sửa lý do thành Đề tài chưa ghi rõ bộ dữ liệu thử nghiệm",
        sender_id="zalo-adm-01",
        chat_id="zalo-adm-01",
        source="zalo",
    )
    assert "Đề tài chưa ghi rõ bộ dữ liệu thử nghiệm" in edit_turn["reply"]

    cancel_turn = bot.handle_command(
        "Hủy",
        sender_id="zalo-adm-01",
        chat_id="zalo-adm-01",
        source="zalo",
    )
    assert "Đã hủy" in cancel_turn["reply"]
    assert mram.get_request_detail(req3, viewer_username="admin", is_admin=True)["status"] == "PENDING_APPROVAL"

    # Now approve sv_zalo_nl naturally by Vietnamese full name -> Agent resolves + asks confirmation 1 time -> OK
    fuzzy_turn1 = bot.handle_command(
        "Đồng ý duyệt cấp tài nguyên cho bạn Sinh Viên NL nhé",
        sender_id="zalo-adm-01",
        chat_id="zalo-adm-01",
        source="zalo",
    )
    assert "xác nhận lại 1 lần nữa" in fuzzy_turn1["reply"]
    assert "sv_zalo_nl" in fuzzy_turn1["reply"]
    assert "50 GiB" in fuzzy_turn1["reply"]
    assert "16 GiB" in fuzzy_turn1["reply"]

    fuzzy_confirm = bot.handle_command(
        "OK duyệt đi",
        sender_id="zalo-adm-01",
        chat_id="zalo-adm-01",
        source="zalo",
    )
    assert fuzzy_confirm["success"] is True
    assert "ĐÃ PHÊ DUYỆT & CẤP PHÁT" in fuzzy_confirm["reply"]
    detail3 = mram.get_request_detail(req3, viewer_username="admin", is_admin=True)
    assert detail3["status"] == "VERIFIED_READY"

    # Scenario 3: Approve with Student RAM > 16 GiB requested in prose -> prerequisite clamp notice + confirm -> Zalo notification with reason
    app_reason = "chạy mô hình AIoT lớn"
    app_turn1 = bot.handle_command(
        f"Duyệt cấp cho @sv_zalo_app 32GB RAM vì {app_reason}",
        sender_id="zalo-adm-01",
        chat_id="zalo-adm-01",
        source="zalo",
    )
    assert "tối đa 16 GiB RAM" in app_turn1["reply"]
    assert "xác nhận lại 1 lần nữa" in app_turn1["reply"]
    assert app_reason in app_turn1["reply"]

    app_confirm = bot.handle_command(
        "Xác nhận",
        sender_id="zalo-adm-01",
        chat_id="zalo-adm-01",
        source="zalo",
    )
    assert app_confirm["success"] is True
    assert "ĐÃ PHÊ DUYỆT & CẤP PHÁT" in app_confirm["reply"]
    assert "16 GiB RAM" in app_confirm["reply"]
    assert "50 GiB Ổ cứng" in app_confirm["reply"]

    sv1_tok = issue_token("sv_zalo_app", "RESEARCHER", member_id=sv1["profile"]["member_id"], ttl=3600)
    sv1_reqs = client.get("/api/v1/me/ssh-requests", headers={"Authorization": f"Bearer {sv1_tok}"}).json()["requests"]
    assert sv1_reqs[0]["status"] == "VERIFIED_READY"
    assert sv1_reqs[0]["decision_reason"] == app_reason
    assert sv1_reqs[0]["latest_approval"]["approved_config"]["ram_gb"] == 16
    assert sv1_reqs[0]["latest_approval"]["approved_config"]["storage_quota_gb"] == 50

    with db.session_scope() as session:
        sv1_notif = session.execute(
            select(NotificationOutboxRecord).where(
                NotificationOutboxRecord.recipient_id == "zalo-sv1-01",
                NotificationOutboxRecord.event_type == "MRAM_SSH_REQUEST_APPROVED",
            )
        ).scalar_one()
        sv1_msg = sv1_notif.message_text
    assert app_reason in sv1_msg
    assert "workstation" in sv1_msg
    assert "/dashboard/researcher/access" in sv1_msg


def test_12_zalo_agent_nlu_edge_cases_and_revocation(mram_env, tmp_path):
    from app.db.models import V4AssistantTaskRecord
    from app.services.assistant_v4.config import AssistantV4Config
    from app.services.assistant_v4.facade import AssistantV4Facade
    from app.services.assistant_v4.task_scheduler import WatchSpec
    from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
    from app.services.zalo_assistant.bot_core import ZaloBotEngine
    from app.services.zalo_assistant.identity_rbac import ZaloPrincipal
    from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
    from app.services.zalo_assistant.quota_ledger import QuotaLedger
    from app.services.zalo_assistant.report_generator import ReportGenerator
    from app.services.zalo_assistant.service import zalo_assistant_service
    from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector
    from app.services.zalo_assistant.tool_registry import SREToolRegistry

    mram: MramService = mram_env["mram"]
    db = mram_env["db"]

    lec = mram.create_web_member_account(
        {"username": "gv_zalo_edge", "full_name": "GV Zalo Edge", "member_code": "GV210", "member_category": "LECTURER"}
    )
    lec_id = lec["profile"]["member_id"]
    sv1 = mram.create_web_member_account(
        {
            "username": "sv_zalo_edge1",
            "full_name": "Sinh Viên Edge 1",
            "member_code": "SV211",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec_id,
        }
    )
    sv2 = mram.create_web_member_account(
        {
            "username": "sv_zalo_edge2",
            "full_name": "Sinh Viên Edge 2",
            "member_code": "SV212",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec_id,
        }
    )

    zalo_assistant_service.identity_rbac.bind_daily_brief_admin(
        zalo_user_id="zalo-adm-edge",
        username="admin",
        member_id="mem-admin",
        verified_chat_id="zalo-adm-edge",
    )
    edge_link = zalo_assistant_service.identity_rbac.admin_bind_identity(
        zalo_user_id="zalo-sv-edge1",
        username="sv_zalo_edge1",
        member_id=sv1["profile"]["member_id"],
        role="RESEARCHER",
        display_name="SV Edge 1",
    )

    req1 = mram.submit_ssh_request(
        username="sv_zalo_edge1",
        payload={
            "primary_supervisor_id": lec_id,
            "project_title": "Edge Case Check 1",
            "registration_reason": "Xin cấp workstation để kiểm thử NLU edge case 1.",
            "ssh_purpose": "Test",
            "expected_tasks": "test",
            "policy_accepted": True,
        },
    )["request"]["id"]
    req2 = mram.submit_ssh_request(
        username="sv_zalo_edge2",
        payload={
            "primary_supervisor_id": lec_id,
            "project_title": "Edge Case Check 2",
            "registration_reason": "Xin cấp workstation để kiểm thử NLU edge case 2.",
            "ssh_purpose": "Test",
            "expected_tasks": "test",
            "policy_accepted": True,
        },
    )["request"]["id"]

    bot_dir = str(tmp_path / "bot_edge")
    os.makedirs(bot_dir, exist_ok=True)
    rep_gen = ReportGenerator(bot_dir, db_manager=db)
    tel_col = UnifiedTelemetryCollector(bot_dir, db_manager=db)
    inc_eng = UnifiedIncidentEngine(bot_dir, db_manager=db)
    tool_reg = SREToolRegistry(
        bot_dir,
        db_manager=db,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        report_generator=rep_gen,
    )
    ai_cli = AIGatewayClient(base_url="http://127.0.0.1:0")
    v4 = AssistantV4Facade(
        data_dir=bot_dir,
        db_manager=db,
        ai_client=ai_cli,
        identity_rbac=zalo_assistant_service.identity_rbac,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        config=AssistantV4Config(enabled=True, task_manager=True, natural_language=True),
    )
    bot = ZaloBotEngine(
        quota_ledger=QuotaLedger(bot_dir),
        ai_client=ai_cli,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        report_generator=rep_gen,
        tool_registry=tool_reg,
        identity_rbac=zalo_assistant_service.identity_rbac,
    )
    bot.v4_facade = v4

    # 1. Lookup/status questions with @username must NOT trigger APPROVED draft
    assert ZaloBotEngine._parse_natural_ssh_decision_intent("@sv_zalo_edge1 đã được duyệt chưa?") is None
    assert ZaloBotEngine._parse_natural_ssh_decision_intent("@sv_zalo_edge1 đơn chờ duyệt thế nào rồi?") is None
    assert (
        ZaloBotEngine._parse_natural_ssh_decision_intent("Kiểm tra giúp tôi @sv_zalo_edge1 có đơn nào chờ duyệt không?")
        is None
    )
    lookup_res = bot.handle_command(
        "@sv_zalo_edge1 đã được duyệt chưa?",
        sender_id="admin",
        chat_id="zalo-adm-edge",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert lookup_res["success"] is True
    assert "xác nhận lại 1 lần nữa" not in lookup_res["reply"]
    assert mram.get_request_detail(req1, viewer_username="admin", is_admin=True)["status"] == "PENDING_APPROVAL"

    # 2. Accented "đừng" / "đừng duyệt" must cancel draft instead of confirming ("đúng" -> "dung")
    assert ZaloBotEngine._is_affirmative_confirmation("Đúng rồi") is True
    assert ZaloBotEngine._is_affirmative_confirmation("đừng") is False
    assert ZaloBotEngine._is_affirmative_confirmation("đừng duyệt") is False
    assert ZaloBotEngine._is_cancel_phrase("đừng") is True
    assert ZaloBotEngine._is_cancel_phrase("đừng duyệt") is True

    d1 = bot.handle_command(
        "duyệt cấp cho @sv_zalo_edge1",
        sender_id="zalo-adm-edge",
        chat_id="zalo-adm-edge",
        source="zalo",
    )
    assert "xác nhận lại 1 lần nữa" in d1["reply"]
    c1 = bot.handle_command(
        "đừng duyệt",
        sender_id="zalo-adm-edge",
        chat_id="zalo-adm-edge",
        source="zalo",
    )
    assert "Đã hủy" in c1["reply"]
    assert mram.get_request_detail(req1, viewer_username="admin", is_admin=True)["status"] == "PENDING_APPROVAL"

    # 3. Switching decision while in AWAITING_CONFIRMATION keeps current draft target
    d2 = bot.handle_command(
        "duyệt cấp cho @sv_zalo_edge1",
        sender_id="zalo-adm-edge",
        chat_id="zalo-adm-edge",
        source="zalo",
    )
    assert "PHÊ DUYỆT" in d2["reply"]

    sw_rej = bot.handle_command(
        "Đổi sang từ chối vì lý do chưa nộp đề cương nghiên cứu",
        sender_id="zalo-adm-edge",
        chat_id="zalo-adm-edge",
        source="zalo",
    )
    assert "TỪ CHỐI" in sw_rej["reply"]
    assert "sv_zalo_edge1" in sw_rej["reply"]
    assert "chưa nộp đề cương nghiên cứu" in sw_rej["reply"]

    conf_rej = bot.handle_command(
        "Đúng rồi",
        sender_id="zalo-adm-edge",
        chat_id="zalo-adm-edge",
        source="zalo",
    )
    assert "ĐÃ TỪ CHỐI ĐƠN SSH" in conf_rej["reply"]
    assert mram.get_request_detail(req1, viewer_username="admin", is_admin=True)["status"] == "REJECTED"

    # Switching from APPROVED to REJECTED without reason must prompt for rejection reason on same target
    d3 = bot.handle_command(
        "duyệt cấp cho @sv_zalo_edge2",
        sender_id="zalo-adm-edge",
        chat_id="zalo-adm-edge",
        source="zalo",
    )
    assert "sv_zalo_edge2" in d3["reply"]
    sw_need_reason = bot.handle_command(
        "Đổi sang từ chối",
        sender_id="zalo-adm-edge",
        chat_id="zalo-adm-edge",
        source="zalo",
    )
    assert "Điều kiện tiên quyết còn thiếu" in sw_need_reason["reply"]
    assert "sv_zalo_edge2" in sw_need_reason["reply"]
    bot.handle_command("đừng", sender_id="zalo-adm-edge", chat_id="zalo-adm-edge", source="zalo")
    assert mram.get_request_detail(req2, viewer_username="admin", is_admin=True)["status"] == "PENDING_APPROVAL"

    # 4. V4 deterministic fallback displays balanced pool spec & ICT timestamp
    admin_p = ZaloPrincipal("admin", "admin", "zalo-adm-edge", "ADMIN", "Admin", True, True)
    fb = v4._deterministic_v4_tool_fallback(question="Danh sách đơn xin cấp SSH chờ duyệt", principal=admin_p)
    assert fb is not None
    assert "ICT / UTC+7" in fb["reply"]
    assert "workstation/pool-hdd3" in fb["reply"]

    # 5. revoke_link cancels orphaned WAITING_CONDITION watch task immediately
    watch = v4.tasks.create_watch(
        owner_id="sv_zalo_edge1",
        owner_role="RESEARCHER",
        spec=WatchSpec(target_id="workstation", metric="gpu_temperature_c", comparator="lt", threshold=75.0, sustained_seconds=60),
    )
    assert zalo_assistant_service.identity_rbac.revoke_link(edge_link["id"], actor="admin") is True
    with db.session_scope() as session:
        t_row = session.get(V4AssistantTaskRecord, watch["task_id"])
        assert t_row is not None
        assert t_row.status == "CANCELLED"


def test_13_v4_ssh_request_detail_and_followup_lookup(mram_env, tmp_path):
    from app.services.assistant_v4.config import AssistantV4Config
    from app.services.assistant_v4.facade import AssistantV4Facade
    from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
    from app.services.zalo_assistant.bot_core import ZaloBotEngine
    from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
    from app.services.zalo_assistant.quota_ledger import QuotaLedger
    from app.services.zalo_assistant.report_generator import ReportGenerator
    from app.services.zalo_assistant.service import zalo_assistant_service
    from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector
    from app.services.zalo_assistant.tool_registry import SREToolRegistry

    mram: MramService = mram_env["mram"]
    db = mram_env["db"]

    lec = mram.create_web_member_account(
        {"username": "gv_ts_huong", "full_name": "TS. Nguyễn Thu Hương", "member_code": "GV301", "member_category": "LECTURER"}
    )
    lec_id = lec["profile"]["member_id"]
    mram.create_web_member_account(
        {
            "username": "sv_nguyenhuutien",
            "full_name": "Nguyễn Hữu Tiến",
            "member_code": "1671020301",
            "member_category": "STUDENT",
            "department_class": "CNTT16-01",
            "primary_supervisor_id": lec_id,
        }
    )
    mram.submit_ssh_request(
        username="sv_nguyenhuutien",
        payload={
            "primary_supervisor_id": lec_id,
            "project_title": "Nghiên cứu giá trị dự báo tín hiệu tin tức GDELT đối với US EPU",
            "registration_reason": "Cần môi trường GPU A30 để xử lý dữ liệu tin tức GDELT quy mô lớn và huấn luyện mô hình dự báo.",
            "ssh_purpose": "Chạy pipeline tiền xử lý và training PyTorch",
            "expected_tasks": "python train_gdelt.py",
            "gpu_requested": True,
            "gpu_justification": "Huấn luyện FinBERT trên tập GDELT",
            "requested_duration_days": 45,
            "policy_accepted": True,
        },
    )

    bot_dir = str(tmp_path / "bot_v4_lookup")
    os.makedirs(bot_dir, exist_ok=True)
    rep_gen = ReportGenerator(bot_dir, db_manager=db)
    tel_col = UnifiedTelemetryCollector(bot_dir, db_manager=db)
    inc_eng = UnifiedIncidentEngine(bot_dir, db_manager=db)
    tool_reg = SREToolRegistry(
        bot_dir,
        db_manager=db,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        report_generator=rep_gen,
    )
    ai_cli = AIGatewayClient(base_url="http://127.0.0.1:0")
    v4 = AssistantV4Facade(
        data_dir=bot_dir,
        db_manager=db,
        ai_client=ai_cli,
        identity_rbac=zalo_assistant_service.identity_rbac,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        config=AssistantV4Config(enabled=True, task_manager=True, natural_language=True),
    )
    bot = ZaloBotEngine(
        quota_ledger=QuotaLedger(bot_dir),
        ai_client=ai_cli,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        report_generator=rep_gen,
        tool_registry=tool_reg,
        identity_rbac=zalo_assistant_service.identity_rbac,
    )
    bot.v4_facade = v4

    # 1. Admin asks general review list -> includes GVHD + registration reason
    r1 = bot.handle_command(
        "tôi có cần kiểm duyệt ssh nào không",
        sender_id="admin",
        chat_id="admin",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert r1["success"] is True
    assert "Nguyễn Hữu Tiến (@sv_nguyenhuutien" in r1["reply"]
    assert "GVHD: Nguyễn Thu Hương" in r1["reply"]
    assert "Lý do xin cấp: Cần môi trường GPU A30 để xử lý dữ liệu tin tức GDELT" in r1["reply"]
    assert "Tác vụ tính toán nhìn thấy" not in r1["reply"]

    # 2. Follow-up question with typo "ginagr viên" and without "đơn"/"duyệt" uses session context & answers GVHD + reason
    r2 = bot.handle_command(
        "tôi cần nhiều thông tin hơn, ginagr viên do ai hướng dẫn, lý do xin cấp là gì",
        sender_id="admin",
        chat_id="admin",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert r2["success"] is True
    assert "Nguyễn Thu Hương" in r2["reply"]
    assert "Cần môi trường GPU A30 để xử lý dữ liệu tin tức GDELT" in r2["reply"]
    assert "Tác vụ tính toán nhìn thấy" not in r2["reply"]

    # 3. Direct @username lookup returns detailed SSH dossier
    r3 = bot.handle_command(
        "cho tôi xem @sv_nguyenhuutien",
        sender_id="admin",
        chat_id="admin",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert r3["success"] is True
    assert "CHI TIẾT ĐƠN XIN CẤP TÀI NGUYÊN SSH" in r3["reply"]
    assert "Nguyễn Thu Hương" in r3["reply"]
    assert "Cần môi trường GPU A30 để xử lý dữ liệu tin tức GDELT" in r3["reply"]
    assert "Tác vụ tính toán nhìn thấy" not in r3["reply"]

    # 4. Direct reason lookup for @username returns reason & supervisor cleanly
    r4 = bot.handle_command(
        "cho tôi xem lý do xin cấp của @sv_nguyenhuutien",
        sender_id="admin",
        chat_id="admin",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert r4["success"] is True
    assert "CHI TIẾT ĐƠN XIN CẤP TÀI NGUYÊN SSH" in r4["reply"]
    assert "Cần môi trường GPU A30 để xử lý dữ liệu tin tức GDELT" in r4["reply"]
    assert "xác nhận lại 1 lần nữa" not in r4["reply"]


def test_14_natural_language_action_and_context_engine(mram_env, tmp_path):
    from app.services.assistant_v4.config import AssistantV4Config
    from app.services.assistant_v4.facade import AssistantV4Facade
    from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
    from app.services.zalo_assistant.bot_core import ZaloBotEngine
    from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
    from app.services.zalo_assistant.quota_ledger import QuotaLedger
    from app.services.zalo_assistant.report_generator import ReportGenerator
    from app.services.zalo_assistant.service import zalo_assistant_service
    from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector
    from app.services.zalo_assistant.tool_registry import SREToolRegistry

    mram: MramService = mram_env["mram"]
    db = mram_env["db"]

    lec = mram.create_web_member_account(
        {"username": "gv_ctx", "full_name": "PGS.TS. Lê Văn Minh", "member_code": "GV401", "member_category": "LECTURER"}
    )
    lec_id = lec["profile"]["member_id"]
    mram.create_web_member_account(
        {
            "username": "sv_ctx1",
            "full_name": "Phạm Minh Anh",
            "member_code": "SV401",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec_id,
        }
    )
    mram.create_web_member_account(
        {
            "username": "sv_ctx2",
            "full_name": "Trần Quốc Bảo",
            "member_code": "SV402",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec_id,
        }
    )
    req1 = mram.submit_ssh_request(
        username="sv_ctx1",
        payload={
            "primary_supervisor_id": lec_id,
            "project_title": "AIoT Edge Vision",
            "registration_reason": "Xin cấp SSH để huấn luyện nhận dạng ảnh công nghiệp.",
            "ssh_purpose": "Train vision model",
            "expected_tasks": "python train.py",
            "policy_accepted": True,
        },
    )["request"]["id"]
    mram.submit_ssh_request(
        username="sv_ctx2",
        payload={
            "primary_supervisor_id": lec_id,
            "project_title": "Speech Anti-Spoofing",
            "registration_reason": "Xin cấp SSH để chạy đánh giá âm thanh.",
            "ssh_purpose": "Eval audio",
            "expected_tasks": "python eval.py",
            "policy_accepted": True,
        },
    )

    bot_dir = str(tmp_path / "bot_nl_actions")
    os.makedirs(bot_dir, exist_ok=True)
    rep_gen = ReportGenerator(bot_dir, db_manager=db)
    tel_col = UnifiedTelemetryCollector(bot_dir, db_manager=db)
    inc_eng = UnifiedIncidentEngine(bot_dir, db_manager=db)
    tool_reg = SREToolRegistry(
        bot_dir,
        db_manager=db,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        report_generator=rep_gen,
    )
    ai_cli = AIGatewayClient(base_url="http://127.0.0.1:0")
    v4 = AssistantV4Facade(
        data_dir=bot_dir,
        db_manager=db,
        ai_client=ai_cli,
        identity_rbac=zalo_assistant_service.identity_rbac,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        config=AssistantV4Config(enabled=True, task_manager=True, natural_language=True),
    )
    bot = ZaloBotEngine(
        quota_ledger=QuotaLedger(bot_dir),
        ai_client=ai_cli,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        report_generator=rep_gen,
        tool_registry=tool_reg,
        identity_rbac=zalo_assistant_service.identity_rbac,
    )
    bot.v4_facade = v4

    # 1. Greeting & capability help
    g1 = bot.handle_command("alooo", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN")
    assert g1["success"] is True
    assert "Xin chào @admin" in g1["reply"]
    assert "tôi có cần kiểm duyệt ssh nào không" in g1["reply"]

    g2 = bot.handle_command(
        "bạn có thể làm gì cho tôi", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert g2["success"] is True
    assert "Xét duyệt SSH" in g2["reply"]

    # 2. Contextual pronoun target resolution ("cho tôi xem @sv_ctx1" -> "duyệt cho bạn này nhé" -> "đừng duyệt")
    look = bot.handle_command(
        "cho tôi xem @sv_ctx1", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert look["success"] is True
    assert req1 in look["reply"]

    pron_approve = bot.handle_command(
        "duyệt cho bạn này nhé", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert pron_approve["success"] is True
    assert "xác nhận lại 1 lần nữa" in pron_approve["reply"]
    assert "sv_ctx1" in pron_approve["reply"]

    cancel_res = bot.handle_command(
        "đừng duyệt", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert "Đã hủy" in cancel_res["reply"]
    assert mram.get_request_detail(req1, viewer_username="admin", is_admin=True)["status"] == "PENDING_APPROVAL"

    # 3. Multi-metric watch + time-based reminder + natural cancel
    w_cpu = bot.handle_command(
        "báo tôi khi CPU workstation vượt 85% trong 5 phút",
        sender_id="admin",
        chat_id="admin",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert w_cpu["success"] is True
    assert "cpu_percent" in w_cpu["reply"]
    assert "85.0%" in w_cpu["reply"]

    rem = bot.handle_command(
        "nhắc tôi sau 30 phút kiểm tra lại job",
        sender_id="admin",
        chat_id="admin",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert rem["success"] is True
    assert "Đã đặt lịch nhắc" in rem["reply"]
    assert "kiểm tra lại job" in rem["reply"]

    can_task = bot.handle_command(
        "hủy cảnh báo vừa tạo",
        sender_id="admin",
        chat_id="admin",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert can_task["success"] is True
    assert "Đã hủy theo dõi / lịch nhắc" in can_task["reply"]
    assert "CANCELLED" in can_task["reply"]

    # 4. Natural incident acknowledge + daily report creation
    inc, _ = inc_eng.upsert_incident(
        event_type="GPU_THERMAL_HIGH",
        severity="P1",
        source="gpu",
        resource_id="workstation:g0",
        fingerprint="nl-ack-test-1",
        title="GPU Workstation quá nhiệt",
        owner_id="admin",
    )
    ack_nl = bot.handle_command(
        "xác nhận sự cố đang mở", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert ack_nl["success"] is True
    assert inc["incident_id"] in ack_nl["reply"]
    assert inc_eng.get_incident(inc["incident_id"])["status"] == "ACKNOWLEDGED"

    rep_nl = bot.handle_command(
        "tạo báo cáo ngày hôm nay", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert rep_nl["success"] is True
    assert "BÁO CÁO" in rep_nl["reply"].upper() or "WORKSTATION" in rep_nl["reply"].upper()

    # 5. Specialized queries & command history
    q_res = bot.handle_command(
        "hạn mức tin nhắn zalo còn bao nhiêu", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert "HẠN MỨC TIN NHẮN NỘI BỘ ZALO" in q_res["reply"]

    hist_nl = bot.handle_command(
        "tôi đã ra các lệnh nào", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert hist_nl["success"] is True
    assert "LỊCH SỬ CÁC LỆNH & CÂU HỎI GẦN ĐÂY" in hist_nl["reply"]
    assert "cho tôi xem @sv_ctx1" in hist_nl["reply"]
    assert "báo tôi khi CPU workstation vượt 85% trong 5 phút" in hist_nl["reply"]


def test_15_go_agent_uuid_pool_alias_resolution_and_nl_override(mram_env, tmp_path):
    from app.services.storage_inventory_service import storage_service
    from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
    from app.services.zalo_assistant.bot_core import ZaloBotEngine
    from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
    from app.services.zalo_assistant.quota_ledger import QuotaLedger
    from app.services.zalo_assistant.report_generator import ReportGenerator
    from app.services.zalo_assistant.service import zalo_assistant_service
    from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector
    from app.services.zalo_assistant.tool_registry import SREToolRegistry

    mram: MramService = mram_env["mram"]
    db = mram_env["db"]

    uuid_sdd = "5ce32377-6072-4c07-a066-807e89a5d48b"
    uuid_sdb = "159bacc4-38ae-4adb-bf25-7a5091c308ca"
    uuid_hdd3 = "d5111439-d134-4ba6-93ef-6268d738f035"
    uuid_root = "d24c3324-936a-4000-88d3-ef92d7759116"

    prev_custom = storage_service._custom_pools.get("workstation")
    try:
        storage_service._custom_pools["workstation"] = [
            {
                "host_id": "workstation",
                "storage_id": uuid_sdd,
                "filesystem_uuid": uuid_sdd,
                "device": "/dev/sdd",
                "mount_point": f"/media/aiot-ws1/{uuid_sdd}",
                "filesystem": "ext4",
                "label": None,
                "capacity_gib": 915.8,
                "used_gib": 55.4,
                "free_gib": 860.4,
                "allocatable_gib": 768.8,
                "reserved_gib": 91.6,
                "eligible": True,
                "read_only": False,
                "mounted": True,
            },
            {
                "host_id": "workstation",
                "storage_id": uuid_sdb,
                "filesystem_uuid": uuid_sdb,
                "device": "/dev/sdb",
                "mount_point": f"/media/aiot-ws1/{uuid_sdb}",
                "filesystem": "ext4",
                "label": None,
                "capacity_gib": 915.8,
                "used_gib": 144.2,
                "free_gib": 771.6,
                "allocatable_gib": 680.0,
                "reserved_gib": 91.6,
                "eligible": True,
                "read_only": False,
                "mounted": True,
            },
            {
                "host_id": "workstation",
                "storage_id": uuid_hdd3,
                "filesystem_uuid": uuid_hdd3,
                "device": "/dev/sdc",
                "mount_point": "/mnt/HDD3",
                "filesystem": "ext4",
                "label": None,
                "capacity_gib": 3666.5,
                "used_gib": 1515.0,
                "free_gib": 2151.5,
                "allocatable_gib": 1784.9,
                "reserved_gib": 366.6,
                "eligible": True,
                "read_only": False,
                "mounted": True,
            },
            {
                "host_id": "workstation",
                "storage_id": uuid_root,
                "filesystem_uuid": uuid_root,
                "device": "/dev/sda2",
                "mount_point": "/",
                "filesystem": "ext4",
                "label": None,
                "capacity_gib": 832.8,
                "used_gib": 73.5,
                "free_gib": 759.3,
                "allocatable_gib": 676.0,
                "reserved_gib": 83.3,
                "eligible": False,
                "read_only": False,
                "mounted": True,
            },
        ]

        # 1. Label enrichment & alias lookup for UUID pools
        pools = {p["storage_id"]: p for p in storage_service.get_storage_pools("workstation")}
        assert "Ổ đĩa Nghiên cứu AI HDD3" in (pools[uuid_hdd3].get("label") or "")
        assert "Ổ đĩa Lưu trữ Thứ cấp SDD" in (pools[uuid_sdd].get("label") or "")
        assert "Ổ đĩa Hệ thống Root" in (pools[uuid_root].get("label") or "")

        assert storage_service.get_storage_pool_by_id("workstation", "pool-hdd3")["storage_id"] == uuid_hdd3
        assert storage_service.get_storage_pool_by_id("workstation", "hdd3")["storage_id"] == uuid_hdd3
        assert storage_service.get_storage_pool_by_id("workstation", "/mnt/HDD3")["storage_id"] == uuid_hdd3
        assert storage_service.get_storage_pool_by_id("workstation", "pool-sdd")["storage_id"] == uuid_sdd
        assert storage_service.get_storage_pool_by_id("workstation", "sdd")["storage_id"] == uuid_sdd
        assert storage_service.get_storage_pool_by_id("workstation", "/dev/sdb")["storage_id"] == uuid_sdb

        # 2. Natural-language Zalo approval targeting specific disk alias canonicalizes to live UUID
        lec = mram.create_web_member_account(
            {"username": "gv_disk1", "full_name": "TS. Nguyễn Văn Hùng", "member_code": "GV501", "member_category": "LECTURER"}
        )
        lec_id = lec["profile"]["member_id"]
        mram.create_web_member_account(
            {
                "username": "sv_disk1",
                "full_name": "Lê Minh Tâm",
                "member_code": "SV501",
                "member_category": "STUDENT",
                "primary_supervisor_id": lec_id,
            }
        )
        req_id = mram.submit_ssh_request(
            username="sv_disk1",
            payload={
                "primary_supervisor_id": lec_id,
                "project_title": "Storage Alias Verification",
                "registration_reason": "Xin cấp SSH trên workstation để chạy thử nghiệm mô hình AI.",
                "ssh_purpose": "Run experiments",
                "expected_tasks": "python run.py",
                "policy_accepted": True,
            },
        )["request"]["id"]

        cfg_hdd3 = mram.resolve_default_approval_config(req_id, overrides={"pool_id": "pool-hdd3"})
        assert cfg_hdd3["pool_id"] == uuid_hdd3

        bot_dir = str(tmp_path / "bot_disk_alias")
        os.makedirs(bot_dir, exist_ok=True)
        rep_gen = ReportGenerator(bot_dir, db_manager=db)
        tel_col = UnifiedTelemetryCollector(bot_dir, db_manager=db)
        inc_eng = UnifiedIncidentEngine(bot_dir, db_manager=db)
        tool_reg = SREToolRegistry(
            bot_dir,
            db_manager=db,
            telemetry_collector=tel_col,
            incident_engine=inc_eng,
            report_generator=rep_gen,
        )
        bot = ZaloBotEngine(
            quota_ledger=QuotaLedger(bot_dir),
            ai_client=AIGatewayClient(base_url="http://127.0.0.1:0"),
            telemetry_collector=tel_col,
            incident_engine=inc_eng,
            report_generator=rep_gen,
            tool_registry=tool_reg,
            identity_rbac=zalo_assistant_service.identity_rbac,
        )

        ap1 = bot.handle_command(
            "@sv_disk1 đồng ý duyệt cấp tài nguyên vào ổ HDD3",
            sender_id="admin",
            chat_id="admin",
            source="dashboard",
            principal_role="ADMIN",
        )
        assert ap1["success"] is True
        assert uuid_hdd3 in ap1["reply"]
        assert "/mnt/HDD3" in ap1["reply"]

        bot.handle_command("Hủy", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN")

        ap2 = bot.handle_command(
            "@sv_disk1 duyệt cấp trên ổ sdd",
            sender_id="admin",
            chat_id="admin",
            source="dashboard",
            principal_role="ADMIN",
        )
        assert ap2["success"] is True
        assert uuid_sdd in ap2["reply"]
    finally:
        if prev_custom is None:
            storage_service._custom_pools.pop("workstation", None)
        else:
            storage_service._custom_pools["workstation"] = prev_custom


def test_16_multi_round_adversarial_zalo_verification(mram_env, tmp_path):
    from app.services.assistant_v4.config import AssistantV4Config
    from app.services.assistant_v4.facade import AssistantV4Facade
    from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
    from app.services.zalo_assistant.bot_core import ZaloBotEngine
    from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
    from app.services.zalo_assistant.quota_ledger import QuotaLedger
    from app.services.zalo_assistant.report_generator import ReportGenerator
    from app.services.zalo_assistant.service import zalo_assistant_service
    from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector
    from app.services.zalo_assistant.tool_registry import SREToolRegistry

    mram: MramService = mram_env["mram"]
    db = mram_env["db"]

    uuid_sdd = "5ce32377-6072-4c07-a066-807e89a5d48b"
    uuid_hdd3 = "d5111439-d134-4ba6-93ef-6268d738f035"
    storage_service._custom_pools["workstation"] = [
        {
            "host_id": "workstation",
            "storage_id": uuid_sdd,
            "filesystem_uuid": uuid_sdd,
            "device": "/dev/sdd",
            "mount_point": f"/media/aiot-ws1/{uuid_sdd}",
            "filesystem": "ext4",
            "label": None,
            "capacity_gib": 915.8,
            "used_gib": 55.4,
            "free_gib": 860.4,
            "allocatable_gib": 768.8,
            "reserved_gib": 91.6,
            "eligible": True,
            "read_only": False,
            "mounted": True,
        },
        {
            "host_id": "workstation",
            "storage_id": uuid_hdd3,
            "filesystem_uuid": uuid_hdd3,
            "device": "/dev/sdc",
            "mount_point": "/mnt/HDD3",
            "filesystem": "ext4",
            "label": None,
            "capacity_gib": 3666.5,
            "used_gib": 1515.0,
            "free_gib": 2151.5,
            "allocatable_gib": 1784.9,
            "reserved_gib": 366.6,
            "eligible": True,
            "read_only": False,
            "mounted": True,
        },
    ]

    lec = mram.create_web_member_account(
        {"username": "gv_adv", "full_name": "TS. Đỗ Minh Tuấn", "member_code": "GV950", "member_category": "LECTURER"}
    )
    lec_id = lec["profile"]["member_id"]
    mram.create_web_member_account(
        {
            "username": "sv_adv1",
            "full_name": "Nguyễn Văn An",
            "member_code": "SV951",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec_id,
        }
    )
    mram.create_web_member_account(
        {
            "username": "sv_adv2",
            "full_name": "Lê Thị Bình",
            "member_code": "SV952",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec_id,
        }
    )
    r_alpha = mram.submit_ssh_request(
        username="sv_adv1",
        payload={
            "primary_supervisor_id": lec_id,
            "project_title": "Mô hình LLM Tiếng Việt",
            "registration_reason": "Cần GPU A30 để fine-tune mô hình ngôn ngữ lớn.",
            "ssh_purpose": "Fine-tune LLM",
            "expected_tasks": "python train_llm.py",
            "gpu_requested": True,
            "policy_accepted": True,
        },
    )["request"]["id"]
    r_beta = mram.submit_ssh_request(
        username="sv_adv2",
        payload={
            "primary_supervisor_id": lec_id,
            "project_title": "Hệ thống IoT Nông nghiệp",
            "registration_reason": "Xử lý dữ liệu cảm biến thời gian thực trên workstation.",
            "ssh_purpose": "Data processing",
            "expected_tasks": "python iot.py",
            "gpu_requested": False,
            "policy_accepted": True,
        },
    )["request"]["id"]

    bot_dir = str(tmp_path / "bot_adversarial")
    os.makedirs(bot_dir, exist_ok=True)
    rep_gen = ReportGenerator(bot_dir, db_manager=db)
    tel_col = UnifiedTelemetryCollector(bot_dir, db_manager=db)
    inc_eng = UnifiedIncidentEngine(bot_dir, db_manager=db)
    inc1, _ = inc_eng.upsert_incident(
        event_type="GPU_TEMP", severity="P1", source="gpu", resource_id="ws:g0", fingerprint="adv-i1", title="GPU A30 nóng", owner_id="admin"
    )
    inc2, _ = inc_eng.upsert_incident(
        event_type="DISK_WARN", severity="P1", source="disk", resource_id="ws:d0", fingerprint="adv-i2", title="Disk sắp đầy", owner_id="admin"
    )
    tool_reg = SREToolRegistry(
        bot_dir,
        db_manager=db,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        report_generator=rep_gen,
    )
    ai_cli = AIGatewayClient(base_url="http://127.0.0.1:0")
    v4 = AssistantV4Facade(
        data_dir=bot_dir,
        db_manager=db,
        ai_client=ai_cli,
        identity_rbac=zalo_assistant_service.identity_rbac,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        config=AssistantV4Config(enabled=True, task_manager=True, natural_language=True),
    )
    bot = ZaloBotEngine(
        quota_ledger=QuotaLedger(bot_dir),
        ai_client=ai_cli,
        telemetry_collector=tel_col,
        incident_engine=inc_eng,
        report_generator=rep_gen,
        tool_registry=tool_reg,
        identity_rbac=zalo_assistant_service.identity_rbac,
    )
    bot.v4_facade = v4

    # Round 1a: Multi-turn single-request context preservation across follow-up question + override
    det = bot.handle_command(
        "chi tiết đơn của Nguyễn Văn An", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert r_alpha in det["reply"]

    fup = bot.handle_command(
        "bạn này do ai hướng dẫn và xin làm đề tài gì?",
        sender_id="admin",
        chat_id="admin",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert r_alpha in fup["reply"]
    assert "Đỗ Minh Tuấn" in fup["reply"]

    ov1 = bot.handle_command(
        "duyệt cho bạn này nhưng đổi sang 8 cpu và ổ sdd",
        sender_id="admin",
        chat_id="admin",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert ov1.get("v4_intent") == "ADMIN_DECISION_CONFIRMATION"
    assert "sv_adv1" in ov1["reply"]
    assert "8 vCPU" in ov1["reply"]
    assert uuid_sdd in ov1["reply"]

    ov2 = bot.handle_command("đổi lại thành 6 cpu thôi", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN")
    assert ov2.get("v4_intent") == "ADMIN_DECISION_CONFIRMATION"
    assert "6 vCPU" in ov2["reply"]
    assert bot.handle_command("Hủy", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN").get("v4_intent") == "ADMIN_DECISION_CANCELLED"

    # Round 1b: Cancel phrase containing decision verb ("thôi đừng từ chối nữa") cancels draft instead of sticking to REJECTED
    bot.handle_command("từ chối đơn của Lê Thị Bình", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN")
    rej = bot.handle_command(
        "vì đề tài chưa cần dùng máy chủ GPU lúc này", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert rej.get("v4_intent") == "ADMIN_DECISION_CONFIRMATION"
    assert "sv_adv2" in rej["reply"]
    can_rej = bot.handle_command("thôi đừng từ chối nữa", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN")
    assert can_rej.get("v4_intent") == "ADMIN_DECISION_CANCELLED"
    assert mram.get_request_detail(r_beta, viewer_username="admin", is_admin=True)["status"] == "PENDING_APPROVAL"

    # Round 1c: Mid-confirmation switch to a completely different applicant ("yêu cầu Nguyễn Văn An bổ sung")
    bot.handle_command("từ chối đơn của Lê Thị Bình vì thiếu thuyết minh", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN")
    sw = bot.handle_command("yêu cầu Nguyễn Văn An bổ sung", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN")
    assert sw.get("v4_intent") == "ADMIN_DECISION_PREREQUISITE"
    assert "sv_adv1" in sw["reply"]
    sw2 = bot.handle_command(
        "bổ sung kế hoạch sử dụng VRAM và xác nhận của GVHD", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN"
    )
    assert sw2.get("v4_intent") == "ADMIN_DECISION_CONFIRMATION"
    assert "sv_adv1" in sw2["reply"]
    assert r_alpha in sw2["reply"]
    bot.handle_command("hủy", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN")

    # Round 2: Workstation pool distribution query + batch incident acknowledge
    sto = bot.handle_command(
        "ổ cứng trên workstation đang chia như thế nào, còn trống bao nhiêu?",
        sender_id="admin",
        chat_id="admin",
        source="dashboard",
        principal_role="ADMIN",
    )
    assert "HDD3" in sto["reply"] and "SDD" in sto["reply"]
    assert "Đề xuất cấp tiếp theo" in sto["reply"]

    ack_all = bot.handle_command("xác nhận tất cả sự cố giúp tôi", sender_id="admin", chat_id="admin", source="dashboard", principal_role="ADMIN")
    assert ack_all.get("v4_intent") == "NL_INCIDENT_ACK"
    assert inc1["incident_id"] in ack_all["reply"] and inc2["incident_id"] in ack_all["reply"]
    assert inc_eng.get_incident(inc1["incident_id"])["status"] == "ACKNOWLEDGED"
    assert inc_eng.get_incident(inc2["incident_id"])["status"] == "ACKNOWLEDGED"

    # Round 3: Researcher RBAC cross-lookup denial & Researcher self-service SSH status lookup
    rbac_deny = bot.handle_command(
        "cho tôi xem đơn xin cấp của @sv_adv1",
        sender_id="sv_adv2",
        chat_id="sv_adv2",
        source="dashboard",
        principal_role="RESEARCHER",
    )
    assert "Từ chối truy cập (RBAC)" in rbac_deny["reply"]

    self1 = bot.handle_command(
        "cho tôi xem đơn xin cấp của tôi",
        sender_id="sv_adv1",
        chat_id="sv_adv1",
        source="dashboard",
        principal_role="RESEARCHER",
    )
    assert r_alpha in self1["reply"]
    assert "Admin chỉ cần" not in self1["reply"]

    self2 = bot.handle_command(
        "đơn xin cấp ssh của tôi được duyệt chưa?",
        sender_id="sv_adv1",
        chat_id="sv_adv1",
        source="dashboard",
        principal_role="RESEARCHER",
    )
    assert r_alpha in self2["reply"]

    res_approve = bot.handle_command(
        "@sv_adv1 đồng ý duyệt cấp tài nguyên",
        sender_id="sv_adv2",
        chat_id="sv_adv2",
        source="dashboard",
        principal_role="RESEARCHER",
    )
    assert "Chỉ Quản trị viên" in res_approve["reply"]


def test_17_zalo_worker_chunking_and_workstation_hb(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector
    from app.services.zalo_assistant.worker import ZaloPollingWorker

    class _FakeBot:
        def __init__(self):
            self.sent = []
            self._idx = 0

        async def send_message(self, *, chat_id: str, text: str):
            self._idx += 1
            self.sent.append((chat_id, text))
            return SimpleNamespace(message_id=f"zalo-msg-{self._idx}")

    fake_svc = SimpleNamespace(
        bot_engine=SimpleNamespace(bot_token="tok"),
        quota_ledger=SimpleNamespace(),
        report_generator=SimpleNamespace(get_last_reported_week=lambda: None),
        lease_manager=SimpleNamespace(),
        identity_rbac=SimpleNamespace(),
        telemetry_collector=SimpleNamespace(),
        incident_engine=SimpleNamespace(),
        notification_dispatcher=SimpleNamespace(),
        repo_alert_monitor=SimpleNamespace(),
        data_dir=str(tmp_path),
    )
    worker = ZaloPollingWorker(fake_svc)
    fake_bot = _FakeBot()
    long_text = "\n".join(
        f"• Mục {i}: Chi tiết đơn xin cấp SSH của thành viên nghiên cứu tại lab workstation với thông tin rất dài"
        for i in range(1, 50)
    )
    assert len(long_text) > 3500
    ids = asyncio.run(worker._send_zalo_chunks(fake_bot, chat_id="chat-1", text=long_text))
    assert len(fake_bot.sent) >= 2
    assert len(ids) == len(fake_bot.sent)
    assert all(len(msg) <= 1800 for _, msg in fake_bot.sent)
    assert "[Phần 1/" in fake_bot.sent[0][1]

    class _HostRegStub:
        def list_hosts(self, live_servers=None):
            return [
                {
                    "id": "workstation",
                    "hostname": "aiot-ws1",
                    "last_heartbeat_at": "2026-11-17T08:25:00+00:00",
                    "freshness": "ONLINE",
                }
            ]

    core_stub = SimpleNamespace(
        host_registry=_HostRegStub(),
        local_host_id="workstation",
        get_gpu_allocations_by_device=lambda: [],
        get_services_health=lambda: {},
        workspaces=[],
        jobs=[],
    )
    col = UnifiedTelemetryCollector(str(tmp_path), core_service=core_stub)
    snap = col.collect_snapshot(
        servers_override=[
            {
                "id": "workstation",
                "status": "online",
                "freshness_status": "ONLINE",
                "telemetry_source": "go_node_agent",
                "collected_at": "2026-11-26T10:15:00+00:00",
            }
        ],
        gpus_override=[],
        jobs_override=[],
        workspaces_override=[],
        services_override={},
    )
    ws_row = next(h for h in snap["hosts"] if h["host_id"] == "workstation")
    assert ws_row["last_heartbeat_at"] == "2026-11-26T10:15:00+00:00"


def test_18_storage_pool_priority_prefers_sdd_sdb_over_hdd3(mram_env):
    from app.services.labops_service import service as labops_service
    from app.services.storage_inventory_service import storage_service

    mram: MramService = mram_env["mram"]
    uuid_sdd = "5ce32377-6072-4c07-a066-807e89a5d48b"
    uuid_sdb = "159bacc4-38ae-4adb-bf25-7a5091c308ca"
    uuid_hdd3 = "d5111439-d134-4ba6-93ef-6268d738f035"

    prev_custom = storage_service._custom_pools.get("workstation")
    prev_members = list(labops_service.members)
    try:
        storage_service._custom_pools["workstation"] = [
            {
                "host_id": "workstation",
                "storage_id": uuid_sdd,
                "filesystem_uuid": uuid_sdd,
                "device": "/dev/sdd",
                "mount_point": f"/media/aiot-ws1/{uuid_sdd}",
                "filesystem": "ext4",
                "capacity_gib": 915.8,
                "used_gib": 55.4,
                "free_gib": 860.4,
                "allocatable_gib": 768.8,
                "reserved_gib": 91.6,
                "eligible": True,
                "read_only": False,
                "mounted": True,
            },
            {
                "host_id": "workstation",
                "storage_id": uuid_sdb,
                "filesystem_uuid": uuid_sdb,
                "device": "/dev/sdb",
                "mount_point": f"/media/aiot-ws1/{uuid_sdb}",
                "filesystem": "ext4",
                "capacity_gib": 915.8,
                "used_gib": 144.2,
                "free_gib": 771.6,
                "allocatable_gib": 680.0,
                "reserved_gib": 91.6,
                "eligible": True,
                "read_only": False,
                "mounted": True,
            },
            {
                "host_id": "workstation",
                "storage_id": uuid_hdd3,
                "filesystem_uuid": uuid_hdd3,
                "device": "/dev/sdc",
                "mount_point": "/mnt/HDD3",
                "filesystem": "ext4",
                "capacity_gib": 3666.5,
                "used_gib": 1515.0,
                "free_gib": 2151.5,
                "allocatable_gib": 1784.9,
                "reserved_gib": 366.6,
                "eligible": True,
                "read_only": False,
                "mounted": True,
            },
        ]

        mock_members = []
        for i in range(9):
            mock_members.append(
                {
                    "id": f"mem-sdd-{i}",
                    "username": f"u_sdd_{i}",
                    "assigned_server": "workstation",
                    "status": "ACTIVE",
                    "storage_pool_id": uuid_sdd,
                    "member_storage_directory": f"/media/aiot-ws1/{uuid_sdd}/labops/users/u_sdd_{i}",
                }
            )
        for i in range(4):
            mock_members.append(
                {
                    "id": f"mem-sdb-{i}",
                    "username": f"u_sdb_{i}",
                    "assigned_server": "workstation",
                    "status": "ACTIVE",
                    "storage_pool_id": uuid_sdb,
                    "member_storage_directory": f"/media/aiot-ws1/{uuid_sdb}/labops/users/u_sdb_{i}",
                }
            )
        for i in range(3):
            mock_members.append(
                {
                    "id": f"mem-hdd3-{i}",
                    "username": f"u_hdd3_{i}",
                    "assigned_server": "workstation",
                    "status": "ACTIVE",
                    "storage_pool_id": "pool-hdd3",
                    "member_storage_directory": f"/mnt/HDD3/labops/users/u_hdd3_{i}",
                }
            )
        labops_service.members = mock_members

        # 1. Verify member counts and 2-tier ranking
        dist = {d["pool_id"]: d for d in mram.get_host_pool_distribution("workstation", required_storage_gb=50.0)}
        assert dist[uuid_sdd]["assigned_member_count"] == 9
        assert dist[uuid_sdd]["priority_rank"] == 0
        assert dist[uuid_sdd]["priority_tier"] == "PREFERRED"
        assert dist[uuid_sdb]["assigned_member_count"] == 4
        assert dist[uuid_sdb]["priority_rank"] == 0
        assert dist[uuid_sdb]["priority_tier"] == "PREFERRED"
        assert dist[uuid_hdd3]["assigned_member_count"] == 3
        assert dist[uuid_hdd3]["priority_rank"] == 1
        assert dist[uuid_hdd3]["priority_tier"] == "FALLBACK_RESTRICTED"

        # 2. Even when HDD3 has 0 members and 1784.9 GiB free, PREFERRED Tier 0 (sdb) wins
        labops_service.members = [m for m in mock_members if not m["id"].startswith("mem-hdd3-")]
        rec = mram.compute_resource_recommendation(
            member_category="STUDENT", requested_duration_days=180, gpu_requested=True
        )
        assert rec["recommended_pool_id"] == uuid_sdb

        lec = mram.create_web_member_account(
            {"username": "gv_prio", "full_name": "GV Priority", "member_code": "GV701", "member_category": "LECTURER"}
        )
        sv = mram.create_web_member_account(
            {
                "username": "sv_prio",
                "full_name": "SV Priority",
                "member_code": "SV701",
                "member_category": "STUDENT",
                "primary_supervisor_id": lec["profile"]["member_id"],
            }
        )
        req_id = mram.submit_ssh_request(
            username="sv_prio",
            payload={
                "primary_supervisor_id": lec["profile"]["member_id"],
                "project_title": "Storage Priority Verification",
                "registration_reason": "Verify Tier 0 sdb/sdd selection over Tier 1 HDD3.",
                "ssh_purpose": "Training",
                "expected_tasks": "python train.py",
                "policy_accepted": True,
            },
        )["request"]["id"]
        resolved_cfg = mram.resolve_default_approval_config(req_id)
        assert resolved_cfg["pool_id"] == uuid_sdb

        # 3. Fallback to HDD3 happens ONLY when both sdd and sdb lack capacity (< 50 GiB)
        for p in storage_service._custom_pools["workstation"]:
            if p["storage_id"] in {uuid_sdd, uuid_sdb}:
                p["allocatable_gib"] = 10.0
                p["free_gib"] = 12.0
        rec_fallback = mram.compute_resource_recommendation(
            member_category="STUDENT", requested_duration_days=180, gpu_requested=True
        )
        assert rec_fallback["recommended_pool_id"] == uuid_hdd3
    finally:
        labops_service.members = prev_members
        if prev_custom is None:
            storage_service._custom_pools.pop("workstation", None)
        else:
            storage_service._custom_pools["workstation"] = prev_custom


def test_19_cross_process_handover_vault_reissue_gate_and_member_freshness(mram_env, tmp_path, monkeypatch):
    """
    Regression test for 4 cross-process & handover hardening guarantees:
    1. OneTimeSecretVault shares encrypted bundles/secrets across separate instances via disk (0600, zero plaintext)
       and deletes entries cleanly after 1-time claim.
    2. get_member_ssh_access_handover NEVER overwrites an existing member's ssh_public_key/ssh_fingerprint.
    3. reissue_member_ssh_secret updates first_login_policy.json in container ssh_ro immediately and invalidates old hash.
    4. LabOpsDataService.ensure_members_fresh() and _save_persistent_data() sync/preserve active DB records across processes.
    """
    import json
    from app.services.labops_service import LabOpsDataService
    from app.services.ssh_access_manager import OneTimeSecretVault

    mram = mram_env["mram"]
    client = mram_env["client"]
    db_mgr = mram_env["db"]

    # 1. Cross-process encrypted OneTimeSecretVault
    vault_path = str(tmp_path / "v2_handover_vault.json")
    vault_worker = OneTimeSecretVault(default_ttl_seconds=86400, storage_path=vault_path)
    vault_web = OneTimeSecretVault(default_ttl_seconds=86400, storage_path=vault_path)

    vault_worker.store_provisioned_package(
        member_id="mem-xproc-vault",
        username="sv_xproc_vault",
        ssh_host="192.168.167.251",
        ssh_port=22099,
        workspace_name="ws-sv_xproc_vault",
        workspace_path="/tmp/ws-sv_xproc_vault",
        fingerprint="SHA256:testfp12345",
        encrypted_private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nSECRET_KEY_BODY_XYZ\n-----END OPENSSH PRIVATE KEY-----",
        passphrase="Passphrase!Xproc#2026",
        temporary_password="TempPw!Xproc#2026",
        temp_password_expires_at="2026-12-01T00:00:00+00:00",
        vpn_guide="WireGuard VPN + SSH",
        ssh_login_user="sv_xproc_vault",
    )
    assert os.path.exists(vault_path)
    assert oct(os.stat(vault_path).st_mode & 0o777) == "0o600"
    with open(vault_path, "r", encoding="utf-8") as vf:
        raw_vault = vf.read()
    assert "BEGIN OPENSSH PRIVATE KEY" not in raw_vault
    assert "Passphrase!Xproc#2026" not in raw_vault
    assert "TempPw!Xproc#2026" not in raw_vault

    avail = vault_web.get_availability("mem-xproc-vault")
    assert avail["key_bundle_available"] is True
    assert avail["channel_secrets_available"] is True

    bundle = vault_web.claim_key_bundle_once("mem-xproc-vault")
    assert bundle["ssh_port"] == 22099
    assert "SECRET_KEY_BODY_XYZ" in bundle["encrypted_private_key"]
    with pytest.raises(LookupError):
        vault_worker.claim_key_bundle_once("mem-xproc-vault")

    secrets_pkg = vault_web.claim_channel_secrets_once("mem-xproc-vault")
    assert secrets_pkg["temporary_password"] == "TempPw!Xproc#2026"
    assert secrets_pkg["key_passphrase"] == "Passphrase!Xproc#2026"
    with pytest.raises(LookupError):
        vault_worker.claim_channel_secrets_once("mem-xproc-vault")
    assert not os.path.exists(vault_path)

    # 2 & 3. Handover key protection + reissue syncing container first_login_policy.json
    lec = mram.create_web_member_account(
        {"username": "gv_xproc_19", "full_name": "GV 19", "member_code": "GV919", "member_category": "LECTURER"}
    )
    sv = mram.create_web_member_account(
        {
            "username": "sv_xproc_19",
            "full_name": "SV 19",
            "member_code": "SV919",
            "member_category": "STUDENT",
            "primary_supervisor_id": lec["profile"]["member_id"],
        }
    )
    req_id = mram.submit_ssh_request(
        username="sv_xproc_19",
        payload={
            "primary_supervisor_id": lec["profile"]["member_id"],
            "project_title": "Vault & Gate Safety Test",
            "registration_reason": "Verify handover keeps key intact and reissue updates container gate.",
            "ssh_purpose": "Train",
            "expected_tasks": "python train.py",
            "policy_accepted": True,
        },
    )["request"]["id"]
    mram.decide_ssh_request(
        request_id=req_id,
        approver_username="admin",
        decision="APPROVED",
        reason="ok",
        config=mram.resolve_default_approval_config(req_id),
    )

    sv_member = next(m for m in labops_service.members if m["username"] == "sv_xproc_19")
    orig_pub = sv_member["ssh_public_key"]
    orig_fp = sv_member["ssh_fingerprint"]
    assert orig_pub and orig_fp

    ws_id = sv_member.get("workspace_id") or f"ws-{sv_member['id']}"
    sv_member["workspace_id"] = ws_id
    runtime_dir = str(tmp_path / "runtime")
    ssh_ro_dir = os.path.join(runtime_dir, ws_id, "ssh_ro")
    os.makedirs(ssh_ro_dir, exist_ok=True)
    monkeypatch.setenv("LABOPS_WORKSPACE_RUNTIME_DIR", runtime_dir)

    labops_service.ssh_access_manager.sync_container_first_login_gate(
        workspace_id=ws_id,
        username="sv_xproc_19",
        member_id=sv_member["id"],
        keys=[orig_pub],
        member_storage_dir=sv_member.get("member_storage_directory"),
    )
    policy_file = os.path.join(ssh_ro_dir, "first_login_policy.json")
    with open(policy_file, "r", encoding="utf-8") as pf:
        policy_before = json.load(pf)
    old_hash = policy_before["temp_password_hash"]
    assert old_hash

    # Simulate empty vault (e.g. legacy V2 member or restart) without claim record -> GET must NOT rotate key
    labops_service.ssh_access_manager.vault.invalidate_member(sv_member["id"])
    sv_tok = issue_token("sv_xproc_19", "RESEARCHER", member_id=sv_member["id"], ttl=3600)
    res_get = client.get("/api/v1/me/ssh-access", headers={"Authorization": f"Bearer {sv_tok}"})
    assert res_get.status_code == 200
    assert res_get.json()["ready"] is True
    assert sv_member["ssh_public_key"] == orig_pub
    assert sv_member["ssh_fingerprint"] == orig_fp

    # Reissue temp password -> updates first_login_policy.json & invalidates old hash
    reissued = mram.reissue_member_ssh_secret(username="sv_xproc_19", step_up_jti="jti-test-19")
    assert reissued["reissued"] is True
    with open(policy_file, "r", encoding="utf-8") as pf:
        policy_after = json.load(pf)
    assert policy_after["must_change_password"] is True
    assert policy_after["temp_password_hash"]
    assert policy_after["temp_password_hash"] != old_hash
    assert old_hash not in policy_after.get("accepted_temp_password_hashes", [])
    assert sv_member["activation_status"] == "PENDING_ACTIVATION"

    # 4. Cross-process ensure_members_fresh() and _save_persistent_data() DB preservation
    xproc_dir = str(tmp_path / "xproc_sync")
    os.makedirs(xproc_dir, exist_ok=True)
    xproc_db_url = f"sqlite:///{os.path.join(xproc_dir, 'labops_v2.sqlite3')}"
    upgrade_database_schema(xproc_db_url)
    monkeypatch.setenv("DATABASE_URL", xproc_db_url)
    from app.core.security import get_revocation_store

    try:
        svc_worker = LabOpsDataService(demo_mode=False, data_dir=xproc_dir)
        svc_api = LabOpsDataService(demo_mode=False, data_dir=xproc_dir)
        svc_stale = LabOpsDataService(demo_mode=False, data_dir=xproc_dir)

        svc_worker.members.append(
            {
                "id": "mem-xproc-1",
                "username": "sv_xproc_synced",
                "full_name": "SV Synced",
                "email": "sv19@fitdnu.edu.vn",
                "role": "RESEARCHER",
                "status": "WEB_ACTIVE",
                "provisioning_state": "VERIFIED_READY",
                "ssh_access_state": "VERIFIED_READY",
                "assigned_server": "workstation",
                "workspace_name": "ws-sv_xproc_synced",
                "version": 2,
            }
        )
        svc_worker._save_persistent_data()
        members_json_path = os.path.join(xproc_dir, "members.json")
        st = os.stat(members_json_path)
        os.utime(members_json_path, (st.st_atime + 2, st.st_mtime + 2))

        found = svc_api._find_member("sv_xproc_synced")
        assert found["id"] == "mem-xproc-1"
        assert found["ssh_access_state"] == "VERIFIED_READY"
        assert found["version"] == 2

        # Even a stale process saving its own member preserves SQLite-committed mem-xproc-1
        svc_stale.members.append(
            {
                "id": "mem-xproc-2",
                "username": "sv_xproc_other",
                "full_name": "SV Other",
                "email": "sv19b@fitdnu.edu.vn",
                "role": "RESEARCHER",
                "status": "WEB_ACTIVE",
                "provisioning_state": "WEB_ONLY",
                "ssh_access_state": "NONE",
                "assigned_server": "workstation",
                "workspace_name": "ws-sv_xproc_other",
                "version": 1,
            }
        )
        svc_stale._save_persistent_data()
        ids_after = {m["id"] for m in svc_stale.members}
        assert {"mem-xproc-1", "mem-xproc-2"}.issubset(ids_after)
    finally:
        get_revocation_store().bind_db(db_mgr)








