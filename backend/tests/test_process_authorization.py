import unittest
import os
import tempfile
from unittest.mock import patch

from app.core import security
from app.adapters.process_monitor_adapter import ProcessMonitorAdapter, sanitize_cmdline


class ProcessAuthorizationTests(unittest.TestCase):
    def test_process_history_uses_isolated_state_directory(self):
        with tempfile.TemporaryDirectory(prefix="labops-process-state-test-") as root, patch.dict(
            os.environ, {"LABOPS_STATE_DIR": root}
        ):
            adapter = ProcessMonitorAdapter(server_id="test-node", demo_mode=True)
        self.assertEqual(os.path.dirname(adapter.history_file), os.path.abspath(root))

    def test_command_line_sanitizer_redacts_secrets_with_common_flag_spellings(self):
        sanitized = sanitize_cmdline([
            "python", "train.py", "--api-key=flag-secret", "--client-secret", '"two word secret"',
            "AWS_SECRET_ACCESS_KEY=env-secret", "Authorization:", "Bearer", "header-secret",
        ])
        for secret in ("flag-secret", "two word secret", "env-secret", "header-secret"):
            self.assertNotIn(secret, sanitized)
        self.assertIn("--api-key=********", sanitized)
        self.assertIn("--client-secret ********", sanitized)
        self.assertIn("AWS_SECRET_ACCESS_KEY=********", sanitized)

    def test_researcher_filter_is_scoped_to_authenticated_username(self):
        researcher = security.Principal(username="alice", role="RESEARCHER")
        resolver = getattr(security, "resolve_owner_filter", lambda *_: None)
        self.assertEqual(resolver(researcher, None), "alice")

    def test_researcher_cannot_select_another_username(self):
        researcher = security.Principal(username="alice", role="RESEARCHER")
        resolver = getattr(security, "resolve_owner_filter", lambda *_: None)
        with self.assertRaises(security.AuthError) as raised:
            resolver(researcher, "bob")
        self.assertEqual(raised.exception.status, 403)

    def test_admin_can_apply_requested_process_filter(self):
        admin = security.Principal(username="admin", role="ADMIN")
        resolver = getattr(security, "resolve_owner_filter", lambda *_: None)
        self.assertEqual(resolver(admin, "bob"), "bob")
        self.assertIsNone(resolver(admin, None))


if __name__ == "__main__":
    unittest.main()
