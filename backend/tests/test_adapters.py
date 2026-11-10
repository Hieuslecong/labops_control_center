import unittest
import asyncio
import sys
import os

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.adapters.nvml_adapter import NvmlAdapter
from app.adapters.incus_adapter import IncusAdapter
from app.adapters.wireguard_adapter import WireGuardAdapter

class TestAdapters(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_nvml_adapter_track(self):
        adapter = NvmlAdapter(server_id="track", demo_mode=True)
        telemetry = self.loop.run_until_complete(adapter.get_gpu_telemetry())
        
        # Must have exactly 3 GPUs on Track
        self.assertEqual(len(telemetry), 3)
        self.assertEqual(telemetry[0]["display_label"], "TRACK-GPU-01")
        self.assertEqual(telemetry[1]["display_label"], "TRACK-GPU-02")
        self.assertEqual(telemetry[2]["display_label"], "TRACK-GPU-03")
        
        # Check VRAM is 24GB each (24576 MB)
        for gpu in telemetry:
            self.assertEqual(gpu["vram_total_mb"], 24576)

    def test_nvml_adapter_workstation(self):
        adapter = NvmlAdapter(server_id="workstation", demo_mode=True)
        telemetry = self.loop.run_until_complete(adapter.get_gpu_telemetry())
        
        # Must have exactly 1 GPU on Workstation
        self.assertEqual(len(telemetry), 1)
        self.assertEqual(telemetry[0]["display_label"], "WS-GPU-01")
        self.assertEqual(telemetry[0]["vram_total_mb"], 24576)

    def test_nvml_adapter_live_hardware(self):
        # Test real local hardware detection
        if os.getenv("LABOPS_TEST_REAL_HARDWARE") != "1":
            self.skipTest("set LABOPS_TEST_REAL_HARDWARE=1 on a verified GPU host")
        adapter = NvmlAdapter(server_id="workstation", demo_mode=False)
        telemetry = self.loop.run_until_complete(adapter.get_gpu_telemetry())
        self.assertGreater(len(telemetry), 0)
        self.assertIn("vram_total_mb", telemetry[0])
        self.assertEqual(telemetry[0]["vram_total_mb"], 24576)

    def test_incus_adapter(self):
        adapter = IncusAdapter(base_url="https://10.10.1.10:8443")
        health = self.loop.run_until_complete(adapter.check_health())
        self.assertIn(health["status"], ["online", "simulated"])
        self.assertIn("server_version", health)

        instances = self.loop.run_until_complete(adapter.list_instances())
        self.assertIsInstance(instances, list)
        self.assertGreater(len(instances), 0)

        creation = self.loop.run_until_complete(adapter.create_instance(
            name="ws-testuser",
            image="ubuntu/24.04",
            cpu_cores=8,
            ram_gb=32
        ))
        self.assertTrue(creation["success"])

    def test_wireguard_adapter(self):
        adapter = WireGuardAdapter(interface="wg0")
        status = self.loop.run_until_complete(adapter.get_peer_status())
        self.assertIn(status["status"], {"ACTIVE", "NOT_CONFIGURED"})
        self.assertIn("live", status)
        self.assertNotIn("site_to_site", status)

if __name__ == "__main__":
    unittest.main()
