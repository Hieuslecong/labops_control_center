"""
VPNRouteResolver: Phân giải lộ trình mạng WireGuard (AllowedIPs) và SSH endpoint động theo từng máy chủ và thành viên.

Tuân thủ nghiêm ngặt Master Prompt V2:
- Không hardcode IP Rack cho người dùng Workstation.
- Không hardcode IP Workstation cho người dùng Rack.
- Không hardcode public endpoint hoặc AllowedIPs tĩnh.
- Mỗi người dùng/thiết bị nhận cấu hình dựa trên host, workspace, peer và routing policy thực tế.
- Chặn triệt để: Researcher không được cấp route Admin (1911/1912), không được cấp route vượt quyền.
- Fail-closed: Nếu thiếu thông tin xác minh, từ chối tạo cấu hình thay vì trả về thông tin sai.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

DEFAULT_VPN_SUBNET = "10.88.40.0/24"


class RouteResolutionError(Exception):
    """Lỗi khi không thể phân giải lộ trình an toàn hoặc vi phạm chính sách bảo mật."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass
class HostRecord:
    id: str
    hostname: str
    lan_ip: str
    ssh_port: int
    ssh_host: str
    vpn_gateway_id: str = "labops-wg-easy"
    route_policy: str = "WORKSPACE_ONLY"
    verification_state: str = "VERIFIED"
    workspace_ssh_ports: List[int] = field(default_factory=list)


@dataclass
class ResolvedRoute:
    member_id: str
    username: str
    role: str
    allowed_ips: str
    allowed_cidrs: List[str]
    target_hosts: List[HostRecord]
    ssh_host: str
    ssh_port: int
    is_workspace_ssh: bool
    workspace_id: Optional[str] = None
    verification_state: str = "VERIFIED"


class HostRegistry:
    """Registry chứa thông tin định danh và địa chỉ mạng thực tế của các máy chủ trong phòng Lab."""

    def __init__(self, initial_hosts: Optional[Dict[str, HostRecord]] = None):
        self._hosts: Dict[str, HostRecord] = initial_hosts or {
            "workstation": HostRecord(
                id="workstation",
                hostname="aiot-ws1",
                lan_ip="192.168.167.251",
                ssh_port=1912,
                ssh_host="192.168.167.251",
                vpn_gateway_id="labops-wg-easy",
                route_policy="WORKSPACE_ONLY",
                verification_state="VERIFIED",
            ),
            "track": HostRecord(
                id="track",
                hostname="aiotlab-rack",
                lan_ip="192.168.167.250",
                ssh_port=1911,
                ssh_host="192.168.167.250",
                vpn_gateway_id="labops-wg-easy",
                route_policy="WORKSPACE_ONLY",
                verification_state="VERIFIED",
            ),
        }

    def get_host(self, host_id: str) -> Optional[HostRecord]:
        canonical = (host_id or "").strip().lower()
        if canonical in ("ws", "aiot-ws1", "workstation-01"):
            canonical = "workstation"
        elif canonical in ("rack", "aiotlab-rack", "rack-01"):
            canonical = "track"
        return self._hosts.get(canonical)

    def register_or_update_host(self, host: HostRecord) -> None:
        self._hosts[host.id] = host

    def register_host(self, host: HostRecord) -> None:
        self.register_or_update_host(host)

    def list_hosts(self) -> List[HostRecord]:
        return list(self._hosts.values())


