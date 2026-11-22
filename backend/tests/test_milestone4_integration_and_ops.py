"""Milestone 4 Cross-Stack Integration, Migration, Backup/Restore, Security & Benchmark Suite."""
from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault(
    "LABOPS_SESSION_SECRET",
    "m4-test-session-secret-0123456789abcdef0123456789abcdef",
)

import app.api.v1.endpoints.audit as audit_ep  # noqa: E402
import app.api.v1.endpoints.auth as auth_ep  # noqa: E402
import app.api.v1.endpoints.dashboard as dash_ep  # noqa: E402
import app.api.v1.endpoints.gpus as gpus_ep  # noqa: E402
import app.api.v1.endpoints.processes as proc_ep  # noqa: E402
import app.api.v1.endpoints.repositories as repo_ep  # noqa: E402
import app.api.v1.endpoints.users as users_ep  # noqa: E402
import app.api.v1.endpoints.v2_platform as v2_ep  # noqa: E402
from app.core.security import issue_token  # noqa: E402
from app.db.models import (  # noqa: E402
    AuditEventRecord,
    DeletionOperationRecord,
    HostNodeRecord,
    MemberRecord,
    WorkspaceRecord,
    ZaloOutboxRecord,
)
from app.main import app  # noqa: E402
from app.services.labops_service import LabOpsDataService  # noqa: E402


class _ServiceIsolationMixin:
    def _mount_service(self, svc: LabOpsDataService) -> None:
        modules = [auth_ep, gpus_ep, proc_ep, repo_ep, users_ep, v2_ep, dash_ep, audit_ep]
        self._orig = []
        for mod in modules:
            if hasattr(mod, "service"):
                self._orig.append((mod, mod.service))
                mod.service = svc

    def _restore_service(self) -> None:
        for mod, orig in getattr(self, "_orig", []):
            mod.service = orig


