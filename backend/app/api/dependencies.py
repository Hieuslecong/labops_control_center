import os
import secrets

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings


_bearer = HTTPBearer(auto_error=False)


def require_labops_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    actor: str | None = Header(default=None, alias="X-LabOps-Actor"),
) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        if not settings.LABOPS_ADMIN_API_TOKEN:
            raise HTTPException(status_code=503, detail="Access management authentication is not configured")
        raise HTTPException(status_code=401, detail="Unauthorized")
    raw_token = credentials.credentials
    # 1. Accept V2 HMAC ADMIN session token issued by Next.js BFF
    v2_secret = os.environ.get("LABOPS_SESSION_SECRET", "").strip()
    if v2_secret:
        try:
            p = principal_from_authorization(f"Bearer {raw_token}")
            require_role(p, "ADMIN")
            # Danh tính bắt buộc lấy từ token đã ký số, không tin header X-LabOps-Actor do client gửi
            return p.username
        except AuthError as v2_err:
            if not settings.LABOPS_ADMIN_API_TOKEN:
                raise HTTPException(status_code=v2_err.status, detail=v2_err.detail) from v2_err
    # 2. Fallback to static LABOPS_ADMIN_API_TOKEN
    configured = settings.LABOPS_ADMIN_API_TOKEN
    if not configured:
        raise HTTPException(status_code=503, detail="Access management authentication is not configured")
    if not secrets.compare_digest(raw_token, configured):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return (actor or "").strip() or "admin-service"


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
