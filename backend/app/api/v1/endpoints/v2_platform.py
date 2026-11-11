from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from app.api.dependencies import require_admin, require_agent, require_user
from app.core.security import Principal
from app.services.labops_service import service
from app.services.workspace_controller import WorkspaceSecurityError

router = APIRouter()


@router.get("/hosts")
async def list_v2_hosts(p: Principal = Depends(require_user)) -> List[Dict[str, Any]]:
    return service.get_v2_hosts()


@router.post("/hosts")
async def register_v2_host(payload: Dict[str, Any], p: Principal = Depends(require_admin)) -> Dict[str, Any]:
    try:
        return service.host_registry.register_host(
            host_id=str(payload.get("host_id", "")),
            hostname=str(payload.get("hostname", "")),
            display_name=str(payload.get("display_name", "")),
            management_ip=str(payload.get("management_ip", "")),
            role=str(payload.get("role", "COMPUTE_NODE")),
            vpn_ip=payload.get("vpn_ip"),
            agent_url=payload.get("agent_url"),
            ssh_legacy_port=int(payload.get("ssh_legacy_port", 22)),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/hosts/{host_id}/agent/heartbeat")
async def record_agent_heartbeat(
    host_id: str,
    payload: Dict[str, Any],
    p: Principal = Depends(require_agent),
) -> Dict[str, Any]:
    try:
        return service.host_registry.record_agent_heartbeat(host_id, payload)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/containers/summary")
async def get_arcane_containers_summary(p: Principal = Depends(require_admin)) -> Dict[str, Any]:
    """Chỉ Admin mới được xem Arcane Docker Management; Researcher không bao giờ có quyền."""
    return service.arcane_adapter.get_integration_summary()


@router.get("/monitoring/beszel")
async def get_beszel_monitoring(p: Principal = Depends(require_user)) -> Dict[str, Any]:
    return service.beszel_adapter.get_monitoring_overview(live_hosts=service.get_v2_hosts())


@router.get("/workspaces")
async def list_v2_workspaces(p: Principal = Depends(require_user)) -> List[Dict[str, Any]]:
    owner = None if p.is_admin else p.username
    return service.workspace_controller.list_workspaces(owner=owner, legacy_endpoints=service.ssh_endpoints)


@router.post("/workspaces")
async def create_v2_workspace(
    payload: Dict[str, Any],
    p: Principal = Depends(require_admin),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Dict[str, Any]:
    try:
        return service.workspace_controller.request_workspace(
            owner=str(payload.get("owner", "")),
            host_id=str(payload.get("host_id", "workstation")),
            ssh_public_key=str(payload.get("ssh_public_key", "")),
            image_ref=str(payload.get("image_ref", "nvcr.io/nvidia/pytorch:24.08-py3")),
            cpu_cores=int(payload.get("cpu_cores", 8)),
            ram_mb=int(payload.get("ram_mb", 32768)),
            pids_limit=int(payload.get("pids_limit", 4096)),
            storage_quota_gb=int(payload.get("storage_quota_gb", 200)),
            gpu_uuid=payload.get("gpu_uuid"),
            privileged=bool(payload.get("privileged", False)),
            mount_docker_socket=bool(payload.get("mount_docker_socket", False)),
            extra_mounts=payload.get("extra_mounts"),
            idempotency_key=idempotency_key,
        )
    except WorkspaceSecurityError as e:
        raise HTTPException(status_code=e.http_status, detail={"code": e.code, "message": e.message})


@router.get("/ssh-keys")
async def list_ssh_keys(p: Principal = Depends(require_user)) -> List[Dict[str, Any]]:
    owner = None if p.is_admin else p.username
    return service.ssh_key_registry.list_keys(username=owner)


@router.post("/ssh-keys")
async def register_ssh_key(payload: Dict[str, Any], p: Principal = Depends(require_user)) -> Dict[str, Any]:
    owner = str(payload.get("username") or p.username) if p.is_admin else p.username
    try:
        return service.enroll_member_ssh_key(
            username=owner,
            label=str(payload.get("label", "Khóa cá nhân")),
            public_key=str(payload.get("public_key", "")),
            actor=p.username,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except WorkspaceSecurityError as e:
        raise HTTPException(status_code=e.http_status, detail=e.message)


@router.post("/ssh-keys/{key_id}/revoke")
async def revoke_ssh_key_v2(key_id: str, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    try:
        return service.ssh_key_registry.revoke_key(key_id, actor=p.username, is_admin=p.is_admin)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/researcher/me")
async def get_researcher_portal(p: Principal = Depends(require_user)) -> Dict[str, Any]:
    return service.get_researcher_portal_snapshot(p.username)


@router.get("/reconcile")
async def reconcile_stores(p: Principal = Depends(require_admin)) -> Dict[str, Any]:
    return service.host_registry.reconcile_json_stores()


# ------------------------------------------------------------------
# Admin-Managed Member SSH Lifecycle Endpoints (/api/v1/v2/ssh-access/*)
# ------------------------------------------------------------------
@router.post("/ssh-access/provision")
async def provision_ssh_access(payload: Dict[str, Any], p: Principal = Depends(require_admin)) -> Dict[str, Any]:
    try:
        data = dict(payload)
        data["_actor_id"] = p.username
        return service.provision_member_ssh_access(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except WorkspaceSecurityError as e:
        raise HTTPException(status_code=e.http_status, detail=e.message)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/ssh-access/{member_id}/status")
async def get_ssh_access_status(member_id: str, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    try:
        status = service.get_member_ssh_access_status(member_id)
        if not p.is_admin and status["username"] != p.username:
            raise HTTPException(status_code=403, detail="Bạn chỉ được xem thông tin truy cập SSH của chính mình")
        return status
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/ssh-access/{member_id}/download-zip")
async def download_ssh_access_zip(member_id: str, p: Principal = Depends(require_user)) -> Response:
    try:
        status = service.get_member_ssh_access_status(member_id)
        if not p.is_admin and status["username"] != p.username:
            raise HTTPException(status_code=403, detail="Chỉ admin hoặc chủ sở hữu tài khoản mới có quyền tải bộ kết nối")
        filename, zip_bytes = service.claim_member_access_zip(member_id, actor=p.username)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi đóng gói file ZIP: {e}")


@router.post("/ssh-access/{member_id}/claim-key-bundle")
async def claim_ssh_key_bundle(member_id: str, p: Principal = Depends(require_admin)) -> Dict[str, Any]:
    try:
        return service.claim_member_key_bundle(member_id, actor=p.username)
    except LookupError as e:
        raise HTTPException(status_code=410, detail=str(e))


@router.post("/ssh-access/{member_id}/claim-secrets")
async def claim_ssh_channel_secrets(member_id: str, p: Principal = Depends(require_admin)) -> Dict[str, Any]:
    try:
        return service.claim_member_secrets(member_id, actor=p.username)
    except LookupError as e:
        raise HTTPException(status_code=410, detail=str(e))


@router.post("/ssh-access/{member_id}/rotate-key")
async def rotate_ssh_keypair(
    member_id: str,
    payload: Optional[Dict[str, Any]] = None,
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    require_passphrase = bool((payload or {}).get("require_passphrase", False))
    try:
        return service.rotate_member_ssh_keypair(
            member_id, actor=p.username, require_passphrase=require_passphrase
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/ssh-access/{member_id}/reissue-temp-password")
async def reissue_temp_password(
    member_id: str,
    payload: Optional[Dict[str, Any]] = None,
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    ttl = int((payload or {}).get("ttl_seconds", 86400))
    try:
        return service.reissue_member_temp_password(member_id, actor=p.username, ttl_seconds=ttl)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/ssh-access/{member_id}/revoke")
async def revoke_ssh_access(member_id: str, p: Principal = Depends(require_admin)) -> Dict[str, Any]:
    try:
        return service.revoke_member_ssh_access(member_id, actor=p.username)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/ssh-access/{member_id}/terminate-sessions")
async def terminate_ssh_sessions(
    member_id: str,
    payload: Dict[str, Any],
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        return service.terminate_member_ssh_sessions(
            member_id, actor=p.username, confirm=bool(payload.get("confirm", False))
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/ssh-access/first-login-change-password")
async def first_login_change_password(
    payload: Dict[str, Any],
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    target_user = str(payload.get("username") or p.username).strip()
    if not p.is_admin and target_user != p.username:
        raise HTTPException(status_code=403, detail="Bạn chỉ được đổi mật khẩu của chính mình")
    try:
        return service.change_member_first_login_password(
            username=target_user,
            temporary_password=str(payload.get("temporary_password") or payload.get("temporaryPassword") or ""),
            new_password=str(payload.get("new_password") or payload.get("newPassword") or ""),
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/ssh-access/verify-login")
async def verify_workspace_login(
    payload: Dict[str, Any],
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    target_user = str(payload.get("username") or p.username).strip()
    if not p.is_admin and target_user != p.username:
        raise HTTPException(status_code=403, detail="Bạn chỉ được kiểm tra đăng nhập của chính mình")
    try:
        return service.verify_member_workspace_password(
            username=target_user,
            password=str(payload.get("password") or ""),
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


# ------------------------------------------------------------------
# VPN Administration & Multi-Device Management (/api/v1/v2/vpn/*)
# ------------------------------------------------------------------
@router.get("/vpn/overview")
async def get_vpn_overview(p: Principal = Depends(require_user)) -> Dict[str, Any]:
    return service.get_vpn_system_overview()


@router.get("/vpn/devices")
async def list_all_vpn_devices(p: Principal = Depends(require_admin)) -> List[Dict[str, Any]]:
    return service.list_all_vpn_devices()


@router.get("/members/{member_id}/devices")
async def get_member_devices(member_id: str, p: Principal = Depends(require_user)) -> List[Dict[str, Any]]:
    member = service._find_member(member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
    if not p.is_admin and member.get("username") != p.username:
        raise HTTPException(status_code=403, detail="Bạn chỉ được xem thiết bị của chính mình")
    try:
        return service.get_member_devices_synced(member["id"])
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/members/{member_id}/devices")
async def add_member_device(
    member_id: str,
    payload: Dict[str, Any],
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    device_name = str(payload.get("name") or payload.get("device_name") or "New Device").strip()
    try:
        return service.add_member_device(member_id, device_name, actor=p.username)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/members/{member_id}/devices/{device_id}/config")
async def get_member_device_config(
    member_id: str,
    device_id: str,
    p: Principal = Depends(require_user),
) -> Response:
    member = service._find_member(member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
    if not p.is_admin and member.get("username") != p.username:
        raise HTTPException(status_code=403, detail="Chỉ admin hoặc chủ sở hữu mới có quyền lấy cấu hình")
    try:
        conf_text = service.get_member_device_config(member["id"], device_id)
        filename = f"{member['username']}-{device_id}.conf"
        return Response(
            content=conf_text,
            media_type="text/plain",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/members/{member_id}")
async def delete_member_v2(
    member_id: str,
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        return service.delete_member_permanently(member_id, actor=p.username)
    except (LookupError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi xóa thành viên: {e}")


@router.delete("/members/{member_id}/devices/{device_id}")
async def revoke_member_device_v2(
    member_id: str,
    device_id: str,
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        return service.revoke_member_device_v2(member_id, device_id, actor=p.username)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/members/{member_id}/devices/{device_id}/toggle")
async def toggle_member_device(
    member_id: str,
    device_id: str,
    payload: Dict[str, Any],
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    enable = bool(payload.get("enable", True))
    try:
        return service.toggle_member_device(member_id, device_id, enable=enable, actor=p.username)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


