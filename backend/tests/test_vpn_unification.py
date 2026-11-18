"""
Bộ kiểm thử tích hợp và bảo mật toàn diện: WireGuard Unification & Per-Host Dynamic Configuration (T01 - T12).
Tuân thủ nghiêm ngặt các yêu cầu của MASTER PROMPT — LABOPS V2:
- YÊU CẦU 1: CHỈ DUY NHẤT DOCKER WG-EASY (loại bỏ legacy wg0, fail-closed, không fallback).
- YÊU CẦU 2: CẤU HÌNH ĐỘNG THEO ĐÚNG MÁY CHỦ ĐƯỢC CẤP (AllowedIPs, SSH ports, HostRegistry, RBAC).
"""

import json
import os
import re
import shutil
import tempfile
import unittest
from typing import Any, Dict, Optional

from app.adapters.vpn_provider import VPNProviderError
from app.adapters.wg_easy_adapter import WgEasyAdapter
from app.core.security import issue_token
from app.services.labops_service import LabOpsDataService
from app.services.vpn_manager import VPNManager
from app.services.vpn_route_resolver import (
    HostRecord,
    HostRegistry,
    ResolvedRoute,
    RouteResolutionError,
    VPNRouteResolver,
)
from app.services.wireguard_config_builder import (
    ConfigBuilderError,
    WireGuardConfigBuilder,
)


class _MockWgEasyGateway:
    """Mock gateway phục vụ kiểm thử đơn vị & tích hợp không chạm daemon production."""

    def __init__(self, server_pubkey: str = "mock_server_pubkey_base64_val==", is_online: bool = True) -> None:
        self.server_pubkey = server_pubkey
        self.is_online = is_online
        self.clients: Dict[str, Any] = {}
        self.counter = 1

    def request(
        self,
        path: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        is_text_response: bool = False,
    ) -> Any:
        if not self.is_online:
            raise RuntimeError("Connection refused: wg-easy container is down")

        p = path.lstrip("/")
        if p == "api/admin/interface":
            return {
                "ipv4Cidr": "10.88.40.0/24",
                "publicKey": self.server_pubkey if self.server_pubkey else None,
            }
        if p == "api/admin/interface/cidr":
            return {"success": True}
        if p == "api/client" and method == "GET":
            return list(self.clients.values())
        if p == "api/client" and method == "POST":
            # Kiểm tra trùng lặp
            name = (data or {}).get("name", "client")
            for c in self.clients.values():
                if c.get("name") == name:
                    pass  # wg-easy cho phép trùng tên nhưng không cho trùng key/ip
            cid = f"wgc-mock-{self.counter}"
            self.counter += 1
            client = {
                "id": cid,
                "name": name,
                "enabled": True,
                "address": f"10.88.40.{self.counter}/32",
                "publicKey": f"mockpubkey_{self.counter}=",
                "createdAt": "2026-11-18T10:00:00Z",
                "updatedAt": "2026-11-18T10:00:00Z",
                "transferRx": 1024,
                "transferTx": 2048,
                "latestHandshakeAt": "2026-11-18T10:05:00Z",
            }
            self.clients[cid] = client
            return client
        if p.startswith("api/client/") and p.endswith("/configuration"):
            cid = p.split("/")[2]
            c = self.clients.get(cid, {})
            addr = c.get("address", "10.88.40.10/32")
            return (
                f"[Interface]\nPrivateKey = mock_client_private_key==\nAddress = {addr}\nDNS = 1.1.1.1\n\n"
                f"[Peer]\nPublicKey = {self.server_pubkey}\nEndpoint = 118.70.169.236:2205\nAllowedIPs = 10.88.40.0/24\n"
            )
        if p.startswith("api/client/") and method == "DELETE":
            cid = p.split("/")[2]
            self.clients.pop(cid, None)
            return {"success": True}
        return {}


