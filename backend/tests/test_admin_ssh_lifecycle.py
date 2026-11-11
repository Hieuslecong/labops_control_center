"""
Kiểm thử toàn diện vòng đời quyền SSH do Admin quản lý (LabOps Control Center V2).

Bao phủ toàn bộ Mục 1..8 và Mật khẩu tạm đăng nhập lần đầu:
- Phân biệt rõ PASS_AUTOMATED và PASS_REAL (chạy thật với /usr/bin/ssh-keygen, /usr/sbin/sshd, /usr/bin/ssh, RLIMIT kernel).
- Tuyệt đối không chạm tới SSH legacy cổng 22, WireGuard, firewall hay dữ liệu thành viên hiện hữu.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

os.environ.setdefault("LABOPS_SESSION_SECRET", "test-secret-admin-ssh-lifecycle-32chars!")

from app.core.security import issue_token
from app.main import LabOpsRequestHandler
from app.services.labops_service import LabOpsDataService


class TestAdminSshLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="labops-ssh-test-")
        self.svc = LabOpsDataService(demo_mode=False, data_dir=self.tmpdir.name)
        self.admin_token = issue_token("admin_root", "ADMIN")
        self.researcher_a_token = issue_token("res_alpha", "RESEARCHER")
        self.researcher_b_token = issue_token("res_beta", "RESEARCHER")

    def tearDown(self) -> None:
        for u in list(self.svc.ssh_access_manager._canary_daemons.keys()):
            self.svc.ssh_access_manager.stop_canary_sshd_for_workspace(u)
        self.tmpdir.cleanup()

    def test_01_researcher_blocked_from_admin_ssh_endpoints_and_scoped_to_self(self) -> None:
        """[PASS_AUTOMATED] Researcher không gọi được API tạo/thu hồi/lấy gói key của Admin và không xem/đổi được mật khẩu người khác."""
        import app.main as main_mod
        original_service = main_mod.service
        main_mod.service = self.svc
        server = ThreadingHTTPServer(("127.0.0.1", 0), LabOpsRequestHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Admin tạo thành viên res_beta
            created = self.svc.provision_member_ssh_access({
                "full_name": "Researcher Beta",
                "username": "res_beta",
                "email": "res_beta@lab.ai.vn",
                "assigned_server": "track",
                "cpu_limit_cores": 4,
                "ram_limit_gb": 8,
                "storage_quota_gb": 50,
                "_actor_id": "admin_root",
            })
            member_id = created["id"]

            def call_api(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
                data = json.dumps(body).encode("utf-8") if body is not None else None
                req = Request(
                    f"http://127.0.0.1:{port}{path}",
                    data=data,
                    method=method,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                try:
                    with urlopen(req, timeout=5) as resp:
                        return resp.status, json.loads(resp.read().decode("utf-8"))
                except HTTPError as err:
                    return err.code, json.loads(err.read().decode("utf-8"))

            # Researcher A bị chặn (403) trên mọi endpoint Admin
            for path, payload in [
                ("/api/v1/v2/ssh-access/provision", {"username": "hack_user", "full_name": "Hack"}),
                (f"/api/v1/v2/ssh-access/{member_id}/claim-key-bundle", {}),
                (f"/api/v1/v2/ssh-access/{member_id}/claim-secrets", {}),
                (f"/api/v1/v2/ssh-access/{member_id}/rotate-key", {}),
                (f"/api/v1/v2/ssh-access/{member_id}/reissue-temp-password", {}),
                (f"/api/v1/v2/ssh-access/{member_id}/revoke", {}),
                (f"/api/v1/v2/ssh-access/{member_id}/terminate-sessions", {"confirm": True}),
            ]:
                status_code, _ = call_api("POST", path, self.researcher_a_token, payload)
                self.assertEqual(status_code, 403, f"Endpoint {path} phải trả 403 cho Researcher")

            # Researcher A không xem được trạng thái SSH của Researcher B
            st_a, _ = call_api("GET", f"/api/v1/v2/ssh-access/{member_id}/status", self.researcher_a_token)
            self.assertEqual(st_a, 403)

            # Researcher B xem được trạng thái của chính mình (nhưng không chứa private key hay mật khẩu)
            st_b, body_b = call_api("GET", f"/api/v1/v2/ssh-access/{member_id}/status", self.researcher_b_token)
            self.assertEqual(st_b, 200)
            self.assertEqual(body_b["username"], "res_beta")
            self.assertNotIn("encrypted_private_key", json.dumps(body_b))
            self.assertNotIn("temporary_password", json.dumps(body_b))

            # Researcher A không được đổi mật khẩu của Researcher B
            st_pw, _ = call_api(
                "POST",
                "/api/v1/v2/ssh-access/first-login-change-password",
                self.researcher_a_token,
                {"username": "res_beta", "temporary_password": "x", "new_password": "NewStrong#Pass2026"},
            )
            self.assertEqual(st_pw, 403)
        finally:
            server.shutdown()
            server.server_close()
            main_mod.service = original_service

    def test_02_provision_on_offline_host_or_without_container_never_reports_ready(self) -> None:
        """[PASS_AUTOMATED] Tạo quyền khi host offline hoặc chưa có container/quota thực tế KHÔNG bao giờ trả READY."""
        # 1. Host workstation đang offline/unknown
        m_offline = self.svc.provision_member_ssh_access({
            "full_name": "Offline Host User",
            "username": "user_offline",
            "assigned_server": "workstation",
            "cpu_limit_cores": 4,
            "ram_limit_gb": 8,
            "storage_quota_gb": 50,
            "_actor_id": "admin_root",
        })
        self.assertNotEqual(m_offline["status"], "READY")
        self.assertEqual(m_offline["provisioning_state"], "NOT_VERIFIED")
        self.assertFalse(m_offline["runtime_verification"]["host_online"])

        # 2. Host track đang online nhưng chưa bật container/canary runtime và ext4 không hỗ trợ quota thư mục
        m_unverified_quota = self.svc.provision_member_ssh_access({
            "full_name": "Track Pending Container",
            "username": "user_no_container",
            "assigned_server": "track",
            "cpu_limit_cores": 4,
            "ram_limit_gb": 8,
            "storage_quota_gb": 50,
            "enforce_canary_runtime": False,
            "_actor_id": "admin_root",
        })
        self.assertNotEqual(m_unverified_quota["status"], "READY")
        self.assertIn(m_unverified_quota["provisioning_state"], ("NOT_ENFORCEABLE", "NOT_VERIFIED"))
        self.assertEqual(m_unverified_quota["runtime_verification"]["directory_isolation"], "VERIFIED")
        self.assertEqual(m_unverified_quota["runtime_verification"]["ssh_key_installed"], "VERIFIED")
        self.assertEqual(m_unverified_quota["runtime_verification"]["storage_quota"], "NOT_ENFORCEABLE")

    def test_03_private_key_passphrase_and_temp_password_never_in_disk_logs_or_audit(self) -> None:
        """[PASS_REAL] Private key, passphrase và mật khẩu tạm không bao giờ xuất hiện trên đĩa, audit log hay API khác."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "Secret Audit User",
            "username": "sec_audit_u",
            "assigned_server": "track",
            "cpu_limit_cores": 4,
            "ram_limit_gb": 8,
            "storage_quota_gb": 20,
            "_actor_id": "admin_root",
        })
        bundle = self.svc.claim_member_key_bundle(member["id"], actor="admin_root")
        secrets_pkg = self.svc.claim_member_secrets(member["id"], actor="admin_root")

        priv_key = bundle["encrypted_private_key"]
        passphrase = secrets_pkg["key_passphrase"]
        temp_pw = secrets_pkg["temporary_password"]

        self.assertIn("BEGIN OPENSSH PRIVATE KEY", priv_key)
        self.assertTrue(len(passphrase) >= 20)
        self.assertTrue(len(temp_pw) >= 16)

        # Quét toàn bộ tệp JSON trên đĩa trong data_dir + audit_logs
        for root, _, files in os.walk(self.tmpdir.name):
            for fname in files:
                fpath = os.path.join(root, fname)
                if not os.path.isfile(fpath):
                    continue
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", content, f"Lộ private key trong {fpath}")
                self.assertNotIn(passphrase, content, f"Lộ passphrase trong {fpath}")
                self.assertNotIn(temp_pw, content, f"Lộ mật khẩu tạm trong {fpath}")

        # Kiểm tra danh sách members và audit_logs qua API
        serialized_members = json.dumps(self.svc.members)
        serialized_audits = json.dumps(self.svc.audit_logs)
        for secret_val in ("BEGIN OPENSSH PRIVATE KEY", passphrase, temp_pw):
            self.assertNotIn(secret_val, serialized_members)
            self.assertNotIn(secret_val, serialized_audits)

    def test_04_one_time_key_bundle_and_separate_channel_secrets_policy(self) -> None:
        """[PASS_AUTOMATED] Chỉ tải được gói key và passphrase/mật khẩu tạm đúng 1 lần; cấp lại khóa sẽ thu hồi khóa cũ."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "One Time User",
            "username": "onetime_u",
            "assigned_server": "track",
            "_actor_id": "admin_root",
        })
        old_fp = member["ssh_fingerprint"]

        # Lần 1 thành công (bao gồm cả file WireGuard .conf, SSH config snippet và script cài đặt nhanh)
        b1 = self.svc.claim_member_key_bundle(member["id"], actor="admin_root")
        s1 = self.svc.claim_member_secrets(member["id"], actor="admin_root")
        self.assertEqual(b1["fingerprint"], old_fp)
        self.assertIn("[Interface]", b1["wireguard_conf"])
        self.assertIn("[Peer]", b1["wireguard_conf"])
        self.assertIn("Host labops-onetime_u", b1["ssh_config_snippet"])
        self.assertIn("id_ed25519_onetime_u", b1["quick_setup_script"])
        self.assertTrue(s1["key_passphrase"])

        # Lần 2 bị từ chối (LookupError -> HTTP 410)
        with self.assertRaises(LookupError):
            self.svc.claim_member_key_bundle(member["id"], actor="admin_root")
        with self.assertRaises(LookupError):
            self.svc.claim_member_secrets(member["id"], actor="admin_root")

        # Khi khóa bị mất/lộ -> Admin cấp lại cặp khóa mới (rotate_member_ssh_keypair)
        rotated = self.svc.rotate_member_ssh_keypair(member["id"], actor="admin_root")
        new_fp = rotated["ssh_fingerprint"]
        self.assertNotEqual(old_fp, new_fp)

        # Khóa cũ trong SshKeyRegistry phải ở trạng thái REVOKED
        keys = self.svc.ssh_key_registry.list_keys(username="onetime_u")
        old_rec = next(k for k in keys if k["fingerprint"] == old_fp)
        self.assertEqual(old_rec["desired_state"], "REVOKED")

        # Gói mới lại tải được đúng 1 lần
        b2 = self.svc.claim_member_key_bundle(member["id"], actor="admin_root")
        self.assertEqual(b2["fingerprint"], new_fp)

    def test_05_temporary_password_first_login_expiry_reissue_and_no_reuse(self) -> None:
        """[PASS_AUTOMATED] Kiểm thử đăng nhập lần đầu, bắt buộc đổi mật khẩu, mật khẩu tạm hết hạn, cấp lại và cấm dùng lại."""
        # 1. Tạo thành viên với mật khẩu tạm đã hết hạn (ttl = -10 giây)
        member = self.svc.provision_member_ssh_access({
            "full_name": "Temp Password User",
            "username": "temppw_u",
            "assigned_server": "track",
            "temp_password_ttl_seconds": -10,
            "_actor_id": "admin_root",
        })
        expired_secrets = self.svc.claim_member_secrets(member["id"], actor="admin_root")
        expired_pw = expired_secrets["temporary_password"]

        # Đăng nhập hoặc đổi mật khẩu bằng mật khẩu tạm đã hết hạn PHẢI bị từ chối
        with self.assertRaisesRegex(PermissionError, "TEMP_PASSWORD_EXPIRED"):
            self.svc.verify_member_workspace_password("temppw_u", expired_pw)
        with self.assertRaisesRegex(PermissionError, "TEMP_PASSWORD_EXPIRED"):
            self.svc.change_member_first_login_password("temppw_u", expired_pw, "NewValidPass#2026!")

        # 2. Admin cấp lại mật khẩu tạm mới (ttl = 3600s) -> mật khẩu tạm cũ lập tức vô hiệu
        self.svc.reissue_member_temp_password(member["id"], actor="admin_root", ttl_seconds=3600)
        reissued_1 = self.svc.claim_member_secrets(member["id"], actor="admin_root")["temporary_password"]

        # Cấp lại lần nữa để kiểm tra mật khẩu tạm reissued_1 bị hủy hiệu lực ngay cả khi chưa hết hạn
        self.svc.reissue_member_temp_password(member["id"], actor="admin_root", ttl_seconds=3600)
        reissued_2 = self.svc.claim_member_secrets(member["id"], actor="admin_root")["temporary_password"]

        with self.assertRaisesRegex(PermissionError, "INVALID_CREDENTIALS"):
            self.svc.verify_member_workspace_password("temppw_u", reissued_1)

        # 3. Đăng nhập lần đầu bằng reissued_2: xác thực đúng nhưng CHƯA cho phép dùng workspace trước khi đổi mật khẩu
        login_check = self.svc.verify_member_workspace_password("temppw_u", reissued_2)
        self.assertTrue(login_check["authenticated"])
        self.assertTrue(login_check["must_change_password"])
        self.assertFalse(login_check["workspace_access_granted"])

        # Không cho phép đặt mật khẩu mới trùng mật khẩu tạm
        with self.assertRaisesRegex(ValueError, "NEW_PASSWORD_MUST_DIFFER"):
            self.svc.change_member_first_login_password("temppw_u", reissued_2, reissued_2)

        # 4. Đổi sang mật khẩu mới hợp lệ
        new_pw = "Researcher#Strong2026!"
        chg = self.svc.change_member_first_login_password("temppw_u", reissued_2, new_pw)
        self.assertEqual(chg["password_status"], "ACTIVE")
        self.assertTrue(chg["temp_password_invalidated"])
        self.assertTrue(chg["workspace_access_granted"])

        # 5. Sau khi đổi thành công, mật khẩu tạm reissued_2 hết hiệu lực hoàn toàn và không thể dùng lại
        with self.assertRaises(PermissionError):
            self.svc.verify_member_workspace_password("temppw_u", reissued_2)
        with self.assertRaisesRegex(PermissionError, "TEMP_PASSWORD_ALREADY_USED"):
            self.svc.change_member_first_login_password("temppw_u", reissued_2, "AnotherStrong#2026!")

        # Đăng nhập bằng mật khẩu mới được cấp quyền sử dụng workspace
        active_check = self.svc.verify_member_workspace_password("temppw_u", new_pw)
        self.assertTrue(active_check["workspace_access_granted"])
        self.assertFalse(active_check["must_change_password"])

    def test_06_revoke_on_offline_host_stays_pending_revoke(self) -> None:
        """[PASS_AUTOMATED] Key thu hồi khi host offline vẫn ở PENDING_REVOKE, không bao giờ báo VERIFIED_REVOKED."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "Offline Revoke User",
            "username": "rev_offline",
            "assigned_server": "workstation",
            "_actor_id": "admin_root",
        })
        res = self.svc.revoke_member_ssh_access(member["id"], actor="admin_root")
        self.assertEqual(res["status"], "PENDING_REVOKE")
        self.assertFalse(res["verified_revoked"])
        self.assertEqual(member["ssh_key_status"], "PENDING_REVOKE")

    def test_07_real_canary_sshd_passphrase_key_connect_and_revoke_blocks_new_connections(self) -> None:
        """[PASS_REAL] Canary sshd thật trên aiotlab-rack: kết nối bằng private key có passphrase -> READY -> thu hồi -> chặn kết nối mới (VERIFIED_REVOKED)."""
        member = self.svc.provision_member_ssh_access({
            "full_name": "Canary Real SSH User",
            "username": "canary_real",
            "assigned_server": "track",
            "cpu_limit_cores": 4,
            "ram_limit_gb": 8,
            "storage_quota_gb": 20,
            "enforce_canary_runtime": True,
            "_actor_id": "admin_root",
        })
        # Đã qua toàn bộ kiểm tra runtime thực tế trên máy thật -> READY
        self.assertEqual(member["status"], "READY")
        self.assertEqual(member["provisioning_state"], "READY")
        self.assertEqual(member["runtime_verification"]["overall_status"], "READY")

        bundle = self.svc.claim_member_key_bundle(member["id"], actor="admin_root")
        secrets_pkg = self.svc.claim_member_secrets(member["id"], actor="admin_root")

        # 1. Kết nối SSH thực tế bằng /usr/bin/ssh với private key mã hóa passphrase
        probe_before = self.svc.ssh_access_manager.verify_canary_ssh_connection(
            port=int(member["ssh_port"]),
            encrypted_private_key=bundle["encrypted_private_key"],
            passphrase=secrets_pkg["key_passphrase"],
        )
        self.assertTrue(probe_before["accepted"], f"Canary SSH phải kết nối thành công trước khi thu hồi: {probe_before}")

        # 2. Thu hồi quyền SSH và xác minh trực tiếp trên tiến trình sshd đang chạy
        rev = self.svc.revoke_member_ssh_access(
            member["id"],
            actor="admin_root",
            encrypted_private_key_for_probe=bundle["encrypted_private_key"],
            passphrase_for_probe=secrets_pkg["key_passphrase"],
        )
        self.assertEqual(rev["status"], "VERIFIED_REVOKED")
        self.assertTrue(rev["verified_revoked"])
        self.assertTrue(rev["new_connections_blocked"])

        # 3. Thử kết nối mới lần nữa sau khi thu hồi -> bị sshd từ chối (Permission denied)
        probe_after = self.svc.ssh_access_manager.verify_canary_ssh_connection(
            port=int(member["ssh_port"]),
            encrypted_private_key=bundle["encrypted_private_key"],
            passphrase=secrets_pkg["key_passphrase"],
        )
        self.assertFalse(probe_after["accepted"])
        self.assertNotEqual(probe_after["returncode"], 0)

        # 4. Đóng tiến trình phiên SSH của workspace bằng thao tác riêng có xác nhận
        term = self.svc.terminate_member_ssh_sessions(member["id"], actor="admin_root", confirm=True)
        self.assertEqual(term["status"], "VERIFIED_TERMINATED")
        self.assertEqual(term["terminated_daemons"], 1)

    def test_08_two_canary_workspaces_isolated_and_runtime_limits_verified(self) -> None:
        """[PASS_REAL] Hai workspace canary cách ly hoàn toàn (0700), chặn mount chéo/Docker socket/privileged và kiểm chứng RLIMIT tại runtime."""
        self.svc.provision_member_ssh_access({
            "full_name": "Canary Member A",
            "username": "canary_a",
            "assigned_server": "track",
            "cpu_limit_cores": 2,
            "ram_limit_gb": 4,
            "storage_quota_gb": 10,
            "_actor_id": "admin_root",
        })
        self.svc.provision_member_ssh_access({
            "full_name": "Canary Member B",
            "username": "canary_b",
            "assigned_server": "track",
            "cpu_limit_cores": 2,
            "ram_limit_gb": 4,
            "storage_quota_gb": 10,
            "_actor_id": "admin_root",
        })
        iso = self.svc.ssh_access_manager.verify_cross_member_isolation("track", "canary_a", "canary_b")
        self.assertTrue(iso["isolated"], f"Hai workspace phải cách ly hoàn toàn: {iso}")
        self.assertEqual(iso["member_a_mode"], "0o700")
        self.assertEqual(iso["member_b_mode"], "0o700")
        self.assertEqual(iso["other_permissions_bits"], 0)
        self.assertTrue(iso["cross_mount_policy_blocked"])

        # Kiểm chứng RLIMIT tại runtime thực sự áp dụng trong kernel cho tiến trình con
        ws_a = self.svc.ssh_access_manager.member_workspace_dir("track", "canary_a")
        self.assertTrue(self.svc.ssh_access_manager._verify_rlimit_enforcement_canary(ws_a, 2, 4, 10))

    def test_09_zip_package_download_and_structure(self) -> None:
        """[PASS_REAL] Tải trọn gói kết nối .zip, kiểm tra cấu trúc thư mục chuẩn, claim-once và endpoint HTTP."""
        import io
        import zipfile
        import app.main as main_mod

        # 1. Tạo thành viên mới với SSH key do hệ thống sinh
        created = self.svc.provision_member_ssh_access({
            "full_name": "Canary Zip Member",
            "username": "canary_zip",
            "assigned_server": "track",
            "cpu_limit_cores": 2,
            "ram_limit_gb": 4,
            "storage_quota_gb": 10,
            "require_passphrase": False,
            "_actor_id": "admin_root",
        })
        member_id = created["id"]
        username = created["username"]

        # 2. Xuất gói ZIP lần 1 qua service
        filename, zip_bytes = self.svc.claim_member_access_zip(member_id, actor="admin_root")
        self.assertEqual(filename, f"LabOps_Access_{username}.zip")
        self.assertGreater(len(zip_bytes), 1000)

        # 3. Mở gói ZIP và kiểm tra cấu trúc thư mục
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            namelist = set(zf.namelist())
            prefix = f"LabOps_Access_{username}"
            
            # Các tệp bắt buộc phải có
            self.assertIn(f"{prefix}/README.md", namelist)
            self.assertIn(f"{prefix}/wireguard/labops.conf", namelist)
            self.assertIn(f"{prefix}/wireguard/SETUP.md", namelist)
            self.assertIn(f"{prefix}/ssh/config", namelist)
            self.assertIn(f"{prefix}/ssh/id_ed25519_{username}.pub", namelist)
            self.assertIn(f"{prefix}/ssh/id_ed25519_{username}", namelist)
            self.assertIn(f"{prefix}/ssh/SETUP.md", namelist)
            self.assertIn(f"{prefix}/connection/connection-info.json", namelist)
            self.assertIn(f"{prefix}/connection/connection-info.txt", namelist)
            self.assertIn(f"{prefix}/connection/SSH_COMMAND.txt", namelist)
            self.assertIn(f"{prefix}/scripts/setup-macos.sh", namelist)
            self.assertIn(f"{prefix}/scripts/setup-linux.sh", namelist)
            self.assertIn(f"{prefix}/scripts/setup-windows.ps1", namelist)

            # Kiểm tra nội dung khóa riêng và cấu hình
            priv_content = zf.read(f"{prefix}/ssh/id_ed25519_{username}").decode("utf-8")
            self.assertIn("-----BEGIN OPENSSH PRIVATE KEY-----", priv_content)
            
            wg_content = zf.read(f"{prefix}/wireguard/labops.conf").decode("utf-8")
            self.assertIn("[Interface]", wg_content)
            self.assertIn("[Peer]", wg_content)

            ssh_cfg = zf.read(f"{prefix}/ssh/config").decode("utf-8")
            self.assertIn(f"Host labops-{username}", ssh_cfg)
            self.assertIn(f"User {username}", ssh_cfg)

        # 4. Kiểm tra claim-once: Khóa riêng đã được giải phóng khỏi RAM sau khi tải gói ZIP
        avail = self.svc.ssh_access_manager.vault.get_availability(member_id)
        self.assertFalse(avail["key_bundle_available"])

        # 5. Kiểm tra tải lại gói ZIP sau khi khóa riêng đã giải phóng
        filename2, zip_bytes2 = self.svc.claim_member_access_zip(member_id, actor="admin_root")
        with zipfile.ZipFile(io.BytesIO(zip_bytes2), "r") as zf2:
            namelist2 = set(zf2.namelist())
            self.assertIn(f"{prefix}/wireguard/labops.conf", namelist2)
            self.assertIn(f"{prefix}/ssh/config", namelist2)
            # Khóa riêng đã giải phóng nên không còn trong ssh/
            self.assertNotIn(f"{prefix}/ssh/id_ed25519_{username}", namelist2)

        # 6. Kiểm tra Cấp lại cặp khóa mới (rotate-key) rồi tải gói ZIP qua HTTP endpoint
        self.svc.rotate_member_ssh_keypair(member_id, actor="admin_root", require_passphrase=False)
        avail_after_rotate = self.svc.ssh_access_manager.vault.get_availability(member_id)
        self.assertTrue(avail_after_rotate["key_bundle_available"])

        # Chạy HTTP server để test GET /api/v1/v2/ssh-access/{id}/download-zip
        original_service = main_mod.service
        main_mod.service = self.svc
        server = ThreadingHTTPServer(("127.0.0.1", 0), LabOpsRequestHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = Request(
                f"http://127.0.0.1:{port}/api/v1/v2/ssh-access/{member_id}/download-zip",
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
            with urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "application/zip")
                disp = resp.headers.get("Content-Disposition")
                self.assertIn(f'filename="LabOps_Access_{username}.zip"', disp)
                http_zip_bytes = resp.read()
                self.assertGreater(len(http_zip_bytes), 1000)

                with zipfile.ZipFile(io.BytesIO(http_zip_bytes), "r") as http_zf:
                    self.assertIn(f"{prefix}/ssh/id_ed25519_{username}", set(http_zf.namelist()))
        finally:
            server.shutdown()
            server.server_close()
            main_mod.service = original_service

        # 7. Kiểm tra audit log đã ghi nhận sự kiện tải ZIP
        audit_actions = [log["action"] for log in self.svc.audit_logs]
        self.assertIn("ACCESS_ZIP_PACKAGE_DOWNLOADED", audit_actions)


if __name__ == "__main__":
    unittest.main()

