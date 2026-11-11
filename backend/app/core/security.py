"""
Xác thực & phân quyền LabOps V2 (thuần stdlib để dùng cho cả FastAPI và fallback server).

- Token phiên: base64url(payload_json).base64url(HMAC-SHA256(secret, payload))
  payload = {"sub": username, "role": "ADMIN"|"RESEARCHER", "member_id": ..., "exp": epoch}
  Được phát hành bởi BFF Next.js (sau khi Clerk xác thực) dùng LABOPS_SESSION_SECRET.
- Node Agent: Bearer token máy riêng (LABOPS_NODE_AGENT_TOKEN), không bao giờ có quyền người dùng.
- Không tin username/role/X-LabOps-Actor do client gửi. Danh tính luôn lấy từ token đã kiểm chữ ký.
- Fail-closed: thiếu secret -> 503, token sai/hết hạn -> 401, sai vai trò -> 403.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings

ROLES = {"ADMIN", "RESEARCHER"}


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    member_id: Optional[str] = None
    kind: str = "user"  # "user" | "node_agent"

    @property
    def is_admin(self) -> bool:
        return self.kind == "user" and self.role == "ADMIN"


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret() -> str:
    if "LABOPS_SESSION_SECRET" in os.environ:
        return os.environ["LABOPS_SESSION_SECRET"]
    return getattr(settings, "LABOPS_SESSION_SECRET", "")


def issue_token(username: str, role: str, member_id: Optional[str] = None, ttl: int = 3600,
                secret: Optional[str] = None) -> str:
    secret = secret or _secret()
    if not secret:
        raise AuthError(503, "LABOPS_SESSION_SECRET chưa được cấu hình")
    if role not in ROLES:
        raise AuthError(400, "Vai trò không hợp lệ")
    payload = json.dumps({"sub": username, "role": role, "member_id": member_id,
                          "exp": int(time.time()) + ttl}, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def verify_token(token: str, secret: Optional[str] = None) -> Principal:
    secret = secret or _secret()
    if not secret:
        raise AuthError(503, "Xác thực chưa được cấu hình (LABOPS_SESSION_SECRET)")
    try:
        p64, s64 = token.split(".", 1)
        payload = _b64d(p64)
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(s64)):
            raise ValueError("bad signature")
        data = json.loads(payload)
    except Exception:
        raise AuthError(401, "Token không hợp lệ")
    if int(data.get("exp", 0)) < time.time():
        raise AuthError(401, "Phiên đăng nhập đã hết hạn")
    if data.get("role") not in ROLES or not data.get("sub"):
        raise AuthError(401, "Token không hợp lệ")
    return Principal(username=str(data["sub"]), role=data["role"], member_id=data.get("member_id"))


def principal_from_authorization(header: Optional[str]) -> Principal:
    if not header or not header.lower().startswith("bearer "):
        raise AuthError(401, "Yêu cầu đăng nhập")
    token = header.split(" ", 1)[1].strip()
    agent_token = os.getenv("LABOPS_NODE_AGENT_TOKEN", "")
    if agent_token and hmac.compare_digest(token, agent_token):
        return Principal(username="node-agent", role="RESEARCHER", kind="node_agent")
    # Token admin tĩnh cũ (BFF) vẫn được chấp nhận để tương thích, danh tính cố định.
    admin_token = os.getenv("LABOPS_ADMIN_API_TOKEN", "")
    if admin_token and hmac.compare_digest(token, admin_token):
        return Principal(username="admin-service", role="ADMIN")
    return verify_token(token)


def require_role(p: Principal, role: str) -> None:
    if p.kind != "user":
        raise AuthError(403, "Danh tính máy không được phép thực hiện thao tác người dùng")
    if role == "ADMIN" and not p.is_admin:
        raise AuthError(403, "Chỉ quản trị viên được phép")


def require_owner_or_admin(p: Principal, owner: Optional[str]) -> None:
    if p.is_admin:
        return
    if p.kind != "user" or not owner or owner != p.username:
        raise AuthError(403, "Bạn không có quyền với tài nguyên này")


def require_node_agent(p: Principal) -> None:
    if p.kind != "node_agent":
        raise AuthError(403, "Chỉ Node Agent được phép")

