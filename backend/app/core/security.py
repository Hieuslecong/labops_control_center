"""
Xác thực & phân quyền LabOps V2 (thuần stdlib để dùng cho cả FastAPI và fallback server).

- Token phiên: base64url(payload_json).base64url(HMAC-SHA256(secret, payload))
  payload = {"sub": username, "role": "ADMIN"|"RESEARCHER", "member_id": ..., "exp": epoch}
  Được phát hành bởi BFF Next.js (sau khi Clerk xác thực) dùng LABOPS_SESSION_SECRET.
- Node Agent: token theo từng node (LABOPS_NODE_AGENT_TOKENS), không bao giờ có quyền người dùng.
- Không tin username/role/X-LabOps-Actor do client gửi. Danh tính luôn lấy từ token đã kiểm chữ ký.
- Fail-closed: thiếu secret -> 503, token sai/hết hạn -> 401, sai vai trò -> 403.
"""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings

ROLES = {"ADMIN", "RESEARCHER"}
MAX_API_TOKEN_TTL_SECONDS = 900
AUTH_SUBJECT_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _valid_node_id(node_id: object) -> bool:
    if not isinstance(node_id, str) or not node_id or len(node_id) > 63:
        return False
    return all(
        char in "abcdefghijklmnopqrstuvwxyz0123456789"
        or (index > 0 and char == "-")
        for index, char in enumerate(node_id)
    )


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
    auth_subject: Optional[str] = None
    kind: str = "user"  # "user" | "node_agent"
    node_id: Optional[str] = None

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


def issue_token(username: str, role: str, member_id: Optional[str] = None, ttl: int = 300,
                secret: Optional[str] = None, auth_subject: Optional[str] = None) -> str:
    secret = secret or _secret()
    if not secret:
        raise AuthError(503, "LABOPS_SESSION_SECRET chưa được cấu hình")
    if role not in ROLES:
        raise AuthError(400, "Vai trò không hợp lệ")
    if not isinstance(username, str) or not username.strip():
        raise AuthError(400, "Danh tính người dùng không hợp lệ")
    if auth_subject is not None and (
        not isinstance(auth_subject, str) or not AUTH_SUBJECT_RE.fullmatch(auth_subject)
    ):
        raise AuthError(400, "Danh tính xác thực bên ngoài không hợp lệ")
    if type(ttl) is not int or not 1 <= ttl <= MAX_API_TOKEN_TTL_SECONDS:
        raise AuthError(400, "Thời hạn token không hợp lệ")
    now = int(time.time())
    payload = json.dumps({
        "sub": username,
        "role": role,
        "member_id": member_id,
        "auth_subject": auth_subject,
        "iat": now,
        "exp": now + ttl,
        "iss": "labops-bff",
        "aud": "labops-api",
    }, separators=(",", ":")).encode()
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
    if not isinstance(data, dict):
        raise AuthError(401, "Token không hợp lệ")
    exp = data.get("exp")
    issued_at = data.get("iat")
    role = data.get("role")
    subject = data.get("sub")
    auth_subject = data.get("auth_subject")
    if (
        type(exp) is not int
        or type(issued_at) is not int
        or data.get("iss") != "labops-bff"
        or data.get("aud") != "labops-api"
        or not isinstance(role, str)
        or role not in ROLES
        or (auth_subject is not None and (
            not isinstance(auth_subject, str) or not AUTH_SUBJECT_RE.fullmatch(auth_subject)
        ))
    ):
        raise AuthError(401, "Token không hợp lệ")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthError(401, "Token không hợp lệ")
    now = int(time.time())
    if exp <= now:
        raise AuthError(401, "Phiên đăng nhập đã hết hạn")
    if issued_at > now + 30 or exp <= issued_at or exp - issued_at > MAX_API_TOKEN_TTL_SECONDS:
        raise AuthError(401, "Thời hạn token không hợp lệ")
    member_id = data.get("member_id")
    if member_id is not None and (not isinstance(member_id, str) or not member_id.strip()):
        raise AuthError(401, "Token không hợp lệ")
    return Principal(
        username=data["sub"], role=data["role"], member_id=member_id,
        auth_subject=auth_subject,
    )


