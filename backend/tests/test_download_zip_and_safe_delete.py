import os
import uuid
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.core.config import settings
from app.core.security import issue_token, get_revocation_store
from app.services.labops_service import service

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_admin_revocation():
    get_revocation_store().unrevoke_subject("admin")
    yield
    get_revocation_store().unrevoke_subject("admin")


def test_download_zip_suspended_or_archived_returns_409():
    """Verify that downloading access ZIP for suspended or archived member returns 409, NOT 500."""
    uname = f"tzip_{uuid.uuid4().hex[:6]}"
    member = service.provision_member({
        "full_name": "Zip Test Target",
        "username": uname,
        "email": f"{uname}@example.org",
        "role": "RESEARCHER",
        "assigned_server": "workstation",
        "workspace_name": f"ws-{uname}",
        "storage_quota_gb": 10,
        "mount_path": "/workspace",
        "cpu_limit_cores": 2,
        "ram_limit_gb": 8,
        "gpu_permissions": [],
        "vram_quota_gb": 0,
    })
    mid = member["id"]
    admin_token = issue_token("admin", "ADMIN")

    # 1. Member suspended -> download-zip returns 409
    member["status"] = "SUSPENDED"
    resp = client.get(
        f"/api/v1/v2/ssh-access/{mid}/download-zip",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 409
    assert "SUSPENDED" in resp.json().get("detail", "")

    # 2. Member archived / delete-eligible -> download-zip returns 409
    member["status"] = "DELETE_ELIGIBLE"
    resp = client.get(
        f"/api/v1/v2/ssh-access/{mid}/download-zip",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 409
    assert "DELETE_ELIGIBLE" in resp.json().get("detail", "")


def test_step_up_and_delete_preview_auto_heal():
    """Verify preview_deletion auto-heals member status and step-up works with LABOPS_ADMIN_PASSWORD."""
    uname = f"theal_{uuid.uuid4().hex[:6]}"
    member = service.provision_member({
        "full_name": "Heal Test Target",
        "username": uname,
        "email": f"{uname}@example.org",
        "role": "RESEARCHER",
        "assigned_server": "workstation",
        "workspace_name": f"ws-{uname}",
        "storage_quota_gb": 10,
        "mount_path": "/workspace",
        "cpu_limit_cores": 2,
        "ram_limit_gb": 8,
        "gpu_permissions": [],
        "vram_quota_gb": 0,
    })
    mid = member["id"]
    admin_token = issue_token("admin", "ADMIN")

    admin_pass = settings.LABOPS_ADMIN_PASSWORD or os.getenv("LABOPS_ADMIN_PASSWORD") or "LabOpsAdmin2026!"

    with patch.dict(os.environ, {"LABOPS_ADMIN_PASSWORD": admin_pass}, clear=False):
        # 1. Step-up token issuance
        step_resp = client.post(
            "/api/v1/auth/step-up",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "password": admin_pass,
                "action": "DELETE_MEMBER",
                "target_id": mid
            }
        )
        assert step_resp.status_code == 200
        token = step_resp.json().get("step_up_token")
        assert token is not None

        # 2. Simulate demoted member that has archived_at and archive_manifest but status was set to SUSPENDED
        member["status"] = "SUSPENDED"
        member["archived_at"] = "2026-11-19T00:00:00Z"
        member["archive_manifest"] = {"file_count": 1, "total_bytes": 100}

        # 3. Delete preview auto-heals and marks eligible_for_deletion
        preview_resp = client.get(
            f"/api/v1/v2/members/{mid}/delete-preview",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert preview_resp.status_code == 200
        preview = preview_resp.json()
        assert preview["eligible_for_deletion"] is True
        assert preview["current_status"] == "DELETE_ELIGIBLE"
        assert member["status"] == "DELETE_ELIGIBLE"
