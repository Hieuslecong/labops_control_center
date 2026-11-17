import tempfile
import unittest

from app.services.labops_service import LabOpsDataService


class ClerkIdentityBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="labops-identity-binding-")
        self.addCleanup(self.tmp.cleanup)
        self.service = LabOpsDataService(demo_mode=True, data_dir=self.tmp.name)
        self.member = self.service.provision_member({
            "full_name": "Alice Researcher",
            "username": "alice",
            "email": "alice@example.test",
            "role": "RESEARCHER",
            "assigned_server": "track",
            "workspace_name": "alice-workspace",
            "storage_quota_gb": 1,
        })

    def test_admin_binding_attaches_stable_identity_and_audit(self):
        result = self.service.bind_member_clerk_identity(
            self.member["id"], "user_abc123", actor="admin-test",
        )

        self.assertEqual(result["status"], "BOUND")
        self.assertEqual(self.member["clerk_user_id"], "user_abc123")
        self.assertEqual(self.service.audit_logs[0]["action"], "MEMBER_IDENTITY_BOUND")
        self.assertEqual(self.service.audit_logs[0]["actor"], "admin-test")

    def test_identity_cannot_be_bound_to_two_members(self):
        self.service.bind_member_clerk_identity(self.member["id"], "user_abc123", actor="admin-test")
        other = self.service.provision_member({
            "full_name": "Bob Researcher",
            "username": "bob",
            "email": "bob@example.test",
            "role": "RESEARCHER",
            "assigned_server": "track",
            "workspace_name": "bob-workspace",
            "storage_quota_gb": 1,
        })

        with self.assertRaisesRegex(ValueError, "already bound"):
            self.service.bind_member_clerk_identity(other["id"], "user_abc123", actor="admin-test")
        self.assertNotIn("clerk_user_id", other)

    def test_identity_format_and_member_role_are_validated(self):
        with self.assertRaisesRegex(ValueError, "Clerk user ID"):
            self.service.bind_member_clerk_identity(self.member["id"], "alice", actor="admin-test")

        admin = self.service.provision_member({
            "full_name": "Local Admin",
            "username": "local_admin",
            "email": "local-admin@example.test",
            "role": "ADMIN",
            "assigned_server": "track",
            "workspace_name": "local-admin-workspace",
            "storage_quota_gb": 1,
        })
        with self.assertRaisesRegex(ValueError, "Only researcher"):
            self.service.bind_member_clerk_identity(admin["id"], "user_admin123", actor="admin-test")


if __name__ == "__main__":
    unittest.main()
