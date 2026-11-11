from fastapi import APIRouter, Depends, HTTPException, Query, Body
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from app.api.dependencies import require_user
from app.core.security import Principal
from app.services.labops_service import service

router = APIRouter()

class TerminateProcessRequest(BaseModel):
    reason: Optional[str] = "Admin yêu cầu dừng tiến trình"
    terminate_tree: bool = True
    timeout_seconds: int = 10

class KillProcessRequest(BaseModel):
    reason: Optional[str] = "Admin yêu cầu buộc dừng tiến trình"
    force_confirmed: bool
    kill_tree: bool = True

@router.get("")
async def list_processes(
    server_id: Optional[str] = Query(None, description="workstation | track | all"),
    classification: Optional[str] = Query(None, description="all | RESEARCH_JOB | GPU_PROCESS | MEMBER_PROCESS | SYSTEM_PROCESS"),
    username: Optional[str] = Query(None, description="Lọc theo Linux username"),
    workspace_id: Optional[str] = Query(None, description="Lọc theo container/workspace ID"),
    gpu_id: Optional[str] = Query(None, description="Lọc theo GPU index hoặc UUID"),
    search: Optional[str] = Query(None, description="Tìm kiếm lệnh, PID, người dùng"),
    p: Principal = Depends(require_user)
) -> Dict[str, Any]:
    """
    Lấy danh sách tiến trình thời gian thực từ Go Node Agent.
    Hỗ trợ lọc theo server, thành viên, workspace, GPU và phân loại 4 nhóm.
    """
    items = service.list_processes(
        server_id=server_id,
        classification=classification,
        username=username,
        workspace_id=workspace_id,
        gpu_id=gpu_id,
        search=search
    )
    return {
        "server_id": server_id or "all",
        "total_count": len(items),
        "items": items
    }

@router.get("/{ref:path}/tree")
async def get_process_tree(
    ref: str,
    p: Principal = Depends(require_user)
) -> Dict[str, Any]:
    """Lấy cấu trúc cây tiến trình phân cấp (Parent-Child) cho một ProcessRef."""
    try:
        return service.get_process_tree(ref)
    except Exception as e:
        err_msg = str(e)
        if "PROCESS_NOT_FOUND" in err_msg:
            raise HTTPException(status_code=404, detail=err_msg)
        raise HTTPException(status_code=400, detail=err_msg)

@router.post("/{ref:path}/stop")
async def terminate_process(
    ref: str,
    body: TerminateProcessRequest = Body(...),
    p: Principal = Depends(require_user)
) -> Dict[str, Any]:
    """
    Dừng an toàn tiến trình (SIGTERM) với thời gian ân hạn 10s.
    Yêu cầu quyền Quản trị viên (Admin).
    """
    if not p.is_admin:
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới có quyền dừng tiến trình")

    try:
        res = service.terminate_process(
            ref=ref,
            timeout_seconds=body.timeout_seconds,
            terminate_tree=body.terminate_tree,
            actor=p.username,
            is_admin=p.is_admin
        )
        return res
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        err_str = str(e)
        if "PROTECTED_PROCESS_DENIED" in err_str:
            raise HTTPException(status_code=403, detail=err_str)
        elif "PID_RECYCLED" in err_str:
            raise HTTPException(status_code=409, detail=err_str)
        elif "PROCESS_NOT_FOUND" in err_str:
            raise HTTPException(status_code=404, detail=err_str)
        raise HTTPException(status_code=400, detail=err_str)

@router.post("/{ref:path}/kill")
async def kill_process(
    ref: str,
    body: KillProcessRequest = Body(...),
    p: Principal = Depends(require_user)
) -> Dict[str, Any]:
    """
    Buộc dừng cưỡng chế tiến trình (SIGKILL).
    Yêu cầu quyền Quản trị viên và xác nhận rủi ro force_confirmed == True.
    """
    if not p.is_admin:
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới có quyền buộc dừng tiến trình")
    if not body.force_confirmed:
        raise HTTPException(status_code=400, detail="Cần xác nhận force_confirmed: true để buộc dừng tiến trình")

    try:
        res = service.kill_process(
            ref=ref,
            force=body.force_confirmed,
            kill_tree=body.kill_tree,
            actor=p.username,
            is_admin=p.is_admin
        )
        return res
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        err_str = str(e)
        if "PROTECTED_PROCESS_DENIED" in err_str:
            raise HTTPException(status_code=403, detail=err_str)
        elif "PID_RECYCLED" in err_str:
            raise HTTPException(status_code=409, detail=err_str)
        elif "PROCESS_NOT_FOUND" in err_str:
            raise HTTPException(status_code=404, detail=err_str)
        raise HTTPException(status_code=400, detail=err_str)
