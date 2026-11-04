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

from app.main import LabOpsRequestHandler, _format_server, _format_gpu

class TestHttpEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LabOpsRequestHandler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = Request(url)
        with urlopen(req, timeout=3) as resp:
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
        # Workstation processes
        status, data = self._get("/api/v1/servers/workstation/processes")
        self.assertEqual(status, 200)
        self.assertEqual(data["source_host"], "workstation")
        self.assertEqual(data["collector_status"], "OK")
        self.assertIn("actual_observations", data)

        # Track processes (unreachable WireGuard)
        status, data_track = self._get("/api/v1/servers/track/processes")
        self.assertEqual(status, 200)
        self.assertEqual(data_track["source_host"], "track")
        self.assertEqual(data_track["collector_status"], "UNAVAILABLE")
        self.assertIn("Mất kết nối", data_track["error_details"])

    def test_gpu_processes_and_ssh_sessions(self):
        status, gpu_procs = self._get("/api/v1/servers/workstation/gpu-processes")
        self.assertEqual(status, 200)
        self.assertEqual(gpu_procs["source_host"], "workstation")

        status, ssh_sessions = self._get("/api/v1/servers/workstation/ssh-sessions")
        self.assertEqual(status, 200)
        self.assertEqual(ssh_sessions["source_host"], "workstation")
        self.assertIn("actual_observations", ssh_sessions)

    def test_jobs_active_and_monitoring_health(self):
        status, jobs = self._get("/api/v1/jobs/active")
        self.assertEqual(status, 200)
        self.assertIsInstance(jobs, list)

        status, health = self._get("/api/v1/monitoring/health")
        self.assertEqual(status, 200)
        self.assertIn("collectors", health)
        self.assertEqual(health["collectors"]["process_monitor"], "OK")

if __name__ == "__main__":
    unittest.main()

