import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.dependencies import require_admin, require_user
from app.core.security import Principal
from app.services.labops_service import service

router = APIRouter()

AGENT_BASE_URL = os.getenv("LABOPS_AGENT_URL", "http://127.0.0.1:9100/api/v1/agent/repositoryguard")
AGENT_TOKEN = os.getenv("LABOPS_AGENT_SECRET", "labops-local-agent-verified-token-2026")


def _agent_call(path: str, method: str = "GET", data: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Any:
    url = f"{AGENT_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    headers = {
        "X-LabOps-Agent-Secret": AGENT_TOKEN,
        "Content-Type": "application/json"
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            if content:
                return json.loads(content)
            return {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            parsed = json.loads(err_body)
            detail = parsed.get("error") or parsed.get("detail") or err_body
        except Exception:
            detail = err_body
        raise HTTPException(status_code=e.code, detail=detail)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Không thể kết nối đến LabOps Repository Guard Agent: {str(e)}"
        )


class SubmitRepoRequest(BaseModel):
    repo_url: str = Field(..., description="HTTPS URL kho lưu trữ GitHub")
    ref: Optional[str] = Field("HEAD", description="Branch, tag hoặc commit SHA")
    target_workspace: Optional[str] = Field(None, description="Tên workspace đích")


class AdminDecisionRequest(BaseModel):
    decision: str = Field(..., description="APPROVED hoặc REJECTED")
    reason: str = Field(..., description="Lý do quản trị viên phê duyệt hoặc từ chối")


class DeliverRepoRequest(BaseModel):
    request_id: str = Field(..., description="Mã yêu cầu quét đã được phê duyệt")
    target_directory: str = Field(..., description="Thư mục làm việc đích trong workspace")


@router.post("/requests", status_code=status.HTTP_202_ACCEPTED)
async def submit_repository_request(
    payload: SubmitRepoRequest,
    p: Principal = Depends(require_user)
) -> Dict[str, Any]:
    """
    Tiếp nhận yêu cầu tải mã nguồn từ GitHub.
    Tự động phân giải commit SHA, kiểm tra cache và đưa vào hàng đợi kiểm duyệt an toàn.
    """
    data = {
        "repo_url": payload.repo_url,
        "ref": payload.ref or "HEAD",
        "submitter": p.username,
        "target_workspace": payload.target_workspace or f"ws-{p.username}"
    }
    res = _agent_call("requests", method="POST", data=data)
    
    # Audit log in backend
    service.audit_logs.insert(0, {
        "id": f"aud-req-{res.get('id', 'new')}",
        "actor": p.username,
        "action": "REPOSITORY_GUARD_SUBMIT",
        "target_type": "REPOSITORY",
        "target_id": payload.repo_url,
        "timestamp": "Vừa xong",
        "result": "SUCCESS",
        "correlation_id": res.get("id"),
        "details": f"Người dùng {p.username} yêu cầu kiểm duyệt repo {payload.repo_url} (ref: {payload.ref})"
    })
    return res


@router.get("/requests")
async def list_repository_requests(
    p: Principal = Depends(require_user)
) -> List[Dict[str, Any]]:
    """
    Lấy danh sách các yêu cầu kiểm duyệt kho mã.
    Admin xem toàn bộ; Researcher chỉ xem các yêu cầu của chính mình.
    """
    is_admin = p.is_admin
    path = f"requests?submitter={p.username}&is_admin={'true' if is_admin else 'false'}"
    res = _agent_call(path, method="GET")
    return res if isinstance(res, list) else []


@router.get("/requests/{request_id}")
async def get_repository_request(
    request_id: str,
    p: Principal = Depends(require_user)
) -> Dict[str, Any]:
    """Lấy chi tiết một yêu cầu kiểm duyệt và kết quả quét bảo mật."""
    res = _agent_call(f"requests/{request_id}", method="GET")
    # Kiểm tra quyền: nếu không phải admin và không phải người gửi -> 403
    if not p.is_admin and res.get("submitted_by") != p.username:
        raise HTTPException(status_code=403, detail="Bạn không có quyền xem yêu cầu của thành viên khác")
    return res


@router.post("/requests/{request_id}/decision")
async def decide_repository_request(
    request_id: str,
    payload: AdminDecisionRequest,
    p: Principal = Depends(require_admin)
) -> Dict[str, Any]:
    """Admin phê duyệt ngoại lệ hoặc từ chối kho mã bị cảnh báo rủi ro."""
    if payload.decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=400, detail="Quyết định phải là APPROVED hoặc REJECTED")

    data = {
        "decision": payload.decision,
        "reviewer": p.username,
        "reason": payload.reason
    }
    res = _agent_call(f"requests/{request_id}/decision", method="POST", data=data)

    service.audit_logs.insert(0, {
        "id": f"aud-dec-{request_id}",
        "actor": p.username,
        "action": f"REPOSITORY_ADMIN_{payload.decision}",
        "target_type": "REPOSITORY_REQUEST",
        "target_id": request_id,
        "timestamp": "Vừa xong",
        "result": "SUCCESS",
        "correlation_id": request_id,
        "details": f"Admin {p.username} đã {payload.decision} yêu cầu {request_id}. Lý do: {payload.reason}"
    })
    return res


@router.post("/deliver")
async def deliver_repository_artifact(
    payload: DeliverRepoRequest,
    p: Principal = Depends(require_user)
) -> Dict[str, Any]:
    """Phân phối gói mã nguồn đã được xác thực an toàn vào workspace của thành viên."""
    # Kiểm tra request trước
    req_info = _agent_call(f"requests/{payload.request_id}", method="GET")
    if not p.is_admin and req_info.get("submitted_by") != p.username:
        raise HTTPException(status_code=403, detail="Không có quyền phân phối yêu cầu của người khác")

    data = {
        "request_id": payload.request_id,
        "target_directory": payload.target_directory,
        "deliver_as_user": p.username
    }
    res = _agent_call("deliver", method="POST", data=data)

    service.audit_logs.insert(0, {
        "id": f"aud-del-{payload.request_id}",
        "actor": p.username,
        "action": "REPOSITORY_ARTIFACT_DELIVERED",
        "target_type": "WORKSPACE",
        "target_id": payload.target_directory,
        "timestamp": "Vừa xong",
        "result": "SUCCESS",
        "correlation_id": payload.request_id,
        "details": f"Đã phân phối artifact được phê duyệt vào thư mục {payload.target_directory}"
    })
    return res


@router.get("/audit")
async def get_repository_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    p: Principal = Depends(require_admin)
) -> List[Dict[str, Any]]:
    """Lấy danh sách nhật ký kiểm toán (Audit Logs) của Repository Guard."""
    res = _agent_call(f"audit?limit={limit}", method="GET")
    return res if isinstance(res, list) else []
