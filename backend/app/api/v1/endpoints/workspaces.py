from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
from app.api.dependencies import require_user
from app.core.security import Principal
from app.services.labops_service import service
from app.services.resource_access import find_visible_workspace

router = APIRouter()

@router.get("/{workspace_id}/processes")
async def get_workspace_processes(workspace_id: str, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    """Get active processes executing inside a specific member workspace or container"""
    owner = None if p.is_admin else p.username
    workspaces = service.workspace_controller.list_workspaces(owner=owner, legacy_endpoints=service.ssh_endpoints)
    workspace = find_visible_workspace(workspaces, workspace_id, p)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return service.get_workspace_processes(str(workspace.get("name") or workspace_id))

