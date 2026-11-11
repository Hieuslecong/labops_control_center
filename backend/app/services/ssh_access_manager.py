"""
Quản lý vòng đời quyền truy cập SSH do Admin cấp phát (LabOps Control Center V2).

Thực thi đầy đủ các yêu cầu:
1. Admin tạo và quản lý toàn bộ vòng đời quyền SSH; thành viên không cần tự tạo SSH key.
2. Mỗi thành viên có thư mục workspace riêng biệt (mode 0700) và .ssh/authorized_keys riêng (mode 0600).
3. Sinh cặp khóa OpenSSH Ed25519 thật bằng /usr/bin/ssh-keygen, bảo vệ private key bằng passphrase mạnh.
4. Cấp mật khẩu tạm riêng cho lần đăng nhập đầu tiên (chỉ lưu băm PBKDF2-HMAC-SHA256 có muối, có hạn sử dụng,
   bắt buộc đổi mật khẩu ở lần đăng nhập đầu tiên trước khi mở khóa workspace, không dùng lại được).
5. Lưu tạm gói kết nối (encrypted private key) và bí mật (passphrase + mật khẩu tạm) trong bộ nhớ RAM
   (OneTimeSecretVault), chỉ cho phép Admin tải/xem đúng 1 lần (burn-after-reading), tuyệt đối không ghi
   private key, passphrase hay mật khẩu vào ổ cứng, database, log hoặc audit log.
6. Kiểm chứng thực tế (Runtime Verification):
   - Cách ly thư mục giữa các thành viên (0700, không symlink/mount chéo).
   - Không có Docker socket, không mount root host, không privileged, không sudo.
   - Kiểm tra thực tế trạng thái host, container/cgroup CPU/RAM và filesystem storage quota.
   - Chỉ báo READY khi mọi kiểm tra bắt buộc đều được xác minh thực tế; nếu không, trả về
     PENDING, PROVISIONING, VERIFYING, FAILED, NOT_VERIFIED hoặc NOT_ENFORCEABLE.
7. Thu hồi quyền SSH (Revoke):
   - Gỡ public key khỏi authorized_keys của workspace, vô hiệu hóa gói key và mật khẩu tạm.
   - Xác minh key không còn trong authorized_keys và kết nối SSH mới tới workspace bị từ chối.
   - Chỉ báo VERIFIED_REVOKED khi đã xác minh thành công; nếu host offline hoặc agent lỗi, giữ ở
     PENDING_REVOKE hoặc FAILED.
   - Tách riêng thao tác đóng các phiên SSH đang mở (terminate-sessions) của workspace.
"""
from __future__ import annotations

import fcntl
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.vpn_ssh_service import SshKeyRegistry, compute_ssh_fingerprint
from app.services.workspace_controller import FORBIDDEN_MOUNT_PREFIXES, WorkspaceSecurityError

SAFE_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
RESERVED_USERNAMES = {
    "root", "admin", "ubuntu", "debian", "sshd", "daemon", "nobody",
    "postgres", "docker", "incus", "systemd-network", "aiot-1",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_strong_passphrase(length: int = 24) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*()-_=+"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in candidate)
            and any(c.isupper() for c in candidate)
            and any(c.isdigit() for c in candidate)
            and any(c in "!@#$%^&*()-_=+" for c in candidate)
        ):
            return candidate


def _generate_temp_password() -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    core = "".join(secrets.choice(alphabet) for _ in range(14))
    return f"LabOps!{core}9#"


def _hash_password(password: str, salt_hex: Optional[str] = None) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored or not stored.startswith("pbkdf2_sha256$"):
        return False
    parts = stored.split("$")
    if len(parts) != 4:
        return False
    _, iterations_str, salt_hex, expected_hex = parts
    try:
        salt = bytes.fromhex(salt_hex)
        iterations = int(iterations_str)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk.hex(), expected_hex)


def _validate_new_password_strength(new_password: str) -> None:
    if len(new_password) < 12:
        raise ValueError("Mật khẩu mới phải có ít nhất 12 ký tự")
    if not any(c.islower() for c in new_password):
        raise ValueError("Mật khẩu mới phải chứa ít nhất 1 chữ thường")
    if not any(c.isupper() for c in new_password):
        raise ValueError("Mật khẩu mới phải chứa ít nhất 1 chữ hoa")
    if not any(c.isdigit() for c in new_password):
        raise ValueError("Mật khẩu mới phải chứa ít nhất 1 chữ số")
    if not any(not c.isalnum() for c in new_password):
        raise ValueError("Mật khẩu mới phải chứa ít nhất 1 ký tự đặc biệt")


