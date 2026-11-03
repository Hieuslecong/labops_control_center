from fastapi import APIRouter, HTTPException
from typing import List
from app.schemas.labops import PhysicalServerResponse, GpuProcessResponse
from app.services.labops_service import service

router = APIRouter()

@router.get("", response_model=List[PhysicalServerResponse])
async def list_servers():
    """List physical servers in the AI research lab (Server 01 Track & Server 02 Workstation)"""
    return service.servers

@router.get("/{server_id}", response_model=PhysicalServerResponse)
async def get_server(server_id: str):
    """Get real-time telemetry and resource quotas for a specific server"""
    server = next((s for s in service.servers if s["id"] == server_id), None)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server

@router.get("/{server_id}/processes", response_model=List[GpuProcessResponse])
async def get_server_processes(server_id: str):
    """Get active processes running on GPUs belonging to the server"""
    server_gpus = [g for g in service.gpus if g["server_id"] == server_id]
    processes = []
    for g in server_gpus:
        processes.extend(g.get("active_processes", []))
    return processes

