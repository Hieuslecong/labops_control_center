from fastapi import APIRouter, Depends
from app.api.dependencies import require_admin, require_user
from app.api.v1.endpoints import (
    dashboard,
    servers,
    gpus,
    users,
    jobs,
    security,
    events,
    audit,
    admin_members,
    access,
    storage_pools,
    ssh_endpoints,
    monitoring,
    workspaces,
    v2_platform
)

api_router = APIRouter()

api_router.include_router(dashboard.router, dependencies=[Depends(require_user)], prefix="/dashboard", tags=["Dashboard"])
api_router.include_router(servers.router, dependencies=[Depends(require_user)], prefix="/servers", tags=["Servers"])
api_router.include_router(gpus.router, prefix="/gpus", tags=["GPUs"])
api_router.include_router(users.router, dependencies=[Depends(require_admin)], prefix="/users", tags=["Users"])
api_router.include_router(admin_members.router, dependencies=[Depends(require_admin)], prefix="/admin/members", tags=["Admin Members"])
api_router.include_router(access.router, dependencies=[Depends(require_admin)], prefix="/access", tags=["Access"])
api_router.include_router(storage_pools.router, dependencies=[Depends(require_admin)], prefix="/storage-pools", tags=["Storage Pools"])
api_router.include_router(ssh_endpoints.router, dependencies=[Depends(require_admin)], prefix="/ssh", tags=["SSH Endpoints"])
api_router.include_router(jobs.router, dependencies=[Depends(require_user)], prefix="/jobs", tags=["Jobs"])
api_router.include_router(security.router, dependencies=[Depends(require_user)], prefix="/security", tags=["Security"])
api_router.include_router(events.router, dependencies=[Depends(require_admin)], prefix="/events", tags=["Events"])
api_router.include_router(audit.router, dependencies=[Depends(require_admin)], prefix="/audit-logs", tags=["Audit"])
api_router.include_router(monitoring.router, dependencies=[Depends(require_user)], prefix="/monitoring", tags=["Monitoring"])
api_router.include_router(workspaces.router, dependencies=[Depends(require_admin)], prefix="/workspaces", tags=["Workspaces"])


api_router.include_router(v2_platform.router, prefix="/v2", tags=["V2 Platform"])
