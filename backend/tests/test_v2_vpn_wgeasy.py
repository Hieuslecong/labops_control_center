"""
Kiểm thử bộ tích hợp wg-easy v15 và VPNManager cho LabOps Control Center.
Kiểm tra toàn bộ luồng quản trị thiết bị, cấp phát WireGuard, lấy cấu hình .conf,
vô hiệu hóa/thu hồi và các endpoint HTTP V2.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

os.environ.setdefault("LABOPS_SESSION_SECRET", "test-secret-vpn-wgeasy-32chars-ok!")

import app.main as main_mod
from app.adapters.wg_easy_adapter import WgEasyAdapter
from app.core.security import issue_token
from app.main import LabOpsRequestHandler
from app.services.labops_service import LabOpsDataService
from app.services.member_lifecycle import LifecyclePreconditionError
from app.services.vpn_manager import VPNManager


class _MockWgEasyBackend:
    def __init__(self) -> None:
        self.clients: Dict[str, Any] = {}
        self.counter = 1

    def request(
        self,
        path: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        is_text_response: bool = False,
    ) -> Any:
        p = path.lstrip("/")
        if p == "api/admin/interface":
            return {"ipv4Cidr": "10.88.40.0/24", "publicKey": "mockserverpub=="}
        if p == "api/admin/interface/cidr":
            return {"success": True}
        if p == "api/client" and method == "GET":
            return list(self.clients.values())
        if p == "api/client" and method == "POST":
            cid = f"wgc-mock-{self.counter}"
            self.counter += 1
            client = {
                "id": cid,
                "name": (data or {}).get("name", "client"),
                "enabled": True,
                "address": f"10.88.40.{self.counter}/32",
                "publicKey": f"mockpubkey{self.counter}=",
                "createdAt": "2026-03-30T10:00:00Z",
                "updatedAt": "2026-03-30T10:00:00Z",
                "transferRx": 1024,
                "transferTx": 2048,
                "latestHandshakeAt": "2026-03-30T10:05:00Z",
            }
            self.clients[cid] = client
            return client
        if p.startswith("api/client/") and p.endswith("/configuration"):
            cid = p.split("/")[2]
            c = self.clients.get(cid, {})
            addr = c.get("address", "10.88.40.10/32")
            return (
                f"[Interface]\nPrivateKey = mockprivkey==\nAddress = {addr}\nDNS = 1.1.1.1\n\n"
                f"[Peer]\nPublicKey = mockserverpub==\nEndpoint = 118.70.169.236:2204\nAllowedIPs = 10.88.40.0/24\n"
            )
        if p.startswith("api/client/") and p.endswith("/disable"):
            cid = p.split("/")[2]
            if cid in self.clients:
                self.clients[cid]["enabled"] = False
            return self.clients.get(cid, {})
        if p.startswith("api/client/") and p.endswith("/enable"):
            cid = p.split("/")[2]
            if cid in self.clients:
                self.clients[cid]["enabled"] = True
            return self.clients.get(cid, {})
        if p.startswith("api/client/") and method == "DELETE":
            cid = p.split("/")[2]
            self.clients.pop(cid, None)
            return {"success": True}
        return {}


class TestVpnWgEasyIntegration(unittest.TestCase):
    has_wgeasy: bool = False

    @classmethod
    def setUpClass(cls) -> None:
        import urllib.request
        try:
            with urllib.request.urlopen("http://127.0.0.1:51821/api/wireguard/client", timeout=1):
                cls.has_wgeasy = True
        except Exception:
            cls.has_wgeasy = False

    def _clean_dirs(self) -> None:
        base_dir = os.environ.get("LABOPS_USER_DATA_DIR") or getattr(self, "_users_dir", "")
        if not base_dir or not os.path.isdir(base_dir):
            return
        for u in ("multidev_user", "api_dev_user", "delete_u", "pending_u"):
            p = os.path.join(base_dir, u)
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="labops-vpn-test-")
        self._prev_user_dir = os.environ.get("LABOPS_USER_DATA_DIR")
        self._users_dir = os.path.join(self.tmpdir.name, "users")
        os.makedirs(self._users_dir, exist_ok=True)
        os.environ["LABOPS_USER_DATA_DIR"] = self._users_dir
        self._clean_dirs()
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmpdir.name)
        if not self.has_wgeasy:
            mock_backend = _MockWgEasyBackend()
            self.svc.vpn_manager.wg_easy._request = mock_backend.request
        self.admin_token = issue_token("admin_vpn", "ADMIN")
        self.researcher_token = issue_token("res_vpn", "RESEARCHER")

    def tearDown(self) -> None:
        self._clean_dirs()
        if self._prev_user_dir is None:
            os.environ.pop("LABOPS_USER_DATA_DIR", None)
        else:
            os.environ["LABOPS_USER_DATA_DIR"] = self._prev_user_dir
        self.tmpdir.cleanup()

    def test_01_vpn_manager_overview_and_provider_resolution(self) -> None:
        """Kiểm tra tổng quan hệ thống VPN và độ phân giải provider (chỉ duy nhất wg-easy)."""
        mgr = self.svc.vpn_manager
        overview = mgr.get_vpn_system_overview()
        self.assertEqual(overview.get("primary_provider"), "wg-easy")
        self.assertEqual(overview.get("active_provider"), "wg-easy")
        self.assertIn("wg_easy", overview)
        self.assertNotIn("legacy_wg0", overview)
        self.assertIn("total_devices", overview)
        from app.adapters.vpn_provider import VPNProviderError
        with self.assertRaises(VPNProviderError) as ctx:
            mgr.get_provider("legacy-wg0")
        self.assertIn("LEGACY_PROVIDER_REMOVED", str(ctx.exception))

    def test_02_member_multi_device_management_lifecycle(self) -> None:
        """Kiểm tra chu trình quản lý nhiều thiết bị cho cùng một thành viên."""
        # 1. Tạo thành viên mới
        member = self.svc.provision_member_ssh_access({
            "full_name": "Multi Device Member",
            "username": "multidev_user",
            "assigned_server": "track",
            "require_passphrase": False,
            "_actor_id": "admin_vpn",
        })
        member_id = member["id"]

        # 2. Thêm thiết bị 1 (MacBook)
        dev1 = self.svc.add_member_device(member_id, "MacBook Pro M3", actor="admin_vpn")
        self.assertEqual(dev1["name"], "MacBook Pro M3")
        self.assertEqual(dev1["status"], "ACTIVE")
        self.assertTrue(dev1["enabled"])

        # 3. Thêm thiết bị 2 (Windows PC)
        dev2 = self.svc.add_member_device(member_id, "Windows Workstation", actor="admin_vpn")
        self.assertEqual(dev2["name"], "Windows Workstation")
        self.assertEqual(dev2["status"], "ACTIVE")

        # 4. Lấy danh sách thiết bị đã đồng bộ
        devs = self.svc.get_member_devices_synced(member_id)
        self.assertGreaterEqual(len(devs), 2)
        dev_names = [d["name"] for d in devs]
        self.assertIn("MacBook Pro M3", dev_names)
        self.assertIn("Windows Workstation", dev_names)

        # 5. Lấy cấu hình của thiết bị 1
        conf1 = self.svc.get_member_device_config(member_id, dev1["device_id"])
        self.assertIn("[Interface]", conf1)
        self.assertIn("[Peer]", conf1)

        # 6. Bật/tắt thiết bị 1 (toggle)
        toggled = self.svc.toggle_member_device(member_id, dev1["device_id"], enable=False, actor="admin_vpn")
        self.assertEqual(toggled["status"], "DISABLED")
        self.assertFalse(toggled["enabled"])

        # 7. Thu hồi thiết bị 2 (revoke)
        revoked = self.svc.revoke_member_device_v2(member_id, dev2["device_id"], actor="admin_vpn")
        self.assertEqual(revoked["status"], "REVOKED")

        # Kiểm tra danh sách sau khi thu hồi: dev2 không còn trong danh sách active
        devs_after = self.svc.get_member_devices_synced(member_id)
        active_ids = [d["device_id"] for d in devs_after]
        self.assertNotIn(dev2["device_id"], active_ids)

    def test_03_http_endpoints_vpn_and_devices(self) -> None:
        """Kiểm tra các endpoint HTTP /api/v1/v2/vpn/* và /members/{id}/devices*."""
        original_service = main_mod.service
        main_mod.service = self.svc
        server = ThreadingHTTPServer(("127.0.0.1", 0), LabOpsRequestHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # 1. GET /api/v1/v2/vpn/overview
            req_ov = Request(
                f"http://127.0.0.1:{port}/api/v1/v2/vpn/overview",
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
            with urlopen(req_ov, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("primary_provider", data)

            # 2. Tạo thành viên và thêm thiết bị qua API
            member = self.svc.provision_member_ssh_access({
                "full_name": "API Device User",
                "username": "api_dev_user",
                "assigned_server": "track",
                "_actor_id": "admin_vpn",
            })
            member_id = member["id"]

            req_add = Request(
                f"http://127.0.0.1:{port}/api/v1/v2/members/{member_id}/devices",
                data=json.dumps({"name": "iPad Pro"}).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.admin_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(req_add, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                new_dev = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(new_dev["name"], "iPad Pro")
                dev_id = new_dev["device_id"]

            # 3. GET /api/v1/v2/members/{member_id}/devices
            req_list = Request(
                f"http://127.0.0.1:{port}/api/v1/v2/members/{member_id}/devices",
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
            with urlopen(req_list, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                dev_list = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(any(d["name"] == "iPad Pro" for d in dev_list))

            # 4. GET /api/v1/v2/members/{member_id}/devices/{device_id}/config
            encoded_id = quote(dev_id)
            req_cfg = Request(
                f"http://127.0.0.1:{port}/api/v1/v2/members/{member_id}/devices/{encoded_id}/config",
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
            with urlopen(req_cfg, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "text/plain")
                cfg_content = resp.read().decode("utf-8")
                self.assertIn("[Interface]", cfg_content)

            # 5. POST toggle
            req_toggle = Request(
                f"http://127.0.0.1:{port}/api/v1/v2/members/{member_id}/devices/{encoded_id}/toggle",
                data=json.dumps({"enable": False}).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.admin_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(req_toggle, timeout=5) as resp:
                self.assertEqual(resp.status, 200)

            # 6. DELETE /api/v1/v2/members/{member_id}/devices/{device_id}
            req_del = Request(
                f"http://127.0.0.1:{port}/api/v1/v2/members/{member_id}/devices/{encoded_id}",
                headers={"Authorization": f"Bearer {self.admin_token}"},
                method="DELETE",
            )
            with urlopen(req_del, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                del_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(del_data["status"], "REVOKED")

            # 7. Phân quyền: Researcher không được thêm hoặc xóa thiết bị của người khác
            req_forbidden = Request(
                f"http://127.0.0.1:{port}/api/v1/v2/members/{member_id}/devices",
                data=json.dumps({"name": "Hacked Device"}).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.researcher_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req_forbidden, timeout=5)
            self.assertEqual(ctx.exception.code, 403)

        finally:
            server.shutdown()
            server.server_close()
            main_mod.service = original_service

    def test_04_delete_member_permanently_lifecycle(self) -> None:
        """Kiểm tra xóa vĩnh viễn tài khoản theo vòng đời an toàn: Suspend -> Archive -> Delete."""
        # 1. Tạo thành viên cần xóa
        member = self.svc.provision_member_ssh_access({
            "full_name": "Delete Target User",
            "username": "delete_u",
            "assigned_server": "workstation",
            "require_passphrase": False,
            "_actor_id": "admin_vpn",
        })
        member_id = member["id"]
        self.svc.add_member_device(member_id, "Target Phone", actor="admin_vpn")

        # Không được phép xóa thẳng khi tài khoản chưa SUSPEND -> ARCHIVE
        with self.assertRaises(LifecyclePreconditionError):
            self.svc.delete_member_permanently(
                member_id,
                actor="admin_vpn",
                remove_files=True,
                confirm_username="delete_u",
                step_up_verified=True,
            )

        # 2. Thực hiện Suspend -> Archive -> Delete với xác nhận step-up
        susp = self.svc.suspend_member_lifecycle(member_id, actor="admin_vpn", reason="Offboarding test")
        self.assertEqual(susp["status"], "SUSPENDED")

        arch = self.svc.archive_member_lifecycle(member_id, actor="admin_vpn")
        self.assertEqual(arch["status"], "DELETE_ELIGIBLE")

        del_res = self.svc.delete_member_permanently(
            member_id,
            actor="admin_vpn",
            remove_files=True,
            confirm_username="delete_u",
            step_up_verified=True,
        )
        self.assertTrue(del_res["success"])
        self.assertEqual(del_res["username"], "delete_u")
        self.assertGreaterEqual(del_res["cleanup"]["wireguard_peers_revoked"], 1)

        # 3. Thành viên không còn trong danh sách
        self.assertFalse(any(m.get("username") == "delete_u" for m in self.svc.members))
        with self.assertRaises(LookupError):
            self.svc._find_member("delete_u")
        with self.assertRaises(LookupError):
            self.svc.delete_member_permanently(member_id)

    def test_05_pending_placeholder_and_dynamic_configuration(self) -> None:
        """Kiểm tra add_member_device trả về configuration động và xử lý thiết bị chờ PENDING_PROVISIONING."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "Pending Device User",
            "username": "pending_u",
            "assigned_server": "workstation",
            "require_passphrase": False,
            "_actor_id": "admin_vpn",
        })
        member_id = member["id"]

        # 1. Cài bản ghi pending placeholder giả lập trạng thái khi wg-easy từng offline
        member.setdefault("wireguard", {"status": "PENDING_PROVISIONING", "devices": []})["devices"] = [
            {
                "device_id": "dev-pending-pending_u",
                "external_id": "",
                "provider": "wg-easy",
                "name": "Thiết bị chính",
                "client_ip": "10.88.40.88",
                "status": "PENDING_PROVISIONING",
                "enabled": False,
            }
        ]

        # Không thể tải .conf hoặc bật/tắt bản ghi pending chưa đăng ký trên wg-easy
        with self.assertRaises(ValueError):
            self.svc.get_member_device_config(member_id, "dev-pending-pending_u", actor={"username": "admin_vpn", "role": "ADMIN"})
        with self.assertRaises(ValueError):
            self.svc.toggle_member_device(member_id, "dev-pending-pending_u", enable=True, actor="admin_vpn")

        # Xóa bản ghi chờ phải thành công ngay cả khi không gọi wg-easy
        rev = self.svc.revoke_member_device_v2(member_id, "dev-pending-pending_u", actor="admin_vpn")
        self.assertEqual(rev["status"], "REVOKED")
        self.assertEqual(len(member["wireguard"]["devices"]), 0)

        # 2. Cấp thiết bị mới qua wg-easy phải trả về configuration động đúng host workstation
        if not self.has_wgeasy:
            dev = self.svc.add_member_device(member_id, "WS Laptop", actor="admin_vpn")
            self.assertIn("configuration", dev)
            self.assertIn("[Interface]", dev["configuration"])
            self.assertIn("AllowedIPs = 10.88.40.0/24, 192.168.167.251/32", dev["configuration"])
            self.assertNotIn("192.168.167.250", dev["configuration"])


if __name__ == "__main__":
    unittest.main()
