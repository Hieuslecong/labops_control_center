from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from app.api.dependencies import require_user
from app.core.security import Principal
from app.services.resource_access import visible_storage_allocations
from app.schemas.labops import StoragePoolResponse, StorageAllocationResponse
from app.services.labops_service import service

router = APIRouter()

@router.get("", response_model=List[StoragePoolResponse])
async def list_storage_pools(p: Principal = Depends(require_user)):
    """List physical storage devices and ZFS storage pools across Track and Workstation hosts"""
    return service.storage_pools

@router.get("/allocations", response_model=List[StorageAllocationResponse])
async def list_storage_allocations(p: Principal = Depends(require_user)):
    """List dedicated custom volume storage allocations per member workspace"""
    return visible_storage_allocations(service.storage_allocations, service.members, p)

@router.get("/{pool_id}", response_model=StoragePoolResponse)
async def get_storage_pool(pool_id: str, p: Principal = Depends(require_user)):
    """Get real-time capacity and health of a specific storage pool"""
    pool = next((p for p in service.storage_pools if p["id"] == pool_id), None)
    if not pool:
        raise HTTPException(status_code=404, detail="Storage pool not found")
    return pool

