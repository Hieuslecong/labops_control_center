import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.services.gpu_scheduler import SchedulerError
from app.services.labops_service import LabOpsDataService

FAKE_UUID = "GPU-test-0000-0000-0000-000000000001"


class TestLabOpsDataServiceV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        self.telemetry = [{"id": "h1-gpu-00", "uuid": FAKE_UUID, "server_id": "h1", "active_processes": []}]
        self.svc.nvml_adapter.get_live_gpu_telemetry_sync = lambda: self.telemetry
        self.svc.gpu_scheduler.telemetry_fn = lambda: self.telemetry
        self.base_gpu = [{"id": "h1-gpu-00", "uuid": FAKE_UUID, "server_id": "h1", "active_processes": []}]
        type(self.svc).gpus  # property dựa trên NVML adapter đã patch

    def tearDown(self):
        self.tmp.cleanup()

    def test_demo_mode_not_implicit_under_unittest(self):
        os.environ.pop("LABOPS_MODE", None)
        self.assertFalse(settings.DEMO_MODE)

    def test_allocate_creates_reservation_without_fake_pid(self):
        res = self.svc.allocate_gpu("h1-gpu-00", "alice", "FULL_GPU_EXCLUSIVE", job_name="train.py")
        self.assertEqual(res["verification"], "PENDING_VERIFICATION")
        self.assertEqual(res["reservation"]["state"], "RESERVED")
        self.assertIsNone(res["reservation"]["pid"])
        self.assertFalse(res["reservation"]["runtime_verified"])

    def test_double_allocation_rejected(self):
        self.svc.allocate_gpu("h1-gpu-00", "alice", "FULL_GPU_EXCLUSIVE")
        with self.assertRaises(SchedulerError) as ctx:
            self.svc.allocate_gpu("h1-gpu-00", "bob", "FULL_GPU_EXCLUSIVE")
        self.assertEqual(ctx.exception.code, "GPU_ALREADY_RESERVED")

    def test_external_process_blocks_exclusive(self):
        self.telemetry[0]["active_processes"] = [{"pid": 42}]
        with self.assertRaises(SchedulerError) as ctx:
            self.svc.allocate_gpu("h1-gpu-00", "alice", "FULL_GPU_EXCLUSIVE")
        self.assertEqual(ctx.exception.code, "GPU_BUSY_EXTERNAL")

    def test_unobserved_gpu_fails_closed(self):
        self.svc.gpu_scheduler.telemetry_fn = lambda: []
        with self.assertRaises(SchedulerError) as ctx:
            self.svc.allocate_gpu("h1-gpu-00", "alice", "FULL_GPU_EXCLUSIVE")
        self.assertEqual(ctx.exception.code, "GPU_NOT_OBSERVED")

    def test_shared_vram_mode_rejected(self):
        with self.assertRaises(SchedulerError) as ctx:
            self.svc.allocate_gpu("h1-gpu-00", "alice", "SHARED", requested_vram_gb=8)
        self.assertEqual(ctx.exception.code, "MODE_NOT_SUPPORTED")

    def test_idempotency_key_returns_same_reservation(self):
        a = self.svc.allocate_gpu("h1-gpu-00", "alice", "FULL_GPU_EXCLUSIVE", idempotency_key="k1")
        b = self.svc.allocate_gpu("h1-gpu-00", "alice", "FULL_GPU_EXCLUSIVE", idempotency_key="k1")
        self.assertEqual(a["reservation"]["id"], b["reservation"]["id"])

    def test_researcher_cannot_stop_others_job(self):
        r = self.svc.allocate_gpu("h1-gpu-00", "alice", "FULL_GPU_EXCLUSIVE")["reservation"]
        with self.assertRaises(SchedulerError) as ctx:
            self.svc.stop_job(r["id"], actor="bob", is_admin=False)
        self.assertEqual(ctx.exception.http_status, 403)

    def test_stop_unstarted_reservation_releases(self):
        r = self.svc.allocate_gpu("h1-gpu-00", "alice", "FULL_GPU_EXCLUSIVE")["reservation"]
        res = self.svc.stop_job(r["id"], actor="alice", is_admin=False)
        self.assertEqual(res["state"], "RELEASED")

    def test_stop_running_requires_agent_verification(self):
        r = self.svc.allocate_gpu("h1-gpu-00", "alice", "FULL_GPU_EXCLUSIVE")["reservation"]
        self.svc.gpu_scheduler.confirm_from_agent(r["id"], "RUNNING", 999)
        res = self.svc.stop_job(r["id"], actor="admin", is_admin=True)
        self.assertEqual(res["state"], "STOP_REQUESTED")
        self.assertEqual(res["verification"], "PENDING_VERIFICATION")

    def test_stop_nonexistent(self):
        with self.assertRaises(SchedulerError) as ctx:
            self.svc.stop_job("nope", actor="admin")
        self.assertEqual(ctx.exception.http_status, 404)


if __name__ == "__main__":
    unittest.main()
