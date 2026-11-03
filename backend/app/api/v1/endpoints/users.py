import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from app.schemas.labops import MemberResponse, MemberCreateRequest
from app.services.labops_service import service

router = APIRouter()

@router.get("", response_model=List[MemberResponse])
async def list_members():
    """List all registered laboratory members with quotas, SSH status, and permissions"""
    return service.members

@router.get("/{user_id}", response_model=MemberResponse)
async def get_member(user_id: str):
    """Get single member detail and resource allocations"""
    member = next((m for m in service.members if m["id"] == user_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    return member

@router.post("", response_model=MemberResponse)
async def create_member(payload: MemberCreateRequest):
    """Provision a new lab member workspace and grant container cgroups quotas"""
    existing = next((m for m in service.members if m["username"] == payload.username), None)
    if existing:
        raise HTTPException(status_code=400, detail="Username đã tồn tại")

    new_id = f"mem-{uuid.uuid4().hex[:6]}"
    new_member = {
        "id": new_id,
        "full_name": payload.full_name,
        "username": payload.username,
        "email": payload.email,
        "role": payload.role,
        "assigned_server": payload.assigned_server,
        "workspace_name": payload.workspace_name,
        "status": "ACTIVE",
        "ssh_status": "OFFLINE",
        "ssh_sessions_count": 0,
        "ssh_public_key_preview": payload.ssh_public_key[:32] + "..." if payload.ssh_public_key else "Chưa cấu hình",
        "cpu_limit_cores": payload.cpu_limit_cores,
        "ram_limit_gb": payload.ram_limit_gb,
        "storage_quota_gb": payload.storage_quota_gb,
        "gpu_permissions": ["track-gpu-03"] if payload.assigned_server == "track" else ["ws-gpu-01"],
        "vram_policy": payload.vram_policy,
        "max_runtime_hours": 24,
        "active_jobs_count": 0,
        "total_gpu_hours": 0.0,
        "created_at": datetime.now().strftime("%Y-%m-%d")
    }
    service.members.append(new_member)

    # Audit log
    service.audit_logs.insert(0, {
        "id": f"aud-{uuid.uuid4().hex[:6]}",
        "actor": "admin@labops.local",
        "action": "MEMBER_CREATED",
        "target_type": "MEMBER",
        "target_id": new_id,
        "timestamp": "Vừa xong",
        "result": "SUCCESS",
        "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
        "details": f"Tạo tài khoản và cấu hình cgroups ({payload.cpu_limit_cores} cores, {payload.ram_limit_gb}GB RAM) cho {payload.username}"
    })

    return new_member

@router.put("/{user_id}/status")
async def update_member_status(user_id: str, payload: Dict[str, str]):
    """Update member access state: ACTIVE, SUSPENDED, REVOKING"""
    status = payload.get("status")
    if not status or status not in ["ACTIVE", "SUSPENDED", "REVOKING"]:
        raise HTTPException(status_code=400, detail="Invalid status value")

    member = next((m for m in service.members if m["id"] == user_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    member["status"] = status
    if status == "SUSPENDED":
        member["ssh_status"] = "OFFLINE"
        member["ssh_sessions_count"] = 0

    return {"success": True, "id": user_id, "status": status}

@router.delete("/{user_id}")
async def delete_member(user_id: str):
    """Deprovision workspace and delete member record"""
    member = next((m for m in service.members if m["id"] == user_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    service.members = [m for m in service.members if m["id"] != user_id]
    return {"success": True, "message": f"Member {member['username']} deleted"}

