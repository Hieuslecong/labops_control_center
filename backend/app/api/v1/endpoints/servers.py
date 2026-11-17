from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Dict, Any, Optional
from app.api.dependencies import require_admin, require_user
from app.core.security import Principal
from app.schemas.labops import PhysicalServerResponse
from app.services.labops_service import service

router = APIRouter()

@router.get("", response_model=List[PhysicalServerResponse])
async def list_servers(p: Principal = Depends(require_user)):
    """List physical servers in the AI research lab (Server 01 Track & Server 02 Workstation)"""
    return service.servers

@router.get("/{server_id}", response_model=PhysicalServerResponse)
async def get_server(server_id: str, p: Principal = Depends(require_user)):
    """Get real-time telemetry and resource quotas for a specific server"""
    server = next((s for s in service.servers if s["id"] == server_id), None)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server

@router.get("/{server_id}/processes")
async def get_server_processes(server_id: str, filter: str = Query("all", description="all, research, or gpu"), p: Principal = Depends(require_admin)):
    """Get real processes running on the server host with user, script, CPU, and RAM"""
    return service.get_server_processes(server_id, filter_type=filter)

@router.get("/{server_id}/gpu-processes")
async def get_server_gpu_processes(server_id: str, p: Principal = Depends(require_admin)):
    """Get active processes utilizing NVIDIA GPU VRAM on the server"""
    return service.get_server_gpu_processes(server_id)

@router.get("/{server_id}/ssh-sessions")
async def get_server_ssh_sessions(server_id: str, p: Principal = Depends(require_admin)):
    """Get active SSH login sessions on the server"""
    return service.get_server_ssh_sessions(server_id)

