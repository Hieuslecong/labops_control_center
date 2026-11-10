from fastapi import Depends
from app.api.dependencies import require_user
from app.core.security import Principal
from app.services.gpu_scheduler import SchedulerError
from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any, Optional
from app.schemas.labops import JobResponse
from app.services.labops_service import service

router = APIRouter()

def _filter_jobs(
    jobs: List[Dict[str, Any]],
    resource: Optional[str] = None,
    username: Optional[str] = None,
    server_id: Optional[str] = None,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    filtered = jobs
    if server_id and server_id != "all":
        filtered = [j for j in filtered if j.get("serverId") == server_id or j.get("server_id") == server_id]
    if username and username != "all":
        filtered = [j for j in filtered if j.get("username") == username]
    if resource == "gpu":
        filtered = [j for j in filtered if j.get("isGpu") or j.get("is_gpu") or (j.get("vramUsedMb") or 0) > 0]
    elif resource == "cpu":
        filtered = [j for j in filtered if not (j.get("isGpu") or j.get("is_gpu")) and (j.get("vramUsedMb") or 0) <= 0]
    if search:
        q = search.lower()
        filtered = [
            j for j in filtered
            if q in str(j.get("pid", "")).lower()
            or q in str(j.get("script", "")).lower()
            or q in str(j.get("cmdline", "")).lower()
            or q in str(j.get("username", "")).lower()
            or q in str(j.get("gpuId", "")).lower()
        ]
    return filtered

@router.get("", response_model=List[JobResponse])
async def list_jobs(
    resource: Optional[str] = Query(None, description="all | gpu | cpu"),
    username: Optional[str] = Query(None, description="Lọc theo Linux username"),
    server_id: Optional[str] = Query(None, description="workstation | track"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm PID hoặc lệnh")
):
    """List all running and historical compute jobs across the two physical servers"""
    return _filter_jobs(service.jobs, resource=resource, username=username, server_id=server_id, search=search)

@router.get("/active", response_model=List[JobResponse])
async def list_active_jobs(
    resource: Optional[str] = Query(None, description="all | gpu | cpu"),
    username: Optional[str] = Query(None, description="Lọc theo Linux username"),
    server_id: Optional[str] = Query(None, description="workstation | track"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm PID hoặc lệnh")
):
    """List real currently executing compute jobs across all physical servers"""
    return _filter_jobs(service.jobs, resource=resource, username=username, server_id=server_id, search=search)

@router.post("/{job_id}/stop")
async def stop_job(job_id: str, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    """Yêu cầu dừng tác vụ; chỉ chủ sở hữu hoặc quản trị viên. Kết quả phản ánh trạng thái xác minh thật."""
    try:
        return service.stop_job(job_id, actor=p.username, is_admin=p.is_admin)
    except SchedulerError as e:
        raise HTTPException(status_code=e.http_status, detail={"code": e.code, "message": e.message})
