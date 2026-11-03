import unittest
import sys
import os

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.labops_service import LabOpsDataService

class TestLabOpsDataService(unittest.TestCase):
    def setUp(self):
        self.svc = LabOpsDataService()

    def test_summary_calculation(self):
        summary = self.svc.get_summary()
        self.assertEqual(summary["servers"]["total"], 2)
        self.assertEqual(summary["servers"]["online"], 2)
        self.assertEqual(summary["gpus"]["totalCount"], 4)
        self.assertEqual(summary["gpus"]["aggregateVramGb"], 96)
        self.assertGreater(summary["members"]["totalRegistered"], 0)
        self.assertGreater(summary["jobs"]["running"], 0)

    def test_gpu_allocation_success(self):
        # Initial VRAM used on track-gpu-03 is 0
        gpu3 = next(g for g in self.svc.gpus if g["id"] == "track-gpu-03")
        self.assertEqual(gpu3["vram_used_mb"], 0)

        # Allocate 12 GB
        res = self.svc.allocate_gpu(
            gpu_id="track-gpu-03",
            username="testresearcher",
            mode="EXCLUSIVE",
            requested_vram_gb=12,
            job_name="test_experiment.py"
        )
        self.assertTrue(res["success"])
        self.assertIn("job_id", res)

        # Verify GPU VRAM updated
        self.assertEqual(gpu3["vram_used_mb"], 12 * 1024)
        self.assertIn("testresearcher", gpu3["active_users"])

        # Verify job was created
        created_job = next(j for j in self.svc.jobs if j["id"] == res["job_id"])
        self.assertEqual(created_job["username"], "testresearcher")
        self.assertEqual(created_job["vram_used_mb"], 12 * 1024)

    def test_gpu_allocation_insufficient_vram(self):
        # Attempting to allocate 30GB on 24GB GPU must raise ValueError
        with self.assertRaises(ValueError) as ctx:
            self.svc.allocate_gpu(
                gpu_id="track-gpu-03",
                username="greedy_user",
                mode="EXCLUSIVE",
                requested_vram_gb=30,
                job_name="overallocate.py"
            )
        self.assertIn("VRAM không đủ", str(ctx.exception))

    def test_gpu_allocation_invalid_gpu_id(self):
        with self.assertRaises(ValueError) as ctx:
            self.svc.allocate_gpu(
                gpu_id="invalid-gpu-99",
                username="test",
                mode="SHARED",
                requested_vram_gb=8,
                job_name="test.py"
            )
        self.assertIn("Không tìm thấy GPU", str(ctx.exception))

    def test_stop_job_success(self):
        # Stop existing job-101 (uses 12GB on track-gpu-01)
        job101 = next(j for j in self.svc.jobs if j["id"] == "job-101")
        gpu1 = next(g for g in self.svc.gpus if g["id"] == "track-gpu-01")
        initial_vram = gpu1["vram_used_mb"]

        res = self.svc.stop_job("job-101")
        self.assertTrue(res["success"])

        # Check job status
        self.assertEqual(job101["status"], "STOPPED")

        # Check VRAM freed on GPU
        self.assertEqual(gpu1["vram_used_mb"], initial_vram - 12288)

        # Check PID removed from active processes
        self.assertNotIn(job101["pid"], [p["pid"] for p in gpu1["active_processes"]])

    def test_stop_nonexistent_job(self):
        with self.assertRaises(ValueError) as ctx:
            self.svc.stop_job("nonexistent-job-xyz")
        self.assertIn("Không tìm thấy job", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()

