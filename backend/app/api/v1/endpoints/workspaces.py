from fastapi import APIRouter
from typing import Dict, Any
from app.services.labops_service import service

router = APIRouter()

@router.get("/{workspace_id}/processes")
async def get_workspace_processes(workspace_id: str) -> Dict[str, Any]:
    """Get active processes executing inside a specific member workspace or container"""
    return service.get_workspace_processes(workspace_id)
