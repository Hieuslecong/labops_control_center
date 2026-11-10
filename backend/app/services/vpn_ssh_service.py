"""
WireGuard VPN & OpenSSH Per-Member Public Key Manager (LabOps V2 — Mục 12).

- Kiến trúc V2 dùng WireGuard + OpenSSH với khóa công khai riêng cho từng thành viên.
- KHÔNG triển khai NetBird, KHÔNG bắt buộc Teleport (Teleport chỉ là tùy chọn mở rộng tương lai).
- Bảo toàn tuyệt đối Legacy SSH (cổng 22 trên host) và cấu hình wg0 hiện có.
- Không bao giờ tự sinh hoặc trả về khóa riêng (private key) của người dùng từ máy chủ.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SSH_KEY_PATTERN = re.compile(
    r"^(ssh-ed25519|ssh-rsa|rsa-sha2-256|rsa-sha2-512|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521)\s+([A-Za-z0-9+/=]+)(?:\s+(.*))?$"
)
WG_PUBKEY_PATTERN = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_ssh_fingerprint(public_key: str) -> Dict[str, str]:
    m = SSH_KEY_PATTERN.match(public_key.strip())
    if not m:
        raise ValueError("Khóa công khai SSH không đúng định dạng OpenSSH (ví dụ: ssh-ed25519 AAAA...)")
    key_type, b64_blob, comment = m.group(1), m.group(2), m.group(3) or ""
    if "PRIVATE KEY" in public_key.upper():
        raise ValueError("Cấm gửi khóa riêng (Private Key) lên máy chủ")
    try:
        raw = base64.b64decode(b64_blob.encode("ascii"), validate=True)
    except Exception:
        raise ValueError("Nội dung base64 của khóa công khai SSH không hợp lệ")
    if len(raw) < 32:
        raise ValueError("Độ dài khóa công khai SSH quá ngắn, không an toàn")
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")
    return {
        "key_type": key_type,
        "fingerprint_sha256": f"SHA256:{digest}",
        "comment": comment.strip(),
    }


def validate_wg_public_key(public_key: str) -> str:
    pk = public_key.strip()
    if not WG_PUBKEY_PATTERN.match(pk):
        raise ValueError("Khóa công khai WireGuard phải là chuỗi Base64 44 ký tự (32 byte)")
    return pk


class SshKeyRegistry:
    """Quản lý vòng đời khóa công khai SSH riêng cho từng thành viên (thêm, xoay vòng, thu hồi)."""

    def __init__(self, data_dir: str):
        self.state_path = os.path.join(data_dir, "v2_ssh_keys.json")
        self.lock_path = self.state_path + ".lock"
        os.makedirs(data_dir, exist_ok=True)

    @contextmanager
    def _locked(self):
        with open(self.lock_path, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                if os.path.exists(self.state_path):
                    with open(self.state_path, "r", encoding="utf-8") as rf:
                        st = json.load(rf)
                else:
                    st = {"keys": []}
                yield st
                tmp = self.state_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as wf:
                    json.dump(st, wf, ensure_ascii=False, indent=2)
                os.replace(tmp, self.state_path)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def list_keys(self, username: Optional[str] = None) -> List[Dict[str, Any]]:
        if not os.path.exists(self.state_path):
            return []
        with open(self.state_path, "r", encoding="utf-8") as rf:
            keys = json.load(rf).get("keys", [])
        return [k for k in keys if username is None or k.get("username") == username]

    def register_key(self, *, username: str, label: str, public_key: str, actor: str) -> Dict[str, Any]:
        meta = compute_ssh_fingerprint(public_key)
        with self._locked() as st:
            if any(k["fingerprint_sha256"] == meta["fingerprint_sha256"] and k["desired_state"] == "ACTIVE" for k in st["keys"]):
                raise ValueError("Khóa công khai SSH này đã được đăng ký")
            record = {
                "id": f"sshkey-{uuid.uuid4().hex[:10]}",
                "username": username,
                "label": label.strip() or meta["comment"] or "Khóa cá nhân",
                "key_type": meta["key_type"],
                "public_key": public_key.strip(),
                "fingerprint_sha256": meta["fingerprint_sha256"],
                "desired_state": "ACTIVE",
                "observed_state": "PENDING_SYNC",
                "created_by": actor,
                "created_at": _now(),
                "revoked_at": None,
            }
            st["keys"].insert(0, record)
            return record

    def revoke_key(self, key_id: str, *, actor: str, is_admin: bool) -> Dict[str, Any]:
        with self._locked() as st:
            rec = next((k for k in st["keys"] if k["id"] == key_id), None)
            if not rec:
                raise LookupError("Không tìm thấy khóa SSH")
            if not is_admin and rec["username"] != actor:
                raise PermissionError("Bạn không sở hữu khóa SSH này")
            rec["desired_state"] = "REVOKED"
            rec["observed_state"] = "REVOKED"
            rec["revoked_at"] = _now()
            rec["revoked_by"] = actor
            return dict(rec)