def principal_from_authorization(header: Optional[str]) -> Principal:
    if not header or not header.lower().startswith("bearer "):
        raise AuthError(401, "Yêu cầu đăng nhập")
    token = header.split(" ", 1)[1].strip()
    configured_node_tokens = os.getenv("LABOPS_NODE_AGENT_TOKENS", "")
    if configured_node_tokens:
        try:
            def reject_duplicate_keys(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate node token key")
                    result[key] = value
                return result

            node_tokens = json.loads(configured_node_tokens, object_pairs_hook=reject_duplicate_keys)
            if not isinstance(node_tokens, dict):
                raise ValueError("node token map must be an object")
            for node_id, expected in node_tokens.items():
                if (_valid_node_id(node_id) and isinstance(expected, str) and expected
                        and hmac.compare_digest(token, expected)):
                    return Principal(
                        username=f"node-agent:{node_id}",
                        role="RESEARCHER",
                        kind="node_agent",
                        node_id=node_id,
                    )
        except (TypeError, ValueError, json.JSONDecodeError):
            # Invalid machine identity configuration must never authorize a node.
            pass
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


def resolve_owner_filter(p: Principal, requested_owner: Optional[str]) -> Optional[str]:
    """Prevent user-controlled list filters from widening a researcher's view."""
    if p.kind != "user":
        raise AuthError(403, "Danh tính máy không được phép xem tiến trình người dùng")
    if p.is_admin:
        return requested_owner
    if requested_owner and requested_owner != p.username:
        raise AuthError(403, "Không được truy vấn tiến trình của thành viên khác")
    return p.username


def require_node_agent(p: Principal) -> None:
    if p.kind != "node_agent" or not _valid_node_id(p.node_id):
        raise AuthError(403, "Chỉ Node Agent được phép")


def require_node_agent_for_host(p: Principal, host_id: str) -> None:
    require_node_agent(p)
    if not host_id or p.node_id != host_id:
        raise AuthError(403, "Node Agent không được phép gửi heartbeat cho node khác")


def _fallback_admin_required(method: str, path: str) -> bool:
    method = method.upper()
    path = path.rstrip("/") or "/"
    admin_prefixes = (
        "/api/v1/users", "/api/v1/admin", "/api/v1/ssh", "/api/v1/access",
        "/api/v1/audit-logs", "/api/v1/events", "/api/v1/security/reviews",
        "/api/v1/storage-pools",
    )
    if path.startswith("/api/v1/workspaces"):
        return method != "GET"
    if path.startswith(admin_prefixes):
        return True
    if path in {"/api/v1/security", "/api/v1/monitoring/health"}:
        return True
    if path.startswith("/api/v1/servers/") and path.rsplit("/", 1)[-1] in {
        "processes", "gpu-processes", "ssh-sessions",
    }:
        return True
    return method in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith("/api/v1/security/scans/") and path.endswith("/decision")


def authorize_fallback_request(
    method: str,
    path: str,
    principal: Principal,
    members: Optional[list[dict]] = None,
) -> Principal:
    """Apply user/action authorization before dispatching a fallback HTTP API route."""
    normalized_path = path.rstrip("/") or "/"
    if method.upper() == "POST" and normalized_path.startswith("/api/v1/v2/hosts/") and normalized_path.endswith("/agent/heartbeat"):
        host_id = normalized_path.split("/")[5] if len(normalized_path.split("/")) > 5 else ""
        require_node_agent_for_host(principal, host_id)
        return principal
    require_role(principal, "RESEARCHER")
    if _fallback_admin_required(method, normalized_path):
        require_role(principal, "ADMIN")
    if principal.is_admin:
        return principal
    if not principal.auth_subject or members is None:
        raise AuthError(403, "Researcher identity is not bound to a LabOps member")
    matches = [
        member for member in members
        if isinstance(member, dict) and member.get("clerk_user_id") == principal.auth_subject
    ]
    if len(matches) != 1:
        raise AuthError(403, "Member access is not provisioned")
    member = matches[0]
    if member.get("status") != "ACTIVE" or member.get("lifecycle_state") not in (None, "ACTIVE"):
        raise AuthError(403, "Member access is not active")
    username, member_id = member.get("username"), member.get("id")
    if not isinstance(username, str) or not username or not isinstance(member_id, str) or not member_id:
        raise AuthError(503, "Member identity registry is invalid")
    return replace(principal, username=username, member_id=member_id)

