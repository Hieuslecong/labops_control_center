"""
WireGuardConfigBuilder: Dịch vụ khởi tạo tệp cấu hình WireGuard (.conf) và gói truy cập SSH động.

Tuân thủ nghiêm ngặt Master Prompt V2:
- Tích hợp độc quyền với wg-easy v15.
- Cấu hình AllowedIPs được sinh động từ VPNRouteResolver theo đúng máy chủ được cấp.
- Endpoint và Server PublicKey đọc trực tiếp từ wg-easy runtime thực tế; cấm fallback mù quáng.
- Sinh companion SSH config chính xác (Admin -> Host Port 1912/1911; Researcher -> Workspace Port).
- Tuyệt đối không lưu trữ hoặc ghi plaintext private key vào log hoặc database.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.adapters.vpn_provider import VPNProviderError
from app.adapters.wg_easy_adapter import WgEasyAdapter
from app.services.vpn_route_resolver import HostRegistry, RouteResolutionError, VPNRouteResolver

logger = logging.getLogger(__name__)


class ConfigBuilderError(Exception):
    """Lỗi khi sinh cấu hình WireGuard / SSH."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.http_status = http_status


class WireGuardConfigBuilder:
    def __init__(
        self,
        wg_easy: Optional[WgEasyAdapter] = None,
        route_resolver: Optional[VPNRouteResolver] = None,
        host_registry: Optional[HostRegistry] = None,
    ):
        self.wg_easy = wg_easy or WgEasyAdapter()
        self.route_resolver = route_resolver or VPNRouteResolver(host_registry=host_registry)

    def build_client_profile(
        self,
        *,
        actor: Dict[str, Any],
        member: Dict[str, Any],
        device: Dict[str, Any],
        workspaces: Optional[List[Dict[str, Any]]] = None,
        target_host_id: Optional[str] = None,
        config_version: int = 2,
    ) -> Dict[str, Any]:
        """
        Sinh cấu hình WireGuard .conf và SSH config hoàn chỉnh cho thiết bị cụ thể của thành viên:
        - Kiểm tra quyền truy cập (Admin hoặc chính thành viên).
        - Phân giải AllowedIPs và SSH connection dựa vào máy chủ thực tế.
        - Tải cấu hình từ wg-easy và cập nhật AllowedIPs + Endpoint đã xác minh.
        """
        actor_username = str(actor.get("username") or actor.get("sub") or "")
        actor_role = str(actor.get("role") or "").upper()
        member_username = str(member.get("username") or "")
        is_admin = actor_role == "ADMIN" or actor.get("is_admin") is True

        # 1. Kiểm tra quyền sở hữu (RBAC)
        if not is_admin and actor_username != member_username:
            raise ConfigBuilderError(
                "FORBIDDEN_CROSS_USER_ACCESS",
                f"Người dùng '{actor_username}' không có quyền tải cấu hình của thành viên '{member_username}'.",
                http_status=403,
            )

        # 2. Phân giải route mạng và SSH endpoint
        try:
            resolved_route = self.route_resolver.resolve_routes_for_member(
                member=member,
                actor_role=actor_role,
                workspaces=workspaces,
                requested_host_id=target_host_id,
            )
        except RouteResolutionError as e:
            raise ConfigBuilderError(
                e.code,
                e.message,
                http_status=e.http_status,
            ) from e

        # 3. Lấy thông tin gateway và tệp .conf từ wg-easy
        ext_device_id = str(device.get("external_id") or device.get("id") or "")
        if not ext_device_id:
            raise ConfigBuilderError(
                "DEVICE_ID_MISSING",
                "Thiết bị thiếu định danh ID hợp lệ để truy vấn wg-easy.",
                http_status=400,
            )

        # Lấy trạng thái runtime gateway để kiểm tra PublicKey và Endpoint
        gw_status = self.wg_easy.get_connection_status()
        if gw_status.get("status") == "UNAVAILABLE":
            raise ConfigBuilderError(
                "VPN_PROVIDER_UNAVAILABLE",
                f"wg-easy Gateway hiện không khả dụng: {gw_status.get('error', 'Mất kết nối API')}.",
                http_status=503,
            )

        server_pubkey = gw_status.get("server_public_key")
        public_endpoint = gw_status.get("public_endpoint") or self.wg_easy.public_endpoint

        if not server_pubkey:
            raise ConfigBuilderError(
                "GATEWAY_KEY_UNVERIFIED",
                "Không thể xác minh khóa công khai của wg-easy Gateway.",
                http_status=502,
            )

        # Lấy file conf thô từ wg-easy
        try:
            raw_conf = self.wg_easy.get_client_configuration(ext_device_id)
        except VPNProviderError as e:
            raise ConfigBuilderError(
                "CONFIG_DOWNLOAD_FAILED",
                f"Lỗi khi tải cấu hình từ wg-easy: {e}",
                http_status=502,
            )

        # 4. Ghi đè AllowedIPs và Endpoint chính xác theo lộ trình máy chủ đã phân giải
        customized_conf = raw_conf
        # Đảm bảo Endpoint chuẩn
        if public_endpoint:
            customized_conf = re.sub(
                r"Endpoint\s*=\s*[^\r\n]+",
                f"Endpoint = {public_endpoint}",
                customized_conf,
            )
        # Ghi đè AllowedIPs theo đúng route của máy chủ được cấp
        if re.search(r"AllowedIPs\s*=", customized_conf):
            customized_conf = re.sub(
                r"AllowedIPs\s*=\s*[^\r\n]+",
                f"AllowedIPs = {resolved_route.allowed_ips}",
                customized_conf,
            )
        else:
            # Thêm vào dưới [Peer] nếu chưa có
            customized_conf = customized_conf.replace(
                "[Peer]\n",
                f"[Peer]\nAllowedIPs = {resolved_route.allowed_ips}\n",
            )

        # 5. Tạo Companion SSH Config chuẩn hóa
        ssh_alias = f"labops-{member_username}"
        ssh_config_snippet = (
            f"# OpenSSH Client Configuration — LabOps V2\n"
            f"# Lưu vào ~/.ssh/config trên máy cá nhân\n"
            f"Host {ssh_alias}\n"
            f"    HostName {resolved_route.ssh_host}\n"
            f"    Port {resolved_route.ssh_port}\n"
            f"    User {member_username}\n"
            f"    IdentityFile ~/.ssh/id_ed25519_{member_username}\n"
            f"    IdentitiesOnly yes\n"
            f"    ServerAliveInterval 60\n"
            f"    ServerAliveCountMax 3\n"
        )

        # Đính kèm chỉ dẫn kết nối SSH vào cuối file .conf (dạng comment)
        ssh_comment_block = (
            f"\n# ==============================================================================\n"
            f"# LabOps V2 Companion SSH Connection Info\n"
            f"# HostName {resolved_route.ssh_host}\n"
            f"# Port {resolved_route.ssh_port}\n"
            f"# User {member_username}\n"
            f"# ==============================================================================\n"
        )
        customized_conf = customized_conf.rstrip() + "\n" + ssh_comment_block

        return {
            "device_id": ext_device_id,
            "device_name": device.get("name") or "LabOps Device",
            "member_username": member_username,
            "vpn_ip": device.get("vpn_ip") or "",
            "server_endpoint": public_endpoint,
            "server_public_key": server_pubkey,
            "allowed_ips": resolved_route.allowed_ips,
            "wireguard_conf": customized_conf,
            "ssh_host": resolved_route.ssh_host,
            "ssh_port": resolved_route.ssh_port,
            "is_workspace_ssh": resolved_route.is_workspace_ssh,
            "ssh_config": ssh_config_snippet,
            "configuration_version": f"v{config_version}.{int(time.time())}",
            "route_verification": "VERIFIED",
        }
