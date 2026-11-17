import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from app.api.dependencies import require_admin, require_aiotgit, require_user
from app.core.config import settings
from app.core.security import Principal
from app.services.aiotgit_tokens import AioGitClaims, issue_workspace_token
from app.services.labops_service import service

router = APIRouter()

AGENT_TOKEN = os.getenv("LABOPS_AGENT_SECRET", "").strip()
CANONICAL_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_GUARD_PATH = "/api/v1/agent/repositoryguard"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_AGENT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect(),
)


def _get_agent_urls() -> List[str]:
    custom = os.getenv("LABOPS_AGENT_URL")
    if custom:
        candidates = [custom.rstrip("/")]
    else:
        candidates = [
            "http://127.0.0.1:9150/api/v1/agent/repositoryguard",
            "http://127.0.0.1:9100/api/v1/agent/repositoryguard"
        ]
    validated = []
    for candidate in candidates:
        try:
            parsed = urllib.parse.urlsplit(candidate)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="Repository Guard agent URL is invalid") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != REPOSITORY_GUARD_PATH
        ):
            raise HTTPException(status_code=503, detail="Repository Guard agent URL is invalid")
        validated.append(candidate)
    return validated


def _agent_call(
    path: str,
    method: str = "GET",
    data: Optional[Dict[str, Any]] = None,
    *,
    actor_token: str,
    timeout: float = 10.0,
) -> Any:
    if not AGENT_TOKEN:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Repository Guard agent authentication is not configured")
    if not actor_token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Repository actor delegation is not configured")
    urls = _get_agent_urls()
    headers = {
        "X-LabOps-Agent-Secret": AGENT_TOKEN,
        "Authorization": f"Bearer {actor_token}",
        "Content-Type": "application/json"
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    last_err = None

    for base_url in urls:
        url = f"{base_url}/{path.lstrip('/')}"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with _AGENT_OPENER.open(req, timeout=timeout) as resp:
                content_bytes = resp.read((16 << 20) + 1)
                if len(content_bytes) > 16 << 20:
                    raise HTTPException(status_code=502, detail="Repository Guard response exceeded the size limit")
                content = content_bytes.decode("utf-8")
                if content:
                    return json.loads(content)
                return {}
        except urllib.error.HTTPError as e:
            err_body = e.read(64 * 1024).decode("utf-8", errors="replace")
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
        detail=f"Không thể kết nối đến LabOps Repository Guard Agent: {str(last_err)}"
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


class AioGitTokenRequest(BaseModel):
    workspace_id: str = Field(..., min_length=13, max_length=13, pattern=r"^ws-[a-f0-9]{10}$")


class AioGitCloneRequest(BaseModel):
    repo_url: str = Field(..., min_length=1, max_length=2048)
    ref: str = Field("HEAD", min_length=1, max_length=255)
    idempotency_key: str = Field(..., min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def _runtime_verified_workspace(item: Dict[str, Any]) -> bool:
    runtime_verified = (
        item.get("runtime_type") == "DOCKER"
        and item.get("lifecycle_state") == "ACTIVE"
        and item.get("observed_state") == "ONLINE"
        and item.get("runtime_verification") == "VERIFIED"
        and isinstance(item.get("host_id"), str)
        and bool(item.get("host_id"))
        and isinstance(item.get("container_id"), str)
        and CANONICAL_CONTAINER_ID.fullmatch(item["container_id"]) is not None
    )
    if not runtime_verified:
        return False
    policy_mode = item.get("repository_policy_mode", "UNENFORCED")
    if policy_mode not in {"UNENFORCED", "REPOSITORY_ENFORCED"}:
        return False
    if policy_mode == "REPOSITORY_ENFORCED":
        return item.get("observed_network_mode") == "none" and item.get("egress_verification") == "VERIFIED_NO_NETWORK"
    return True


def _resolve_owned_workspace(p: Principal, requested: Optional[str]) -> Dict[str, Any]:
    # Resolve workspace identity from backend-owned metadata. A caller-supplied name
    # is only a selector and never establishes ownership or a filesystem path.
    workspaces = service.workspace_controller.list_workspaces(owner=p.username, legacy_endpoints=[])
    matches = [item for item in workspaces if item.get("owner") == p.username]

    if requested:
        selected = next((item for item in matches if requested in (item.get("id"), item.get("name"))), None)
        if selected is None:
            raise HTTPException(status_code=403, detail="Workspace does not belong to the authenticated user")
        if not _runtime_verified_workspace(selected):
            raise HTTPException(status_code=409, detail="Workspace runtime is not verified and cannot receive repository artifacts")
        return selected
    eligible = [item for item in matches if _runtime_verified_workspace(item)]
    if len(eligible) != 1:
        raise HTTPException(status_code=409, detail="An unambiguous registered workspace is required")
    return eligible[0]


def _resolve_owned_workspace_id(p: Principal, requested: Optional[str]) -> str:
    return str(_resolve_owned_workspace(p, requested)["id"])


def _issue_actor_token(
    p: Principal,
    *,
    scopes: List[str],
    workspace_id: str = "",
    node_id: Optional[str] = None,
    resource_id: str = "",
    ttl: int = 60,
) -> str:
    try:
        return issue_workspace_token(
            username=p.username,
            role="ADMIN" if p.is_admin else "RESEARCHER",
            member_id=p.member_id or "",
            workspace_id=workspace_id,
            node_id=node_id or settings.LOCAL_HOST_ID,
            scopes=scopes,
            resource_id=resource_id,
            secret=settings.LABOPS_AIOTGIT_SIGNING_KEY,
            ttl=ttl,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Repository actor signing is unavailable") from exc


def _authorize_aiotgit_workspace(claims: AioGitClaims) -> Dict[str, Any]:
    workspaces = service.workspace_controller.list_workspaces(owner=claims.subject, legacy_endpoints=[])
    workspace = next((item for item in workspaces if item.get("id") == claims.workspace_id), None)
    if workspace is None or workspace.get("owner") != claims.subject or not _runtime_verified_workspace(workspace):
        raise HTTPException(status_code=403, detail="AIOTGIT capability no longer maps to an active owned workspace")
    if workspace.get("host_id") != claims.node_id:
        raise HTTPException(status_code=403, detail="AIOTGIT capability node does not match the registered workspace")
    if claims.member_id and workspace.get("owner") != claims.subject:
        raise HTTPException(status_code=403, detail="AIOTGIT member identity does not own the workspace")
    return workspace


def _require_scope(claims: AioGitClaims, scope: str) -> None:
    if scope not in claims.scopes:
        raise HTTPException(status_code=403, detail="AIOTGIT capability does not authorize this operation")


@router.post("/requests", status_code=status.HTTP_202_ACCEPTED)
async def submit_repository_request(
    payload: SubmitRepoRequest,
    p: Principal = Depends(require_user)
) -> Dict[str, Any]:
    """
    Tiếp nhận yêu cầu tải mã nguồn từ GitHub.
    Tự động phân giải commit SHA, kiểm tra cache và đưa vào hàng đợi kiểm duyệt an toàn.
    """
    workspace = _resolve_owned_workspace(p, payload.target_workspace)
    workspace_id = str(workspace["id"])
    actor_token = _issue_actor_token(
        p,
        scopes=["repo:request:submit"],
        workspace_id=workspace_id,
        node_id=str(workspace["host_id"]),
    )
    data = {
        "repo_url": payload.repo_url,
        "ref": payload.ref or "HEAD",
        "target_workspace": workspace_id
    }
    res = _agent_call("requests", method="POST", data=data, actor_token=actor_token)
    
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
    actor_token = _issue_actor_token(
        p,
        scopes=["repo:request:list"],
        node_id=settings.LOCAL_HOST_ID,
    )
    res = _agent_call("requests", method="GET", actor_token=actor_token)
    return res if isinstance(res, list) else []


@router.get("/requests/{request_id}")
async def get_repository_request(
    request_id: str,
    p: Principal = Depends(require_user)
) -> Dict[str, Any]:
    """Lấy chi tiết một yêu cầu kiểm duyệt và kết quả quét bảo mật."""
    _validate_request_id(request_id)
    actor_token = _issue_actor_token(
        p,
        scopes=["repo:request:read"],
        node_id=settings.LOCAL_HOST_ID,
        resource_id=request_id,
    )
    res = _agent_call(f"requests/{request_id}", method="GET", actor_token=actor_token)
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
    _validate_request_id(request_id)
    if payload.decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=400, detail="Quyết định phải là APPROVED hoặc REJECTED")

    data = {
        "decision": payload.decision,
        "reviewer": p.username,
        "reason": payload.reason
    }
    actor_token = _issue_actor_token(
        p,
        scopes=["repo:request:decide"],
        node_id=settings.LOCAL_HOST_ID,
        resource_id=request_id,
    )
    res = _agent_call(f"requests/{request_id}/decision", method="POST", data=data, actor_token=actor_token)

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
    """Delivery stays closed until trusted workspace registry and approved-commit binding are integrated."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Artifact delivery is unavailable until workspace ownership and approved artifact binding are verified",
    )


@router.get("/audit")
async def get_repository_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    p: Principal = Depends(require_admin)
) -> List[Dict[str, Any]]:
    """Lấy danh sách nhật ký kiểm toán (Audit Logs) của Repository Guard."""
    actor_token = _issue_actor_token(
        p,
        scopes=["repo:audit:read"],
        node_id=settings.LOCAL_HOST_ID,
    )
    res = _agent_call(f"audit?limit={limit}", method="GET", actor_token=actor_token)
    return res if isinstance(res, list) else []


@router.post("/cli-token")
async def issue_aiotgit_cli_token(
    payload: AioGitTokenRequest,
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    """Issue a short-lived capability after verifying the active workspace owner."""
    workspace = _resolve_owned_workspace(p, payload.workspace_id)
    token = _issue_actor_token(
        p,
        scopes=["repo:request:submit", "repo:request:list", "repo:request:read", "repo:bundle:read"],
        workspace_id=str(workspace["id"]),
        node_id=str(workspace["host_id"]),
        ttl=900,
    )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 900,
        "workspace_id": workspace["id"],
        "node_id": workspace["host_id"],
        "repository_policy_mode": workspace.get("repository_policy_mode", "UNENFORCED"),
        "egress_verification": workspace.get("egress_verification", "NOT_ENFORCED"),
        "scopes": ["repo:request:submit", "repo:request:list", "repo:request:read", "repo:bundle:read"],
    }


@router.post("/cli/requests", status_code=status.HTTP_202_ACCEPTED)
async def aiotgit_submit_request(
    payload: AioGitCloneRequest,
    request: Request,
    claims: AioGitClaims = Depends(require_aiotgit),
) -> Dict[str, Any]:
    _require_scope(claims, "repo:request:submit")
    _authorize_aiotgit_workspace(claims)
    data = {
        "repo_url": payload.repo_url,
        "ref": payload.ref,
        "target_workspace": claims.workspace_id,
        "idempotency_key": payload.idempotency_key,
    }
    return _agent_call("requests", method="POST", data=data, actor_token=_forwarded_aiotgit_token(request))


@router.get("/cli/requests")
async def aiotgit_list_requests(
    request: Request,
    claims: AioGitClaims = Depends(require_aiotgit),
) -> List[Dict[str, Any]]:
    _require_scope(claims, "repo:request:list")
    _authorize_aiotgit_workspace(claims)
    res = _agent_call("requests", method="GET", actor_token=_forwarded_aiotgit_token(request))
    return res if isinstance(res, list) else []


@router.get("/cli/requests/{request_id}")
async def aiotgit_get_request(
    request_id: str,
    request: Request,
    claims: AioGitClaims = Depends(require_aiotgit),
) -> Dict[str, Any]:
    _require_scope(claims, "repo:request:read")
    _authorize_aiotgit_workspace(claims)
    _validate_request_id(request_id)
    return _agent_call(f"requests/{request_id}", method="GET", actor_token=_forwarded_aiotgit_token(request))


@router.get("/cli/requests/{request_id}/bundle")
async def aiotgit_get_approved_bundle(
    request_id: str,
    request: Request,
    claims: AioGitClaims = Depends(require_aiotgit),
):
    _require_scope(claims, "repo:bundle:read")
    _authorize_aiotgit_workspace(claims)
    _validate_request_id(request_id)
    response = _agent_open_stream(f"requests/{request_id}/bundle", _forwarded_aiotgit_token(request))
    digest = response.headers.get("X-LabOps-Artifact-SHA256", "")
    commit = response.headers.get("X-LabOps-Commit-SHA", "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        response.close()
        raise HTTPException(status_code=502, detail="Repository Guard returned invalid artifact metadata")
    headers = {
        "X-LabOps-Artifact-SHA256": digest,
        "X-LabOps-Commit-SHA": commit,
        "Cache-Control": "no-store",
    }
    if response.headers.get("Content-Length"):
        headers["Content-Length"] = response.headers["Content-Length"]
    return StreamingResponse(
        iter(lambda: response.read(64 * 1024), b""),
        media_type="application/vnd.git-bundle",
        headers=headers,
        background=BackgroundTask(response.close),
    )


def _forwarded_aiotgit_token(request: Request) -> str:
    """The request-scoped token must be forwarded verbatim, not reconstructed."""
    token = getattr(request.state, "aiotgit_token", None)
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=401, detail="AIOTGIT capability is unavailable")
    return token


def _validate_request_id(request_id: str) -> None:
    if not re.fullmatch(r"req-[a-f0-9]{12}", request_id):
        raise HTTPException(status_code=404, detail="Repository request not found")


def _agent_open_stream(path: str, actor_token: str, timeout: float = 120.0):
    if not AGENT_TOKEN or not actor_token:
        raise HTTPException(status_code=503, detail="Repository Guard authentication is not configured")
    last_error: Exception | None = None
    for base_url in _get_agent_urls():
        req = urllib.request.Request(
            f"{base_url}/{path.lstrip('/')}",
            headers={"X-LabOps-Agent-Secret": AGENT_TOKEN, "Authorization": f"Bearer {actor_token}"},
            method="GET",
        )
        try:
            response = _AGENT_OPENER.open(req, timeout=timeout)
            if response.status != 200:
                response.close()
                raise HTTPException(status_code=502, detail="Repository Guard rejected the artifact request")
            return response
        except urllib.error.HTTPError as exc:
            detail = exc.read(64 * 1024).decode("utf-8", errors="replace")
            exc.close()
            try:
                parsed = json.loads(detail)
                detail = parsed.get("error") or parsed.get("detail") or detail
            except (json.JSONDecodeError, AttributeError):
                pass
            raise HTTPException(status_code=exc.code, detail=detail)
        except HTTPException:
            raise
        except Exception as exc:
            last_error = exc
    raise HTTPException(status_code=503, detail=f"Repository Guard artifact service unavailable: {last_error}")
