from fastapi import APIRouter, Depends
from app.api.dependencies import require_admin
from app.core.security import Principal
from typing import Dict, Any
from app.services.labops_service import service

router = APIRouter()

@router.get("/health")
async def get_monitoring_health(p: Principal = Depends(require_admin)) -> Dict[str, Any]:
    """Get operational status and health across host, GPU, Incus, SSH, and WireGuard monitoring collectors"""
    return service.get_monitoring_health()

