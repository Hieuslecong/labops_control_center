"""Offline wg-easy unit tests; no application configuration or network is loaded."""
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
config = types.ModuleType('app.core.config')
config.settings = types.SimpleNamespace()
with patch.dict(sys.modules, {'app.core.config': config}):
    adapter_module = importlib.import_module('app.adapters.wg_easy_adapter')

WgEasyAdapter = adapter_module.WgEasyAdapter
VPNProviderError = adapter_module.VPNProviderError


class WgEasySafetyTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WgEasyAdapter(username='test', password='test')

    def test_missing_credentials(self):
        self.adapter.password = ''
        with self.assertRaises(VPNProviderError):
            self.adapter._auth_header()

    def test_admin_username_is_not_defaulted(self):
        adapter = WgEasyAdapter(username='', password='secret')
        self.assertEqual(adapter.username, '')
        with self.assertRaises(VPNProviderError):
            adapter._auth_header()

    def test_invalid_device_list_fails_closed(self):
        with patch.object(self.adapter, '_request', return_value={'error': 'failed'}):
            with self.assertRaises(VPNProviderError):
                self.adapter.list_devices()

    def test_subnet_mismatch_only_reads(self):
        with patch.object(self.adapter, '_request', return_value={'ipv4Cidr': '10.9.0.0/24'}) as request:
            self.adapter.ensure_subnet_configured()
        request.assert_called_once_with('api/admin/interface', method='GET')

    def test_configuration_endpoint_is_unchanged(self):
        config_text = '[Interface]\nPrivateKey = fixture\n[Peer]\nEndpoint = example.test:42\n'
        with patch.object(self.adapter, '_request', return_value=config_text):
            self.assertEqual(self.adapter.get_client_configuration('id'), config_text)

    def test_unverified_create_fails(self):
        with patch.object(self.adapter, 'ensure_subnet_configured'), patch.object(self.adapter, '_request', return_value={'clientId': 'id'}), patch.object(self.adapter, 'get_device', return_value=None):
            with self.assertRaises(VPNProviderError):
                self.adapter.create_device('device')

    def test_list_rejects_missing_identity_or_enabled_state(self):
        for body in ([{'enabled': True}], [{'id': 'id'}], [{'id': '', 'enabled': True}], ['bad']):
            with self.subTest(body=body), patch.object(self.adapter, '_request', return_value=body):
                with self.assertRaises(VPNProviderError):
                    self.adapter.list_devices()

    def test_list_rejects_duplicate_provider_ids(self):
        body = [{'id': 'id', 'enabled': True}, {'id': 'id', 'enabled': False}]
        with patch.object(self.adapter, '_request', return_value=body):
            with self.assertRaises(VPNProviderError):
                self.adapter.list_devices()

    def test_disable_requires_verified_disabled_state(self):
        with patch.object(self.adapter, '_request', side_effect=[{}, [{'id': 'id', 'enabled': True}]]):
            with self.assertRaises(VPNProviderError):
                self.adapter.disable_device('id')

    def test_disable_accepts_verified_disabled_state(self):
        with patch.object(self.adapter, '_request', side_effect=[{}, [{'id': 'id', 'enabled': False}]]):
            self.assertTrue(self.adapter.disable_device('id'))

    def test_revoke_requires_verified_absence(self):
        with patch.object(self.adapter, '_request', side_effect=[{}, [{'id': 'id', 'enabled': True}]]):
            with self.assertRaises(VPNProviderError):
                self.adapter.revoke_device('id')

    def test_revoke_accepts_verified_absence(self):
        with patch.object(self.adapter, '_request', side_effect=[{}, []]):
            self.assertTrue(self.adapter.revoke_device('id'))

    def test_device_id_cannot_inject_api_path(self):
        with patch.object(self.adapter, '_request') as request:
            with self.assertRaises(VPNProviderError):
                self.adapter.revoke_device('../admin/interface')
        request.assert_not_called()

    def test_status_does_not_claim_container_runtime_or_version(self):
        with patch.object(self.adapter, '_request', side_effect=[
            {'ipv4Cidr': '10.88.40.0/24', 'publicKey': 'observed-public-key'},
            [],
        ]):
            status = self.adapter.get_connection_status()
        self.assertIsNone(status['container_running'])
        self.assertNotIn('version', status)


if __name__ == '__main__':
    unittest.main()