class TestWireGuardUnification(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="labops-vpn-unification-")
        self.data_dir = self.tmpdir.name
        self.users_dir = os.path.join(self.data_dir, "users")
        os.makedirs(self.users_dir, exist_ok=True)
        os.environ["LABOPS_USER_DATA_DIR"] = self.users_dir

        # Khởi tạo Mock Gateway
        self.mock_gw = _MockWgEasyGateway()
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.data_dir)
        self.svc.vpn_manager.wg_easy._request = self.mock_gw.request

    def tearDown(self) -> None:
        os.environ.pop("LABOPS_USER_DATA_DIR", None)
        self.tmpdir.cleanup()

    # =========================================================================
    # T01: Cấu hình Workstation chỉ chứa route Workstation, cấm lộ Rack IP
    # =========================================================================
    def test_t01_workstation_route_allowed_ips_and_ssh_port(self) -> None:
        """T01: Người dùng Workstation nhận AllowedIPs = 10.88.40.0/24, 192.168.167.251/32, KHÔNG có Rack IP."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "Nguyen Van Workstation",
            "username": "ws_researcher",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "_actor_id": "admin",
        })
        dev = self.svc.add_member_device(member["id"], "WS Laptop", actor="admin")
        conf_str = self.svc.get_member_device_config(
            member["id"], dev["device_id"], actor={"username": "ws_researcher", "role": "RESEARCHER"}
        )

        # 1. Kiểm tra AllowedIPs
        self.assertIn("AllowedIPs = 10.88.40.0/24, 192.168.167.251/32", conf_str)
        self.assertNotIn("192.168.167.250", conf_str, "Cấm lộ IP của Rack cho người dùng Workstation")

        # 2. Kiểm tra SSH Config trong comment profile
        self.assertIn("HostName 192.168.167.251", conf_str)
        self.assertNotIn("HostName 192.168.167.250", conf_str)

        # 3. Phải dùng SSH Workspace port (22010-29999), KHÔNG được là Admin host SSH port 1912
        self.assertNotIn("Port 1912", conf_str)
        self.assertNotIn("Port 1911", conf_str)

    # =========================================================================
    # T02: Cấu hình Rack chỉ chứa route Rack, cấm lộ Workstation IP
    # =========================================================================
    def test_t02_rack_route_allowed_ips_and_ssh_port(self) -> None:
        """T02: Người dùng Rack nhận AllowedIPs = 10.88.40.0/24, 192.168.167.250/32, KHÔNG có Workstation IP."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "Tran Thi Rack",
            "username": "rack_researcher",
            "role": "RESEARCHER",
            "assigned_server": "track",
            "_actor_id": "admin",
        })
        dev = self.svc.add_member_device(member["id"], "Rack Desktop", actor="admin")
        conf_str = self.svc.get_member_device_config(
            member["id"], dev["device_id"], actor={"username": "rack_researcher", "role": "RESEARCHER"}
        )

        # 1. Kiểm tra AllowedIPs
        self.assertIn("AllowedIPs = 10.88.40.0/24, 192.168.167.250/32", conf_str)
        self.assertNotIn("192.168.167.251", conf_str, "Cấm lộ IP của Workstation cho người dùng Rack")

        # 2. Kiểm tra SSH Config
        self.assertIn("HostName 192.168.167.250", conf_str)
        self.assertNotIn("HostName 192.168.167.251", conf_str)

        # 3. Phải dùng SSH Workspace port, KHÔNG được là Admin host SSH port 1911
        self.assertNotIn("Port 1911", conf_str)
        self.assertNotIn("Port 1912", conf_str)

    # =========================================================================
    # T03: Admin đa máy chủ nhận toàn bộ dải mạng của tất cả máy chủ quản lý
    # =========================================================================
    def test_t03_admin_multi_host_allowed_ips(self) -> None:
        """T03: Admin nhận AllowedIPs chứa cả VPN subnet, Rack IP và Workstation IP."""
        admin_member = self.svc.provision_member({
            "full_name": "Lab Administrator",
            "username": "lab_admin",
            "role": "ADMIN",
            "assigned_server": "workstation",
        })
        dev = self.svc.add_member_device(admin_member["id"], "Admin MacBook", actor="lab_admin")
        conf_str = self.svc.get_member_device_config(
            admin_member["id"], dev["device_id"], actor={"username": "lab_admin", "role": "ADMIN"}
        )

        # Admin phải nhìn thấy cả 2 host
        self.assertIn("10.88.40.0/24", conf_str)
        self.assertIn("192.168.167.250/32", conf_str)
        self.assertIn("192.168.167.251/32", conf_str)

    # =========================================================================
    # T04: Dynamic Host IP Resolution qua HostRegistry
    # =========================================================================
    def test_t04_dynamic_host_ip_resolution_via_registry(self) -> None:
        """T04: Khi cập nhật IP của host trong HostRegistry, profile WireGuard sinh ra tự động cập nhật mà không sửa code."""
        reg = self.svc.vpn_manager.route_resolver.host_registry
        # Giả lập thay đổi LAN IP của máy chủ workstation sang subnet bảo trì: 192.168.167.252
        reg.register_host(HostRecord(
            id="workstation",
            hostname="aiot-ws1",
            lan_ip="192.168.167.252",
            ssh_host="192.168.167.252",
            ssh_port=1912,
            route_policy="WORKSPACE_ONLY",
            verification_state="VERIFIED",
        ))

        member = self.svc.provision_member_ssh_access({
            "full_name": "Dynamic Host Member",
            "username": "dynamic_user",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "_actor_id": "admin",
        })
        dev = self.svc.add_member_device(member["id"], "Dyn Device", actor="admin")
        conf_str = self.svc.get_member_device_config(
            member["id"], dev["device_id"], actor={"username": "dynamic_user", "role": "RESEARCHER"}
        )

        self.assertIn("192.168.167.252/32", conf_str)
        self.assertNotIn("192.168.167.251/32", conf_str)

    # =========================================================================
    # T05: Thiếu khóa công khai Gateway hoặc Gateway lỗi -> Chặn sinh cấu hình
    # =========================================================================
    def test_t05_gateway_public_key_missing_blocks_config(self) -> None:
        """T05: Gateway thiếu public key hoặc gateway chết -> fail-closed, tuyệt đối không dùng fallback hardcode."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "Key Missing User",
            "username": "key_missing_u",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "_actor_id": "admin",
        })
        dev = self.svc.add_member_device(member["id"], "Test Dev", actor="admin")

        # 1. Giả lập Gateway mất public key runtime
        self.mock_gw.server_pubkey = ""

        with self.assertRaises(ConfigBuilderError) as ctx:
            self.svc.get_member_device_config(
                member["id"], dev["device_id"], actor={"username": "key_missing_u", "role": "RESEARCHER"}
            )
        self.assertIn("GATEWAY_KEY_UNVERIFIED", str(ctx.exception))

        # 2. Đăng ký thiết bị mới khi gateway mất key cũng phải fail-closed
        with self.assertRaises(VPNProviderError) as vpn_ctx:
            self.svc.add_member_device(member["id"], "Blocked Dev", actor="admin")
        self.assertIn("VPN_PROVIDER_UNAVAILABLE", str(vpn_ctx.exception))

    # =========================================================================
    # T06: VPN Gateway Unavailable từ chối mọi fallback về legacy-wg0
    # =========================================================================
    def test_t06_gateway_unavailable_rejects_legacy_fallback(self) -> None:
        """T06: Khi wg-easy offline, hệ thống báo lỗi VPN_PROVIDER_UNAVAILABLE và không fallback về legacy-wg0."""
        self.mock_gw.is_online = False

        overview = self.svc.vpn_manager.get_vpn_system_overview()
        self.assertEqual(overview["status"], "UNAVAILABLE")
        self.assertEqual(overview["primary_provider"], "wg-easy")
        self.assertNotIn("legacy_wg0", overview)

        # Gọi legacy provider phải bị từ chối
        with self.assertRaises(VPNProviderError) as ctx:
            self.svc.vpn_manager.get_provider("legacy-wg0")
        self.assertIn("LEGACY_PROVIDER_REMOVED", str(ctx.exception))

    # =========================================================================
    # T07: Kiểm tra độ khớp SSH Port giữa Inventory và Runtime
    # =========================================================================
    def test_t07_ssh_port_isolation_and_workspace_mapping(self) -> None:
        """T07: Cổng SSH của Researcher ánh xạ chính xác vào workspace port được cấp phát, không được cấp cổng hệ thống."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "SSH Port User",
            "username": "ssh_port_u",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "_actor_id": "admin",
        })
        dev = self.svc.add_member_device(member["id"], "Port Dev", actor="admin")
        conf_str = self.svc.get_member_device_config(
            member["id"], dev["device_id"], actor={"username": "ssh_port_u", "role": "RESEARCHER"}
        )

        match = re.search(r"Port\s+(\d+)", conf_str)
        self.assertIsNotNone(match)
        port = int(match.group(1))
        # Cổng workspace phải nằm trong dải phân bổ người dùng
        self.assertTrue(22000 <= port <= 29999 or port == 22011, f"Cổng {port} không hợp lệ cho workspace")
        self.assertNotIn(port, [1911, 1912, 22], "Cấm ánh xạ vào cổng SSH Admin của host")

    # =========================================================================
    # T08: Researcher yêu cầu truy cập máy chủ khác không có quyền -> 403 Forbidden
    # =========================================================================
    def test_t08_researcher_requesting_unauthorized_host_route_blocked(self) -> None:
        """T08: Researcher chỉ có workspace trên Workstation yêu cầu route tới Rack bị từ chối UNAUTHORIZED_HOST_ROUTE."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "Isolated Researcher",
            "username": "isolated_res",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "_actor_id": "admin",
        })
        dev = self.svc.add_member_device(member["id"], "Res Laptop", actor="admin")

        # Researcher tự ý yêu cầu route tới máy chủ 'track' (Rack) mà mình không có workspace
        with self.assertRaises(ConfigBuilderError) as ctx:
            self.svc.vpn_manager.build_dynamic_client_profile(
                actor={"username": "isolated_res", "role": "RESEARCHER"},
                member=member,
                device=dev,
                workspaces=self.svc.workspaces,
                target_host_id="track",
            )
        self.assertIn("UNAUTHORIZED_HOST_ROUTE", str(ctx.exception))

    # =========================================================================
    # T09: Researcher không được phép tải cấu hình của người dùng khác
    # =========================================================================
    def test_t09_cross_user_config_download_rejected(self) -> None:
        """T09: Researcher A tải cấu hình của Researcher B -> FORBIDDEN_CROSS_USER_ACCESS (403)."""
        member_a = self.svc.provision_member_ssh_access({
            "full_name": "User Alpha",
            "username": "user_alpha",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "_actor_id": "admin",
        })
        dev_a = self.svc.add_member_device(member_a["id"], "Alpha PC", actor="admin")

        # Researcher Beta cố tình tải cấu hình của User Alpha
        with self.assertRaises(ConfigBuilderError) as ctx:
            self.svc.get_member_device_config(
                member_a["id"],
                dev_a["device_id"],
                actor={"username": "user_beta", "role": "RESEARCHER"},
            )
        self.assertIn("FORBIDDEN_CROSS_USER_ACCESS", str(ctx.exception))

    # =========================================================================
    # T10: Trùng lặp IP / Khóa được bảo vệ trong wg-easy
    # =========================================================================
    def test_t10_duplicate_device_management(self) -> None:
        """T10: Tạo nhiều thiết bị cho thành viên sinh các IP VPN độc lập, không xung đột."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "Multi Device Member",
            "username": "multi_dev_u",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "_actor_id": "admin",
        })
        dev1 = self.svc.add_member_device(member["id"], "Device 1", actor="admin")
        dev2 = self.svc.add_member_device(member["id"], "Device 2", actor="admin")

        self.assertNotEqual(dev1["device_id"], dev2["device_id"])
        self.assertNotEqual(dev1["vpn_ip"], dev2["vpn_ip"])

    # =========================================================================
    # T11: Đảm bảo tính nhất quán của Docker UDP Port cấu hình
    # =========================================================================
    def test_t11_docker_compose_port_consistency(self) -> None:
        """T11: Kiểm tra tệp compose.yaml của wg-easy không chứa xung đột cổng host 2204 và 2205."""
        compose_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "infrastructure", "wg-easy", "compose.yaml")
        )
        self.assertTrue(os.path.isfile(compose_path), f"Không tìm thấy tệp {compose_path}")
        with open(compose_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Kiểm tra không có khai báo tĩnh cả 2 port "2204:2204/udp" và "2205:2204/udp" cùng lúc
        self.assertNotIn(
            '"2204:2204/udp"',
            content,
            "compose.yaml không được mở cả 2204 và 2205 gây xung đột socket",
        )
        # Phải dùng biến môi trường WG_EASY_UDP_PORT
        self.assertIn("WG_EASY_UDP_PORT", content)

    # =========================================================================
    # T12: Legacy Migration Reconciliation (Không còn lệnh gọi wg set / legacy)
    # =========================================================================
    def test_t12_legacy_migration_reconciliation(self) -> None:
        """T12: Toàn bộ quá trình cấp phát thiết bị mới chỉ dùng wg-easy; legacy adapter không can thiệp."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "Modern Member",
            "username": "modern_u",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "_actor_id": "admin",
        })
        dev = self.svc.add_member_device(member["id"], "Modern Laptop", actor="admin")

        self.assertEqual(dev["provider"], "wg-easy")
        self.assertNotIn("legacy", dev["provider"])

        # Kiểm tra không có `wireguard_adapter` trong LabOpsDataService
        self.assertFalse(hasattr(self.svc, "wireguard_adapter"))


if __name__ == "__main__":
    unittest.main()
