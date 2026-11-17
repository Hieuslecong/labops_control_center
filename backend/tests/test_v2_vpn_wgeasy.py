"""Safe wg-easy regression tests. All provider calls are mocked; no service starts."""
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
test_config = types.ModuleType("app.core.config")
test_config.settings = types.SimpleNamespace()
with patch.dict(sys.modules, {"app.core.config": test_config}):
    adapter_module = importlib.import_module("app.adapters.wg_easy_adapter")

WgEasyAdapter = adapter_module.WgEasyAdapter
VPNProviderError = adapter_module.VPNProviderError


class WgEasyV2RegressionTests(unittest.TestCase):
    def test_missing_admin_credentials_fail_closed(self):
        adapter = WgEasyAdapter(username="", password="")
        with self.assertRaises(VPNProviderError):
            adapter._auth_header()

    def test_cidr_mismatch_is_read_only(self):
        adapter = WgEasyAdapter(username="test", password="test", vpn_subnet="10.88.40.0/24")
        with patch.object(adapter, "_request", return_value={"ipv4Cidr": "10.9.0.0/24"}) as request:
            adapter.ensure_subnet_configured()
        request.assert_called_once_with("api/admin/interface", method="GET")

    def test_create_is_not_success_until_device_is_observed_enabled(self):
        adapter = WgEasyAdapter(username="test", password="test")
        with patch.object(adapter, "ensure_subnet_configured"), \
             patch.object(adapter, "_request", return_value={"clientId": "client-1"}), \
             patch.object(adapter, "get_device", return_value={"id": "client-1", "enabled": False}):
            with self.assertRaises(VPNProviderError):
                adapter.create_device("fixture-device")

    def test_revoke_is_not_success_until_device_is_absent(self):
        adapter = WgEasyAdapter(username="test", password="test")
        with patch.object(adapter, "_request", side_effect=[{}, [{"id": "client-1", "enabled": True}]]):
            with self.assertRaises(VPNProviderError):
                adapter.revoke_device("client-1")

    def test_configuration_endpoint_is_preserved_and_private_key_not_logged(self):
        adapter = WgEasyAdapter(username="test", password="test", public_endpoint="configured.example:2204")
        config_text = "[Interface]\nPrivateKey = fixture-private-key\n[Peer]\nEndpoint = provider.example:2205\n"
        with patch.object(adapter, "_request", return_value=config_text):
            self.assertEqual(adapter.get_client_configuration("client-1"), config_text)


if __name__ == "__main__":
    unittest.main()
