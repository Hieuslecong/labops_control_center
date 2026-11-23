"""Comprehensive regression tests for deleted-member username reuse, transactional persistence consistency, revocation isolation, schema migration, and testacc state repair."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateIndex

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import app.api.v1.endpoints.admin_members as admin_ep
import app.api.v1.endpoints.auth as auth_ep
import app.api.v1.endpoints.repositories as repo_ep
import app.api.v1.endpoints.users as users_ep
import app.api.v1.endpoints.v2_platform as v2_ep
from app.core.repository_credentials import issue_repository_token, verify_repository_token
from app.core.security import AuthError, get_revocation_store, issue_token, verify_token
from app.db.atomic_json import atomic_write_json
from app.db.models import (
    AuditEventRecord,
    DeletionOperationRecord,
    MemberRecord,
    PersistenceRecoveryRecord,
)
from app.db.session import DatabaseManager, upgrade_database_schema
from app.main import app
from app.services import labops_service as labops_mod
from app.services.labops_service import (
    LabOpsDataService,
    MemberConflictError,
    MemberPersistenceError,
)
from scripts.repair_testacc_and_migrate import (
    HISTORICAL_DELETE_OP_ID,
    HISTORICAL_MEMBER_ID,
    ORPHAN_ENDPOINT_ID,
    ORPHAN_MEMBER_ID,
    ORPHAN_WORKSPACE_ID,
    apply_repair,
    inspect_state,
    rollback_from_backup,
)


def _member_payload(username: str = "reusable_u", **overrides):
    data = {
        "full_name": f"Researcher {username}",
        "username": username,
        "email": f"{username}@lab.ai.vn",
        "role": "RESEARCHER",
        "assigned_server": "workstation",
        "workspace_name": f"ws-{username}",
        "storage_quota_gb": 10,
        "mount_path": "/workspace",
        "cpu_limit_cores": 4,
        "ram_limit_gb": 8,
        "gpu_permissions": [],
        "vram_quota_gb": 0,
    }
    data.update(overrides)
    return data


def _complete_delete(svc: LabOpsDataService, member: dict, *, actor: str = "admin", remove_files: bool = False):
    mid = member["id"]
    uname = member["username"]
    svc.suspend_member_lifecycle(mid, actor=actor, reason="Test suspension")
    svc.archive_member_lifecycle(mid, actor=actor, retain_backup=True)
    return svc.delete_member_permanently(
        mid,
        actor=actor,
        remove_files=remove_files,
        confirm_username=uname,
        step_up_verified=True,
        enforce_lifecycle=True,
    )


class TestUsernameReuseAndPersistenceConsistency(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="labops-reuse-test-")
        self.users_dir = os.path.join(self.tmp.name, "users")
        os.makedirs(self.users_dir, exist_ok=True)
        self.prev_data_dir = os.environ.get("LABOPS_DATA_DIR")
        self.prev_user_dir = os.environ.get("LABOPS_USER_DATA_DIR")
        os.environ["LABOPS_DATA_DIR"] = self.tmp.name
        os.environ["LABOPS_USER_DATA_DIR"] = self.users_dir

        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        targets = [auth_ep, users_ep, admin_ep, v2_ep, repo_ep]
        self._orig_services = [(mod, mod.service) for mod in targets]
        for mod in targets:
            mod.service = self.svc
        self.client = TestClient(app)
        self.admin_token = issue_token("admin", "ADMIN")

    def tearDown(self) -> None:
        for mod, orig in self._orig_services:
            mod.service = orig
        try:
            self.svc.db.engine.dispose()
        except Exception:
            pass
        if self.prev_data_dir is None:
            os.environ.pop("LABOPS_DATA_DIR", None)
        else:
            os.environ["LABOPS_DATA_DIR"] = self.prev_data_dir
        if self.prev_user_dir is None:
            os.environ.pop("LABOPS_USER_DATA_DIR", None)
        else:
            os.environ["LABOPS_USER_DATA_DIR"] = self.prev_user_dir
        self.tmp.cleanup()

    def test_01_create_new_and_reject_active_duplicate_with_409_and_no_side_effects(self) -> None:
        r_create = self.client.post(
            "/api/v1/users",
            json=_member_payload("alice_one"),
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(r_create.status_code, 200)
        created = r_create.json()
        self.assertEqual(created["username"], "alice_one")

        keys_before = len(self.svc.ssh_key_registry.list_keys(username="alice_one"))
        r_dup_users = self.client.post(
            "/api/v1/users",
            json=_member_payload("alice_one", workspace_name="ws-alice_two"),
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(r_dup_users.status_code, 409)
        self.assertEqual(r_dup_users.json()["detail"]["code"], "USERNAME_ALREADY_EXISTS")

        r_dup_ssh = self.client.post(
            "/api/v1/v2/ssh-access/provision",
            json={**_member_payload("alice_one"), "provision_ssh_access": True},
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(r_dup_ssh.status_code, 409)
        self.assertEqual(r_dup_ssh.json()["detail"]["code"], "USERNAME_ALREADY_EXISTS")
        self.assertEqual(len(self.svc.ssh_key_registry.list_keys(username="alice_one")), keys_before)

    def test_02_recreate_after_completed_deletion_preserves_history_both_flows(self) -> None:
        # 1. Create via SSH provisioning path -> delete -> recreate via normal & SSH paths
        with patch.object(self.svc, "_is_host_online", return_value=False):
            m1 = self.svc.provision_member_ssh_access({
                "full_name": "Testacc First",
                "username": "testacc",
                "assigned_server": "workstation",
                "cpu_limit_cores": 2,
                "ram_limit_gb": 4,
                "storage_quota_gb": 5,
                "_actor_id": "admin",
            })
        old_id = m1["id"]
        old_key_ids = [k["id"] for k in self.svc.ssh_key_registry.list_keys(username="testacc", member_id=old_id)]
        self.assertEqual(len(old_key_ids), 1)

        del_res = _complete_delete(self.svc, m1, remove_files=False)
        self.assertTrue(del_res["success"])

        # Old DB tombstone + DeletionOperationRecord remain intact
        with self.svc.db.session_scope() as s:
            old_row = s.get(MemberRecord, old_id)
            self.assertIsNotNone(old_row)
            self.assertEqual(old_row.status, "DELETED")
            self.assertIsNotNone(old_row.deleted_at)
            del_op = s.get(DeletionOperationRecord, del_res["operation_id"])
            self.assertIsNotNone(del_op)
            self.assertEqual(del_op.status, "COMPLETED")

        # Recreate via /api/v1/v2/ssh-access/provision
        with patch.object(self.svc, "_is_host_online", return_value=False):
            r_new = self.client.post(
                "/api/v1/v2/ssh-access/provision",
                json={
                    "full_name": "Testacc Second",
                    "username": "testacc",
                    "assigned_server": "workstation",
                    "cpu_limit_cores": 2,
                    "ram_limit_gb": 4,
                    "storage_quota_gb": 5,
                },
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
        self.assertEqual(r_new.status_code, 200, r_new.text)
        m2 = r_new.json()
        self.assertNotEqual(m2["id"], old_id)
        self.assertEqual(m2["username"], "testacc")

        # Historical DB rows & revoked SSH keys & archived SSH password entry preserved cleanly
        with self.svc.db.session_scope() as s:
            all_rows = s.query(MemberRecord).filter(MemberRecord.username == "testacc").all()
            self.assertEqual(len(all_rows), 2)
            statuses = {r.id: r.status for r in all_rows}
            self.assertEqual(statuses[old_id], "DELETED")
            self.assertNotEqual(statuses[m2["id"]], "DELETED")

        all_keys = self.svc.ssh_key_registry.list_keys(username="testacc")
        self.assertEqual(len(all_keys), 2)
        old_key = next(k for k in all_keys if k["id"] == old_key_ids[0])
        self.assertEqual(old_key["desired_state"], "REVOKED")

        with open(os.path.join(self.tmp.name, "v2_ssh_accounts.json"), "r", encoding="utf-8") as af:
            accts = json.load(af)
        self.assertIn(old_id, accts.get("archived_accounts", {}))
        self.assertEqual(accts["accounts"]["testacc"]["member_id"], m2["id"])

        # Also test delete m2 -> recreate via regular provision_member()
        _complete_delete(self.svc, m2, remove_files=False)
        m3 = self.svc.provision_member(_member_payload("testacc"))
        self.assertNotIn(m3["id"], {old_id, m2["id"]})

    def test_03_reject_recreate_when_deletion_incomplete_or_failed(self) -> None:
        m = self.svc.provision_member(_member_payload("incomp_user"))
        mid = m["id"]

        # Suspended / Archived -> rejected
        self.svc.suspend_member_lifecycle(mid, actor="admin", reason="test")
        with self.assertRaises(MemberConflictError) as ctx_s:
            self.svc.provision_member(_member_payload("incomp_user"))
        self.assertEqual(ctx_s.exception.http_status, 409)

        self.svc.archive_member_lifecycle(mid, actor="admin")
        with self.assertRaises(MemberConflictError):
            self.svc.provision_member(_member_payload("incomp_user"))

        # Simulate PARTIAL_FAILURE during delete -> stays rejected
        with patch.object(self.svc.workspace_controller, "remove_workspace", side_effect=RuntimeError("registry busy")):
            with self.assertRaises(RuntimeError):
                self.svc.delete_member_permanently(
                    mid, actor="admin", confirm_username="incomp_user", step_up_verified=True
                )
        with self.assertRaises(MemberConflictError) as ctx_pf:
            self.svc.provision_member(_member_payload("incomp_user"))
        self.assertEqual(ctx_pf.exception.code, "MEMBER_DELETION_INCOMPLETE")

        # Even if someone removed from RAM list, incomplete DeletionOperationRecord in DB STILL blocks recreation
        saved_members = list(self.svc.members)
        self.svc.members = [x for x in self.svc.members if x["id"] != mid]
        with self.assertRaises(MemberConflictError) as ctx_op:
            self.svc.provision_member(_member_payload("incomp_user"))
        self.assertEqual(ctx_op.exception.code, "MEMBER_DELETION_INCOMPLETE")
        self.svc.members = saved_members

    def test_04_concurrent_creation_allows_only_one_account(self) -> None:
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def worker():
            barrier.wait()
            try:
                results.append(self.svc.provision_member(_member_payload("race_user")))
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], MemberConflictError)
        self.assertEqual(sum(1 for m in self.svc.members if m["username"] == "race_user"), 1)

    def test_05_database_failure_rolls_back_ram_json_endpoints_and_preserves_user_files(self) -> None:
        # Seed one valid member so members.json and ssh_endpoints.json already exist on disk and must stay untouched
        r_base = self.client.post(
            "/api/v1/users",
            json=_member_payload("baseline_u"),
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(r_base.status_code, 200)
        members_json_path = os.path.join(self.tmp.name, "members.json")
        endpoints_json_path = os.path.join(self.tmp.name, "ssh_endpoints.json")
        with open(members_json_path, "r", encoding="utf-8") as mf:
            members_json_before = mf.read()
        with open(endpoints_json_path, "r", encoding="utf-8") as ef:
            endpoints_json_before = ef.read()

        # Pre-create user files to ensure rollback NEVER deletes user data on disk
        user_dir = os.path.join(self.users_dir, "workstation", "dbfail_user")
        os.makedirs(user_dir, exist_ok=True)
        important_file = os.path.join(user_dir, "research_notes.txt")
        with open(important_file, "w", encoding="utf-8") as rf:
            rf.write("do-not-delete")

        # 1) DB unavailable during precheck returns clean 503 DB_PRECHECK_FAILED with zero side effects
        with patch.object(self.svc.db, "session_scope", side_effect=OperationalError("stmt", {}, RuntimeError("db offline"))):
            r_pre = self.client.post(
                "/api/v1/users",
                json=_member_payload("dbfail_user"),
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
        self.assertEqual(r_pre.status_code, 503)
        self.assertEqual(r_pre.json()["detail"]["code"], "DB_PRECHECK_FAILED")

        # 2) DB fails AFTER SSH key & workspace side effects are prepared (during commit) -> compensates cleanly
        orig_scope = self.svc.db.session_scope
        calls = {"n": 0}

        def fail_on_write_scope():
            calls["n"] += 1
            if calls["n"] >= 2:
                raise OperationalError("stmt", {}, RuntimeError("db commit locked"))
            return orig_scope()

        with patch.object(self.svc.db, "session_scope", side_effect=fail_on_write_scope):
            with patch.object(self.svc, "_is_host_online", return_value=False):
                r = self.client.post(
                    "/api/v1/v2/ssh-access/provision",
                    json={**_member_payload("dbfail_user"), "provision_ssh_access": True},
                    headers={"Authorization": f"Bearer {self.admin_token}"},
                )
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"]["code"], "DB_PERSISTENCE_FAILED")

        # User files on disk preserved, NO phantom in members / members.json / ssh_endpoints / workspaces
        self.assertTrue(os.path.isfile(important_file))
        self.assertFalse(any(m["username"] == "dbfail_user" for m in self.svc.members))
        with open(members_json_path, "r", encoding="utf-8") as mf:
            members_json_after = mf.read()
            self.assertEqual(members_json_after, members_json_before)
            self.assertFalse(any(m["username"] == "dbfail_user" for m in json.loads(members_json_after)))
        with open(endpoints_json_path, "r", encoding="utf-8") as ef:
            self.assertEqual(ef.read(), endpoints_json_before)
        self.assertFalse(any(w["owner"] == "dbfail_user" for w in self.svc.workspace_controller.list_workspaces(owner="dbfail_user")))
        for k in self.svc.ssh_key_registry.list_keys(username="dbfail_user"):
            self.assertEqual(k["observed_state"], "PROVISIONING_ROLLED_BACK")

        # Retry after DB recovers -> succeeds cleanly with no duplicates
        with patch.object(self.svc, "_is_host_online", return_value=False):
            r_retry = self.client.post(
                "/api/v1/v2/ssh-access/provision",
                json={**_member_payload("dbfail_user"), "provision_ssh_access": True},
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
        self.assertEqual(r_retry.status_code, 200, r_retry.text)
        self.assertEqual(sum(1 for m in self.svc.members if m["username"] == "dbfail_user"), 1)

    def test_06_json_write_failure_records_durable_recovery_and_heals_on_restart(self) -> None:
        orig_atomic = labops_mod.atomic_write_json

        def fail_on_members_json(path, data, *, mode=0o600, use_lock=True):
            if os.path.basename(path) == "members.json":
                raise OSError("Simulated disk write failure on members.json")
            return orig_atomic(path, data, mode=mode, use_lock=use_lock)

        with patch.object(labops_mod, "atomic_write_json", side_effect=fail_on_members_json):
            r = self.client.post(
                "/api/v1/users",
                json=_member_payload("jsonfail_u"),
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
        self.assertEqual(r.status_code, 503)
        detail = r.json()["detail"]
        self.assertEqual(detail["code"], "JSON_PERSISTENCE_FAILED")
        recovery_id = detail["recovery_id"]
        self.assertTrue(recovery_id)

        # DB holds the committed member AND the pending recovery record
        with self.svc.db.session_scope() as s:
            rec = s.get(PersistenceRecoveryRecord, recovery_id)
            self.assertIsNotNone(rec)
            self.assertEqual(rec.status, "PENDING_RECOVERY")
            db_row = s.query(MemberRecord).filter(MemberRecord.username == "jsonfail_u").first()
            self.assertIsNotNone(db_row)

        # Retrying same username MUST NOT create duplicate
        r_dup = self.client.post(
            "/api/v1/users",
            json=_member_payload("jsonfail_u"),
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(r_dup.status_code, 409)

        # Restarting service reconciles JSON from DB and resolves recovery record
        restarted = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        try:
            self.assertEqual(sum(1 for m in restarted.members if m["username"] == "jsonfail_u"), 1)
            with open(os.path.join(self.tmp.name, "members.json"), "r", encoding="utf-8") as mf:
                disk_members = json.load(mf)
            self.assertEqual(sum(1 for m in disk_members if m["username"] == "jsonfail_u"), 1)
            with restarted.db.session_scope() as s:
                rec_after = s.get(PersistenceRecoveryRecord, recovery_id)
                self.assertEqual(rec_after.status, "RECOVERED")
                self.assertIsNotNone(rec_after.resolved_at)
        finally:
            restarted.db.engine.dispose()

    def test_07_failure_after_ssh_and_workspace_creation_preserves_data_and_restart_consistent(self) -> None:
        ws_dir = os.path.join(self.users_dir, "workstation", "midfail_u")
        os.makedirs(ws_dir, exist_ok=True)
        artifact = os.path.join(ws_dir, "checkpoint.pt")
        with open(artifact, "w", encoding="utf-8") as f:
            f.write("weights")

        with patch.object(self.svc.ssh_access_manager, "issue_temporary_password", side_effect=RuntimeError("vault write error")):
            with self.assertRaises(RuntimeError):
                self.svc.provision_member_ssh_access({**_member_payload("midfail_u"), "_actor_id": "admin"})

        self.assertTrue(os.path.isfile(artifact))
        self.assertEqual([m for m in self.svc.members if m["username"] == "midfail_u"], [])

        with patch.object(self.svc, "_is_host_online", return_value=False):
            m = self.svc.provision_member_ssh_access({**_member_payload("midfail_u"), "_actor_id": "admin"})
        _complete_delete(self.svc, m, remove_files=False)

        with patch.object(self.svc, "_is_host_online", return_value=False):
            m2 = self.svc.provision_member_ssh_access({**_member_payload("midfail_u"), "_actor_id": "admin"})

        restarted = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        try:
            loaded = [x for x in restarted.members if x["username"] == "midfail_u"]
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["id"], m2["id"])
        finally:
            restarted.db.engine.dispose()

    def test_08_revocation_isolation_old_jti_stays_revoked_new_account_authenticates(self) -> None:
        with patch.object(self.svc, "_is_host_online", return_value=False):
            old_m = self.svc.provision_member_ssh_access({**_member_payload("tok_user"), "_actor_id": "admin"})
        old_id = old_m["id"]
        old_secrets = self.svc.claim_member_secrets(old_id, actor="admin")
        old_temp_pw = old_secrets["temporary_password"]

        old_tok = issue_token("tok_user", "RESEARCHER", member_id=old_id, ttl=3600)
        old_legacy_tok = issue_token("tok_user", "RESEARCHER", member_id=None, ttl=3600)
        old_explicit_jti_tok = issue_token("tok_user", "RESEARCHER", member_id=old_id, jti="jti-explicit-old", ttl=3600)
        get_revocation_store().revoke_jti("jti-explicit-old", 9999999999, reason="test_logout", member_id=old_id)

        _complete_delete(self.svc, old_m, remove_files=False)

        # Recreate immediately within the same second
        with patch.object(self.svc, "_is_host_online", return_value=False):
            new_m = self.svc.provision_member_ssh_access({**_member_payload("tok_user"), "_actor_id": "admin"})
        new_id = new_m["id"]
        new_secrets = self.svc.claim_member_secrets(new_id, actor="admin")
        new_temp_pw = new_secrets["temporary_password"]

        # Old password fails login on new account
        r_old_pw = self.client.post("/api/v1/auth/login", json={"username": "tok_user", "password": old_temp_pw})
        self.assertEqual(r_old_pw.status_code, 401)

        # Complete first-login activation for new member and login via HTTP API
        r_chg = self.client.post(
            "/api/v1/v2/ssh-access/first-login-change-password",
            json={"username": "tok_user", "temporary_password": new_temp_pw, "new_password": "NewStrong!Pass2026"},
            headers={"Authorization": f"Bearer {issue_token('tok_user', 'RESEARCHER', member_id=new_id)}"},
        )
        self.assertEqual(r_chg.status_code, 200, r_chg.text)

        r_login = self.client.post("/api/v1/auth/login", json={"username": "tok_user", "password": "NewStrong!Pass2026"})
        self.assertEqual(r_login.status_code, 200, r_login.text)
        new_tok = r_login.json()["access_token"]
        self.assertEqual(r_login.json()["member_id"], new_id)

        # New token succeeds
        r_me_new = self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_tok}"})
        self.assertEqual(r_me_new.status_code, 200)
        self.assertEqual(r_me_new.json()["member_id"], new_id)

        # Repository token for new member succeeds while old member repository token is revoked
        new_repo_tok = issue_repository_token("tok_user", new_id, "ws-new", "workstation")["token"]
        old_repo_tok = issue_repository_token("tok_user", old_id, "ws-old", "workstation")["token"]
        self.assertEqual(verify_repository_token(new_repo_tok, "repository:request").member_id, new_id)
        with self.assertRaises(AuthError):
            verify_repository_token(old_repo_tok, "repository:request")

        # All old tokens (member_id=old_id, legacy member_id=None, and explicit JTI) MUST stay revoked (401)!
        for revoked_tok in (old_tok, old_legacy_tok, old_explicit_jti_tok):
            with self.assertRaises(AuthError):
                verify_token(revoked_tok)
            self.assertEqual(
                self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {revoked_tok}"}).status_code,
                401,
            )

    def test_09_legacy_schema_migration_and_postgresql_ddl_compatibility(self) -> None:
        # 1. Verify PostgreSQL DDL emits partial unique index
        ddl = str(CreateIndex(next(i for i in MemberRecord.__table__.indexes if i.name == "uq_members_active_username")).compile(dialect=postgresql.dialect()))
        self.assertIn("CREATE UNIQUE INDEX uq_members_active_username ON members (username)", ddl)
        self.assertIn("WHERE status != 'DELETED' OR deleted_at IS NULL", ddl)

        # 2. Build an unmigrated legacy SQLite DB matching live production schema
        mig_dir = tempfile.TemporaryDirectory(prefix="labops-mig-upgrade-")
        try:
            db_path = os.path.join(mig_dir.name, "labops_v2.sqlite3")
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE hosts (id VARCHAR(64) PRIMARY KEY, hostname VARCHAR(128) NOT NULL UNIQUE, display_name VARCHAR(160) NOT NULL, role VARCHAR(64) NOT NULL, management_ip VARCHAR(64) NOT NULL, vpn_ip VARCHAR(64), agent_url VARCHAR(255), status VARCHAR(32) NOT NULL, freshness_status VARCHAR(32) NOT NULL, last_heartbeat_at VARCHAR(64), metadata_json JSON NOT NULL, updated_at DATETIME NOT NULL);
                CREATE TABLE members (
                    id VARCHAR(64) PRIMARY KEY, username VARCHAR(64) NOT NULL, email VARCHAR(255) NOT NULL, full_name VARCHAR(160) NOT NULL,
                    role VARCHAR(32) NOT NULL, status VARCHAR(48) NOT NULL, provisioning_state VARCHAR(64) NOT NULL, assigned_server VARCHAR(64) NOT NULL,
                    workspace_name VARCHAR(128) NOT NULL, workspace_dir VARCHAR(255), ssh_port INTEGER, storage_quota_gb INTEGER NOT NULL,
                    cpu_limit_cores INTEGER NOT NULL, ram_limit_gb INTEGER NOT NULL, vram_quota_gb INTEGER NOT NULL, gpu_permissions_json JSON NOT NULL,
                    archived_at VARCHAR(64), archive_manifest_json JSON, deleted_at VARCHAR(64), version INTEGER NOT NULL,
                    payload_json JSON NOT NULL, created_at VARCHAR(64) NOT NULL, updated_at DATETIME NOT NULL
                );
                CREATE UNIQUE INDEX ix_members_username ON members (username);
                CREATE INDEX ix_members_status ON members (status);
                CREATE TABLE workspaces (id VARCHAR(64) PRIMARY KEY, name VARCHAR(128) NOT NULL UNIQUE, owner VARCHAR(64) NOT NULL, host_id VARCHAR(64) NOT NULL, container_id VARCHAR(128), runtime_type VARCHAR(32) NOT NULL, image_ref VARCHAR(255) NOT NULL, ssh_host_port INTEGER, lifecycle_state VARCHAR(48) NOT NULL, observed_state VARCHAR(48) NOT NULL, non_privileged BOOLEAN NOT NULL, docker_socket_mounted BOOLEAN NOT NULL, limits_json JSON NOT NULL, created_at VARCHAR(64) NOT NULL, updated_at DATETIME NOT NULL);
                CREATE TABLE gpu_reservations (id VARCHAR(64) PRIMARY KEY, gpu_uuid VARCHAR(128) NOT NULL, gpu_id VARCHAR(64), host_id VARCHAR(64), owner VARCHAR(64) NOT NULL, requested_by VARCHAR(64) NOT NULL, job_name VARCHAR(200) NOT NULL, mode VARCHAR(64) NOT NULL, requested_vram_gb INTEGER NOT NULL, state VARCHAR(32) NOT NULL, runtime_verified BOOLEAN NOT NULL, pid INTEGER, reserved_at VARCHAR(64) NOT NULL, started_at VARCHAR(64), expires_at VARCHAR(64), ended_at VARCHAR(64));
                CREATE TABLE audit_events (id VARCHAR(64) PRIMARY KEY, timestamp VARCHAR(64) NOT NULL, actor VARCHAR(128) NOT NULL, action VARCHAR(96) NOT NULL, target_type VARCHAR(64) NOT NULL, target_id VARCHAR(255) NOT NULL, result VARCHAR(48) NOT NULL, correlation_id VARCHAR(96) NOT NULL, details TEXT NOT NULL);
                CREATE TABLE idempotency_records (id INTEGER PRIMARY KEY AUTOINCREMENT, actor VARCHAR(128) NOT NULL, endpoint VARCHAR(160) NOT NULL, idempotency_key VARCHAR(160) NOT NULL, request_hash VARCHAR(128) NOT NULL, status_code INTEGER NOT NULL, response_json JSON NOT NULL, created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL);
                CREATE TABLE revoked_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, jti VARCHAR(128) UNIQUE, username VARCHAR(128), revoked_before_epoch INTEGER, reason VARCHAR(128) NOT NULL, expires_at INTEGER NOT NULL, created_at DATETIME NOT NULL);
                CREATE TABLE deletion_operations (op_id VARCHAR(64) PRIMARY KEY, member_id VARCHAR(64) NOT NULL, username VARCHAR(64) NOT NULL, actor VARCHAR(128) NOT NULL, dry_run BOOLEAN NOT NULL, status VARCHAR(48) NOT NULL, precheck_json JSON NOT NULL, steps_json JSON NOT NULL, error_detail TEXT, created_at DATETIME NOT NULL, completed_at DATETIME);
                CREATE TABLE zalo_outbox (id VARCHAR(64) PRIMARY KEY, month_key VARCHAR(16) NOT NULL, recipient_id VARCHAR(128) NOT NULL, is_critical BOOLEAN NOT NULL, status VARCHAR(32) NOT NULL, message_snippet VARCHAR(255) NOT NULL, idempotency_scope VARCHAR(255), error_detail TEXT, created_at DATETIME NOT NULL, sent_at DATETIME);
                INSERT INTO members VALUES (
                    'mem-0226d821478c','testacc','testacc@lab.ai.vn','Testacc','RESEARCHER','DELETED','DELETED','track','ws-testacc',
                    '/tmp/ws',22001,10,4,8,0,'[]','2026-11-19T09:29:39+00:00',NULL,'2026-11-19T09:29:46+00:00',3,'{}','2026-11-11','2026-11-19 09:29:46'
                );
                INSERT INTO deletion_operations VALUES (
                    'op-del-25c8ea9f2a10','mem-0226d821478c','testacc','admin',0,'COMPLETED','{}','{}',NULL,'2026-11-19 09:29:44','2026-11-19 09:29:46'
                );
                """
            )
            conn.commit()
            conn.close()

            # Before migration: fails closed cleanly BEFORE creating phantom JSON entry
            unmigrated = LabOpsDataService(demo_mode=False, data_dir=mig_dir.name)
            try:
                with self.assertRaises(MemberPersistenceError) as ctx:
                    unmigrated.provision_member(_member_payload("testacc"))
                self.assertEqual(ctx.exception.code, "SCHEMA_MIGRATION_REQUIRED")
                self.assertEqual(unmigrated.members, [])
            finally:
                unmigrated.db.engine.dispose()

            # Run explicit upgrade
            mig_res = upgrade_database_schema(f"sqlite:///{db_path}")
            self.assertIn(
                mig_res["current_revision"],
                {"0002_member_username_reuse", "0003_labops_ai_sre_agent_v2", "0004_labops_mram_v1"},
            )
            self.assertFalse(mig_res["after"]["has_legacy_username_unique_index"])
            self.assertTrue(mig_res["after"]["has_active_username_partial_index"])

            # Idempotent re-run succeeds
            upgrade_database_schema(f"sqlite:///{db_path}")

            migrated = LabOpsDataService(demo_mode=False, data_dir=mig_dir.name)
            try:
                created = migrated.provision_member(_member_payload("testacc"))
                self.assertNotEqual(created["id"], "mem-0226d821478c")
                with migrated.db.session_scope() as s:
                    rows = s.query(MemberRecord).filter(MemberRecord.username == "testacc").all()
                    self.assertEqual(len(rows), 2)
            finally:
                migrated.db.engine.dispose()
        finally:
            mig_dir.cleanup()

    def test_10_repair_script_dry_run_apply_verify_and_rollback_in_temp_dir(self) -> None:
        rep_dir = tempfile.TemporaryDirectory(prefix="labops-repair-sim-")
        try:
            d = rep_dir.name
            db_path = os.path.join(d, "labops_v2.sqlite3")
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE hosts (id VARCHAR(64) PRIMARY KEY, hostname VARCHAR(128) NOT NULL UNIQUE, display_name VARCHAR(160) NOT NULL, role VARCHAR(64) NOT NULL, management_ip VARCHAR(64) NOT NULL, vpn_ip VARCHAR(64), agent_url VARCHAR(255), status VARCHAR(32) NOT NULL, freshness_status VARCHAR(32) NOT NULL, last_heartbeat_at VARCHAR(64), metadata_json JSON NOT NULL, updated_at DATETIME NOT NULL);
                CREATE TABLE members (
                    id VARCHAR(64) PRIMARY KEY, username VARCHAR(64) NOT NULL, email VARCHAR(255) NOT NULL, full_name VARCHAR(160) NOT NULL,
                    role VARCHAR(32) NOT NULL, status VARCHAR(48) NOT NULL, provisioning_state VARCHAR(64) NOT NULL, assigned_server VARCHAR(64) NOT NULL,
                    workspace_name VARCHAR(128) NOT NULL, workspace_dir VARCHAR(255), ssh_port INTEGER, storage_quota_gb INTEGER NOT NULL,
                    cpu_limit_cores INTEGER NOT NULL, ram_limit_gb INTEGER NOT NULL, vram_quota_gb INTEGER NOT NULL, gpu_permissions_json JSON NOT NULL,
                    archived_at VARCHAR(64), archive_manifest_json JSON, deleted_at VARCHAR(64), version INTEGER NOT NULL,
                    payload_json JSON NOT NULL, created_at VARCHAR(64) NOT NULL, updated_at DATETIME NOT NULL
                );
                CREATE UNIQUE INDEX ix_members_username ON members (username);
                CREATE INDEX ix_members_status ON members (status);
                CREATE TABLE workspaces (id VARCHAR(64) PRIMARY KEY, name VARCHAR(128) NOT NULL UNIQUE, owner VARCHAR(64) NOT NULL, host_id VARCHAR(64) NOT NULL, container_id VARCHAR(128), runtime_type VARCHAR(32) NOT NULL, image_ref VARCHAR(255) NOT NULL, ssh_host_port INTEGER, lifecycle_state VARCHAR(48) NOT NULL, observed_state VARCHAR(48) NOT NULL, non_privileged BOOLEAN NOT NULL, docker_socket_mounted BOOLEAN NOT NULL, limits_json JSON NOT NULL, created_at VARCHAR(64) NOT NULL, updated_at DATETIME NOT NULL);
                CREATE TABLE gpu_reservations (id VARCHAR(64) PRIMARY KEY, gpu_uuid VARCHAR(128) NOT NULL, gpu_id VARCHAR(64), host_id VARCHAR(64), owner VARCHAR(64) NOT NULL, requested_by VARCHAR(64) NOT NULL, job_name VARCHAR(200) NOT NULL, mode VARCHAR(64) NOT NULL, requested_vram_gb INTEGER NOT NULL, state VARCHAR(32) NOT NULL, runtime_verified BOOLEAN NOT NULL, pid INTEGER, reserved_at VARCHAR(64) NOT NULL, started_at VARCHAR(64), expires_at VARCHAR(64), ended_at VARCHAR(64));
                CREATE TABLE audit_events (id VARCHAR(64) PRIMARY KEY, timestamp VARCHAR(64) NOT NULL, actor VARCHAR(128) NOT NULL, action VARCHAR(96) NOT NULL, target_type VARCHAR(64) NOT NULL, target_id VARCHAR(255) NOT NULL, result VARCHAR(48) NOT NULL, correlation_id VARCHAR(96) NOT NULL, details TEXT NOT NULL);
                CREATE TABLE idempotency_records (id INTEGER PRIMARY KEY AUTOINCREMENT, actor VARCHAR(128) NOT NULL, endpoint VARCHAR(160) NOT NULL, idempotency_key VARCHAR(160) NOT NULL, request_hash VARCHAR(128) NOT NULL, status_code INTEGER NOT NULL, response_json JSON NOT NULL, created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL);
                CREATE TABLE revoked_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, jti VARCHAR(128) UNIQUE, username VARCHAR(128), revoked_before_epoch INTEGER, reason VARCHAR(128) NOT NULL, expires_at INTEGER NOT NULL, created_at DATETIME NOT NULL);
                CREATE TABLE deletion_operations (op_id VARCHAR(64) PRIMARY KEY, member_id VARCHAR(64) NOT NULL, username VARCHAR(64) NOT NULL, actor VARCHAR(128) NOT NULL, dry_run BOOLEAN NOT NULL, status VARCHAR(48) NOT NULL, precheck_json JSON NOT NULL, steps_json JSON NOT NULL, error_detail TEXT, created_at DATETIME NOT NULL, completed_at DATETIME);
                CREATE TABLE zalo_outbox (id VARCHAR(64) PRIMARY KEY, month_key VARCHAR(16) NOT NULL, recipient_id VARCHAR(128) NOT NULL, is_critical BOOLEAN NOT NULL, status VARCHAR(32) NOT NULL, message_snippet VARCHAR(255) NOT NULL, idempotency_scope VARCHAR(255), error_detail TEXT, created_at DATETIME NOT NULL, sent_at DATETIME);
                INSERT INTO members VALUES (
                    'mem-0226d821478c','testacc','testacc@lab.ai.vn','Testacc','RESEARCHER','DELETED','DELETED','track','ws-testacc',
                    '/tmp/old',22001,10,4,8,0,'[]','2026-11-19T09:29:39+00:00',NULL,'2026-11-19T09:29:46+00:00',3,'{}','2026-11-11','2026-11-19 09:29:46'
                );
                INSERT INTO deletion_operations VALUES (
                    'op-del-25c8ea9f2a10','mem-0226d821478c','testacc','admin',0,'COMPLETED','{}','{}',NULL,'2026-11-19 09:29:44','2026-11-19 09:29:46'
                );
                """
            )
            conn.commit()
            conn.close()

            atomic_write_json(os.path.join(d, "members.json"), [
                {"id": ORPHAN_MEMBER_ID, "username": "testacc", "status": "SUSPENDED", "wireguard": {"devices": []}},
                {"id": "mem-vinh_test", "username": "vinh_test", "status": "READY"},
            ])
            atomic_write_json(os.path.join(d, "ssh_endpoints.json"), [
                {"id": ORPHAN_ENDPOINT_ID, "member_id": ORPHAN_MEMBER_ID, "listen_port": 22001},
                {"id": "ep-keep", "member_id": "mem-vinh_test", "listen_port": 22002},
            ])
            atomic_write_json(os.path.join(d, "v2_workspaces.json"), {
                "workspaces": [{"id": ORPHAN_WORKSPACE_ID, "owner": "testacc", "container_id": None}],
                "idempotency": {},
            })
            atomic_write_json(os.path.join(d, "v2_ssh_accounts.json"), {
                "accounts": {"testacc": {"member_id": HISTORICAL_MEMBER_ID, "password_status": "REVOKED", "activation_status": "REVOKED"}},
            })
            atomic_write_json(os.path.join(d, "v2_ssh_keys.json"), {
                "keys": [{"id": "sshkey-7bdc435153", "username": "testacc", "desired_state": "REVOKED", "observed_state": "VERIFIED_REVOKED"}],
            })
            atomic_write_json(os.path.join(d, "revoked_tokens.json"), {
                "jtis": {},
                "subjects": {"testacc": {"revoked_before": 1795137865, "updated_at": 1795137864, "reason": "suspended:admin"}},
            })
            atomic_write_json(os.path.join(d, "audit_logs.json"), [])

            # Seed dummy workspace file on disk -> repair MUST NOT delete it
            ws_disk = os.path.join(d, "workspaces", "workstation", "testacc", "work")
            os.makedirs(ws_disk, exist_ok=True)
            preserved_file = os.path.join(ws_disk, "keep.txt")
            with open(preserved_file, "w", encoding="utf-8") as kf:
                kf.write("preserve")

            dry = inspect_state(d, skip_running_check=True)
            self.assertTrue(dry["prechecks"]["ok"], dry)

            applied = apply_repair(d, skip_running_check=True)
            self.assertEqual(applied["status"], "APPLIED")
            self.assertTrue(applied["postchecks"]["ok"])
            self.assertTrue(os.path.isfile(preserved_file))

            # Verify historical member preserved and recreation now succeeds cleanly
            repaired_svc = LabOpsDataService(demo_mode=False, data_dir=d)
            try:
                recreated = repaired_svc.provision_member(_member_payload("testacc"))
                self.assertNotIn(recreated["id"], {HISTORICAL_MEMBER_ID, ORPHAN_MEMBER_ID})
            finally:
                repaired_svc.db.engine.dispose()

            # Rollback restores exact pre-repair snapshot
            rb = rollback_from_backup(applied["backup_dir"], data_dir=d)
            self.assertEqual(rb["status"], "ROLLED_BACK")
            with open(os.path.join(d, "members.json"), "r", encoding="utf-8") as mf:
                self.assertTrue(any(m["id"] == ORPHAN_MEMBER_ID for m in json.load(mf)))
        finally:
            rep_dir.cleanup()

    def test_11_legacy_unmigrated_orphan_does_not_break_repository_requests_or_audit_saves(self) -> None:
        leg_dir = tempfile.TemporaryDirectory(prefix="labops-legacy-repo-")
        leg_svc = None
        mounted = []
        try:
            d = leg_dir.name
            db_path = os.path.join(d, "labops_v2.sqlite3")
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE hosts (id VARCHAR(64) PRIMARY KEY, hostname VARCHAR(128) NOT NULL UNIQUE, display_name VARCHAR(160) NOT NULL, role VARCHAR(64) NOT NULL, management_ip VARCHAR(64) NOT NULL, vpn_ip VARCHAR(64), agent_url VARCHAR(255), status VARCHAR(32) NOT NULL, freshness_status VARCHAR(32) NOT NULL, last_heartbeat_at VARCHAR(64), metadata_json JSON NOT NULL, updated_at DATETIME NOT NULL);
                CREATE TABLE members (
                    id VARCHAR(64) PRIMARY KEY, username VARCHAR(64) NOT NULL, email VARCHAR(255) NOT NULL, full_name VARCHAR(160) NOT NULL,
                    role VARCHAR(32) NOT NULL, status VARCHAR(48) NOT NULL, provisioning_state VARCHAR(64) NOT NULL, assigned_server VARCHAR(64) NOT NULL,
                    workspace_name VARCHAR(128) NOT NULL, workspace_dir VARCHAR(255), ssh_port INTEGER, storage_quota_gb INTEGER NOT NULL,
                    cpu_limit_cores INTEGER NOT NULL, ram_limit_gb INTEGER NOT NULL, vram_quota_gb INTEGER NOT NULL, gpu_permissions_json JSON NOT NULL,
                    archived_at VARCHAR(64), archive_manifest_json JSON, deleted_at VARCHAR(64), version INTEGER NOT NULL,
                    payload_json JSON NOT NULL, created_at VARCHAR(64) NOT NULL, updated_at DATETIME NOT NULL
                );
                CREATE UNIQUE INDEX ix_members_username ON members (username);
                CREATE INDEX ix_members_status ON members (status);
                CREATE TABLE workspaces (id VARCHAR(64) PRIMARY KEY, name VARCHAR(128) NOT NULL UNIQUE, owner VARCHAR(64) NOT NULL, host_id VARCHAR(64) NOT NULL, container_id VARCHAR(128), runtime_type VARCHAR(32) NOT NULL, image_ref VARCHAR(255) NOT NULL, ssh_host_port INTEGER, lifecycle_state VARCHAR(48) NOT NULL, observed_state VARCHAR(48) NOT NULL, non_privileged BOOLEAN NOT NULL, docker_socket_mounted BOOLEAN NOT NULL, limits_json JSON NOT NULL, created_at VARCHAR(64) NOT NULL, updated_at DATETIME NOT NULL);
                CREATE TABLE gpu_reservations (id VARCHAR(64) PRIMARY KEY, gpu_uuid VARCHAR(128) NOT NULL, gpu_id VARCHAR(64), host_id VARCHAR(64), owner VARCHAR(64) NOT NULL, requested_by VARCHAR(64) NOT NULL, job_name VARCHAR(200) NOT NULL, mode VARCHAR(64) NOT NULL, requested_vram_gb INTEGER NOT NULL, state VARCHAR(32) NOT NULL, runtime_verified BOOLEAN NOT NULL, pid INTEGER, reserved_at VARCHAR(64) NOT NULL, started_at VARCHAR(64), expires_at VARCHAR(64), ended_at VARCHAR(64));
                CREATE TABLE audit_events (id VARCHAR(64) PRIMARY KEY, timestamp VARCHAR(64) NOT NULL, actor VARCHAR(128) NOT NULL, action VARCHAR(96) NOT NULL, target_type VARCHAR(64) NOT NULL, target_id VARCHAR(255) NOT NULL, result VARCHAR(48) NOT NULL, correlation_id VARCHAR(96) NOT NULL, details TEXT NOT NULL);
                CREATE TABLE idempotency_records (id INTEGER PRIMARY KEY AUTOINCREMENT, actor VARCHAR(128) NOT NULL, endpoint VARCHAR(160) NOT NULL, idempotency_key VARCHAR(160) NOT NULL, request_hash VARCHAR(128) NOT NULL, status_code INTEGER NOT NULL, response_json JSON NOT NULL, created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL);
                CREATE TABLE revoked_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, jti VARCHAR(128) UNIQUE, username VARCHAR(128), revoked_before_epoch INTEGER, reason VARCHAR(128) NOT NULL, expires_at INTEGER NOT NULL, created_at DATETIME NOT NULL);
                CREATE TABLE deletion_operations (op_id VARCHAR(64) PRIMARY KEY, member_id VARCHAR(64) NOT NULL, username VARCHAR(64) NOT NULL, actor VARCHAR(128) NOT NULL, dry_run BOOLEAN NOT NULL, status VARCHAR(48) NOT NULL, precheck_json JSON NOT NULL, steps_json JSON NOT NULL, error_detail TEXT, created_at DATETIME NOT NULL, completed_at DATETIME);
                CREATE TABLE zalo_outbox (id VARCHAR(64) PRIMARY KEY, month_key VARCHAR(16) NOT NULL, recipient_id VARCHAR(128) NOT NULL, is_critical BOOLEAN NOT NULL, status VARCHAR(32) NOT NULL, message_snippet VARCHAR(255) NOT NULL, idempotency_scope VARCHAR(255), error_detail TEXT, created_at DATETIME NOT NULL, sent_at DATETIME);
                INSERT INTO members VALUES (
                    'mem-0226d821478c','testacc','testacc@lab.ai.vn','Testacc','RESEARCHER','DELETED','DELETED','track','ws-testacc',
                    '/tmp/old',22001,10,4,8,0,'[]','2026-11-19T09:29:39+00:00',NULL,'2026-11-19T09:29:46+00:00',3,'{}','2026-11-11','2026-11-19 09:29:46'
                );
                INSERT INTO deletion_operations VALUES (
                    'op-del-25c8ea9f2a10','mem-0226d821478c','testacc','admin',0,'COMPLETED','{}','{}',NULL,'2026-11-19 09:29:44','2026-11-19 09:29:46'
                );
                """
            )
            conn.commit()
            conn.close()

            atomic_write_json(os.path.join(d, "members.json"), [
                {
                    "id": ORPHAN_MEMBER_ID,
                    "username": "testacc",
                    "status": "DELETE_ELIGIBLE",
                    "provisioning_state": "ARCHIVED",
                    "wireguard": {"devices": []},
                }
            ])

            leg_svc = LabOpsDataService(demo_mode=False, data_dir=d)
            targets = [auth_ep, users_ep, admin_ep, v2_ep, repo_ep]
            mounted = [(mod, mod.service) for mod in targets]
            for mod in targets:
                mod.service = leg_svc

            class _FakeAgentResp:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return json.dumps({
                        "id": "req-legacy-1",
                        "repo_url": "https://github.com/fosrl/pangolin",
                        "state": "QUEUED",
                    }).encode("utf-8")

            with patch.dict(os.environ, {"LABOPS_AGENT_SECRET": "test-agent-secret"}, clear=False):
                with patch("urllib.request.urlopen", return_value=_FakeAgentResp()):
                    r_repo = self.client.post(
                        "/api/v1/repositories/requests",
                        json={"repo_url": "https://github.com/fosrl/pangolin", "ref": "HEAD"},
                        headers={"Authorization": f"Bearer {self.admin_token}"},
                    )
            self.assertEqual(r_repo.status_code, 202, r_repo.text)

            with leg_svc.db.session_scope() as s:
                aud_row = s.query(AuditEventRecord).filter(AuditEventRecord.action == "REPOSITORY_GUARD_SUBMIT").first()
                self.assertIsNotNone(aud_row)

            # _save_persistent_data() on unrelated changes must not fail on the legacy quarantined orphan
            leg_svc._save_persistent_data()

            # Recreating 'testacc' while orphan mem-aaf261206f13 is still un-deleted in members.json must fail closed with 409
            r_recreate = self.client.post(
                "/api/v1/users",
                json=_member_payload("testacc"),
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
            self.assertEqual(r_recreate.status_code, 409)
            self.assertEqual(r_recreate.json()["detail"]["code"], "USERNAME_ALREADY_EXISTS")
        finally:
            for mod, orig in mounted:
                mod.service = orig
            if leg_svc is not None:
                try:
                    leg_svc.db.engine.dispose()
                except Exception:
                    pass
            leg_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
