from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
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
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    """Lấy danh sách tiến trình. Researcher chỉ xem được tiến trình của chính mình, không xem SYSTEM_PROCESS."""
    effective_user = username if p.is_admin else p.username
    effective_class = classification
    if not p.is_admin and classification in ("all", "SYSTEM_PROCESS"):
        effective_class = "workloads"

    items = service.list_processes(
        server_id=server_id,
        classification=effective_class,
        username=effective_user,
        workspace_id=workspace_id,
        gpu_id=gpu_id,
        search=search,
    )
    if not p.is_admin:
        items = [
            x
            for x in items
            if x.get("username") == p.username and x.get("classification") != "SYSTEM_PROCESS"
        ]

    return {
        "server_id": server_id or "all",
        "total_count": len(items),
        "items": items,
    }


@router.get("/{ref:path}/tree")
async def get_process_tree(ref: str, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    """Lấy cấu trúc cây tiến trình phân cấp (Parent-Child) cho một ProcessRef."""
    try:
        tree = service.get_process_tree(ref)
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except Exception as e:
        err_msg = str(e)
        if "PROCESS_NOT_FOUND" in err_msg:
            raise HTTPException(status_code=404, detail=err_msg)
        if "HOST_MISMATCH" in err_msg or "PID_RECYCLED" in err_msg:
            raise HTTPException(status_code=409, detail=err_msg)
        raise HTTPException(status_code=400, detail=err_msg)

    if not p.is_admin:
        root_owner = (tree.get("root") or {}).get("username") or tree.get("username")
        if root_owner != p.username:
            raise HTTPException(status_code=403, detail="Bạn chỉ được phép xem cây tiến trình do chính mình sở hữu")
    return tree


@router.post("/{ref:path}/stop")
async def terminate_process(
    ref: str,
    body: TerminateProcessRequest = Body(...),
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    """Dừng an toàn tiến trình (SIGTERM) với thời gian ân hạn."""
    if not p.is_admin:
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới có quyền dừng tiến trình")

    try:
        return service.terminate_process(
            ref=ref,
            timeout_seconds=body.timeout_seconds,
            terminate_tree=body.terminate_tree,
            actor=p.username,
            is_admin=p.is_admin,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except Exception as e:
        err_str = str(e)
        if "PROTECTED_PROCESS_DENIED" in err_str:
            raise HTTPException(status_code=403, detail=err_str)
        elif "PID_RECYCLED" in err_str or "HOST_MISMATCH" in err_str:
            raise HTTPException(status_code=409, detail=err_str)
        elif "PROCESS_NOT_FOUND" in err_str:
            raise HTTPException(status_code=404, detail=err_str)
        raise HTTPException(status_code=400, detail=err_str)


@router.post("/{ref:path}/kill")
async def kill_process(
    ref: str,
    body: KillProcessRequest = Body(...),
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    """Buộc dừng cưỡng chế tiến trình (SIGKILL) với xác nhận force_confirmed."""
    if not p.is_admin:
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới có quyền buộc dừng tiến trình")
    if not body.force_confirmed:
        raise HTTPException(status_code=400, detail="Cần xác nhận force_confirmed: true để buộc dừng tiến trình")

    try:
        return service.kill_process(
            ref=ref,
            force=body.force_confirmed,
            kill_tree=body.kill_tree,
            actor=p.username,
            is_admin=p.is_admin,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except Exception as e:
        err_str = str(e)
        if "PROTECTED_PROCESS_DENIED" in err_str:
            raise HTTPException(status_code=403, detail=err_str)
        elif "PID_RECYCLED" in err_str or "HOST_MISMATCH" in err_str:
            raise HTTPException(status_code=409, detail=err_str)
        elif "PROCESS_NOT_FOUND" in err_str:
            raise HTTPException(status_code=404, detail=err_str)
        raise HTTPException(status_code=400, detail=err_str)
