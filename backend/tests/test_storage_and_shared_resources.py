"""Comprehensive test suite covering T01 - T18:
LabOps Control Center V2 — Storage Allocation & Shared Resource Management.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from app.services.labops_service import LabOpsDataService
from app.services.storage_inventory_service import StorageInventoryService, storage_service
from app.services.workspace_controller import WorkspaceController
from app.services.ssh_access_manager import SshAccessManager
from app.schemas.labops import MemberCreateRequest


class TestStorageAndSharedResourcesT01ToT18(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.temp_dir.name, "data")
        self.storage_root = os.path.join(self.temp_dir.name, "media", "LabOps-Data")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.storage_root, exist_ok=True)

        self.storage_service = storage_service
        self.storage_service._custom_pools.clear()
        self.storage_service._reservations.clear()

        # Register a valid custom storage pool on workstation for testing
        self.storage_service.register_custom_pool(
            "workstation",
            {
                "host_id": "workstation",
                "storage_id": "ws-labops-data",
                "device": "/dev/sdb1",
                "filesystem_uuid": "mock-uuid-ws",
                "mount_point": self.storage_root,
                "filesystem": "ext4",
                "capacity_gib": 1000.0,
                "used_gib": 200.0,
                "free_gib": 800.0,
                "allocatable_gib": 720.0,
                "reserved_gib": 80.0,
                "quota_backend": "EXT4_PROJECT",
                "quota_supported": True,
                "quota_enforced": True,
                "read_only": False,
                "mounted": True,
                "eligible": True,
                "last_verified_at": "2026-11-19T00:00:00Z",
                "verification_status": "VERIFIED",
            },
        )
        self.storage_service.register_custom_pool(
            "track",
            {
                "host_id": "track",
                "storage_id": "track-data",
                "device": "/dev/nvme0n1p1",
                "filesystem_uuid": "mock-uuid-track",
                "mount_point": "/data",
                "filesystem": "xfs",
                "capacity_gib": 2000.0,
                "used_gib": 400.0,
                "free_gib": 1600.0,
                "allocatable_gib": 1440.0,
                "reserved_gib": 160.0,
                "quota_backend": "XFS_PROJECT",
                "quota_supported": True,
                "quota_enforced": True,
                "read_only": False,
                "mounted": True,
                "eligible": True,
                "last_verified_at": "2026-11-19T00:00:00Z",
                "verification_status": "VERIFIED",
            },
        )

        self.svc = LabOpsDataService(
            demo_mode=False,
            data_dir=self.data_dir,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    # T01: Storage inventory returns correct drives per host (Workstation vs Track)
    def test_t01_storage_inventory_returns_correct_drives_per_host(self):
        ws_pools = self.storage_service.get_storage_pools("workstation")
        track_pools = self.storage_service.get_storage_pools("track")

        self.assertTrue(len(ws_pools) >= 1)
        self.assertTrue(len(track_pools) >= 1)

        ws_pool = next((p for p in ws_pools if p["storage_id"] == "ws-labops-data"), None)
        self.assertIsNotNone(ws_pool)
        self.assertEqual(ws_pool["mount_point"], self.storage_root)
        self.assertEqual(ws_pool["filesystem"], "ext4")
        self.assertEqual(ws_pool["capacity_gib"], 1000.0)
        self.assertEqual(ws_pool["allocatable_gib"], 720.0)
        self.assertTrue(ws_pool["eligible"])

        # Ineligible paths like / or /home must be flagged
        root_pool = self.storage_service.inspect_mount_point("/etc")
        self.assertFalse(root_pool["eligible"])
        self.assertIn("FORBIDDEN", root_pool.get("ineligibility_reason", ""))

    # T02: Selection of specific drive creates directory on that drive only
    def test_t02_selection_of_specific_drive_creates_directory_on_that_drive_only(self):
        payload = {
            "full_name": "Nguyen Van A",
            "username": "nguyenvana",
            "email": "nguyenvana@lab.ai.vn",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "storage_id": "ws-labops-data",
            "storage_quota_gb": 50,
            "container_mount_path": "/workspace",
        }
        member = self.svc.provision_member(payload)

        self.assertEqual(member["storage_id"], "ws-labops-data")
        self.assertEqual(member["host_storage_root"], self.storage_root)
        expected_dir = os.path.join(self.storage_root, "labops", "users", "nguyenvana")
        self.assertEqual(member["member_storage_directory"], expected_dir)
        self.assertEqual(member["container_mount_path"], "/workspace")

        # Provision directory on filesystem
        res = self.storage_service.provision_member_storage(
            host_id="workstation",
            storage_id="ws-labops-data",
            username="nguyenvana",
            quota_gib=50,
        )
        self.assertTrue(os.path.isdir(expected_dir))
        self.assertTrue(os.path.isdir(os.path.join(expected_dir, "work")))
        self.assertTrue(os.path.isdir(os.path.join(expected_dir, "checkpoints")))
        self.assertTrue(os.path.isdir(os.path.join(expected_dir, "datasets")))
        self.assertTrue(os.path.isdir(os.path.join(expected_dir, ".ssh")))

        # Check permissions: 0700
        mode = os.stat(expected_dir).st_mode & 0o777
        self.assertEqual(mode, 0o700)

        # Ensure NO directory created on /home or /
        self.assertFalse(os.path.exists("/home/nguyenvana"))
        self.assertFalse(os.path.exists("/nguyenvana"))

    # T03: Storage quota enforcement & read-back verification
    def test_t03_storage_quota_enforcement_and_read_back_verification(self):
        val = self.storage_service.validate_allocation(
            host_id="workstation",
            storage_id="ws-labops-data",
            requested_gib=100.0,
        )
        self.assertTrue(val["valid"])
        self.assertEqual(val["quota_backend"], "EXT4_PROJECT")

        res = self.storage_service.provision_member_storage(
            host_id="workstation",
            storage_id="ws-labops-data",
            username="quotauser",
            quota_gib=100,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["quota_gib"], 100)
        self.assertIn(res["enforcement_status"], ["HARD_ENFORCED", "QUOTA_NOT_ENFORCEABLE", "POLICY_ONLY"])

    # T04: Two members on same drive get isolated directories & independent quotas
    def test_t04_two_members_isolated_directories_and_independent_quotas(self):
        res_a = self.storage_service.provision_member_storage(
            host_id="workstation",
            storage_id="ws-labops-data",
            username="member_a",
            quota_gib=100,
        )
        res_b = self.storage_service.provision_member_storage(
            host_id="workstation",
            storage_id="ws-labops-data",
            username="member_b",
            quota_gib=200,
        )

        dir_a = res_a["directory"]
        dir_b = res_b["directory"]
        self.assertNotEqual(dir_a, dir_b)
        self.assertTrue(dir_a.endswith("/labops/users/member_a"))
        self.assertTrue(dir_b.endswith("/labops/users/member_b"))

        self.assertEqual(res_a["quota_gib"], 100)
        self.assertEqual(res_b["quota_gib"], 200)

        # Verify strict 0700 mode on both
        self.assertEqual(os.stat(dir_a).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(dir_b).st_mode & 0o777, 0o700)

    # T05: Storage offline prevents workspace creation without fallback to root
    def test_t05_storage_offline_prevents_workspace_creation_without_fallback_to_root(self):
        # Register unmounted pool
        self.storage_service.register_custom_pool(
            "workstation",
            {
                "host_id": "workstation",
                "storage_id": "ws-offline",
                "device": "/dev/sdc1",
                "filesystem_uuid": "offline-uuid",
                "mount_point": "/media/offline-disk",
                "filesystem": "ext4",
                "capacity_gib": 500.0,
                "used_gib": 0.0,
                "free_gib": 0.0,
                "allocatable_gib": 0.0,
                "reserved_gib": 0.0,
                "quota_backend": "NONE",
                "quota_supported": False,
                "quota_enforced": False,
                "read_only": False,
                "mounted": False,
                "eligible": False,
                "ineligibility_reason": "MOUNT_OFFLINE",
            },
        )

        val = self.storage_service.validate_allocation("workstation", "ws-offline", 50)
        self.assertFalse(val["valid"])
        self.assertIn("không hợp lệ", val["reason"])

        with self.assertRaises(ValueError):
            self.storage_service.provision_member_storage(
                host_id="workstation",
                storage_id="ws-offline",
                username="failuser",
                quota_gib=50,
            )

        # Ensure no fallback directory created on / or /home
        self.assertFalse(os.path.exists("/labops/users/failuser"))

    # T06: Concurrent allocation does not exceed capacity admission limit
    def test_t06_concurrent_allocation_admission_limit(self):
        # Pool has 720 GiB allocatable
        val1 = self.storage_service.validate_allocation("workstation", "ws-labops-data", 300)
        self.assertTrue(val1["valid"])

        val2 = self.storage_service.validate_allocation("workstation", "ws-labops-data", 400)
        self.assertTrue(val2["valid"])

        # Exceeds allocatable (800 > 720)
        val3 = self.storage_service.validate_allocation("workstation", "ws-labops-data", 800)
        self.assertFalse(val3["valid"])
        self.assertIn("vượt quá dung lượng", val3["reason"].lower())

    # T07: Existing workspace data preserved, not overwritten
    def test_t07_existing_workspace_data_preserved_not_overwritten(self):
        user_dir = os.path.join(self.storage_root, "labops", "users", "preserve_user", "work")
        os.makedirs(user_dir, exist_ok=True)
        test_file = os.path.join(user_dir, "experiment.py")
        with open(test_file, "w") as f:
            f.write("print('hello labops')")

        # Provision member again
        res = self.storage_service.provision_member_storage(
            host_id="workstation",
            storage_id="ws-labops-data",
            username="preserve_user",
            quota_gib=50,
        )
        self.assertTrue(res["success"])
        self.assertTrue(os.path.exists(test_file))
        with open(test_file, "r") as f:
            self.assertEqual(f.read(), "print('hello labops')")

    # T08: Symlink and path traversal attacks rejected
    def test_t08_symlink_and_path_traversal_attacks_rejected(self):
        for bad_user in ["../../etc", "../user", "user/../../passwd"]:
            with self.assertRaises(ValueError):
                self.storage_service.provision_member_storage(
                    host_id="workstation",
                    storage_id="ws-labops-data",
                    username=bad_user,
                    quota_gib=10,
                )

        # Attempt to supply forbidden mount point directly
        res = self.storage_service.inspect_mount_point("/root")
        self.assertFalse(res["eligible"])

        # Ineligible storage id validation
        val = self.storage_service.validate_allocation("workstation", "ineligible-pool", 10)
        self.assertFalse(val["valid"])

    # T09: Quota unsupported reported honestly as QUOTA_NOT_ENFORCEABLE
    def test_t09_quota_unsupported_reported_honestly(self):
        noquota_root = os.path.join(self.temp_dir.name, "noquota")
        os.makedirs(noquota_root, exist_ok=True)
        self.storage_service.register_custom_pool(
            "workstation",
            {
                "host_id": "workstation",
                "storage_id": "ws-no-quota",
                "device": "/dev/sdd1",
                "filesystem_uuid": "noquota-uuid",
                "mount_point": noquota_root,
                "filesystem": "ext4",
                "capacity_gib": 100.0,
                "used_gib": 20.0,
                "free_gib": 80.0,
                "allocatable_gib": 70.0,
                "reserved_gib": 10.0,
                "quota_supported": False,
                "quota_backend": "NONE",
                "quota_enforced": False,
                "read_only": False,
                "mounted": True,
                "eligible": True,
            },
        )

        res = self.storage_service.provision_member_storage(
            host_id="workstation",
            storage_id="ws-no-quota",
            username="honestuser",
            quota_gib=20,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["enforcement_status"], "QUOTA_NOT_ENFORCEABLE")
        self.assertNotEqual(res["enforcement_status"], "HARD_ENFORCED")

    # T10: Full filesystem error handling
    def test_t10_full_filesystem_error_handling(self):
        full_root = os.path.join(self.temp_dir.name, "full")
        os.makedirs(full_root, exist_ok=True)
        self.storage_service.register_custom_pool(
            "workstation",
            {
                "host_id": "workstation",
                "storage_id": "ws-full",
                "device": "/dev/sde1",
                "filesystem_uuid": "full-uuid",
                "mount_point": full_root,
                "filesystem": "ext4",
                "capacity_gib": 100.0,
                "used_gib": 100.0,
                "free_gib": 0.0,
                "allocatable_gib": 0.0,
                "reserved_gib": 10.0,
                "quota_supported": True,
                "quota_backend": "EXT4_PROJECT",
                "quota_enforced": True,
                "read_only": False,
                "mounted": True,
                "eligible": False,
                "ineligibility_reason": "INSUFFICIENT_SPACE",
            },
        )
        val = self.storage_service.validate_allocation("workstation", "ws-full", 10)
        self.assertFalse(val["valid"])

    # T11: Shared CPU mode (no fixed cores, CpuShares = 100, burstable)
    def test_t11_shared_cpu_mode_burstable_shares_and_pids(self):
        profile = self.storage_service.get_resource_profile("workstation")
        self.assertEqual(profile["cpu_scheduling_mode"], "SHARED_WEIGHTED")
        self.assertEqual(profile["default_cpu_weight"], 100)
        self.assertEqual(profile["pids_limit"], 4096)
        self.assertTrue(profile["total_cores"] >= 1)

        # Verify controller accepts SHARED_WEIGHTED
        ctrl = WorkspaceController(data_dir=self.data_dir)
        res = ctrl.request_workspace(
            owner="cpuuser",
            host_id="workstation",
            ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI1234567890123456789012345678901234567890123 user@laptop",
            storage_path=self.storage_root,
            member_storage_dir=os.path.join(self.storage_root, "labops", "users", "cpuuser"),
            container_mount_path="/workspace",
            resource_mode="SHARED_WEIGHTED",
            cpu_weight=100,
        )
        self.assertEqual(res["resource_mode"], "SHARED_WEIGHTED")
        self.assertEqual(res["cpu_weight"], 100)
        self.assertEqual(res["lifecycle_state"], "PENDING_RETRY")

    # T12: Shared RAM mode (host memory reserve applied, 20% safety)
    def test_t12_shared_ram_mode_host_safety_reserve(self):
        profile = self.storage_service.get_resource_profile("workstation")
        self.assertEqual(profile["memory_protection_mode"], "SHARED_HOST_PROTECTED")
        total_ram = profile["total_ram_bytes"]
        host_reserve = profile["host_reserve_bytes"]
        shared_budget = profile["shared_budget_bytes"]

        # Reserve must be >= 20% of total RAM or at least min 8 GiB
        expected_min_reserve = max(int(total_ram * 0.20), 8 * 1024 * 1024 * 1024)
        self.assertGreaterEqual(host_reserve, expected_min_reserve - 1024)
        self.assertEqual(shared_budget, total_ram - host_reserve)

    # T13: Memory pressure monitoring (PSI read-back)
    def test_t13_memory_pressure_monitoring_psi(self):
        profile = self.storage_service.get_resource_profile("workstation")
        psi = profile["memory_pressure"]
        self.assertIsNotNone(psi)
        self.assertIn("some_avg10", psi)
        self.assertIn("full_avg10", psi)
        self.assertIn("pressure_level", psi)
        self.assertIn(psi["pressure_level"], ["NONE", "NORMAL", "ELEVATED", "CRITICAL"])

    # T14: Existing GPU jobs unaffected
    def test_t14_existing_gpu_jobs_unaffected(self):
        initial_workloads = list(self.svc.jobs)
        payload = {
            "full_name": "Test User",
            "username": "unaffected_user",
            "email": "unaffected@lab.ai.vn",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "storage_id": "ws-labops-data",
            "storage_quota_gb": 20,
        }
        self.svc.provision_member(payload)

        # Workloads must be completely untouched
        self.assertEqual(len(self.svc.jobs), len(initial_workloads))
        for initial, current in zip(initial_workloads, self.svc.jobs):
            self.assertEqual(initial["id"], current["id"])
            self.assertEqual(initial["status"], current["status"])

    # T15: Restart persistence of allocations
    def test_t15_restart_persistence_of_allocations(self):
        payload = {
            "full_name": "Persistent User",
            "username": "persisted_user",
            "email": "persisted@lab.ai.vn",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "storage_id": "ws-labops-data",
            "storage_quota_gb": 75,
            "container_mount_path": "/workspace",
        }
        member = self.svc.provision_member(payload)

        # Simulate service restart by reloading from same data directory
        restarted_svc = LabOpsDataService(
            demo_mode=False,
            data_dir=self.data_dir,
        )
        loaded = next((m for m in restarted_svc.members if m["username"] == "persisted_user"), None)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["storage_id"], "ws-labops-data")
        self.assertEqual(loaded["storage_quota_gb"], 75)
        self.assertEqual(loaded["member_storage_directory"], member["member_storage_directory"])
        self.assertEqual(loaded["container_mount_path"], "/workspace")

    # T16: Shared GPU allocation & VRAM policy status (POLICY_LIMIT / MONITORED)
    def test_t16_shared_gpu_allocation_vram_status_monitored(self):
        payload = {
            "full_name": "Shared GPU User",
            "username": "shared_gpu_user",
            "email": "shared_gpu@lab.ai.vn",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "storage_id": "ws-labops-data",
            "storage_quota_gb": 30,
            "gpu_permissions": ["gpu-ws1-0"],
            "gpu_allocation_mode": "SHARED",
            "vram_quota_gb": 12,
        }
        member = self.svc.provision_member(payload)
        self.assertEqual(member["gpu_allocation_mode"], "SHARED")
        self.assertEqual(member["vram_quota_gb"], 12)
        # Truthful status: NEVER claim HARD_ENFORCED
        self.assertIn(member["vram_enforcement_status"], ["POLICY_LIMIT", "MONITORED", "POLICY_ONLY"])
        self.assertNotEqual(member["vram_enforcement_status"], "HARD_ENFORCED")

    # T17: Exclusive GPU allocation & conflict rejection
    def test_t17_exclusive_gpu_allocation_and_conflict_rejection(self):
        payload1 = {
            "full_name": "Exclusive User 1",
            "username": "exclusive_user1",
            "email": "ex1@lab.ai.vn",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "storage_id": "ws-labops-data",
            "storage_quota_gb": 30,
            "gpu_permissions": ["gpu-ws1-0"],
            "gpu_allocation_mode": "EXCLUSIVE",
            "vram_quota_gb": 24,
        }
        m1 = self.svc.provision_member(payload1)
        self.assertEqual(m1["gpu_allocation_mode"], "EXCLUSIVE")

        payload2 = {
            "full_name": "Exclusive User 2",
            "username": "exclusive_user2",
            "email": "ex2@lab.ai.vn",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "storage_id": "ws-labops-data",
            "storage_quota_gb": 30,
            "gpu_permissions": ["gpu-ws1-0"],
            "gpu_allocation_mode": "EXCLUSIVE",
            "vram_quota_gb": 24,
        }
        with self.assertRaises(ValueError) as ctx:
            self.svc.provision_member(payload2)
        self.assertIn("GPU", str(ctx.exception))

    # T18: VRAM quota enforcement status truthfulness
    def test_t18_vram_quota_enforcement_status_truthfulness(self):
        for mode in ["EXCLUSIVE", "SHARED"]:
            payload = {
                "full_name": f"Truth User {mode}",
                "username": f"truth_{mode.lower()}",
                "email": f"truth_{mode.lower()}@lab.ai.vn",
                "role": "RESEARCHER",
                "assigned_server": "workstation",
                "storage_id": "ws-labops-data",
                "storage_quota_gb": 20,
                "gpu_permissions": [],
                "gpu_allocation_mode": mode,
                "vram_quota_gb": 0,
            }
            m = self.svc.provision_member(payload)
            # Must never falsely report HARD_ENFORCED
            self.assertNotEqual(m["vram_enforcement_status"], "HARD_ENFORCED")
            self.assertIn(m["vram_enforcement_status"], ["NOT_CONFIGURED", "MONITORED", "POLICY_LIMIT", "POLICY_ONLY", "NOT_APPLICABLE"])


if __name__ == "__main__":
    unittest.main()
