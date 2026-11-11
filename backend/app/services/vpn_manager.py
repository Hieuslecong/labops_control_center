"""
VPNManager: Bộ điều phối dịch vụ VPN WireGuard cho LabOps Control Center.
Quản lý các VPN Provider (wg-easy v15 và legacy wg0), hỗ trợ multi-device cho từng thành viên.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.adapters.legacy_wireguard_provider import LegacyWireGuardProvider
from app.adapters.vpn_provider import VPNProvider, VPNProviderError
from app.adapters.wg_easy_adapter import WgEasyAdapter
from app.core.config import settings

logger = logging.getLogger(__name__)


class VPNManager:
    def __init__(
        self,
        wg_easy: Optional[WgEasyAdapter] = None,
        legacy: Optional[LegacyWireGuardProvider] = None,
    ) -> None:
        self.wg_easy = wg_easy or WgEasyAdapter()
        self.legacy = legacy or LegacyWireGuardProvider()
        self.providers: Dict[str, VPNProvider] = {
            self.wg_easy.name: self.wg_easy,
            self.legacy.name: self.legacy,
        }

    @property
    def primary_provider(self) -> VPNProvider:
        """wg-easy là Single Source of Truth duy nhất cho VPN."""
        return self.wg_easy

    def get_provider(self, name: Optional[str] = None) -> VPNProvider:
        if name and name == "legacy-wg0":
            return self.legacy
        return self.wg_easy

    def get_vpn_system_overview(self) -> Dict[str, Any]:
        """Tổng hợp trạng thái toàn bộ hệ sinh thái VPN (wg-easy và legacy wg0)."""
        wg_easy_status = self.wg_easy.get_connection_status()
        legacy_status = self.legacy.get_connection_status()
        active_provider = self.primary_provider.name
        return {
            "primary_provider": active_provider,
            "wg_easy": wg_easy_status,
            "legacy_wg0": legacy_status,
            "total_devices": wg_easy_status.get("total_clients", 0) + legacy_status.get("total_clients", 0),
            "active_handshakes": wg_easy_status.get("active_handshakes", 0),
            "total_rx_bytes": wg_easy_status.get("total_rx_bytes", 0),
            "total_tx_bytes": wg_easy_status.get("total_tx_bytes", 0),
        }

    def create_member_device(
        self,
        username: str,
        device_name: str,
        expires_at: Optional[str] = None,
        provider_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Tạo một thiết bị WireGuard cho thành viên qua VPN provider."""
        provider = self.get_provider(provider_name)
        full_name = f"{username}-{device_name}".strip("-")
        device = provider.create_device(name=full_name, expires_at=expires_at)
        device["member_username"] = username
        device["custom_name"] = device_name
        return device

    def get_device_configuration(self, device_id: str, provider_name: Optional[str] = None) -> str:
        """Lấy nội dung tệp .conf của thiết bị."""
        provider = self.get_provider(provider_name)
        return provider.get_client_configuration(device_id)

    def disable_device(self, device_id: str, provider_name: Optional[str] = None) -> bool:
        provider = self.get_provider(provider_name)
        return provider.disable_device(device_id)

    def enable_device(self, device_id: str, provider_name: Optional[str] = None) -> bool:
        provider = self.get_provider(provider_name)
        return provider.enable_device(device_id)

    def revoke_device(self, device_id: str, provider_name: Optional[str] = None) -> bool:
        provider = self.get_provider(provider_name)
        return provider.revoke_device(device_id)

    def get_server_public_key(self, provider_name: Optional[str] = None) -> str:
        provider = self.get_provider(provider_name)
        try:
            status = provider.get_connection_status()
            if status.get("server_public_key"):
                return status["server_public_key"]
        except Exception:
            pass
        return getattr(settings, "LABOPS_WG_SERVER_PUBLIC_KEY", "jf+Uw/DFyi5swclp7mhH9mamYjQgV3ulZRlxBIdqlmQ=")


