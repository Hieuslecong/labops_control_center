from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from app.schemas.labops import PhysicalGpuResponse, GpuAllocateRequest
from app.services.labops_service import service

router = APIRouter()

@router.get("", response_model=List[PhysicalGpuResponse])
async def list_gpus():
    """List all 4 physical NVIDIA A30 24GB GPUs across the 2 servers"""
    return service.gpus

@router.get("/{gpu_id}", response_model=PhysicalGpuResponse)
async def get_gpu(gpu_id: str):
    """Get real-time telemetry, temperature, power, and processes for a specific GPU"""
    gpu = next((g for g in service.gpus if g["id"] == gpu_id), None)
    if not gpu:
        raise HTTPException(status_code=404, detail="GPU not found")
    return gpu

@router.post("/allocate")
async def allocate_gpu(payload: GpuAllocateRequest) -> Dict[str, Any]:
    """Allocate VRAM on a specific GPU for a researcher workload"""
    try:
        result = service.allocate_gpu(
            gpu_id=payload.gpu_id,
            username=payload.username,
            mode=payload.mode,
            requested_vram_gb=payload.requested_vram_gb,
            job_name=payload.job_name
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

