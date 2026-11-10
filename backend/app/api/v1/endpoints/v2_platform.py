from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException
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
        return service.ssh_key_registry.register_key(
            username=owner,
            label=str(payload.get("label", "Khóa cá nhân")),
            public_key=str(payload.get("public_key", "")),
            actor=p.username,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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

