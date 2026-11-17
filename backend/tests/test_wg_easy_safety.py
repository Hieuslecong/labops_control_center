"""wg-easy safety tests: no network, Docker, or production filesystem access."""
from unittest.mock import patch

import pytest

from app.adapters.vpn_provider import VPNProviderError
from app.adapters.wg_easy_adapter import WgEasyAdapter


def test_missing_credentials_fail_closed():
    adapter = WgEasyAdapter(username='', password='')
    adapter.username = ''
    adapter.password = ''
    with pytest.raises(VPNProviderError):
        adapter._auth_header()


def test_disabled_provider_never_makes_an_api_request():
    adapter = WgEasyAdapter(username='test', password='test')
    adapter.enabled = False
    with patch('app.adapters.wg_easy_adapter.urlopen') as urlopen:
        with pytest.raises(VPNProviderError):
            adapter.list_devices()
    urlopen.assert_not_called()


def test_subnet_mismatch_does_not_mutate_network():
    adapter = WgEasyAdapter(username='test', password='test', vpn_subnet='10.88.40.0/24')
    with patch.object(adapter, '_request', return_value={'ipv4Cidr': '10.9.0.0/24'}) as request:
        adapter.ensure_subnet_configured()
    request.assert_called_once_with('api/admin/interface', method='GET')


def test_client_configuration_preserves_provider_endpoint():
    adapter = WgEasyAdapter(username='test', password='test', public_endpoint='wrong.example:2204')
    config = '[Interface]\nPrivateKey = test\n[Peer]\nEndpoint = actual.example:2205\n'
    with patch.object(adapter, '_request', return_value=config):
        assert adapter.get_client_configuration('client-id') == config


def test_create_without_observed_client_does_not_claim_success():
    adapter = WgEasyAdapter(username='test', password='test')
    with patch.object(adapter, 'ensure_subnet_configured'), \
         patch.object(adapter, '_request', return_value={'clientId': 'id'}), \
         patch.object(adapter, 'get_device', return_value=None):
        with pytest.raises(VPNProviderError):
            adapter.create_device('device')
