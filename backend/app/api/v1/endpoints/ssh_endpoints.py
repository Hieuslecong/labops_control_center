from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any
from app.schemas.labops import SshEndpointResponse
from app.services.labops_service import service

router = APIRouter()

@router.get("", response_model=List[SshEndpointResponse])
async def list_ssh_endpoints():
    """List all reserved host SSH proxy endpoints mapped to container port 22"""
    return service.ssh_endpoints

@router.get("/available-port")
async def get_available_ssh_port(server_id: str = Query("track", description="Server ID (track or workstation)")):
    """Find next non-colliding TCP listen port in 22000-29999 for the given host"""
    try:
        port = service.get_next_available_ssh_port(server_id)
        server = next((s for s in service.servers if s["id"] == server_id), None)
        return {
            "server_id": server_id,
            "listen_address": server["vpn_ip"] if server else "10.200.0.1",
            "available_port": port,
            "port_range": "22000-29999"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
