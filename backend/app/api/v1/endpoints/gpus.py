from fastapi import APIRouter, Depends, Header, HTTPException
from typing import Optional
from app.api.dependencies import require_user
from app.core.security import Principal
from app.services.gpu_scheduler import SchedulerError, GpuScheduler
from typing import List, Dict, Any
from app.schemas.labops import PhysicalGpuResponse, GpuAllocateRequest
from app.services.labops_service import service

router = APIRouter()

@router.get("", response_model=List[PhysicalGpuResponse])
async def list_gpus(p: Principal = Depends(require_user)):
    """Danh sách GPU quan sát được động qua NVML trên mọi host đã đăng ký"""
    return service.gpus

@router.get("/{gpu_id}", response_model=PhysicalGpuResponse)
async def get_gpu(gpu_id: str, p: Principal = Depends(require_user)):
    """Get real-time telemetry, temperature, power, and processes for a specific GPU"""
    gpu = next((g for g in service.gpus if g["id"] == gpu_id), None)
    if not gpu:
        raise HTTPException(status_code=404, detail="GPU not found")
    return gpu

@router.post("/allocate")
async def allocate_gpu(payload: GpuAllocateRequest, p: Principal = Depends(require_user),
                       idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> Dict[str, Any]:
    """Giữ độc quyền một GPU (FULL_GPU_EXCLUSIVE). Researcher chỉ được giữ cho chính mình."""
    owner = payload.username if p.is_admin and payload.username else p.username
    try:
        return service.allocate_gpu(gpu_id=payload.gpu_id, username=owner, mode=payload.mode,
                                    job_name=payload.job_name, actor=p.username,
                                    idempotency_key=idempotency_key)
    except SchedulerError as e:
        raise HTTPException(status_code=e.http_status, detail={"code": e.code, "message": e.message})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/reservations/me")
async def my_reservations(p: Principal = Depends(require_user)) -> List[Dict[str, Any]]:
    owner = None if p.is_admin else p.username
    return [{**r, **GpuScheduler.gpu_hours(r)} for r in service.gpu_scheduler.list_reservations(owner)]
