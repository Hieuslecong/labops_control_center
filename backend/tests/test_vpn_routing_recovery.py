"""
Kiểm thử hồi quy khắc phục định tuyến WireGuard VPN & fail-closed provisioning (LabOps V2).

Bao phủ toàn bộ 9 kịch bản phục hồi định tuyến, NAT, route, phân tách Workstation/Rack,
kiểm soát CIDR có restart, trạng thái trung thực (VPN_API_HEALTHY vs HEALTHY) và bảo mật cấu hình.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
repo_root = os.path.abspath(os.path.join(backend_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault("LABOPS_SESSION_SECRET", "test-secret-vpn-routing-recovery-32chars!")

from app.adapters.wg_easy_adapter import WgEasyAdapter
from app.core.config import Settings
from app.services.labops_service import LabOpsDataService
from app.services.vpn_route_resolver import HostRegistry, VPNRouteResolver
from app.services.wireguard_config_builder import ConfigBuilderError, WireGuardConfigBuilder


VALID_POST_UP = (
    "iptables -t nat -A POSTROUTING -s 10.88.40.0/24 -d 192.168.167.250/31 -o eth0 -j MASQUERADE;"
)
MOCK_SERVER_PUBKEY = "TA7sUCFRsPHfg52B/Gh4w7vnu3SpzaxH4A1HS6N3bDk="


def _make_adapter(
    *,
    persisted_cidr: str = "10.88.40.0/24",
    ipv6_cidr: str = "fdcc:ad94:bacf:61a4::cafe:0/112",
    hooks_post_up: str = VALID_POST_UP,
    clients: Optional[List[Dict[str, Any]]] = None,
    runtime_probe: Optional[Dict[str, Any]] = None,
    recorded_calls: Optional[List[tuple[str, str, Any]]] = None,
) -> WgEasyAdapter:
    adapter = WgEasyAdapter(
        base_url="http://127.0.0.1:51821",
        username="admin",
        password="mock-wg-easy-password",
        public_endpoint="118.70.169.236:2204",
        vpn_subnet="10.88.40.0/24",
    )
    client_store: List[Dict[str, Any]] = list(clients or [])

    def fake_request(
        path: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        is_text_response: bool = False,
    ) -> Any:
        norm = path.lstrip("/")
        if recorded_calls is not None:
            recorded_calls.append((norm, method, data))
        if norm == "api/admin/interface" and method == "GET":
            return {
                "name": "wg0",
                "enabled": True,
                "ipv4Cidr": persisted_cidr,
                "ipv6Cidr": ipv6_cidr,
                "port": 2204,
                "publicKey": MOCK_SERVER_PUBKEY,
            }
        if norm == "api/admin/interface/cidr" and method == "POST":
            return {"success": True}
        if norm == "api/admin/interface/restart" and method == "POST":
            return {"success": True}
        if norm == "api/admin/hooks" and method == "GET":
            return {"id": "wg0", "postUp": hooks_post_up}
        if norm == "api/admin/userconfig" and method == "GET":
            return {"host": "118.70.169.236", "port": 2204}
        if norm == "api/client" and method == "GET":
            return list(client_store)
        if norm == "api/client" and method == "POST":
            cid = len(client_store) + 1
            ip = f"10.88.40.{cid + 1}"
            rec = {
                "id": cid,
                "name": (data or {}).get("name") or f"peer-{cid}",
                "ipv4Address": ip,
                "enabled": True,
                "publicKey": f"PeerPubKey{cid}00000000000000000000000000000000=",
                "latestHandshakeAt": None,
                "transferRx": 0,
                "transferTx": 0,
            }
            client_store.append(rec)
            return {"clientId": cid, "ipv4Address": ip}
        if norm.startswith("api/client/") and norm.endswith("/configuration") and method == "GET":
            cid = norm.split("/")[2]
            c = next((item for item in client_store if str(item["id"]) == str(cid)), None)
            addr = (c or {}).get("ipv4Address") or "10.88.40.2"
            return (
                "[Interface]\n"
                "PrivateKey = MOCK_CLIENT_PRIVATE_KEY_NOT_REAL\n"
                f"Address = {addr}/32\n"
                "DNS = 1.1.1.1\n\n"
                "[Peer]\n"
                f"PublicKey = {MOCK_SERVER_PUBKEY}\n"
                "Endpoint = 118.70.169.236:2204\n"
                "AllowedIPs = 10.88.40.0/24\n"
                "PersistentKeepalive = 25\n"
            )
        return {}

    adapter._request = fake_request  # type: ignore[method-assign]
    if runtime_probe is not None:
        adapter.inspect_runtime_network = lambda _iface="wg0": dict(runtime_probe)  # type: ignore[method-assign]
    return adapter


class TestVpnRoutingRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_actor = {"username": "admin_root", "role": "ADMIN", "is_admin": True}
        self.member_ws = {
            "id": "mem-ws-1",
            "username": "testacc2",
            "role": "RESEARCHER",
            "assigned_server": "workstation",
            "ssh_port": 22004,
        }
        self.device_valid = {
            "id": "1",
            "external_id": 1,
            "name": "testacc2-device",
            "vpn_ip": "10.88.40.2",
        }

    def test_01_runtime_cidr_mismatch_blocks_profile(self) -> None:
        adapter = _make_adapter(
            persisted_cidr="10.88.40.0/24",
            clients=[{
                "id": 1,
                "name": "testacc2",
                "ipv4Address": "10.88.40.2",
                "enabled": True,
                "publicKey": "PeerPub1=",
            }],
            runtime_probe={
                "inspected": True,
                "runtime_wg_cidr": "10.8.0.0/24",
                "runtime_routes": ["10.88.40.0/24"],
                "nat_subnets": ["10.88.40.0/24"],
            },
        )
        status = adapter.get_connection_status()
        self.assertTrue(status["state_flags"]["VPN_INTERFACE_MISMATCH"])
        self.assertEqual(status["service_health"], "VPN_ROUTING_MISCONFIGURED")
        self.assertIn("VPN_ROUTING_MISCONFIGURED", status["states"])

        builder = WireGuardConfigBuilder(wg_easy=adapter, route_resolver=VPNRouteResolver(vpn_subnet="10.88.40.0/24"))
        with self.assertRaises(ConfigBuilderError) as ctx:
            builder.build_client_profile(actor=self.admin_actor, member=self.member_ws, device=self.device_valid)
        self.assertEqual(ctx.exception.code, "VPN_ROUTING_MISCONFIGURED")

    def test_02_nat_subnet_mismatch_and_bad_hook_detected(self) -> None:
        # (a) Kernel NAT POSTROUTING thiếu 10.88.40.0/24 (đang kẹt 10.8.0.0/24)
        adapter_runtime_nat = _make_adapter(
            clients=[{"id": 1, "name": "testacc2", "ipv4Address": "10.88.40.2", "enabled": True}],
            runtime_probe={
                "inspected": True,
                "runtime_wg_cidr": "10.88.40.0/24",
                "runtime_routes": ["10.88.40.0/24"],
                "nat_subnets": ["10.8.0.0/24"],
            },
        )
        st_a = adapter_runtime_nat.get_connection_status()
        self.assertTrue(st_a["state_flags"]["VPN_NAT_MISMATCH"])
        self.assertEqual(st_a["service_health"], "VPN_ROUTING_MISCONFIGURED")

        # (b) Persisted hook postUp hardcode 10.8.0.0/24
        adapter_hook_nat = _make_adapter(
            hooks_post_up="iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o eth0 -j MASQUERADE",
            clients=[{"id": 1, "name": "testacc2", "ipv4Address": "10.88.40.2", "enabled": True}],
            runtime_probe={
                "inspected": True,
                "runtime_wg_cidr": "10.88.40.0/24",
                "runtime_routes": ["10.88.40.0/24"],
                "nat_subnets": ["10.88.40.0/24"],
            },
        )
        st_b = adapter_hook_nat.get_connection_status()
        self.assertTrue(st_b["state_flags"]["VPN_NAT_MISMATCH"])
        self.assertEqual(st_b["service_health"], "VPN_ROUTING_MISCONFIGURED")

    def test_03_gateway_route_mismatch_detected(self) -> None:
        adapter = _make_adapter(
            clients=[{"id": 1, "name": "testacc2", "ipv4Address": "10.88.40.2", "enabled": True}],
            runtime_probe={
                "inspected": True,
                "runtime_wg_cidr": "10.88.40.0/24",
                "runtime_routes": [],
                "nat_subnets": ["10.88.40.0/24"],
            },
        )
        status = adapter.get_connection_status()
        self.assertTrue(status["state_flags"]["VPN_ROUTE_MISMATCH"])
        self.assertEqual(status["service_health"], "VPN_ROUTING_MISCONFIGURED")

    def test_04_peer_ip_outside_subnet_detected_and_blocked(self) -> None:
        adapter = _make_adapter()
        diag = adapter.detect_routing_inconsistencies(
            "10.88.40.0/24",
            [{"name": "stale", "vpn_ip": "10.8.0.2", "status": "ACTIVE"}],
            hooks={"postUp": VALID_POST_UP},
            runtime_probe={
                "inspected": True,
                "runtime_wg_cidr": "10.88.40.0/24",
                "runtime_routes": ["10.88.40.0/24"],
                "nat_subnets": ["10.88.40.0/24"],
            },
        )
        self.assertTrue(any("PEER_IP_OUTSIDE_SUBNET" in issue for issue in diag["issues"]))

        # Kiểm tra build_client_profile từ chối peer IP nằm ngoài subnet hiệu lực
        stale_client = {"id": "1", "external_id": 1, "name": "stale", "vpn_ip": "10.8.0.2"}
        adapter.get_connection_status = lambda: {  # type: ignore[method-assign]
            "status": "ACTIVE",
            "service_health": "HEALTHY",
            "server_public_key": MOCK_SERVER_PUBKEY,
            "routing_issues": [],
            "runtime_wg_cidr": "10.88.40.0/24",
            "persisted_cidr": "10.88.40.0/24",
            "vpn_subnet": "10.88.40.0/24",
            "public_endpoint": "118.70.169.236:2204",
        }
        adapter.list_devices = lambda: [stale_client]  # type: ignore[method-assign]
        builder = WireGuardConfigBuilder(wg_easy=adapter, route_resolver=VPNRouteResolver(vpn_subnet="10.88.40.0/24"))
        with self.assertRaises(ConfigBuilderError) as ctx:
            builder.build_client_profile(actor=self.admin_actor, member=self.member_ws, device=stale_client)
        self.assertEqual(ctx.exception.code, "PEER_IP_OUTSIDE_SUBNET")

    def test_05_unregistered_peer_fail_closed_never_generates_fake_conf(self) -> None:
        adapter = _make_adapter(
            clients=[],
            runtime_probe={
                "inspected": True,
                "runtime_wg_cidr": "10.88.40.0/24",
                "runtime_routes": ["10.88.40.0/24"],
                "nat_subnets": ["10.88.40.0/24"],
            },
        )
        builder = WireGuardConfigBuilder(wg_easy=adapter, route_resolver=VPNRouteResolver(vpn_subnet="10.88.40.0/24"))
        with self.assertRaises(ConfigBuilderError) as ctx:
            builder.build_client_profile(actor=self.admin_actor, member=self.member_ws, device=self.device_valid)
        self.assertEqual(ctx.exception.code, "PEER_NOT_REGISTERED_ON_GATEWAY")

        with tempfile.TemporaryDirectory(prefix="labops-vpn-failclosed-") as tmpdir:
            svc = LabOpsDataService(demo_mode=False, data_dir=tmpdir)
            svc.vpn_manager.wg_easy.password = ""
            svc.vpn_manager.wg_easy._request = (  # type: ignore[method-assign]
                lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("gateway offline"))
            )
            created = svc.provision_member_ssh_access({
                "full_name": "Offline Peer Test",
                "username": "offline_peer_u",
                "assigned_server": "workstation",
                "cpu_limit_cores": 2,
                "ram_limit_gb": 4,
                "storage_quota_gb": 10,
                "require_passphrase": False,
                "_actor_id": "admin_root",
            })
            _, zip_bytes = svc.claim_member_access_zip(created["id"], actor="admin_root")
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                names = set(zf.namelist())
                prefix = "LabOps_Access_offline_peer_u"
                self.assertNotIn(f"{prefix}/wireguard/labops.conf", names)
                self.assertIn(f"{prefix}/wireguard/WIREGUARD_PENDING.md", names)

    def test_06_workstation_vs_rack_isolation_offline_host_and_researcher_acl(self) -> None:
        resolver = VPNRouteResolver(host_registry=HostRegistry(), vpn_subnet="10.88.40.0/24")

        ws_route = resolver.resolve_routes_for_member(
            member={"id": "m1", "username": "ws_user", "role": "RESEARCHER", "assigned_server": "workstation", "ssh_port": 22004},
            actor_role="RESEARCHER",
        )
        self.assertIn("10.88.40.0/24", ws_route.allowed_cidrs)
        self.assertIn("192.168.167.251/32", ws_route.allowed_cidrs)
        self.assertNotIn("192.168.167.250/32", ws_route.allowed_cidrs)

        rack_route = resolver.resolve_routes_for_member(
            member={"id": "m2", "username": "rack_user", "role": "RESEARCHER", "assigned_server": "track", "ssh_port": 22005},
            actor_role="RESEARCHER",
        )
        self.assertIn("10.88.40.0/24", rack_route.allowed_cidrs)
        self.assertIn("192.168.167.250/32", rack_route.allowed_cidrs)
        self.assertNotIn("192.168.167.251/32", rack_route.allowed_cidrs)

        offline_route = resolver.resolve_routes_for_member(
            member={"id": "m3", "username": "off_user", "role": "RESEARCHER", "assigned_server": "workstation", "ssh_port": 22006},
            actor_role="RESEARCHER",
            host_online=False,
        )
        self.assertEqual(offline_route.verification_state, "HOST_OFFLINE_UNVERIFIED")

        # Researcher không bao giờ nhận port SSH Admin (1911/1912) dù workspace/member bị gán nhầm
        clamped = resolver.resolve_routes_for_member(
            member={"id": "m4", "username": "res_acl", "role": "RESEARCHER", "assigned_server": "workstation"},
            actor_role="RESEARCHER",
            workspaces=[{"id": "ws-acl", "member_username": "res_acl", "host_id": "workstation", "ssh_host_port": 1912}],
        )
        self.assertTrue(clamped.is_workspace_ssh)
        self.assertNotIn(clamped.ssh_port, (1911, 1912))

    def test_07_controlled_cidr_change_preserves_ipv6_and_create_device_has_no_cidr_mutation(self) -> None:
        calls: List[tuple[str, str, Any]] = []
        adapter = _make_adapter(
            persisted_cidr="10.8.0.0/24",
            ipv6_cidr="fdcc:ad94:bacf:61a4::cafe:0/112",
            recorded_calls=calls,
        )
        dev = adapter.create_device("peer-1")
        self.assertEqual(dev["name"], "peer-1")
        self.assertFalse(any(p == "api/admin/interface/cidr" for p, _, _ in calls))
        self.assertTrue(any(p == "api/client" and m == "POST" for p, m, _ in calls))

        calls.clear()
        adapter.ensure_subnet_configured(restart_on_change=True)
        cidr_calls = [(m, d) for p, m, d in calls if p == "api/admin/interface/cidr"]
        restart_calls = [m for p, m, _ in calls if p == "api/admin/interface/restart"]
        self.assertEqual(
            cidr_calls,
            [("POST", {"ipv4Cidr": "10.88.40.0/24", "ipv6Cidr": "fdcc:ad94:bacf:61a4::cafe:0/112"})],
        )
        self.assertEqual(restart_calls, ["POST"])

    def test_08_honest_vpn_api_healthy_vs_full_healthy_status(self) -> None:
        client = [{
            "id": 1,
            "name": "testacc2",
            "ipv4Address": "10.88.40.2",
            "enabled": True,
            "publicKey": "PeerPub1=",
            "latestHandshakeAt": "2026-11-19T07:35:37.000Z",
            "transferRx": 1200,
            "transferTx": 3400,
        }]

        # Không có docker.sock => chỉ được báo VPN_API_HEALTHY, KHÔNG được báo HEALTHY giả
        api_only = _make_adapter(
            clients=client,
            runtime_probe={"inspected": False, "reason": "DOCKER_SOCKET_UNAVAILABLE"},
        )
        st_api = api_only.get_connection_status()
        self.assertEqual(st_api["service_health"], "VPN_API_HEALTHY")
        self.assertEqual(st_api["status"], "VPN_API_HEALTHY")
        self.assertFalse(st_api["state_flags"]["VPN_DATA_PATH_VERIFIED"])

        # Có kiểm chứng kernel wg0 + route + NAT + handshake => báo HEALTHY thật
        verified = _make_adapter(
            clients=client,
            runtime_probe={
                "inspected": True,
                "runtime_wg_cidr": "10.88.40.0/24",
                "runtime_routes": ["10.88.40.0/24"],
                "nat_subnets": ["10.88.40.0/24"],
            },
        )
        st_ok = verified.get_connection_status()
        self.assertEqual(st_ok["service_health"], "HEALTHY")
        self.assertEqual(st_ok["status"], "ACTIVE")
        self.assertTrue(st_ok["state_flags"]["VPN_DATA_PATH_VERIFIED"])

    def test_09_config_and_compose_security_hygiene(self) -> None:
        root = Path(repo_root)
        checked_files = [
            root / "infrastructure" / "wg-easy" / "compose.yaml",
            root / "infrastructure" / "wg-easy" / ".env.example",
            root / ".env.workstation.example",
            root / "backend" / "app" / "core" / "config.py",
        ]
        for path in checked_files:
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("LabOpsSecureVPN2026!", content, f"Hardcoded password leaked in {path}")
            self.assertNotIn("118.70.169.236:2205", content, f"Wrong legacy endpoint port 2205 in {path}")

        compose_text = (root / "infrastructure" / "wg-easy" / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("INIT_IPV4_CIDR", compose_text)
        self.assertIn("INIT_IPV6_CIDR", compose_text)
        self.assertIn("${WG_EASY_ADMIN_PASSWORD:?", compose_text)

        with patch.dict(os.environ, {}, clear=True):
            clean_settings = Settings(_env_file=None)
            self.assertEqual(clean_settings.WG_EASY_ADMIN_PASSWORD, "")
            self.assertEqual(clean_settings.WG_EASY_PUBLIC_ENDPOINT, "")


if __name__ == "__main__":
    unittest.main()
