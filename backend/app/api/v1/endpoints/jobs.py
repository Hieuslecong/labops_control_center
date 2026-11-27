from fastapi import APIRouter, Depends, HTTPException, Query, Response
from typing import List, Dict, Any, Optional
from app.api.dependencies import require_admin, require_user
from app.core.security import Principal
from app.schemas.labops import JobResponse
from app.schemas.workload_v31 import (
    WorkloadAnalyticsEnvelope,
    WorkloadDetailResponse,
    WorkloadEventsEnvelope,
    WorkloadHistoryEnvelope,
    WorkloadSnapshotEnvelope,
)
from app.services.gpu_scheduler import SchedulerError
from app.services.job_access import visible_jobs
from app.services.labops_service import service
from app.services.workload_intelligence_service import (
    export_workloads_csv,
    get_workload_intelligence_service,
)

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
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm PID hoặc lệnh"),
    p: Principal = Depends(require_user)
):
    """List all running and historical compute jobs across the two physical servers"""
    return _filter_jobs(visible_jobs(service.jobs, p), resource=resource, username=username, server_id=server_id, search=search)

@router.get("/active", response_model=List[JobResponse])
async def list_active_jobs(
    resource: Optional[str] = Query(None, description="all | gpu | cpu"),
    username: Optional[str] = Query(None, description="Lọc theo Linux username"),
    server_id: Optional[str] = Query(None, description="workstation | track"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm PID hoặc lệnh"),
    p: Principal = Depends(require_user)
):
    """List real currently executing compute jobs across all physical servers"""
    return _filter_jobs(visible_jobs(service.jobs, p), resource=resource, username=username, server_id=server_id, search=search)


@router.get("/snapshot", response_model=WorkloadSnapshotEnvelope)
async def get_workload_snapshot(
    node_id: Optional[str] = Query(None, description="Lọc phạm vi node (workstation | track)"),
    gpu_uuid: Optional[str] = Query(None, description="Lọc phạm vi GPU UUID"),
    member_id: Optional[str] = Query(None, description="Lọc workload theo thành viên"),
    group_id: Optional[str] = Query(None, description="Lọc workload theo nhóm nghiên cứu"),
    _p: Principal = Depends(require_admin),
):
    """Canonical versioned V3.1 live workload & device snapshot (Admin only)."""
    wk_svc = get_workload_intelligence_service(service)
    try:
        return wk_svc.get_snapshot(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            member_id=member_id,
            group_id=group_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/snapshot/export.csv")
async def export_workload_snapshot_csv(
    node_id: Optional[str] = Query(None),
    gpu_uuid: Optional[str] = Query(None),
    member_id: Optional[str] = Query(None),
    group_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=200),
    _p: Principal = Depends(require_admin),
):
    """Safe formula-escaped scoped CSV export of observed workloads without raw secrets/cmdlines."""
    wk_svc = get_workload_intelligence_service(service)
    try:
        snap = wk_svc.get_snapshot(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            member_id=member_id,
            group_id=group_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    rows = list(snap.workloads)
    if search and search.strip():
        q = search.strip().lower()
        rows = [
            w
            for w in rows
            if q in str(w.host_pid)
            or q in w.display_name.lower()
            or q in (w.username or "").lower()
            or q in (w.workspace or "").lower()
            or q in w.workload_kind.lower()
        ]
    csv_body = export_workloads_csv(rows, scope=snap.scope)
    return Response(
        content=csv_body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="labops-workloads-v31.csv"',
            "Cache-Control": "no-store, no-cache, must-revalidate",
        },
    )


@router.get("/analytics", response_model=WorkloadAnalyticsEnvelope)
async def get_workload_analytics(
    node_id: Optional[str] = Query(None),
    gpu_uuid: Optional[str] = Query(None),
    member_id: Optional[str] = Query(None),
    group_id: Optional[str] = Query(None),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    bucket_seconds: Optional[int] = Query(None, alias="bucket"),
    _p: Principal = Depends(require_admin),
):
    """Read-only historical analytics over existing telemetry_history_samples (Admin only)."""
    wk_svc = get_workload_intelligence_service(service)
    try:
        return wk_svc.get_analytics(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            member_id=member_id,
            group_id=group_id,
            from_ts=from_ts,
            to_ts=to_ts,
            bucket_seconds=bucket_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/history", response_model=WorkloadHistoryEnvelope)
async def get_workload_history(
    node_id: Optional[str] = Query(None),
    gpu_uuid: Optional[str] = Query(None),
    member_id: Optional[str] = Query(None),
    group_id: Optional[str] = Query(None),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    cursor: Optional[str] = Query(None, max_length=256),
    limit: int = Query(50, ge=1, le=100),
    _p: Principal = Depends(require_admin),
):
    """Read-only paginated observed workload intervals from telemetry_history_samples (Admin only)."""
    wk_svc = get_workload_intelligence_service(service)
    try:
        return wk_svc.get_history(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            member_id=member_id,
            group_id=group_id,
            from_ts=from_ts,
            to_ts=to_ts,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/events", response_model=WorkloadEventsEnvelope)
async def get_workload_events(
    node_id: Optional[str] = Query(None),
    gpu_uuid: Optional[str] = Query(None),
    member_id: Optional[str] = Query(None),
    group_id: Optional[str] = Query(None),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    cursor: Optional[str] = Query(None, max_length=256),
    limit: int = Query(50, ge=1, le=100),
    _p: Principal = Depends(require_admin),
):
    """Read-only verified audit/lifecycle events (Admin only)."""
    wk_svc = get_workload_intelligence_service(service)
    try:
        return wk_svc.get_events(
            node_id=node_id,
            gpu_uuid=gpu_uuid,
            member_id=member_id,
            group_id=group_id,
            from_ts=from_ts,
            to_ts=to_ts,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{workload_id}/detail", response_model=WorkloadDetailResponse)
async def get_workload_detail(
    workload_id: str,
    _p: Principal = Depends(require_admin),
):
    """Sanitized workload detail view by immutable workload ID or ProcessRef (Admin only)."""
    wk_svc = get_workload_intelligence_service(service)
    try:
        return wk_svc.get_workload_detail(workload_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/{job_id}/stop")
async def stop_job(job_id: str, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    """Yêu cầu dừng tác vụ; chỉ chủ sở hữu hoặc quản trị viên. Kết quả phản ánh trạng thái xác minh thật."""
    try:
        return service.stop_job(job_id, actor=p.username, is_admin=p.is_admin)
    except SchedulerError as e:
        raise HTTPException(status_code=e.http_status, detail={"code": e.code, "message": e.message})
