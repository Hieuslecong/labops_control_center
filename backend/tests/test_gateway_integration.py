"""
Unit test kiểm thử tích hợp giữa LabOps Python Backend và Go Agent Gateway.
"""
from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
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
        with patch.object(executor, "_inspect_host", return_value={"source": "test-double"}):
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

        with patch.dict(os.environ, {"LABOPS_GATEWAY_CONTROL_SECRET": "test-control-secret-0123456789abcdef"}), \
             patch("urllib.request.urlopen", return_value=mock_gw_response):
            envelope = client.dispatch(remote_host, "INSPECT_HOST")
            self.assertEqual(envelope["host_id"], "workstation")
            self.assertEqual(envelope["action"], "INSPECT_HOST")
            self.assertEqual(envelope["result"]["hostname"], "aiot-ws1")

    def test_host_monitor_adapter_gateway_telemetry_parser(self):
        adapter = HostMonitorAdapter(local_host_id="track")
        observed_at = datetime.now(timezone.utc).isoformat()

        mock_gw_data = {
            "node_id": "workstation",
            "status": "ONLINE",
            "host_received_at": observed_at,
            "gpu_received_at": observed_at,
            "host": {
                "node_id": "workstation",
                "status": "online",
                "is_real_host": True,
                "collected_at": observed_at,
                "ip_address": "192.0.2.251",
                "disk_type": "ext4 (test-device)",
                "hostname": "aiot-ws1",
                "cpu_model": "AMD Ryzen test CPU",
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
                "node_id": "workstation",
                "status": "VERIFIED",
                "collected_at": observed_at,
                "driver_version": "550.163.01",
                "cuda_version": "12.4",
                "gpu_count": 1,
                "gpus": [
                    {
                        "index": 0,
                        "name": "NVIDIA A30",
                        "uuid": "GPU-ws1-uuid-001",
                        "pci_bus_id": "0000:01:00.0",
                        "memory_total_mb": 24576,
                        "memory_used_mb": 4096,
                        "gpu_utilization_percent": 30.0,
                        "memory_utilization_percent": 16.0,
                        "temperature_celsius": 48,
                        "power_usage_watts": 85.0,
                        "power_limit_watts": 165.0,
                        "enforcement_state": "MONITOR_ONLY",
                        "processes": [],
                    }
                ],
            },
        }

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_gw_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch.dict(os.environ, {
            "LABOPS_GATEWAY_HTTP_URL": "http://127.0.0.1:9444",
            "LABOPS_GATEWAY_CONTROL_SECRET": "test-control-secret-0123456789abcdef",
        }), patch("urllib.request.urlopen", return_value=mock_resp):
            telemetry = adapter.get_local_workstation_telemetry()
            self.assertEqual(telemetry["id"], "workstation")
            self.assertEqual(telemetry["status"], "online")
            self.assertEqual(telemetry["gpu_count"], 1)
            self.assertEqual(telemetry["hostname"], "aiot-ws1")
            self.assertEqual(telemetry["ram_total_gb"], 64)
            self.assertEqual(telemetry["network_in_mbps"], 10.0)
            self.assertTrue(telemetry["is_real_host"])
            self.assertEqual(len(telemetry["gpus"]), 1)
            self.assertEqual(telemetry["gpus"][0]["uuid"], "GPU-ws1-uuid-001")


if __name__ == "__main__":
    unittest.main()

