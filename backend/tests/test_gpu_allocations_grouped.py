import os
import sys
import tempfile
import unittest
from unittest.mock import patch

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault("LABOPS_SESSION_SECRET", "test-secret-gpu-grouped")

from fastapi.testclient import TestClient

from app.core.security import issue_token
from app.main import app
from app.services.labops_service import LabOpsDataService, canonicalize_gpu_id
from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient
from app.services.zalo_assistant.bot_core import ZaloBotEngine
from app.services.zalo_assistant.quota_ledger import QuotaLedger
from app.services.zalo_assistant.report_generator import ReportGenerator


MOCK_UUID_WS = "GPU-workstation-0000-0000-000000000001"


class TestGpuAllocationsGrouped(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        self.telemetry = [
            {
                "id": "workstation-gpu-01",
                "display_label": "WS-GPU-01",
                "displayLabel": "WS-GPU-01",
                "server_id": "workstation",
                "serverId": "workstation",
                "server_name": "Server 02 — Workstation (aiot-ws1 • Cục bộ)",
                "model": "NVIDIA A30 (24GB HBM2)",
                "pci_bus_id": "00000000:D8:00.0",
                "uuid": MOCK_UUID_WS,
                "vram_total_mb": 24576,
                "vram_used_mb": 5734,
                "gpu_util_percent": 75,
                "temp_celsius": 64,
                "allocation_mode": "SHARED",
                "active_processes": [
                    {
                        "pid": 999901,
                        "name": "python (11_train_rgb.py)",
                        "username": "vinh_test",
                        "vram_mb": 5734,
                        "cpu_percent": 110.0,
                    },
                    {
                        "pid": 999902,
                        "name": "systemd-helper",
                        "username": "root",
                        "vram_mb": 128,
                        "cpu_percent": 0.5,
                    },
                    {
                        "pid": 999903,
                        "name": "control-plane-agent",
                        "username": "aiot-ws1",
                        "vram_mb": 64,
                        "cpu_percent": 0.1,
                    },
                ],
            }
        ]
        self.svc.nvml_adapter.get_live_gpu_telemetry_sync = lambda: self.telemetry
        self.svc.gpu_scheduler.telemetry_fn = lambda: self.telemetry
        self.svc.members = [
            {
                "id": "mem-vinh",
                "username": "vinh_test",
                "full_name": "Nguyễn Thế Vinh",
                "role": "RESEARCHER",
                "assigned_server": "workstation",
                "gpu_permissions": ["ws-gpu-01"],
                "vram_quota_gb": 16,
                "gpu_allocation_mode": "SHARED",
            },
            {
                "id": "mem-nhan",
                "username": "nhannv",
                "full_name": "Nguyễn Văn Nhân",
                "role": "RESEARCHER",
                "assigned_server": "workstation",
                "gpu_permissions": ["workstation-gpu-01"],
                "vram_quota_gb": 12,
                "gpu_allocation_mode": "SHARED",
            },
            {
                "id": "mem-hieu",
                "username": "hieulc",
                "full_name": "Lê Công Hiếu",
                "role": "RESEARCHER",
                "assigned_server": "workstation",
                "gpu_permissions": ["WS-GPU-01"],
                "vram_quota_gb": 8,
                "gpu_allocation_mode": "SHARED",
            },
            {
                "id": "mem-testacc",
                "username": "testacc",
                "full_name": "testacc",
                "role": "RESEARCHER",
                "assigned_server": "track",
                "gpu_permissions": ["rack-gpu-01"],
                "vram_quota_gb": 2,
                "gpu_allocation_mode": "EXCLUSIVE",
            },
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def test_canonicalize_gpu_id_aliases(self):
        self.assertEqual(canonicalize_gpu_id("WS-GPU-01"), "workstation-gpu-01")
        self.assertEqual(canonicalize_gpu_id("ws-gpu-01"), "workstation-gpu-01")
        self.assertEqual(canonicalize_gpu_id("workstation-gpu-01"), "workstation-gpu-01")
        self.assertEqual(canonicalize_gpu_id("RACK-GPU-01"), "track-gpu-01")
        self.assertEqual(canonicalize_gpu_id("rack-gpu-01"), "track-gpu-01")
        self.assertEqual(canonicalize_gpu_id("RACK-GPU-02"), "track-gpu-02")
        self.assertEqual(canonicalize_gpu_id("rack-gpu-03"), "track-gpu-03")
        self.assertEqual(canonicalize_gpu_id("h1-gpu-00"), "h1-gpu-00")

    def test_gpu_allocations_grouped_by_device_and_filters_system_accounts(self):
        groups = self.svc.get_gpu_allocations_by_device()
        labels = [g["gpu_label"] for g in groups]
        self.assertEqual(labels, ["WS-GPU-01", "RACK-GPU-01", "RACK-GPU-02", "RACK-GPU-03"])

        ws_group = groups[0]
        self.assertEqual(ws_group["gpu_id"], "workstation-gpu-01")
        self.assertTrue(ws_group["is_online"])
        self.assertEqual(ws_group["accounts_count"], 3)
        self.assertEqual(ws_group["active_accounts_count"], 1)

        ws_usernames = [a["username"] for a in ws_group["accounts"]]
        self.assertEqual(ws_usernames[0], "vinh_test")
        self.assertCountEqual(ws_usernames, ["vinh_test", "nhannv", "hieulc"])
        self.assertNotIn("root", ws_usernames)
        self.assertNotIn("aiot-ws1", ws_usernames)

        vinh_alloc = ws_group["accounts"][0]
        self.assertEqual(vinh_alloc["status"], "IN_USE")
        self.assertEqual(vinh_alloc["vram_used_mb"], 5734)
        self.assertEqual(vinh_alloc["current_vram_mb"], 5734)
        self.assertIn("11_train_rgb.py", vinh_alloc["primary_script"])

        nhan_alloc = next(a for a in ws_group["accounts"] if a["username"] == "nhannv")
        self.assertEqual(nhan_alloc["status"], "IDLE")

        rack1_group = groups[1]
        self.assertFalse(rack1_group["is_online"])
        self.assertEqual(rack1_group["accounts_count"], 1)
        self.assertEqual(rack1_group["accounts"][0]["username"], "testacc")
        self.assertEqual(rack1_group["accounts"][0]["status"], "OFFLINE_HOST")

        for empty_rack_group in groups[2:]:
            self.assertFalse(empty_rack_group["is_online"])
            self.assertEqual(empty_rack_group["accounts_count"], 0)
            self.assertEqual(empty_rack_group["accounts"], [])

    def test_scheduler_reservation_merges_into_gpu_group(self):
        # Xóa process đang chạy để GPU rảnh cho phép reserve độc quyền
        self.telemetry[0]["active_processes"] = []
        self.svc.allocate_gpu(
            gpu_id="workstation-gpu-01",
            username="alice_researcher",
            mode="FULL_GPU_EXCLUSIVE",
            job_name="train_diffusion.py",
        )
        groups = self.svc.get_gpu_allocations_by_device()
        ws_group = next(g for g in groups if g["gpu_id"] == "workstation-gpu-01")
        alice_alloc = next(
            (a for a in ws_group["accounts"] if a["username"] == "alice_researcher"),
            None,
        )
        self.assertIsNotNone(alice_alloc)
        self.assertEqual(alice_alloc["status"], "RESERVED")
        self.assertEqual(alice_alloc["allocation_mode"], "EXCLUSIVE")
        self.assertIn("train_diffusion.py", alice_alloc["primary_script"])

    def test_fastapi_endpoints_return_200(self):
        client = TestClient(app)
        token = issue_token("admin_tester", "ADMIN")
        headers = {"Authorization": f"Bearer {token}"}

        res_flat = client.get("/api/v1/gpus/allocations", headers=headers)
        self.assertEqual(res_flat.status_code, 200)
        self.assertIsInstance(res_flat.json(), list)

        res_grouped = client.get("/api/v1/gpus/allocations/by-gpu", headers=headers)
        self.assertEqual(res_grouped.status_code, 200)
        data = res_grouped.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 1)
        self.assertIn("gpu_id", data[0])
        self.assertIn("accounts", data[0])

    def test_zalo_bot_alloc_command_formats_grouped_by_gpu(self):
        ledger = QuotaLedger(self.tmp.name)
        report_gen = ReportGenerator(self.tmp.name)
        ai_client = AIGatewayClient(model="model_free")
        bot = ZaloBotEngine(ledger, ai_client, report_gen, authorized_users=["admin"])

        with patch("app.services.labops_service.service", self.svc):
            reply = bot._cmd_allocations()

        self.assertIn("THỐNG KÊ TÀI KHOẢN THEO TỪNG GPU", reply)
        self.assertIn("WS-GPU-01", reply)
        self.assertIn("RACK-GPU-01", reply)
        self.assertIn("RACK-GPU-02", reply)
        self.assertIn("RACK-GPU-03", reply)
        self.assertIn("@vinh_test", reply)
        self.assertIn("@nhannv", reply)
        self.assertIn("@hieulc", reply)
        self.assertIn("@testacc", reply)
        self.assertNotIn("@root", reply)


if __name__ == "__main__":
    unittest.main()
