import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.workspace_controller import WorkspaceController, WorkspaceSecurityError


VALID_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeBase64MaterialForTestingOnly user@test"


class WorkspaceControllerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="labops-workspace-test-")
        self.addCleanup(self.tmp.cleanup)
        self.controller = WorkspaceController(self.tmp.name)

    def create(self, owner, key="shared-key"):
        return self.controller.request_workspace(
            owner=owner, host_id="test-node", ssh_public_key=VALID_KEY,
            idempotency_key=key,
        )

    def test_idempotency_key_cannot_return_another_owners_workspace(self):
        alice = self.create("alice")
        with self.assertRaises(WorkspaceSecurityError) as raised:
            self.create("bob")
        self.assertEqual(raised.exception.code, "IDEMPOTENCY_KEY_REUSED")
        self.assertEqual(self.controller.list_workspaces(owner="alice")[0]["id"], alice["id"])
        self.assertEqual(self.controller.list_workspaces(owner="bob"), [])

    def test_state_write_syncs_file_and_directory(self):
        with patch("app.services.workspace_controller.os.fsync", wraps=__import__("os").fsync) as sync:
            self.create("alice", key="durable-key")
        self.assertGreaterEqual(sync.call_count, 2)
        state = Path(self.controller.state_path)
        self.assertTrue(state.is_file())
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_workspace_is_not_reported_active_before_runtime_verification(self):
        workspace = self.create("alice", key="pending-state")
        self.assertEqual(workspace["lifecycle_state"], "PENDING_RETRY")
        self.assertEqual(workspace["observed_state"], "NOT_VERIFIED")
        self.assertNotIn("vpn_bind_ip", workspace)

    def test_legacy_ssh_endpoint_is_not_reissued_as_a_verified_workspace_host(self):
        records = self.controller.list_workspaces(owner="alice", legacy_endpoints=[{
            "id": "old-endpoint", "username": "alice", "server_id": "track",
            "listen_port": 22400, "bind_address": "192.0.2.10",
            "verification_status": "NOT_VERIFIED",
        }])
        legacy = next(item for item in records if item.get("legacy_preserved"))
        self.assertNotIn("vpn_bind_ip", legacy)
        self.assertNotIn("ssh_host", legacy)
        self.assertNotEqual(legacy.get("ssh_readiness"), "VERIFIED")


if __name__ == "__main__":
    unittest.main()
