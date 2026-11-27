from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.dependencies import require_admin, require_user
from app.core.security import Principal
from app.schemas.workload_v31 import ProcessActionResultEnvelope, V31ProcessActionRequest
from app.services.labops_service import service
from app.services.workload_intelligence_service import get_workload_intelligence_service

router = APIRouter()


def _filter_tree_for_owner(node: Dict[str, Any], owner_username: str) -> Optional[Dict[str, Any]]:
    """Strip foreign-owned and system/protected descendants from process trees for non-admin callers."""
    if not isinstance(node, dict):
        return None
    node_owner = node.get("user") or node.get("username")
    is_protected = bool(node.get("is_protected") or node.get("classification") == "SYSTEM_PROCESS")
    if node_owner != owner_username or is_protected:
        return None
    clean = dict(node)
    raw_children = node.get("children") or []
    filtered_children = []
    for ch in raw_children:
        c_clean = _filter_tree_for_owner(ch, owner_username)
        if c_clean is not None:
            filtered_children.append(c_clean)
    clean["children"] = filtered_children
    return clean


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
        root_owner = (tree.get("root") or {}).get("username") or tree.get("username") or tree.get("user")
        if root_owner != p.username:
            raise HTTPException(status_code=403, detail="Bạn chỉ được phép xem cây tiến trình do chính mình sở hữu")
        if "children" in tree and isinstance(tree["children"], list):
            filtered_list = []
            for ch in tree["children"]:
                c_user = ch.get("username") or ch.get("user")
                if c_user == p.username and not ch.get("is_protected") and ch.get("classification") != "SYSTEM_PROCESS":
                    if "children" in ch:
                        c_clean = _filter_tree_for_owner(ch, p.username)
                        if c_clean is not None:
                            filtered_list.append(c_clean)
                    else:
                        filtered_list.append(ch)
            tree = dict(tree)
            tree["children"] = filtered_list
    return tree


@router.post("/{ref:path}/v31-action", response_model=ProcessActionResultEnvelope)
async def execute_v31_action(
    ref: str,
    body: V31ProcessActionRequest = Body(...),
    p: Principal = Depends(require_admin),
) -> ProcessActionResultEnvelope:
    """Hardened V3.1 process action with boot_id + pidfd + durable idempotency (Admin only)."""
    wk_svc = get_workload_intelligence_service(service)
    try:
        return wk_svc.execute_v31_process_action(
            ref=ref,
            req=body,
            actor=p.username,
            is_admin=p.is_admin,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe)) from pe
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le)) from le
    except ValueError as ve:
        msg = str(ve)
        if "PID_RECYCLED" in msg or "BOOT_IDENTITY_MISMATCH" in msg:
            raise HTTPException(status_code=409, detail=msg) from ve
        raise HTTPException(status_code=422, detail=msg) from ve
    except RuntimeError as re_err:
        msg = str(re_err)
        if "IDEMPOTENCY_CONFLICT" in msg or "ACTION_IN_FLIGHT" in msg:
            raise HTTPException(status_code=409, detail=msg) from re_err
        if "FAIL_CLOSED" in msg:
            raise HTTPException(status_code=503, detail=msg) from re_err
        raise HTTPException(status_code=400, detail=msg) from re_err


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
