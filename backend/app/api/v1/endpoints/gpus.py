from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.api.dependencies import require_agent, require_user
from app.core.security import Principal
from app.schemas.labops import GpuAllocateRequest, PhysicalGpuResponse
from app.services.gpu_scheduler import GpuScheduler, SchedulerError
from app.services.labops_service import service

router = APIRouter()


class AgentConfirmRequest(BaseModel):
    observed_state: str
    pid: Optional[int] = None


@router.get("", response_model=List[PhysicalGpuResponse])
async def list_gpus(p: Principal = Depends(require_user)):
    """Danh sách GPU quan sát được động qua NVML trên mọi host đã đăng ký."""
    return service.gpus


@router.get("/allocations/by-gpu", response_model=List[Dict[str, Any]])
async def list_gpu_allocations_by_device(p: Principal = Depends(require_user)):
    """Thống kê danh sách tài khoản được cấp quyền nhóm theo từng thiết bị GPU vật lý."""
    return service.get_gpu_allocations_by_device()


@router.get("/allocations", response_model=List[Dict[str, Any]])
async def list_gpu_allocations(p: Principal = Depends(require_user)):
    """Danh sách thống kê tài khoản nào đang được cấp quyền cho GPU nào."""
    return service.get_gpu_account_allocations()


@router.get("/reservations/me")
async def my_reservations(p: Principal = Depends(require_user)) -> List[Dict[str, Any]]:
    owner = None if p.is_admin else p.username
    return [{**r, **GpuScheduler.gpu_hours(r)} for r in service.gpu_scheduler.list_reservations(owner)]


@router.get("/{gpu_id}", response_model=PhysicalGpuResponse)
async def get_gpu(gpu_id: str, p: Principal = Depends(require_user)):
    """Get real-time telemetry, temperature, power, and processes for a specific GPU."""
    gpu = next((g for g in service.gpus if g["id"] == gpu_id), None)
    if not gpu:
        raise HTTPException(status_code=404, detail="GPU not found")
    return gpu


@router.post("/allocate")
async def allocate_gpu(
    payload: GpuAllocateRequest,
    p: Principal = Depends(require_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Dict[str, Any]:
    """Giữ độc quyền một GPU (FULL_GPU_EXCLUSIVE). Researcher chỉ được giữ GPU mình có quyền."""
    owner = payload.username if p.is_admin and payload.username else p.username
    try:
        return service.allocate_gpu(
            gpu_id=payload.gpu_id,
            username=owner,
            mode=payload.mode,
            requested_vram_gb=payload.requested_vram_gb,
            job_name=payload.job_name,
            actor=p.username,
            idempotency_key=idempotency_key,
            ttl_minutes=payload.ttl_minutes,
            is_admin=p.is_admin,
            enforce_permissions=not p.is_admin,
        )
    except SchedulerError as e:
        raise HTTPException(status_code=e.http_status, detail={"code": e.code, "message": e.message})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reservations/{reservation_id}/release")
async def release_reservation(reservation_id: str, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    try:
        res = service.stop_job(reservation_id, actor=p.username, is_admin=p.is_admin)
        service._save_persistent_data()
        return res
    except SchedulerError as e:
        raise HTTPException(status_code=e.http_status, detail={"code": e.code, "message": e.message})


@router.post("/reservations/{reservation_id}/agent-confirm")
async def agent_confirm_reservation(
    reservation_id: str,
    payload: AgentConfirmRequest,
    p: Principal = Depends(require_agent),
) -> Dict[str, Any]:
    try:
        r = service.gpu_scheduler.confirm_from_agent(
            reservation_id, observed_state=payload.observed_state, pid=payload.pid
        )
        service._audit("node-agent", "GPU_AGENT_CONFIRM", "RESERVATION", reservation_id, r["state"], f"Observed {payload.observed_state} pid={payload.pid}")
        service._save_persistent_data()
        return r
    except SchedulerError as e:
        raise HTTPException(status_code=e.http_status, detail={"code": e.code, "message": e.message})
