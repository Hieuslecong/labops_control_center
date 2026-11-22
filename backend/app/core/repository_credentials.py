"""Credentials accepted exclusively by the managed repository broker."""
import time
import uuid

from app.core.security import (
    AuthError,
    Principal,
    _decode_and_verify_raw,
    _secret,
    _sign_payload,
    get_revocation_store,
)

AUDIENCE = "labops-repository"
SCOPES = ("repository:request", "repository:status", "repository:deliver")
MAX_TTL = 8 * 3600


def issue_repository_token(username: str, member_id: str, workspace_id: str, node_id: str) -> dict:
    secret = _secret()
    if not secret:
        raise AuthError(503, "Xác thực repository chưa được cấu hình")
    if not all((username, member_id, workspace_id, node_id)):
        raise AuthError(400, "Thiếu danh tính thành viên, workspace hoặc node")
    now = int(time.time())
    claims = {
        "sub": username,
        "member_id": member_id,
        "workspace_id": workspace_id,
        "node_id": node_id,
        "aud": AUDIENCE,
        "scope": list(SCOPES),
        "iat": now,
        "exp": now + MAX_TTL,
        "jti": uuid.uuid4().hex,
    }
    return {
        "token": _sign_payload(claims, secret),
        "jti": claims["jti"],
        "expires_at": claims["exp"],
    }


def verify_repository_token(token: str, scope: str) -> Principal:
    data = _decode_and_verify_raw(token)
    iat, exp = data.get("iat"), data.get("exp")
    now = time.time()
    required = ("sub", "member_id", "workspace_id", "node_id", "jti")
    if (
        data.get("aud") != AUDIENCE
        or any(not isinstance(data.get(key), str) or not data[key].strip() for key in required)
        or type(iat) is not int
        or type(exp) is not int
        or not iat <= now < exp <= iat + MAX_TTL
    ):
        raise AuthError(401, "Credential repository không hợp lệ hoặc đã hết hạn")
    granted = data.get("scope")
    if not isinstance(granted, list) or any(item not in SCOPES for item in granted) or scope not in granted:
        raise AuthError(403, "Credential repository thiếu quyền cho thao tác này")
    if get_revocation_store().is_revoked(
        data["jti"],
        data["sub"],
        iat,
        member_id=data["member_id"],
    ):
        raise AuthError(401, "Credential repository đã bị thu hồi")
    return Principal(
        username=data["sub"],
        role="RESEARCHER",
        member_id=data["member_id"],
        kind="repository",
        jti=data["jti"],
        iat=iat,
        scope=scope,
        workspace_id=data["workspace_id"],
        node_id=data["node_id"],
    )
