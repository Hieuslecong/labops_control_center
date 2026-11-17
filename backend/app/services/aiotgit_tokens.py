"""Short-lived, workspace-scoped capabilities for the AIOTGIT Broker."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
from typing import Iterable

ISSUER = "labops-aiotgit"
AUDIENCE = "labops-repositoryguard"
MAX_TTL_SECONDS = 900
ALLOWED_SCOPES = {
    "repo:request:submit",
    "repo:request:list",
    "repo:request:read",
    "repo:bundle:read",
    "repo:request:decide",
    "repo:audit:read",
}
_WORKSPACE_ID = re.compile(r"^ws-[a-f0-9]{10}$")
_NODE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ACTOR_ID = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")
_TOKEN_FIELDS = {
    "iss", "aud", "sub", "role", "member_id", "workspace_id", "node_id",
    "scopes", "resource_id", "jti", "iat", "exp",
}


@dataclass(frozen=True)
class AioGitClaims:
    subject: str
    role: str
    member_id: str
    workspace_id: str
    node_id: str
    scopes: tuple[str, ...]
    resource_id: str
    token_id: str
    issued_at: int
    expires_at: int


def issue_workspace_token(
    *,
    username: str,
    role: str,
    member_id: str | None,
    workspace_id: str,
    node_id: str,
    scopes: Iterable[str],
    secret: str,
    ttl: int = 600,
    resource_id: str = "",
    now: datetime | None = None,
) -> str:
    """Issue a Backend-signed capability; caller must already authorize scope."""
    if len(secret) < 32:
        raise ValueError("AIOTGIT signing key must contain at least 32 characters")
    if not _ACTOR_ID.fullmatch(username) or role not in {"ADMIN", "RESEARCHER"}:
        raise ValueError("authenticated actor identity is invalid")
    if type(ttl) is not int or not 1 <= ttl <= MAX_TTL_SECONDS:
        raise ValueError("AIOTGIT capability lifetime is invalid")
    if not _NODE_ID.fullmatch(node_id):
        raise ValueError("workspace node identity is invalid")
    if role == "RESEARCHER":
        if not member_id or not _ACTOR_ID.fullmatch(member_id) or (workspace_id and not _WORKSPACE_ID.fullmatch(workspace_id)):
            raise ValueError("researcher capability identity is invalid")
    elif workspace_id and not _WORKSPACE_ID.fullmatch(workspace_id):
        raise ValueError("workspace identity is invalid")
    if len(resource_id) > 128 or any(char in resource_id for char in "\r\n\x00"):
        raise ValueError("capability resource is invalid")
    scope_list = list(scopes)
    if not scope_list or len(scope_list) > len(ALLOWED_SCOPES) or len(set(scope_list)) != len(scope_list):
        raise ValueError("AIOTGIT capability scopes are invalid")
    if any(scope not in ALLOWED_SCOPES for scope in scope_list):
        raise ValueError("unsupported AIOTGIT capability scope")
    if role == "RESEARCHER" and not workspace_id and any(scope not in {"repo:request:list", "repo:request:read"} for scope in scope_list):
        raise ValueError("unbound researcher capability may only read own request metadata")

    issued = int((now or datetime.now(timezone.utc)).timestamp())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": username,
        "role": role,
        "member_id": member_id or "",
        "workspace_id": workspace_id,
        "node_id": node_id,
        "scopes": sorted(scope_list),
        "resource_id": resource_id,
        "jti": secrets.token_hex(16),
        "iat": issued,
        "exp": issued + ttl,
    }
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def verify_workspace_token(
    token: str,
    secret: str,
    *,
    now: datetime | None = None,
) -> AioGitClaims:
    if len(secret) < 32 or not isinstance(token, str) or len(token) > 16_384 or token.count(".") != 1:
        raise ValueError("invalid AIOTGIT capability")
    payload_part, signature_part = token.split(".", 1)
    try:
        payload = _b64decode(payload_part)
        supplied_signature = _b64decode(signature_part)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid AIOTGIT capability encoding") from exc
    if not payload or len(payload) > 8192:
        raise ValueError("invalid AIOTGIT capability payload")
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_signature, expected):
        raise ValueError("AIOTGIT capability signature mismatch")
    try:
        data = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid AIOTGIT capability payload") from exc
    if not isinstance(data, dict) or set(data) != _TOKEN_FIELDS:
        raise ValueError("AIOTGIT capability fields are invalid")
    if data["iss"] != ISSUER or data["aud"] != AUDIENCE:
        raise ValueError("AIOTGIT capability issuer or audience is invalid")
    subject = data["sub"]
    role = data["role"]
    member_id = data["member_id"]
    workspace_id = data["workspace_id"]
    node_id = data["node_id"]
    resource_id = data["resource_id"]
    token_id = data["jti"]
    scopes = data["scopes"]
    issued = data["iat"]
    expires = data["exp"]
    if not isinstance(subject, str) or not _ACTOR_ID.fullmatch(subject) or not isinstance(role, str) or role not in {"ADMIN", "RESEARCHER"}:
        raise ValueError("AIOTGIT capability actor is invalid")
    if not isinstance(member_id, str) or not isinstance(workspace_id, str) or not isinstance(node_id, str):
        raise ValueError("AIOTGIT capability binding is invalid")
    if not _NODE_ID.fullmatch(node_id):
        raise ValueError("AIOTGIT capability node is invalid")
    if role == "RESEARCHER" and (not _ACTOR_ID.fullmatch(member_id) or (workspace_id and not _WORKSPACE_ID.fullmatch(workspace_id))):
        raise ValueError("researcher capability identity is invalid")
    if role == "ADMIN" and workspace_id and not _WORKSPACE_ID.fullmatch(workspace_id):
        raise ValueError("AIOTGIT capability workspace is invalid")
    if not isinstance(resource_id, str) or len(resource_id) > 128 or any(c in resource_id for c in "\r\n\x00"):
        raise ValueError("AIOTGIT capability resource is invalid")
    if not isinstance(token_id, str) or len(token_id) < 16 or len(token_id) > 128:
        raise ValueError("AIOTGIT capability token ID is invalid")
    if not isinstance(scopes, list) or not scopes or len(scopes) > len(ALLOWED_SCOPES):
        raise ValueError("AIOTGIT capability scopes are invalid")
    if any(not isinstance(scope, str) or scope not in ALLOWED_SCOPES for scope in scopes) or len(set(scopes)) != len(scopes):
        raise ValueError("AIOTGIT capability scopes are invalid")
    if role == "RESEARCHER" and not workspace_id and any(scope not in {"repo:request:list", "repo:request:read"} for scope in scopes):
        raise ValueError("unbound researcher capability may only read own request metadata")
    if type(issued) is not int or type(expires) is not int:
        raise ValueError("AIOTGIT capability lifetime is invalid")
    current = int((now or datetime.now(timezone.utc)).timestamp())
    if issued > current + 30 or expires <= current or expires <= issued or expires - issued > MAX_TTL_SECONDS:
        raise ValueError("AIOTGIT capability has expired or invalid lifetime")
    return AioGitClaims(
        subject=subject,
        role=role,
        member_id=member_id,
        workspace_id=workspace_id,
        node_id=node_id,
        scopes=tuple(scopes),
        resource_id=resource_id,
        token_id=token_id,
        issued_at=issued,
        expires_at=expires,
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
