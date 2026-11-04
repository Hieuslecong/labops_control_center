from fastapi import APIRouter
from typing import Dict, Any
from app.services.labops_service import service

router = APIRouter()

@router.get("/health")
async def get_monitoring_health() -> Dict[str, Any]:
    """Get operational status and health across host, GPU, Incus, SSH, and WireGuard monitoring collectors"""
    return service.get_monitoring_health()

