"""
VPNManager: Bộ điều phối dịch vụ VPN WireGuard duy nhất cho LabOps Control Center.

Tuân thủ nghiêm ngặt Yêu cầu 1 và Yêu cầu 2 của Master Prompt V2:
- CHỈ GIỮ DUY NHẤT DOCKER WG-EASY làm VPN Gateway và Single Source of Truth cho peer management.
- Loại bỏ hoàn toàn cơ chế quản lý legacy wg0 host và LegacyWireGuardProvider khỏi runtime path.
- Tích hợp VPNRouteResolver và WireGuardConfigBuilder để tạo cấu hình động chính xác theo máy chủ được cấp.
- Không fallback sang legacy khi wg-easy offline; trả VPNProviderError("VPN_PROVIDER_UNAVAILABLE").
- Không trả về public key hoặc endpoint mặc định dạng hardcode khi API lỗi.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.adapters.vpn_provider import VPNProvider, VPNProviderError
from app.adapters.wg_easy_adapter import WgEasyAdapter
from app.core.config import settings
from app.services.vpn_route_resolver import HostRegistry, VPNRouteResolver
from app.services.wireguard_config_builder import WireGuardConfigBuilder

logger = logging.getLogger(__name__)


class VPNManager:
    """Quản trị viên VPN duy nhất của LabOps dựa trên wg-easy v15."""

    def __init__(
        self,
        wg_easy: Optional[WgEasyAdapter] = None,
        host_registry: Optional[HostRegistry] = None,
    ) -> None:
        self.wg_easy = wg_easy or WgEasyAdapter()
        self.host_registry = host_registry or HostRegistry()
        self.route_resolver = VPNRouteResolver(host_registry=self.host_registry)
        self.config_builder = WireGuardConfigBuilder(
            wg_easy=self.wg_easy,
            route_resolver=self.route_resolver,
            host_registry=self.host_registry,
        )
        self.providers: Dict[str, VPNProvider] = {
            self.wg_easy.name: self.wg_easy,
        }

    @property
    def primary_provider(self) -> VPNProvider:
        """wg-easy là Single Source of Truth duy nhất cho VPN."""
        return self.wg_easy

    def get_provider(self, name: Optional[str] = None) -> VPNProvider:
        if name and name == "legacy-wg0":
            raise VPNProviderError(
                "LEGACY_PROVIDER_REMOVED: Cơ chế quản lý legacy wg0 trên host đã bị loại bỏ hoàn toàn khỏi LabOps. "
                "Hệ thống hiện chỉ hỗ trợ duy nhất Docker wg-easy VPN Gateway."
            )
        return self.wg_easy

    def get_vpn_system_overview(self) -> Dict[str, Any]:
        """Tổng hợp trạng thái của hệ thống VPN wg-easy duy nhất."""
        wg_easy_status = self.wg_easy.get_connection_status()
        active_provider = self.primary_provider.name
        svc_health = wg_easy_status.get("service_health", "UNAVAILABLE")
        if svc_health == "HEALTHY":
            overview_status = "HEALTHY"
        elif svc_health == "VPN_API_HEALTHY":
            overview_status = "VPN_API_HEALTHY"
        elif svc_health == "VPN_ROUTING_MISCONFIGURED":
            overview_status = "VPN_ROUTING_MISCONFIGURED"
        else:
            overview_status = "UNAVAILABLE"

        return {
            "primary_provider": active_provider,
            "active_provider": active_provider,
            "status": overview_status,
            "service_health": svc_health,
            "states": wg_easy_status.get("states", []),
            "state_flags": wg_easy_status.get("state_flags", {}),
            "routing_issues": wg_easy_status.get("routing_issues", []),
            "configured_cidr": wg_easy_status.get("configured_cidr") or self.wg_easy.vpn_subnet,
            "persisted_cidr": wg_easy_status.get("persisted_cidr") or "",
            "runtime_wg_cidr": wg_easy_status.get("runtime_wg_cidr") or "",
            "nat_subnets": wg_easy_status.get("nat_subnets", []),
            "runtime_inspection_status": wg_easy_status.get("runtime_inspection_status", "UNVERIFIED"),
            "public_endpoint": wg_easy_status.get("public_endpoint") or self.wg_easy.public_endpoint,
            "cidr": wg_easy_status.get("vpn_subnet") or self.wg_easy.vpn_subnet,
            "vpn_subnet": wg_easy_status.get("vpn_subnet") or self.wg_easy.vpn_subnet,
            "wg_easy": wg_easy_status,
            "total_clients": wg_easy_status.get("total_clients", 0),
            "total_devices": wg_easy_status.get("total_clients", 0),
            "active_clients": wg_easy_status.get("active_handshakes", 0),
            "active_handshakes": wg_easy_status.get("active_handshakes", 0),
            "ever_connected_clients": wg_easy_status.get("ever_connected_clients", 0),
            "handshake_ttl_seconds": wg_easy_status.get("handshake_ttl_seconds", 180),
            "total_rx_bytes": wg_easy_status.get("total_rx_bytes", 0),
            "total_tx_bytes": wg_easy_status.get("total_tx_bytes", 0),
            "server_public_key": wg_easy_status.get("server_public_key") or "",
            "route_policy": "PER_HOST_DYNAMIC",
        }

    def list_devices(self) -> List[Dict[str, Any]]:
        """Liệt kê toàn bộ thiết bị WireGuard từ wg-easy."""
        return self.wg_easy.list_devices()

    def create_member_device(
        self,
        username: str,
        device_name: str,
        expires_at: Optional[str] = None,
        provider_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Tạo một thiết bị WireGuard cho thành viên qua wg-easy."""
        provider = self.get_provider(provider_name)
        full_name = f"{username}-{device_name}".strip("-")
        device = provider.create_device(name=full_name, expires_at=expires_at)
        device["member_username"] = username
        device["custom_name"] = device_name
        return device

    def get_device_configuration(self, device_id: str, provider_name: Optional[str] = None) -> str:
        """Lấy nội dung tệp .conf của thiết bị từ wg-easy."""
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
        """
        Lấy khóa công khai của máy chủ wg-easy Gateway thực tế.
        Tuyệt đối không fallback về khóa mặc định giả lập khi gateway lỗi.
        """
        provider = self.get_provider(provider_name)
        status = provider.get_connection_status()
        server_key = status.get("server_public_key")
        if server_key:
            return server_key
        raise VPNProviderError(
            "VPN_PROVIDER_UNAVAILABLE: Không thể lấy khóa công khai từ wg-easy Gateway đang chạy. "
            "Kiểm tra kết nối hoặc khởi động container labops-wg-easy."
        )

    def build_dynamic_client_profile(
        self,
        *,
        actor: Dict[str, Any],
        member: Dict[str, Any],
        device: Dict[str, Any],
        workspaces: Optional[List[Dict[str, Any]]] = None,
        target_host_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sinh cấu hình WireGuard & SSH hoàn chỉnh theo đúng máy chủ được cấp."""
        return self.config_builder.build_client_profile(
            actor=actor,
            member=member,
            device=device,
            workspaces=workspaces,
            target_host_id=target_host_id,
        )
