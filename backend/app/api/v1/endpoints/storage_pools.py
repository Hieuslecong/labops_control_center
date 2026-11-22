from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional, Dict, Any
from app.schemas.storage import (
    StoragePoolInfoResponse,
    StorageValidateRequest,
    StorageValidateResponse,
)
from app.services.storage_inventory_service import storage_service
from app.services.labops_service import service

router = APIRouter()

def _enrich_pool(p: Dict[str, Any]) -> Dict[str, Any]:
    res = dict(p)
    # Ensure legacy compatibility fields
    res["id"] = p.get("storage_id", p.get("id", ""))
    res["serverId"] = p.get("host_id", p.get("serverId", "workstation"))
    res["server_id"] = res["serverId"]
    res["serverName"] = "Workstation Primary" if res["serverId"] == "workstation" else "Rack Server Cluster"
    res["server_name"] = res["serverName"]
    res["name"] = p.get("mount_point", p.get("name", ""))
    res["label"] = p.get("label") or f"{p.get('filesystem', '').upper()} ({p.get('mount_point', '')})"
    res["diskIdentifier"] = p.get("device", "")
    res["disk_identifier"] = res["diskIdentifier"]
    res["driver"] = p.get("filesystem", "ext4")
    res["capacityGb"] = p.get("capacity_gib", 0.0)
    res["capacity_gb"] = res["capacityGb"]
    res["usedGb"] = p.get("used_gib", 0.0)
    res["used_gb"] = res["usedGb"]
    res["freeGb"] = p.get("free_gib", 0.0)
    res["free_gb"] = res["freeGb"]
    res["healthStatus"] = "ONLINE" if p.get("eligible", False) else "DEGRADED"
    res["health_status"] = res["healthStatus"]
    res["isDefault"] = bool(
        p.get("is_default")
        or p.get("mount_point") in ["/mnt/HDD3", "/media/LabOps-Data", "/tank/workspaces"]
    )
    res["is_default"] = res["isDefault"]
    return res

@router.get("", response_model=List[Dict[str, Any]])
async def list_storage_pools(host_id: Optional[str] = Query(None, alias="host_id")):
    """List physical storage devices and verified storage pools per host or globally."""
    if host_id:
        pools = storage_service.get_storage_pools(host_id)
        return [_enrich_pool(p) for p in pools]
    
    # Global: cả workstation và track (deduplicate theo host + storage + device + mount)
    ws_pools = storage_service.get_storage_pools("workstation")
    track_pools = storage_service.get_storage_pools("track")
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for p in ws_pools + track_pools:
        enriched = _enrich_pool(p)
        key = (
            enriched.get("serverId"),
            enriched.get("id"),
            enriched.get("diskIdentifier"),
            enriched.get("name"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(enriched)
    return deduped

@router.post("/validate", response_model=StorageValidateResponse)
async def validate_storage_allocation(payload: StorageValidateRequest):
    """Kiểm tra tính hợp lệ và dung lượng khả dụng của storage pool trước khi cấp phát cho thành viên."""
    res = storage_service.validate_allocation(
        host_id=payload.host_id,
        storage_id=payload.storage_id,
        requested_quota_gib=payload.requested_quota_gib
    )
    if not res.get("valid"):
        raise HTTPException(status_code=400, detail=res.get("error", "Storage allocation invalid"))
    return res

@router.get("/allocations")
async def list_storage_allocations():
    """List dedicated custom volume storage allocations per member workspace."""
    return service.storage_allocations

@router.get("/{pool_id}")
async def get_storage_pool(pool_id: str, host_id: Optional[str] = None):
    """Get real-time capacity and health of a specific storage pool."""
    target_host = host_id or "workstation"
    pool = storage_service.get_storage_pool_by_id(target_host, pool_id)
    if not pool:
        # Check track as well
        pool = storage_service.get_storage_pool_by_id("track", pool_id)
    if not pool:
        # Check legacy service
        legacy = next((p for p in service.storage_pools if p.get("id") == pool_id), None)
        if legacy:
            return legacy
        raise HTTPException(status_code=404, detail=f"Storage pool '{pool_id}' not found")
    return _enrich_pool(pool)
