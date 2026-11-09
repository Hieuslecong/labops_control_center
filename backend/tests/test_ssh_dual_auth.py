import asyncio
import unittest

from app.adapters.incus_adapter import IncusAdapter


class TestLegacySshAdapterFailsClosed(unittest.TestCase):
    def setUp(self):
        self.adapter = IncusAdapter("https://127.0.0.1:8443")

    def test_per_user_ssh_proxy_is_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            asyncio.run(self.adapter.add_proxy_device("ws-alice", "ssh", "0.0.0.0", 22001))

    def test_legacy_password_ssh_configuration_is_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "Teleport"):
            asyncio.run(self.adapter.configure_sshd_dual_auth("ws-alice", "alice", password_hash="unused"))

    def test_privilege_and_ssh_policy_are_not_reported_verified_without_remote_probes(self):
        with self.assertRaisesRegex(RuntimeError, "not been checked"):
            asyncio.run(self.adapter.verify_privilege_isolation("ws-alice", "alice"))
        with self.assertRaisesRegex(RuntimeError, "live Teleport"):
            asyncio.run(self.adapter.verify_dual_auth_policy("ws-alice", "alice"))


if __name__ == "__main__":
    unittest.main()
