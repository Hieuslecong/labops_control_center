from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from app.schemas.labops import (
    MemberResponse,
    MemberCreateRequest,
    StorageQuotaUpdateRequest,
    SshPortUpdateRequest,
    SshKeyEnrollRequest,
    SshPasswordResetRequest,
    SshPasswordResetResponse,
    SshLoginEventResponse,
    SshDualAuthVerificationResponse
)
from app.services.labops_service import service

router = APIRouter()

@router.post("/provision", response_model=MemberResponse)
async def provision_member(payload: MemberCreateRequest):
    """
    Atomic member provisioning endpoint:
    1. Validates safe Linux username.
    2. Checks storage pool capacity & reserves dedicated ZFS volume.
    3. Allocates non-colliding SSH port (22000-29999) mapped to WireGuard VPN IP.
    4. Enforces unprivileged container isolation (sudoPrivilege = DENIED).
    5. Records immutable audit log with correlation ID.
    """
    try:
        return service.provision_member(payload.model_dump(by_alias=False))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{user_id}/suspend", response_model=MemberResponse)
async def suspend_member(user_id: str):
    """Suspend member access: disables SSH proxy port, keeps storage volume intact"""
    try:
        return service.suspend_member(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{user_id}/restore", response_model=MemberResponse)
async def restore_member(user_id: str):
    """Restore suspended member access: reactivates SSH proxy device"""
    try:
        return service.restore_member(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{user_id}/rotate-key", response_model=MemberResponse)
async def rotate_ssh_key(user_id: str, payload: SshKeyEnrollRequest):
    """Update SSH public key, re-compute fingerprint and update container authorized_keys"""
    try:
        return service.rotate_ssh_key(user_id, payload.public_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{user_id}/revoke-key", response_model=MemberResponse)
async def revoke_ssh_key(user_id: str):
    """Revoke SSH key, purge authorized_keys and close SSH access"""
    try:
        return service.revoke_ssh_key(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.patch("/{user_id}/storage-quota", response_model=MemberResponse)
async def update_storage_quota(user_id: str, payload: StorageQuotaUpdateRequest):
    """Adjust container ZFS storage quota with pool capacity verification"""
    try:
        return service.update_storage_quota(user_id, payload.quota_gb)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch("/{user_id}/ssh-port", response_model=MemberResponse)
async def update_ssh_port(user_id: str, payload: SshPortUpdateRequest):
    """Change dedicated SSH port with uniqueness check on the physical host"""
    try:
        return service.update_ssh_port(user_id, payload.listen_port)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{user_id}/archive", response_model=MemberResponse)
async def archive_workspace(user_id: str):
    """Archive workspace container, disconnect SSH, and keep ZFS volume snapshots"""
    try:
        return service.archive_workspace(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/{user_id}/permanent")
async def delete_member_permanently(user_id: str):
    """Permanently destroy container workspace, release SSH port, and delete member record"""
    try:
        return service.delete_member_permanently(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{user_id}/reset-password", response_model=SshPasswordResetResponse)
async def reset_member_password(user_id: str, payload: SshPasswordResetRequest):
    """Reset Linux SSH password separately from website password; returns one-time temporary password"""
    try:
        return service.reset_member_password(
            user_id,
            new_password=payload.new_password,
            require_change=payload.require_change_on_next_login
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{user_id}/suspend-ssh", response_model=MemberResponse)
async def suspend_ssh_access(user_id: str):
    """Suspend member SSH access without affecting physical host administrator access"""
    try:
        return service.suspend_ssh_access(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{user_id}/restore-ssh", response_model=MemberResponse)
async def restore_ssh_access(user_id: str):
    """Restore member SSH access after policy compliance"""
    try:
        return service.restore_ssh_access(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/{user_id}/login-events", response_model=List[SshLoginEventResponse])
async def get_member_login_events(user_id: str):
    """Retrieve detailed SSH login audit events (tracking both Public Key and Password stages)"""
    return service.get_member_login_events(user_id)

@router.get("/{user_id}/verify-dual-auth", response_model=SshDualAuthVerificationResponse)
async def verify_member_dual_auth(user_id: str):
    """Execute automated sshd verification tests inside member container"""
    try:
        return service.verify_member_dual_auth(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

