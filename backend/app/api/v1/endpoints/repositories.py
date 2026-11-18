import hashlib
import hmac
import json
import os
import posixpath
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.dependencies import require_admin, require_user
from app.core.security import Principal
from app.services.labops_service import service

router = APIRouter()

_FORBIDDEN_DELIVER_PREFIXES = (
    "/etc",
    "/proc",
    "/sys",
    "/root",
    "/var/run",
    "/run",
    "/dev",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
)


def _get_agent_urls() -> List[str]:
    custom = os.getenv("LABOPS_AGENT_URL")
    if custom:
        return [custom.rstrip("/")]
    return [
        "http://127.0.0.1:9150/api/v1/agent/repositoryguard",
        "http://127.0.0.1:9100/api/v1/agent/repositoryguard",
    ]


def _sign_broker_headers(method: str, path: str, principal: Optional[Principal]) -> Dict[str, str]:
    if principal is None:
        return {}
    role = principal.role.upper()
    headers = {
        "X-LabOps-Verified-Actor": principal.username,
        "X-LabOps-Verified-Role": role,
    }
    broker_secret = (os.getenv("LABOPS_BROKER_VERIFY_SECRET") or "").strip()
    if broker_secret:
        ts = str(int(time.time()))
        url_path = "/api/v1/agent/repositoryguard/" + path.split("?", 1)[0].lstrip("/")
        signing_payload = f"{ts}:{principal.username}:{role}:{method.upper()}:{url_path}"
        sig = hmac.new(broker_secret.encode("utf-8"), signing_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["X-LabOps-Verified-Timestamp"] = ts
        headers["X-LabOps-Verified-Signature"] = sig
    return headers


def _validate_target_directory(target_directory: str, principal: Principal) -> str:
    raw = (target_directory or "").strip()
    if not raw or "\x00" in raw or "\\" in raw:
        raise HTTPException(status_code=400, detail="Đường dẫn thư mục đích không hợp lệ")
    if not raw.startswith("/"):
        raise HTTPException(status_code=400, detail="Đường dẫn đích phải là đường dẫn tuyệt đối")
    if ".." in raw.split("/"):
        raise HTTPException(status_code=400, detail="Đường dẫn đích không được chứa path traversal ('..')")
    clean = posixpath.normpath(raw)
    if clean == "/" or any(clean == p or clean.startswith(p + "/") for p in _FORBIDDEN_DELIVER_PREFIXES):
        raise HTTPException(status_code=403, detail=f"Thư mục đích '{clean}' bị cấm bởi chính sách bảo mật")
    if clean == "/home" or "/.ssh" in clean or clean.endswith("/.ssh"):
        raise HTTPException(status_code=403, detail="Không được phép phân phối mã nguồn vào thư mục .ssh hoặc /home")
    if not principal.is_admin:
        allowed_user_roots = (
            f"/workspaces/{principal.username}",
            f"/mnt/{principal.username}",
            f"/var/lib/labops/workspaces/{principal.username}",
            f"/home/{principal.username}",
            f"/media/LabOps-Data/users/{principal.username}",
            f"/tank/workspaces/{principal.username}",
        )
        if any(clean.startswith(prefix.rsplit("/", 1)[0] + "/") for prefix in allowed_user_roots):
            if not any(clean == root or clean.startswith(root + "/") for root in allowed_user_roots):
                raise HTTPException(status_code=403, detail="Không được phép phân phối mã vào workspace của thành viên khác")
    return clean


def _agent_call(
    path: str,
    method: str = "GET",
    data: Optional[Dict[str, Any]] = None,
    *,
    principal: Optional[Principal] = None,
    timeout: float = 10.0,
) -> Any:
    agent_token = (os.getenv("LABOPS_AGENT_SECRET") or "").strip()
    if not agent_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LABOPS_AGENT_SECRET chưa được cấu hình (fail-closed)",
        )

    urls = _get_agent_urls()
    headers: Dict[str, str] = {
        "X-LabOps-Agent-Secret": agent_token,
        "Content-Type": "application/json",
    }
    headers.update(_sign_broker_headers(method, path, principal))

    body = json.dumps(data).encode("utf-8") if data is not None else None
    last_err = None

    for base_url in urls:
        url = f"{base_url}/{path.lstrip('/')}"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read().decode("utf-8")
                if content:
                    return json.loads(content)
                return {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(err_body)
                detail = parsed.get("error") or parsed.get("detail") or err_body
            except Exception:
                detail = err_body
            raise HTTPException(status_code=e.code, detail=detail)
        except Exception as e:
            last_err = e
            continue

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Không thể kết nối đến LabOps Repository Guard Agent: {str(last_err)}",
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


_REPO_PATH_RE = re.compile(r"^[a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-]+$")


def _validate_repo_url(raw_url: str) -> str:
    url_str = (raw_url or "").strip()
    if not url_str:
        raise HTTPException(status_code=400, detail="URL kho mã nguồn không được để trống")
    parsed = urllib.parse.urlparse(url_str)
    if parsed.scheme.lower() != "https":
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận giao thức https:// cho kho mã bên ngoài")
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "gitlab.com"):
        raise HTTPException(
            status_code=400,
            detail=f"Máy chủ '{host or 'unknown'}' không được phép; chỉ hỗ trợ github.com hoặc gitlab.com",
        )
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="URL không được chứa thông tin xác thực nhúng")
    trimmed = parsed.path.strip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[:-4]
    if not _REPO_PATH_RE.match(trimmed) or ".." in trimmed.split("/"):
        raise HTTPException(status_code=400, detail="Định dạng kho mã phải là 'owner/repository'")
    return f"https://{host}/{trimmed}"