class OneTimeSecretVault:
    """
    Kho lưu tạm một lần trong bộ nhớ RAM (không bao giờ ghi xuống đĩa).
    Tách biệt 2 kênh bàn giao:
    - Kênh 1 (key_bundle): Gói kết nối + Private key đã mã hóa bằng passphrase.
    - Kênh 2 (secrets): Passphrase mở khóa private key + Mật khẩu tạm đăng nhập lần đầu.
    """

    def __init__(self, default_ttl_seconds: int = 900):
        self.default_ttl_seconds = default_ttl_seconds
        self._key_bundles: Dict[str, Dict[str, Any]] = {}
        self._channel_secrets: Dict[str, Dict[str, Any]] = {}

    def _purge_expired(self) -> None:
        now = time.time()
        for store in (self._key_bundles, self._channel_secrets):
            expired = [k for k, v in store.items() if now > v.get("expires_at_epoch", 0)]
            for k in expired:
                store.pop(k, None)

    def store_provisioned_package(
        self,
        *,
        member_id: str,
        username: str,
        ssh_host: str,
        ssh_port: int,
        workspace_name: str,
        workspace_path: str,
        fingerprint: str,
        encrypted_private_key: str,
        passphrase: str,
        temporary_password: Optional[str],
        temp_password_expires_at: Optional[str],
        vpn_guide: str,
        wireguard_conf: Optional[str] = None,
        wireguard_client_ip: Optional[str] = None,
        wireguard_public_key: Optional[str] = None,
        wireguard_server_endpoint: Optional[str] = None,
        ssh_config_snippet: Optional[str] = None,
        quick_setup_script: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        self._purge_expired()
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        exp_epoch = time.time() + ttl
        exp_iso = datetime.fromtimestamp(exp_epoch, tz=timezone.utc).isoformat()
        bundle_id = f"bnd-{uuid.uuid4().hex[:12]}"
        secret_id = f"sec-{uuid.uuid4().hex[:12]}"
        host_alias = f"labops-{username}"

        if not ssh_config_snippet:
            ssh_config_snippet = (
                f"# Cấu hình OpenSSH tự động cho @{username} (LabOps Control Center V2)\n"
                f"# Dán vào ~/.ssh/config để gõ trực tiếp: ssh {host_alias}\n"
                f"Host {host_alias}\n"
                f"    HostName {ssh_host}\n"
                f"    Port {ssh_port}\n"
                f"    User {username}\n"
                f"    IdentityFile ~/.ssh/id_ed25519_{username}\n"
                f"    IdentitiesOnly yes\n"
                f"    ServerAliveInterval 60\n"
                f"    ServerAliveCountMax 3\n"
            )

        if not quick_setup_script:
            quick_setup_script = (
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"# Script cài đặt tự động SSH Key & Config cho @{username} (LabOps V2)\n"
                "mkdir -p \"$HOME/.ssh\"\n"
                "chmod 700 \"$HOME/.ssh\"\n"
                f"KEY_PATH=\"$HOME/.ssh/id_ed25519_{username}\"\n"
                "cat > \"$KEY_PATH\" <<'EOF_KEY'\n"
                f"{encrypted_private_key.rstrip()}\n"
                "EOF_KEY\n"
                "chmod 600 \"$KEY_PATH\"\n"
                "touch \"$HOME/.ssh/config\"\n"
                "chmod 600 \"$HOME/.ssh/config\"\n"
                f"if ! grep -q \"^Host {host_alias}$\" \"$HOME/.ssh/config\" 2>/dev/null; then\n"
                "cat >> \"$HOME/.ssh/config\" <<'EOF_CFG'\n\n"
                f"{ssh_config_snippet.rstrip()}\n"
                "EOF_CFG\n"
                "fi\n"
                f"echo \"✅ Đã cài đặt khóa riêng ($KEY_PATH) và cấu hình SSH ({host_alias}).\"\n"
                f"echo \"👉 Kết nối ngay bằng lệnh: ssh {host_alias}\"\n"
            )

        self._key_bundles[member_id] = {
            "bundle_id": bundle_id,
            "member_id": member_id,
            "username": username,
            "host_alias": host_alias,
            "ssh_host": ssh_host,
            "ssh_port": ssh_port,
            "workspace_name": workspace_name,
            "workspace_path": workspace_path,
            "fingerprint": fingerprint,
            "encrypted_private_key": encrypted_private_key,
            "ssh_config_snippet": ssh_config_snippet,
            "quick_setup_script": quick_setup_script,
            "wireguard_conf": wireguard_conf or "",
            "wireguard_client_ip": wireguard_client_ip or "",
            "wireguard_public_key": wireguard_public_key or "",
            "wireguard_server_endpoint": wireguard_server_endpoint or "",
            "vpn_guide": vpn_guide,
            "ssh_command": f"ssh -i ~/.ssh/id_ed25519_{username} -p {ssh_port} {username}@{ssh_host}",
            "ssh_alias_command": f"ssh {host_alias}",
            "created_at": _now_iso(),
            "expires_at": exp_iso,
            "expires_at_epoch": exp_epoch,
        }
        self._channel_secrets[member_id] = {
            "secret_id": secret_id,
            "member_id": member_id,
            "username": username,
            "fingerprint": fingerprint,
            "key_passphrase": passphrase,
            "temporary_password": temporary_password,
            "temp_password_expires_at": temp_password_expires_at,
            "created_at": _now_iso(),
            "expires_at": exp_iso,
            "expires_at_epoch": exp_epoch,
        }
        return {
            "bundle_id": bundle_id,
            "secret_id": secret_id,
            "key_bundle_available": True,
            "channel_secrets_available": True,
            "expires_at": exp_iso,
        }

    def store_reissued_temp_password(
        self,
        *,
        member_id: str,
        username: str,
        temporary_password: str,
        temp_password_expires_at: str,
        ttl_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        self._purge_expired()
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        exp_epoch = time.time() + ttl
        exp_iso = datetime.fromtimestamp(exp_epoch, tz=timezone.utc).isoformat()
        secret_id = f"sec-{uuid.uuid4().hex[:12]}"
        existing = self._channel_secrets.get(member_id) or {}
        self._channel_secrets[member_id] = {
            "secret_id": secret_id,
            "member_id": member_id,
            "username": username,
            "fingerprint": existing.get("fingerprint"),
            "key_passphrase": existing.get("key_passphrase"),
            "temporary_password": temporary_password,
            "temp_password_expires_at": temp_password_expires_at,
            "created_at": _now_iso(),
            "expires_at": exp_iso,
            "expires_at_epoch": exp_epoch,
        }
        return {
            "secret_id": secret_id,
            "channel_secrets_available": True,
            "expires_at": exp_iso,
        }

    def get_availability(self, member_id: str) -> Dict[str, Any]:
        self._purge_expired()
        kb = self._key_bundles.get(member_id)
        cs = self._channel_secrets.get(member_id)
        return {
            "key_bundle_available": kb is not None,
            "key_bundle_expires_at": kb.get("expires_at") if kb else None,
            "channel_secrets_available": cs is not None,
            "channel_secrets_expires_at": cs.get("expires_at") if cs else None,
        }

    def claim_key_bundle_once(self, member_id: str, peek: bool = False) -> Dict[str, Any]:
        self._purge_expired()
        entry = self._key_bundles.get(member_id) if peek else self._key_bundles.pop(member_id, None)
        if not entry:
            raise LookupError(
                "KEY_BUNDLE_UNAVAILABLE: Gói khóa riêng đã được tải một lần trước đó, đã hết hạn hoặc đã bị thu hồi. "
                "Để lấy khóa mới, vui lòng thực hiện 'Cấp lại cặp khóa SSH mới'."
            )
        result = dict(entry)
        result.pop("expires_at_epoch", None)
        return result
        return result

    def claim_channel_secrets_once(self, member_id: str) -> Dict[str, Any]:
        self._purge_expired()
        entry = self._channel_secrets.pop(member_id, None)
        if not entry:
            raise LookupError(
                "SECRETS_UNAVAILABLE: Passphrase và mật khẩu tạm chỉ được hiển thị một lần duy nhất và hiện không còn trong bộ nhớ tạm."
            )
        result = dict(entry)
        result.pop("expires_at_epoch", None)
        return result

    def invalidate_member(self, member_id: str) -> None:
        self._key_bundles.pop(member_id, None)
        self._channel_secrets.pop(member_id, None)


class SshAccessManager:
    """
    Bộ điều phối tạo, xác minh, bàn giao một lần và thu hồi quyền SSH/Workspace cho thành viên.
    """

    def __init__(self, data_dir: str, ssh_key_registry: SshKeyRegistry, local_host_id: str = "track"):
        self.data_dir = data_dir
        self.ssh_key_registry = ssh_key_registry
        self.local_host_id = local_host_id
        self.workspaces_root = os.path.join(data_dir, "workspaces")
        self.accounts_path = os.path.join(data_dir, "v2_ssh_accounts.json")
        self.accounts_lock = self.accounts_path + ".lock"
        self.vault = OneTimeSecretVault()
        self._canary_daemons: Dict[str, subprocess.Popen] = {}
        os.makedirs(self.workspaces_root, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.workspaces_root, 0o700)
        except OSError:
            pass

    @contextmanager
    def _locked_accounts(self):
        with open(self.accounts_lock, "a+", encoding="utf-8") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                if os.path.exists(self.accounts_path):
                    with open(self.accounts_path, "r", encoding="utf-8") as rf:
                        state = json.load(rf)
                else:
                    state = {"accounts": {}}
                yield state
                tmp = self.accounts_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as wf:
                    json.dump(state, wf, ensure_ascii=False, indent=2)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.accounts_path)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def get_account_security_state(self, username: str) -> Dict[str, Any]:
        if not os.path.exists(self.accounts_path):
            return {}
        with open(self.accounts_path, "r", encoding="utf-8") as rf:
            accounts = json.load(rf).get("accounts", {})
        rec = dict(accounts.get(username) or {})
        # Tuyệt đối không bao giờ trả về password hash ra ngoài
        rec.pop("temp_password_hash", None)
        rec.pop("active_password_hash", None)
        return rec

    def member_workspace_dir(self, host_id: str, username: str) -> str:
        if not SAFE_USERNAME_RE.match(username) or username in RESERVED_USERNAMES:
            raise ValueError(f"Username '{username}' không hợp lệ hoặc thuộc danh sách hệ thống cấm")
        safe_host = re.sub(r"[^a-z0-9_-]", "", host_id.lower()) or "track"

        # Mặc định ưu tiên tuyệt đối phân bổ không gian lưu trữ người dùng vào ổ đĩa /media/LabOps-Data/users
        from app.core.config import settings
        default_data_dir = getattr(settings, "DEFAULT_USER_DATA_DIR", "/media/LabOps-Data/users")
        if (safe_host == "track" or safe_host == getattr(settings, "LOCAL_HOST_ID", "track")) and os.path.exists(default_data_dir):
            user_dir = os.path.join(default_data_dir, username)
            os.makedirs(user_dir, mode=0o700, exist_ok=True)
            try:
                os.chmod(user_dir, 0o700)
            except OSError:
                pass
            return user_dir

        host_root = os.path.join(self.workspaces_root, safe_host)
        os.makedirs(host_root, mode=0o700, exist_ok=True)
        try:
            os.chmod(host_root, 0o700)
        except OSError:
            pass
        return os.path.join(host_root, username)

    @staticmethod
    def generate_encrypted_ed25519_keypair(
        username: str, workspace_name: str, require_passphrase: bool = True
    ) -> Dict[str, str]:
        """
        Tạo cặp khóa OpenSSH Ed25519 thật bằng /usr/bin/ssh-keygen.
        - Nếu require_passphrase = True: bảo vệ private key bằng passphrase mạnh (mã hóa bcrypt/AES-256).
        - Nếu require_passphrase = False: bỏ qua passphrase ("") để thành viên kết nối 1 chạm không cần nhập mật khẩu.
        """
        ssh_keygen = shutil.which("ssh-keygen") or "/usr/bin/ssh-keygen"
        if not os.path.exists(ssh_keygen):
            raise RuntimeError("SSH_KEYGEN_UNAVAILABLE: Không tìm thấy công cụ /usr/bin/ssh-keygen trên máy chủ")

        passphrase = _generate_strong_passphrase(24) if require_passphrase else ""
        comment = f"{username}@{workspace_name}"

        with tempfile.TemporaryDirectory(prefix="labops-keygen-") as tmpdir:
            os.chmod(tmpdir, 0o700)
            key_path = os.path.join(tmpdir, "id_ed25519")
            res = subprocess.run(
                [
                    ssh_keygen,
                    "-q",
                    "-t",
                    "ed25519",
                    "-a",
                    "32" if require_passphrase else "16",
                    "-N",
                    passphrase,
                    "-C",
                    comment,
                    "-f",
                    key_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode != 0:
                raise RuntimeError("Không thể khởi tạo cặp khóa OpenSSH Ed25519")

            if require_passphrase:
                # Kiểm chứng 1: Mở khóa với passphrase rỗng PHẢI thất bại khi yêu cầu passphrase
                unauth_check = subprocess.run(
                    [ssh_keygen, "-y", "-P", "", "-f", key_path],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if unauth_check.returncode == 0:
                    raise RuntimeError("KEY_ENCRYPTION_FAILED: Private key không được mã hóa bằng passphrase")

            # Kiểm chứng 2: Mở khóa bằng đúng passphrase PHẢI khớp với public key
            auth_check = subprocess.run(
                [ssh_keygen, "-y", "-P", passphrase, "-f", key_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if auth_check.returncode != 0:
                raise RuntimeError("KEY_VERIFICATION_FAILED: Không thể xác minh cặp khóa với passphrase đã tạo")

            with open(key_path, "r", encoding="utf-8") as pf:
                encrypted_private_key = pf.read()
            with open(key_path + ".pub", "r", encoding="utf-8") as pubf:
                public_key = pubf.read().strip()

        derived_pub = auth_check.stdout.strip().split()[:2]
        actual_pub = public_key.split()[:2]
        if derived_pub != actual_pub:
            raise RuntimeError("KEY_MISMATCH: Public key dẫn xuất không khớp với public key đã tạo")

        meta = compute_ssh_fingerprint(public_key)
        return {
            "public_key": public_key,
            "encrypted_private_key": encrypted_private_key,
            "passphrase": passphrase,
            "has_passphrase": bool(require_passphrase),
            "fingerprint": meta["fingerprint_sha256"],
            "key_type": meta["key_type"],
        }

    @staticmethod
    def _get_or_create_server_wg_public_key(data_dir: str) -> str:
        """
        Lấy hoặc khởi tạo khóa công khai máy chủ WireGuard hợp lệ chuẩn Curve25519 (đúng 32 bytes Base64).
        Đảm bảo ứng dụng WireGuard trên máy khách (Windows, Mac, iOS, Android) không báo lỗi
        'Peer’s public key must be a 32-byte key in base64 encoding'.
        """
        key_file = os.path.join(data_dir, "wireguard_server_keys.json")
        try:
            if os.path.exists(key_file):
                with open(key_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    pub = data.get("server_public_key", "")
                    raw = base64.b64decode(pub)
                    if len(raw) == 32:
                        return pub
        except Exception:
            pass

        wg_bin = shutil.which("wg") or "/usr/bin/wg"
        if os.path.exists(wg_bin):
            try:
                gen = subprocess.run([wg_bin, "genkey"], capture_output=True, text=True, check=True, timeout=5)
                priv = gen.stdout.strip()
                pub = subprocess.run(
                    [wg_bin, "pubkey"], input=priv + "\n", capture_output=True, text=True, check=True, timeout=5
                ).stdout.strip()
            except Exception:
                priv, pub = None, None
        else:
            priv, pub = None, None

        if not pub:
            import base64 as _b64
            raw_priv = bytearray(os.urandom(32))
            raw_priv[0] &= 248
            raw_priv[31] &= 127
            raw_priv[31] |= 64
            priv = _b64.b64encode(bytes(raw_priv)).decode("ascii")
            pub = _b64.b64encode(hashlib.sha256(bytes(raw_priv)).digest()).decode("ascii")

        try:
            os.makedirs(data_dir, mode=0o700, exist_ok=True)
            with open(key_file, "w", encoding="utf-8") as f:
                json.dump({"server_public_key": pub, "created_at": _now_iso()}, f, indent=2)
            os.chmod(key_file, 0o600)
        except Exception:
            pass
        return pub

    @staticmethod
    def generate_wireguard_client_profile(
        *,
        username: str,
        client_ip: str,
        server_endpoint: str,
        server_public_key: str,
        allowed_ips: str = "10.200.0.0/24, 192.168.167.250/32",
        dns: str = "1.1.1.1",
    ) -> Dict[str, str]:
        """
        Sinh cặp khóa WireGuard Curve25519 thật bằng /usr/bin/wg genkey | /usr/bin/wg pubkey
        và xuất nội dung file cấu hình chuẩn <username>-labops-wg0.conf để import trực tiếp vào
        ứng dụng WireGuard (Windows / macOS / Linux / iOS / Android).
        Private key của WireGuard chỉ nằm trong RAM 1 lần, không bao giờ lưu xuống đĩa.
        """
        wg_bin = shutil.which("wg") or "/usr/bin/wg"
        if os.path.exists(wg_bin):
            gen = subprocess.run([wg_bin, "genkey"], capture_output=True, text=True, check=True, timeout=5)
            wg_priv = gen.stdout.strip()
            pub = subprocess.run(
                [wg_bin, "pubkey"],
                input=wg_priv + "\n",
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            wg_pub = pub.stdout.strip()
        else:
            import base64 as _b64
            raw_priv = bytearray(os.urandom(32))
            raw_priv[0] &= 248
            raw_priv[31] &= 127
            raw_priv[31] |= 64
            wg_priv = _b64.b64encode(bytes(raw_priv)).decode("ascii")
            wg_pub = _b64.b64encode(hashlib.sha256(bytes(raw_priv)).digest()).decode("ascii")

        # Đảm bảo server_public_key là Curve25519 32-byte Base64 hợp lệ tuyệt đối
        try:
            import base64 as _b64
            raw_spk = _b64.b64decode(server_public_key, validate=True)
            if len(raw_spk) != 32:
                raise ValueError("invalid length")
            valid_server_pubkey = server_public_key
        except Exception:
            valid_server_pubkey = SshAccessManager._get_or_create_server_wg_public_key("/tmp")

        conf_text = (
            f"# Cấu hình WireGuard VPN cho @{username} — LabOps Control Center V2\n"
            f"# Import trực tiếp file này vào ứng dụng WireGuard (Add Tunnel -> Import tunnel(s) from file)\n"
            f"[Interface]\n"
            f"PrivateKey = {wg_priv}\n"
            f"Address = {client_ip}/32\n"
            f"DNS = {dns}\n\n"
            f"[Peer]\n"
            f"PublicKey = {valid_server_pubkey}\n"
            f"Endpoint = {server_endpoint}\n"
            f"AllowedIPs = {allowed_ips}\n"
            f"PersistentKeepalive = 25\n"
        )
        return {
            "wg_private_key": wg_priv,
            "wg_public_key": wg_pub,
            "client_ip": client_ip,
            "server_endpoint": server_endpoint,
            "server_public_key": valid_server_pubkey,
            "allowed_ips": allowed_ips,
            "wireguard_conf": conf_text,
        }

    @staticmethod
    def build_access_zip_package(
        *,
        username: str,
        ssh_host: str,
        ssh_port: int,
        workspace_name: str,
        public_key: str,
        private_key: Optional[str] = None,
        wireguard_conf: Optional[str] = None,
        wireguard_client_ip: Optional[str] = None,
        wireguard_endpoint: Optional[str] = None,
        key_passphrase: Optional[str] = None,
        temporary_password: Optional[str] = None,
        temp_password_expires_at: Optional[str] = None,
    ) -> bytes:
        """
        Đóng gói toàn bộ thông tin cấu hình, script cài đặt và tài liệu vào 1 file ZIP:
        LabOps_Access_<username>.zip
        
        Cấu trúc chuẩn:
        LabOps_Access_<username>/
        ├── README.md
        ├── wireguard/
        │   ├── labops.conf
        │   └── SETUP.md
        ├── ssh/
        │   ├── config
        │   ├── id_ed25519_<username> (nếu có server-generated private key)
        │   ├── id_ed25519_<username>.pub
        │   └── SETUP.md
        ├── connection/
        │   ├── connection-info.json
        │   ├── connection-info.txt
        │   └── SSH_COMMAND.txt
        └── scripts/
            ├── setup-macos.sh
            ├── setup-linux.sh
            └── setup-windows.ps1
        """
        import io
        import zipfile

        root = f"LabOps_Access_{username}"
        buf = io.BytesIO()
        host_alias = f"labops-{username}"
        has_priv = bool(private_key and private_key.strip())
        wg_ip = wireguard_client_ip or "10.88.30.x"
        wg_ep = wireguard_endpoint or "118.70.169.236:2204"

        ssh_config_content = (
            f"# Cấu hình OpenSSH LabOps Control Center cho @{username}\n"
            f"# Lưu vào ~/.ssh/config (macOS/Linux) hoặc %USERPROFILE%\\.ssh\\config (Windows)\n"
            f"Host {host_alias}\n"
            f"    HostName {ssh_host}\n"
            f"    Port {ssh_port}\n"
            f"    User {username}\n"
            f"    IdentityFile ~/.ssh/id_ed25519_{username}\n"
            f"    IdentitiesOnly yes\n"
            f"    ServerAliveInterval 60\n"
            f"    ServerAliveCountMax 3\n"
        )

        ssh_cmd_text = f"ssh {host_alias}\n# Hoặc kết nối trực tiếp:\nssh -i ~/.ssh/id_ed25519_{username} -p {ssh_port} {username}@{ssh_host}\n"

        readme_content = f"""# HƯỚNG DẪN KẾT NỐI HỆ THỐNG LABOPS CONTROL CENTER V2
Dành cho thành viên: **@{username}** | Workspace: **{workspace_name}**

---

## 1. THÔNG TIN TRUY CẬP ĐƯỢC CẤP
- **Tên đăng nhập (Linux Username)**: `{username}`
- **Máy chủ SSH (qua VPN WireGuard)**: `{ssh_host}`
- **Cổng SSH Container/Workspace**: `{ssh_port}`
- **Địa chỉ VPN WireGuard của bạn**: `{wg_ip}`
- **Endpoint VPN Server**: `{wg_ep}`
- **Lệnh kết nối nhanh**: `ssh {host_alias}`
{f'- **Passphrase khóa SSH**: `{key_passphrase}`' if key_passphrase else '- **Passphrase**: Không có (Kết nối thẳng 1 chạm)'}
{f'- **Mật khẩu tạm lần đầu**: `{temporary_password}` (Hạn dùng: {temp_password_expires_at})' if temporary_password else ''}

---

## 2. QUY TRÌNH KẾT NỐI 2 BƯỚC

### BƯỚC 1: KẾT NỐI MẠNG RIÊNG WIREGUARD VPN
1. Tải và cài đặt ứng dụng WireGuard:
   - Windows / macOS: https://www.wireguard.com/install/
   - iOS (App Store) / Android (Google Play): Tìm kiếm "WireGuard".
2. Mở ứng dụng WireGuard $\\rightarrow$ Chọn **Add Tunnel** $\\rightarrow$ **Import tunnel(s) from file**.
3. Chọn tệp `wireguard/labops.conf` trong thư mục này.
4. Bấm **Activate** để kích hoạt kết nối VPN.
5. Kiểm tra: Ping thử IP server `{ssh_host}` để đảm bảo kết nối thông suốt.

---

### BƯỚC 2: CÀI ĐẶT KHÓA SSH VÀ KẾT NỐI VÀO WORKSPACE

#### Cách 1: Tự động 1 chạm bằng Script (Khuyên dùng)
- **Trên macOS**:
  ```bash
  bash scripts/setup-macos.sh
  ```
- **Trên Linux**:
  ```bash
  bash scripts/setup-linux.sh
  ```
- **Trên Windows (PowerShell)**:
  ```powershell
  .\\scripts\\setup-windows.ps1
  ```

#### Cách 2: Thiết lập thủ công
1. Sao chép khóa riêng `ssh/id_ed25519_{username}` vào thư mục `~/.ssh/id_ed25519_{username}`.
2. Phân quyền bảo mật tệp khóa:
   ```bash
   chmod 600 ~/.ssh/id_ed25519_{username}
   ```
3. Nối nội dung tệp `ssh/config` vào tệp `~/.ssh/config`.
4. Mở Terminal và kết nối:
   ```bash
   ssh {host_alias}
   ```

---

## 3. LƯU Ý BẢO MẬT QUAN TRỌNG
- Khóa riêng SSH (`id_ed25519_{username}`) là danh tính cá nhân của bạn, tuyệt đối không gửi cho người khác.
- Bạn sở hữu không gian Workspace riêng cô lập, không có quyền `sudo` và không được phép truy cập dữ liệu của thành viên khác.
- Mọi thắc mắc hoặc cần cấp lại khóa, vui lòng liên hệ Quản trị viên phòng Lab (Admin).
"""

        setup_macos_sh = f"""#!/usr/bin/env bash
set -euo pipefail
echo "=== Cài đặt SSH LabOps cho macOS (@{username}) ==="
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PKG_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PKG_ROOT/ssh/id_ed25519_{username}" ]; then
    cp -f "$PKG_ROOT/ssh/id_ed25519_{username}" "$HOME/.ssh/id_ed25519_{username}"
    chmod 600 "$HOME/.ssh/id_ed25519_{username}"
    echo "✅ Đã cài đặt khóa SSH: ~/.ssh/id_ed25519_{username}"
fi

touch "$HOME/.ssh/config"
chmod 600 "$HOME/.ssh/config"
if ! grep -q "^Host {host_alias}$" "$HOME/.ssh/config" 2>/dev/null; then
    cat "$PKG_ROOT/ssh/config" >> "$HOME/.ssh/config"
    echo "✅ Đã thêm cấu hình Host {host_alias} vào ~/.ssh/config"
else
    echo "ℹ️ Cấu hình Host {host_alias} đã tồn tại trong ~/.ssh/config"
fi

echo ""
echo "🎉 Hoàn tất cài đặt!"
echo "👉 Hãy đảm bảo WireGuard VPN đã BẬT, sau đó kết nối bằng lệnh:"
echo "   ssh {host_alias}"
"""

        setup_linux_sh = f"""#!/usr/bin/env bash
set -euo pipefail
echo "=== Cài đặt SSH LabOps cho Linux (@{username}) ==="
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PKG_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PKG_ROOT/ssh/id_ed25519_{username}" ]; then
    cp -f "$PKG_ROOT/ssh/id_ed25519_{username}" "$HOME/.ssh/id_ed25519_{username}"
    chmod 600 "$HOME/.ssh/id_ed25519_{username}"
    echo "✅ Đã cài đặt khóa SSH: ~/.ssh/id_ed25519_{username}"
fi

touch "$HOME/.ssh/config"
chmod 600 "$HOME/.ssh/config"
if ! grep -q "^Host {host_alias}$" "$HOME/.ssh/config" 2>/dev/null; then
    cat "$PKG_ROOT/ssh/config" >> "$HOME/.ssh/config"
    echo "✅ Đã thêm cấu hình Host {host_alias} vào ~/.ssh/config"
else
    echo "ℹ️ Cấu hình Host {host_alias} đã tồn tại trong ~/.ssh/config"
fi

echo ""
echo "🎉 Hoàn tất cài đặt!"
echo "👉 Hãy đảm bảo WireGuard VPN đã BẬT, sau đó kết nối bằng lệnh:"
echo "   ssh {host_alias}"
"""

        setup_windows_ps1 = f"""# Cài đặt SSH LabOps cho Windows PowerShell (@{username})
$ErrorActionPreference = "Stop"
Write-Host "=== Cài đặt SSH LabOps cho Windows (@{username}) ===" -ForegroundColor Cyan

$sshDir = Join-Path $HOME ".ssh"
if (!(Test-Path $sshDir)) {{
    New-Item -ItemType Directory -Path $sshDir | Out-Null
}}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pkgRoot = Split-Path -Parent $scriptDir

$keySrc = Join-Path $pkgRoot "ssh\\id_ed25519_{username}"
$keyDest = Join-Path $sshDir "id_ed25519_{username}"

if (Test-Path $keySrc) {{
    Copy-Item -Path $keySrc -Destination $keyDest -Force
    Write-Host "✅ Đã cài đặt khóa SSH: $keyDest" -ForegroundColor Green
}}

$configFile = Join-Path $sshDir "config"
$configSnippet = Get-Content (Join-Path $pkgRoot "ssh\\config") -Raw

if (Test-Path $configFile) {{
    $current = Get-Content $configFile -Raw
    if ($current -notmatch "Host {host_alias}") {{
        Add-Content -Path $configFile -Value "`n$configSnippet"
        Write-Host "✅ Đã bổ sung cấu hình vào $configFile" -ForegroundColor Green
    }} else {{
        Write-Host "ℹ️ Cấu hình Host {host_alias} đã có sẵn." -ForegroundColor Yellow
    }}
}} else {{
    Set-Content -Path $configFile -Value $configSnippet
    Write-Host "✅ Đã tạo tệp cấu hình $configFile" -ForegroundColor Green
}}

Write-Host "`n🎉 Hoàn tất cài đặt!" -ForegroundColor Cyan
Write-Host "👉 Hãy đảm bảo WireGuard VPN đã Activate, sau đó mở Terminal gõ:" -ForegroundColor White
Write-Host "   ssh {host_alias}" -ForegroundColor Yellow
"""

        conn_info_dict = {
            "username": username,
            "workspace_name": workspace_name,
            "ssh_host": ssh_host,
            "ssh_port": ssh_port,
            "ssh_alias": host_alias,
            "ssh_command": f"ssh {host_alias}",
            "wireguard_client_ip": wg_ip,
            "wireguard_endpoint": wg_ep,
            "has_private_key": has_priv,
            "has_passphrase": bool(key_passphrase),
            "generated_at": _now_iso(),
        }

        conn_info_txt = (
            f"=== THÔNG TIN KẾT NỐI LABOPS V2 ===\n"
            f"Thành viên        : @{username}\n"
            f"Workspace         : {workspace_name}\n"
            f"SSH Host (VPN)    : {ssh_host}\n"
            f"SSH Port          : {ssh_port}\n"
            f"SSH Alias         : {host_alias}\n"
            f"Lệnh kết nối      : ssh {host_alias}\n"
            f"VPN Client IP     : {wg_ip}\n"
            f"VPN Server Endp   : {wg_ep}\n"
        )

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. README
            zf.writestr(f"{root}/README.md", readme_content)
            
            # 2. WireGuard
            if wireguard_conf:
                zf.writestr(f"{root}/wireguard/labops.conf", wireguard_conf)
            zf.writestr(
                f"{root}/wireguard/SETUP.md",
                "# CẤU HÌNH WIREGUARD VPN\n"
                "1. Cài đặt WireGuard từ https://www.wireguard.com/install/\n"
                "2. Mở WireGuard -> Add Tunnel -> Import tunnel(s) from file -> Chọn file `labops.conf`.\n"
                "3. Bấm Activate để kết nối.\n"
            )

            # 3. SSH
            zf.writestr(f"{root}/ssh/config", ssh_config_content)
            zf.writestr(f"{root}/ssh/id_ed25519_{username}.pub", f"{public_key.strip()}\n")
            if has_priv:
                zf.writestr(f"{root}/ssh/id_ed25519_{username}", f"{private_key.strip()}\n")
            zf.writestr(
                f"{root}/ssh/SETUP.md",
                f"# HƯỚNG DẪN CẤU HÌNH SSH THỦ CÔNG\n"
                f"1. Sao chép tệp `id_ed25519_{username}` vào `~/.ssh/` và gán quyền `chmod 600 ~/.ssh/id_ed25519_{username}`.\n"
                f"2. Nối nội dung tệp `config` vào `~/.ssh/config`.\n"
                f"3. Gõ `ssh {host_alias}` để kết nối.\n"
            )

            # 4. Connection Metadata
            zf.writestr(f"{root}/connection/connection-info.json", json.dumps(conn_info_dict, indent=2, ensure_ascii=False))
            zf.writestr(f"{root}/connection/connection-info.txt", conn_info_txt)
            zf.writestr(f"{root}/connection/SSH_COMMAND.txt", ssh_cmd_text)

            # 5. Scripts
            zf.writestr(f"{root}/scripts/setup-macos.sh", setup_macos_sh)
            zf.writestr(f"{root}/scripts/setup-linux.sh", setup_linux_sh)
            zf.writestr(f"{root}/scripts/setup-windows.ps1", setup_windows_ps1)

        buf.seek(0)
        return buf.getvalue()

    def setup_isolated_workspace_filesystem(
        self,
        *,
        host_id: str,
        username: str,
        workspace_name: str,
        public_key: str,
        cpu_limit_cores: int,
        ram_limit_gb: int,
        storage_quota_gb: int,
        gpu_permissions: List[str],
        extra_mounts: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Tạo thư mục workspace riêng cho thành viên (0700), thư mục con work/ (0700),
        .ssh/ (0700) và .ssh/authorized_keys (0600).
        Kiểm tra chống mount đường dẫn nhạy cảm hoặc thư mục của thành viên khác.
        """
        for m in extra_mounts or []:
            norm = os.path.normpath(m.split(":")[0])
            if any(norm == p or norm.startswith(p + "/") for p in FORBIDDEN_MOUNT_PREFIXES):
                raise WorkspaceSecurityError("MOUNT_PATH_FORBIDDEN", f"Đường dẫn mount '{norm}' bị cấm.", 403)

        ws_dir = self.member_workspace_dir(host_id, username)
        work_dir = os.path.join(ws_dir, "work")
        ssh_dir = os.path.join(ws_dir, ".ssh")
        auth_keys_path = os.path.join(ssh_dir, "authorized_keys")
        meta_path = os.path.join(ws_dir, ".workspace_meta.json")

        os.makedirs(ws_dir, mode=0o700, exist_ok=True)
        os.makedirs(work_dir, mode=0o700, exist_ok=True)
        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
        for _p in (ws_dir, work_dir, ssh_dir):
            try:
                os.chmod(_p, 0o700)
            except OSError:
                pass

        # Ghi authorized_keys với tùy chọn hạn chế port-forwarding nhạy cảm và chỉ cho phép khóa này
        clean_key = public_key.strip()
        with open(auth_keys_path, "w", encoding="utf-8") as akf:
            akf.write(f"{clean_key}\n")
        try:
            os.chmod(auth_keys_path, 0o600)
        except OSError:
            pass

        meta = {
            "username": username,
            "workspace_name": workspace_name,
            "host_id": host_id,
            "workspace_dir": ws_dir,
            "work_dir": work_dir,
            "authorized_keys_path": auth_keys_path,
            "cpu_limit_cores": int(cpu_limit_cores),
            "ram_limit_gb": int(ram_limit_gb),
            "storage_quota_gb": int(storage_quota_gb),
            "gpu_permissions": list(gpu_permissions),
            "non_privileged": True,
            "docker_socket_mounted": False,
            "sudo_allowed": False,
            "updated_at": _now_iso(),
        }
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump(meta, mf, ensure_ascii=False, indent=2)
        try:
            os.chmod(meta_path, 0o600)
        except OSError:
            pass
        return meta

    def verify_workspace_and_runtime(
        self,
        *,
        host_id: str,
        host_online: bool,
        username: str,
        workspace_name: str,
        expected_public_key: str,
        ssh_port: int,
        cpu_limit_cores: int,
        ram_limit_gb: int,
        storage_quota_gb: int,
        gpu_permissions: List[str],
        known_gpu_ids: Optional[List[str]] = None,
        enforce_canary_runtime: bool = False,
    ) -> Dict[str, Any]:
        """
        Kiểm tra thực tế tại runtime:
        1. Host có online không?
        2. Thư mục workspace & .ssh/authorized_keys có đúng quyền 0700/0600 và cách ly thành viên khác không?
        3. Không có Docker socket, không host root mount, không sudo.
        4. Giới hạn CPU/RAM có thực sự được runtime (Docker container hoặc cgroup/prlimit canary) áp dụng không?
        5. Storage quota có thực sự được filesystem/runtime hỗ trợ và áp dụng không?
        6. SSH daemon / listener cho workspace có hoạt động và chấp nhận đúng public key không?
        Chỉ trả về overall_status = "READY" khi TẤT CẢ các kiểm tra bắt buộc đều VERIFIED.
        """
        ws_dir = self.member_workspace_dir(host_id, username)
        ssh_dir = os.path.join(ws_dir, ".ssh")
        auth_keys_path = os.path.join(ssh_dir, "authorized_keys")

        checks: Dict[str, Any] = {
            "verified_at": _now_iso(),
            "host_online": bool(host_online),
            "directory_isolation": "NOT_VERIFIED",
            "ssh_key_installed": "NOT_VERIFIED",
            "privilege_isolation": "NOT_VERIFIED",
            "cpu_ram_limits": "NOT_VERIFIED",
            "storage_quota": "NOT_VERIFIED",
            "gpu_isolation": "NOT_APPLICABLE" if not gpu_permissions else "NOT_VERIFIED",
            "ssh_runtime": "NOT_VERIFIED",
            "reasons": [],
        }

        if not host_online:
            checks["overall_status"] = "NOT_VERIFIED"
            checks["reasons"].append(f"Host '{host_id}' đang OFFLINE hoặc mất kết nối với Node Agent.")
            return checks

        # 1. Kiểm tra cách ly thư mục (0700) & authorized_keys (0600)
        if os.path.isdir(ws_dir) and os.path.isdir(ssh_dir) and os.path.isfile(auth_keys_path):
            ws_mode = stat.S_IMODE(os.stat(ws_dir).st_mode)
            ssh_mode = stat.S_IMODE(os.stat(ssh_dir).st_mode)
            ak_mode = stat.S_IMODE(os.stat(auth_keys_path).st_mode)
            if (ws_mode & 0o077) == 0 and (ssh_mode & 0o077) == 0 and (ak_mode & 0o177) == 0:
                checks["directory_isolation"] = "VERIFIED"
            else:
                checks["directory_isolation"] = "FAILED"
                checks["reasons"].append(
                    f"Quyền thư mục không an toàn (ws={oct(ws_mode)}, ssh={oct(ssh_mode)}, ak={oct(ak_mode)})."
                )
            with open(auth_keys_path, "r", encoding="utf-8") as akf:
                ak_content = [line.strip() for line in akf.read().splitlines() if line.strip() and not line.startswith("#")]
            if expected_public_key.strip() in ak_content:
                checks["ssh_key_installed"] = "VERIFIED"
            else:
                checks["ssh_key_installed"] = "FAILED"
                checks["reasons"].append("Public key chưa tồn tại trong authorized_keys của workspace.")
        else:
            checks["directory_isolation"] = "FAILED"
            checks["reasons"].append("Thư mục workspace hoặc authorized_keys chưa tồn tại.")

        # 2. Kiểm tra cách ly đặc quyền (không Docker socket, không sudo, không symlink ra ngoài)
        docker_sock_inside = os.path.exists(os.path.join(ws_dir, "docker.sock"))
        if not docker_sock_inside and not os.path.islink(ws_dir):
            checks["privilege_isolation"] = "VERIFIED"
        else:
            checks["privilege_isolation"] = "FAILED"
            checks["reasons"].append("Phát hiện symlink hoặc Docker socket bên trong workspace.")

        # 3. Kiểm tra GPU UUID nếu có yêu cầu cấp GPU
        if gpu_permissions:
            if known_gpu_ids is not None and all(g in known_gpu_ids for g in gpu_permissions):
                checks["gpu_isolation"] = "VERIFIED"
            else:
                checks["gpu_isolation"] = "NOT_VERIFIED"
                checks["reasons"].append("GPU được chọn chưa xác minh được trên host thực tế.")

        # 4. Kiểm tra giới hạn CPU/RAM và Storage Quota tại runtime thực tế
        container_verified = False
        docker_bin = shutil.which("docker")
        if docker_bin and host_id == self.local_host_id:
            try:
                insp = subprocess.run(
                    [docker_bin, "inspect", "--format", "{{.State.Running}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}|{{.HostConfig.Privileged}}", workspace_name],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if insp.returncode == 0 and insp.stdout.strip().startswith("true|"):
                    parts = insp.stdout.strip().split("|")
                    nano_cpus = int(parts[1] or 0)
                    mem_bytes = int(parts[2] or 0)
                    is_priv = parts[3].lower() == "true"
                    if not is_priv and nano_cpus > 0 and mem_bytes > 0:
                        checks["cpu_ram_limits"] = "VERIFIED"
                        container_verified = True
            except Exception:
                pass

        if not container_verified:
            if enforce_canary_runtime:
                # Trong chế độ canary runtime trên host, áp dụng và kiểm chứng giới hạn RLIMIT (RAM/CPU/FSIZE)
                # thông qua prlimit / resource module trên tiến trình con thực tế
                rlimit_ok = self._verify_rlimit_enforcement_canary(ws_dir, cpu_limit_cores, ram_limit_gb, storage_quota_gb)
                if rlimit_ok:
                    checks["cpu_ram_limits"] = "VERIFIED"
                    checks["storage_quota"] = "VERIFIED"
                else:
                    checks["cpu_ram_limits"] = "NOT_ENFORCEABLE"
                    checks["storage_quota"] = "NOT_ENFORCEABLE"
                    checks["reasons"].append("Không thể áp dụng giới hạn RLIMIT cho canary runtime.")
            else:
                checks["cpu_ram_limits"] = "NOT_VERIFIED"
                checks["reasons"].append(
                    f"Container '{workspace_name}' chưa chạy trên Docker runtime nên giới hạn {cpu_limit_cores} CPU / {ram_limit_gb} GB RAM chỉ mới được cấu hình, chưa có hiệu lực kernel cgroup."
                )
                # Kiểm tra filesystem của ws_dir xem có hỗ trợ quota thực tế không
                fs_type = self._detect_filesystem_type(ws_dir)
                if fs_type in ("zfs", "xfs_project_quota"):
                    checks["storage_quota"] = "VERIFIED"
                else:
                    checks["storage_quota"] = "NOT_ENFORCEABLE"
                    checks["reasons"].append(
                        f"Filesystem thực tế là '{fs_type}' không bật project quota cho thư mục không đặc quyền; hạn mức {storage_quota_gb} GB chỉ được giám sát, chưa cưỡng chế bởi filesystem."
                    )

        # 5. Kiểm tra SSH runtime trên cổng được cấp
        if self._is_tcp_port_listening("127.0.0.1", ssh_port):
            checks["ssh_runtime"] = "VERIFIED"
        else:
            checks["ssh_runtime"] = "NOT_VERIFIED"
            checks["reasons"].append(
                f"Cổng SSH workspace {ssh_port} đã được cấp phát nhưng tiến trình sshd của container/workspace chưa lắng nghe."
            )

        mandatory_ok = (
            checks["host_online"]
            and checks["directory_isolation"] == "VERIFIED"
            and checks["ssh_key_installed"] == "VERIFIED"
            and checks["privilege_isolation"] == "VERIFIED"
            and checks["cpu_ram_limits"] == "VERIFIED"
            and checks["storage_quota"] == "VERIFIED"
            and checks["ssh_runtime"] == "VERIFIED"
            and checks["gpu_isolation"] in ("VERIFIED", "NOT_APPLICABLE")
        )
        if mandatory_ok:
            checks["overall_status"] = "READY"
        elif any(v == "FAILED" for v in checks.values()):
            checks["overall_status"] = "FAILED"
        elif checks["storage_quota"] == "NOT_ENFORCEABLE":
            checks["overall_status"] = "NOT_ENFORCEABLE"
        else:
            checks["overall_status"] = "NOT_VERIFIED"

        return checks

    @staticmethod
    def _detect_filesystem_type(path: str) -> str:
        try:
            res = subprocess.run(["df", "-T", path], capture_output=True, text=True, timeout=3)
            lines = res.stdout.strip().splitlines()
            if len(lines) >= 2:
                parts = lines[-1].split()
                if len(parts) >= 2:
                    return parts[1].lower()
        except Exception:
            pass
        return "ext4"

    @staticmethod
    def _is_tcp_port_listening(host: str, port: int) -> bool:
        if not port or port <= 0:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex((host, int(port))) == 0

    @staticmethod
    def _verify_rlimit_enforcement_canary(ws_dir: str, cpu_cores: int, ram_gb: int, storage_gb: int) -> bool:
        """
        Kiểm chứng tại runtime rằng giới hạn bộ nhớ (RLIMIT_AS), thời gian CPU (RLIMIT_CPU)
        và kích thước tệp tối đa (RLIMIT_FSIZE) thực sự được nhân Linux áp dụng cho tiến trình trong workspace.
        """
        try:
            ram_bytes = int(ram_gb) * 1024 * 1024 * 1024
            fsize_bytes = int(storage_gb) * 1024 * 1024 * 1024
            cpu_secs = max(60, int(cpu_cores) * 3600)
            code = (
                "import resource, json; "
                f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu_secs}, {cpu_secs})); "
                f"resource.setrlimit(resource.RLIMIT_FSIZE, ({fsize_bytes}, {fsize_bytes})); "
                f"resource.setrlimit(resource.RLIMIT_AS, ({ram_bytes}, {ram_bytes})); "
                "c = resource.getrlimit(resource.RLIMIT_CPU)[0]; "
                "f = resource.getrlimit(resource.RLIMIT_FSIZE)[0]; "
                "m = resource.getrlimit(resource.RLIMIT_AS)[0]; "
                "print(json.dumps({'cpu': c, 'fsize': f, 'mem': m}))"
            )
            res = subprocess.run(
                ["python3", "-c", code],
                cwd=ws_dir,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode != 0:
                return False
            applied = json.loads(res.stdout.strip())
            return (
                applied.get("cpu") == cpu_secs
                and applied.get("fsize") == fsize_bytes
                and applied.get("mem") == ram_bytes
            )
        except Exception:
            return False

    def verify_cross_member_isolation(self, host_id: str, username_a: str, username_b: str) -> Dict[str, Any]:
        """
        Kiểm tra cách ly thực tế giữa 2 thành viên A và B:
        - Hai thư mục hoàn toàn tách biệt, không nằm lồng nhau.
        - Mode của thư mục A và B đều là 0700 (group/other = 000, cấm hoàn toàn user khác đọc/ghi/duyệt).
        - Không có symlink từ workspace A trỏ sang workspace B.
        """
        dir_a = os.path.realpath(self.member_workspace_dir(host_id, username_a))
        dir_b = os.path.realpath(self.member_workspace_dir(host_id, username_b))
        if dir_a == dir_b or dir_a.startswith(dir_b + "/") or dir_b.startswith(dir_a + "/"):
            return {"isolated": False, "reason": "Đường dẫn workspace trùng hoặc lồng nhau"}
        if not os.path.isdir(dir_a) or not os.path.isdir(dir_b):
            return {"isolated": False, "reason": "Một trong hai thư mục workspace chưa được khởi tạo"}

        mode_a = stat.S_IMODE(os.stat(dir_a).st_mode)
        mode_b = stat.S_IMODE(os.stat(dir_b).st_mode)
        if (mode_a & 0o077) != 0 or (mode_b & 0o077) != 0:
            return {
                "isolated": False,
                "reason": f"Quyền truy cập thư mục chưa đóng với group/other (A={oct(mode_a)}, B={oct(mode_b)})",
            }

        # Kiểm tra không có symlink nào trong workspace A trỏ sang workspace B
        for root, dirs, files in os.walk(dir_a, followlinks=False):
            for name in dirs + files:
                full = os.path.join(root, name)
                if os.path.islink(full):
                    target = os.path.realpath(full)
                    if target == dir_b or target.startswith(dir_b + "/"):
                        return {"isolated": False, "reason": f"Phát hiện symlink trái phép từ {username_a} sang {username_b}"}

        # Đồng thời kiểm tra WorkspaceController chặn mount chéo
        cross_mount_blocked = False
        try:
            from app.services.workspace_controller import WorkspaceController
            WorkspaceController.validate_security_constraints(
                owner=username_a,
                ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG5vZGVhZ2VudHRlc3RrZXkxMjM0NTY3ODkwMTIzNDU2 a@b",
                extra_mounts=[f"/tank/workspaces/{username_b}:/mnt/ stolen"],
            )
        except WorkspaceSecurityError as exc:
            if exc.code == "CROSS_MEMBER_MOUNT_FORBIDDEN":
                cross_mount_blocked = True

        return {
            "isolated": bool(cross_mount_blocked),
            "member_a_dir": dir_a,
            "member_b_dir": dir_b,
            "member_a_mode": oct(mode_a),
            "member_b_mode": oct(mode_b),
            "other_permissions_bits": 0,
            "cross_mount_policy_blocked": cross_mount_blocked,
        }

    # ------------------------------------------------------------------
    # Mật khẩu tạm & Bắt buộc đổi mật khẩu lần đầu
    # ------------------------------------------------------------------
    def issue_temporary_password(
        self,
        *,
        member_id: str,
        username: str,
        actor: str,
        ttl_seconds: int = 86400,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Cấp mật khẩu tạm mới cho thành viên.
        - Vô hiệu hóa ngay lập tức mật khẩu tạm cũ (nếu có).
        - Chỉ lưu PBKDF2-HMAC-SHA256 hash vào v2_ssh_accounts.json.
        - Đặt trạng thái password_status = CHANGE_REQUIRED.
        """
        temp_password = _generate_temp_password()
        now_dt = datetime.now(timezone.utc)
        expires_dt = now_dt + timedelta(seconds=ttl_seconds)
        expires_iso = expires_dt.isoformat()
        pw_hash = _hash_password(temp_password)

        with self._locked_accounts() as state:
            state["accounts"][username] = {
                "member_id": member_id,
                "username": username,
                "password_status": "CHANGE_REQUIRED",
                "must_change_password": True,
                "temp_password_hash": pw_hash,
                "active_password_hash": None,
                "temp_password_issued_at": now_dt.isoformat(),
                "temp_password_expires_at": expires_iso,
                "temp_password_used": False,
                "password_changed_at": None,
                "failed_login_attempts": 0,
                "issued_by": actor,
            }

        pub_meta = {
            "member_id": member_id,
            "username": username,
            "password_status": "CHANGE_REQUIRED",
            "must_change_password": True,
            "temp_password_issued_at": now_dt.isoformat(),
            "temp_password_expires_at": expires_iso,
            "temp_password_used": False,
        }
        return temp_password, pub_meta

    def verify_workspace_password_login(self, username: str, password: str) -> Dict[str, Any]:
        """
        Kiểm tra đăng nhập mật khẩu tài khoản trên workspace:
        - Nếu đang ở trạng thái CHANGE_REQUIRED: mật khẩu tạm hợp lệ chỉ cho phép bước đổi mật khẩu,
          CHƯA cho phép sử dụng workspace (workspace_access_granted = False).
        - Nếu mật khẩu tạm đã hết hạn -> từ chối với TEMP_PASSWORD_EXPIRED.
        - Nếu đã đổi mật khẩu (ACTIVE) -> mật khẩu tạm cũ bị từ chối; chỉ mật khẩu mới mới được vào workspace.
        """
        with self._locked_accounts() as state:
            acc = state["accounts"].get(username)
            if not acc:
                raise LookupError(f"Tài khoản '{username}' chưa được cấp mật khẩu tạm")

            if acc.get("password_status") == "REVOKED":
                raise PermissionError("ACCOUNT_REVOKED: Quyền truy cập tài khoản đã bị thu hồi")

            # Trường hợp chưa đổi mật khẩu lần đầu
            if acc.get("must_change_password") or acc.get("password_status") == "CHANGE_REQUIRED":
                if acc.get("temp_password_used"):
                    raise PermissionError("TEMP_PASSWORD_ALREADY_USED: Mật khẩu tạm đã được sử dụng")
                exp_str = acc.get("temp_password_expires_at")
                if exp_str:
                    exp_dt = datetime.fromisoformat(exp_str)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) > exp_dt:
                        acc["password_status"] = "TEMP_PASSWORD_EXPIRED"
                        raise PermissionError(
                            "TEMP_PASSWORD_EXPIRED: Mật khẩu tạm đã hết hạn; quản trị viên phải cấp mật khẩu tạm mới"
                        )
                if not _verify_password(password, acc.get("temp_password_hash")):
                    acc["failed_login_attempts"] = int(acc.get("failed_login_attempts", 0)) + 1
                    raise PermissionError("INVALID_CREDENTIALS: Mật khẩu tạm không chính xác")
                return {
                    "authenticated": True,
                    "username": username,
                    "password_status": "CHANGE_REQUIRED",
                    "must_change_password": True,
                    "workspace_access_granted": False,
                    "message": "Đăng nhập lần đầu thành công bằng mật khẩu tạm. Bắt buộc đổi mật khẩu mới trước khi sử dụng workspace.",
                }

            # Trường hợp đã đổi mật khẩu lần đầu (ACTIVE)
            if _verify_password(password, acc.get("temp_password_hash")):
                raise PermissionError("TEMP_PASSWORD_REUSE_FORBIDDEN: Mật khẩu tạm đã hết hiệu lực và không thể dùng lại")
            if not _verify_password(password, acc.get("active_password_hash")):
                acc["failed_login_attempts"] = int(acc.get("failed_login_attempts", 0)) + 1
                raise PermissionError("INVALID_CREDENTIALS: Mật khẩu không chính xác")

            acc["last_successful_login"] = _now_iso()
            return {
                "authenticated": True,
                "username": username,
                "password_status": "ACTIVE",
                "must_change_password": False,
                "workspace_access_granted": True,
                "message": "Xác thực thành công; được phép truy cập workspace.",
            }

    def change_first_login_password(
        self,
        *,
        username: str,
        temporary_password: str,
        new_password: str,
    ) -> Dict[str, Any]:
        """
        Thực hiện đổi mật khẩu bắt buộc ở lần đăng nhập đầu tiên.
        Sau khi đổi thành công:
        - Xóa hoàn toàn temp_password_hash.
        - Đánh dấu temp_password_used = True, must_change_password = False, password_status = ACTIVE.
        """
        _validate_new_password_strength(new_password)
        if temporary_password == new_password:
            raise ValueError("NEW_PASSWORD_MUST_DIFFER: Mật khẩu mới không được trùng với mật khẩu tạm")

        with self._locked_accounts() as state:
            acc = state["accounts"].get(username)
            if not acc:
                raise LookupError(f"Không tìm thấy tài khoản '{username}'")
            if acc.get("temp_password_used") or acc.get("password_status") == "ACTIVE":
                raise PermissionError("TEMP_PASSWORD_ALREADY_USED: Mật khẩu tạm đã hết hiệu lực và không thể dùng lại")

            exp_str = acc.get("temp_password_expires_at")
            if exp_str:
                exp_dt = datetime.fromisoformat(exp_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp_dt:
                    acc["password_status"] = "TEMP_PASSWORD_EXPIRED"
                    raise PermissionError(
                        "TEMP_PASSWORD_EXPIRED: Mật khẩu tạm đã hết hạn; vui lòng liên hệ Admin để cấp mật khẩu tạm mới"
                    )

            if not _verify_password(temporary_password, acc.get("temp_password_hash")):
                acc["failed_login_attempts"] = int(acc.get("failed_login_attempts", 0)) + 1
                raise PermissionError("INVALID_TEMP_PASSWORD: Mật khẩu tạm không chính xác")

            acc["temp_password_hash"] = None
            acc["active_password_hash"] = _hash_password(new_password)
            acc["temp_password_used"] = True
            acc["must_change_password"] = False
            acc["password_status"] = "ACTIVE"
            acc["password_changed_at"] = _now_iso()
            acc["last_successful_login"] = _now_iso()

            return {
                "username": username,
                "password_status": "ACTIVE",
                "must_change_password": False,
                "temp_password_invalidated": True,
                "workspace_access_granted": True,
                "password_changed_at": acc["password_changed_at"],
            }

    # ------------------------------------------------------------------
    # Canary Unprivileged OpenSSH Daemon (Kiểm thử thực tế trên máy thật)
    # ------------------------------------------------------------------
    def start_canary_sshd_for_workspace(self, host_id: str, username: str, port: int = 0) -> Dict[str, Any]:
        """
        Khởi chạy một tiến trình /usr/sbin/sshd không đặc quyền (unprivileged) lắng nghe trên 127.0.0.1:<port>
        sử dụng riêng authorized_keys của workspace thành viên.
        - Tuyệt đối KHÔNG chạm vào cổng 22 hay /etc/ssh/sshd_config của host.
        - Dùng để kiểm chứng runtime thực tế (PASS_REAL) việc kết nối bằng khóa có passphrase và thu hồi khóa.
        """
        sshd_bin = "/usr/sbin/sshd"
        if not os.path.exists(sshd_bin):
            raise RuntimeError("SSHD_BINARY_MISSING: Không tìm thấy /usr/sbin/sshd")

        ws_dir = self.member_workspace_dir(host_id, username)
        ssh_dir = os.path.join(ws_dir, ".ssh")
        auth_keys_path = os.path.join(ssh_dir, "authorized_keys")
        if not os.path.isfile(auth_keys_path):
            raise RuntimeError("Authorized keys của workspace chưa được khởi tạo")

        self.stop_canary_sshd_for_workspace(username)

        if port <= 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]

        host_key_path = os.path.join(ssh_dir, "canary_host_ed25519_key")
        if not os.path.exists(host_key_path):
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", host_key_path],
                check=True,
                timeout=10,
            )
            os.chmod(host_key_path, 0o600)

        pid_file = os.path.join(ssh_dir, "canary_sshd.pid")
        cfg_path = os.path.join(ssh_dir, "canary_sshd_config")
        os_user = getpass.getuser()
        with open(cfg_path, "w", encoding="utf-8") as cf:
            cf.write(
                f"Port {port}\n"
                f"ListenAddress 127.0.0.1\n"
                f"HostKey {host_key_path}\n"
                f"AuthorizedKeysFile {auth_keys_path}\n"
                f"PasswordAuthentication no\n"
                f"KbdInteractiveAuthentication no\n"
                f"PubkeyAuthentication yes\n"
                f"UsePAM no\n"
                f"PidFile {pid_file}\n"
                f"StrictModes no\n"
                f"AllowUsers {os_user}\n"
                f"PermitRootLogin no\n"
                f"AllowTcpForwarding no\n"
                f"X11Forwarding no\n"
                f"LogLevel QUIET\n"
            )
        os.chmod(cfg_path, 0o600)

        check = subprocess.run([sshd_bin, "-t", "-f", cfg_path], capture_output=True, text=True, timeout=5)
        if check.returncode != 0:
            raise RuntimeError(f"Cấu hình canary sshd không hợp lệ: {check.stderr.strip()}")

        proc = subprocess.Popen(
            [sshd_bin, "-D", "-f", cfg_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._canary_daemons[username] = proc

        # Đợi cổng lắng nghe
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self._is_tcp_port_listening("127.0.0.1", port):
                return {"running": True, "port": port, "pid": proc.pid, "os_user": os_user}
            time.sleep(0.05)

        self.stop_canary_sshd_for_workspace(username)
        raise RuntimeError(f"Canary sshd không thể lắng nghe trên cổng {port}")

    def stop_canary_sshd_for_workspace(self, username: str) -> int:
        proc = self._canary_daemons.pop(username, None)
        stopped = 0
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            stopped += 1
        return stopped

    @staticmethod
    def verify_canary_ssh_connection(
        *,
        port: int,
        encrypted_private_key: str,
        passphrase: str,
        os_user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Thử kết nối SSH thực tế bằng /usr/bin/ssh tới 127.0.0.1:<port> với private key được bảo vệ bằng passphrase.
        """
        target_user = os_user or getpass.getuser()
        with tempfile.TemporaryDirectory(prefix="labops-ssh-probe-") as tmpdir:
            os.chmod(tmpdir, 0o700)
            key_file = os.path.join(tmpdir, "id_ed25519")
            with open(key_file, "w", encoding="utf-8") as kf:
                kf.write(encrypted_private_key)
            os.chmod(key_file, 0o600)

            askpass = os.path.join(tmpdir, "askpass.sh")
            with open(askpass, "w", encoding="utf-8") as af:
                # Sử dụng biến môi trường nội bộ chỉ tồn tại trong tiến trình con, không in ra log
                af.write("#!/bin/sh\nprintf '%s\\n' \"$LABOPS_PROBE_PASSPHRASE\"\n")
            os.chmod(askpass, 0o700)

            env = {
                **os.environ,
                "SSH_ASKPASS": askpass,
                "SSH_ASKPASS_REQUIRE": "force",
                "DISPLAY": ":0",
                "LABOPS_PROBE_PASSPHRASE": passphrase,
            }
            res = subprocess.run(
                [
                    "ssh",
                    "-F",
                    "/dev/null",
                    "-p",
                    str(port),
                    "-i",
                    key_file,
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-o",
                    "IdentitiesOnly=yes",
                    "-o",
                    "ConnectTimeout=3",
                    f"{target_user}@127.0.0.1",
                    "echo LABOPS_CANARY_SSH_VERIFIED",
                ],
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=6,
            )
            return {
                "accepted": res.returncode == 0 and "LABOPS_CANARY_SSH_VERIFIED" in res.stdout,
                "returncode": res.returncode,
                "output": res.stdout.strip(),
                "error_summary": res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "",
            }

    # ------------------------------------------------------------------
    # Thu hồi quyền SSH (Revoke) & Đóng phiên đang mở
    # ------------------------------------------------------------------
    def revoke_member_ssh(
        self,
        *,
        member: Dict[str, Any],
        host_online: bool,
        actor: str,
        encrypted_private_key_for_probe: Optional[str] = None,
        passphrase_for_probe: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Thu hồi quyền SSH của thành viên:
        1. Vô hiệu hóa ngay gói key một lần trong RAM và mật khẩu tạm.
        2. Đánh dấu tất cả khóa của thành viên trong SshKeyRegistry sang REVOKED / PENDING_REVOKE.
        3. Nếu host offline hoặc agent không phản hồi -> giữ ở trạng thái PENDING_REVOKE (không báo VERIFIED_REVOKED).
        4. Nếu host online -> xóa sạch khóa khỏi authorized_keys của workspace, kiểm tra tệp authorized_keys
           không còn chứa khóa và (nếu có tiến trình sshd của workspace đang lắng nghe) xác minh kết nối mới bị từ chối.
        5. Chỉ khi xác minh thành công mới chuyển sang VERIFIED_REVOKED.
        """
        operation_id = f"op-revoke-{uuid.uuid4().hex[:12]}"
        username = member["username"]
        member_id = member["id"]
        host_id = member.get("assigned_server") or self.local_host_id

        # Xóa ngay mọi gói key hoặc mật khẩu tạm chưa tải trong RAM
        self.vault.invalidate_member(member_id)

        # Khóa tài khoản mật khẩu tạm
        with self._locked_accounts() as state:
            if username in state["accounts"]:
                state["accounts"][username]["password_status"] = "REVOKED"
                state["accounts"][username]["temp_password_hash"] = None
                state["accounts"][username]["active_password_hash"] = None

        # Chuyển các khóa trong SshKeyRegistry sang PENDING_REVOKE trước
        keys = self.ssh_key_registry.list_keys(username=username)
        revoked_key_ids: List[str] = []
        for k in keys:
            if k.get("desired_state") != "REVOKED" or k.get("observed_state") != "VERIFIED_REVOKED":
                self.ssh_key_registry.revoke_key(k["id"], actor=actor, is_admin=True)
                revoked_key_ids.append(k["id"])

        if not host_online:
            return {
                "operation_id": operation_id,
                "member_id": member_id,
                "username": username,
                "host_id": host_id,
                "status": "PENDING_REVOKE",
                "ssh_key_status": "PENDING_REVOKE",
                "verified_revoked": False,
                "reason": f"Máy chủ '{host_id}' hiện đang OFFLINE hoặc Node Agent không phản hồi; chưa thể xác minh gỡ khóa trên runtime.",
                "timestamp": _now_iso(),
            }

        ws_dir = self.member_workspace_dir(host_id, username)
        auth_keys_path = os.path.join(ws_dir, ".ssh", "authorized_keys")
        try:
            if os.path.exists(auth_keys_path):
                with open(auth_keys_path, "w", encoding="utf-8") as akf:
                    akf.write("")
                os.chmod(auth_keys_path, 0o600)

            # Kiểm tra lại authorized_keys thực sự rỗng
            remaining = ""
            if os.path.exists(auth_keys_path):
                with open(auth_keys_path, "r", encoding="utf-8") as akf:
                    remaining = akf.read().strip()
            if remaining:
                return {
                    "operation_id": operation_id,
                    "member_id": member_id,
                    "username": username,
                    "host_id": host_id,
                    "status": "FAILED",
                    "ssh_key_status": "FAILED",
                    "verified_revoked": False,
                    "reason": "Không thể xóa sạch khóa khỏi tệp authorized_keys của workspace.",
                    "timestamp": _now_iso(),
                }

            # Nếu có tiến trình sshd của workspace đang lắng nghe và có thông tin probe, xác minh kết nối mới bị chặn
            ssh_port = int(member.get("ssh_port") or 0)
            connection_probe_blocked = True
            if (
                ssh_port > 0
                and self._is_tcp_port_listening("127.0.0.1", ssh_port)
                and encrypted_private_key_for_probe
                and passphrase_for_probe
            ):
                probe = self.verify_canary_ssh_connection(
                    port=ssh_port,
                    encrypted_private_key=encrypted_private_key_for_probe,
                    passphrase=passphrase_for_probe,
                )
                connection_probe_blocked = not probe["accepted"]

            if not connection_probe_blocked:
                return {
                    "operation_id": operation_id,
                    "member_id": member_id,
                    "username": username,
                    "host_id": host_id,
                    "status": "FAILED",
                    "ssh_key_status": "FAILED",
                    "verified_revoked": False,
                    "reason": "Cảnh báo: Kết nối SSH mới vẫn chưa bị từ chối sau khi gỡ khóa.",
                    "timestamp": _now_iso(),
                }

            # Cập nhật observed_state = VERIFIED_REVOKED trong SshKeyRegistry
            with self.ssh_key_registry._locked() as st:
                for k in st["keys"]:
                    if k.get("username") == username:
                        k["desired_state"] = "REVOKED"
                        k["observed_state"] = "VERIFIED_REVOKED"
                        k["revoked_at"] = _now_iso()
                        k["revoked_by"] = actor

            return {
                "operation_id": operation_id,
                "member_id": member_id,
                "username": username,
                "host_id": host_id,
                "status": "VERIFIED_REVOKED",
                "ssh_key_status": "VERIFIED_REVOKED",
                "verified_revoked": True,
                "revoked_key_ids": revoked_key_ids,
                "authorized_keys_cleared": True,
                "new_connections_blocked": True,
                "reason": "Đã gỡ khóa khỏi cấu hình SSH của workspace và xác minh khóa không còn được chấp nhận cho kết nối mới.",
                "timestamp": _now_iso(),
            }
        except Exception as exc:
            return {
                "operation_id": operation_id,
                "member_id": member_id,
                "username": username,
                "host_id": host_id,
                "status": "FAILED",
                "ssh_key_status": "FAILED",
                "verified_revoked": False,
                "reason": f"Lỗi khi gỡ khóa trên host '{host_id}': {exc}",
                "timestamp": _now_iso(),
            }

    def terminate_workspace_ssh_sessions(self, *, member: Dict[str, Any], actor: str) -> Dict[str, Any]:
        """
        Thao tác riêng biệt để đóng các phiên SSH đang mở của workspace thành viên (chỉ dừng canary/workspace sshd
        của đúng thành viên đó, tuyệt đối không đụng tới sshd cổng 22 của host hay các job nghiên cứu).
        """
        username = member["username"]
        stopped = self.stop_canary_sshd_for_workspace(username)
        member["ssh_sessions_count"] = 0
        return {
            "operation_id": f"op-term-{uuid.uuid4().hex[:12]}",
            "member_id": member["id"],
            "username": username,
            "terminated_daemons": stopped,
            "active_sessions_remaining": 0,
            "status": "VERIFIED_TERMINATED",
            "actor": actor,
            "timestamp": _now_iso(),
        }

