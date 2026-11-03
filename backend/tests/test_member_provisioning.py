import unittest
import sys
import os

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.labops_service import (
    LabOpsDataService,
    validate_linux_username,
    validate_mount_path
)

class TestMemberProvisioningSecurity(unittest.TestCase):
    def setUp(self):
        self.svc = LabOpsDataService()

    def test_safe_username_validation_valid(self):
        valid_names = ["longnh", "nam_nv", "user-01", "researcher_a", "phamdinh2"]
        for name in valid_names:
            try:
                validate_linux_username(name)
            except ValueError:
                self.fail(f"Valid username '{name}' was unexpectedly rejected")

    def test_safe_username_validation_reject_metacharacters(self):
        invalid_names = [
            "user;rm -rf /",
            "user$(id)",
            "user`whoami`",
            "user&&cat",
            "user|ls",
            "user name",
            "user/name",
            "../user",
            "A" * 35,  # too long
            "a",        # too short (< 3)
            "1user"     # starts with digit
        ]
        for name in invalid_names:
            with self.assertRaises(ValueError, msg=f"Username '{name}' should have been rejected"):
                validate_linux_username(name)

    def test_safe_username_validation_reject_reserved_names(self):
        reserved_names = ["root", "bin", "sudo", "wheel", "admin", "nobody", "docker", "incus"]
        for name in reserved_names:
            with self.assertRaises(ValueError, msg=f"Reserved username '{name}' should have been rejected"):
                validate_linux_username(name)

    def test_mount_path_allowlist(self):
        # Approved paths
        validate_mount_path("/workspace", "testuser")
        validate_mount_path("/home/testuser", "testuser")
        validate_mount_path("/data", "testuser")

        # Disallowed / dangerous arbitrary host paths
        dangerous_paths = ["/", "/etc", "/root", "/var", "/dev", "/usr", "/home/otheruser", "/etc/passwd"]
        for path in dangerous_paths:
            with self.assertRaises(ValueError, msg=f"Mount path '{path}' should have been rejected"):
                validate_mount_path(path, "testuser")

    def test_atomic_member_provisioning_success(self):
        payload = {
            "full_name": "Trương Gia Bình",
            "username": "binhtg",
            "email": "binhtg@labops.local",
            "role": "RESEARCHER",
            "assigned_server": "track",
            "workspace_name": "ws-binhtg",
            "storage_pool_id": "pool-nvme-fast",
            "mount_path": "/workspace",
            "storage_quota_gb": 350,
            "backup_policy": "DAILY_SNAPSHOT",
            "cpu_limit_cores": 16,
            "ram_limit_gb": 64,
            "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI1234567890abcdef binhtg@laptop"
        }
        res = self.svc.provision_member(payload)

        # Invariants check
        self.assertEqual(res["username"], "binhtg")
        self.assertEqual(res["sudo_privilege"], "DENIED")
        self.assertEqual(res["security_isolation_level"], "UNPRIVILEGED_CGROUPS_ISOLATED")
        self.assertTrue(res["privilege_validation"]["sudo_test_failed_as_expected"])
        self.assertTrue(res["privilege_validation"]["unprivileged_container"])
        self.assertEqual(res["status"], "ACTIVE")
        self.assertEqual(res["ssh_key_status"], "ACTIVE")
        self.assertTrue(res["ssh_fingerprint"].startswith("SHA256:"))

        # Check port allocation in range 22000-29999
        self.assertGreaterEqual(res["ssh_port"], 22001)
        self.assertLessEqual(res["ssh_port"], 29999)

        # Check SSH endpoint created
        endpoint = next((ep for ep in self.svc.ssh_endpoints if ep["member_id"] == res["id"]), None)
        self.assertIsNotNone(endpoint)
        self.assertEqual(endpoint["listen_port"], res["ssh_port"])
        self.assertEqual(endpoint["status"], "ACTIVE")

        # Check Storage Allocation created
        allocation = next((sa for sa in self.svc.storage_allocations if sa["member_id"] == res["id"]), None)
        self.assertIsNotNone(allocation)
        self.assertEqual(allocation["volume_name"], "data-binhtg")
        self.assertEqual(allocation["quota_gb"], 350)

    def test_provision_pending_key_enrollment(self):
        # Without public key, member should be created in PENDING_KEY_ENROLLMENT status with disabled port
        payload = {
            "full_name": "Sinh Viên Thực Tập",
            "username": "intern_sv",
            "email": "intern@labops.local",
            "role": "STUDENT",
            "assigned_server": "workstation",
            "storage_quota_gb": 50,
            "ssh_public_key": None
        }
        res = self.svc.provision_member(payload)
        self.assertEqual(res["status"], "PENDING_KEY_ENROLLMENT")
        self.assertEqual(res["ssh_key_status"], "PENDING_KEY_ENROLLMENT")
        
        endpoint = next(ep for ep in self.svc.ssh_endpoints if ep["member_id"] == res["id"])
        self.assertEqual(endpoint["status"], "DISABLED")

        # Now enroll key (rotate key)
        rotated = self.svc.rotate_ssh_key(res["id"], "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI_new_key intern@device")
        self.assertEqual(rotated["status"], "ACTIVE")
        self.assertEqual(rotated["ssh_key_status"], "ACTIVE")
        self.assertEqual(endpoint["status"], "ACTIVE")

    def test_storage_quota_exceeds_pool_free_space(self):
        pool = self.svc.storage_pools[0]
        free_space = pool["free_gb"]

        payload = {
            "full_name": "Tham Lam",
            "username": "greedy_mem",
            "email": "greedy@labops.local",
            "role": "RESEARCHER",
            "assigned_server": pool["server_id"],
            "storage_pool_id": pool["id"],
            "storage_quota_gb": int(free_space + 5000)
        }
        with self.assertRaises(ValueError) as ctx:
            self.svc.provision_member(payload)
        self.assertIn("vượt quá dung lượng trống", str(ctx.exception))

    def test_ssh_port_collision_detection(self):
        # Port 22001 is already reserved on track for mem-1
        payload = {
            "full_name": "Người Dùng Trùng Cổng",
            "username": "collision_usr",
            "email": "coll@labops.local",
            "role": "RESEARCHER",
            "assigned_server": "track",
            "ssh_port": 22001
        }
        with self.assertRaises(ValueError) as ctx:
            self.svc.provision_member(payload)
        self.assertIn("đã được sử dụng", str(ctx.exception))

    def test_member_suspension_and_restoration(self):
        # Suspend member mem-1
        suspended = self.svc.suspend_member("mem-1")
        self.assertEqual(suspended["status"], "SUSPENDED")
        self.assertEqual(suspended["ssh_status"], "OFFLINE")

        # Endpoint must be disabled
        ep = next(ep for ep in self.svc.ssh_endpoints if ep["member_id"] == "mem-1")
        self.assertEqual(ep["status"], "DISABLED")

        # Storage volume must remain intact
        sa = next(sa for sa in self.svc.storage_allocations if sa["member_id"] == "mem-1")
        self.assertIsNotNone(sa)

        # Restore member mem-1
        restored = self.svc.restore_member("mem-1")
        self.assertEqual(restored["status"], "ACTIVE")
        self.assertEqual(ep["status"], "ACTIVE")

    def test_revoke_ssh_key(self):
        revoked = self.svc.revoke_ssh_key("mem-2")
        self.assertEqual(revoked["ssh_key_status"], "REVOKED")
        self.assertEqual(revoked["ssh_status"], "OFFLINE")
        ep = next(ep for ep in self.svc.ssh_endpoints if ep["member_id"] == "mem-2")
        self.assertEqual(ep["status"], "DISABLED")

    def test_permanent_deletion_releases_resources(self):
        res = self.svc.delete_member_permanently("mem-4")
        self.assertTrue(res["success"])
        self.assertFalse(any(m["id"] == "mem-4" for m in self.svc.members))
        self.assertFalse(any(ep["member_id"] == "mem-4" for ep in self.svc.ssh_endpoints))
        self.assertFalse(any(sa["member_id"] == "mem-4" for sa in self.svc.storage_allocations))

if __name__ == "__main__":
    unittest.main()

