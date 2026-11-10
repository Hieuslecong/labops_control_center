import base64
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from unittest.mock import patch

from app.adapters.wireguard_adapter import WireGuardAdapter, WireGuardError
from app.services.labops_service import LabOpsDataService


def key(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


SERVER = {
    "workstation": {
        "server_public_key": key(bytes(range(1, 33))),
        "server_endpoint": "vpn.example.org:51820",
        "server_vpn_ip": "10.66.66.1",
        "vpn_subnet": "10.66.66.0/24",
        "client_allowed_ips": ["10.66.66.5/32"],
        "allowed_subnets": "10.66.66.0/24",
        "interface": "wg0",
    }
}


class TestWireGuardProvisioning(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = WireGuardAdapter(
            interface="wg0", data_dir=self.temp.name, server_configs=SERVER
        )
        self.adapter.has_wg = True

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_invalid_or_unknown_server_identity(self):
        with self.assertRaises(WireGuardError):
            self.adapter.allocate_vpn_ip("track")
        with self.assertRaises(WireGuardError):
            self.adapter.register_peer("u1", "not-a-key", "10.66.66.10", "ws1", "workstation")

    def test_wg_set_failure_does_not_persist_peer(self):
        public_key = key(bytes(range(33, 65)))
        show = CompletedProcess([], 0, "", "")
        failure = CalledProcessError(1, ["wg", "set"], stderr="permission denied")
        empty_dump = CompletedProcess([], 0, "private\tserver\t51820\toff\n", "")
        with patch("app.adapters.wireguard_adapter.subprocess.run", side_effect=[show, empty_dump, failure]):
            with self.assertRaises(WireGuardError):
                self.adapter.register_peer("u1", "device-1", public_key, "workstation")
        self.assertEqual(self.adapter.peers, {})
        self.assertFalse(Path(self.adapter.peers_file).exists())

    def test_success_returns_public_metadata_only_after_live_peer_check(self):
        public_key = key(bytes(range(33, 65)))
        interface = CompletedProcess([], 0, "wg0\n", "")
        empty_dump = CompletedProcess([], 0, "private\tserver\t51820\toff\n", "")
        applied = CompletedProcess([], 0, "", "")
        dump = "private\tserver\t51820\toff\n" + f"{public_key}\t(none)\t(none)\t10.66.66.2/32\t0\t0\t0\t25\n"
        verified = CompletedProcess([], 0, dump, "")
        with patch("app.adapters.wireguard_adapter.subprocess.run", side_effect=[interface, empty_dump, applied, verified]):
            peer = self.adapter.register_peer("u1", "device-1", public_key, "workstation")

        self.assertEqual(peer["status"], "PENDING_HANDSHAKE")
        self.assertEqual(peer["public_key"], public_key)
        self.assertEqual(peer["client_ip"], "10.66.66.2")
        self.assertNotIn("private_key", peer)
        self.assertNotIn("config_content", peer)
        saved = json.loads(Path(self.adapter.peers_file).read_text())
        self.assertNotIn("private_key", json.dumps(saved))
        self.assertNotIn("config_content", json.dumps(saved))

    def test_duplicate_key_and_ip_are_rejected(self):
        public_key = key(bytes(range(33, 65)))
        self.adapter.peers["device-1"] = {
            "username": "u1", "device_id": "device-1", "public_key": public_key,
            "client_ip": "10.66.66.10", "server_id": "workstation", "status": "ACTIVE"
        }
        self.adapter._save_peers()
        with self.assertRaises(WireGuardError):
            self.adapter.register_peer("u2", "device-2", public_key, "workstation")
        self.adapter.peers["device-1"]["client_ip"] = "10.66.66.2"
        self.assertEqual(self.adapter.allocate_vpn_ip("workstation"), "10.66.66.3")

    def test_ipam_skips_addresses_already_present_on_live_interface(self):
        self.assertEqual(
            self.adapter.allocate_vpn_ip("workstation", live_addresses=["10.66.66.2/32"]),
            "10.66.66.3",
        )

    def test_service_creates_pending_user_without_ssh_password_or_vpn_secret(self):
        with tempfile.TemporaryDirectory() as data_dir:
            service = LabOpsDataService(demo_mode=True, data_dir=data_dir)
            member = service.provision_member({
                "full_name": "Test Researcher", "username": "secure_test", "email": "test@example.org",
                "assigned_server": "workstation", "workspace_name": "ws-secure_test",
                "storage_quota_gb": 20, "cpu_limit_cores": 2, "ram_limit_gb": 4,
            })
        self.assertEqual(member["status"], "PENDING_PROVISIONING")
        self.assertIsNone(member["ssh_port"])
        self.assertEqual(member["password_status"], "NOT_APPLICABLE")
        self.assertEqual(member["wireguard"]["status"], "NOT_CONFIGURED")
        self.assertNotIn("config_content", member["wireguard"])
        self.assertNotIn("initial_password", member)

    def test_device_enrollment_stays_blocked_without_verified_workspace_and_network_policy(self):
        with tempfile.TemporaryDirectory() as data_dir:
            service = LabOpsDataService(demo_mode=True, data_dir=data_dir)
            member = service.provision_member({
                "full_name": "Test Researcher", "username": "secure_test", "email": "test@example.org",
                "assigned_server": "workstation", "workspace_name": "ws-secure_test",
                "storage_quota_gb": 20, "cpu_limit_cores": 2, "ram_limit_gb": 4,
            })
            with self.assertRaisesRegex(RuntimeError, "Workspace provisioning"):
                service.register_wireguard_device(member["id"], key(bytes(range(33, 65))), "laptop")

            member["workspace_status"] = "VERIFIED"
            with self.assertRaisesRegex(RuntimeError, "network policy"):
                service.register_wireguard_device(member["id"], key(bytes(range(33, 65))), "laptop")


if __name__ == "__main__":
    unittest.main()
