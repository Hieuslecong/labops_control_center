from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_labops_admin
from app.schemas.labops import MemberCreateRequest, MemberResponse, WireGuardDeviceCreateRequest
from app.services.labops_service import service
from app.services.workspace_controller import WorkspaceSecurityError

router = APIRouter(dependencies=[Depends(require_labops_admin)])


@router.get("", response_model=List[MemberResponse])
async def list_members():
    for m in service.members:
        m["one_time_package"] = service.ssh_access_manager.vault.get_availability(m.get("id", ""))
    return service.members


@router.get("/{user_id}", response_model=MemberResponse)
async def get_member(user_id: str):
    member = next((item for item in service.members if item["id"] == user_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    member["one_time_package"] = service.ssh_access_manager.vault.get_availability(member.get("id", ""))
    return member


@router.get("/{user_id}/wireguard/config")
@router.post("/{user_id}/wireguard/regenerate")
async def disabled_server_generated_profile(user_id: str):
    raise HTTPException(status_code=410, detail="Create the private key on the user device and enroll its public key")


@router.post("/{user_id}/wireguard/devices")
async def register_wireguard_device(
    user_id: str,
    payload: WireGuardDeviceCreateRequest,
    actor: str = Depends(require_labops_admin),
):
    member = next((item for item in service.members if item["id"] == user_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.get("workspace_status") != "VERIFIED":
        raise HTTPException(status_code=409, detail="Workspace provisioning is not verified")
    health = await service.get_access_health()
    if health["network_policy"] != "VERIFIED":
        raise HTTPException(status_code=409, detail="VPN-to-Teleport network policy is not verified")
    try:
        return service.register_wireguard_device(user_id, payload.public_key, payload.name, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/{user_id}/wireguard/devices/{device_id}")
async def revoke_wireguard_device(user_id: str, device_id: str, actor: str = Depends(require_labops_admin)):
    try:
        return service.revoke_wireguard_device(user_id, device_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{user_id}/wireguard/devices")
async def list_wireguard_devices(user_id: str):
    try:
        return service.refresh_wireguard_devices(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{user_id}/activation")
async def activate_teleport_user(user_id: str, actor: str = Depends(require_labops_admin)):
    member = next((item for item in service.members if item["id"] == user_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    health = await service.get_access_health()
    if member.get("workspace_status") != "VERIFIED" or health["network_policy"] != "VERIFIED":
        raise HTTPException(status_code=409, detail="Workspace and VPN network policy must be verified first")
    if health["teleport"].get("status") != "AVAILABLE":
        raise HTTPException(status_code=503, detail="Teleport Community is not verified as available")
    try:
        result = service.activate_teleport_user(user_id, actor=actor)
        return {"id": user_id, "status": result["status"]}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{user_id}/revoke")
async def revoke_member_access(user_id: str, actor: str = Depends(require_labops_admin)):
    try:
        result = await service.revoke_member_access(user_id, actor=actor)
        return {"id": user_id, "status": result["status"]}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        return {"status": "PARTIAL_FAILURE", "detail": str(exc), "id": user_id}


@router.post("/{user_id}/wireguard/revoke")
async def revoke_all_wireguard_devices(user_id: str, actor: str = Depends(require_labops_admin)):
    try:
        return service.revoke_wireguard(user_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{user_id}/wireguard/restore")
async def disabled_wireguard_restore(user_id: str):
    raise HTTPException(status_code=410, detail="A revoked device key cannot be restored; enroll a new device key")


@router.post("", response_model=MemberResponse)
async def create_member(payload: MemberCreateRequest, actor: str = Depends(require_labops_admin)):
    try:
        data = payload.model_dump(by_alias=False)
        data["_actor_id"] = actor
        return service.provision_member(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except WorkspaceSecurityError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.put("/{user_id}/status")
async def update_member_status(user_id: str, payload: Dict[str, str], actor: str = Depends(require_labops_admin)):
    if payload.get("status") != "SUSPENDED":
        raise HTTPException(status_code=409, detail="Only verified access revocation is supported; restore/archive are not automatic")
    try:
        result = await service.revoke_member_access(user_id, actor=actor)
        return {"success": result["status"] == "SUSPENDED", "id": user_id, "status": result["status"]}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        return {"success": False, "id": user_id, "status": "PARTIAL_FAILURE", "detail": str(exc)}


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    actor: str = Depends(require_labops_admin),
):
    try:
        result = service.delete_member_permanently(user_id, actor=actor)
        return {"success": True, "id": user_id, "result": result}
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi xóa tài khoản: {exc}") from exc
