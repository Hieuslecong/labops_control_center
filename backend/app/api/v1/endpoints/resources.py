from fastapi import APIRouter, Query
from typing import Optional, Dict, Any
from app.schemas.storage import HostResourceProfileResponse
from app.services.storage_inventory_service import storage_service

router = APIRouter()

@router.get("/profile", response_model=HostResourceProfileResponse)
async def get_resource_profile(host_id: Optional[str] = Query("workstation", alias="host_id")):
    """Trả về cấu hình CPU Shared Weighted và RAM Shared Host Protected cho máy chủ được chỉ định."""
    target_host = host_id or "workstation"
    prof = storage_service.get_resource_profile(target_host)
    return prof
