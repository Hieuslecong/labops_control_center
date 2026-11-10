import secrets

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings


_bearer = HTTPBearer(auto_error=False)


def require_labops_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    actor: str | None = Header(default=None, alias="X-LabOps-Actor"),
) -> str:
    configured = settings.LABOPS_ADMIN_API_TOKEN
    if not configured:
        raise HTTPException(status_code=503, detail="Access management authentication is not configured")
    if not credentials or credentials.scheme.lower() != "bearer" or not secrets.compare_digest(credentials.credentials, configured):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return actor if actor and len(actor) <= 128 else "admin-unattributed"


# ---------------- V2 principal-based auth ----------------
from fastapi import Request

from app.core.security import (AuthError, Principal, principal_from_authorization,
                               require_node_agent, require_role)


def _raise(e: AuthError):
    raise HTTPException(status_code=e.status, detail=e.detail)


def get_principal(request: Request) -> Principal:
    try:
        return principal_from_authorization(request.headers.get("Authorization"))
    except AuthError as e:
        _raise(e)


def require_user(p: Principal = Depends(get_principal)) -> Principal:
    if p.kind != "user":
        raise HTTPException(status_code=403, detail="Danh tính máy không được phép")
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
