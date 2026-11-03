from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from app.schemas.labops import JobResponse
from app.services.labops_service import service

router = APIRouter()

@router.get("", response_model=List[JobResponse])
async def list_jobs():
    """List all running and historical compute jobs across the two physical servers"""
    return service.jobs

@router.post("/{job_id}/stop")
async def stop_job(job_id: str) -> Dict[str, Any]:
    """Gracefully terminate a running compute job (SIGTERM -> SIGKILL) and release its GPU memory"""
    try:
        result = service.stop_job(job_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

