import unittest
from unittest.mock import Mock, patch

from app.adapters.teleport_adapter import TeleportAdapter, TeleportError


class TestTeleportProvisioning(unittest.TestCase):
    def test_researcher_and_admin_roles_are_workspace_bounded_and_short_lived(self):
        researcher = TeleportAdapter.role_resource("alice", "ws-alice", "RESEARCHER")
        admin = TeleportAdapter.role_resource("bob", "ws-bob", "ADMIN")

        self.assertEqual(researcher["version"], "v8")
        self.assertEqual(researcher["spec"]["allow"]["node_labels"], {"labops-workspace": ["ws-alice"]})
        self.assertEqual(researcher["spec"]["allow"]["logins"], ["alice"])
        self.assertEqual(researcher["spec"]["options"]["max_session_ttl"], "12h")
        self.assertFalse(researcher["spec"]["options"]["forward_agent"])
        self.assertEqual(admin["spec"]["allow"]["node_labels"], {"labops-workspace": ["*"]})
        self.assertEqual(admin["spec"]["options"]["max_session_ttl"], "2h")
        self.assertTrue(admin["spec"]["options"]["require_session_mfa"])

    def test_invalid_identity_is_rejected(self):
        with self.assertRaises(TeleportError):
            TeleportAdapter.role_resource("root", "host-root", "RESEARCHER")

    def test_missing_tctl_reports_not_configured(self):
        adapter = TeleportAdapter(tctl_path="/not-installed/tctl")
        with patch("app.adapters.teleport_adapter.shutil.which", return_value=None):
            self.assertEqual(adapter.health()["status"], "NOT_CONFIGURED")

    def test_health_requires_pinned_community_version(self):
        adapter = TeleportAdapter()
        with patch("app.adapters.teleport_adapter.shutil.which", return_value="/usr/bin/tctl"), patch.object(
            adapter, "_run", return_value=Mock(stdout="Teleport v18.11.8")
        ):
            self.assertEqual(adapter.health()["status"], "AVAILABLE")

    def test_invitation_requires_private_cluster_and_delivery_configuration(self):
        adapter = TeleportAdapter(tctl_path="/not-installed/tctl")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(TeleportError) as error:
                adapter.provision_user("alice", "alice@example.org", "ws-alice", "RESEARCHER")
        self.assertNotIn("invite", str(error.exception).lower())


if __name__ == "__main__":
    unittest.main()
