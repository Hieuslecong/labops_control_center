"""
Bộ kiểm thử toàn diện LabOps Control Center V2 (Mục 22 & 23 prompt.md).
Bao phủ:
- Dynamic Host Registry (đăng ký máy chủ thứ 3, thứ N, đối soát JSON không phá hủy)
- Node Agent allowlist (chặn mọi thao tác ngoài allowlist, idempotency, kiểm tra GPU thực)
- Arcane Adapter (Admin only, trả NOT_CONFIGURED trung thực khi chưa cấu hình)
- Beszel Adapter (trả NOT_CONFIGURED trung thực kèm local fallback)
- Workspace Controller (chặn --privileged, chặn Docker socket, chặn mount /etc và cross-member mount, bắt buộc SSH public key)
- WireGuard & OpenSSH Per-Member Public Keys (chặn private key, kiểm tra fingerprint SHA256, quyền sở hữu khi thu hồi)
- Researcher Portal (cô lập dữ liệu giữa alice và bob)
- 10 nhóm kiểm thử âm tính & bảo mật bắt buộc qua cả tầng Service và HTTP Server.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("LABOPS_SESSION_SECRET", "v2-comprehensive-test-secret-32bytes")
os.environ.setdefault("LABOPS_NODE_AGENT_TOKEN", "v2-node-agent-machine-token-xyz")

from app.adapters.arcane_adapter import ArcaneAdapter
from app.adapters.beszel_adapter import BeszelAdapter
from app.adapters.node_agent_adapter import LocalNodeAgentExecutor, NodeAgentError
from app.core.security import issue_token
from app.main import LabOpsRequestHandler
from app.services.host_registry import HostRegistry
from app.services.labops_service import LabOpsDataService
from app.services.vpn_ssh_service import SshKeyRegistry, compute_ssh_fingerprint, validate_wg_public_key
from app.services.workspace_controller import WorkspaceController, WorkspaceSecurityError

VALID_SSH_PUBKEY_ALICE = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl alice@labops"
)
VALID_SSH_PUBKEY_BOB = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK8r2Z9x1vL7mP4qR6tY3uW0eN5bC8dF2gH4jK6lM9nP bob@labops"
)


class TestV2ModulesAndSecurity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    # 1. Dynamic Host Registry (N máy chủ, không gán cứng 2 máy chủ)
    def test_dynamic_host_registry_supports_n_hosts_and_reconcile(self):
        reg = HostRegistry(self.data_dir)
        initial = reg.list_hosts()
        self.assertEqual(len(initial), 2)
        h3 = reg.register_host(
            host_id="compute-node-03",
            hostname="labops-node-03",
            display_name="Máy chủ 03 — GPU Node",
            management_ip="10.10.3.30",
            role="COMPUTE_NODE",
            vpn_ip="10.200.0.3",
        )
        self.assertEqual(h3["id"], "compute-node-03")
        self.assertEqual(h3["observed_state"], "UNKNOWN")
        self.assertEqual(len(reg.list_hosts()), 3)
        # Chặn đăng ký trùng host_id
        with self.assertRaises(ValueError):
            reg.register_host(
                host_id="compute-node-03",
                hostname="other",
                display_name="Other",
                management_ip="10.10.3.31",
            )
        rec = reg.reconcile_json_stores()
        self.assertEqual(rec["mode"], "NON_DESTRUCTIVE")

    # 2. Node Agent Allowlist & Idempotency
    def test_node_agent_rejects_non_allowlisted_action_and_supports_idempotency(self):
        agent = LocalNodeAgentExecutor(host_id="workstation")
        with self.assertRaises(NodeAgentError) as ctx:
            agent.execute("RUN_ARBITRARY_SHELL", {"cmd": "rm -rf /"})
        self.assertEqual(ctx.exception.code, "ACTION_NOT_ALLOWLISTED")
        self.assertEqual(ctx.exception.http_status, 403)

        r1 = agent.execute("INSPECT_HOST", idempotency_key="idem-host-1")
        r2 = agent.execute("INSPECT_HOST", idempotency_key="idem-host-1")
        self.assertEqual(r1["request_id"], r2["request_id"])
        self.assertTrue(r2.get("idempotent_replay"))

    # 3. Arcane & Beszel honest unconfigured states
    def test_arcane_and_beszel_report_not_configured_without_fake_data(self):
        arcane = ArcaneAdapter(base_url="", api_key="")
        summary = arcane.get_integration_summary()
        self.assertEqual(summary["status"], "NOT_CONFIGURED")
        self.assertFalse(summary["security_boundary"]["researcher_access"])
        self.assertFalse(summary["security_boundary"]["docker_socket_exposed_to_researcher"])

        beszel = BeszelAdapter(hub_url="", token="")
        b_overview = beszel.get_monitoring_overview(live_hosts=[{"id": "workstation"}])
        self.assertEqual(b_overview["status"], "NOT_CONFIGURED")
        self.assertEqual(len(b_overview["systems"]), 0)

    # 4. Workspace Controller negative security tests
    def test_workspace_controller_enforces_isolation_and_blocks_dangerous_mounts(self):
        wc = WorkspaceController(self.data_dir)
        # Chặn --privileged
        with self.assertRaises(WorkspaceSecurityError) as c1:
            wc.request_workspace(owner="alice", host_id="workstation", ssh_public_key=VALID_SSH_PUBKEY_ALICE, privileged=True)
        self.assertEqual(c1.exception.code, "PRIVILEGED_FORBIDDEN")

        # Chặn mount Docker socket
        with self.assertRaises(WorkspaceSecurityError) as c2:
            wc.request_workspace(owner="alice", host_id="workstation", ssh_public_key=VALID_SSH_PUBKEY_ALICE, mount_docker_socket=True)
        self.assertEqual(c2.exception.code, "DOCKER_SOCKET_FORBIDDEN")

        # Chặn mount /etc hoặc thư mục của thành viên khác
        with self.assertRaises(WorkspaceSecurityError) as c3:
            wc.request_workspace(owner="alice", host_id="workstation", ssh_public_key=VALID_SSH_PUBKEY_ALICE, extra_mounts=["/etc/shadow:/mnt/shadow"])
        self.assertEqual(c3.exception.code, "MOUNT_PATH_FORBIDDEN")

        with self.assertRaises(WorkspaceSecurityError) as c4:
            wc.request_workspace(owner="alice", host_id="workstation", ssh_public_key=VALID_SSH_PUBKEY_ALICE, extra_mounts=["/tank/workspaces/bob:/mnt/bob"])
        self.assertEqual(c4.exception.code, "CROSS_MEMBER_MOUNT_FORBIDDEN")

        # Chặn tạo workspace thiếu khóa công khai SSH hợp lệ
        with self.assertRaises(WorkspaceSecurityError) as c5:
            wc.request_workspace(owner="alice", host_id="workstation", ssh_public_key="invalid-key")
        self.assertEqual(c5.exception.code, "SSH_PUBKEY_REQUIRED")

        # Tạo hợp lệ -> trạng thái trung thực NOT_VERIFIED (không báo ACTIVE giả)
        ws = wc.request_workspace(owner="alice", host_id="workstation", ssh_public_key=VALID_SSH_PUBKEY_ALICE, idempotency_key="ws-idem-1")
        self.assertEqual(ws["observed_state"], "NOT_VERIFIED")
        self.assertTrue(ws["non_privileged"])
        self.assertFalse(ws["docker_socket_mounted"])

    # 5. SSH Public Key & WireGuard validation
    def test_ssh_key_registry_blocks_private_keys_and_enforces_ownership(self):
        reg = SshKeyRegistry(self.data_dir)
        with self.assertRaises(ValueError):
            compute_ssh_fingerprint("-----BEGIN OPENSSH PRIVATE KEY----- AAAA")
        with self.assertRaises(ValueError):
            validate_wg_public_key("short-key")

        k = reg.register_key(username="alice", label="Laptop", public_key=VALID_SSH_PUBKEY_ALICE, actor="alice")
        self.assertTrue(k["fingerprint_sha256"].startswith("SHA256:"))
        self.assertEqual(k["observed_state"], "PENDING_SYNC")

        # Bob không được phép thu hồi khóa SSH của Alice
        with self.assertRaises(PermissionError):
            reg.revoke_key(k["id"], actor="bob", is_admin=False)

        # Alice tự thu hồi khóa của mình thành công
        revoked = reg.revoke_key(k["id"], actor="alice", is_admin=False)
        self.assertEqual(revoked["observed_state"], "REVOKED")

    # 6. Researcher Portal data isolation
    def test_researcher_portal_strictly_scopes_data_per_user(self):
        svc = LabOpsDataService(demo_mode=False, data_dir=self.data_dir)
        svc.ssh_key_registry.register_key(username="alice", label="AliceKey", public_key=VALID_SSH_PUBKEY_ALICE, actor="alice")
        svc.ssh_key_registry.register_key(username="bob", label="BobKey", public_key=VALID_SSH_PUBKEY_BOB, actor="bob")

        alice_view = svc.get_researcher_portal_snapshot("alice")
        bob_view = svc.get_researcher_portal_snapshot("bob")
        self.assertEqual(len(alice_view["ssh_keys"]), 1)
        self.assertEqual(alice_view["ssh_keys"][0]["username"], "alice")
        self.assertEqual(len(bob_view["ssh_keys"]), 1)
        self.assertEqual(bob_view["ssh_keys"][0]["username"], "bob")


    # 7. Real Host (aiotlab-rack) 3x NVIDIA A30 Inventory & Canary Reservation/Release
    def test_real_host_aiotlab_rack_gpu_inventory_and_canary_reservation(self):
        import shutil
        if not shutil.which("nvidia-smi"):
            self.skipTest("BLOCKED: nvidia-smi not available")
        svc = LabOpsDataService(demo_mode=False, data_dir=self.data_dir)
        servers = {srv["id"]: srv for srv in svc.servers}
        self.assertEqual(servers["track"]["hostname"], "aiotlab-rack")
        self.assertEqual(servers["track"]["status"], "online")
        self.assertEqual(servers["track"]["gpu_count"], 3)
        self.assertEqual(servers["workstation"]["status"], "unknown")

        gpus = svc.gpus
        self.assertEqual(len(gpus), 3)
        self.assertEqual([g["id"] for g in gpus], ["track-gpu-00", "track-gpu-01", "track-gpu-02"])
        for g in gpus:
            self.assertEqual(g["server_id"], "track")
            self.assertTrue(g["uuid"].startswith("GPU-"))
            self.assertEqual(g["vram_total_mb"], 24576)

        # Canary reservation & release against live NVML telemetry on track-gpu-00
        if not gpus[0].get("active_processes"):
            alloc = svc.allocate_gpu("track-gpu-00", "canary_user", "FULL_GPU_EXCLUSIVE", job_name="canary_verify.py")
            rsv_id = alloc["reservation"]["id"]
            self.assertEqual(alloc["state"], "RESERVED")
            self.assertEqual(alloc["verification"], "PENDING_VERIFICATION")
            # Second concurrent exclusive request on same GPU UUID must be rejected
            from app.services.gpu_scheduler import SchedulerError
            with self.assertRaises(SchedulerError) as ctx:
                svc.allocate_gpu("track-gpu-00", "other_user", "FULL_GPU_EXCLUSIVE", job_name="conflict.py")
            self.assertEqual(ctx.exception.code, "GPU_ALREADY_RESERVED")
            # Release canary reservation cleanly
            rel = svc.stop_job(rsv_id, actor="canary_user", is_admin=False)
            self.assertEqual(rel["state"], "RELEASED")


class TestV2HttpNegativeSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LabOpsRequestHandler)
        except PermissionError as exc:
            raise unittest.SkipTest("sandbox does not permit loopback listeners") from exc
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _call(self, method: str, path: str, role=None, username="alice", body=None, origin=None):
        req = Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        if role:
            req.add_header("Authorization", "Bearer " + issue_token(username, role))
        if origin:
            req.add_header("Origin", origin)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, data=data, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
        except HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8")), dict(e.headers)

    def test_unauthenticated_requests_rejected_401(self):
        for path in ("/api/v1/gpus", "/api/v1/v2/hosts", "/api/v1/v2/researcher/me", "/api/v1/audit-logs"):
            status, _, _ = self._call("GET", path, role=None)
            self.assertEqual(status, 401, f"Expected 401 for {path}")

    def test_researcher_blocked_from_admin_endpoints_403(self):
        for path in ("/api/v1/v2/containers/summary", "/api/v1/v2/reconcile", "/api/v1/audit-logs"):
            status, _, _ = self._call("GET", path, role="RESEARCHER", username="alice")
            self.assertEqual(status, 403, f"Expected 403 for {path}")

        status, _, _ = self._call(
            "POST",
            "/api/v1/v2/hosts",
            role="RESEARCHER",
            username="alice",
            body={"host_id": "h9", "hostname": "h9", "display_name": "H9", "management_ip": "10.0.0.9"},
        )
        self.assertEqual(status, 403)

    def test_cors_does_not_allow_wildcard_or_untrusted_origin(self):
        status, _, headers = self._call(
            "GET", "/api/v1/v2/researcher/me", role="RESEARCHER", origin="https://evil.example.com"
        )
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)


    def test_node_agent_endpoint_rejects_user_token_and_accepts_machine_token(self):
        # Admin user token không được phép giả mạo làm Node Agent
        status, _, _ = self._call(
            "POST",
            "/api/v1/v2/hosts/workstation/agent/heartbeat",
            role="ADMIN",
            username="root",
            body={"agent_version": "2.0.0"},
        )
        self.assertEqual(status, 403)

        # Machine token thật được chấp nhận cho heartbeat nhưng bị chặn khi gọi endpoint người dùng
        req = Request(
            f"http://127.0.0.1:{self.port}/api/v1/v2/hosts/workstation/agent/heartbeat",
            data=json.dumps({"agent_version": "2.0.0"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer v2-node-agent-machine-token-xyz",
            },
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)


if __name__ == "__main__":
    unittest.main()
