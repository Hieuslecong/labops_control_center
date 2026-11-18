"""Milestone 2 Comprehensive Hardening & Regression Suite."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import inspect

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault(
    "LABOPS_SESSION_SECRET",
    "m2-test-session-secret-0123456789abcdef0123456789abcdef",
)

import app.api.v1.endpoints.ai_assistant as ai_ep
import app.api.v1.endpoints.auth as auth_ep
import app.api.v1.endpoints.gpus as gpus_ep
import app.api.v1.endpoints.processes as proc_ep
import app.api.v1.endpoints.repositories as repo_ep
import app.api.v1.endpoints.users as users_ep
import app.api.v1.endpoints.v2_platform as v2_ep
from app.core.delegation import (
    DELEGATION_AUDIENCE,
    build_delegation_context,
    compute_params_hash,
)
from app.core.security import (
    AuthError,
    get_revocation_store,
    issue_token,
    verify_step_up_token,
)
from app.db.atomic_json import PersistenceCorruptionError
from app.db.idempotency import IdempotencyConflictError, IdempotencyStore
from app.db.models import DeletionOperationRecord
from app.db.session import DatabaseManager
from app.main import app
from app.services.gpu_scheduler import GpuScheduler, SchedulerError
from app.services.labops_service import LabOpsDataService
from app.services.workspace_controller import WorkspaceController
from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
from app.services.zalo_assistant.bot_core import ZaloBotEngine
from app.services.zalo_assistant.quota_ledger import QuotaLedger
from app.services.zalo_assistant.report_generator import ReportGenerator
from app.services.zalo_assistant.service import ZaloAssistantService
from app.services.zalo_assistant.worker import ZaloPollingWorker


class _PatchedServiceMixin:
    def _patch_endpoints_service(self, svc: LabOpsDataService):
        targets = [auth_ep, gpus_ep, proc_ep, repo_ep, users_ep, v2_ep]
        self._orig_services = [(mod, mod.service) for mod in targets]
        for mod in targets:
            mod.service = svc

    def _restore_endpoints_service(self):
        for mod, orig in getattr(self, "_orig_services", []):
            mod.service = orig


class TestMilestone2AuthAndLockout(unittest.TestCase, _PatchedServiceMixin):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="m2-auth-")
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        self._patch_endpoints_service(self.svc)
        auth_ep._FAILED_ATTEMPTS.clear()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        auth_ep._FAILED_ATTEMPTS.clear()
        self._restore_endpoints_service()
        self.tmp.cleanup()

    def test_login_lockout_logout_jti_revocation_and_actor_spoof_rejected(self) -> None:
        # 1. X-LabOps-Actor without valid signed Bearer token is rejected 401
        r_spoof = self.client.get("/api/v1/users", headers={"X-LabOps-Actor": "root"})
        self.assertEqual(r_spoof.status_code, 401)

        res_tok = issue_token("res_spoof_check", "RESEARCHER")
        r_res = self.client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {res_tok}", "X-LabOps-Actor": "admin"},
        )
        self.assertEqual(r_res.status_code, 403)

        # 2. 5 failed logins lock out 6th attempt with 429
        with patch.dict(os.environ, {"LABOPS_ADMIN_USERNAME": "admin", "LABOPS_ADMIN_PASSWORD": "StrongAdminPass!2026"}, clear=False):
            for _ in range(5):
                r_bad = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
                self.assertEqual(r_bad.status_code, 401)

            r_locked = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "StrongAdminPass!2026"})
            self.assertEqual(r_locked.status_code, 429)

            # Clear failed attempt bucket and perform clean login -> /me -> /logout -> /me revoked 401
            auth_ep._FAILED_ATTEMPTS.clear()
            r_ok = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "StrongAdminPass!2026"})
            self.assertEqual(r_ok.status_code, 200)
            tok = r_ok.json()["access_token"]

            r_me = self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
            self.assertEqual(r_me.status_code, 200)
            self.assertEqual(r_me.json()["role"], "ADMIN")

            r_out = self.client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {tok}"})
            self.assertEqual(r_out.status_code, 200)
            self.assertTrue(r_out.json()["revoked"])

            r_me_after = self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
            self.assertEqual(r_me_after.status_code, 401)

    def test_suspend_immediately_revokes_active_member_token_and_step_up_is_single_use(self) -> None:
        uname = f"m2susp_{uuid.uuid4().hex[:6]}"
        member = self.svc.provision_member({
            "full_name": "Suspend Target",
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
        tok = issue_token(uname, "RESEARCHER", member_id=member["id"])
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code,
            200,
        )

        # Suspending member must invalidate already-issued session tokens immediately
        self.svc.suspend_member_lifecycle(member["id"], actor="admin", reason="Security test")
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code,
            401,
        )

        # Step-up token is action + target bound and single-use
        admin_tok = issue_token("admin", "ADMIN")
        with patch.dict(os.environ, {"LABOPS_ADMIN_USERNAME": "admin", "LABOPS_ADMIN_PASSWORD": "StepUpPass!2026"}, clear=False):
            r_step = self.client.post(
                "/api/v1/auth/step-up",
                json={"password": "StepUpPass!2026", "action": "DELETE_MEMBER", "target_id": member["id"]},
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
        self.assertEqual(r_step.status_code, 200)
        step_tok = r_step.json()["step_up_token"]

        # Wrong target_id rejected 403 BEFORE consuming JTI? Note wrong target raises before revoke_jti
        with self.assertRaises(AuthError) as ctx_wrong:
            verify_step_up_token(
                step_tok,
                expected_username="admin",
                expected_action="DELETE_MEMBER",
                expected_target_id="mem-wrong-target",
            )
        self.assertEqual(ctx_wrong.exception.status, 403)

        # First valid consumption succeeds
        claims = verify_step_up_token(
            step_tok,
            expected_username="admin",
            expected_action="DELETE_MEMBER",
            expected_target_id=member["id"],
        )
        self.assertEqual(claims["tgt"], member["id"])

        # Second replay fails 403 STEP_UP_REPLAYED
        with self.assertRaises(AuthError) as ctx_replay:
            verify_step_up_token(
                step_tok,
                expected_username="admin",
                expected_action="DELETE_MEMBER",
                expected_target_id=member["id"],
            )
        self.assertEqual(ctx_replay.exception.status, 403)


class TestMilestone2PersistenceAndMigrations(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="m2-db-")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_alembic_upgrade_downgrade_roundtrip_and_corrupt_json_fail_closed(self) -> None:
        from sqlalchemy import create_engine

        db_file = os.path.join(self.tmp.name, "mig_test.sqlite3")
        cfg = AlembicConfig(os.path.join(backend_dir, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")

        expected = {
            "hosts",
            "members",
            "workspaces",
            "gpu_reservations",
            "audit_events",
            "idempotency_records",
            "revoked_tokens",
            "deletion_operations",
            "zalo_outbox",
        }

        alembic_command.upgrade(cfg, "head")
        e1 = create_engine(f"sqlite:///{db_file}")
        tables_after_up = set(inspect(e1).get_table_names())
        e1.dispose()
        self.assertTrue(expected.issubset(tables_after_up), f"Missing tables: {expected - tables_after_up}")

        alembic_command.downgrade(cfg, "base")
        e2 = create_engine(f"sqlite:///{db_file}")
        tables_after_down = set(inspect(e2).get_table_names())
        e2.dispose()
        self.assertFalse(expected.intersection(tables_after_down))

        alembic_command.upgrade(cfg, "head")
        e3 = create_engine(f"sqlite:///{db_file}")
        tables_after_reup = set(inspect(e3).get_table_names())
        e3.dispose()
        self.assertTrue(expected.issubset(tables_after_reup))

        # Corrupt JSON must fail closed rather than silently regenerating empty state
        corrupt_dir = os.path.join(self.tmp.name, "corrupt")
        os.makedirs(corrupt_dir, exist_ok=True)
        with open(os.path.join(corrupt_dir, "zalo_quota_ledger.json"), "w", encoding="utf-8") as f:
            f.write("{corrupt")
        with self.assertRaises(PersistenceCorruptionError):
            QuotaLedger(corrupt_dir)

        with open(os.path.join(corrupt_dir, "zalo_reports.json"), "w", encoding="utf-8") as f:
            f.write("{corrupt")
        with self.assertRaises(PersistenceCorruptionError):
            ReportGenerator(corrupt_dir)

    def test_idempotency_store_scoped_actor_and_payload_conflict(self) -> None:
        db = DatabaseManager(self.tmp.name)
        store = IdempotencyStore(db)
        calls = {"count": 0}

        def compute(tag: str):
            def _fn():
                calls["count"] += 1
                return {"id": f"res-{calls['count']}", "tag": tag}
            return _fn

        r1 = store.check_or_store(
            actor="alice",
            endpoint="/api/v1/gpus/allocate",
            key="k-1",
            payload={"gpu": "gpu-0"},
            compute_fn=compute("alice"),
        )
        self.assertEqual(calls["count"], 1)
        self.assertEqual(r1["tag"], "alice")

        # Same actor + same key + same payload -> replay
        r1_replay = store.check_or_store(
            actor="alice",
            endpoint="/api/v1/gpus/allocate",
            key="k-1",
            payload={"gpu": "gpu-0"},
            compute_fn=compute("alice"),
        )
        self.assertEqual(calls["count"], 1)
        self.assertTrue(r1_replay["idempotent_replay"])

        # Same actor + same key + DIFFERENT payload -> 409 conflict
        with self.assertRaises(IdempotencyConflictError) as ctx:
            store.check_or_store(
                actor="alice",
                endpoint="/api/v1/gpus/allocate",
                key="k-1",
                payload={"gpu": "gpu-1"},
                compute_fn=compute("alice"),
            )
        self.assertEqual(ctx.exception.status_code, 409)

        # Different actor + same key -> independent execution
        r2 = store.check_or_store(
            actor="bob",
            endpoint="/api/v1/gpus/allocate",
            key="k-1",
            payload={"gpu": "gpu-0"},
            compute_fn=compute("bob"),
        )
        self.assertEqual(calls["count"], 2)
        self.assertEqual(r2["tag"], "bob")
        db.engine.dispose()


class TestMilestone2GpuSchedulerAndLifecycle(unittest.TestCase, _PatchedServiceMixin):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="m2-gpu-lc-")
        self.prev_user_dir = os.environ.get("LABOPS_USER_DATA_DIR")
        self.users_dir = os.path.join(self.tmp.name, "users")
        os.makedirs(self.users_dir, exist_ok=True)
        os.environ["LABOPS_USER_DATA_DIR"] = self.users_dir

        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        self._patch_endpoints_service(self.svc)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._restore_endpoints_service()
        if self.prev_user_dir is None:
            os.environ.pop("LABOPS_USER_DATA_DIR", None)
        else:
            os.environ["LABOPS_USER_DATA_DIR"] = self.prev_user_dir
        self.tmp.cleanup()

    def test_gpu_permissions_acl_ttl_expiry_and_nvml_reconciliation(self) -> None:
        fake_gpus = [{
            "id": "workstation-gpu-01",
            "display_label": "WS-GPU-01",
            "server_id": "workstation",
            "server_name": "Workstation",
            "model": "NVIDIA A30",
            "pci_bus_id": "00000000:D8:00.0",
            "uuid": "GPU-M2-A30-01",
            "vram_total_mb": 24576,
            "vram_used_mb": 0,
            "gpu_util_percent": 0,
            "temp_celsius": 42,
            "power_watts": 35,
            "power_limit_watts": 165,
            "allocation_mode": "FULL_GPU_EXCLUSIVE",
            "enforcement_state": "MONITOR_ONLY",
            "active_processes": [],
            "active_users": [],
            "associated_jobs": [],
        }]

        # Seed two researchers: alice has permission, bob does not
        self.svc.provision_member({
            "full_name": "Alice GPU",
            "username": "m2_alice",
            "email": "alice@example.org",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "workspace_name": "ws-m2_alice",
            "storage_quota_gb": 10,
            "mount_path": "/workspace",
            "cpu_limit_cores": 4,
            "ram_limit_gb": 16,
            "gpu_permissions": ["workstation-gpu-01"],
            "vram_quota_gb": 24,
        })
        self.svc.provision_member({
            "full_name": "Bob NoGPU",
            "username": "m2_bob",
            "email": "bob@example.org",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "workspace_name": "ws-m2_bob",
            "storage_quota_gb": 10,
            "mount_path": "/workspace",
            "cpu_limit_cores": 4,
            "ram_limit_gb": 16,
            "gpu_permissions": [],
            "vram_quota_gb": 0,
        })

        alice_tok = issue_token("m2_alice", "RESEARCHER")
        bob_tok = issue_token("m2_bob", "RESEARCHER")

        with patch.object(LabOpsDataService, "gpus", fake_gpus):
            with patch.object(self.svc.gpu_scheduler, "telemetry_fn", return_value=fake_gpus):
                # Bob without gpu_permissions denied 403
                r_bob = self.client.post(
                    "/api/v1/gpus/allocate",
                    json={"gpuId": "workstation-gpu-01", "mode": "FULL_GPU_EXCLUSIVE", "jobName": "unauth", "ttlMinutes": 30},
                    headers={"Authorization": f"Bearer {bob_tok}"},
                )
                self.assertEqual(r_bob.status_code, 403)
                self.assertIn("GPU_PERMISSION_DENIED", r_bob.text)

                # Alice with permission reserves GPU with Idempotency-Key & TTL
                r_a1 = self.client.post(
                    "/api/v1/gpus/allocate",
                    json={"gpuId": "workstation-gpu-01", "mode": "FULL_GPU_EXCLUSIVE", "requestedVramGb": 16, "jobName": "train-1", "ttlMinutes": 30},
                    headers={"Authorization": f"Bearer {alice_tok}", "Idempotency-Key": "rsv-k1"},
                )
                self.assertEqual(r_a1.status_code, 200)
                rsv = r_a1.json()["reservation"]
                self.assertEqual(rsv["state"], "RESERVED")

                # Payload mismatch on same Idempotency-Key returns 409
                r_conflict = self.client.post(
                    "/api/v1/gpus/allocate",
                    json={"gpuId": "workstation-gpu-01", "mode": "FULL_GPU_EXCLUSIVE", "requestedVramGb": 20, "jobName": "train-2", "ttlMinutes": 30},
                    headers={"Authorization": f"Bearer {alice_tok}", "Idempotency-Key": "rsv-k1"},
                )
                self.assertEqual(r_conflict.status_code, 409)

        # Backdate expires_at to force TTL expiry reconciliation
        with self.svc.gpu_scheduler._locked() as st:
            st["reservations"][0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        reconciled = self.svc.gpu_scheduler.reconcile(telemetry=fake_gpus)
        self.assertEqual(reconciled[0]["state"], "EXPIRED")

        # New reservation transitions RESERVED -> RUNNING when NVML sees owner PID
        with patch.object(self.svc.gpu_scheduler, "telemetry_fn", return_value=fake_gpus):
            r2 = self.svc.gpu_scheduler.reserve(
                gpu_uuid="GPU-M2-A30-01",
                owner="m2_alice",
                actor="m2_alice",
                job_name="train-live",
                ttl_minutes=60,
            )
        self.assertEqual(r2["state"], "RESERVED")
        live_telemetry = [{**fake_gpus[0], "active_processes": [{"pid": 43210, "username": "m2_alice", "vram_mb": 8192}]}]
        reconciled2 = self.svc.gpu_scheduler.reconcile(telemetry=live_telemetry)
        live_r2 = next(x for x in reconciled2 if x["id"] == r2["id"])
        self.assertEqual(live_r2["state"], "RUNNING")
        self.assertTrue(live_r2["runtime_verified"])
        self.assertEqual(live_r2["pid"], 43210)

    def test_safe_member_deletion_lifecycle_and_workspace_persistence(self) -> None:
        uname = f"m2del_{uuid.uuid4().hex[:6]}"
        member = self.svc.provision_member({
            "full_name": "Safe Delete Member",
            "username": uname,
            "email": f"{uname}@example.org",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "workspace_name": f"ws-{uname}",
            "storage_quota_gb": 10,
            "mount_path": "/workspace",
            "cpu_limit_cores": 4,
            "ram_limit_gb": 16,
            "gpu_permissions": ["workstation-gpu-01"],
            "vram_quota_gb": 12,
        })
        member_id = member["id"]
        ws_dir = os.path.join(self.users_dir, uname)
        os.makedirs(ws_dir, exist_ok=True)
        with open(os.path.join(ws_dir, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("important-research-artifact")
        member["workspace_dir"] = ws_dir

        self.svc.workspace_controller.request_workspace(
            owner=uname,
            host_id="workstation",
            ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIM6x7y8z9a0b1c2d3e4f5g6h7i8j9k0l m2del@test",
        )
        admin_tok = issue_token("admin", "ADMIN")

        with patch.dict(os.environ, {"LABOPS_ADMIN_USERNAME": "admin", "LABOPS_ADMIN_PASSWORD": "DelStepPass!2026"}, clear=False):
            # 1. Delete without step-up -> 403 STEP_UP_REQUIRED
            r_no_step = self.client.delete(
                f"/api/v1/v2/members/{member_id}?confirm_username={uname}",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            self.assertEqual(r_no_step.status_code, 403)

            # 2. Helper to obtain step-up token
            def _step():
                rs = self.client.post(
                    "/api/v1/auth/step-up",
                    json={"password": "DelStepPass!2026", "action": "DELETE_MEMBER", "target_id": member_id},
                    headers={"Authorization": f"Bearer {admin_tok}"},
                )
                self.assertEqual(rs.status_code, 200)
                return rs.json()["step_up_token"]

            # Delete while still ACTIVE -> 409 LIFECYCLE_PRECONDITION_FAILED
            r_active_del = self.client.delete(
                f"/api/v1/v2/members/{member_id}?confirm_username={uname}",
                headers={"Authorization": f"Bearer {admin_tok}", "X-LabOps-Step-Up-Token": _step()},
            )
            self.assertEqual(r_active_del.status_code, 409)

            # 3. Suspend member
            r_susp = self.client.post(
                f"/api/v1/v2/members/{member_id}/suspend",
                json={"reason": "Lifecycle verification"},
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            self.assertEqual(r_susp.status_code, 200)
            self.assertEqual(r_susp.json()["status"], "SUSPENDED")

            # 4. Seed active GPU reservation -> archive MUST be blocked 409 ACTIVE_WORKLOADS_PRESENT
            rsv = self.svc.gpu_scheduler.reserve(
                gpu_uuid="GPU-LC-LOCK",
                owner=uname,
                actor=uname,
                job_name="running-exp",
                telemetry_fn=None,
            ) if False else None
            # Mock telemetry for reserve so GPU appears observed
            fake_t = [{"id": "workstation-gpu-01", "server_id": "workstation", "uuid": "GPU-LC-LOCK", "vram_total_mb": 24576, "active_processes": []}]
            with patch.object(self.svc.gpu_scheduler, "telemetry_fn", return_value=fake_t):
                rsv = self.svc.gpu_scheduler.reserve(
                    gpu_uuid="GPU-LC-LOCK",
                    owner=uname,
                    actor=uname,
                    job_name="running-exp",
                )
            r_arch_blocked = self.client.post(
                f"/api/v1/v2/members/{member_id}/archive",
                json={"retain_backup": True},
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            self.assertEqual(r_arch_blocked.status_code, 409)
            self.assertIn("ACTIVE_WORKLOADS_PRESENT", r_arch_blocked.text)

            # Release reservation -> archive succeeds -> DELETE_ELIGIBLE
            self.svc.gpu_scheduler.request_stop(rsv["id"], actor="admin", is_admin=True)
            r_arch_ok = self.client.post(
                f"/api/v1/v2/members/{member_id}/archive",
                json={"retain_backup": True},
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            self.assertEqual(r_arch_ok.status_code, 200)
            self.assertEqual(r_arch_ok.json()["status"], "DELETE_ELIGIBLE")
            self.assertGreaterEqual(r_arch_ok.json()["archive_manifest"]["file_count"], 1)

            # 5. Dry-run preview shows eligible
            r_prev = self.client.get(
                f"/api/v1/v2/members/{member_id}/delete-preview",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            self.assertEqual(r_prev.status_code, 200)
            self.assertTrue(r_prev.json()["eligible_for_deletion"])

            # 6. Wrong confirm_username fails 409/400
            r_wrong_name = self.client.delete(
                f"/api/v1/v2/members/{member_id}?confirm_username=wrong_user&remove_files=true",
                headers={"Authorization": f"Bearer {admin_tok}", "X-LabOps-Step-Up-Token": _step()},
            )
            self.assertIn(r_wrong_name.status_code, (400, 409))

            # 7. Valid step-up + confirm_username completes deletion & persists workspace removal
            r_del = self.client.delete(
                f"/api/v1/v2/members/{member_id}?confirm_username={uname}&remove_files=true",
                headers={"Authorization": f"Bearer {admin_tok}", "X-LabOps-Step-Up-Token": _step()},
            )
            self.assertEqual(r_del.status_code, 200)
            res_json = r_del.json()
            self.assertTrue(res_json["success"])
            self.assertEqual(res_json["status"], "DELETED")
            self.assertFalse(os.path.exists(ws_dir))

        # Verify persistent DeletionOperationRecord in DB & WorkspaceController reload empty
        op_id = res_json["operation_id"]
        with self.svc.db.session_scope() as s:
            rec = s.get(DeletionOperationRecord, op_id)
            self.assertIsNotNone(rec)
            self.assertEqual(rec.status, "COMPLETED")
        reloaded_wc = WorkspaceController(self.tmp.name)
        self.assertEqual([w for w in reloaded_wc.list_workspaces(owner=uname) if w.get("owner") == uname], [])


class TestMilestone2ProcessesAndRepoGuard(unittest.TestCase, _PatchedServiceMixin):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="m2-proc-repo-")
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        self._patch_endpoints_service(self.svc)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._restore_endpoints_service()
        self.tmp.cleanup()

    def test_researcher_process_visibility_rbac(self) -> None:
        admin_tok = issue_token("admin", "ADMIN")
        res_tok = issue_token("alice_m2", "RESEARCHER")

        fake_procs = [
            {"pid": 1, "username": "root", "classification": "SYSTEM_PROCESS", "name": "systemd"},
            {"pid": 200, "username": "bob_other", "classification": "RESEARCH_JOB", "name": "python"},
            {"pid": 201, "username": "alice_m2", "classification": "RESEARCH_JOB", "name": "train.py"},
        ]

        def _fake_list_procs(*, server_id=None, classification=None, username=None, **_kwargs):
            out = list(fake_procs)
            if username:
                out = [x for x in out if x["username"] == username]
            if classification == "SYSTEM_PROCESS":
                out = [x for x in out if x["classification"] == "SYSTEM_PROCESS"]
            return out

        with patch.object(self.svc, "list_processes", side_effect=_fake_list_procs):
            r_res = self.client.get(
                "/api/v1/processes?classification=all",
                headers={"Authorization": f"Bearer {res_tok}"},
            )
            self.assertEqual(r_res.status_code, 200)
            items = r_res.json()["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["username"], "alice_m2")
            self.assertTrue(all(x["classification"] != "SYSTEM_PROCESS" for x in items))

            r_adm = self.client.get(
                "/api/v1/processes?classification=all",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            self.assertEqual(r_adm.status_code, 200)
            self.assertTrue(any(x["classification"] == "SYSTEM_PROCESS" for x in r_adm.json()["items"]))

    def test_delegation_token_format_and_repoguard_broker_security(self) -> None:
        token = build_delegation_context(
            secret="delegation-hmac-secret-test-32bytes",
            command_id="cmd-1",
            actor="alice",
            role="RESEARCHER",
            node_id="workstation",
            action="STOP_DOCKER_WORKSPACE",
            parameters={"b": "2", "a": "1"},
            ttl_seconds=900,  # must clamp to 300s
        )
        p64, s64 = token.split(".", 1)
        self.assertTrue(p64 and s64)
        self.assertEqual(
            compute_params_hash({"a": 1, "b": "2"}),
            compute_params_hash({"b": "2", "a": 1}),
        )
        import base64
        claims = json.loads(base64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4)))
        self.assertEqual(claims["aud"], DELEGATION_AUDIENCE)
        self.assertEqual(claims["exp"] - claims["iat"], 300)

        alice_tok = issue_token("alice", "RESEARCHER")

        # Missing LABOPS_AGENT_SECRET must fail closed 503
        with patch.dict(os.environ, {"LABOPS_AGENT_SECRET": ""}, clear=False):
            r_503 = self.client.post(
                "/api/v1/repositories/requests",
                json={"repo_url": "https://github.com/octocat/Hello-World.git"},
                headers={"Authorization": f"Bearer {alice_tok}"},
            )
            self.assertEqual(r_503.status_code, 503)

        # Forbidden target paths & traversal blocked before calling agent
        with patch.dict(
            os.environ,
            {"LABOPS_AGENT_SECRET": "agent-secret-test", "LABOPS_BROKER_VERIFY_SECRET": "broker-hmac-secret-test"},
            clear=False,
        ):
            r_etc = self.client.post(
                "/api/v1/repositories/deliver",
                json={"request_id": "req-1", "target_directory": "/etc/passwd"},
                headers={"Authorization": f"Bearer {alice_tok}"},
            )
            self.assertEqual(r_etc.status_code, 403)

            r_trav = self.client.post(
                "/api/v1/repositories/deliver",
                json={"request_id": "req-1", "target_directory": "/workspaces/alice/../bob"},
                headers={"Authorization": f"Bearer {alice_tok}"},
            )
            self.assertEqual(r_trav.status_code, 400)

            captured_headers = {}

            class _MockResp:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(self):
                    return json.dumps({"request_id": "req-123", "status": "QUEUED"}).encode("utf-8")

            def _fake_urlopen(req, timeout=10.0):
                captured_headers.update(dict(req.header_items()))
                return _MockResp()

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                r_ok = self.client.post(
                    "/api/v1/repositories/requests",
                    json={"repo_url": "https://github.com/octocat/Hello-World.git"},
                    headers={"Authorization": f"Bearer {alice_tok}"},
                )
            self.assertEqual(r_ok.status_code, 202)
            norm = {k.lower(): v for k, v in captured_headers.items()}
            self.assertEqual(norm.get("x-labops-verified-actor"), "alice")
            self.assertEqual(norm.get("x-labops-verified-role"), "RESEARCHER")
            self.assertTrue(len(norm.get("x-labops-verified-signature", "")) == 64)


class TestMilestone2ZaloAnd9RouterHardening(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="m2-zalo-")
        self.svc = ZaloAssistantService(data_dir=self.tmp.name)
        self.orig_zalo_svc = ai_ep.zalo_assistant_service
        ai_ep.zalo_assistant_service = self.svc
        self.client = TestClient(app)

    def tearDown(self) -> None:
        ai_ep.zalo_assistant_service = self.orig_zalo_svc
        self.tmp.cleanup()

    def test_wildcard_banned_and_config_endpoints_admin_only(self) -> None:
        ledger = QuotaLedger(self.tmp.name)
        ai = AIGatewayClient()
        rpt = ReportGenerator(self.tmp.name)

        with self.assertRaises(ValueError):
            ZaloBotEngine(ledger, ai, rpt, authorized_users=["*"])
        with self.assertRaises(ValueError):
            ZaloBotEngine(ledger, ai, rpt, authorized_users=["u1"], allowed_chat_ids=["*"])

        admin_tok = issue_token("admin", "ADMIN")
        res_tok = issue_token("res_u", "RESEARCHER")

        self.assertEqual(self.client.get("/api/v1/ai-assistant/config", headers={"Authorization": f"Bearer {res_tok}"}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/ai-assistant/tools", headers={"Authorization": f"Bearer {res_tok}"}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/ai-assistant/history", headers={"Authorization": f"Bearer {res_tok}"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/ai-assistant/reports/generate", headers={"Authorization": f"Bearer {res_tok}"}).status_code, 403)

        r_bad_cfg = self.client.post(
            "/api/v1/ai-assistant/config",
            json={"authorized_users": ["*"]},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        self.assertEqual(r_bad_cfg.status_code, 400)

    def test_worker_silent_drop_and_two_phase_outbox_refund(self) -> None:
        self.svc.bot_engine.authorized_users = ["zalo_ok"]
        self.svc.bot_engine.allowed_chat_ids = ["chat_ok"]
        worker = ZaloPollingWorker(service_instance=self.svc)

        class _FakeBot:
            def __init__(self, fail: bool = False):
                self.fail = fail
                self.sent = []

            async def send_message(self, *, chat_id: str, text: str):
                self.sent.append((chat_id, text))
                if self.fail:
                    raise RuntimeError("Zalo transport down")
                return type("Msg", (), {"message_id": "prov-123"})()

        # 1. Unauthorized sender -> silently dropped, zero outbound calls, zero quota used
        bot_ok = _FakeBot(fail=False)
        drop_res = asyncio.run(
            worker.process_incoming_message(
                bot_ok,
                raw_text="/status",
                chat_id="chat_ok",
                sender_id="intruder_user",
                message_id="m1",
            )
        )
        self.assertTrue(drop_res["dropped"])
        self.assertEqual(len(bot_ok.sent), 0)
        self.assertEqual(self.svc.quota_ledger.get_status()["total_sent"], 0)

        # 2. Authorized sender when Zalo delivery fails -> outbox refunded back to 0
        bot_fail = _FakeBot(fail=True)
        fail_res = asyncio.run(
            worker.process_incoming_message(
                bot_fail,
                raw_text="/help",
                chat_id="chat_ok",
                sender_id="zalo_ok",
                message_id="m2",
            )
        )
        self.assertTrue(fail_res.get("refunded"))
        self.assertEqual(len(bot_fail.sent), 1)
        self.assertEqual(self.svc.quota_ledger.get_status()["total_sent"], 0)
        recent = self.svc.quota_ledger.get_recent_outbox(5)
        self.assertEqual(recent[0]["status"], "FAILED_REFUNDED")

        # 3. Successful delivery confirms SENT & preserves quota count
        succ_res = asyncio.run(
            worker.process_incoming_message(
                bot_ok,
                raw_text="/help",
                chat_id="chat_ok",
                sender_id="zalo_ok",
                message_id="m3",
            )
        )
        self.assertTrue(succ_res["success"])
        self.assertEqual(self.svc.quota_ledger.get_status()["total_sent"], 1)
        self.assertEqual(self.svc.quota_ledger.get_recent_outbox(5)[0]["status"], "SENT")

    def test_ai_gateway_strips_think_no_reasoning_leak_and_never_reads_9router_sqlite(self) -> None:
        # Place a fake ~/.9router/db/data.sqlite inside temp HOME to prove AIGatewayClient never reads it
        fake_home = os.path.join(self.tmp.name, "fake_home")
        fake_db_dir = os.path.join(fake_home, ".9router", "db")
        os.makedirs(fake_db_dir, exist_ok=True)
        fake_db = os.path.join(fake_db_dir, "data.sqlite")
        conn = sqlite3.connect(fake_db)
        conn.execute("CREATE TABLE apiKeys (key TEXT)")
        conn.execute("INSERT INTO apiKeys VALUES ('secret-from-9router-sqlite')")
        conn.commit()
        conn.close()

        with patch.dict(os.environ, {"HOME": fake_home, "ROUTER_API_KEY": ""}, clear=False):
            client = AIGatewayClient(base_url="http://127.0.0.1:20128/v1", model="model_free")
            self.assertEqual(client._resolve_api_key(), "")

        # Strip <think>...</think> from content
        payload_think = {
            "choices": [{
                "message": {
                    "content": "<think>internal hidden reasoning</think>\nHệ thống aiot-ws1 đang hoạt động ổn định."
                }
            }]
        }

        class _MockAiResp:
            def __init__(self, body: dict):
                self._body = json.dumps(body).encode("utf-8")
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return self._body

        with patch("urllib.request.urlopen", return_value=_MockAiResp(payload_think)):
            out = client.generate_chat_response([{"role": "user", "content": "status"}])
        self.assertEqual(out, "Hệ thống aiot-ws1 đang hoạt động ổn định.")
        self.assertNotIn("internal hidden reasoning", out)

        # Never leak reasoning_content when content is None
        payload_reasoning = {
            "choices": [{
                "message": {
                    "content": None,
                    "reasoning_content": "TOP SECRET CHAIN OF THOUGHT",
                }
            }]
        }
        with patch("urllib.request.urlopen", return_value=_MockAiResp(payload_reasoning)):
            out2 = client.generate_chat_response([{"role": "user", "content": "status"}])
        self.assertNotIn("TOP SECRET CHAIN OF THOUGHT", out2)


if __name__ == "__main__":
    unittest.main()
