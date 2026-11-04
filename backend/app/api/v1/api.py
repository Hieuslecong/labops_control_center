from fastapi import APIRouter
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
    storage_pools,
    ssh_endpoints,
    monitoring,
    workspaces
)

api_router = APIRouter()

api_router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
api_router.include_router(servers.router, prefix="/servers", tags=["Servers"])
api_router.include_router(gpus.router, prefix="/gpus", tags=["GPUs"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(admin_members.router, prefix="/admin/members", tags=["Admin Members"])
api_router.include_router(storage_pools.router, prefix="/storage-pools", tags=["Storage Pools"])
api_router.include_router(ssh_endpoints.router, prefix="/ssh", tags=["SSH Endpoints"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["Jobs"])
api_router.include_router(security.router, prefix="/security", tags=["Security"])
api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(audit.router, prefix="/audit-logs", tags=["Audit"])
api_router.include_router(monitoring.router, prefix="/monitoring", tags=["Monitoring"])
api_router.include_router(workspaces.router, prefix="/workspaces", tags=["Workspaces"])

