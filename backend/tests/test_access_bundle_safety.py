import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from app.services.ssh_access_manager import SshAccessManager
from app.services.vpn_config import VPNConfigError, parse_provider_vpn_config
from app.services.labops_service import LabOpsDataService


class AccessBundleSafetyTests(unittest.TestCase):
    def test_access_bundle_refuses_unverified_ssh_before_vpn_lookup(self):
        with tempfile.TemporaryDirectory(prefix="labops-access-bundle-") as data_dir:
            service = LabOpsDataService(demo_mode=True, data_dir=data_dir)
            member = {
                "id": "member-pending", "username": "alice", "assigned_server": "track",
                "workspace_name": "ws-alice", "workspace_status": "NOT_VERIFIED",
                "ssh_status": "PENDING_RUNTIME_VERIFICATION", "ssh_ip": None, "ssh_port": 22400,
            }
            service.members = [member]
            with patch.object(service, "get_member_devices_synced") as devices:
                with self.assertRaisesRegex(RuntimeError, "SSH runtime endpoint is not verified"):
                    service.claim_member_access_zip("member-pending", actor="alice")
            devices.assert_not_called()

    def test_missing_provider_config_fails_closed(self):
        with self.assertRaises(VPNConfigError):
            parse_provider_vpn_config(None)

    def test_provider_endpoint_and_client_address_are_taken_from_issued_config(self):
        config = (
            "[Interface]\nPrivateKey = fixture-private-key\nAddress = 10.9.0.7/32\n"
            "[Peer]\nPublicKey = fixture-server-key\nEndpoint = vpn.example.test:51820\n"
            "AllowedIPs = 10.9.0.0/24\n"
        )
        parsed = parse_provider_vpn_config(config)
        self.assertEqual(parsed.endpoint, "vpn.example.test:51820")
        self.assertEqual(parsed.client_address, "10.9.0.7/32")
        self.assertEqual(parsed.config, config)

    def test_incomplete_config_is_not_completed_with_defaults(self):
        config = "[Interface]\nPrivateKey = fixture-private-key\nAddress = 10.9.0.7/32\n[Peer]\n"
        with self.assertRaises(VPNConfigError):
            parse_provider_vpn_config(config)

    def test_server_public_key_helper_does_not_generate_or_persist_fallback_keys(self):
        with tempfile.TemporaryDirectory(prefix="labops-vpn-key-test-") as data_dir, \
             patch("app.services.ssh_access_manager.shutil.which", return_value=None), \
             patch("app.services.ssh_access_manager.os.urandom", return_value=b"x" * 32):
            with self.assertRaisesRegex(RuntimeError, "server-generated WireGuard keys are disabled"):
                SshAccessManager._get_or_create_server_wg_public_key(data_dir)
            self.assertFalse(os.path.exists(os.path.join(data_dir, "wireguard_server_keys.json")))

    def test_server_generated_client_profile_is_disabled(self):
        valid_fixture_key = base64.b64encode(b"s" * 32).decode("ascii")
        with patch("app.services.ssh_access_manager.shutil.which", return_value=None), \
             patch("app.services.ssh_access_manager.os.urandom", return_value=b"c" * 32):
            with self.assertRaisesRegex(RuntimeError, "server-generated WireGuard client profiles are disabled"):
                SshAccessManager.generate_wireguard_client_profile(
                    username="alice", client_ip="10.9.0.7", server_endpoint="vpn.example.test:51820",
                    server_public_key=valid_fixture_key,
                )

    def test_access_bundle_requires_provider_config_and_preserves_its_endpoint(self):
        args = {
            "username": "alice", "ssh_host": "workspace.example.test", "ssh_port": 1911,
            "workspace_name": "alice-workspace", "public_key": "ssh-ed25519 fixture",
        }
        with self.assertRaises(VPNConfigError):
            SshAccessManager.build_access_zip_package(**args)

        provider_config = (
            "[Interface]\nPrivateKey = fixture-private-key\nAddress = 10.9.0.7/32\n"
            "[Peer]\nPublicKey = fixture-server-key\nEndpoint = issued.example.test:51820\n"
            "AllowedIPs = 10.9.0.0/24\n"
        )
        package = SshAccessManager.build_access_zip_package(
            **args, wireguard_conf=provider_config,
            wireguard_client_ip="10.88.40.50", wireguard_endpoint="wrong.example.test:2204",
        )
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            info = json.loads(archive.read("LabOps_Access_alice/connection/connection-info.json"))
            included_config = archive.read("LabOps_Access_alice/wireguard/labops.conf").decode()
        self.assertEqual(info["wireguard_endpoint"], "issued.example.test:51820")
        self.assertEqual(info["wireguard_client_ip"], "10.9.0.7/32")
        self.assertEqual(included_config, provider_config)


if __name__ == "__main__":
    unittest.main()
