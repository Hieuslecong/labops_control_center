import os
from dataclasses import replace
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings


_bearer = HTTPBearer(auto_error=False)


def require_labops_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        if not os.environ.get("LABOPS_SESSION_SECRET", "").strip() and not settings.LABOPS_SESSION_SECRET:
            raise HTTPException(status_code=503, detail="Access management authentication is not configured")
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        principal = principal_from_authorization(f"Bearer {credentials.credentials}")
        require_role(principal, "ADMIN")
        return principal.username
    except AuthError as error:
        raise HTTPException(status_code=error.status, detail=error.detail) from error


# ---------------- V2 principal-based auth ----------------
from fastapi import Request

from app.core.security import (AuthError, Principal, principal_from_authorization,
                               require_node_agent, require_role)
from app.services.aiotgit_tokens import AioGitClaims, verify_workspace_token


def _raise(e: AuthError):
    raise HTTPException(status_code=e.status, detail=e.detail)


def _resolve_active_member(principal: Principal) -> Principal:
    """Resolve a signed external subject through the active member registry."""
    if not principal.auth_subject:
        raise HTTPException(status_code=403, detail="Researcher identity is not bound to a LabOps member")
    try:
        from app.services.labops_service import service

        matches = [
            member for member in service.members
            if member.get("clerk_user_id") == principal.auth_subject
        ]
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Member authorization state is unavailable") from exc
    if len(matches) != 1:
        raise HTTPException(status_code=403, detail="Member access is not provisioned")
    member = matches[0]
    if member.get("status") != "ACTIVE" or member.get("lifecycle_state") not in (None, "ACTIVE"):
        raise HTTPException(status_code=403, detail="Member access is not active")
    username = member.get("username")
    member_id = member.get("id")
    if not isinstance(username, str) or not username or not isinstance(member_id, str) or not member_id:
        raise HTTPException(status_code=503, detail="Member identity registry is invalid")
    return replace(principal, username=username, member_id=member_id)


def get_principal(request: Request) -> Principal:
    try:
        return principal_from_authorization(request.headers.get("Authorization"))
    except AuthError as e:
        _raise(e)


def require_user(p: Principal = Depends(get_principal)) -> Principal:
    if p.kind != "user":
        raise HTTPException(status_code=403, detail="Danh tính máy không được phép")
    if not p.is_admin:
        return _resolve_active_member(p)
    return p


def require_admin(p: Principal = Depends(get_principal)) -> Principal:
    try:
        require_role(p, "ADMIN")
    except AuthError as e:
        _raise(e)
    return p


def require_agent(p: Principal = Depends(get_principal)) -> Principal:
    try:
        require_node_agent(p)
    except AuthError as e:
        _raise(e)
    return p


def require_aiotgit(request: Request) -> AioGitClaims:
    """Authenticate a scoped CLI capability; never accept identity headers."""
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="AIOTGIT capability is required")
    secret = settings.LABOPS_AIOTGIT_SIGNING_KEY
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="AIOTGIT capability verification is not configured")
    token = header.split(" ", 1)[1].strip()
    try:
        claims = verify_workspace_token(token, secret)
        request.state.aiotgit_token = token
        return claims
    except ValueError as error:
        raise HTTPException(status_code=401, detail="AIOTGIT capability is invalid or expired") from error
