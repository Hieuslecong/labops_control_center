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

import base64
import fcntl
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import signal
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

FIRST_LOGIN_GATE_SCRIPT = r'''#!/usr/bin/env python3
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

POLICY_PATH = os.environ.get("LABOPS_FIRST_LOGIN_POLICY_PATH", "/etc/labops-ssh/first_login_policy.json")
ACTIVATION_PATH = os.environ.get("LABOPS_FIRST_LOGIN_ACTIVATION_PATH", "/workspace/.labops_activation.json")
ATTEMPTS_PATH = os.environ.get("LABOPS_FIRST_LOGIN_ATTEMPTS_PATH", "/workspace/.labops_activation_attempts.json")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as rf:
            data = json.load(rf)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json_atomic(path: str, payload: Dict[str, Any], mode: int = 0o644) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as wf:
        json.dump(payload, wf, ensure_ascii=False, indent=2)
        wf.flush()
        os.fsync(wf.fileno())
    os.chmod(tmp_path, mode)
    os.replace(tmp_path, path)


def _hash_password_scrypt(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=16384,
        r=8,
        p=1,
        maxmem=32 * 1024 * 1024,
        dklen=32,
    )
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    if stored.startswith("scrypt$"):
        parts = stored.split("$")
        if len(parts) != 6:
            return False
        _, n_s, r_s, p_s, salt_hex, expected_hex = parts
        try:
            salt = bytes.fromhex(salt_hex)
            n_val, r_val, p_val = int(n_s), int(r_s), int(p_s)
            dklen = len(bytes.fromhex(expected_hex))
        except ValueError:
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n_val,
            r=r_val,
            p=p_val,
            maxmem=64 * 1024 * 1024,
            dklen=dklen,
        )
        return hmac.compare_digest(dk.hex(), expected_hex)
    if stored.startswith("pbkdf2_sha256$"):
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
    return False


def _validate_new_password_strength(new_password: str, temp_password: str) -> Optional[str]:
    if len(new_password) < 12:
        return "Mật khẩu mới phải có ít nhất 12 ký tự."
    if not any(c.islower() for c in new_password):
        return "Mật khẩu mới phải chứa ít nhất 1 chữ thường."
    if not any(c.isupper() for c in new_password):
        return "Mật khẩu mới phải chứa ít nhất 1 chữ hoa."
    if not any(c.isdigit() for c in new_password):
        return "Mật khẩu mới phải chứa ít nhất 1 chữ số."
    if not any(not c.isalnum() for c in new_password):
        return "Mật khẩu mới phải chứa ít nhất 1 ký tự đặc biệt."
    if new_password == temp_password:
        return "Mật khẩu mới không được trùng với mật khẩu tạm."
    return None


def _launch_target_shell() -> None:
    orig_cmd = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
    if os.path.isdir("/workspace"):
        try:
            os.chdir("/workspace")
        except OSError:
            pass
    if orig_cmd:
        if orig_cmd == "internal-sftp" and os.path.exists("/usr/lib/openssh/sftp-server"):
            os.execl("/usr/lib/openssh/sftp-server", "sftp-server")
        os.execl("/bin/bash", "bash", "-c", orig_cmd)
    os.execl("/bin/bash", "-bash")


def main() -> int:
    policy = _load_json(POLICY_PATH)
    if not policy or not policy.get("must_change_password"):
        _launch_target_shell()
        return 0

    expected_hash = str(policy.get("temp_password_hash") or "").strip()
    raw_accepted = policy.get("accepted_temp_password_hashes")
    accepted_hashes: List[str] = []
    for h in [expected_hash] + (raw_accepted if isinstance(raw_accepted, list) else []):
        h_s = str(h or "").strip()
        if h_s and h_s not in accepted_hashes:
            accepted_hashes.append(h_s)
    if not expected_hash and not accepted_hashes:
        _launch_target_shell()
        return 0
    if not expected_hash:
        expected_hash = accepted_hashes[0]

    activation = _load_json(ACTIVATION_PATH)
    activated_for = str(activation.get("activated_for_hash") or "").strip()
    if activation.get("activated") is True and activated_for and activated_for in accepted_hashes:
        _launch_target_shell()
        return 0

    username = str(policy.get("username") or os.environ.get("USER") or "member")

    if not sys.stdin.isatty():
        sys.stderr.write(
            f"\n🔒 Tài khoản SSH @{username} chưa kích hoạt mật khẩu lần đầu.\n"
            f"   Vui lòng kết nối bằng terminal tương tác: ssh labops-{username}\n\n"
        )
        sys.stderr.flush()
        return 1

    exp_dt = _parse_iso(policy.get("temp_password_expires_at"))
    if exp_dt is not None and _now_utc() > exp_dt:
        sys.stderr.write(
            "\n❌ Mật khẩu tạm lần đầu đã hết hạn.\n"
            "   Vui lòng liên hệ Admin LabOps để cấp lại mật khẩu tạm mới.\n\n"
        )
        sys.stderr.flush()
        return 1

    print("==================================================================")
    print(f"🔐 LabOps Control Center — Kích hoạt Workspace lần đầu (@{username})")
    print("   Mật khẩu tạm chỉ dùng 1 lần. Vui lòng xác thực và đổi mật khẩu mới.")
    print("==================================================================")
    sys.stdout.flush()

    verified_temp: Optional[str] = None
    while True:
        try:
            candidate = getpass.getpass("Nhập mật khẩu tạm (LabOps!...): ").rstrip("\r\n")
        except (EOFError, KeyboardInterrupt):
            print("\nĐã hủy phiên kích hoạt SSH (bạn có thể chạy lại ssh labops-" + username + " bất cứ lúc nào).")
            return 1

        if not candidate:
            print("❌ Mật khẩu tạm không được để trống. Vui lòng thử lại (hoặc nhấn Ctrl+C để thoát).")
            continue

        if any(_verify_password(candidate, h) for h in accepted_hashes):
            verified_temp = candidate
            break

        print("❌ Mật khẩu tạm không chính xác. Vui lòng thử lại (hoặc nhấn Ctrl+C để thoát).")

    if verified_temp is None:
        return 1

    print("\n✅ Xác thực mật khẩu tạm thành công. Hãy đặt mật khẩu mới mạnh để mở khóa Workspace:")
    print("   Yêu cầu: >= 12 ký tự, có chữ hoa, chữ thường, chữ số và ký tự đặc biệt.")
    sys.stdout.flush()

    new_password: Optional[str] = None
    while True:
        try:
            pw1 = getpass.getpass("Mật khẩu mới: ").rstrip("\r\n")
            pw2 = getpass.getpass("Nhập lại mật khẩu mới: ").rstrip("\r\n")
        except (EOFError, KeyboardInterrupt):
            print("\nĐã hủy phiên kích hoạt SSH (bạn có thể chạy lại ssh labops-" + username + " bất cứ lúc nào).")
            return 1

        if pw1 != pw2:
            print("❌ Mật khẩu xác nhận không khớp. Vui lòng nhập lại.")
            continue

        err = _validate_new_password_strength(pw1, verified_temp)
        if err:
            print(f"❌ {err}")
            continue

        new_password = pw1
        break

    activation_payload = {
        "username": username,
        "member_id": policy.get("member_id"),
        "activated": True,
        "activated_at": _now_iso(),
        "activated_for_hash": expected_hash,
        "new_password_hash": _hash_password_scrypt(new_password),
    }
    try:
        _save_json_atomic(ACTIVATION_PATH, activation_payload, mode=0o644)
    except OSError as exc:
        sys.stderr.write(f"\n❌ Không thể lưu trạng thái kích hoạt vào workspace: {exc}\n")
        sys.stderr.flush()
        return 1

    if os.path.exists(ATTEMPTS_PATH):
        try:
            os.remove(ATTEMPTS_PATH)
        except OSError:
            pass

    print("\n🎉 Kích hoạt thành công! Mật khẩu mới đã được thiết lập.")
    print("   Từ các lần kết nối sau sẽ vào thẳng terminal workspace.\n")
    sys.stdout.flush()
    _launch_target_shell()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


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
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=16384,
        r=8,
        p=1,
        maxmem=32 * 1024 * 1024,
        dklen=32,
    )
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    if stored.startswith("scrypt$"):
        parts = stored.split("$")
        if len(parts) != 6:
            return False
        _, n_s, r_s, p_s, salt_hex, expected_hex = parts
        try:
            salt = bytes.fromhex(salt_hex)
            n_val, r_val, p_val = int(n_s), int(r_s), int(p_s)
        except ValueError:
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n_val,
            r=r_val,
            p=p_val,
            maxmem=64 * 1024 * 1024,
            dklen=len(bytes.fromhex(expected_hex)),
        )
        return hmac.compare_digest(dk.hex(), expected_hex)
    if stored.startswith("pbkdf2_sha256$"):
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
    if stored.startswith("$argon2id$"):
        try:
            from argon2 import PasswordHasher  # type: ignore[import-not-found]
            PasswordHasher().verify(stored, password)
            return True
        except Exception:
            return False
    return False


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


def _build_ssh_config_snippet(
    *,
    username: str,
    ssh_host: str,
    ssh_port: int,
    ssh_login_user: Optional[str] = None,
) -> str:
    """Sinh block OpenSSH client config thống nhất cho gói cài đặt thành viên."""
    host_alias = f"labops-{username}"
    effective_user = (ssh_login_user or username).strip() or username
    return (
        f"# Cấu hình OpenSSH LabOps Control Center cho @{username} (Workspace Account: User {username})\n"
        f"# Lưu vào ~/.ssh/config (macOS/Linux) hoặc %USERPROFILE%\\.ssh\\config (Windows)\n"
        f"Host {host_alias}\n"
        f"    HostName {ssh_host}\n"
        f"    Port {ssh_port}\n"
        f"    User {effective_user}\n"
        f"    IdentityFile ~/.ssh/id_ed25519_{username}\n"
        f"    IdentitiesOnly yes\n"
        f"    StrictHostKeyChecking accept-new\n"
        f"    UserKnownHostsFile ~/.ssh/known_hosts_labops\n"
        f"    ServerAliveInterval 60\n"
        f"    ServerAliveCountMax 3\n"
    )


def _build_ssh_config_upsert_shell(host_alias: str) -> str:
    """Sinh đoạn bash+awk xóa block Host cũ (nếu có) trước khi ghi block mới vào ~/.ssh/config."""
    return (
        f"awk -v target=\"Host {host_alias}\" '\n"
        "BEGIN { skip = 0 }\n"
        "/^[ \\t]*(Host|Match)[ \\t]+/ {\n"
        "    if ($0 ~ \"^[ \\t]*\" target \"([ \\t]|$)\") {\n"
        "        skip = 1\n"
        "        next\n"
        "    } else {\n"
        "        skip = 0\n"
        "    }\n"
        "}\n"
        "!skip { print }\n"
        "' \"$HOME/.ssh/config\" > \"$HOME/.ssh/config.labops.tmp\" && mv \"$HOME/.ssh/config.labops.tmp\" \"$HOME/.ssh/config\"\n"
        "chmod 600 \"$HOME/.ssh/config\"\n"
    )


def _build_posix_setup_script(
    *,
    os_label: str,
    username: str,
    ssh_host: str,
    ssh_port: int,
    ssh_config_snippet: Optional[str] = None,
    embedded_private_key: Optional[str] = None,
) -> str:
    """Sinh script cài đặt tự động ~/.ssh/config + khóa riêng + kiểm tra TCP tới workspace cho macOS/Linux."""
    host_alias = f"labops-{username}"
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"echo \"=== Cài đặt SSH LabOps cho {os_label} (@{username}) ===\"",
        "mkdir -p \"$HOME/.ssh\"",
        "chmod 700 \"$HOME/.ssh\"",
    ]
    if embedded_private_key is not None:
        lines.extend([
            f"KEY_PATH=\"$HOME/.ssh/id_ed25519_{username}\"",
            "cat > \"$KEY_PATH\" <<'EOF_KEY'",
            embedded_private_key.rstrip(),
            "EOF_KEY",
            "chmod 600 \"$KEY_PATH\"",
        ])
    else:
        lines.extend([
            "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"",
            "PKG_ROOT=\"$(dirname \"$SCRIPT_DIR\")\"",
            f"if [ -f \"$PKG_ROOT/ssh/id_ed25519_{username}\" ]; then",
            f"    cp -f \"$PKG_ROOT/ssh/id_ed25519_{username}\" \"$HOME/.ssh/id_ed25519_{username}\"",
            f"    chmod 600 \"$HOME/.ssh/id_ed25519_{username}\"",
            f"    echo \"✅ Đã cài đặt khóa SSH: ~/.ssh/id_ed25519_{username}\"",
            "fi",
        ])

    lines.extend([
        "touch \"$HOME/.ssh/config\"",
        "chmod 600 \"$HOME/.ssh/config\"",
        f"HAD_EXISTING=0",
        f"if grep -qE \"^[ \\t]*Host[ \\t]+{host_alias}([ \\t]|$)\" \"$HOME/.ssh/config\" 2>/dev/null; then",
        "    HAD_EXISTING=1",
        "fi",
        _build_ssh_config_upsert_shell(host_alias).rstrip("\n"),
        "printf \"\\n\" >> \"$HOME/.ssh/config\"",
    ])

    if ssh_config_snippet is not None:
        lines.extend([
            "cat >> \"$HOME/.ssh/config\" <<'EOF_CFG'",
            ssh_config_snippet.rstrip(),
            "EOF_CFG",
        ])
    else:
        lines.append("cat \"$PKG_ROOT/ssh/config\" >> \"$HOME/.ssh/config\"")

    lines.extend([
        "chmod 600 \"$HOME/.ssh/config\"",
        "if [ \"$HAD_EXISTING\" -eq 1 ]; then",
        f"    echo \"✅ Đã cập nhật cấu hình Host {host_alias} mới nhất trong ~/.ssh/config\"",
        "else",
        f"    echo \"✅ Đã thêm cấu hình Host {host_alias} vào ~/.ssh/config\"",
        "fi",
        "TCP_OK=0",
        "if command -v nc >/dev/null 2>&1; then",
        f"    if nc -z -w 3 \"{ssh_host}\" \"{ssh_port}\" >/dev/null 2>&1; then",
        "        TCP_OK=1",
        "    fi",
        "elif command -v python3 >/dev/null 2>&1; then",
        f"    if python3 -c \"import socket; s=socket.socket(); s.settimeout(3); s.connect(('{ssh_host}', {ssh_port})); s.close()\" 2>/dev/null; then",
        "        TCP_OK=1",
        "    fi",
        "fi",
        "echo \"\"",
        "echo \"🎉 Hoàn tất cài đặt!\"",
        "if [ \"$TCP_OK\" -eq 1 ]; then",
        f"    echo \"✅ Kết nối TCP tới {ssh_host}:{ssh_port} thông suốt. Chạy ngay:\"",
        f"    echo \"   ssh {host_alias}\"",
        "else",
        f"    echo \"⚠️  Chưa kết nối được TCP tới {ssh_host}:{ssh_port}!\"",
        "    echo \"   • Nếu bạn đang ở cùng mạng LAN phòng Lab và đã BẬT WireGuard nhưng không thấy 'Latest handshake' (Data received = 0 B):\"",
        f"    echo \"     Hãy BẤM TẮT (Deactivate) tunnel WireGuard trong ứng dụng WireGuard để kết nối trực tiếp qua LAN tới {ssh_host}:{ssh_port}.\"",
        "    echo \"   • Nếu bạn kết nối từ bên ngoài Internet: Hãy kiểm tra WireGuard đã có handshake hợp lệ từ máy chủ VPN Gateway.\"",
        f"    echo \"👉 Sau khi kiểm tra mạng, kết nối bằng lệnh: ssh {host_alias}\"",
        "fi",
    ])
    return "\n".join(lines) + "\n"


class OneTimeSecretVault:
    """
    Kho lưu tạm một lần có thể đồng bộ an toàn giữa đa tiến trình (Zalo Worker & Web API).
    Tách biệt 2 kênh bàn giao:
    - Kênh 1 (key_bundle): Gói kết nối + Private key đã mã hóa bằng passphrase.
    - Kênh 2 (secrets): Passphrase mở khóa private key + Mật khẩu tạm đăng nhập lần đầu.
    Dữ liệu trên đĩa luôn được niêm phong mã hóa (0600, flock) và tự động xóa sau 1 lần nhận hoặc hết hạn.
    """

    def __init__(self, default_ttl_seconds: int = 86400, storage_path: Optional[str] = None):
        import threading

        self.default_ttl_seconds = default_ttl_seconds
        self.storage_path: Optional[str] = os.path.realpath(storage_path) if storage_path else None
        self._thread_lock = threading.RLock()
        self._key_bundles: Dict[str, Dict[str, Any]] = {}
        self._channel_secrets: Dict[str, Dict[str, Any]] = {}
        if self.storage_path:
            with self._vault_lock():
                self._sync_from_disk_unlocked()

    def _vault_lock(self):
        from app.db.atomic_json import file_lock

        thread_lock = self._thread_lock
        storage_path = self.storage_path

        class _Guard:
            def __init__(self) -> None:
                self._fl = None

            def __enter__(self) -> None:
                thread_lock.acquire()
                try:
                    if storage_path:
                        self._fl = file_lock(storage_path)
                        self._fl.__enter__()
                except Exception:
                    thread_lock.release()
                    raise

            def __exit__(self, exc_type, exc, tb) -> None:
                try:
                    if self._fl is not None:
                        self._fl.__exit__(exc_type, exc, tb)
                finally:
                    thread_lock.release()

        return _Guard()

    def _cipher_key(self) -> bytes:
        import hashlib

        secret = (
            os.getenv("LABOPS_SESSION_SECRET")
            or os.getenv("LABOPS_DELEGATION_SECRET")
            or os.getenv("LABOPS_BROKER_VERIFY_SECRET")
        )
        if not secret:
            try:
                from app.core.config import settings

                secret = (
                    getattr(settings, "LABOPS_SESSION_SECRET", "")
                    or getattr(settings, "LABOPS_DELEGATION_SECRET", "")
                    or getattr(settings, "LABOPS_BROKER_VERIFY_SECRET", "")
                )
            except Exception:
                secret = ""
        material = f"{secret or 'labops-v2-handover-vault'}|{self.storage_path or ''}".encode("utf-8")
        return hashlib.sha256(material).digest()

    @staticmethod
    def _xor_keystream(key: bytes, nonce: bytes, data: bytes) -> bytes:
        import hashlib

        out = bytearray(len(data))
        offset = 0
        counter = 0
        while offset < len(data):
            block = hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
            take = min(len(block), len(data) - offset)
            for idx in range(take):
                out[offset + idx] = data[offset + idx] ^ block[idx]
            offset += take
            counter += 1
        return bytes(out)

    def _seal_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        import base64
        import hmac
        import json

        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(16)
        key = self._cipher_key()
        ct = self._xor_keystream(key, nonce, raw)
        mac = hmac.new(key, b"labops-vault-v1:" + nonce + ct, "sha256").hexdigest()
        return {
            "version": 1,
            "nonce": nonce.hex(),
            "ciphertext": base64.urlsafe_b64encode(ct).decode("ascii"),
            "mac": mac,
        }

    def _unseal_state(self, envelope: Any) -> Dict[str, Dict[str, Any]]:
        import base64
        import hmac
        import json

        if not isinstance(envelope, dict) or envelope.get("version") != 1:
            return {"key_bundles": {}, "channel_secrets": {}}
        try:
            nonce = bytes.fromhex(str(envelope.get("nonce") or ""))
            ct = base64.urlsafe_b64decode(str(envelope.get("ciphertext") or "").encode("ascii"))
            mac = str(envelope.get("mac") or "")
            key = self._cipher_key()
            expected_mac = hmac.new(key, b"labops-vault-v1:" + nonce + ct, "sha256").hexdigest()
            if not hmac.compare_digest(expected_mac, mac):
                return {"key_bundles": {}, "channel_secrets": {}}
            raw = self._xor_keystream(key, nonce, ct)
            decoded = json.loads(raw.decode("utf-8"))
            kb = decoded.get("key_bundles") if isinstance(decoded.get("key_bundles"), dict) else {}
            cs = decoded.get("channel_secrets") if isinstance(decoded.get("channel_secrets"), dict) else {}
            return {"key_bundles": dict(kb), "channel_secrets": dict(cs)}
        except Exception:
            return {"key_bundles": {}, "channel_secrets": {}}

    @staticmethod
    def _purge_stores(key_bundles: Dict[str, Dict[str, Any]], channel_secrets: Dict[str, Dict[str, Any]]) -> bool:
        now = time.time()
        changed = False
        for store in (key_bundles, channel_secrets):
            expired = [k for k, v in store.items() if now > v.get("expires_at_epoch", 0)]
            for k in expired:
                store.pop(k, None)
                changed = True
        return changed

    def _load_disk_unlocked(self) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        if not self.storage_path or not os.path.exists(self.storage_path):
            return {}, {}
        from app.db.atomic_json import PersistenceCorruptionError, safe_load_json

        try:
            envelope = safe_load_json(self.storage_path, dict)
        except PersistenceCorruptionError:
            return {}, {}
        unsealed = self._unseal_state(envelope)
        kb = unsealed["key_bundles"]
        cs = unsealed["channel_secrets"]
        self._purge_stores(kb, cs)
        return kb, cs

    def _write_disk_unlocked(self, kb: Dict[str, Dict[str, Any]], cs: Dict[str, Dict[str, Any]]) -> None:
        if not self.storage_path:
            return
        from app.db.atomic_json import atomic_write_json

        self._purge_stores(kb, cs)
        if not kb and not cs:
            try:
                if os.path.exists(self.storage_path):
                    os.unlink(self.storage_path)
            except OSError:
                pass
            return
        sealed = self._seal_state({"key_bundles": kb, "channel_secrets": cs})
        atomic_write_json(self.storage_path, sealed, mode=0o600, use_lock=False)

    def _sync_from_disk_unlocked(self) -> None:
        if self.storage_path:
            kb, cs = self._load_disk_unlocked()
            self._key_bundles.clear()
            self._key_bundles.update(kb)
            self._channel_secrets.clear()
            self._channel_secrets.update(cs)
        self._purge_stores(self._key_bundles, self._channel_secrets)

    def _commit_member_unlocked(self, member_id: str) -> None:
        if self.storage_path:
            kb, cs = self._load_disk_unlocked()
            if member_id in self._key_bundles:
                kb[member_id] = dict(self._key_bundles[member_id])
            else:
                kb.pop(member_id, None)
            if member_id in self._channel_secrets:
                cs[member_id] = dict(self._channel_secrets[member_id])
            else:
                cs.pop(member_id, None)
            self._write_disk_unlocked(kb, cs)
            self._key_bundles.clear()
            self._key_bundles.update(kb)
            self._channel_secrets.clear()
            self._channel_secrets.update(cs)
        else:
            self._purge_stores(self._key_bundles, self._channel_secrets)

    def _purge_expired(self) -> None:
        with self._vault_lock():
            self._sync_from_disk_unlocked()

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
        ssh_login_user: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._purge_expired()
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        exp_epoch = time.time() + ttl
        exp_iso = datetime.fromtimestamp(exp_epoch, tz=timezone.utc).isoformat()
        bundle_id = f"bnd-{uuid.uuid4().hex[:12]}"
        secret_id = f"sec-{uuid.uuid4().hex[:12]}"
        host_alias = f"labops-{username}"
        effective_ssh_user = (ssh_login_user or username).strip() or username

        if not ssh_config_snippet:
            ssh_config_snippet = _build_ssh_config_snippet(
                username=username,
                ssh_host=ssh_host,
                ssh_port=ssh_port,
                ssh_login_user=effective_ssh_user,
            )

        if not quick_setup_script:
            quick_setup_script = _build_posix_setup_script(
                os_label="macOS / Linux",
                username=username,
                ssh_host=ssh_host,
                ssh_port=ssh_port,
                ssh_config_snippet=ssh_config_snippet,
                embedded_private_key=encrypted_private_key,
            )

        self._key_bundles[member_id] = {
            "bundle_id": bundle_id,
            "member_id": member_id,
            "username": username,
            "host_alias": host_alias,
            "ssh_host": ssh_host,
            "ssh_port": ssh_port,
            "ssh_login_user": effective_ssh_user,
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
            "ssh_command": f"ssh -i ~/.ssh/id_ed25519_{username} -p {ssh_port} {effective_ssh_user}@{ssh_host}",
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
        with self._vault_lock():
            self._commit_member_unlocked(member_id)
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
        with self._vault_lock():
            self._sync_from_disk_unlocked()
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
            self._commit_member_unlocked(member_id)
            return {
                "secret_id": secret_id,
                "channel_secrets_available": True,
                "expires_at": exp_iso,
            }

    def get_availability(self, member_id: str) -> Dict[str, Any]:
        with self._vault_lock():
            self._sync_from_disk_unlocked()
            kb = self._key_bundles.get(member_id)
            cs = self._channel_secrets.get(member_id)
            return {
                "key_bundle_available": kb is not None,
                "key_bundle_expires_at": kb.get("expires_at") if kb else None,
                "channel_secrets_available": cs is not None,
                "channel_secrets_expires_at": cs.get("expires_at") if cs else None,
            }

    def claim_key_bundle_once(self, member_id: str, peek: bool = False) -> Dict[str, Any]:
        with self._vault_lock():
            self._sync_from_disk_unlocked()
            entry = self._key_bundles.get(member_id) if peek else self._key_bundles.pop(member_id, None)
            if not entry:
                raise LookupError(
                    "KEY_BUNDLE_UNAVAILABLE: Gói khóa riêng đã được tải một lần trước đó, đã hết hạn hoặc đã bị thu hồi. "
                    "Để lấy khóa mới, vui lòng thực hiện 'Cấp lại cặp khóa SSH mới'."
                )
            if not peek:
                self._commit_member_unlocked(member_id)
            result = dict(entry)
            result.pop("expires_at_epoch", None)
            return result

    def claim_channel_secrets_once(self, member_id: str) -> Dict[str, Any]:
        with self._vault_lock():
            self._sync_from_disk_unlocked()
            entry = self._channel_secrets.pop(member_id, None)
            if not entry:
                raise LookupError(
                    "SECRETS_UNAVAILABLE: Passphrase và mật khẩu tạm chỉ được hiển thị một lần duy nhất và hiện không còn trong bộ nhớ tạm."
                )
            self._commit_member_unlocked(member_id)
            result = dict(entry)
            result.pop("expires_at_epoch", None)
            return result

    def invalidate_member(self, member_id: str) -> None:
        with self._vault_lock():
            self._sync_from_disk_unlocked()
            self._key_bundles.pop(member_id, None)
            self._channel_secrets.pop(member_id, None)
            self._commit_member_unlocked(member_id)


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
        self.vault = OneTimeSecretVault(
            default_ttl_seconds=86400,
            storage_path=os.path.join(self.data_dir, "v2_handover_vault.json"),
        )
        self._canary_daemons: Dict[str, subprocess.Popen] = {}
        os.makedirs(self.workspaces_root, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.workspaces_root, 0o700)
        except OSError:
            pass

    @contextmanager
    def _locked_accounts(self):
        from app.db.atomic_json import atomic_write_json, safe_load_json

        with open(self.accounts_lock, "a+", encoding="utf-8") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            state: Optional[Dict[str, Any]] = None
            try:
                state = safe_load_json(self.accounts_path, lambda: {"accounts": {}})
                yield state
            finally:
                try:
                    if state is not None:
                        atomic_write_json(self.accounts_path, state, mode=0o600, use_lock=False)
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)

    def get_account_security_state(
        self, username: str, *, expected_member_id: Optional[str] = None
    ) -> Dict[str, Any]:
        if not os.path.exists(self.accounts_path):
            return {}
        with open(self.accounts_path, "r", encoding="utf-8") as rf:
            accounts = json.load(rf).get("accounts", {})
        rec = dict(accounts.get(username) or {})
        if not rec:
            return {}
        if expected_member_id and rec.get("member_id") and rec.get("member_id") != expected_member_id:
            return {}
        # Tuyệt đối không bao giờ trả về password hash ra ngoài
        rec.pop("temp_password_hash", None)
        rec.pop("active_password_hash", None)
        return rec

    def snapshot_account_state(self, username: str) -> Dict[str, Any]:
        """Capture prior active/archived entry for clean compensation if DB commit fails."""
        with self._locked_accounts() as state:
            acct = state.get("accounts", {}).get(username)
            archived = dict(state.get("archived_accounts") or {})
            return {
                "had_account": acct is not None,
                "account": dict(acct) if isinstance(acct, dict) else None,
                "archived_keys": set(archived.keys()),
            }

    def restore_account_state(
        self,
        username: str,
        snapshot: Dict[str, Any],
        *,
        expected_new_member_id: Optional[str] = None,
    ) -> None:
        """Restore pre-provisioning account state cleanly without destroying prior member history."""
        with self._locked_accounts() as state:
            current = state.get("accounts", {}).get(username)
            if expected_new_member_id and isinstance(current, dict):
                cur_mid = current.get("member_id")
                if cur_mid and cur_mid != expected_new_member_id:
                    return
            if snapshot.get("had_account") and isinstance(snapshot.get("account"), dict):
                state["accounts"][username] = dict(snapshot["account"])
                old_mid = snapshot["account"].get("member_id")
                archived = state.get("archived_accounts")
                if isinstance(archived, dict) and old_mid and old_mid not in (snapshot.get("archived_keys") or set()):
                    archived.pop(old_mid, None)
            else:
                state.get("accounts", {}).pop(username, None)

    def member_workspace_dir(self, host_id: str, username: str, storage_root: Optional[str] = None) -> str:
        if not SAFE_USERNAME_RE.match(username) or username in RESERVED_USERNAMES:
            raise ValueError(f"Username '{username}' không hợp lệ hoặc thuộc danh sách hệ thống cấm")
        safe_host = re.sub(r"[^a-z0-9_-]", "", host_id.lower()) or "track"

        if storage_root:
            clean_root = os.path.abspath(storage_root)
            expected_base = os.path.join(clean_root, "labops", "users")
            user_dir = os.path.join(expected_base, username)
            if not user_dir.startswith(expected_base + os.sep):
                raise ValueError(f"SECURITY_VIOLATION: Path traversal in storage_root: {user_dir}")
            os.makedirs(user_dir, mode=0o700, exist_ok=True)
            try:
                os.chmod(user_dir, 0o700)
            except OSError:
                pass
            return user_dir

        # Mặc định ưu tiên tuyệt đối phân bổ không gian lưu trữ người dùng vào ổ đĩa /media/LabOps-Data/users
        from app.core.config import settings
        user_data_override = (os.getenv("LABOPS_USER_DATA_DIR") or "").strip()
        default_data_dir = user_data_override or getattr(settings, "DEFAULT_USER_DATA_DIR", "/media/LabOps-Data/users")
        default_backend_data = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
        using_custom_test_dir = os.path.abspath(self.data_dir) != default_backend_data
        use_override_root = bool(user_data_override) or not using_custom_test_dir
        if use_override_root and (safe_host == "track" or safe_host == getattr(settings, "LOCAL_HOST_ID", "track")) and os.path.exists(default_data_dir):
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
        allowed_ips: str = "10.88.40.0/24",
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
        except Exception as e:
            raise ValueError(f"Khóa công khai máy chủ WireGuard không hợp lệ ({server_public_key}): {e}") from e

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
        ssh_login_user: Optional[str] = None,
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
        effective_ssh_user = (ssh_login_user or username).strip() or username
        has_priv = bool(private_key and private_key.strip())
        wg_ip = wireguard_client_ip or "CHƯA CẤP"
        wg_ep = wireguard_endpoint or "CHƯA XÁC MINH"

        ssh_config_content = _build_ssh_config_snippet(
            username=username,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_login_user=effective_ssh_user,
        )

        ssh_cmd_text = (
            f"ssh {host_alias}\n"
            f"# Hoặc kết nối trực tiếp:\n"
            f"ssh -i ~/.ssh/id_ed25519_{username} -p {ssh_port} {effective_ssh_user}@{ssh_host}\n"
        )

        readme_content = f"""# HƯỚNG DẪN KẾT NỐI HỆ THỐNG LABOPS CONTROL CENTER V2
Dành cho thành viên: **@{username}** | Workspace: **{workspace_name}**

---

## 1. THÔNG TIN TRUY CẬP ĐƯỢC CẤP
- **Tên tài khoản Workspace**: `{username}`
- **SSH Transport User**: `{effective_ssh_user}`
- **Máy chủ SSH**: `{ssh_host}`
- **Cổng SSH Container/Workspace**: `{ssh_port}`
- **Địa chỉ VPN WireGuard của bạn**: `{wg_ip}`
- **Endpoint VPN Server**: `{wg_ep}`
- **Lệnh kết nối nhanh**: `ssh {host_alias}`
- **Passphrase & Mật khẩu kích hoạt**: Được Admin cấp qua kênh riêng (**Separate-Channel Delivery**, KHÔNG nhúng trong tệp ZIP này).

---

## 2. QUY TRÌNH KẾT NỐI

### BƯỚC 1: KIỂM TRA MẠNG (LAN PHÒNG LAB HOẶC WIREGUARD VPN)
- **Nếu bạn đang ở cùng mạng LAN phòng Lab (ví dụ cùng mạng `192.168.167.x`)**:
  KHÔNG cần bật WireGuard (hãy bấm **Deactivate** nếu đang bật tunnel mà thấy `Data received` trống / chưa có `Latest handshake`, vì bật tunnel chết sẽ làm `Operation timed out` tới `{ssh_host}`).
- **Nếu bạn kết nối từ xa qua Internet**:
  1. Cài đặt WireGuard từ https://www.wireguard.com/install/
  2. Mở ứng dụng WireGuard -> **Add Tunnel** -> **Import tunnel(s) from file** -> chọn `wireguard/labops.conf`.
  3. Bấm **Activate** và kiểm tra phải có dòng `Latest handshake` + `Data received > 0 B`.

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
3. Cập nhật nội dung tệp `ssh/config` vào tệp `~/.ssh/config`.
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

        setup_macos_sh = _build_posix_setup_script(
            os_label="macOS",
            username=username,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
        )

        setup_linux_sh = _build_posix_setup_script(
            os_label="Linux",
            username=username,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
        )

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
$configSnippet = (Get-Content (Join-Path $pkgRoot "ssh\\config") -Raw).TrimEnd()

if (Test-Path $configFile) {{
    $current = Get-Content $configFile -Raw
    if ($current -match "(?m)^[ \\t]*Host[ \\t]+{host_alias}([ \\t]*\\r?$|\\s)") {{
        $cleaned = [regex]::Replace($current, "(?ms)^[ \\t]*Host[ \\t]+{host_alias}(?=[ \\t]*\\r?$).*?(?=^[ \\t]*(?:Host|Match)[ \\t]+|\\z)", "").TrimEnd()
        Set-Content -Path $configFile -Value ($cleaned + "`n`n" + $configSnippet + "`n")
        Write-Host "✅ Đã cập nhật cấu hình Host {host_alias} trong $configFile" -ForegroundColor Green
    }} else {{
        Add-Content -Path $configFile -Value ("`n" + $configSnippet)
        Write-Host "✅ Đã bổ sung cấu hình vào $configFile" -ForegroundColor Green
    }}
}} else {{
    Set-Content -Path $configFile -Value ($configSnippet + "`n")
    Write-Host "✅ Đã tạo tệp cấu hình $configFile" -ForegroundColor Green
}}

Write-Host "`n🎉 Hoàn tất cài đặt!" -ForegroundColor Cyan
Write-Host "👉 Nếu bạn đang ở cùng mạng LAN phòng Lab, hãy tắt (Deactivate) WireGuard nếu chưa có Latest handshake." -ForegroundColor Gray
Write-Host "👉 Kết nối ngay bằng lệnh: ssh {host_alias}" -ForegroundColor Yellow
"""

        conn_info_dict = {
            "username": username,
            "ssh_login_user": effective_ssh_user,
            "workspace_name": workspace_name,
            "ssh_host": ssh_host,
            "ssh_port": ssh_port,
            "ssh_alias": host_alias,
            "ssh_command": f"ssh {host_alias}",
            "direct_ssh_command": f"ssh -i ~/.ssh/id_ed25519_{username} -p {ssh_port} {effective_ssh_user}@{ssh_host}",
            "wireguard_client_ip": wg_ip,
            "wireguard_endpoint": wg_ep,
            "has_private_key": has_priv,
            "has_passphrase": bool(key_passphrase),
            "separate_channel_secrets_required": True,
            "activation_required": True,
            "generated_at": _now_iso(),
        }

        conn_info_txt = (
            f"=== THÔNG TIN KẾT NỐI LABOPS V2 ===\n"
            f"Thành viên        : @{username}\n"
            f"SSH User          : {effective_ssh_user}\n"
            f"Workspace         : {workspace_name}\n"
            f"SSH Host          : {ssh_host}\n"
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
            else:
                zf.writestr(
                    f"{root}/wireguard/WIREGUARD_PENDING.md",
                    "# WIREGUARD CHƯA ĐƯỢC CẤP PHÁT\n"
                    "- Gói kết nối này KHÔNG tạo file `.conf` giả khi gateway `wg-easy` chưa xác minh hoặc đang ngoại tuyến.\n"
                    "- Nếu đang ở mạng LAN phòng Lab, kết nối thẳng bằng SSH: `ssh labops-" + username + "`.\n"
                    "- Để nhận WireGuard `.conf` hợp lệ, Admin cần kiểm tra `wg-easy` và cấp thiết bị qua Dashboard.\n"
                )
            zf.writestr(
                f"{root}/wireguard/SETUP.md",
                "# CẤU HÌNH WIREGUARD VPN\n"
                "- **Nếu đang ở cùng mạng LAN phòng Lab**: KHÔNG cần bật WireGuard (hãy bấm Deactivate nếu đang bật tunnel mà chưa có `Latest handshake` / `Data received = 0 B`).\n"
                "- **Nếu kết nối từ xa qua Internet**:\n"
                "  1. Cài đặt WireGuard từ https://www.wireguard.com/install/\n"
                "  2. Mở WireGuard -> Add Tunnel -> Import tunnel(s) from file -> Chọn file `labops.conf`.\n"
                "  3. Bấm Activate để kết nối và kiểm tra phải thấy dòng `Latest handshake`.\n"
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
        storage_root: Optional[str] = None,
        extra_mounts: Optional[List[str]] = None,
        authorize_ssh_key: bool = False,
    ) -> Dict[str, Any]:
        """
        Tạo thư mục workspace riêng cho thành viên (0700), thư mục con work/, checkpoints/, datasets/ (0700),
        .ssh/ (0700) và .ssh/authorized_keys (0600).
        Mặc định authorize_ssh_key=False giữ authorized_keys RỖNG cho tới khi vượt qua authorization gate sau activation.
        """
        for m in extra_mounts or []:
            norm = os.path.normpath(m.split(":")[0])
            if any(norm == p or norm.startswith(p + "/") for p in FORBIDDEN_MOUNT_PREFIXES):
                raise WorkspaceSecurityError("MOUNT_PATH_FORBIDDEN", f"Đường dẫn mount '{norm}' bị cấm.", 403)

        ws_dir = self.member_workspace_dir(host_id, username, storage_root=storage_root)
        work_dir = os.path.join(ws_dir, "work")
        checkpoints_dir = os.path.join(ws_dir, "checkpoints")
        datasets_dir = os.path.join(ws_dir, "datasets")
        ssh_dir = os.path.join(ws_dir, ".ssh")
        auth_keys_path = os.path.join(ssh_dir, "authorized_keys")
        pending_key_path = os.path.join(ssh_dir, "registered_key.pub")
        meta_path = os.path.join(ws_dir, ".workspace_meta.json")

        for _d in (ws_dir, work_dir, checkpoints_dir, datasets_dir, ssh_dir):
            os.makedirs(_d, mode=0o700, exist_ok=True)
            try:
                os.chmod(_d, 0o700)
            except OSError:
                pass

        clean_key = public_key.strip()
        with open(pending_key_path, "w", encoding="utf-8") as pkf:
            pkf.write(f"{clean_key}\n")
        try:
            os.chmod(pending_key_path, 0o600)
        except OSError:
            pass

        with open(auth_keys_path, "w", encoding="utf-8") as akf:
            akf.write(f"{clean_key}\n" if (authorize_ssh_key and clean_key) else "")
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
            "authorized_keys_populated": bool(authorize_ssh_key and clean_key),
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

    @staticmethod
    def write_authorized_keys_state(ws_dir: str, keys: Optional[List[str]]) -> bool:
        """Đồng bộ hoặc xóa trắng tệp authorized_keys cục bộ (dùng cho test canary loopback hoặc trạng thái thu hồi)."""
        ssh_dir = os.path.join(ws_dir, ".ssh")
        if not os.path.isdir(ssh_dir):
            return False
        auth_keys_path = os.path.join(ssh_dir, "authorized_keys")
        clean = [str(k).strip() for k in (keys or []) if str(k).strip()]
        payload = ("\n".join(clean) + "\n") if clean else ""
        with open(auth_keys_path, "w", encoding="utf-8") as akf:
            akf.write(payload)
        try:
            os.chmod(auth_keys_path, 0o600)
        except OSError:
            pass
        return True

    @staticmethod
    def _inspect_mount_quota_state(path: str) -> Dict[str, Any]:
        """Kiểm tra mount point thực tế và mount options (prjquota/pquota/project) trong /proc/mounts."""
        abs_path = os.path.abspath(path)
        best_mount = "/"
        best_fs = SshAccessManager._detect_filesystem_type(abs_path)
        best_opts: List[str] = []
        best_dev = ""
        if os.path.exists("/proc/mounts"):
            try:
                best_len = -1
                with open("/proc/mounts", "r", encoding="utf-8") as mf:
                    for line in mf:
                        parts = line.split()
                        if len(parts) < 4:
                            continue
                        dev, mnt, fstype, opts_str = parts[0], parts[1], parts[2].lower(), parts[3]
                        if mnt == "/" or abs_path == mnt or abs_path.startswith(mnt.rstrip("/") + "/"):
                            if len(mnt) > best_len:
                                best_len = len(mnt)
                                best_mount = mnt
                                best_fs = fstype
                                best_opts = [o.strip() for o in opts_str.split(",") if o.strip()]
                                best_dev = dev
            except Exception:
                pass
        has_quota_opt = best_fs in ("zfs", "btrfs") or (
            best_fs in ("ext4", "xfs") and any(o in ("prjquota", "pquota", "project") for o in best_opts)
        )
        return {
            "mount_point": best_mount,
            "device": best_dev,
            "fs_type": best_fs,
            "options": best_opts,
            "quota_supported": has_quota_opt,
        }

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
        workspace_dir: Optional[str] = None,
        agent_verification: Optional[Dict[str, Any]] = None,
        cpu_policy: str = "SHARED_WEIGHTED",
        cpu_weight: int = 100,
        expected_uid: Optional[int] = None,
        require_authorized_key_present: bool = False,
        storage_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Kiểm tra thực tế tại runtime (tuyệt đối không giả lập PASSED):
        1. Host & Node Agent có online không?
        2. Thư mục workspace (0700) & .ssh/authorized_keys (0600) có cô lập đúng không?
        3. Không Docker socket, non-root UID >= 20000, CapDrop=ALL, NoNewPrivs, không privileged/host namespaces.
        4. Giới hạn CPU (SHARED_WEIGHTED CpuShares hoặc HARD_LIMIT NanoCpus) + RAM + PidsLimit trên cgroup.
        5. Storage hard quota được xác minh từ kernel/filesystem (/proc/mounts + quota tools).
        6. Cổng SSH được ánh xạ và phục vụ bởi chính container cô lập (cấm SSH daemon chạy trực tiếp trên host OS).
        Chỉ trả về overall_status = "READY" khi TẤT CẢ kiểm tra bắt buộc đều VERIFIED.
        """
        ws_dir = workspace_dir or self.member_workspace_dir(host_id, username)
        quota_target_dir = storage_dir or ws_dir
        ssh_dir = os.path.join(ws_dir, ".ssh")
        auth_keys_path = os.path.join(ssh_dir, "authorized_keys")
        now_ts = _now_iso()

        checks_detail: Dict[str, Dict[str, Any]] = {}
        checks: Dict[str, Any] = {
            "verified_at": now_ts,
            "host_online": bool(host_online),
            "canary_runtime": bool(enforce_canary_runtime),
            "docker_running": "NOT_VERIFIED",
            "directory_isolation": "NOT_VERIFIED",
            "ssh_key_installed": "NOT_VERIFIED",
            "privilege_isolation": "NOT_VERIFIED",
            "cpu_ram_limits": "NOT_VERIFIED",
            "storage_quota": "NOT_VERIFIED",
            "gpu_isolation": "NOT_APPLICABLE" if not gpu_permissions else "NOT_VERIFIED",
            "ssh_runtime": "NOT_VERIFIED",
            "checks_detail": checks_detail,
            "reasons": [],
        }

        def _set_detail(
            key: str,
            status_val: str,
            observed: Any,
            expected: Any,
            source: str,
            *,
            error_code: str = "",
            reason: str = "",
        ) -> None:
            checks_detail[key] = {
                "status": status_val,
                "observed_value": observed,
                "expected_value": expected,
                "source": source,
                "verified_at": now_ts,
                "error_code": error_code,
                "reason": reason,
            }

        if not host_online:
            checks["overall_status"] = "NOT_VERIFIED"
            reason_offline = f"Host '{host_id}' đang OFFLINE hoặc mất kết nối với Node Agent."
            checks["reasons"].append(reason_offline)
            _set_detail("host_online", "FAILED", False, True, "node_agent", error_code="AGENT_UNAVAILABLE", reason=reason_offline)
            return checks

        # 1. Kiểm tra cách ly thư mục (0700) & authorized_keys (0600)
        if os.path.isdir(ws_dir) and os.path.isdir(ssh_dir) and os.path.isfile(auth_keys_path):
            ws_mode = stat.S_IMODE(os.stat(ws_dir).st_mode)
            ssh_mode = stat.S_IMODE(os.stat(ssh_dir).st_mode)
            ak_mode = stat.S_IMODE(os.stat(auth_keys_path).st_mode)
            if (ws_mode & 0o077) == 0 and (ssh_mode & 0o077) == 0 and (ak_mode & 0o177) == 0:
                checks["directory_isolation"] = "VERIFIED"
                _set_detail("directory_isolation", "VERIFIED", oct(ws_mode), "0o700", "filesystem_stat")
            else:
                checks["directory_isolation"] = "FAILED"
                dir_err = f"Quyền thư mục không an toàn (ws={oct(ws_mode)}, ssh={oct(ssh_mode)}, ak={oct(ak_mode)})."
                checks["reasons"].append(dir_err)
                _set_detail(
                    "directory_isolation",
                    "FAILED",
                    {"ws": oct(ws_mode), "ssh": oct(ssh_mode), "ak": oct(ak_mode)},
                    "0o700/0o700/0o600",
                    "filesystem_stat",
                    error_code="UNSAFE_DIRECTORY_PERMISSIONS",
                    reason=dir_err,
                )

            with open(auth_keys_path, "r", encoding="utf-8") as akf:
                ak_content = [line.strip() for line in akf.read().splitlines() if line.strip() and not line.startswith("#")]
            if expected_public_key.strip() and expected_public_key.strip() in ak_content:
                checks["ssh_key_installed"] = "VERIFIED"
                _set_detail("ssh_key_installed", "VERIFIED", len(ak_content), 1, "authorized_keys")
            elif not require_authorized_key_present and len(ak_content) == 0:
                checks["ssh_key_installed"] = "REGISTERED_PENDING_ACTIVATION"
                _set_detail(
                    "ssh_key_installed",
                    "REGISTERED_PENDING_ACTIVATION",
                    0,
                    "empty_until_activation",
                    "authorized_keys",
                    reason="Khóa SSH đã đăng ký nhưng giữ authorized_keys rỗng cho tới khi hoàn tất kích hoạt lần đầu.",
                )
            else:
                checks["ssh_key_installed"] = "FAILED"
                ak_err = "Public key chưa khớp với authorized_keys của workspace."
                checks["reasons"].append(ak_err)
                _set_detail(
                    "ssh_key_installed",
                    "FAILED",
                    len(ak_content),
                    expected_public_key[:36],
                    "authorized_keys",
                    error_code="SSH_KEY_MISMATCH",
                    reason=ak_err,
                )
        else:
            checks["directory_isolation"] = "FAILED"
            miss_err = "Thư mục workspace hoặc authorized_keys chưa tồn tại."
            checks["reasons"].append(miss_err)
            _set_detail("directory_isolation", "FAILED", "missing", ws_dir, "filesystem_stat", error_code="WORKSPACE_DIR_MISSING", reason=miss_err)

        # Kiểm tra symlink hoặc docker.sock bên trong workspace
        docker_sock_inside = os.path.exists(os.path.join(ws_dir, "docker.sock"))
        symlink_violation = os.path.islink(ws_dir)
        if docker_sock_inside or symlink_violation:
            checks["privilege_isolation"] = "FAILED"
            sock_err = "Phát hiện symlink hoặc Docker socket bên trong workspace."
            checks["reasons"].append(sock_err)
            _set_detail(
                "privilege_isolation",
                "FAILED",
                "docker_sock_or_symlink",
                "ISOLATED",
                "filesystem_stat",
                error_code="PRIVILEGE_ISOLATION_VIOLATION",
                reason=sock_err,
            )

        # 2. Kiểm tra GPU UUID nếu có yêu cầu cấp GPU
        if gpu_permissions:
            if known_gpu_ids is not None and all(g in known_gpu_ids for g in gpu_permissions):
                checks["gpu_isolation"] = "VERIFIED"
                _set_detail("gpu_isolation", "VERIFIED", gpu_permissions, gpu_permissions, "gpu_registry")
            else:
                checks["gpu_isolation"] = "NOT_VERIFIED"
                gpu_err = "GPU được chọn chưa xác minh được trên host thực tế."
                checks["reasons"].append(gpu_err)
                _set_detail("gpu_isolation", "NOT_VERIFIED", gpu_permissions, "HOST_GPU_VERIFIED", "gpu_registry", error_code="GPU_NOT_VERIFIED", reason=gpu_err)

        # 3. Kiểm tra runtime cô lập Docker (ưu tiên báo cáo VERIFY_DOCKER_WORKSPACE từ Go Node Agent)
        agent_checks = (agent_verification or {}).get("checks") or {}
        if agent_checks:
            dr = agent_checks.get("docker_running") or {}
            pr = agent_checks.get("privilege_isolation") or {}
            re_chk = agent_checks.get("resource_enforcement") or {}
            sr = agent_checks.get("ssh_runtime") or {}
            sq = agent_checks.get("storage_quota") or {}

            checks["docker_running"] = dr.get("status") or "NOT_VERIFIED"
            _set_detail(
                "docker_running",
                checks["docker_running"],
                dr.get("observed_value"),
                dr.get("expected_value", "running"),
                dr.get("source", "go_node_agent"),
                error_code=dr.get("error_code", ""),
                reason=dr.get("reason", ""),
            )
            if dr.get("reason") and checks["docker_running"] != "VERIFIED":
                checks["reasons"].append(dr["reason"])

            if checks["privilege_isolation"] != "FAILED":
                checks["privilege_isolation"] = pr.get("status") or "NOT_VERIFIED"
                _set_detail(
                    "privilege_isolation",
                    checks["privilege_isolation"],
                    pr.get("observed_value"),
                    pr.get("expected_value"),
                    pr.get("source", "go_node_agent"),
                    error_code=pr.get("error_code", ""),
                    reason=pr.get("reason", ""),
                )
                if pr.get("reason") and checks["privilege_isolation"] != "VERIFIED":
                    checks["reasons"].append(pr["reason"])

            checks["cpu_ram_limits"] = re_chk.get("status") or "NOT_VERIFIED"
            _set_detail(
                "cpu_ram_limits",
                checks["cpu_ram_limits"],
                re_chk.get("observed_value"),
                re_chk.get("expected_value"),
                re_chk.get("source", "go_node_agent"),
                error_code=re_chk.get("error_code", ""),
                reason=re_chk.get("reason", ""),
            )
            if re_chk.get("reason") and checks["cpu_ram_limits"] != "VERIFIED":
                checks["reasons"].append(re_chk["reason"])

            checks["ssh_runtime"] = sr.get("status") or "NOT_VERIFIED"
            _set_detail(
                "ssh_runtime",
                checks["ssh_runtime"],
                sr.get("observed_value"),
                sr.get("expected_value"),
                sr.get("source", "go_node_agent"),
                error_code=sr.get("error_code", ""),
                reason=sr.get("reason", ""),
            )
            if sr.get("reason") and checks["ssh_runtime"] != "VERIFIED":
                checks["reasons"].append(sr["reason"])

            if sq.get("status") == "VERIFIED" or (agent_verification or {}).get("storage_quota_verified"):
                checks["storage_quota"] = "VERIFIED"
                _set_detail("storage_quota", "VERIFIED", sq.get("observed_value") or storage_quota_gb, storage_quota_gb, sq.get("source", "go_node_agent"))
            else:
                mount_info = self._inspect_mount_quota_state(quota_target_dir)
                checks["storage_quota"] = "NOT_ENFORCEABLE"
                q_reason = sq.get("reason") or (
                    f"Filesystem '{mount_info['fs_type']}' tại '{mount_info['mount_point']}' "
                    f"(options: {','.join(mount_info['options']) or 'none'}) chưa bật hard project quota."
                )
                checks["reasons"].append(q_reason)
                _set_detail(
                    "storage_quota",
                    "NOT_ENFORCEABLE",
                    mount_info,
                    "prjquota/zfs/btrfs",
                    "proc_mounts",
                    error_code="STORAGE_QUOTA_NOT_ENFORCEABLE",
                    reason=q_reason,
                )
        elif enforce_canary_runtime:
            is_test_ctx = "PYTEST_CURRENT_TEST" in os.environ or os.getenv("LABOPS_ALLOW_TEST_CANARY_SSHD") == "1"
            if not is_test_ctx:
                raise PermissionError("CANARY_RUNTIME_FORBIDDEN: Chỉ cho phép canary runtime trong test harness.")
            rlimit_ok = self._verify_rlimit_enforcement_canary(ws_dir, cpu_limit_cores, ram_limit_gb, storage_quota_gb)
            if rlimit_ok:
                checks["docker_running"] = "VERIFIED"
                checks["cpu_ram_limits"] = "VERIFIED"
                checks["storage_quota"] = "VERIFIED"
                if checks["privilege_isolation"] != "FAILED":
                    checks["privilege_isolation"] = "VERIFIED"
                _set_detail("docker_running", "VERIFIED", "canary_test_harness", "running", "canary_test_harness")
                _set_detail("privilege_isolation", checks["privilege_isolation"], "canary_non_root", "isolated", "canary_test_harness")
                _set_detail("cpu_ram_limits", "VERIFIED", f"{cpu_limit_cores}c/{ram_limit_gb}g", "rlimit_ok", "canary_prlimit")
                _set_detail("storage_quota", "VERIFIED", f"{storage_quota_gb}g", "rlimit_fsize", "canary_prlimit")
            else:
                checks["cpu_ram_limits"] = "NOT_ENFORCEABLE"
                checks["storage_quota"] = "NOT_ENFORCEABLE"
                checks["reasons"].append("Không thể áp dụng giới hạn RLIMIT cho canary runtime.")
            if self._is_tcp_port_listening("127.0.0.1", ssh_port):
                checks["ssh_runtime"] = "VERIFIED"
                _set_detail("ssh_runtime", "VERIFIED", ssh_port, ssh_port, "canary_tcp_probe")
            else:
                checks["ssh_runtime"] = "NOT_VERIFIED"
                _set_detail("ssh_runtime", "NOT_VERIFIED", 0, ssh_port, "canary_tcp_probe", error_code="SSH_PORT_NOT_LISTENING")
        else:
            # Chế độ production: kiểm tra Docker container thật (không bao giờ coi thư mục rỗng là cách ly đặc quyền)
            container_verified = False
            docker_bin = shutil.which("docker")
            if docker_bin and host_id == self.local_host_id:
                try:
                    insp = subprocess.run([docker_bin, "inspect", workspace_name], capture_output=True, text=True, timeout=3)
                    if insp.returncode == 0:
                        arr = json.loads(insp.stdout)
                        if isinstance(arr, list) and arr:
                            raw = arr[0]
                            st = raw.get("State") or {}
                            cfg = raw.get("Config") or {}
                            hc = raw.get("HostConfig") or {}
                            running = bool(st.get("Running"))
                            user_spec = str(cfg.get("User") or "")
                            uid_ok = False
                            if ":" in user_spec:
                                try:
                                    uid_val = int(user_spec.split(":", 1)[0])
                                    uid_ok = 20000 <= uid_val <= 59999 and (expected_uid is None or uid_val == expected_uid)
                                except ValueError:
                                    uid_ok = False
                            cap_drop = [c.upper() for c in (hc.get("CapDrop") or [])]
                            sec_opts = hc.get("SecurityOpt") or []
                            no_new_privs = any("no-new-privileges" in str(s) for s in sec_opts)
                            mounts = raw.get("Mounts") or []
                            bad_mount = any(
                                str(m.get("Source") or "").startswith(("/var/run/docker.sock", "/run/docker.sock", "/etc", "/root"))
                                or str(m.get("Source") or "") == "/"
                                for m in mounts
                            )
                            priv_ok = (
                                running
                                and uid_ok
                                and not hc.get("Privileged")
                                and "ALL" in cap_drop
                                and no_new_privs
                                and hc.get("IpcMode") != "host"
                                and hc.get("NetworkMode") != "host"
                                and hc.get("PidMode") != "host"
                                and not bad_mount
                            )
                            is_shared = str(cpu_policy).upper() in ("SHARED", "SHARED_WEIGHTED")
                            cpu_ok = int(hc.get("CpuShares") or 0) > 0 if is_shared else int(hc.get("NanoCpus") or 0) > 0
                            mem_ok = int(hc.get("Memory") or 0) > 0
                            pids_ok = int(hc.get("PidsLimit") or 0) > 0
                            port_bindings = (hc.get("PortBindings") or {}).get("2222/tcp") or []
                            port_bound = any(int(b.get("HostPort") or 0) == int(ssh_port) for b in port_bindings)

                            checks["docker_running"] = "VERIFIED" if running else "FAILED"
                            _set_detail("docker_running", checks["docker_running"], st.get("Status"), "running", "docker_inspect")
                            if checks["privilege_isolation"] != "FAILED":
                                checks["privilege_isolation"] = "VERIFIED" if priv_ok else "FAILED"
                                _set_detail("privilege_isolation", checks["privilege_isolation"], user_spec, "uid>=20000,CapDrop=ALL,no-new-privileges", "docker_inspect")
                            checks["cpu_ram_limits"] = "VERIFIED" if (cpu_ok and mem_ok and pids_ok) else "FAILED"
                            _set_detail("cpu_ram_limits", checks["cpu_ram_limits"], {"policy": cpu_policy, "mem": hc.get("Memory"), "pids": hc.get("PidsLimit")}, "enforced", "docker_inspect")
                            checks["ssh_runtime"] = "VERIFIED" if (port_bound and self._is_tcp_port_listening("127.0.0.1", ssh_port)) else "NOT_VERIFIED"
                            _set_detail("ssh_runtime", checks["ssh_runtime"], port_bound, ssh_port, "docker_inspect")
                            container_verified = running and priv_ok
                except Exception:
                    pass

            if not container_verified:
                if checks["docker_running"] == "NOT_VERIFIED":
                    c_reason = f"Container workspace '{workspace_name}' chưa chạy hoặc chưa qua Go Node Agent verify."
                    checks["reasons"].append(c_reason)
                    _set_detail("docker_running", "NOT_VERIFIED", "missing", "running", "docker_inspect", error_code="CONTAINER_NOT_RUNNING", reason=c_reason)
                if checks["privilege_isolation"] == "NOT_VERIFIED":
                    p_reason = "Chưa xác minh cô lập UID >= 20000 / CapDrop=ALL / no-sudo trên container Docker thực tế."
                    checks["reasons"].append(p_reason)
                    _set_detail("privilege_isolation", "NOT_VERIFIED", "unverified", "CONTAINER_UID_20000_PLUS", "docker_inspect", error_code="SSH_RUNTIME_NOT_ISOLATED", reason=p_reason)
                if checks["cpu_ram_limits"] == "NOT_VERIFIED":
                    r_reason = f"Giới hạn {cpu_policy} / {ram_limit_gb} GiB RAM / PIDs chưa xác minh được trên cgroup container."
                    checks["reasons"].append(r_reason)
                    _set_detail("cpu_ram_limits", "NOT_VERIFIED", "unverified", f"{cpu_policy}/{ram_limit_gb}GiB", "docker_inspect", error_code="RESOURCE_LIMITS_NOT_VERIFIED", reason=r_reason)
                # Nếu cổng đang mở trên host nhưng KHÔNG thuộc container đã verify -> đánh dấu SSH_RUNTIME_NOT_ISOLATED
                if checks["ssh_runtime"] != "VERIFIED":
                    if ssh_port > 0 and self._is_tcp_port_listening("127.0.0.1", ssh_port):
                        checks["ssh_runtime"] = "SSH_RUNTIME_NOT_ISOLATED"
                        iso_reason = f"Cổng SSH {ssh_port} đang mở trên host nhưng KHÔNG thuộc Docker container cô lập của thành viên!"
                        checks["reasons"].append(iso_reason)
                        _set_detail("ssh_runtime", "SSH_RUNTIME_NOT_ISOLATED", f"host_port_{ssh_port}", "container_port_2222", "tcp_probe", error_code="SSH_RUNTIME_NOT_ISOLATED", reason=iso_reason)
                    else:
                        checks["ssh_runtime"] = "NOT_VERIFIED"
                        _set_detail("ssh_runtime", "NOT_VERIFIED", "closed", f"container:{ssh_port}", "tcp_probe", error_code="SSH_ENDPOINT_NOT_VERIFIED")

            # Storage quota trung thực trên host (không dùng RLIMIT_FSIZE giả mạo quota đĩa)
            mount_info = self._inspect_mount_quota_state(quota_target_dir)
            checks["storage_quota"] = "NOT_ENFORCEABLE"
            q_err = (
                f"Filesystem '{mount_info['fs_type']}' tại '{mount_info['mount_point']}' "
                f"(options: {','.join(mount_info['options']) or 'none'}) chưa xác minh hard quota bởi kernel."
                if not mount_info["quota_supported"]
                else f"Filesystem '{mount_info['fs_type']}' hỗ trợ quota nhưng chưa xác minh áp đặt project quota cho '{ws_dir}' qua Go Node Agent."
            )
            checks["reasons"].append(q_err)
            _set_detail("storage_quota", "NOT_ENFORCEABLE", mount_info, "kernel_project_quota", "proc_mounts", error_code="STORAGE_QUOTA_NOT_ENFORCEABLE", reason=q_err)

        mandatory_ok = (
            checks["host_online"]
            and checks["docker_running"] == "VERIFIED"
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
        elif any(
            checks.get(k) in ("FAILED", "SSH_RUNTIME_NOT_ISOLATED")
            for k in ("docker_running", "directory_isolation", "ssh_key_installed", "privilege_isolation", "cpu_ram_limits", "ssh_runtime", "gpu_isolation")
        ):
            checks["overall_status"] = "SSH_RUNTIME_NOT_ISOLATED" if checks["ssh_runtime"] == "SSH_RUNTIME_NOT_ISOLATED" else "FAILED"
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
        invalidate_previous: bool = False,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Cấp mật khẩu tạm mới cho thành viên.
        - Chỉ lưu PBKDF2/scrypt hash vào v2_ssh_accounts.json.
        - Đặt trạng thái password_status = CHANGE_REQUIRED.
        """
        temp_password = _generate_temp_password()
        now_dt = datetime.now(timezone.utc)
        expires_dt = now_dt + timedelta(seconds=ttl_seconds)
        expires_iso = expires_dt.isoformat()
        pw_hash = _hash_password(temp_password)

        with self._locked_accounts() as state:
            prev = state["accounts"].get(username)
            prev_hashes: List[str] = []
            if isinstance(prev, dict):
                prev_mid = str(prev.get("member_id") or "").strip()
                if prev_mid and prev_mid != member_id:
                    state.setdefault("archived_accounts", {})[prev_mid] = dict(prev)
                elif not invalidate_previous and not prev.get("temp_password_used"):
                    for h in [prev.get("temp_password_hash")] + list(prev.get("prev_temp_password_hashes") or []):
                        h_s = str(h or "").strip()
                        if h_s and h_s != pw_hash and h_s not in prev_hashes:
                            prev_hashes.append(h_s)
                    prev_hashes = prev_hashes[:5]
            state["accounts"][username] = {
                "member_id": member_id,
                "username": username,
                "activation_status": "PENDING_ACTIVATION",
                "password_status": "CHANGE_REQUIRED",
                "must_change_password": True,
                "temp_password_hash": pw_hash,
                "prev_temp_password_hashes": prev_hashes,
                "active_password_hash": None,
                "temp_password_issued_at": now_dt.isoformat(),
                "temp_password_expires_at": expires_iso,
                "temp_password_used": False,
                "password_changed_at": None,
                "activated_at": None,
                "revoked_at": None,
                "failed_login_attempts": 0,
                "locked_until": None,
                "issued_by": actor,
            }

        pub_meta = {
            "member_id": member_id,
            "username": username,
            "activation_status": "PENDING_ACTIVATION",
            "password_status": "CHANGE_REQUIRED",
            "must_change_password": True,
            "temp_password_issued_at": now_dt.isoformat(),
            "temp_password_expires_at": expires_iso,
            "temp_password_used": False,
        }
        return temp_password, pub_meta

    def verify_workspace_password_login(
        self, username: str, password: str, *, expected_member_id: Optional[str] = None
    ) -> Dict[str, Any]:
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
            if expected_member_id and acc.get("member_id") and acc.get("member_id") != expected_member_id:
                raise LookupError(f"Tài khoản '{username}' (ID: {expected_member_id}) chưa được cấp mật khẩu tạm")

            if acc.get("password_status") == "REVOKED" or acc.get("activation_status") == "REVOKED":
                raise PermissionError("ACCOUNT_REVOKED: Quyền truy cập tài khoản đã bị thu hồi")

            now_dt = datetime.now(timezone.utc)
            locked_until_str = acc.get("locked_until")
            if locked_until_str:
                try:
                    locked_dt = datetime.fromisoformat(str(locked_until_str).replace("Z", "+00:00"))
                    if locked_dt.tzinfo is None:
                        locked_dt = locked_dt.replace(tzinfo=timezone.utc)
                    if now_dt < locked_dt:
                        raise PermissionError("ACCOUNT_LOCKED: Tài khoản tạm khóa 15 phút do đăng nhập sai quá 5 lần")
                except PermissionError:
                    raise
                except Exception:
                    pass

            def _record_login_failure() -> None:
                attempts = int(acc.get("failed_login_attempts", 0)) + 1
                acc["failed_login_attempts"] = attempts
                if attempts >= 5:
                    acc["locked_until"] = (now_dt + timedelta(minutes=15)).isoformat()

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
                        acc["activation_status"] = "TEMP_PASSWORD_EXPIRED"
                        raise PermissionError(
                            "TEMP_PASSWORD_EXPIRED: Mật khẩu tạm đã hết hạn; quản trị viên phải cấp mật khẩu tạm mới"
                        )
                if not _verify_password(password, acc.get("temp_password_hash")):
                    _record_login_failure()
                    raise PermissionError("INVALID_CREDENTIALS: Mật khẩu tạm không chính xác")
                acc["failed_login_attempts"] = 0
                acc["locked_until"] = None
                return {
                    "authenticated": True,
                    "username": username,
                    "activation_status": acc.get("activation_status") or "PENDING_ACTIVATION",
                    "password_status": "CHANGE_REQUIRED",
                    "must_change_password": True,
                    "workspace_access_granted": False,
                    "message": "Đăng nhập lần đầu thành công bằng mật khẩu tạm. Bắt buộc đổi mật khẩu mới trước khi sử dụng workspace.",
                }

            # Trường hợp đã đổi mật khẩu lần đầu (ACTIVE)
            if _verify_password(password, acc.get("temp_password_hash")):
                _record_login_failure()
                raise PermissionError("TEMP_PASSWORD_REUSE_FORBIDDEN: Mật khẩu tạm đã hết hiệu lực và không thể dùng lại")
            if not _verify_password(password, acc.get("active_password_hash")):
                _record_login_failure()
                raise PermissionError("INVALID_CREDENTIALS: Mật khẩu không chính xác")

            acc["failed_login_attempts"] = 0
            acc["locked_until"] = None
            acc["last_successful_login"] = _now_iso()
            activation_state = acc.get("activation_status") or "ACTIVATED"
            return {
                "authenticated": True,
                "username": username,
                "activation_status": activation_state,
                "password_status": "ACTIVE",
                "must_change_password": False,
                "workspace_access_granted": activation_state == "ACTIVATED",
                "message": "Xác thực thành công; được phép truy cập workspace.",
            }

    def change_first_login_password(
        self,
        *,
        username: str,
        temporary_password: str,
        new_password: str,
        expected_member_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Thực hiện đổi mật khẩu bắt buộc ở lần đăng nhập đầu tiên.
        Sau khi đổi thành công:
        - Xóa hoàn toàn temp_password_hash.
        - Đánh dấu temp_password_used = True, must_change_password = False, password_status = ACTIVE, activation_status = ACTIVATED.
        """
        _validate_new_password_strength(new_password)
        if temporary_password == new_password:
            raise ValueError("NEW_PASSWORD_MUST_DIFFER: Mật khẩu mới không được trùng với mật khẩu tạm")

        with self._locked_accounts() as state:
            acc = state["accounts"].get(username)
            if not acc:
                raise LookupError(f"Không tìm thấy tài khoản '{username}'")
            if expected_member_id and acc.get("member_id") and acc.get("member_id") != expected_member_id:
                raise LookupError(f"Không tìm thấy thông tin kích hoạt cho '{username}' (ID: {expected_member_id})")
            if acc.get("password_status") == "REVOKED" or acc.get("activation_status") == "REVOKED":
                raise PermissionError("ACCOUNT_REVOKED: Quyền truy cập tài khoản đã bị thu hồi")

            now_dt = datetime.now(timezone.utc)
            locked_until_str = acc.get("locked_until")
            if locked_until_str:
                try:
                    locked_dt = datetime.fromisoformat(str(locked_until_str).replace("Z", "+00:00"))
                    if locked_dt.tzinfo is None:
                        locked_dt = locked_dt.replace(tzinfo=timezone.utc)
                    if now_dt < locked_dt:
                        raise PermissionError("ACCOUNT_LOCKED: Tài khoản tạm khóa 15 phút do nhập sai mật khẩu quá 5 lần")
                except PermissionError:
                    raise
                except Exception:
                    pass

            if acc.get("temp_password_used") or acc.get("password_status") == "ACTIVE":
                raise PermissionError("TEMP_PASSWORD_ALREADY_USED: Mật khẩu tạm đã hết hiệu lực và không thể dùng lại")

            exp_str = acc.get("temp_password_expires_at")
            if exp_str:
                exp_dt = datetime.fromisoformat(exp_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if now_dt > exp_dt:
                    acc["password_status"] = "TEMP_PASSWORD_EXPIRED"
                    acc["activation_status"] = "TEMP_PASSWORD_EXPIRED"
                    raise PermissionError(
                        "TEMP_PASSWORD_EXPIRED: Mật khẩu tạm đã hết hạn; vui lòng liên hệ Admin để cấp mật khẩu tạm mới"
                    )

            if not _verify_password(temporary_password, acc.get("temp_password_hash")):
                attempts = int(acc.get("failed_login_attempts", 0)) + 1
                acc["failed_login_attempts"] = attempts
                if attempts >= 5:
                    acc["locked_until"] = (now_dt + timedelta(minutes=15)).isoformat()
                raise PermissionError("INVALID_TEMP_PASSWORD: Mật khẩu tạm không chính xác")

            changed_iso = _now_iso()
            acc["temp_password_hash"] = None
            acc["active_password_hash"] = _hash_password(new_password)
            acc["temp_password_used"] = True
            acc["must_change_password"] = False
            acc["password_status"] = "ACTIVE"
            acc["activation_status"] = "ACTIVATED"
            acc["failed_login_attempts"] = 0
            acc["locked_until"] = None
            acc["password_changed_at"] = changed_iso
            acc["activated_at"] = changed_iso
            acc["last_successful_login"] = changed_iso

            return {
                "username": username,
                "activation_status": "ACTIVATED",
                "password_status": "ACTIVE",
                "must_change_password": False,
                "temp_password_invalidated": True,
                "workspace_access_granted": True,
                "password_changed_at": changed_iso,
            }

    # ------------------------------------------------------------------
    # Canary Unprivileged OpenSSH Daemon (Kiểm thử thực tế trên máy thật)
    # ------------------------------------------------------------------
    def start_canary_sshd_for_workspace(
        self,
        host_id: str,
        username: str,
        port: int = 0,
        *,
        workspace_dir: Optional[str] = None,
        listen_address: str = "127.0.0.1",
        workspace_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Khởi chạy tiến trình /usr/sbin/sshd phục vụ RIÊNG kiểm thử tự động trên loopback (127.0.0.1).
        Bị chặn hoàn toàn trong production và khi có listen_address ngoài loopback.
        """
        if "PYTEST_CURRENT_TEST" not in os.environ and os.getenv("LABOPS_ALLOW_TEST_CANARY_SSHD") != "1":
            raise PermissionError(
                "HOST_SSHD_FALLBACK_FORBIDDEN_IN_PRODUCTION: Không được phép chạy canary sshd trên host trong production."
            )
        if listen_address not in ("127.0.0.1", "localhost"):
            raise PermissionError(
                f"CANARY_SSHD_LOOPBACK_ONLY: Canary sshd chỉ được lắng nghe trên 127.0.0.1 (nhận '{listen_address}')."
            )
        sshd_bin = "/usr/sbin/sshd"
        if not os.path.exists(sshd_bin):
            raise RuntimeError("SSHD_BINARY_MISSING: Không tìm thấy /usr/sbin/sshd")

        ws_dir = os.path.abspath(workspace_dir or self.member_workspace_dir(host_id, username))
        prod_ws_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "workspaces")
        )
        if ws_dir == prod_ws_root or ws_dir.startswith(prod_ws_root + os.sep):
            raise PermissionError(
                "CANARY_SSHD_FORBIDDEN_IN_PRODUCTION_DATADIR: Không được phép chạy canary sshd trên thư mục workspace production."
            )
        ssh_dir = os.path.join(ws_dir, ".ssh")
        auth_keys_path = os.path.join(ssh_dir, "authorized_keys")
        if not os.path.isfile(auth_keys_path):
            raise RuntimeError("Authorized keys của workspace chưa được khởi tạo")

        self.stop_canary_sshd_for_workspace(username, workspace_dir=ws_dir)

        if port <= 0:
            bind_host = "127.0.0.1" if listen_address in ("127.0.0.1", "localhost") else "0.0.0.0"
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((bind_host, 0))
                port = s.getsockname()[1]

        host_key_path = os.path.join(ssh_dir, "canary_host_ed25519_key")
        if not os.path.exists(host_key_path):
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", host_key_path],
                check=True,
                timeout=10,
            )
            os.chmod(host_key_path, 0o600)

        ws_label = (workspace_name or f"ws-{username}").strip() or f"ws-{username}"
        shell_script_path = os.path.join(ssh_dir, "workspace_shell.sh")
        q_ws_dir = shlex.quote(ws_dir)
        q_username = shlex.quote(username)
        q_ws_label = shlex.quote(ws_label)
        with open(shell_script_path, "w", encoding="utf-8") as sf:
            sf.write(
                "#!/usr/bin/env bash\n"
                f"export HOME={q_ws_dir}\n"
                f"export USER={q_username}\n"
                f"export LOGNAME={q_username}\n"
                f"export LABOPS_WORKSPACE={q_ws_label}\n"
                f"cd {q_ws_dir} || exit 1\n"
                "if [ -n \"${SSH_ORIGINAL_COMMAND:-}\" ]; then\n"
                "    exec /bin/bash -c \"$SSH_ORIGINAL_COMMAND\"\n"
                "else\n"
                "    echo \"==================================================================\"\n"
                f"    echo \"🚀 LabOps Control Center V2 — Workspace: {ws_label} (@{username})\"\n"
                f"    echo \"   Thư mục làm việc (HOME): {ws_dir}\"\n"
                "    echo \"==================================================================\"\n"
                f"    export PS1=\"{username}@{ws_label}:\\w\\$ \"\n"
                "    exec /bin/bash --noprofile --norc -i\n"
                "fi\n"
            )
        os.chmod(shell_script_path, 0o700)

        pid_file = os.path.join(ssh_dir, "canary_sshd.pid")
        cfg_path = os.path.join(ssh_dir, "canary_sshd_config")
        os_user = getpass.getuser()
        sftp_line = (
            "Subsystem sftp /usr/lib/openssh/sftp-server\n"
            if os.path.exists("/usr/lib/openssh/sftp-server")
            else ""
        )
        with open(cfg_path, "w", encoding="utf-8") as cf:
            cf.write(
                f"Port {port}\n"
                f"ListenAddress {listen_address}\n"
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
                f"{sftp_line}"
                f"ForceCommand {shlex.quote(shell_script_path)}\n"
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
            start_new_session=True,
            text=True,
        )
        self._canary_daemons[username] = proc

        check_host = "127.0.0.1" if listen_address in ("0.0.0.0", "127.0.0.1", "localhost") else listen_address
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self._is_tcp_port_listening(check_host, port):
                return {
                    "running": True,
                    "port": port,
                    "pid": proc.pid,
                    "os_user": os_user,
                    "listen_address": listen_address,
                    "workspace_dir": ws_dir,
                }
            time.sleep(0.05)

        self.stop_canary_sshd_for_workspace(username, workspace_dir=ws_dir)
        raise RuntimeError(f"Canary sshd không thể lắng nghe trên cổng {port}")

    def stop_canary_sshd_for_workspace(self, username: str, *, workspace_dir: Optional[str] = None) -> int:
        proc = self._canary_daemons.pop(username, None)
        stopped = 0
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            stopped += 1

        # Dọn thêm tiến trình sshd mồ côi theo canary_sshd.pid (ví dụ sau khi backend reload)
        candidate_dirs = []
        if workspace_dir:
            candidate_dirs.append(os.path.abspath(workspace_dir))
        candidate_dirs.append(os.path.join(self.workspaces_root, self.local_host_id, username))
        for ws_dir in dict.fromkeys(candidate_dirs):
            pid_file = os.path.join(ws_dir, ".ssh", "canary_sshd.pid")
            cfg_path = os.path.join(ws_dir, ".ssh", "canary_sshd_config")
            if not os.path.isfile(pid_file):
                continue
            try:
                with open(pid_file, "r", encoding="utf-8") as pf:
                    old_pid = int(pf.read().strip() or "0")
                if old_pid > 1 and (proc is None or old_pid != proc.pid):
                    cmdline_file = f"/proc/{old_pid}/cmdline"
                    if os.path.isfile(cmdline_file):
                        with open(cmdline_file, "rb") as cf:
                            cmdline = cf.read().decode("utf-8", errors="ignore")
                        if "sshd" in cmdline and cfg_path in cmdline:
                            os.kill(old_pid, signal.SIGTERM)
                            for _ in range(20):
                                if not os.path.exists(f"/proc/{old_pid}"):
                                    break
                                time.sleep(0.05)
                            stopped += 1
            except Exception:
                pass
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
        workspace_dir: Optional[str] = None,
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

        # Khóa tài khoản mật khẩu tạm đúng theo member_id
        with self._locked_accounts() as state:
            acct = state["accounts"].get(username)
            if isinstance(acct, dict) and acct.get("member_id") in (None, "", member_id):
                acct["password_status"] = "REVOKED"
                acct["activation_status"] = "REVOKED"
                acct["revoked_at"] = _now_iso()
                acct["temp_password_hash"] = None
                acct["active_password_hash"] = None

        # Chuyển các khóa trong SshKeyRegistry sang PENDING_REVOKE trước
        keys = self.ssh_key_registry.list_keys(username=username, member_id=member_id)
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

        ws_dir = workspace_dir or member.get("workspace_dir") or self.member_workspace_dir(host_id, username)
        fallback_ws_dir = self.member_workspace_dir(host_id, username)
        auth_keys_paths = [os.path.join(d, ".ssh", "authorized_keys") for d in dict.fromkeys((ws_dir, fallback_ws_dir))]
        try:
            for ak_path in auth_keys_paths:
                if os.path.exists(ak_path):
                    with open(ak_path, "w", encoding="utf-8") as akf:
                        akf.write("")
                    os.chmod(ak_path, 0o600)

            # Kiểm tra lại các tệp authorized_keys thực sự rỗng
            remaining = ""
            for ak_path in auth_keys_paths:
                if os.path.exists(ak_path):
                    with open(ak_path, "r", encoding="utf-8") as akf:
                        content = akf.read().strip()
                        if content:
                            remaining = content
                            break
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
                    if k.get("username") == username and k.get("member_id") in (None, "", member_id):
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
        stopped = self.stop_canary_sshd_for_workspace(username, workspace_dir=member.get("workspace_dir"))
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

    @staticmethod
    def _atomic_write_text(path: str, content: str, mode: int = 0o644) -> None:
        parent = os.path.dirname(path) or "."
        tmp_path = os.path.join(parent, f".{os.path.basename(path)}.tmp.{os.getpid()}.{uuid.uuid4().hex[:6]}")
        with open(tmp_path, "w", encoding="utf-8") as wf:
            wf.write(content)
            wf.flush()
            os.fsync(wf.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)

    def reconcile_container_activation_marker(
        self,
        username: str,
        *,
        expected_member_id: Optional[str] = None,
        member_storage_dir: Optional[str] = None,
    ) -> bool:
        """
        Đồng bộ trạng thái đã kích hoạt từ file /workspace/.labops_activation.json (nếu đọc được)
        vào v2_ssh_accounts.json để trạng thái Dashboard và gate luôn nhất quán.
        """
        if not member_storage_dir:
            return False
        marker_path = os.path.join(str(member_storage_dir), ".labops_activation.json")
        try:
            if not os.path.isfile(marker_path):
                return False
            with open(marker_path, "r", encoding="utf-8") as rf:
                marker = json.load(rf)
        except (OSError, ValueError):
            return False

        if not isinstance(marker, dict) or marker.get("activated") is not True:
            return False
        marker_hash = str(marker.get("activated_for_hash") or "").strip()
        new_pw_hash = str(marker.get("new_password_hash") or "").strip()
        if not marker_hash or not new_pw_hash.startswith(("scrypt$", "pbkdf2_sha256$", "$argon2id$")):
            return False

        with self._locked_accounts() as state:
            acc = state.get("accounts", {}).get(username)
            if not isinstance(acc, dict):
                return False
            if expected_member_id and acc.get("member_id") and acc.get("member_id") != expected_member_id:
                return False
            if acc.get("activation_status") == "ACTIVATED" and acc.get("password_status") == "ACTIVE":
                return False
            current_temp_hash = str(acc.get("temp_password_hash") or "").strip()
            candidate_hashes = [current_temp_hash] + [
                str(h or "").strip() for h in (acc.get("prev_temp_password_hashes") or [])
            ]
            if not any(h and hmac.compare_digest(h, marker_hash) for h in candidate_hashes):
                return False

            changed_iso = str(marker.get("activated_at") or _now_iso())
            acc["temp_password_hash"] = None
            acc["prev_temp_password_hashes"] = []
            acc["active_password_hash"] = new_pw_hash
            acc["temp_password_used"] = True
            acc["must_change_password"] = False
            acc["password_status"] = "ACTIVE"
            acc["activation_status"] = "ACTIVATED"
            acc["failed_login_attempts"] = 0
            acc["locked_until"] = None
            acc["password_changed_at"] = changed_iso
            acc["activated_at"] = changed_iso
            acc["last_successful_login"] = changed_iso
            return True

    def sync_container_first_login_gate(
        self,
        *,
        workspace_id: str,
        username: str,
        keys: List[str],
        member_id: Optional[str] = None,
        member_storage_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Cập nhật cặp script/policy trong <runtime_root>/<workspace_id>/ssh_ro (/etc/labops-ssh:ro)
        và gắn forced-command vào authorized_keys khi tài khoản còn must_change_password=True.
        """
        self.reconcile_container_activation_marker(
            username,
            expected_member_id=member_id,
            member_storage_dir=member_storage_dir,
        )

        runtime_root = os.getenv("LABOPS_WORKSPACE_RUNTIME_DIR") or "/var/lib/labops-agent/runtime"
        ssh_ro_dir = os.path.join(runtime_root, str(workspace_id), "ssh_ro")
        if not os.path.isdir(ssh_ro_dir):
            return {"synced": False, "reason": "ssh_ro_dir_missing", "ssh_ro_dir": ssh_ro_dir}

        acc: Dict[str, Any] = {}
        if os.path.exists(self.accounts_path):
            try:
                with open(self.accounts_path, "r", encoding="utf-8") as rf:
                    raw = json.load(rf).get("accounts", {}).get(username)
                    if isinstance(raw, dict):
                        if not member_id or not raw.get("member_id") or raw.get("member_id") == member_id:
                            acc = raw
            except (OSError, ValueError):
                acc = {}

        temp_hash = str(acc.get("temp_password_hash") or "").strip()
        must_change = bool(
            acc
            and temp_hash
            and (
                acc.get("must_change_password")
                or acc.get("activation_status") == "PENDING_ACTIVATION"
                or acc.get("password_status") == "CHANGE_REQUIRED"
            )
            and acc.get("activation_status") != "ACTIVATED"
            and acc.get("password_status") != "ACTIVE"
        )

        clean_keys = [
            str(k).strip()
            for k in keys
            if str(k or "").strip() and "\n" not in str(k) and "\r" not in str(k)
        ]

        gate_script_path = os.path.join(ssh_ro_dir, "first_login_gate.py")
        policy_path = os.path.join(ssh_ro_dir, "first_login_policy.json")
        ak_path = os.path.join(ssh_ro_dir, "authorized_keys")

        accepted_hashes: List[str] = []
        if must_change:
            accepted_hashes = [temp_hash] + [
                str(h).strip()
                for h in (acc.get("prev_temp_password_hashes") or [])
                if isinstance(h, str) and h.strip() and h.strip() != temp_hash
            ]

        policy_payload = {
            "username": username,
            "member_id": str(member_id or acc.get("member_id") or ""),
            "must_change_password": must_change,
            "temp_password_hash": temp_hash if must_change else None,
            "accepted_temp_password_hashes": accepted_hashes,
            "temp_password_expires_at": acc.get("temp_password_expires_at") if must_change else None,
            "activation_status": acc.get("activation_status") or "PENDING_ACTIVATION",
            "password_status": acc.get("password_status") or "CHANGE_REQUIRED",
            "updated_at": _now_iso(),
        }

        if clean_keys and must_change:
            ak_lines = [
                f'command="/usr/bin/python3 /etc/labops-ssh/first_login_gate.py" {pub}'
                for pub in clean_keys
            ]
        else:
            ak_lines = clean_keys

        ak_content = ("\n".join(ak_lines) + "\n") if ak_lines else ""

        self._atomic_write_text(gate_script_path, FIRST_LOGIN_GATE_SCRIPT, mode=0o755)
        self._atomic_write_text(
            policy_path,
            json.dumps(policy_payload, ensure_ascii=False, indent=2) + "\n",
            mode=0o644,
        )
        self._atomic_write_text(ak_path, ak_content, mode=0o644)

        return {
            "synced": True,
            "must_change_password": must_change,
            "ssh_ro_dir": ssh_ro_dir,
            "authorized_keys_count": len(ak_lines),
        }