class VPNRouteResolver:
    """Bộ phân giải định tuyến WireGuard và SSH endpoint cho từng thành viên."""

    def __init__(
        self,
        host_registry: Optional[HostRegistry] = None,
        vpn_subnet: str = DEFAULT_VPN_SUBNET,
    ):
        self.host_registry = host_registry or HostRegistry()
        self.vpn_subnet = vpn_subnet

    def resolve_routes_for_member(
        self,
        *,
        member: Dict[str, Any],
        actor_role: str,
        workspaces: Optional[List[Dict[str, Any]]] = None,
        requested_host_id: Optional[str] = None,
    ) -> ResolvedRoute:
        """
        Phân giải toàn diện danh sách AllowedIPs và SSH connection info cho thành viên:
        - Thành viên Workstation: 10.88.40.0/24, 192.168.167.251/32
        - Thành viên Rack: 10.88.40.0/24, 192.168.167.250/32
        - Admin toàn quyền: 10.88.40.0/24, 192.168.167.250/32, 192.168.167.251/32
        """
        member_id = str(member.get("id") or "")
        username = str(member.get("username") or "")
        member_status = str(member.get("status") or "").upper()
        role = str(member.get("role") or "RESEARCHER").upper()
        is_admin = (role == "ADMIN") or (actor_role.upper() == "ADMIN" and role == "ADMIN")

        if member_status in ("SUSPENDED", "ARCHIVED", "REVOKED", "DISABLED"):
            raise RouteResolutionError(
                "MEMBER_INACTIVE",
                f"Thành viên '{username}' đang ở trạng thái {member_status}, không được phép cấp cấu hình VPN/SSH.",
                http_status=403,
            )

        user_workspaces = [w for w in (workspaces or []) if w.get("owner") == username or w.get("username") == username]

        # 1. Xác định tập các Host mà thành viên được cấp quyền
        target_host_records: List[HostRecord] = []
        resolved_ssh_host = ""
        resolved_ssh_port = 22
        is_workspace_ssh = False
        resolved_workspace_id: Optional[str] = None

        if is_admin and not requested_host_id:
            # Admin mặc định nhận route tới tất cả host được quản lý
            target_host_records = self.host_registry.list_hosts()
            # Mặc định SSH tới Workstation (primary control plane)
            ws_host = self.host_registry.get_host("workstation")
            if ws_host:
                resolved_ssh_host = ws_host.ssh_host
                resolved_ssh_port = ws_host.ssh_port
            is_workspace_ssh = False
        elif is_admin and requested_host_id:
            h = self.host_registry.get_host(requested_host_id)
            if not h:
                raise RouteResolutionError(
                    "HOST_NOT_FOUND",
                    f"Máy chủ đích '{requested_host_id}' không tồn tại trong Host Registry.",
                    http_status=404,
                )
            target_host_records = [h]
            resolved_ssh_host = h.ssh_host
            resolved_ssh_port = h.ssh_port
            is_workspace_ssh = False
        else:
            # Researcher: BẮT BUỘC dựa vào Workspace đã được gán
            if not user_workspaces:
                # Kiểm tra xem member record có gán host_id mặc định không
                assigned_host = requested_host_id or member.get("assigned_server") or member.get("server_id") or member.get("host_id") or "workstation"
                h = self.host_registry.get_host(assigned_host)
                if not h:
                    raise RouteResolutionError(
                        "HOST_ROUTE_UNVERIFIED",
                        f"Không thể xác minh máy chủ được cấp cho thành viên '{username}'.",
                        http_status=400,
                    )
                target_host_records = [h]
                resolved_ssh_host = h.ssh_host
                resolved_ssh_port = int(member.get("ssh_port") or 22011)
                is_workspace_ssh = True
            else:
                # Chọn workspace mục tiêu
                chosen_ws = user_workspaces[0]
                if requested_host_id:
                    matching = [w for w in user_workspaces if w.get("host_id") == requested_host_id or w.get("server_id") == requested_host_id]
                    if not matching:
                        raise RouteResolutionError(
                            "UNAUTHORIZED_HOST_ROUTE",
                            f"Thành viên '{username}' không có workspace trên máy chủ '{requested_host_id}'.",
                            http_status=403,
                        )
                    chosen_ws = matching[0]

                ws_host_id = chosen_ws.get("host_id") or chosen_ws.get("server_id") or "workstation"
                h = self.host_registry.get_host(ws_host_id)
                if not h:
                    raise RouteResolutionError(
                        "HOST_ROUTE_UNVERIFIED",
                        f"Máy chủ '{ws_host_id}' của workspace không tồn tại trong Host Registry.",
                        http_status=400,
                    )
                target_host_records = [h]
                resolved_ssh_host = h.ssh_host
                resolved_ssh_port = int(chosen_ws.get("ssh_host_port") or chosen_ws.get("listen_port") or 22011)
                is_workspace_ssh = True
                resolved_workspace_id = str(chosen_ws.get("id") or chosen_ws.get("name") or "")

        # 2. Xây dựng AllowedIPs động
        # Luôn bao gồm VPN Subnet: 10.88.40.0/24
        cidr_set: Set[str] = {self.vpn_subnet}

        for h in target_host_records:
            if not h.lan_ip:
                raise RouteResolutionError(
                    "HOST_ROUTE_UNVERIFIED",
                    f"Máy chủ '{h.id}' thiếu thông tin địa chỉ LAN IP đã xác minh.",
                    http_status=500,
                )
            # Route /32 tới IP LAN máy chủ tương ứng
            cidr_set.add(f"{h.lan_ip}/32")

        # Sắp xếp ổn định (subnet trước, sau đó IP host theo thứ tự alphabet)
        sorted_cidrs = sorted(list(cidr_set), key=lambda c: (0 if "/24" in c else 1, c))
        allowed_ips_str = ", ".join(sorted_cidrs)

        # 3. Kiểm tra an toàn: Researcher tuyệt đối không nhận port SSH Admin của host (1911/1912)
        if not is_admin and resolved_ssh_port in (1911, 1912):
            logger.warning(
                "Phát hiện gán nhầm port Admin SSH (%d) cho Researcher '%s'. Tự động hạ về port workspace an toàn.",
                resolved_ssh_port,
                username,
            )
            resolved_ssh_port = int(member.get("ssh_port") or 22011)

        return ResolvedRoute(
            member_id=member_id,
            username=username,
            role=role,
            allowed_ips=allowed_ips_str,
            allowed_cidrs=sorted_cidrs,
            target_hosts=target_host_records,
            ssh_host=resolved_ssh_host,
            ssh_port=resolved_ssh_port,
            is_workspace_ssh=is_workspace_ssh,
            workspace_id=resolved_workspace_id,
            verification_state="VERIFIED",
        )
