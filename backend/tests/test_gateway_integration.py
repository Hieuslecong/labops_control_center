"""
Unit test kiểm thử tích hợp giữa LabOps Python Backend và Go Agent Gateway.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from app.adapters.host_monitor_adapter import HostMonitorAdapter
from app.adapters.node_agent_adapter import (
    ALLOWED_AGENT_ACTIONS,
    LocalNodeAgentExecutor,
    NodeAgentClient,
    NodeAgentError,
)


class TestGatewayIntegration(unittest.TestCase):

    def test_local_executor_allowlist(self):
        executor = LocalNodeAgentExecutor(host_id="track")
        # Lệnh hợp lệ
        res = executor.execute("INSPECT_HOST")
        self.assertIn("result", res)
        self.assertEqual(res["action"], "INSPECT_HOST")

        # Lệnh không nằm trong allowlist
        with self.assertRaises(NodeAgentError) as ctx:
            executor.execute("MALICIOUS_SHELL_CMD")
        self.assertEqual(ctx.exception.code, "ACTION_NOT_ALLOWLISTED")

    def test_gateway_dispatch_routing(self):
        client = NodeAgentClient(local_host_id="track")
        remote_host = {"id": "workstation", "hostname": "aiot-ws1"}

        mock_gw_response = MagicMock()
        mock_gw_response.read.return_value = json.dumps({
            "success": True,
            "command_id": "cmd-12345",
            "node_id": "workstation",
            "status": "SUCCESS",
            "result": {"hostname": "aiot-ws1", "status": "ONLINE", "gpus": 1},
            "duration_ms": 12,
        }).encode("utf-8")
        mock_gw_response.__enter__.return_value = mock_gw_response

        with patch("urllib.request.urlopen", return_value=mock_gw_response):
            envelope = client.dispatch(remote_host, "INSPECT_HOST")
            self.assertEqual(envelope["host_id"], "workstation")
            self.assertEqual(envelope["action"], "INSPECT_HOST")
            self.assertEqual(envelope["result"]["hostname"], "aiot-ws1")

    def test_host_monitor_adapter_gateway_telemetry_parser(self):
        adapter = HostMonitorAdapter(local_host_id="track")

        mock_gw_data = {
            "node_id": "workstation",
            "status": "ONLINE",
            "host": {
                "hostname": "aiot-ws1",
                "management_ip": "192.168.167.251",
                "cpu_model": "AMD Ryzen / EPYC",
                "cpu_cores": 16,
                "cpu_usage_percent": 15.5,
                "ram_total_bytes": 64 * 1024 * 1024 * 1024,
                "ram_used_bytes": 16 * 1024 * 1024 * 1024,
                "ram_usage_percent": 25.0,
                "disk_total_bytes": 2 * 1024 * 1024 * 1024 * 1024,
                "disk_used_bytes": 500 * 1024 * 1024 * 1024,
                "disk_usage_percent": 24.4,
                "uptime_seconds": 86400 * 3,
                "net_rx_bytes_sec": 1250000,
                "net_tx_bytes_sec": 625000,
            },
            "gpu": {
                "driver_version": "550.163.01",
                "gpu_count": 1,
                "gpus": [
                    {
                        "index": 0,
                        "name": "NVIDIA A30",
                        "uuid": "GPU-ws1-uuid-001",
                        "memory_total_mb": 24576,
                        "memory_used_mb": 4096,
                        "gpu_utilization_percent": 30.0,
                        "temperature_celsius": 48,
                        "power_usage_watts": 85,
                    }
                ],
            },
        }

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_gw_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            telemetry = adapter.get_local_workstation_telemetry()
            self.assertEqual(telemetry["id"], "workstation")
            self.assertEqual(telemetry["status"], "online")
            self.assertEqual(telemetry["gpu_count"], 1)
            self.assertEqual(telemetry["hostname"], "aiot-ws1")
            self.assertTrue(telemetry["is_real_host"])
            self.assertEqual(len(telemetry["gpus"]), 1)
            self.assertEqual(telemetry["gpus"][0]["uuid"], "GPU-ws1-uuid-001")


if __name__ == "__main__":
    unittest.main()

