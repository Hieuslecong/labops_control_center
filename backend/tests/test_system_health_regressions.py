import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault("LABOPS_SESSION_SECRET", "test-secret-health-regressions")

from fastapi.testclient import TestClient

from app.adapters.nvml_adapter import NvmlAdapter
from app.adapters.process_monitor_adapter import ProcessMonitorAdapter
from app.adapters.vpn_provider import VPNProviderError
from app.core.security import issue_token
from app.main import app
from app.services.host_registry import HostRegistry
from app.services.labops_service import LabOpsDataService, service as global_service


class TestSystemHealthRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_audit_logs_without_correlation_id_do_not_500(self):
        with open(os.path.join(self.tmp.name, "audit_logs.json"), "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "id": "aud-legacy-01",
                        "actor": "admin",
                        "action": "WIREGUARD_DEVICE_REGISTERED",
                        "target_type": "MEMBER",
                        "target_id": "hieulc",
                        "timestamp": "2026-11-10T17:00:11+00:00",
                        "result": "SUCCESS",
                        "details": "Legacy audit record without correlation_id",
                    }
                ],
                f,
            )
        reloaded = LabOpsDataService(demo_mode=False, data_dir=self.tmp.name)
        self.assertTrue(reloaded.audit_logs[0].get("correlation_id"))

        client = TestClient(app)
        token = issue_token("admin", "ADMIN")
        res = client.get("/api/v1/audit-logs", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json(), list)

    def test_list_all_vpn_devices_falls_back_when_wgeasy_offline(self):
        self.svc.members = [
            {
                "id": "mem-1",
                "username": "testacc",
                "wireguard": {
                    "devices": [
                        {"device_id": "dev-1", "name": "Laptop", "vpn_ip": "10.88.40.10", "status": "ACTIVE"}
                    ]
                },
            }
        ]
        with patch.object(
            self.svc.vpn_manager.primary_provider,
            "list_devices",
            side_effect=VPNProviderError("Connection refused"),
        ):
            devices = self.svc.list_all_vpn_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["owner"], "testacc")
        self.assertEqual(devices[0]["sync_state"], "CACHED_GATEWAY_OFFLINE")

        # Kiểm tra endpoint FastAPI thực tế trả về 200 thay vì 500
        client = TestClient(app)
        token = issue_token("admin", "ADMIN")
        with patch.object(
            global_service.vpn_manager.primary_provider,
            "list_devices",
            side_effect=VPNProviderError("Connection refused"),
        ):
            res = client.get("/api/v1/v2/vpn/devices", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json(), list)

    def test_nvml_adapter_parses_decimal_temperature_and_no_fake_64(self):
        adapter = NvmlAdapter(server_id="workstation", demo_mode=False)
        fake_agent_payload = json.dumps(
            {
                "status": "VERIFIED",
                "collected_at": "2026-11-18T02:35:27Z",
                "gpus": [
                    {
                        "index": 0,
                        "name": "NVIDIA A30",
                        "uuid": "GPU-1",
                        "pci_bus_id": "00000000:D8:00.0",
                        "memory_total_mb": 24576,
                        "memory_used_mb": 15006,
                        "utilization_gpu_pct": 100,
                        "temperature_c": 0,
                        "power_watts": 135,
                        "power_limit_watts": 165,
                        "active_processes": [],
                    }
                ],
            }
        ).encode("utf-8")

        class _FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return fake_agent_payload

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            with patch("subprocess.check_output", return_value="89.50\n"):
                gpus = adapter._fetch_go_agent_gpus()
        self.assertIsNotNone(gpus)
        self.assertEqual(gpus[0]["temp_celsius"], 90)

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            with patch("subprocess.check_output", side_effect=RuntimeError("nvidia-smi failed")):
                gpus_err = adapter._fetch_go_agent_gpus()
        self.assertIsNotNone(gpus_err)
        self.assertEqual(gpus_err[0]["temp_celsius"], 0)

    def test_host_registry_marks_offline_server_without_agent_url_offline(self):
        reg = HostRegistry(self.tmp.name, local_host_id="workstation")
        # Giả lập heartbeat cũ tồn tại trên track nhưng live status báo offline
        hosts = reg.list_hosts(
            live_servers=[
                {"id": "track", "status": "offline"},
                {"id": "workstation", "status": "online"},
            ]
        )
        track = next(h for h in hosts if h["id"] == "track")
        self.assertEqual(track["observed_state"], "OFFLINE")
        self.assertEqual(track["freshness"], "OFFLINE")

    def test_process_monitor_normalizes_go_agent_uid_and_protected_commands(self):
        import pwd

        expected_user = pwd.getpwuid(1000).pw_name
        adapter = ProcessMonitorAdapter(server_id="workstation", demo_mode=False)
        fake_agent_payload = {
            "collected_at": "2026-11-18T03:00:00Z",
            "processes": [
                {
                    "pid": 54321,
                    "ppid": 1,
                    "pgid": 54321,
                    "uid": 1000,
                    "user": "aiot-1",
                    "name": "python3",
                    "command_line": "python3 -c from app.main import app",
                    "classification": "MEMBER_PROCESS",
                    "attribution": "LIKELY_USER_WORKLOAD",
                    "is_user_workload": True,
                    "is_protected": False,
                    "is_gpu": False,
                },
                {
                    "pid": 940706,
                    "ppid": 1,
                    "pgid": 940706,
                    "uid": 1000,
                    "user": "aiot-1",
                    "name": "python",
                    "command_line": "python /mnt/HDD3/vinh_test/11_train_rgb.py",
                    "classification": "GPU_PROCESS",
                    "attribution": "VERIFIED_USER_WORKLOAD",
                    "is_user_workload": True,
                    "is_protected": False,
                    "is_gpu": True,
                    "gpu_index": 0,
                    "vram_mb": 1024,
                },
            ],
        }
        with patch.object(adapter, "_query_go_agent", return_value=fake_agent_payload):
            res = adapter.get_real_processes(filter_type="all")
        obs = {p["pid"]: p for p in res["actual_observations"]}
        self.assertEqual(obs[54321]["username"], expected_user)
        self.assertEqual(obs[54321]["classification"], "SYSTEM_PROCESS")
        self.assertEqual(obs[54321]["attribution"], "SYSTEM_PROCESS")
        self.assertTrue(obs[54321]["is_protected"])
        self.assertFalse(obs[54321]["is_user_workload"])
        self.assertEqual(obs[940706]["username"], expected_user)
        self.assertEqual(obs[940706]["classification"], "GPU_PROCESS")


if __name__ == "__main__":
    unittest.main()
