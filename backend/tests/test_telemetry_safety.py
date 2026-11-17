import json
import os
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.adapters.host_monitor_adapter import HostMonitorAdapter
from app.adapters.nvml_adapter import NvmlAdapter
from app.services.host_registry import HostRegistry, compute_freshness


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class TelemetrySafetyTests(unittest.TestCase):
    @staticmethod
    def _gateway_snapshot():
        observed_at = datetime.now(timezone.utc).isoformat()
        return {
            "node_id": "workstation",
            "status": "ONLINE",
            "host_received_at": observed_at,
            "gpu_received_at": observed_at,
            "host": {
                "node_id": "workstation", "status": "online", "is_real_host": True,
                "collected_at": observed_at, "ip_address": "192.0.2.251",
                "disk_type": "ext4", "hostname": "test-workstation",
                "cpu_model": "test CPU", "cpu_cores": 8, "cpu_usage_percent": 5.0,
                "ram_total_bytes": 16 * 1024**3, "ram_used_bytes": 4 * 1024**3,
                "ram_usage_percent": 25.0, "disk_total_bytes": 2 * 1024**4,
                "disk_used_bytes": 1024**4, "disk_usage_percent": 50.0,
                "uptime_seconds": 3600, "net_rx_bytes_sec": 0, "net_tx_bytes_sec": 0,
            },
            "gpu": {
                "node_id": "workstation", "status": "VERIFIED",
                "collected_at": observed_at, "gpu_count": 1,
                "gpus": [{
                    "index": 0, "name": "NVIDIA A30", "uuid": "GPU-test-0",
                    "pci_bus_id": "0000:01:00.0", "memory_total_mb": 24576,
                    "memory_used_mb": 1024, "gpu_utilization_percent": 5.0,
                    "memory_utilization_percent": 4.0, "temperature_celsius": 40,
                    "power_usage_watts": 50.0, "power_limit_watts": 165.0,
                    "enforcement_state": "MONITOR_ONLY", "processes": [],
                }],
            },
        }

    def _query_gateway_snapshot(self, snapshot):
        adapter = HostMonitorAdapter(local_host_id="track")
        with patch.dict(os.environ, {
            "LABOPS_GATEWAY_HTTP_URL": "http://127.0.0.1:9444",
            "LABOPS_GATEWAY_CONTROL_SECRET": "test-control-secret-0123456789abcdef",
        }, clear=True), patch(
            "app.adapters.host_monitor_adapter.urllib.request.urlopen",
            return_value=FakeResponse(snapshot),
        ):
            return adapter._query_gateway_node_telemetry("workstation")

    def test_local_host_agent_is_not_called_without_configured_token(self):
        adapter = HostMonitorAdapter(local_host_id="track")
        with patch.dict(os.environ, {}, clear=True), patch("app.adapters.host_monitor_adapter.urllib.request.urlopen") as urlopen:
            self.assertIsNone(adapter._fetch_go_agent_telemetry("track", "Rack", "Lab"))
        urlopen.assert_not_called()

    def test_gateway_telemetry_does_not_fallback_to_user_session_secrets(self):
        adapter = HostMonitorAdapter(local_host_id="workstation")
        env = {
            "LABOPS_SESSION_SECRET": "user-session-secret",
            "LABOPS_ADMIN_API_TOKEN": "legacy-admin-token",
        }
        with patch.dict(os.environ, env, clear=True), patch("app.adapters.host_monitor_adapter.urllib.request.urlopen") as urlopen:
            self.assertIsNone(adapter._query_gateway_node_telemetry("track"))
        urlopen.assert_not_called()

    def test_gpu_agent_telemetry_requires_configured_token(self):
        adapter = NvmlAdapter(server_id="track")
        with patch.dict(os.environ, {}, clear=True), patch("app.adapters.nvml_adapter.urllib.request.urlopen") as urlopen:
            self.assertIsNone(adapter._fetch_go_agent_gpus())
        urlopen.assert_not_called()

    def test_gpu_telemetry_missing_uuid_is_not_filled_with_fake_identity(self):
        adapter = NvmlAdapter(server_id="track")
        body = {
            "status": "VERIFIED",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "gpus": [{"index": 0, "name": "NVIDIA A30", "memory_total_mb": 24576}],
        }
        with patch.dict(os.environ, {"LABOPS_NODE_AGENT_TOKEN": "test-agent-token"}, clear=True), \
             patch("app.adapters.nvml_adapter.urllib.request.urlopen", return_value=FakeResponse(body)):
            self.assertIsNone(adapter._fetch_go_agent_gpus())

    def test_verified_gpu_uses_observed_node_and_uuid(self):
        adapter = NvmlAdapter(server_id="workstation")
        collected_at = datetime.now(timezone.utc).isoformat()
        body = {
            "status": "VERIFIED", "count": 1, "collected_at": collected_at,
            "gpus": [{
                "index": 2, "uuid": "GPU-test-uuid", "name": "NVIDIA Test GPU",
                "pci_bus_id": "0000:01:00.0", "memory_total_mb": 1000,
                "memory_used_mb": 250, "utilization_gpu_pct": 12,
                "temperature_c": 41, "power_watts": 30, "power_limit_watts": 100,
                "active_processes": [{"pid": 123, "name": "train.py", "used_memory_mb": 250}],
            }],
        }
        with patch.dict(os.environ, {"LABOPS_NODE_AGENT_TOKEN": "test-agent-token"}, clear=True), \
             patch("app.adapters.nvml_adapter.urllib.request.urlopen", return_value=FakeResponse(body)) as urlopen:
            result = adapter._fetch_go_agent_gpus()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("X-labops-agent-secret"), "test-agent-token")
        self.assertEqual(result[0]["id"], "workstation-gpu-02")
        self.assertEqual(result[0]["uuid"], "GPU-test-uuid")
        self.assertEqual(result[0]["temp_celsius"], 41)
        self.assertEqual(result[0]["allocation_mode"], "OBSERVED_ONLY")
        self.assertEqual(result[0]["active_processes"][0]["username"], "UNATTRIBUTED_EXTERNAL")

    def test_duplicate_gpu_index_is_rejected_even_when_uuids_differ(self):
        adapter = NvmlAdapter(server_id="workstation")
        collected_at = datetime.now(timezone.utc).isoformat()
        first = {
            "index": 0, "uuid": "GPU-test-uuid-0", "name": "NVIDIA Test GPU",
            "pci_bus_id": "0000:01:00.0", "memory_total_mb": 1000,
            "memory_used_mb": 250, "utilization_gpu_pct": 12,
            "temperature_c": 41, "power_watts": 30, "power_limit_watts": 100,
            "active_processes": [],
        }
        second = {**first, "uuid": "GPU-test-uuid-1", "pci_bus_id": "0000:02:00.0"}
        body = {"status": "VERIFIED", "count": 2, "collected_at": collected_at, "gpus": [first, second]}
        with patch.dict(os.environ, {"LABOPS_NODE_AGENT_TOKEN": "test-agent-token"}, clear=True), \
             patch("app.adapters.nvml_adapter.urllib.request.urlopen", return_value=FakeResponse(body)):
            self.assertIsNone(adapter._fetch_go_agent_gpus())

    def test_container_local_metrics_are_not_reported_as_physical_host(self):
        adapter = HostMonitorAdapter(local_host_id="track")
        adapter.is_containerized = True
        with patch.object(adapter, "_fetch_go_agent_telemetry", return_value=None), \
             patch.object(adapter, "_get_cpu_model") as cpu_model:
            result = adapter._build_local_telemetry("track", "Rack", "Lab")
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["is_real_host"])
        cpu_model.assert_not_called()

    def test_new_host_registry_does_not_invent_online_heartbeats(self):
        with tempfile.TemporaryDirectory(prefix="labops-host-registry-test-") as data_dir:
            registry = HostRegistry(data_dir, local_host_id="track")
            hosts = registry.list_hosts()
        self.assertTrue(all(host["observed_state"] == "UNKNOWN" for host in hosts))
        self.assertTrue(all(host["last_heartbeat_at"] is None for host in hosts))

    def test_host_registry_rejects_symlinked_and_malformed_state(self):
        with tempfile.TemporaryDirectory(prefix="labops-host-registry-test-") as data_dir:
            registry = HostRegistry(data_dir, local_host_id="track")
            outside = Path(data_dir) / "outside.json"
            outside.write_text('{"hosts": [], "reconciled_at": null}', encoding="utf-8")
            state = Path(registry.state_path)
            state.unlink()
            state.symlink_to(outside)
            with self.assertRaises((OSError, ValueError, RuntimeError)):
                registry.list_hosts()

            state.unlink()
            state.write_text('{"hosts": "malformed"}', encoding="utf-8")
            with self.assertRaises((ValueError, RuntimeError)):
                registry.list_hosts()

    def test_future_heartbeat_is_unknown(self):
        future = (datetime.now(timezone.utc).replace(year=datetime.now(timezone.utc).year + 1)).isoformat()
        self.assertEqual(compute_freshness(future), "UNKNOWN")

    def test_gateway_telemetry_requires_fresh_gateway_receipt(self):
        snapshot = self._gateway_snapshot()
        snapshot["host_received_at"] = "2000-01-01T00:00:00+00:00"
        self.assertIsNone(self._query_gateway_snapshot(snapshot))

    def test_gateway_telemetry_rejects_mismatched_gpu_node(self):
        snapshot = self._gateway_snapshot()
        snapshot["gpu"]["node_id"] = "track"
        self.assertIsNone(self._query_gateway_snapshot(snapshot))

    def test_gateway_telemetry_rejects_unverified_host_and_duplicate_gpu_identity(self):
        snapshot = self._gateway_snapshot()
        snapshot["host"]["is_real_host"] = False
        self.assertIsNone(self._query_gateway_snapshot(snapshot))

        duplicate = deepcopy(self._gateway_snapshot())
        second_gpu = dict(duplicate["gpu"]["gpus"][0], index=1)
        duplicate["gpu"]["gpus"].append(second_gpu)
        duplicate["gpu"]["gpu_count"] = 2
        self.assertIsNone(self._query_gateway_snapshot(duplicate))


if __name__ == "__main__":
    unittest.main()
