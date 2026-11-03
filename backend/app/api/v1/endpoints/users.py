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
    """Provision a new lab member workspace, allocate ZFS storage, reserve SSH port, and verify privilege isolation"""
    try:
        return service.provision_member(payload.model_dump(by_alias=False))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{user_id}/status")
async def update_member_status(user_id: str, payload: Dict[str, str]):
    """Update member access state: ACTIVE, SUSPENDED"""
    status = payload.get("status")
    if not status or status not in ["ACTIVE", "SUSPENDED", "ARCHIVED"]:
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ")

    try:
        if status == "SUSPENDED":
            member = service.suspend_member(user_id)
        elif status == "ACTIVE":
            member = service.restore_member(user_id)
        else:
            member = service.archive_workspace(user_id)
        return {"success": True, "id": user_id, "status": member["status"]}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/{user_id}")
async def delete_member(user_id: str):
    """Deprovision workspace and delete member record"""
    try:
        return service.delete_member_permanently(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

