from fastapi import APIRouter, Depends, HTTPException
from app.api.dependencies import require_labops_admin
from typing import List, Dict, Any
from app.schemas.labops import SshEndpointResponse
from app.services.labops_service import service

router = APIRouter(dependencies=[Depends(require_labops_admin)])

@router.get("", response_model=List[SshEndpointResponse])
async def list_ssh_endpoints():
    """List all reserved host SSH proxy endpoints mapped to container port 22"""
    return service.ssh_endpoints

@router.get("/available-port")
async def disabled_ssh_port_allocation():
    raise HTTPException(status_code=410, detail="Per-user SSH port allocation is disabled")

