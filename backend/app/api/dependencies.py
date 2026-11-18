from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import (
    AuthError,
    Principal,
    principal_from_authorization,
    require_node_agent,
    require_role,
)

_bearer = HTTPBearer(auto_error=False)


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


def require_labops_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Authenticate strictly via signed V2 ADMIN Bearer token; ignores X-LabOps-Actor headers."""
    if not credentials or credentials.scheme.lower() != "bearer":
        try:
            # Fail-closed 503 if LABOPS_SESSION_SECRET is missing, 401 if unauthenticated
            principal_from_authorization(None)
        except AuthError as err:
            from app.core.security import _secret

            if not _secret():
                raise HTTPException(status_code=503, detail="Access management authentication is not configured") from err
            raise HTTPException(status_code=401, detail="Unauthorized") from err
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        p = principal_from_authorization(f"Bearer {credentials.credentials}")
        require_role(p, "ADMIN")
        return p.username
    except AuthError as err:
        raise HTTPException(status_code=err.status, detail=err.detail) from err
