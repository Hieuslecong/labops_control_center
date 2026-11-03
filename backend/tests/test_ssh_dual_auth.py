import unittest
import asyncio
from app.services.labops_service import LabOpsDataService
from app.adapters.incus_adapter import IncusAdapter

class TestSshDualMethodAuthentication(unittest.TestCase):
    def setUp(self):
        self.service = LabOpsDataService()
        self.adapter = IncusAdapter("https://10.200.0.1:8443")

    def test_sshd_configuration_template(self):
        """Test OpenSSH configuration template inside member container requires publickey,password"""
        res = asyncio.run(self.adapter.configure_sshd_dual_auth(
            instance_name="ws-testuser",
            username="testuser",
            public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey test@mac",
            password_hash="sha512$hash"
        ))
        self.assertTrue(res["success"])
        self.assertIn("AuthenticationMethods publickey,password", res["config_content"])
        self.assertIn("PubkeyAuthentication yes", res["config_content"])
        self.assertIn("PasswordAuthentication yes", res["config_content"])
        self.assertIn("PermitRootLogin no", res["config_content"])
        self.assertIn("PermitEmptyPasswords no", res["config_content"])
        self.assertIn("UsePAM yes", res["config_content"])

    def test_dual_auth_policy_verification_matrix(self):
        """Test all 8 scenarios in dual authentication verification matrix"""
        res = asyncio.run(self.adapter.verify_dual_auth_policy(
            instance_name="ws-testuser",
            username="testuser",
            host_ip="10.200.0.1"
        ))
        self.assertTrue(res["sshd_syntax_valid"])
        self.assertEqual(res["authentication_methods"], "publickey,password")
        self.assertTrue(res["pubkey_authentication"])
        self.assertTrue(res["password_authentication"])
        self.assertFalse(res["permit_root_login"])
        self.assertFalse(res["permit_empty_passwords"])
        self.assertTrue(res["use_pam"])
        
        matrix = res["test_matrix"]
        self.assertEqual(matrix["valid_key_and_valid_password"], "SUCCESS")
        self.assertEqual(matrix["password_only_no_key"], "REJECTED_PUBLICKEY_REQUIRED")
        self.assertEqual(matrix["key_only_no_password"], "REJECTED_PASSWORD_REQUIRED")
        self.assertEqual(matrix["valid_key_and_wrong_password"], "REJECTED_INVALID_PASSWORD")
        self.assertEqual(matrix["wrong_key_and_valid_password"], "REJECTED_INVALID_PUBLICKEY")
        self.assertEqual(matrix["revoked_key"], "REJECTED_KEY_REVOKED")
        self.assertEqual(matrix["root_login_attempt"], "REJECTED_ROOT_LOGIN_PROHIBITED")
        self.assertEqual(matrix["sudo_execution_attempt"], "REJECTED_USER_NOT_IN_SUDOERS")

    def test_member_provision_with_password_policy(self):
        """Test provisioning a member with valid 12+ character Linux SSH password"""
        payload = {
            "full_name": "Trần Văn Test",
            "username": "test_user_dual",
            "email": "testdual@lab.ai.vn",
            "assigned_server": "track",
            "workspace_name": "ws-testdual",
            "storage_pool_id": "pool-nvme-fast",
            "storage_quota_gb": 100,
            "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestDualKey",
            "initial_password": "LabOps-Secure-Pass-2026!",
            "require_password_change": True
        }
        member = self.service.provision_member(payload)
        self.assertEqual(member["username"], "test_user_dual")
        self.assertEqual(member["ssh_auth_mode"], "PUBLIC_KEY_AND_PASSWORD")
        self.assertEqual(member["password_status"], "CHANGE_REQUIRED")
        self.assertEqual(member["sudo_privilege"], "DENIED")

        # Verify policy record created
        policy = next((p for p in self.service.ssh_policies if p["linux_username"] == "test_user_dual"), None)
        self.assertIsNotNone(policy)
        self.assertEqual(policy["authentication_mode"], "PUBLIC_KEY_AND_PASSWORD")
        self.assertEqual(policy["password_status"], "CHANGE_REQUIRED")

        # Verify initial login audit event
        events = self.service.get_member_login_events(member["id"])
        self.assertTrue(len(events) >= 1)
        self.assertEqual(events[0]["authentication_result"], "SUCCESS")

    def test_member_provision_rejects_short_password(self):
        """Test provisioning rejects password with less than 12 characters"""
        payload = {
            "full_name": "Short Pass User",
            "username": "short_pass_user",
            "email": "shortpass@lab.ai.vn",
            "assigned_server": "track",
            "workspace_name": "ws-shortpass",
            "storage_pool_id": "pool-nvme-fast",
            "storage_quota_gb": 100,
            "initial_password": "short" # only 5 chars
        }
        with self.assertRaises(ValueError) as ctx:
            self.service.provision_member(payload)
        self.assertIn("12 ký tự", str(ctx.exception))

    def test_reset_ssh_password(self):
        """Test resetting member SSH password with compliance check"""
        res = self.service.reset_member_password("mem-2", new_password="New-Secure-Passphrase-2026!", require_change=True)
        self.assertTrue(res["success"])
        self.assertEqual(res["password_status"], "CHANGE_REQUIRED")
        self.assertEqual(res["temporary_password"], "New-Secure-Passphrase-2026!")

        # Verify member state updated
        member = next(m for m in self.service.members if m["id"] == "mem-2")
        self.assertEqual(member["password_status"], "CHANGE_REQUIRED")
        self.assertEqual(member["failed_login_attempts"], 0)

    def test_reset_ssh_password_rejects_short_password(self):
        """Test resetting password rejects short passwords"""
        with self.assertRaises(ValueError) as ctx:
            self.service.reset_member_password("mem-2", new_password="weak")
        self.assertIn("12 ký tự", str(ctx.exception))

    def test_suspend_and_restore_ssh_access(self):
        """Test suspending SSH access closes proxy and restoring re-enables it"""
        # Suspend
        m_suspended = self.service.suspend_ssh_access("mem-1")
        self.assertEqual(m_suspended["ssh_status"], "SUSPENDED")
        ep = next(e for e in self.service.ssh_endpoints if e["member_id"] == "mem-1")
        self.assertEqual(ep["status"], "DISABLED")

        # Restore
        m_restored = self.service.restore_ssh_access("mem-1")
        self.assertEqual(m_restored["ssh_status"], "OFFLINE")
        ep_restored = next(e for e in self.service.ssh_endpoints if e["member_id"] == "mem-1")
        self.assertEqual(ep_restored["status"], "ACTIVE")

    def test_dual_auth_verification_endpoint_service(self):
        """Test service verify_member_dual_auth returns complete status"""
        res = self.service.verify_member_dual_auth("mem-1")
        self.assertTrue(res["sshd_syntax_valid"])
        self.assertEqual(res["authentication_methods"], "publickey,password")
        self.assertEqual(res["status"], "DUAL_AUTH_ENFORCED_VERIFIED")

if __name__ == "__main__":
    unittest.main()