class TestMilestone4MigrationsAndBackup(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="m4-mig-")
        self.alembic_bin = str(BACKEND_DIR / ".venv" / "bin" / "alembic")
        self.python_bin = str(BACKEND_DIR / ".venv" / "bin" / "python")
        self.backup_script = str(REPO_ROOT / "scripts" / "labops_db_backup_restore.py")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_alembic_cli_roundtrip_and_checksum_verified_backup_restore(self) -> None:
        src_db = os.path.join(self.tmp.name, "src_mig.sqlite3")
        src_url = f"sqlite:///{src_db}"
        env = dict(os.environ)
        env["DATABASE_URL"] = src_url

        expected_tables = {
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

        # 1. upgrade -> downgrade base -> upgrade head via Alembic CLI
        subprocess.run([self.alembic_bin, "upgrade", "head"], cwd=str(BACKEND_DIR), env=env, check=True)
        e1 = create_engine(src_url)
        t_up = set(inspect(e1).get_table_names())
        e1.dispose()
        self.assertTrue(expected_tables.issubset(t_up))

        subprocess.run([self.alembic_bin, "downgrade", "base"], cwd=str(BACKEND_DIR), env=env, check=True)
        e2 = create_engine(src_url)
        t_down = set(inspect(e2).get_table_names())
        e2.dispose()
        self.assertFalse(expected_tables.intersection(t_down))

        subprocess.run([self.alembic_bin, "upgrade", "head"], cwd=str(BACKEND_DIR), env=env, check=True)

        # 2. Seed realistic rows into the migrated DB
        from sqlalchemy.orm import Session

        eng = create_engine(src_url)
        with Session(eng) as s:
            s.add(
                HostNodeRecord(
                    id="workstation",
                    hostname="aiot-ws1",
                    display_name="Server 02 — Workstation",
                    role="PRIMARY_CONTROL_PLANE",
                    management_ip="192.168.167.251",
                    status="ONLINE",
                    freshness_status="FRESH",
                    metadata_json={"gpus": 1},
                )
            )
            s.add(
                MemberRecord(
                    id="mem-m4-01",
                    username="minhtq",
                    email="minhtq@example.org",
                    full_name="Minh Tran",
                    role="RESEARCHER",
                    status="ACTIVE",
                    provisioning_state="READY",
                    assigned_server="workstation",
                    workspace_name="ws-minhtq",
                    ssh_port=22011,
                    gpu_permissions_json=["workstation-gpu-01"],
                    payload_json={"id": "mem-m4-01", "username": "minhtq"},
                    created_at="2026-11-18T00:00:00+00:00",
                )
            )
            s.add(
                WorkspaceRecord(
                    id="ws-minhtq",
                    name="ws-minhtq",
                    owner="minhtq",
                    host_id="workstation",
                    runtime_type="DOCKER",
                    image_ref="labops/pytorch:2.4-cuda12",
                    ssh_host_port=22011,
                    lifecycle_state="RUNNING",
                    observed_state="RUNNING",
                    non_privileged=True,
                    docker_socket_mounted=False,
                    limits_json={"cpu": 8, "ram_gb": 16},
                    created_at="2026-11-18T00:00:00+00:00",
                )
            )
            s.add(
                AuditEventRecord(
                    id="aud-m4-1",
                    timestamp="2026-11-18T00:01:00+00:00",
                    actor="admin",
                    action="MEMBER_PROVISIONED",
                    target_type="MEMBER",
                    target_id="minhtq",
                    result="SUCCESS",
                    correlation_id="corr-m4-1",
                    details="Provisioned test researcher",
                )
            )
            s.add(
                ZaloOutboxRecord(
                    id="zalo-m4-1",
                    month_key="2026-11",
                    recipient_id="uid-1",
                    is_critical=True,
                    status="SENT",
                    message_snippet="Workstation alert summary",
                )
            )
            s.commit()
        eng.dispose()

        # 3. Backup -> verify manifest & SHA-256 digests
        bundle_dir = Path(self.tmp.name) / "bundle_ok"
        res_bak = subprocess.run(
            [self.python_bin, self.backup_script, "backup", "--db-url", src_url, "--out-dir", str(bundle_dir)],
            capture_output=True,
            text=True,
            check=True,
        )
        manifest = json.loads(res_bak.stdout)
        self.assertEqual(manifest["alembic_version"], "0003_labops_ai_sre_agent_v2")
        self.assertEqual(manifest["table_counts"]["members"], 1)
        self.assertIn("tables/members.jsonl", manifest["artifacts"])
        self.assertIn("sqlite_snapshot.db", manifest["artifacts"])

        # 4. Tampered backup must be rejected BEFORE writing target DB
        tampered_dir = Path(self.tmp.name) / "bundle_tampered"
        shutil.copytree(bundle_dir, tampered_dir)
        members_jsonl = tampered_dir / "tables" / "members.jsonl"
        members_jsonl.write_text(members_jsonl.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        bad_target_url = f"sqlite:///{os.path.join(self.tmp.name, 'tampered_target.sqlite3')}"
        res_tamper = subprocess.run(
            [self.python_bin, self.backup_script, "restore", "--bundle-dir", str(tampered_dir), "--target-db-url", bad_target_url],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_tamper.returncode, 0)
        self.assertIn("Checksum mismatch", res_tamper.stderr)

        # 5. Clean restore into fresh target DB succeeds and verifies row parity
        clean_target = os.path.join(self.tmp.name, "restored.sqlite3")
        clean_target_url = f"sqlite:///{clean_target}"
        res_restore = subprocess.run(
            [self.python_bin, self.backup_script, "restore", "--bundle-dir", str(bundle_dir), "--target-db-url", clean_target_url],
            capture_output=True,
            text=True,
            check=True,
        )
        restore_summary = json.loads(res_restore.stdout)
        self.assertEqual(restore_summary["status"], "RESTORED_VERIFIED")
        self.assertEqual(restore_summary["restored_counts"]["members"], 1)
        self.assertEqual(restore_summary["restored_counts"]["workspaces"], 1)
        self.assertEqual(restore_summary["restored_counts"]["hosts"], 1)

        # 6. Refusing overwrite without --allow-overwrite
        res_refuse = subprocess.run(
            [self.python_bin, self.backup_script, "restore", "--bundle-dir", str(bundle_dir), "--target-db-url", clean_target_url],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_refuse.returncode, 0)
        self.assertIn("Refusing to overwrite", res_refuse.stderr)

        # Verify restored data integrity
        eng_dst = create_engine(clean_target_url)
        with eng_dst.connect() as conn:
            row = conn.execute(text("SELECT username, workspace_name, ssh_port FROM members WHERE id='mem-m4-01'")).fetchone()
            self.assertEqual(tuple(row), ("minhtq", "ws-minhtq", 22011))
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            self.assertEqual(ver, "0003_labops_ai_sre_agent_v2")
        eng_dst.dispose()


class TestMilestone4EndToEndAndSecurity(unittest.TestCase, _ServiceIsolationMixin):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="m4-e2e-")
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        self._mount_service(self.svc)
        auth_ep._FAILED_ATTEMPTS.clear()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        auth_ep._FAILED_ATTEMPTS.clear()
        self._restore_service()
        self.tmp.cleanup()

    def test_admin_3_stage_safe_member_deletion_lifecycle_and_step_up_binding(self) -> None:
        uname = f"m4del_{uuid.uuid4().hex[:6]}"
        member = self.svc.provision_member(
            {
                "full_name": "Safe Delete Target",
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
            }
        )
        mid = member["id"]

        with patch.dict(
            os.environ,
            {"LABOPS_ADMIN_USERNAME": "admin", "LABOPS_ADMIN_PASSWORD": "AdminE2E!Pass2026"},
            clear=False,
        ):
            r_login = self.client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "AdminE2E!Pass2026"},
            )
            self.assertEqual(r_login.status_code, 200)
            admin_hdr = {"Authorization": f"Bearer {r_login.json()['access_token']}"}

            # Issue a step-up token for delete attempts
            r_step_wrong_target = self.client.post(
                "/api/v1/auth/step-up",
                json={"password": "AdminE2E!Pass2026", "action": "DELETE_MEMBER", "target_id": "mem-other"},
                headers=admin_hdr,
            )
            self.assertEqual(r_step_wrong_target.status_code, 200)
            wrong_step_tok = r_step_wrong_target.json()["step_up_token"]

            r_step_pre = self.client.post(
                "/api/v1/auth/step-up",
                json={"password": "AdminE2E!Pass2026", "action": "DELETE_MEMBER", "target_id": mid},
                headers=admin_hdr,
            )
            step_tok_1 = r_step_pre.json()["step_up_token"]

            # Wrong step-up target rejected 403
            r_bad_target = self.client.delete(
                f"/api/v1/v2/members/{mid}?confirm_username={uname}&remove_files=false",
                headers={**admin_hdr, "X-LabOps-Step-Up-Token": wrong_step_tok},
            )
            self.assertEqual(r_bad_target.status_code, 403)

            # DELETE before SUSPEND rejected 409
            r_before_suspend = self.client.delete(
                f"/api/v1/v2/members/{mid}?confirm_username={uname}&remove_files=false",
                headers={**admin_hdr, "X-LabOps-Step-Up-Token": step_tok_1},
            )
            self.assertEqual(r_before_suspend.status_code, 409)

            # Stage 1: SUSPEND
            r_susp = self.client.post(f"/api/v1/v2/members/{mid}/suspend", json={"reason": "M4 test"}, headers=admin_hdr)
            self.assertEqual(r_susp.status_code, 200)
            self.assertEqual(r_susp.json()["status"], "SUSPENDED")

            # DELETE before ARCHIVE rejected 409
            r_step_pre2 = self.client.post(
                "/api/v1/auth/step-up",
                json={"password": "AdminE2E!Pass2026", "action": "DELETE_MEMBER", "target_id": mid},
                headers=admin_hdr,
            )
            r_before_archive = self.client.delete(
                f"/api/v1/v2/members/{mid}?confirm_username={uname}&remove_files=false",
                headers={**admin_hdr, "X-LabOps-Step-Up-Token": r_step_pre2.json()["step_up_token"]},
            )
            self.assertEqual(r_before_archive.status_code, 409)

            # Stage 2: Preview + ARCHIVE
            r_prev_1 = self.client.get(f"/api/v1/v2/members/{mid}/delete-preview", headers=admin_hdr)
            self.assertEqual(r_prev_1.status_code, 200)
            self.assertFalse(r_prev_1.json()["eligible_for_deletion"])

            r_arch = self.client.post(f"/api/v1/v2/members/{mid}/archive", json={"retain_backup": True}, headers=admin_hdr)
            self.assertEqual(r_arch.status_code, 200)
            self.assertIn(r_arch.json()["status"], ("ARCHIVED", "DELETE_ELIGIBLE"))

            r_prev_2 = self.client.get(f"/api/v1/v2/members/{mid}/delete-preview", headers=admin_hdr)
            self.assertTrue(r_prev_2.json()["eligible_for_deletion"])

            # DELETE without X-LabOps-Step-Up-Token rejected 403
            r_no_step = self.client.delete(
                f"/api/v1/v2/members/{mid}?confirm_username={uname}&remove_files=false",
                headers=admin_hdr,
            )
            self.assertIn(r_no_step.status_code, (401, 403))

            # Stage 3: Valid Step-Up + confirm_username -> permanent delete 200
            r_step_ok = self.client.post(
                "/api/v1/auth/step-up",
                json={"password": "AdminE2E!Pass2026", "action": "DELETE_MEMBER", "target_id": mid},
                headers=admin_hdr,
            )
            r_del = self.client.delete(
                f"/api/v1/v2/members/{mid}?confirm_username={uname}&remove_files=false",
                headers={**admin_hdr, "X-LabOps-Step-Up-Token": r_step_ok.json()["step_up_token"]},
            )
            self.assertEqual(r_del.status_code, 200)
            self.assertIsNone(next((m for m in self.svc.members if m.get("id") == mid), None))

            # DeletionOperationRecord recorded in DB
            with self.svc.db.session_scope() as s:
                ops = s.query(DeletionOperationRecord).filter(DeletionOperationRecord.member_id == mid).all()
                self.assertTrue(any(o.status == "COMPLETED" for o in ops))

    def test_researcher_password_change_isolation_and_logout_jti_revocation(self) -> None:
        uname = f"m4res_{uuid.uuid4().hex[:6]}"
        member = self.svc.provision_member(
            {
                "full_name": "Researcher Isolation User",
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
            }
        )
        temp_pw, _ = self.svc.ssh_access_manager.issue_temporary_password(
            member_id=member["id"], username=uname, actor="admin", ttl_seconds=900
        )

        # 1. Login with temporary password -> must_change_password=True
        r_login1 = self.client.post("/api/v1/auth/login", json={"username": uname, "password": temp_pw})
        self.assertEqual(r_login1.status_code, 200)
        d1 = r_login1.json()
        self.assertTrue(d1["must_change_password"])
        self.assertEqual(d1["role"], "RESEARCHER")
        tok1 = d1["access_token"]

        # 2. Change password via researcher endpoint
        new_pw = "NewLabOpsResearcher!2026#"
        r_chg = self.client.post(
            "/api/v1/v2/ssh-access/first-login-change-password",
            json={"temporary_password": temp_pw, "new_password": new_pw},
            headers={"Authorization": f"Bearer {tok1}"},
        )
        self.assertEqual(r_chg.status_code, 200)

        # 3. Old temp password fails login; new password succeeds with must_change_password=False
        r_old_login = self.client.post("/api/v1/auth/login", json={"username": uname, "password": temp_pw})
        self.assertEqual(r_old_login.status_code, 401)

        r_login2 = self.client.post("/api/v1/auth/login", json={"username": uname, "password": new_pw})
        self.assertEqual(r_login2.status_code, 200)
        d2 = r_login2.json()
        self.assertFalse(d2["must_change_password"])
        tok2 = d2["access_token"]
        res_hdr = {"Authorization": f"Bearer {tok2}"}

        # 4. Researcher self-service works
        r_me = self.client.get("/api/v1/v2/researcher/me", headers=res_hdr)
        self.assertEqual(r_me.status_code, 200)
        self.assertEqual(r_me.json()["username"], uname)

        # 5. Privilege escalation attempts all fail closed 403
        self.assertEqual(self.client.get("/api/v1/audit-logs", headers=res_hdr).status_code, 403)
        self.assertEqual(
            self.client.post(f"/api/v1/v2/members/{member['id']}/suspend", json={"reason": "x"}, headers=res_hdr).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/step-up",
                json={"password": "x", "action": "DELETE_MEMBER", "target_id": member["id"]},
                headers=res_hdr,
            ).status_code,
            403,
        )
        self.assertEqual(self.client.get("/api/v1/repositories/audit", headers=res_hdr).status_code, 403)

        # 6. Logout revokes JTI immediately
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers=res_hdr).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/v2/researcher/me", headers=res_hdr).status_code, 401)

    def test_repository_guard_ssrf_and_traversal_negative_matrix(self) -> None:
        tok = issue_token("alice", "RESEARCHER", member_id="mem-alice")
        hdr = {"Authorization": f"Bearer {tok}"}

        with patch.dict(
            os.environ,
            {"LABOPS_AGENT_SECRET": "m4-agent-secret", "LABOPS_BROKER_VERIFY_SECRET": "m4-broker-secret"},
            clear=False,
        ):
            for bad_url in (
                "http://127.0.0.1/org/repo.git",
                "http://169.254.169.254/latest/meta-data",
                "http://192.168.167.250/internal/repo.git",
                "file:///etc/passwd",
                "https://user:pass@github.com/org/repo.git",
                "https://evil.example.com/org/repo.git",
            ):
                r = self.client.post("/api/v1/repositories/requests", json={"repo_url": bad_url}, headers=hdr)
                self.assertEqual(r.status_code, 400, f"Expected 400 for SSRF URL {bad_url}, got {r.status_code}")

            for bad_dir, expected_code in (
                ("/etc/passwd", 403),
                ("/root/.ssh", 403),
                ("/workspaces/bob/repo", 403),
                ("/workspaces/alice/../../etc/cron.d", 400),
            ):
                r = self.client.post(
                    "/api/v1/repositories/deliver",
                    json={"request_id": "req-1", "target_directory": bad_dir},
                    headers=hdr,
                )
                self.assertEqual(r.status_code, expected_code, f"Unexpected status for {bad_dir}: {r.status_code}")


