"""Comprehensive P0-1 through P0-5 security hardening verification suite (G1 + G4)."""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from unittest.mock import patch

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault("LABOPS_SESSION_SECRET", "test-secret-p0-security-hardening-32chars!")
os.environ.setdefault("LABOPS_WG_SERVER_PUBLIC_KEY", "TA7sUCFRsPHfg52B/Gh4w7vnu3SpzaxH4A1HS6N3bDk=")

from app.core.config import settings
from app.services.labops_service import LabOpsDataService
from app.services.ssh_authorization_gate import evaluate_ssh_authorization_gate
from app.services.storage_inventory_service import StorageInventoryService
from app.services.workspace_controller import WorkspaceController, WorkspaceSecurityError


def _generate_ed25519_pubkey(tmpdir: str, label: str = "client-key") -> str:
    priv = os.path.join(tmpdir, f"id_{label}")
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", label, "-f", priv, "-q"],
        check=True,
    )
    with open(priv + ".pub", "r", encoding="utf-8") as pf:
        return pf.read().strip()


class TestP0SecurityHardening(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="labops-p0-sec-")
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmpdir.name)
        self._saved_env: Dict[str, Any] = {
            k: os.environ.get(k)
            for k in ("LABOPS_LOCAL_AGENT_URL", "LABOPS_NODE_AGENT_TOKEN", "LABOPS_ALLOW_TEST_CANARY_SSHD")
        }

        def provision_test_storage(host_id, storage_id, username, quota_gib):
            root = os.path.join(self.tmpdir.name, "storage")
            member_path = os.path.join(root, username)
            os.makedirs(member_path, exist_ok=True)
            return {
                "host_storage_root": root,
                "member_storage_directory": member_path,
                "quota_backend": "PROJECT_QUOTA",
                "verification_status": "VERIFIED",
            }

        self.enterContext(patch.object(
            StorageInventoryService, "provision_member_storage", side_effect=provision_test_storage
        ))

    def tearDown(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for u in list(self.svc.ssh_access_manager._canary_daemons.keys()):
            self.svc.ssh_access_manager.stop_canary_sshd_for_workspace(u)
        self.tmpdir.cleanup()

    def test_01_no_host_admin_impersonation_and_canary_blocked_in_prod(self) -> None:
        """G1/G4-1/G4-2: Tuyệt đối không dùng tài khoản quản trị host làm ssh_login_user và cấm host canary SSHD."""
        service_path = os.path.join(backend_dir, "app", "services", "labops_service.py")
        with open(service_path, "r", encoding="utf-8") as sf:
            src = sf.read()
        self.assertNotIn("import getpass", src)

        self.assertEqual(self.svc._resolve_effective_ssh_login_user("student_01", "workstation"), "student_01")
        self.assertEqual(self.svc._resolve_effective_ssh_login_user("student_01", "track"), "student_01")

        # _ensure_local_workspace_sshd_running bị vô hiệu hóa vĩnh viễn
        self.assertFalse(
            self.svc._ensure_local_workspace_sshd_running({"username": "student_01", "assigned_server": "workstation"})
        )

        # Cấm bật start_canary_sshd_for_workspace vào thư mục production data
        prod_ws = os.path.join(backend_dir, "data", "workspaces", "evil_user")
        with self.assertRaisesRegex(PermissionError, "CANARY_SSHD_FORBIDDEN"):
            self.svc.ssh_access_manager.start_canary_sshd_for_workspace(
                "workstation", "evil_user", workspace_dir=prod_ws
            )

        # Ngoài pytest / khi không bật LABOPS_ALLOW_TEST_CANARY_SSHD cũng phải bị chặn
        saved_pytest = os.environ.pop("PYTEST_CURRENT_TEST", None)
        os.environ.pop("LABOPS_ALLOW_TEST_CANARY_SSHD", None)
        try:
            with self.assertRaisesRegex(PermissionError, r"CANARY_SSHD_FORBIDDEN|HOST_SSHD_FALLBACK_FORBIDDEN"):
                self.svc.ssh_access_manager.start_canary_sshd_for_workspace(
                    "workstation", "evil_user", workspace_dir=os.path.join(self.tmpdir.name, "ws")
                )
        finally:
            if saved_pytest is not None:
                os.environ["PYTEST_CURRENT_TEST"] = saved_pytest

    def test_reject_invalid_image_reference_and_digest_pairs(self) -> None:
        wc = WorkspaceController(self.tmpdir.name)
        digest = "sha256:" + "c" * 64
        for ref, supplied_digest in (
            ("labops-workspace:v2", digest),
            (digest, "sha256:" + "d" * 64),
            ("repo@" + digest + "-extra", digest),
            ("repo@" + digest + "\n", digest),
            ("repo@" + digest, digest + "-extra"),
        ):
            with self.subTest(ref=ref, digest=supplied_digest):
                with self.assertRaisesRegex(WorkspaceSecurityError, "sha256"):
                    wc.build_provision_workspace_spec(
                        member={"id": "m-alice", "username": "alice"},
                        workspace={"id": "ws-alice", "owner": "alice"},
                        correlation_id="image-validation",
                        image_ref=ref,
                        image_digest=supplied_digest,
                    )

    def test_02_agent_unreachable_fail_closed_and_uid_allocation(self) -> None:
        """G4-3: Fail-closed khi thiếu image digest (PROVISION_FAILED) và khi Node Agent không reachable (AGENT_UNAVAILABLE)."""
        wc = WorkspaceController(self.tmpdir.name)
        uid_a, gid_a = wc.allocate_uid_gid("alice")
        uid_b, gid_b = wc.allocate_uid_gid("bob")
        self.assertGreaterEqual(uid_a, 20000)
        self.assertLessEqual(uid_a, 59999)
        self.assertEqual(uid_a, gid_a)
        self.assertNotEqual(uid_a, uid_b)

        # (a) Không cấu hình immutable @sha256 digest hoặc chỉ truyền mutable tag -> fail-closed PROVISION_FAILED
        no_digest_member = self.svc.provision_member_ssh_access({
            "full_name": "No Digest User",
            "username": "no_digest_u",
            "assigned_server": "workstation",
            "image_ref": "labops-workspace:v2",
            "cpu_limit_cores": 2,
            "ram_limit_gb": 4,
            "storage_quota_gb": 10,
            "_actor_id": "admin_root",
        })
        self.assertEqual(no_digest_member["workspace_delegation_status"], "PROVISION_FAILED")
        self.assertIn("sha256:", str(no_digest_member.get("workspace_delegation_reason") or ""))
        self.assertFalse(no_digest_member["authorization_gate"]["ssh_authorized"])

        # (b) Có pure local image ID (sha256:<64hex>) và @sha256: nhưng Node Agent không reachable -> fail-closed AGENT_UNAVAILABLE
        spec_local = wc.build_provision_workspace_spec(
            member={"id": "m-loc", "username": "alice", "assigned_server": "workstation", "ssh_port": 22015, "member_storage_directory": os.path.join(self.tmpdir.name, "alice")},
            workspace={"id": "ws-alice", "name": "ws-alice", "owner": "alice"},
            correlation_id="corr-loc-id",
            image_ref=f"sha256:{'c' * 64}",
        )
        self.assertEqual(spec_local["image_ref"], f"sha256:{'c' * 64}")
        self.assertEqual(spec_local["image_digest"], f"sha256:{'c' * 64}")

        member = self.svc.provision_member_ssh_access({
            "full_name": "Agent Down User",
            "username": "agent_down_u",
            "assigned_server": "workstation",
            "image_ref": f"labops-workspace:debian13-hardened@sha256:{'b' * 64}",
            "cpu_limit_cores": 2,
            "ram_limit_gb": 4,
            "storage_quota_gb": 10,
            "_actor_id": "admin_root",
        })
        self.assertNotEqual(member["status"], "READY")
        self.assertNotEqual(member["overall_readiness"], "READY")
        self.assertEqual(member["ssh_status"], "SSH_ACCESS_DENIED")
        self.assertEqual(member["workspace_delegation_status"], "AGENT_UNAVAILABLE")
        self.assertFalse(member["authorization_gate"]["ssh_authorized"])
        self.assertNotEqual(member.get("ssh_login_user"), "aiot-ws1")
        ws_dir = member["workspace_dir"]
        self.assertFalse(os.path.exists(os.path.join(ws_dir, ".ssh", "canary_sshd.pid")))

    def test_03_port_listening_without_isolated_container_flagged_not_isolated(self) -> None:
        """G4-5: Nếu cổng TCP đang mở trên host nhưng KHÔNG thuộc container cô lập -> SSH_RUNTIME_NOT_ISOLATED."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]

            ws_fs = self.svc.ssh_access_manager.setup_isolated_workspace_filesystem(
                host_id="workstation",
                username="raw_host_port",
                workspace_name="ws-raw-host-port",
                public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKeyData123456789012345678901234",
                cpu_limit_cores=2,
                ram_limit_gb=4,
                storage_quota_gb=10,
                gpu_permissions=[],
                authorize_ssh_key=False,
            )
            rv = self.svc.ssh_access_manager.verify_workspace_and_runtime(
                host_id="workstation",
                host_online=True,
                username="raw_host_port",
                workspace_name="ws-raw-host-port",
                expected_public_key="",
                ssh_port=port,
                cpu_limit_cores=2,
                ram_limit_gb=4,
                storage_quota_gb=10,
                gpu_permissions=[],
                enforce_canary_runtime=False,
                workspace_dir=ws_fs["workspace_dir"],
            )
            self.assertEqual(rv["ssh_runtime"], "SSH_RUNTIME_NOT_ISOLATED")
            self.assertEqual(rv["overall_status"], "SSH_RUNTIME_NOT_ISOLATED")
            self.assertEqual(rv["privilege_isolation"], "NOT_VERIFIED")
            self.assertEqual(rv["storage_quota"], "NOT_ENFORCEABLE")

    def test_04_password_activation_lockout_expiry_and_gate(self) -> None:
        """G4-6..10: Gate từ chối trước activation, temp pw hết hạn, brute-force 5 lần khóa 15p, yếu/trùng mật khẩu tạm."""
        # Test expired temp password
        expired_member = self.svc.provision_member_ssh_access({
            "full_name": "Expired Temp PW User",
            "username": "expired_pw_u",
            "assigned_server": "workstation",
            "temp_password_ttl_seconds": -10,
            "_actor_id": "admin_root",
        })
        secrets_exp = self.svc.claim_member_secrets(expired_member["id"], actor="admin_root")
        with self.assertRaisesRegex(PermissionError, "TEMP_PASSWORD_EXPIRED"):
            self.svc.change_member_first_login_password(
                "expired_pw_u", secrets_exp["temporary_password"], "StrongPass#2026!"
            )

        gate_exp = evaluate_ssh_authorization_gate(
            member=expired_member,
            account_security=self.svc.ssh_access_manager.get_account_security_state("expired_pw_u"),
            runtime_verification={"docker_running": "VERIFIED", "privilege_isolation": "VERIFIED", "storage_quota": "VERIFIED", "cpu_ram_limits": "VERIFIED", "ssh_runtime": "VERIFIED"},
        )
        self.assertFalse(gate_exp["ssh_authorized"])
        self.assertIn("ACTIVATION_PASSWORD_EXPIRED", gate_exp["blocking_codes"])

        # Test lockout after 5 wrong attempts + weak/same password validation
        lock_member = self.svc.provision_member_ssh_access({
            "full_name": "Lockout Test User",
            "username": "lockout_u",
            "assigned_server": "workstation",
            "_actor_id": "admin_root",
        })
        secrets_lock = self.svc.claim_member_secrets(lock_member["id"], actor="admin_root")

        with self.assertRaises(ValueError):
            self.svc.change_member_first_login_password("lockout_u", secrets_lock["temporary_password"], "weak")
        with self.assertRaisesRegex(ValueError, "NEW_PASSWORD_MUST_DIFFER"):
            self.svc.change_member_first_login_password(
                "lockout_u", secrets_lock["temporary_password"], secrets_lock["temporary_password"]
            )

        for _ in range(5):
            with self.assertRaisesRegex(PermissionError, "INVALID_TEMP_PASSWORD"):
                self.svc.change_member_first_login_password("lockout_u", "WrongTemp#999!", "ValidNewPass#2026")

        # Lần thứ 6 dùng ĐÚNG mật khẩu tạm vẫn bị ACCOUNT_LOCKED
        with self.assertRaisesRegex(PermissionError, "ACCOUNT_LOCKED"):
            self.svc.change_member_first_login_password(
                "lockout_u", secrets_lock["temporary_password"], "ValidNewPass#2026"
            )

        # Kiểm tra password hash lưu trong v2_ssh_accounts.json dùng scrypt, không plaintext
        with open(os.path.join(self.tmpdir.name, "v2_ssh_accounts.json"), "r", encoding="utf-8") as af:
            acc_data = json.load(af)["accounts"]["lockout_u"]
        self.assertTrue(str(acc_data["temp_password_hash"]).startswith("scrypt$"))
        self.assertIsNone(acc_data["active_password_hash"])

    def test_05_client_enrolled_pubkey_and_separate_channel_zip(self) -> None:
        """G4-11/G4-12: Client-enrolled public key dùng mode CLIENT_ENROLLED_KEY (cấm PASSPHRASE_VERIFIED); ZIP không chứa secret."""
        client_pub = _generate_ed25519_pubkey(self.tmpdir.name, "student-laptop")
        member = self.svc.provision_member_ssh_access({
            "full_name": "Client Key User",
            "username": "client_key_u",
            "assigned_server": "track",
            "ssh_public_key": client_pub,
            "_actor_id": "admin_root",
        })
        self.assertEqual(member["ssh_key_enrollment_mode"], "CLIENT_ENROLLED_PUBKEY")
        self.assertEqual(member["ssh_key_passphrase_mode"], "CLIENT_ENROLLED_KEY")
        self.assertEqual(member["ssh_public_key"], client_pub)

        secrets_pkg = self.svc.claim_member_secrets(member["id"], actor="admin_root")
        with self.assertRaises(LookupError):
            self.svc.claim_member_secrets(member["id"], actor="admin_root")

        _, zip_bytes = self.svc.claim_member_access_zip(member["id"], actor="admin_root")
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for name in zf.namelist():
                txt = zf.read(name).decode("utf-8", errors="ignore")
                self.assertNotIn(secrets_pkg["temporary_password"], txt)
                self.assertNotIn("PRIVATE KEY", txt)

    def test_06_honest_storage_inventory_no_fake_pools(self) -> None:
        """G4-13: StorageInventoryService không bao giờ tạo giả pool ZFS track hay /media/LabOps-Data /dev/sdb1."""
        inv = StorageInventoryService()
        self.assertEqual(inv.get_storage_pools("track"), [])
        ws_pools = inv.get_storage_pools("workstation")
        self.assertFalse(any(p.get("mount_point") == "/media/LabOps-Data" and p.get("device") == "/dev/sdb1" for p in ws_pools))
        for p in ws_pools:
            if not p.get("quota_supported"):
                self.assertFalse(p.get("quota_enforced"))
                self.assertIn(p.get("verification_status"), ("QUOTA_NOT_ENFORCEABLE", "INELIGIBLE"))

    def test_07_e2e_python_to_go_agent_http_gate_and_cpu_policies(self) -> None:
        """G4-14/G4-15: Tích hợp Python -> Go Agent HTTP (/api/v1/agent/execute) với SHARED_WEIGHTED, activation gate và revoke."""
        state: Dict[str, Any] = {
            "actions": [],
            "authorized_keys": [],
            "running": True,
            "storage_quota_verified": True,
            "docker_running_status": "VERIFIED",
        }
        token = "unit-test-agent-token-32bytes-ok!"
        fake_cid = "a" * 64

        class MockGoAgentHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:
                if self.headers.get("X-LabOps-Agent-Secret") != token:
                    self.send_response(401)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                action = body.get("action")
                params = body.get("parameters") or {}
                state["actions"].append(action)

                if action == "PROVISION_DOCKER_WORKSPACE":
                    spec = json.loads(params["spec_json"])
                    self.result = {
                        "workspace_id": spec["workspace_id"],
                        "canonical_container_id": fake_cid,
                        "observed_state": "ACTIVE",
                        "uid": spec["uid"],
                        "gid": spec["gid"],
                        "ssh_port": spec["ssh_port"],
                        "resource_mode": spec["resource_mode"],
                        "cpu_weight": spec["cpu_weight"],
                    }
                    self._send_ok(body.get("request_id", "r1"), action, self.result)
                    return

                if action == "VERIFY_DOCKER_WORKSPACE":
                    res = {
                        "verified": state["docker_running_status"] == "VERIFIED",
                        "container_id": fake_cid,
                        "storage_quota_verified": state["storage_quota_verified"],
                        "checks": {
                            "docker_running": {
                                "status": state["docker_running_status"],
                                "observed_value": "running" if state["docker_running_status"] == "VERIFIED" else "exited",
                                "expected_value": "running",
                                "source": "docker_inspect",
                            },
                            "privilege_isolation": {
                                "status": "VERIFIED",
                                "observed_value": f"{params.get('uid')}:{params.get('gid')}",
                                "expected_value": "uid>=20000,CapDrop=ALL,no-new-privileges",
                                "source": "docker_inspect",
                            },
                            "resource_enforcement": {
                                "status": "VERIFIED",
                                "observed_value": {"resource_mode": params.get("resource_mode"), "cpu_shares": 100},
                                "expected_value": "CGROUP_ENFORCED",
                                "source": "docker_hostconfig",
                            },
                            "ssh_runtime": {
                                "status": "VERIFIED",
                                "observed_value": int(params.get("ssh_port") or 22001),
                                "expected_value": int(params.get("ssh_port") or 22001),
                                "source": "docker_port_bindings_and_tcp_banner",
                            },
                            "storage_quota": {
                                "status": "VERIFIED" if state["storage_quota_verified"] else "NOT_ENFORCEABLE",
                                "observed_value": "prjquota" if state["storage_quota_verified"] else "none",
                                "expected_value": "prjquota",
                                "source": "repquota",
                            },
                        },
                    }
                    self._send_ok(body.get("request_id", "r2"), action, res)
                    return

                if action == "SYNC_SSH_AUTHORIZED_KEYS":
                    state["authorized_keys"] = json.loads(params.get("keys_json") or "[]")
                    self._send_ok(
                        body.get("request_id", "r3"),
                        action,
                        {"synced": True, "key_count": len(state["authorized_keys"])},
                    )
                    return

                self._send_ok(body.get("request_id", "r"), action, {})

            def _send_ok(self, req_id: str, action: str, result: Any) -> None:
                payload = json.dumps({"success": True, "request_id": req_id, "action": action, "result": result}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockGoAgentHandler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            os.environ["LABOPS_LOCAL_AGENT_URL"] = f"http://127.0.0.1:{port}"
            os.environ["LABOPS_NODE_AGENT_TOKEN"] = token

            member = self.svc.provision_member_ssh_access({
                "full_name": "E2E Docker User",
                "username": "e2e_docker_u",
                "assigned_server": "workstation",
                "image_ref": f"labops-workspace:debian13-hardened@sha256:{'b' * 64}",
                "cpu_policy": "SHARED_WEIGHTED",
                "cpu_weight": 150,
                "cpu_limit_cores": 8,
                "ram_limit_gb": 16,
                "storage_quota_gb": 50,
                "_actor_id": "admin_root",
            })
            # Container & SHARED_WEIGHTED & quota đã verify, nhưng chưa đổi temp password -> gate VẪN ĐÓNG authorized_keys!
            self.assertEqual(member["container_id"], fake_cid)
            self.assertGreaterEqual(member["uid"], 20000)
            self.assertEqual(member["activation_status"], "PENDING_ACTIVATION")
            self.assertEqual(state["authorized_keys"], [])
            self.assertFalse(member["authorization_gate"]["ssh_authorized"])
            self.assertEqual(member["runtime_verification"]["cpu_ram_limits"], "VERIFIED")
            self.assertIn("VERIFY_DOCKER_WORKSPACE", state["actions"])

            # Kiểm tra HARD_LIMIT verification path
            hard_member = self.svc.provision_member_ssh_access({
                "full_name": "E2E Hard Limit User",
                "username": "e2e_hard_u",
                "assigned_server": "workstation",
                "image_ref": f"labops-workspace:debian13-hardened@sha256:{'b' * 64}",
                "cpu_policy": "HARD_LIMIT",
                "cpu_limit_cores": 4,
                "ram_limit_gb": 8,
                "storage_quota_gb": 50,
                "_actor_id": "admin_root",
            })
            self.assertEqual(hard_member["cpu_policy"], "HARD_LIMIT")
            self.assertEqual(hard_member["runtime_verification"]["cpu_ram_limits"], "VERIFIED")

            # Sau khi đổi mật khẩu lần đầu -> gate TỰ ĐỘNG mở & SYNC_SSH_AUTHORIZED_KEYS ghi đúng 1 public key
            secrets_pkg = self.svc.claim_member_secrets(member["id"], actor="admin_root")
            act = self.svc.change_member_first_login_password(
                "e2e_docker_u", secrets_pkg["temporary_password"], "DockerStrong#2026!"
            )
            self.assertEqual(act["overall_readiness"], "READY")
            self.assertTrue(act["authorization_gate"]["ssh_authorized"])
            self.assertEqual(state["authorized_keys"], [member["ssh_public_key"]])

            # Khi container dừng lại -> reverify fail-closed & tự xóa authorized_keys qua Go Agent
            state["docker_running_status"] = "FAILED"
            rev_status = self.svc.reverify_member_workspace("e2e_docker_u", actor="admin_root")
            self.assertFalse(rev_status["authorization_gate"]["ssh_authorized"])
            self.assertEqual(state["authorized_keys"], [])

            # Khôi phục container running rồi test revoke -> xóa authorized_keys + REVOKED
            state["docker_running_status"] = "VERIFIED"
            self.svc.reverify_member_workspace("e2e_docker_u", actor="admin_root")
            self.assertEqual(state["authorized_keys"], [member["ssh_public_key"]])
            rev = self.svc.revoke_member_ssh_access(member["id"], actor="admin_root")
            self.assertEqual(rev["status"], "VERIFIED_REVOKED")
            self.assertEqual(state["authorized_keys"], [])
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_08_gpu_uuid_mapping_soft_quota_gate_and_reverify_provision_retry(self) -> None:
        """Kiểm tra map GPU ID -> NVIDIA UUID, opt-in soft quota gate và reverify tự động provision + truyền đúng ssh_ip."""
        # 1. GPU ID -> UUID mapping
        wc = WorkspaceController(self.tmpdir.name)
        gpu_uuid = "GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc"
        spec = wc.build_provision_workspace_spec(
            member={
                "id": "m-gpu",
                "username": "gpu_u",
                "assigned_server": "workstation",
                "ssh_port": 22016,
                "member_storage_directory": os.path.join(self.tmpdir.name, "gpu_u"),
                "gpu_permissions": ["workstation-gpu-01"],
            },
            workspace={"id": "ws-gpu", "name": "ws-gpu", "owner": "gpu_u"},
            correlation_id="corr-gpu",
            image_ref=f"sha256:{'d' * 64}",
            gpu_id_to_uuid={"workstation-gpu-01": gpu_uuid},
        )
        self.assertEqual(spec["gpu_uuids"], [gpu_uuid])

        # 2. evaluate_ssh_authorization_gate: mặc định fail-closed STORAGE_QUOTA_NOT_ENFORCEABLE,
        #    opt-in LABOPS_ALLOW_SOFT_STORAGE_QUOTA=true mở POLICY_ONLY nhưng vẫn khóa khi chưa activation
        base_member = {
            "id": "m-gate",
            "username": "gate_u",
            "status": "PENDING_PROVISIONING",
            "ssh_key_status": "ACTIVE",
            "ssh_public_key": _generate_ed25519_pubkey(self.tmpdir.name, "gate"),
            "ssh_port": 22016,
            "activation_status": "ACTIVATED",
            "password_status": "ACTIVE",
        }
        base_rv = {
            "docker_running": "VERIFIED",
            "privilege_isolation": "VERIFIED",
            "cpu_ram_limits": "VERIFIED",
            "ssh_runtime": "VERIFIED",
            "storage_quota": "NOT_ENFORCEABLE",
            "gpu_isolation": "NOT_APPLICABLE",
            "checks_detail": {"storage_quota": {"observed_value": {"mount_point": "/mnt/HDD3"}, "source": "proc_mounts"}},
        }
        acc_activated = {"password_status": "ACTIVE", "activation_status": "ACTIVATED", "must_change_password": False}
        acc_pending = {"password_status": "CHANGE_REQUIRED", "activation_status": "PENDING_ACTIVATION", "must_change_password": True}

        gate_default = evaluate_ssh_authorization_gate(member=base_member, account_security=acc_activated, runtime_verification=base_rv)
        self.assertFalse(gate_default["ssh_authorized"])
        self.assertIn("STORAGE_QUOTA_NOT_ENFORCEABLE", gate_default["blocking_codes"])

        with patch.dict(os.environ, {"LABOPS_ALLOW_SOFT_STORAGE_QUOTA": "true"}):
            gate_soft_open = evaluate_ssh_authorization_gate(member=base_member, account_security=acc_activated, runtime_verification=base_rv)
            self.assertTrue(gate_soft_open["ssh_authorized"])
            self.assertEqual(gate_soft_open["overall_readiness"], "READY")
            self.assertEqual(gate_soft_open["gates"]["storage_quota"]["status"], "POLICY_ONLY")

            gate_pending_act = evaluate_ssh_authorization_gate(
                member={**base_member, "activation_status": "PENDING_ACTIVATION", "password_status": "CHANGE_REQUIRED"},
                account_security=acc_pending,
                runtime_verification=base_rv,
            )
            self.assertFalse(gate_pending_act["ssh_authorized"])
            self.assertIn("PENDING_ACTIVATION", gate_pending_act["blocking_codes"])

        with patch.dict(
            os.environ,
            {
                "LABOPS_ALLOW_SOFT_STORAGE_QUOTA": "true",
                "LABOPS_AUTO_ACTIVATE_SSH_ON_PROVISION": "true",
            },
        ):
            gate_auto_act = evaluate_ssh_authorization_gate(
                member={**base_member, "activation_status": "PENDING_ACTIVATION", "password_status": "CHANGE_REQUIRED"},
                account_security=acc_pending,
                runtime_verification=base_rv,
            )
            self.assertTrue(gate_auto_act["ssh_authorized"])
            self.assertEqual(gate_auto_act["overall_readiness"], "READY")
            self.assertEqual(gate_auto_act["gates"]["account_activation"]["status"], "POLICY_ONLY")

            gate_revoked_act = evaluate_ssh_authorization_gate(
                member={**base_member, "activation_status": "REVOKED", "password_status": "REVOKED"},
                account_security={"password_status": "REVOKED", "activation_status": "REVOKED", "must_change_password": False},
                runtime_verification=base_rv,
            )
            self.assertFalse(gate_revoked_act["ssh_authorized"])
            self.assertIn("ACCOUNT_REVOKED", gate_revoked_act["blocking_codes"])

        # 3. reverify_member_workspace retries PROVISION_DOCKER_WORKSPACE when container_id is missing & passes ssh_ip
        seen: Dict[str, Any] = {"actions": [], "verify_vpn_bind_ip": None, "prov_spec": None}
        token = "unit-test-reverify-token-32bytes!"
        fake_cid = "b" * 64

        class ReverifyAgentHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:
                if self.headers.get("X-LabOps-Agent-Secret") != token:
                    self.send_response(401)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                action = body.get("action")
                params = body.get("parameters") or {}
                seen["actions"].append(action)
                if action == "PROVISION_DOCKER_WORKSPACE":
                    seen["prov_spec"] = json.loads(params["spec_json"])
                    res = {"workspace_id": seen["prov_spec"]["workspace_id"], "canonical_container_id": fake_cid, "observed_state": "ACTIVE"}
                elif action == "VERIFY_DOCKER_WORKSPACE":
                    seen["verify_vpn_bind_ip"] = params.get("vpn_bind_ip")
                    res = {
                        "verified": True,
                        "container_id": fake_cid,
                        "checks": {
                            "docker_running": {"status": "VERIFIED", "observed_value": "running", "expected_value": "running", "source": "docker_inspect"},
                            "privilege_isolation": {"status": "VERIFIED", "observed_value": "25000:25000", "expected_value": "isolated", "source": "docker_inspect"},
                            "resource_enforcement": {"status": "VERIFIED", "observed_value": "ok", "expected_value": "enforced", "source": "docker_hostconfig"},
                            "ssh_runtime": {"status": "VERIFIED", "observed_value": 22017, "expected_value": 22017, "source": "docker_port_bindings"},
                        },
                    }
                else:
                    res = {"synced": True}
                payload = json.dumps({"success": True, "request_id": body.get("request_id", "r"), "action": action, "result": res}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), ReverifyAgentHandler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            # First provision fails because agent is unreachable -> member has no container_id
            os.environ["LABOPS_LOCAL_AGENT_URL"] = "http://127.0.0.1:9"
            os.environ["LABOPS_NODE_AGENT_TOKEN"] = token
            member = self.svc.provision_member_ssh_access({
                "full_name": "Retry Prov User",
                "username": "retry_prov_u",
                "assigned_server": "workstation",
                "image_ref": f"sha256:{'e' * 64}",
                "cpu_limit_cores": 4,
                "ram_limit_gb": 8,
                "storage_quota_gb": 20,
                "_actor_id": "admin_root",
            })
            self.assertFalse(member.get("container_id"))
            # Simulate WireGuard client IP differing from host ssh_ip
            member["wireguard"] = {"status": "ACTIVE", "client_ip": "10.88.40.99"}
            member["ssh_ip"] = "192.168.167.251"
            self.svc._save_persistent_data()

            # Bring agent online and call reverify -> must auto-provision & verify using ssh_ip, NOT client_ip
            os.environ["LABOPS_LOCAL_AGENT_URL"] = f"http://127.0.0.1:{port}"
            with patch.dict(
                os.environ,
                {
                    "LABOPS_ALLOW_SOFT_STORAGE_QUOTA": "true",
                    "LABOPS_WORKSPACE_IMAGE_REF": f"sha256:{'e' * 64}",
                    "LABOPS_WORKSPACE_IMAGE_DIGEST": f"sha256:{'e' * 64}",
                },
            ):
                status = self.svc.reverify_member_workspace("retry_prov_u", actor="admin_root")
            self.assertIn(
                "PROVISION_DOCKER_WORKSPACE",
                seen["actions"],
                msg=f"reverify status={status}",
            )
            self.assertIn("VERIFY_DOCKER_WORKSPACE", seen["actions"])
            self.assertEqual(seen["verify_vpn_bind_ip"], "192.168.167.251")
            self.assertEqual(status["workspace_delegation_status"], "VERIFIED")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_09_container_first_login_gate_enforced_and_activates(self) -> None:
        mgr = self.svc.ssh_access_manager
        username = "gate_member"
        temp_pw, _ = mgr.issue_temporary_password(
            member_id="mem-gate-01",
            username=username,
            actor="admin_root",
        )
        ws_id = "ws-gate-01"
        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGateTestKeyMaterialForUnitTest gate@test"

        with tempfile.TemporaryDirectory() as runtime_root, tempfile.TemporaryDirectory() as storage_dir:
            ssh_ro = os.path.join(runtime_root, ws_id, "ssh_ro")
            os.makedirs(ssh_ro, exist_ok=True)
            ak_path = os.path.join(ssh_ro, "authorized_keys")
            with open(ak_path, "w", encoding="utf-8") as wf:
                wf.write(pubkey + "\n")

            with patch.dict(os.environ, {"LABOPS_WORKSPACE_RUNTIME_DIR": runtime_root}):
                res1 = mgr.sync_container_first_login_gate(
                    workspace_id=ws_id,
                    username=username,
                    keys=[pubkey],
                    member_id="mem-gate-01",
                    member_storage_dir=storage_dir,
                )

            self.assertTrue(res1["synced"])
            self.assertTrue(res1["must_change_password"])
            gate_script = os.path.join(ssh_ro, "first_login_gate.py")
            policy_path = os.path.join(ssh_ro, "first_login_policy.json")
            self.assertTrue(os.path.isfile(gate_script))
            self.assertTrue(os.path.isfile(policy_path))
            with open(ak_path, "r", encoding="utf-8") as rf:
                ak_content = rf.read().strip()
            self.assertTrue(
                ak_content.startswith('command="/usr/bin/python3 /etc/labops-ssh/first_login_gate.py" '),
                msg=f"ak_content={ak_content}",
            )
            self.assertIn(pubkey, ak_content)

            with open(policy_path, "r", encoding="utf-8") as rf:
                policy = json.load(rf)
            self.assertTrue(policy["must_change_password"])
            self.assertTrue(str(policy.get("temp_password_hash") or "").startswith("scrypt$"))

            # Reissuing an unused temp password preserves previous unused hash in policy so retries keep working
            temp_pw2, _ = mgr.issue_temporary_password(
                member_id="mem-gate-01",
                username=username,
                actor="admin_root",
            )
            with patch.dict(os.environ, {"LABOPS_WORKSPACE_RUNTIME_DIR": runtime_root}):
                res_reissue = mgr.sync_container_first_login_gate(
                    workspace_id=ws_id,
                    username=username,
                    keys=[pubkey],
                    member_id="mem-gate-01",
                    member_storage_dir=storage_dir,
                )
            self.assertTrue(res_reissue["synced"])
            self.assertTrue(res_reissue["must_change_password"])
            with open(policy_path, "r", encoding="utf-8") as rf:
                reissued_policy = json.load(rf)
            accepted_hashes = reissued_policy.get("accepted_temp_password_hashes") or []
            self.assertGreaterEqual(len(accepted_hashes), 2)
            self.assertEqual(accepted_hashes[0], reissued_policy["temp_password_hash"])
            self.assertIn(policy["temp_password_hash"], accepted_hashes)
            self.assertNotEqual(temp_pw, temp_pw2)

            # Non-TTY execution before activation must fail closed with clear Vietnamese guidance
            activation_path = os.path.join(storage_dir, ".labops_activation.json")
            attempts_path = os.path.join(storage_dir, ".labops_activation_attempts.json")
            env_gate = {
                **os.environ,
                "LABOPS_FIRST_LOGIN_POLICY_PATH": policy_path,
                "LABOPS_FIRST_LOGIN_ACTIVATION_PATH": activation_path,
                "LABOPS_FIRST_LOGIN_ATTEMPTS_PATH": attempts_path,
            }
            proc_nontty = subprocess.run(
                [sys.executable, gate_script],
                input="",
                capture_output=True,
                text=True,
                env=env_gate,
            )
            self.assertNotEqual(proc_nontty.returncode, 0)
            self.assertIn("ssh labops-gate_member", proc_nontty.stderr)

            # Simulate completed container activation marker matching active temp_password_hash
            with open(activation_path, "w", encoding="utf-8") as wf:
                json.dump(
                    {
                        "username": username,
                        "member_id": "mem-gate-01",
                        "activated": True,
                        "activated_at": "2026-11-20T04:00:00+00:00",
                        "activated_for_hash": reissued_policy["temp_password_hash"],
                        "new_password_hash": "scrypt$16384$8$1$00112233$44556677",
                    },
                    wf,
                )

            # Activated non-TTY command (e.g., ssh labops-gate_member echo ok) must pass through
            proc_after = subprocess.run(
                [sys.executable, gate_script],
                capture_output=True,
                text=True,
                env={**env_gate, "SSH_ORIGINAL_COMMAND": "echo gate_ok"},
            )
            self.assertEqual(proc_after.returncode, 0, msg=proc_after.stderr)
            self.assertIn("gate_ok", proc_after.stdout)

            # Re-sync must promote account to ACTIVATED and drop forced command from authorized_keys
            with patch.dict(os.environ, {"LABOPS_WORKSPACE_RUNTIME_DIR": runtime_root}):
                res2 = mgr.sync_container_first_login_gate(
                    workspace_id=ws_id,
                    username=username,
                    keys=[pubkey],
                    member_id="mem-gate-01",
                    member_storage_dir=storage_dir,
                )
            self.assertTrue(res2["synced"])
            self.assertFalse(res2["must_change_password"])
            sec = mgr.get_account_security_state(username, expected_member_id="mem-gate-01")
            self.assertEqual(sec["activation_status"], "ACTIVATED")
            self.assertEqual(sec["password_status"], "ACTIVE")
            self.assertFalse(sec["must_change_password"])
            with open(ak_path, "r", encoding="utf-8") as rf:
                self.assertEqual(rf.read().strip(), pubkey)
            self.assertTrue(bool(temp_pw))


if __name__ == "__main__":
    unittest.main()
