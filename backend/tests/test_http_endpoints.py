import os
import sys
import json
import unittest
import threading
from urllib.request import urlopen, Request
from http.server import ThreadingHTTPServer

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault("LABOPS_SESSION_SECRET", "test-secret-http")
from app.core.security import issue_token
from app.main import LabOpsRequestHandler, _format_server, _format_gpu

class TestHttpEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LabOpsRequestHandler)
        except PermissionError as exc:
            raise unittest.SkipTest("sandbox does not permit loopback listeners") from exc
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path, role="ADMIN"):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = Request(url)
        if role:
            req.add_header("Authorization", "Bearer " + issue_token("tester", role))
        with urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data)

    def test_healthz(self):
        status, data = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")

    def test_servers_endpoint(self):
        status, data = self._get("/api/v1/servers")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 2)
        # Verify both camelCase and snake_case exist
        for srv in data:
            self.assertIn("cpuUsagePercent", srv)
            self.assertIn("cpu_usage_percent", srv)
            self.assertIn("vpnIp", srv)
            self.assertIn("vpn_ip", srv)

    def test_gpus_endpoint(self):
        status, data = self._get("/api/v1/gpus")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, list)
        for gpu in data:
            self.assertIn("vramTotalMb", gpu)
            self.assertIn("vram_total_mb", gpu)
            self.assertIn("displayLabel", gpu)

    def test_dashboard_summary(self):
        status, data = self._get("/api/v1/dashboard/summary")
        self.assertEqual(status, 200)
        self.assertIn("servers", data)
        self.assertIn("gpus", data)
        self.assertIn("members", data)

    def test_storage_pools(self):
        status, data = self._get("/api/v1/storage-pools")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, list)

    def test_format_helpers(self):
        srv = {"cpu_usage_percent": 25.5, "vpn_ip": "10.200.0.2"}
        formatted_srv = _format_server(srv)
        self.assertEqual(formatted_srv["cpuUsagePercent"], 25.5)
        self.assertEqual(formatted_srv["vpnIp"], "10.200.0.2")

        gpu = {"vram_total_mb": 24576, "gpu_util_percent": 50}
        formatted_gpu = _format_gpu(gpu)
        self.assertEqual(formatted_gpu["vramTotalMb"], 24576)
        self.assertEqual(formatted_gpu["gpuUtilPercent"], 50)

    def test_processes_endpoints(self):
        from app.core.config import settings
        local_id = settings.LOCAL_HOST_ID
        remote_id = "workstation" if local_id == "track" else "track"

        status, data = self._get(f"/api/v1/servers/{local_id}/processes")
        self.assertEqual(status, 200)
        self.assertEqual(data["source_host"], local_id)
        self.assertEqual(data["collector_status"], "OK")
        self.assertIn("actual_observations", data)

        status, data_remote = self._get(f"/api/v1/servers/{remote_id}/processes")
        self.assertEqual(status, 200)
        self.assertEqual(data_remote["source_host"], remote_id)
        self.assertEqual(data_remote["collector_status"], "NOT_CONFIGURED")
        self.assertIn("not configured", data_remote["error_details"])

    def test_gpu_processes_and_ssh_sessions(self):
        from app.core.config import settings
        local_id = settings.LOCAL_HOST_ID
        status, gpu_procs = self._get(f"/api/v1/servers/{local_id}/gpu-processes")
        self.assertEqual(status, 200)
        self.assertEqual(gpu_procs["source_host"], local_id)

        status, ssh_sessions = self._get(f"/api/v1/servers/{local_id}/ssh-sessions")
        self.assertEqual(status, 200)
        self.assertEqual(ssh_sessions["source_host"], local_id)
        self.assertIn("actual_observations", ssh_sessions)

    def test_jobs_active_and_monitoring_health(self):
        status, jobs = self._get("/api/v1/jobs/active")
        self.assertEqual(status, 200)
        self.assertIsInstance(jobs, list)

        status, health = self._get("/api/v1/monitoring/health")
        self.assertEqual(status, 200)
        self.assertIn("collectors", health)
        self.assertEqual(health["collectors"]["process_monitor"], "OK")

    def test_users_and_ssh_enrollment_endpoints(self):
        status, users = self._get("/api/v1/users")
        self.assertEqual(status, 200)
        self.assertIsInstance(users, list)
        self.assertGreaterEqual(len(users), 1)
        self.assertIn("fullName", users[0])
        self.assertIn("sshKeyStatus", users[0])

        status_h, access_health = self._get("/api/v1/access/health")
        self.assertEqual(status_h, 200)
        self.assertIn("wireguard", access_health)

if __name__ == "__main__":
    unittest.main()