class TestMilestone4PerformanceBenchmark(unittest.TestCase, _ServiceIsolationMixin):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="m4-bench-")
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        self._mount_service(self.svc)
        self.svc.provision_member(
            {
                "full_name": "Benchmark User",
                "username": "benchuser",
                "email": "benchuser@example.org",
                "role": "RESEARCHER",
                "assigned_server": "workstation",
                "workspace_name": "ws-benchuser",
                "storage_quota_gb": 10,
                "mount_path": "/workspace",
                "cpu_limit_cores": 2,
                "ram_limit_gb": 8,
                "gpu_permissions": [],
                "vram_quota_gb": 0,
            }
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._restore_service()
        self.tmp.cleanup()

    def test_api_latency_benchmark_50_requests_per_endpoint(self) -> None:
        admin_hdr = {"Authorization": f"Bearer {issue_token('admin', 'ADMIN')}"}
        res_hdr = {"Authorization": f"Bearer {issue_token('benchuser', 'RESEARCHER')}"}

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b"[]"

        summary = {}
        with patch.dict(os.environ, {"LABOPS_AGENT_SECRET": "bench-secret"}, clear=False):
            with patch("urllib.request.urlopen", return_value=_FakeResp()):
                targets = [
                    ("GET /healthz", "/healthz", {}),
                    ("GET /api/v1/dashboard/summary", "/api/v1/dashboard/summary", admin_hdr),
                    ("GET /api/v1/v2/researcher/me", "/api/v1/v2/researcher/me", res_hdr),
                    ("GET /api/v1/repositories/requests", "/api/v1/repositories/requests", res_hdr),
                ]
                for label, path, hdr in targets:
                    samples = []
                    # warm-up
                    self.client.get(path, headers=hdr)
                    for _ in range(50):
                        t0 = time.perf_counter()
                        resp = self.client.get(path, headers=hdr)
                        dt_ms = (time.perf_counter() - t0) * 1000.0
                        self.assertEqual(resp.status_code, 200)
                        samples.append(dt_ms)
                    samples.sort()
                    p50 = statistics.median(samples)
                    p95 = samples[int(0.95 * len(samples)) - 1]
                    p99 = samples[int(0.99 * len(samples)) - 1]
                    summary[label] = {
                        "count": len(samples),
                        "min_ms": round(samples[0], 3),
                        "p50_ms": round(p50, 3),
                        "p95_ms": round(p95, 3),
                        "p99_ms": round(p99, 3),
                        "max_ms": round(samples[-1], 3),
                    }
                    self.assertLess(p95, 200.0, f"{label} p95 {p95:.2f}ms exceeded 200ms threshold")

        Path("/tmp/labops_m4_benchmark.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
