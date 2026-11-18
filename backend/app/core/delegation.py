"""HMAC-SHA256 delegation helpers matching Go gateway/delegation.go."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, Optional

DELEGATION_AUDIENCE = "labops-node-execution"
MAX_DELEGATION_TTL_SECONDS = 300


def compute_params_hash(params: Optional[Dict[str, Any]]) -> str:
    str_params = {str(k): str(v) for k, v in (params or {}).items()}
    h = hashlib.sha256()
    for key in sorted(str_params.keys()):
        h.update(key.encode("utf-8"))
        h.update(b"\x00")
        h.update(str_params[key].encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def build_delegation_context(
    *,
    secret: str,
    command_id: str,
    actor: str,
    role: str,
    node_id: str,
    action: str,
    workspace_id: str = "",
    resource_ref: str = "",
    parameters: Optional[Dict[str, Any]] = None,
    ttl_seconds: int = 60,
    service_id: str = "labops-fastapi",
) -> str:
    if not secret or not secret.strip():
        raise ValueError("LABOPS_DELEGATION_SECRET is required to build delegation token")
    now = int(time.time())
    clamped_ttl = max(1, min(int(ttl_seconds), MAX_DELEGATION_TTL_SECONDS))
    claims: Dict[str, Any] = {
        "actor_id": actor,
        "actor_role": role.upper(),
        "service_id": service_id,
        "action": action,
        "node_id": node_id,
        "aud": DELEGATION_AUDIENCE,
        "request_id": command_id,
        "nonce": uuid.uuid4().hex,
        "params_sha256": compute_params_hash(parameters),
        "iat": now,
        "exp": now + clamped_ttl,
    }
    if workspace_id:
        claims["workspace_id"] = workspace_id
    if resource_ref:
        claims["resource_ref"] = resource_ref

    payload_bytes = json.dumps(claims, separators=(",", ":")).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    sig = hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    encoded_sig = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    return f"{encoded_payload}.{encoded_sig}"
