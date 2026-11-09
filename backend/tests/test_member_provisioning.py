import tempfile
import unittest
import json
import os
from unittest.mock import PropertyMock, patch

from pydantic import ValidationError

from app.schemas.labops import MemberCreateRequest
from app.services.labops_service import LabOpsDataService, validate_linux_username, validate_mount_path


class TestMemberProvisioningSecurity(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.TemporaryDirectory()
        self.svc = LabOpsDataService(demo_mode=True, data_dir=self.data_dir.name)

    def tearDown(self):
        self.data_dir.cleanup()

    def payload(self, **overrides):
        data = {
            "full_name": "Test Researcher",
            "username": "secure_tester",
            "email": "researcher@example.org",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "workspace_name": "ws-secure_tester",
            "storage_quota_gb": 10,
            "mount_path": "/workspace",
            "cpu_limit_cores": 4,
            "ram_limit_gb": 16,
            "gpu_permissions": [],
            "vram_quota_gb": 0,
        }
        data.update(overrides)
        return data

    def test_safe_username_validation(self):
        for name in ["longnh", "nam_nv", "user-01", "researcher_a"]:
            validate_linux_username(name)
        for name in ["root", "admin", "1user", "ab", "bad user", "../user"]:
            with self.assertRaises(ValueError):
                validate_linux_username(name)

    def test_mount_path_allowlist(self):
        for path in ["/workspace", "/home/testuser", "/data"]:
            validate_mount_path(path, "testuser")
        for path in ["/", "/etc", "/root", "/home/otheruser", "/etc/passwd"]:
            with self.assertRaises(ValueError):
                validate_mount_path(path, "testuser")

    def test_new_user_is_only_a_pending_request(self):
        allocation_count = len(self.svc.storage_allocations)
        endpoint_count = len(self.svc.ssh_endpoints)
        member = self.svc.provision_member(self.payload())

        self.assertEqual(member["status"], "PENDING_PROVISIONING")
        self.assertEqual(member["workspace_status"], "NOT_VERIFIED")
        self.assertEqual(member["teleport_status"], "NOT_CONFIGURED")
        self.assertEqual(member["password_status"], "NOT_APPLICABLE")
        self.assertIsNone(member["ssh_port"])
        self.assertEqual(member["wireguard"]["status"], "NOT_CONFIGURED")
        self.assertEqual(member["privilege_validation"]["status"], "NOT_VERIFIED")
        self.assertEqual(member["cpu_limit_cores"], 4)
        self.assertEqual(member["ram_limit_gb"], 16)
        self.assertNotIn("initial_password", member)
        self.assertNotIn("private_key", member["wireguard"])
        self.assertNotIn("config_content", member["wireguard"])
        self.assertEqual(len(self.svc.storage_allocations), allocation_count)
        self.assertEqual(len(self.svc.ssh_endpoints), endpoint_count)

    def test_production_without_persisted_members_does_not_load_demo_users(self):
        production = LabOpsDataService(demo_mode=False, data_dir=self.data_dir.name)
        self.assertEqual(production.members, [])
        self.assertEqual(production.ssh_endpoints, [])

    def test_production_records_pending_request_without_verified_storage_pool(self):
        production = LabOpsDataService(demo_mode=False, data_dir=self.data_dir.name)
        with patch.object(LabOpsDataService, "storage_pools", new_callable=PropertyMock, return_value=[]):
            member = production.provision_member(self.payload())
        self.assertEqual(member["status"], "PENDING_PROVISIONING")
        self.assertIsNone(member["storage_pool_id"])
        self.assertEqual(member["storage_pool_label"], "NOT_VERIFIED")

    def test_legacy_active_records_are_not_reported_as_verified_access(self):
        legacy = [{
            "id": "legacy-1",
            "username": "legacy_user",
            "status": "ACTIVE",
            "ssh_status": "ONLINE",
            "ssh_sessions_count": 3,
            "wireguard": {
                "status": "ACTIVE",
                "public_key": "legacy-public-key",
                "server_endpoint": "example.invalid:51820",
                "config_content": "[Interface]\\nPrivateKey = redacted",
            },
        }]
        with open(os.path.join(self.data_dir.name, "members.json"), "w", encoding="utf-8") as stream:
            json.dump(legacy, stream)

        production = LabOpsDataService(demo_mode=False, data_dir=self.data_dir.name)
        member = production.members[0]
        self.assertEqual(member["status"], "PENDING_PROVISIONING")
        self.assertEqual(member["provisioning_state"], "LEGACY_ACCESS_RETAINED_UNVERIFIED")
        self.assertEqual(member["workspace_status"], "NOT_VERIFIED")
        self.assertEqual(member["teleport_status"], "NOT_CONFIGURED")
        self.assertEqual(member["ssh_status"], "NOT_VERIFIED")
        self.assertEqual(member["ssh_sessions_count"], 0)
        self.assertEqual(member["wireguard"]["status"], "NOT_VERIFIED")
        self.assertNotIn("config_content", member["wireguard"])
        self.assertNotIn("server_endpoint", member["wireguard"])

    def test_request_validation_rejects_legacy_credentials_and_out_of_range_resources(self):
        with self.assertRaises(ValidationError):
            MemberCreateRequest.model_validate({**self.payload(), "initialPassword": "long-secret"})
        with self.assertRaises(ValidationError):
            MemberCreateRequest.model_validate({**self.payload(), "cpuLimitCores": 0})

    def test_duplicate_username_and_workspace_are_rejected(self):
        self.svc.provision_member(self.payload())
        with self.assertRaisesRegex(ValueError, "đã tồn tại"):
            self.svc.provision_member(self.payload(workspace_name="ws-another"))
        with self.assertRaisesRegex(ValueError, "đã tồn tại"):
            self.svc.provision_member(self.payload(username="another_user"))

    def test_storage_request_cannot_exceed_available_pool(self):
        pool = next(pool for pool in self.svc.storage_pools if pool["server_id"] == "workstation")
        with self.assertRaisesRegex(ValueError, "Dung lượng"):
            self.svc.provision_member(self.payload(storage_pool_id=pool["id"], storage_quota_gb=int(pool["free_gb"]) + 1))

    def test_legacy_ssh_and_password_operations_fail_closed(self):
        for operation in [
            lambda: self.svc.rotate_ssh_key("mem-1", "ssh-ed25519 invalid"),
            lambda: self.svc.revoke_ssh_key("mem-1"),
            lambda: self.svc.update_ssh_port("mem-1", 22002),
            lambda: self.svc.reset_member_password("mem-1"),
            lambda: self.svc.verify_member_dual_auth("mem-1"),
        ]:
            with self.assertRaises(RuntimeError):
                operation()

    def test_restore_and_delete_do_not_activate_or_destroy_workspace_data(self):
        member = self.svc.provision_member(self.payload())
        restored = self.svc.restore_member(member["id"])
        self.assertEqual(restored["status"], "PENDING_PROVISIONING")
        with self.assertRaises(RuntimeError):
            self.svc.delete_member_permanently(member["id"])
        self.assertIn(member, self.svc.members)


if __name__ == "__main__":
    unittest.main()