@router.post("/requests", status_code=status.HTTP_202_ACCEPTED)
async def submit_repository_request(
    payload: SubmitRepoRequest,
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    canonical_url = _validate_repo_url(payload.repo_url)
    data = {
        "repo_url": canonical_url,
        "ref": payload.ref or "HEAD",
        "submitter": p.username,
        "target_workspace": payload.target_workspace or f"ws-{p.username}",
    }
    res = _agent_call("requests", method="POST", data=data, principal=p)
    service._audit(
        p.username,
        "REPOSITORY_GUARD_SUBMIT",
        "REPOSITORY",
        payload.repo_url,
        "SUCCESS",
        f"Người dùng {p.username} yêu cầu kiểm duyệt repo {payload.repo_url} (ref: {payload.ref})",
    )
    service._save_persistent_data()
    return res


@router.get("/requests")
async def list_repository_requests(p: Principal = Depends(require_user)) -> List[Dict[str, Any]]:
    path = f"requests?submitter={p.username}"
    res = _agent_call(path, method="GET", principal=p)
    return res if isinstance(res, list) else []


@router.get("/requests/{request_id}")
async def get_repository_request(request_id: str, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    res = _agent_call(f"requests/{request_id}", method="GET", principal=p)
    if not p.is_admin and res.get("submitted_by") != p.username:
        raise HTTPException(status_code=403, detail="Bạn không có quyền xem yêu cầu của thành viên khác")
    return res


@router.post("/requests/{request_id}/decision")
async def decide_repository_request(
    request_id: str,
    payload: AdminDecisionRequest,
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    if payload.decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=400, detail="Quyết định phải là APPROVED hoặc REJECTED")

    data = {
        "decision": payload.decision,
        "reviewer": p.username,
        "reason": payload.reason,
    }
    res = _agent_call(f"requests/{request_id}/decision", method="POST", data=data, principal=p)
    service._audit(
        p.username,
        f"REPOSITORY_ADMIN_{payload.decision}",
        "REPOSITORY_REQUEST",
        request_id,
        "SUCCESS",
        f"Admin {p.username} đã {payload.decision} yêu cầu {request_id}. Lý do: {payload.reason}",
    )
    service._save_persistent_data()
    return res


@router.post("/deliver")
async def deliver_repository_artifact(
    payload: DeliverRepoRequest,
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    clean_dir = _validate_target_directory(payload.target_directory, p)
    req_info = _agent_call(f"requests/{payload.request_id}", method="GET", principal=p)
    if not p.is_admin and req_info.get("submitted_by") != p.username:
        raise HTTPException(status_code=403, detail="Không có quyền phân phối yêu cầu của người khác")

    data = {
        "request_id": payload.request_id,
        "target_directory": clean_dir,
        "deliver_as_user": p.username,
    }
    res = _agent_call("deliver", method="POST", data=data, principal=p)
    service._audit(
        p.username,
        "REPOSITORY_ARTIFACT_DELIVERED",
        "WORKSPACE",
        clean_dir,
        "SUCCESS",
        f"Đã phân phối artifact được phê duyệt vào thư mục {clean_dir}",
    )
    service._save_persistent_data()
    return res


@router.get("/audit")
async def get_repository_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    p: Principal = Depends(require_admin),
) -> List[Dict[str, Any]]:
    res = _agent_call(f"audit?limit={limit}", method="GET", principal=p)
    return res if isinstance(res, list) else []
