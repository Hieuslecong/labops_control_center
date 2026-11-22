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

        with patch.dict(os.environ, {"LABOPS_AGENT_SECRET": ""}, clear=False):
            self.assertIsNone(adapter._fetch_go_agent_gpus())

        with patch.dict(os.environ, {"LABOPS_AGENT_SECRET": "unit-test-agent-secret"}, clear=False):
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

    def test_get_active_compute_jobs_rolls_up_tree_gpu_vram_and_preserves_fields(self):
        from app.schemas.labops import JobResponse

        adapter = ProcessMonitorAdapter(server_id="workstation", demo_mode=False)
        fake_workloads_payload = {
            "collected_at": "2026-11-22T12:59:30Z",
            "processes": [
                {
                    "ref": "workstation:host:555432:94320744",
                    "pid": 555432,
                    "ppid": 1,
                    "pgid": 555432,
                    "uid": 1011,
                    "user": "vinh_test",
                    "name": "python",
                    "command_line": ".venv/bin/python -u scripts/17_run_all_protocol_fusion.py",
                    "classification": "RESEARCH_JOB",
                    "attribution": "VERIFIED_USER_WORKLOAD",
                    "is_user_workload": True,
                    "is_root_workload": True,
                    "children_count": 19,
                    "total_cpu_percent": 6.1,
                    "total_memory_rss_mb": 20466.1,
                    "total_vram_mb": 2960,
                    "tree_is_gpu": True,
                    "tree_gpu_index": 0,
                    "cpu_percent": 0.0,
                    "memory_rss_mb": 15.7,
                    "start_time": "2026-11-22 13:47:44",
                    "start_time_ticks": 94320744,
                    "elapsed_time": "06h 11m 45s",
                    "state": "S",
                    "is_gpu": False,
                    "vram_mb": 0,
                    "is_protected": False,
                },
                {
                    "ref": "workstation:host:777001:94320800",
                    "pid": 777001,
                    "ppid": 1,
                    "pgid": 777001,
                    "uid": 1012,
                    "user": "testacc",
                    "name": "python",
                    "command_line": "python train.py",
                    "classification": "GPU_PROCESS",
                    "attribution": "VERIFIED_USER_WORKLOAD",
                    "is_user_workload": True,
                    "is_root_workload": True,
                    "children_count": 2,
                    "total_cpu_percent": 12.0,
                    "total_memory_rss_mb": 4096.0,
                    "cpu_percent": 12.0,
                    "memory_rss_mb": 4096.0,
                    "start_time": "2026-11-22 14:00:00",
                    "start_time_ticks": 94320800,
                    "elapsed_time": "05h 00m 00s",
                    "state": "R",
                    "is_gpu": True,
                    "gpu_index": 0,
                    "vram_mb": 4096,
                    "is_protected": False,
                },
            ],
        }
        with patch.object(adapter, "_query_go_agent", return_value=fake_workloads_payload), \
             patch.object(adapter, "get_real_ssh_sessions", return_value={"actual_observations": []}):
            jobs = adapter.get_active_compute_jobs(members_db=[{"id": "m1", "username": "vinh_test", "full_name": "Nguyễn Thế Vinh"}])

        self.assertEqual(len(jobs), 2)
        by_pid = {j["pid"]: j for j in jobs}
        vinh_job = by_pid[555432]
        self.assertEqual(vinh_job["vramUsedMb"], 2960)
        self.assertTrue(vinh_job["isGpu"])
        self.assertEqual(vinh_job["gpuId"], "workstation-gpu-01")
        self.assertEqual(vinh_job["classification"], "GPU_PROCESS")
        self.assertEqual(vinh_job["ref"], "workstation:host:555432:94320744")

        test_job = by_pid[777001]
        self.assertEqual(test_job["vramUsedMb"], 4096)
        self.assertTrue(test_job["isGpu"])
        self.assertEqual(test_job["gpuId"], "workstation-gpu-01")
        self.assertEqual(test_job["classification"], "GPU_PROCESS")

        dumped = JobResponse.model_validate(vinh_job).model_dump(by_alias=True)
        self.assertEqual(dumped["vramUsedMb"], 2960)
        self.assertTrue(dumped["isGpu"])
        self.assertEqual(dumped["gpuId"], "workstation-gpu-01")
        self.assertEqual(dumped["classification"], "GPU_PROCESS")
        self.assertEqual(dumped["ref"], "workstation:host:555432:94320744")
        self.assertFalse(dumped["isProtected"])

    def test_storage_inventory_does_not_duplicate_local_disks_to_remote_track(self):
        import io
        import json
        from app.services.storage_inventory_service import StorageInventoryService
        from app.core.config import settings

        fake_agent_storage = {
            "storage_pools": [
                {"storage_id": "pool-root", "device": "/dev/sda2", "mount_point": "/", "filesystem": "ext4", "capacity_gib": 832.8, "used_gib": 69.1, "free_gib": 763.7, "eligible": False},
                {"storage_id": "pool-sdb", "device": "/dev/sdb", "mount_point": "/media/aiot-ws1/159bacc4", "filesystem": "ext4", "capacity_gib": 915.8, "used_gib": 47.1, "free_gib": 868.7, "eligible": True},
                {"storage_id": "pool-sdd", "device": "/dev/sdd", "mount_point": "/media/aiot-ws1/5ce32377", "filesystem": "ext4", "capacity_gib": 915.8, "used_gib": 236.9, "free_gib": 678.9, "eligible": True},
                {"storage_id": "pool-hdd3", "device": "/dev/sdc", "mount_point": "/mnt/HDD3", "filesystem": "ext4", "capacity_gib": 3666.5, "used_gib": 1484.8, "free_gib": 2181.7, "eligible": True, "is_default": True},
            ]
        }

        class _FakeResp(io.BytesIO):
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False

        inv = StorageInventoryService()
        calls = []

        def fake_urlopen(req, timeout=1.5):
            calls.append(req.full_url)
            return _FakeResp(json.dumps(fake_agent_storage).encode("utf-8"))

        with patch.object(settings, "LOCAL_HOST_ID", "workstation"), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            track_pools = inv.get_storage_pools("track")
            ws_pools = inv.get_storage_pools("workstation")

        self.assertEqual(track_pools, [])
        self.assertEqual(len(ws_pools), 4)
        self.assertTrue(all(p["host_id"] == "workstation" for p in ws_pools))
        self.assertEqual(len(calls), 1)

    def test_member_create_request_accepts_student_role(self):
        from pydantic import ValidationError
        from app.schemas.labops import MemberCreateRequest

        base = {
            "fullName": "Sinh Viên Test",
            "username": "svtest01",
            "email": "svtest01@lab.ai.vn",
            "workspaceName": "ws-svtest01",
        }
        for valid_role in ("RESEARCHER", "STUDENT", "ADMIN"):
            req = MemberCreateRequest.model_validate({**base, "role": valid_role})
            self.assertEqual(req.role, valid_role)

        with self.assertRaises(ValidationError):
            MemberCreateRequest.model_validate({**base, "role": "INVALID_ROLE"})

    def test_wg_easy_counts_only_fresh_handshakes_as_online(self):
        from datetime import datetime, timedelta, timezone
        from app.adapters.wg_easy_adapter import WgEasyAdapter

        now = datetime.now(timezone.utc)
        fresh_ts = (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
        stale_ts = (now - timedelta(hours=4)).isoformat().replace("+00:00", "Z")
        fake_clients = [
            {
                "id": "1",
                "name": "peer-fresh",
                "enabled": True,
                "ipv4Address": "10.88.40.2",
                "publicKey": "pub1=",
                "latestHandshakeAt": fresh_ts,
                "transferRx": 100,
                "transferTx": 200,
            },
            {
                "id": "2",
                "name": "peer-stale",
                "enabled": True,
                "ipv4Address": "10.88.40.3",
                "publicKey": "pub2=",
                "latestHandshakeAt": stale_ts,
                "transferRx": 300,
                "transferTx": 400,
            },
            {
                "id": "3",
                "name": "peer-never",
                "enabled": True,
                "ipv4Address": "10.88.40.4",
                "publicKey": "pub3=",
                "latestHandshakeAt": None,
                "transferRx": 0,
                "transferTx": 0,
            },
        ]
        adapter = WgEasyAdapter(password="secret", public_endpoint="118.70.169.236:2204")

        def fake_req(path: str, method: str = "GET", **kwargs):
            if path == "api/admin/interface":
                return {"name": "wg0", "ipv4Cidr": "10.88.40.0/24", "publicKey": "srvpub="}
            if path == "api/client":
                return fake_clients
            return {}

        with patch.object(adapter, "_request", side_effect=fake_req), \
             patch.object(adapter, "inspect_runtime_network", return_value={"inspected": False, "reason": "TEST"}):
            devs = adapter.list_devices()
            status = adapter.get_connection_status()

        by_id = {d["id"]: d for d in devs}
        self.assertTrue(by_id["1"]["is_online"])
        self.assertFalse(by_id["2"]["is_online"])
        self.assertFalse(by_id["3"]["is_online"])
        self.assertEqual(status["total_clients"], 3)
        self.assertEqual(status["active_handshakes"], 1)
        self.assertEqual(status["ever_connected_clients"], 2)

    def test_zalo_assistant_overview_status_and_config_sync(self):
        import json
        from datetime import datetime, timezone
        from app.services.zalo_assistant.service import ZaloAssistantService

        with tempfile.TemporaryDirectory(prefix="labops-zalo-reg-") as tmp:
            old_auth = os.environ.get("ZALO_AUTHORIZED_USERS")
            old_tok = os.environ.get("ZALO_BOT_TOKEN")
            try:
                os.environ["ZALO_AUTHORIZED_USERS"] = "*"
                os.environ["ZALO_BOT_TOKEN"] = "123456:fake-token-for-unit-test"
                svc = ZaloAssistantService(data_dir=tmp)
                with patch.object(svc.ai_client, "check_health", return_value={"status": "ONLINE"}):
                    ov_mis = svc.get_overview()
                    self.assertIn("MISCONFIGURED", ov_mis["zalo_status"])

                    svc.update_config({"authorized_users": ["bc8900a9f2fe1ba042ef"]})
                    ov_off = svc.get_overview()
                    self.assertEqual(ov_off["zalo_status"], "WORKER_OFFLINE")

                    with open(os.path.join(tmp, "zalo_worker_status.json"), "w", encoding="utf-8") as f:
                        json.dump(
                            {
                                "pid": os.getpid(),
                                "is_polling": True,
                                "bot_name": "Bot Test",
                                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                                "authorized_users_count": 1,
                            },
                            f,
                        )
                    ov_ready = svc.get_overview()
                    self.assertEqual(ov_ready["zalo_status"], "READY")
                    self.assertTrue(ov_ready["consumer"]["is_polling"])
                    self.assertEqual(ov_ready["consumer"]["authorized_users_count"], 1)

                # Khởi tạo instance khác (giống worker.py) -> tự nạp zalo_config.json mới lưu
                svc_worker = ZaloAssistantService(data_dir=tmp)
                self.assertEqual(svc_worker.bot_engine.authorized_users, ["bc8900a9f2fe1ba042ef"])
            finally:
                if old_auth is None:
                    os.environ.pop("ZALO_AUTHORIZED_USERS", None)
                else:
                    os.environ["ZALO_AUTHORIZED_USERS"] = old_auth
                if old_tok is None:
                    os.environ.pop("ZALO_BOT_TOKEN", None)
                else:
                    os.environ["ZALO_BOT_TOKEN"] = old_tok


if __name__ == "__main__":
    unittest.main()
