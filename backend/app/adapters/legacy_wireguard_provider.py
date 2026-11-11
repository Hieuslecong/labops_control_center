"""
LegacyWireGuardProvider: Bộ điều hợp VPN WireGuard host (wg0, cổng 2204, subnet 10.88.30.0/24).
CHẾ ĐỘ READ-ONLY: Duy trì chỉ để kiểm tra trạng thái và tương thích kiểm toán lịch sử.
Mọi thao tác cấp phát mới (provisioning/mutations) đều bị khóa và chuyển hoàn toàn sang wg-easy.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.adapters.vpn_provider import VPNProvider, VPNProviderError
from app.adapters.wireguard_adapter import WireGuardAdapter


class LegacyWireGuardProvider(VPNProvider):
    def __init__(self, adapter: Optional[WireGuardAdapter] = None) -> None:
        self.adapter = adapter or WireGuardAdapter(interface="wg0")

    @property
    def name(self) -> str:
        return "legacy-wg0"

    def list_devices(self) -> List[Dict[str, Any]]:
        devs = []
        for pubkey, data in self.adapter.peers.items():
            devs.append({
                "id": pubkey,
                "provider": self.name,
                "name": data.get("client_name", "Legacy Device"),
                "vpn_ip": data.get("client_ip", ""),
                "public_key": pubkey,
                "status": "ACTIVE",
                "enabled": True,
                "last_handshake": None,
                "transfer_rx": 0,
                "transfer_tx": 0,
                "created_at": data.get("enrolled_at"),
            })
        return devs

    def create_device(
        self,
        name: str,
        expires_at: Optional[str] = None,
        public_key: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise VPNProviderError(
            "LEGACY_VPN_READ_ONLY: Provisioning trên host WireGuard cũ đã bị vô hiệu hóa hoàn toàn. "
            "wg-easy là nguồn quản lý VPN duy nhất (Single Source of Truth)."
        )

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        data = self.adapter.peers.get(device_id)
        if not data:
            return None
        return {
            "id": device_id,
            "provider": self.name,
            "name": data.get("client_name", "Legacy Device"),
            "vpn_ip": data.get("client_ip", ""),
            "public_key": device_id,
            "status": "ACTIVE",
            "enabled": True,
        }

    def disable_device(self, device_id: str) -> bool:
        raise VPNProviderError("LEGACY_VPN_READ_ONLY: Không thể thay đổi trạng thái peer legacy qua API này.")

    def enable_device(self, device_id: str) -> bool:
        raise VPNProviderError("LEGACY_VPN_READ_ONLY: Không thể thay đổi trạng thái peer legacy qua API này.")

    def revoke_device(self, device_id: str) -> bool:
        raise VPNProviderError("LEGACY_VPN_READ_ONLY: Thu hồi peer trên host cũ yêu cầu kịch bản cutover có phê duyệt.")

    def get_connection_status(self) -> Dict[str, Any]:
        wg0_up = os.path.exists("/sys/class/net/wg0")
        return {
            "provider": self.name,
            "service_health": "READ_ONLY_ACTIVE" if wg0_up else "OFFLINE",
            "status": "READ_ONLY" if wg0_up else "INACTIVE",
            "interface_name": "wg0 (Host legacy - Read Only)",
            "public_endpoint": "118.70.169.236:2204",
            "vpn_subnet": "10.88.30.0/24",
            "total_clients": len(self.adapter.peers),
            "active_handshakes": 0,
            "total_rx_bytes": 0,
            "total_tx_bytes": 0,
        }

    def get_traffic_statistics(self) -> Dict[str, Any]:
        return {"total_rx_bytes": 0, "total_tx_bytes": 0}

    def get_client_configuration(self, device_id: str) -> str:
        peer = self.adapter.peers.get(device_id) or {}
        client_ip = peer.get("client_ip", "10.88.30.2")
        return (
            "[Interface]\n"
            "PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
            f"Address = {client_ip}/32\n"
            "DNS = 1.1.1.1\n\n"
            "[Peer]\n"
            "PublicKey = jf+Uw/DFyi5swclp7mhH9mamYjQgV3ulZRlxBIdqlmQ=\n"
            "Endpoint = 118.70.169.236:2204\n"
            "AllowedIPs = 10.88.30.0/24, 192.168.167.250/32\n"
        )

    def reconcile_state(self) -> Dict[str, Any]:
        return {"provider": self.name, "clients": self.list_devices()}
