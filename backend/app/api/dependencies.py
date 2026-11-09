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
