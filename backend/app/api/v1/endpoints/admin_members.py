from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_labops_admin
from app.schemas.labops import MemberCreateRequest, MemberResponse, StorageQuotaUpdateRequest
from app.services.labops_service import service
from app.services.workspace_controller import WorkspaceSecurityError

router = APIRouter(dependencies=[Depends(require_labops_admin)])


@router.post("/provision", response_model=MemberResponse)
async def provision_member(payload: MemberCreateRequest, actor: str = Depends(require_labops_admin)):
    try:
        data = payload.model_dump(by_alias=False)
        data["_actor_id"] = actor
        return service.provision_member(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except WorkspaceSecurityError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.post("/{user_id}/suspend", response_model=MemberResponse)
async def suspend_member(user_id: str, actor: str = Depends(require_labops_admin)):
    try:
        await service.revoke_member_access(user_id, actor=actor)
        return next(member for member in service.members if member["id"] == user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{user_id}/restore", response_model=MemberResponse)
async def restore_member(user_id: str):
    try:
        return service.restore_member(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{user_id}/storage-quota", response_model=MemberResponse)
async def update_storage_quota(user_id: str, payload: StorageQuotaUpdateRequest):
    try:
        return service.update_storage_quota(user_id, payload.quota_gb)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{user_id}/rotate-key")
@router.post("/{user_id}/revoke-key")
@router.patch("/{user_id}/ssh-port")
@router.delete("/{user_id}/permanent")
@router.post("/{user_id}/reset-password")
@router.post("/{user_id}/suspend-ssh")
@router.post("/{user_id}/restore-ssh")
@router.get("/{user_id}/login-events")
@router.get("/{user_id}/verify-dual-auth")
async def disabled_legacy_ssh_management(user_id: str):
    raise HTTPException(
        status_code=410,
        detail="Legacy per-user SSH/password provisioning and simulated verification are disabled",
    )


@router.post("/{user_id}/archive")
async def disabled_workspace_archive(user_id: str):
    raise HTTPException(status_code=409, detail="Workspace archival requires the verified workspace controller")
