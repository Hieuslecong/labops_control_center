import hashlib
import hmac
import json
import os
import posixpath
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.adapters.repository_agent_http import open_repository_agent
from app.api.dependencies import require_admin
from app.core.config import settings
from app.core.repository_credentials import issue_repository_token, verify_repository_token
from app.core.security import AuthError, Principal, get_revocation_store, principal_from_authorization
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


def _workspace_for_member(username: str, member_id: str, workspace_id: str, node_id: str):
    member = next(
        (item for item in service.members if item.get("username") == username and item.get("id") == member_id),
        None,
    )
    workspace = next(
        (item for item in service.workspace_controller.list_workspaces(owner=username) if item.get("id") == workspace_id),
        None,
    )
    if (
        not member
        or member.get("status") != "ACTIVE"
        or member.get("workspace_id") != workspace_id
        or not workspace
        or workspace.get("runtime_type") != "DOCKER"
        or workspace.get("host_id") != node_id
        or workspace.get("desired_state") != "ACTIVE"
        or node_id != settings.LOCAL_HOST_ID
    ):
        raise HTTPException(403, "Danh tính không khớp workspace Docker đang được cấp trên node cục bộ")
    return workspace


def _repository_access(scope: str):
    def authenticate(request: Request) -> Principal:
        header = request.headers.get("Authorization")
        try:
            try:
                principal = principal_from_authorization(header)
            except AuthError as session_error:
                if session_error.status != 401 or not header or not header.lower().startswith("bearer "):
                    raise
                principal = verify_repository_token(header.split(" ", 1)[1].strip(), scope)
        except AuthError as err:
            raise HTTPException(err.status, err.detail) from err
        if principal.kind == "repository":
            _workspace_for_member(principal.username, principal.member_id, principal.workspace_id, principal.node_id)
        elif principal.kind != "user":
            raise HTTPException(403, "Danh tính máy không được phép")
        return principal

    return authenticate


def _validate_request_id(request_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
        raise HTTPException(400, "Mã yêu cầu không hợp lệ")


def _check_repository_owner(info: dict, principal: Principal):
    if not principal.is_admin and info.get("submitted_by") != principal.username:
        raise HTTPException(403, "Yêu cầu không thuộc về thành viên")
    if principal.kind == "repository" and info.get("target_workspace") != f"managed:{principal.workspace_id}":
        raise HTTPException(403, "Yêu cầu không thuộc về workspace")


def _get_agent_urls() -> List[str]:
    custom = os.getenv("LABOPS_AGENT_URL")
    if custom:
        return [custom.rstrip("/")]
    urls: List[str] = []
    local_base = (
        os.getenv("LABOPS_LOCAL_AGENT_URL")
        or getattr(settings, "LABOPS_LOCAL_AGENT_URL", "")
        or ""
    ).strip().rstrip("/")
    if local_base:
        urls.append(f"{local_base}/api/v1/agent/repositoryguard")
    for fallback in (
        "http://127.0.0.1:9100/api/v1/agent/repositoryguard",
        "http://127.0.0.1:9150/api/v1/agent/repositoryguard",
    ):
        if fallback not in urls:
            urls.append(fallback)
    return urls


def _sign_broker_headers(method: str, path: str, principal: Optional[Principal]) -> Dict[str, str]:
    if principal is None:
        return {}
    role = principal.role.upper()
    headers = {
        "X-LabOps-Verified-Actor": principal.username,
        "X-LabOps-Verified-Role": role,
    }
    broker_secret = (
        os.getenv("LABOPS_BROKER_VERIFY_SECRET")
        or os.getenv("LABOPS_REPO_BROKER_SECRET")
        or ""
    ).strip()
    if principal.kind == "repository" and not broker_secret:
        raise HTTPException(503, "Managed Git yêu cầu xác thực Repository Broker")
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
    raw_agent_secret = os.environ.get("LABOPS_AGENT_SECRET")
    if raw_agent_secret is not None:
        agent_token = raw_agent_secret.strip()
    else:
        agent_token = (
            os.getenv("LABOPS_NODE_AGENT_TOKEN")
            or getattr(settings, "LABOPS_AGENT_SECRET", "")
            or getattr(settings, "LABOPS_NODE_AGENT_TOKEN", "")
            or ""
        ).strip()
    if not agent_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LABOPS_AGENT_SECRET chưa được cấu hình (fail-closed)",
        )

    urls = _get_agent_urls()
    headers: Dict[str, str] = {
        "X-LabOps-Agent-Secret": agent_token,
        "Authorization": f"Bearer {agent_token}",
        "Content-Type": "application/json",
    }
    headers.update(_sign_broker_headers(method, path, principal))

    body = json.dumps(data).encode("utf-8") if data is not None else None
    last_err = None

    open_agent = open_repository_agent if principal and principal.kind == "repository" else urllib.request.urlopen
    for base_url in urls:
        url = f"{base_url}/{path.lstrip('/')}"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with open_agent(req, timeout=timeout) as resp:
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
            detail_str = str(detail)
            detail_lower = detail_str.lower()
            if e.code == status.HTTP_401_UNAUTHORIZED and (
                "agent token" in detail_lower or "broker" in detail_lower
            ):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        "Xác thực nội bộ Backend → Go Node Agent thất bại "
                        f"({detail_str}). Kiểm tra đồng bộ LABOPS_AGENT_SECRET / "
                        "LABOPS_NODE_AGENT_TOKEN hoặc LABOPS_BROKER_VERIFY_SECRET giữa .env.local và /etc/labops/agent.env."
                    ),
                ) from e
            raise HTTPException(status_code=e.code, detail=detail)
        except Exception as e:
            last_err = e
            continue

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Không thể kết nối đến LabOps Repository Guard Agent: {str(last_err)}",
    )


class RepositoryTokenRequest(BaseModel):
    member_id: str
    previous_jti: str | None = None


@router.post("/credentials/{workspace_id}")
def create_workspace_credential(
    workspace_id: str, payload: RepositoryTokenRequest, response: Response, p: Principal = Depends(require_admin)
):
    member = next((item for item in service.members if item.get("id") == payload.member_id), None)
    if not member:
        raise HTTPException(404, "Không tìm thấy thành viên")
    node_id = settings.LOCAL_HOST_ID
    _workspace_for_member(member["username"], payload.member_id, workspace_id, node_id)
    try:
        credential = issue_repository_token(member["username"], payload.member_id, workspace_id, node_id)
    except AuthError as err:
        raise HTTPException(err.status, err.detail) from err
    if payload.previous_jti:
        get_revocation_store().revoke_jti(payload.previous_jti, credential["expires_at"], reason="repository_rotation")
    response.headers["Cache-Control"] = "no-store"
    service._audit(p.username, "REPOSITORY_CREDENTIAL_ISSUED", "WORKSPACE", workspace_id, "SUCCESS", credential["jti"])
    getattr(service, "save_audit_logs", service._save_persistent_data)()
    return credential


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
    try:
        parsed = urllib.parse.urlparse(url_str)
        port = parsed.port
    except ValueError as err:
        raise HTTPException(400, "URL kho mã nguồn không hợp lệ") from err
    if port not in (None, 443) or parsed.query or parsed.fragment or parsed.params:
        raise HTTPException(400, "URL repo không hỗ trợ port riêng, query hoặc fragment")
    if parsed.scheme.lower() != "https":
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận giao thức https:// cho kho mã bên ngoài")
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "gitlab.com"):
        raise HTTPException(
            status_code=400,
            detail=f"Máy chủ '{host or 'unknown'}' không được phép; chỉ hỗ trợ github.com hoặc gitlab.com",
        )
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(status_code=400, detail="URL không được chứa thông tin xác thực nhúng")
    trimmed = parsed.path.strip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[:-4]
    if not _REPO_PATH_RE.match(trimmed) or ".." in trimmed.split("/"):
        raise HTTPException(status_code=400, detail="Định dạng kho mã phải là 'owner/repository'")
    return f"https://{host}/{trimmed}"


@router.post("/requests", status_code=status.HTTP_202_ACCEPTED)
def submit_repository_request(
    payload: SubmitRepoRequest,
    p: Principal = Depends(_repository_access("repository:request")),
) -> Dict[str, Any]:
    canonical_url = _validate_repo_url(payload.repo_url)
    if p.kind != "repository" and (payload.target_workspace or "").startswith("managed:"):
        raise HTTPException(403, "Workspace được quản lý yêu cầu credential riêng")
    if p.kind == "repository" and payload.target_workspace not in (None, p.workspace_id):
        raise HTTPException(403, "Không thể gửi yêu cầu cho workspace khác")
    data = {
        "repo_url": canonical_url,
        "ref": payload.ref or "HEAD",
        "submitter": p.username,
        "target_workspace": f"managed:{p.workspace_id}" if p.kind == "repository" else (payload.target_workspace or f"ws-{p.username}"),
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
    getattr(service, "save_audit_logs", service._save_persistent_data)()
    return res


@router.post("/requests/{request_id}/rescan", status_code=status.HTTP_202_ACCEPTED)
def rescan_repository_request(
    request_id: str,
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    _validate_request_id(request_id)
    req_info = _agent_call(f"requests/{request_id}", method="GET", principal=p)
    submitter = str(req_info.get("submitted_by") or "").strip()
    data: Dict[str, Any] = {
        "source": str(req_info.get("source") or ""),
        "local_repo_rel_path": str(req_info.get("local_repo_path") or ""),
        "node_id": settings.LOCAL_HOST_ID,
    }
    member = next(
        (item for item in service.members if str(item.get("username") or "").strip() == submitter),
        None,
    )
    if member:
        data["member_id"] = str(member.get("id") or "")
        data["workspace_id"] = str(member.get("workspace_id") or "")
        # If this failed before workspace_clone metadata was attached, or is already a workspace_clone,
        # inspect the live container to pick up moved paths or updated commit SHAs.
        if (data["source"] == "workspace_clone" or not data["local_repo_rel_path"]) and (
            member.get("container_id") or member.get("workspace_id")
        ):
            container_ref = str(member.get("container_id") or f"ws-{submitter}")
            target_canonical = str(req_info.get("canonical_repo") or req_info.get("repo_url") or "").strip().lower()
            try:
                proc = subprocess.run(
                    ["docker", "exec", container_ref, "/usr/bin/python3", "-c", _DOCKER_DISCOVER_SCRIPT],
                    capture_output=True,
                    text=True,
                    timeout=2.5,
                )
                if proc.returncode == 0:
                    for line in proc.stdout.splitlines():
                        try:
                            rec = json.loads(line.strip())
                        except ValueError:
                            continue
                        norm_u = _normalize_discovered_git_url(str(rec.get("url") or ""))
                        if norm_u and norm_u.lower() == target_canonical:
                            data["source"] = "workspace_clone"
                            data["local_repo_rel_path"] = str(rec.get("dir") or "")
                            discovered_sha = str(rec.get("sha") or "").strip()
                            if _COMMIT_SHA_RE.match(discovered_sha):
                                data["ref"] = discovered_sha
                            break
            except Exception:
                pass

    res = _agent_call(f"requests/{request_id}/rescan", method="POST", data=data, principal=p)
    service._audit(
        p.username,
        "REPOSITORY_ADMIN_RESCAN",
        "REPOSITORY_REQUEST",
        request_id,
        "SUCCESS",
        f"Admin {p.username} yêu cầu quét lại hậu kiểm cho {request_id}.",
    )
    getattr(service, "save_audit_logs", service._save_persistent_data)()
    try:
        from app.services.zalo_assistant import zalo_assistant_service

        if isinstance(res, dict) and res.get("id"):
            zalo_assistant_service.repo_alert_monitor.push_event(res)
    except Exception:
        pass
    return res


_SYNC_LOCK = threading.Lock()
_LAST_WORKSPACE_CLONE_SYNC_TS: float = 0.0
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_DOCKER_DISCOVER_SCRIPT = (
    "import json, os, subprocess\n"
    "root = '/workspace'\n"
    "seen = set()\n"
    "def inspect(p, rel):\n"
    "    if not os.path.isdir(os.path.join(p, '.git')) or p in seen:\n"
    "        return\n"
    "    seen.add(p)\n"
    "    try:\n"
    "        url = subprocess.check_output(['git', '-C', p, 'config', '--get', 'remote.origin.url'], text=True, stderr=subprocess.DEVNULL).strip()\n"
    "        sha = subprocess.check_output(['git', '-C', p, 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()\n"
    "        print(json.dumps({'dir': rel, 'url': url, 'sha': sha}))\n"
    "    except Exception:\n"
    "        pass\n"
    "if os.path.isdir(root):\n"
    "    for entry in sorted(os.listdir(root)):\n"
    "        if entry.startswith('.'):\n"
    "            continue\n"
    "        p1 = os.path.join(root, entry)\n"
    "        if not os.path.isdir(p1):\n"
    "            continue\n"
    "        inspect(p1, entry)\n"
    "        for sub in sorted(os.listdir(p1)):\n"
    "            if sub.startswith('.'):\n"
    "                continue\n"
    "            p2 = os.path.join(p1, sub)\n"
    "            if os.path.isdir(p2):\n"
    "                inspect(p2, entry + '/' + sub)\n"
)


def _normalize_discovered_git_url(raw_url: str) -> Optional[str]:
    u = (raw_url or "").strip()
    if not u:
        return None
    ssh_match = re.match(r"^(?:ssh://)?git@(github\.com|gitlab\.com)[:/](.+)$", u, flags=re.IGNORECASE)
    if ssh_match:
        u = f"https://{ssh_match.group(1).lower()}/{ssh_match.group(2)}"
    try:
        return _validate_repo_url(u)
    except HTTPException:
        return None


def _sync_workspace_cloned_repositories(principal: Principal, existing_requests: List[Dict[str, Any]]) -> bool:
    global _LAST_WORKSPACE_CLONE_SYNC_TS
    if "PYTEST_CURRENT_TEST" in os.environ:
        return False

    now = time.monotonic()
    with _SYNC_LOCK:
        if now - _LAST_WORKSPACE_CLONE_SYNC_TS < 5.0:
            return False
        _LAST_WORKSPACE_CLONE_SYNC_TS = now

    existing_by_exact: Dict[tuple, Dict[str, Any]] = {}
    existing_by_repo: Dict[tuple, Dict[str, Any]] = {}
    for item in existing_requests:
        if not isinstance(item, dict):
            continue
        r_url = str(item.get("repo_url") or item.get("canonical_repo") or "").strip().lower()
        sub = str(item.get("submitted_by") or "").strip().lower()
        sha = str(item.get("commit_sha") or "").strip().lower()
        ref = str(item.get("requested_ref") or "").strip().lower()
        if r_url and sub:
            if sha:
                existing_by_exact[(r_url, sha, sub)] = item
            if ref:
                existing_by_exact[(r_url, ref, sub)] = item
            existing_by_repo[(r_url, sub)] = item

    submitted_any = False
    for member in list(service.members):
        username = str(member.get("username") or "").strip()
        if not username:
            continue
        if member.get("status") in ("DELETED", "ARCHIVED", "REVOKED"):
            continue
        if not (member.get("container_id") or member.get("workspace_id")):
            continue
        if not principal.is_admin and username != principal.username:
            continue

        container_ref = str(member.get("container_id") or f"ws-{username}")
        try:
            proc = subprocess.run(
                ["docker", "exec", container_ref, "/usr/bin/python3", "-c", _DOCKER_DISCOVER_SCRIPT],
                capture_output=True,
                text=True,
                timeout=2.5,
            )
        except Exception:
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue

        member_principal = Principal(
            kind="user",
            username=username,
            role="RESEARCHER",
            member_id=str(member.get("id") or ""),
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            canonical_url = _normalize_discovered_git_url(str(rec.get("url") or ""))
            sha = str(rec.get("sha") or "").strip()
            rel_dir = str(rec.get("dir") or "").strip()
            if not canonical_url or not _COMMIT_SHA_RE.match(sha):
                continue
            key_exact = (canonical_url.lower(), sha.lower(), username.lower())
            key_repo = (canonical_url.lower(), username.lower())
            existing_item = existing_by_exact.get(key_exact) or existing_by_repo.get(key_repo)

            request_payload = {
                "repo_url": canonical_url,
                "ref": sha,
                "submitter": username,
                "target_workspace": f"ws-{username}",
                "source": "workspace_clone",
                "local_repo_rel_path": rel_dir,
                "workspace_id": str(member.get("workspace_id") or ""),
                "member_id": str(member.get("id") or ""),
                "node_id": settings.LOCAL_HOST_ID,
            }

            if existing_item is not None:
                dec = existing_item.get("decision")
                dec_verdict = str(dec.get("verdict") or "").upper() if isinstance(dec, dict) else ""
                is_admin_approved = (
                    str(existing_item.get("state") or "").upper() == "MANUALLY_APPROVED"
                    or str(existing_item.get("review_status") or "").upper() == "APPROVED"
                    or dec_verdict in {"APPROVED", "MANUALLY_APPROVED"}
                )
                if is_admin_approved:
                    continue
                if (
                    existing_item.get("state") == "FAILED"
                    and existing_item.get("source") != "workspace_clone"
                    and existing_item.get("id")
                ):
                    try:
                        _agent_call(
                            f"requests/{existing_item['id']}/rescan",
                            method="POST",
                            data=request_payload,
                            principal=member_principal,
                            timeout=5.0,
                        )
                        existing_item["source"] = "workspace_clone"
                        submitted_any = True
                    except Exception:
                        continue
                continue

            try:
                _agent_call(
                    "requests",
                    method="POST",
                    data=request_payload,
                    principal=member_principal,
                    timeout=5.0,
                )
                placeholder = {"source": "workspace_clone", "state": "REQUESTED"}
                existing_by_exact[key_exact] = placeholder
                existing_by_repo[key_repo] = placeholder
                submitted_any = True
                service._audit(
                    username,
                    "REPOSITORY_WORKSPACE_CLONE_DETECTED",
                    "REPOSITORY",
                    canonical_url,
                    "SUCCESS",
                    f"Phát hiện git clone trong workspace ws-{username} ({canonical_url} @ {sha[:8]}); đã ghi nhận Clone thành công và đưa vào luồng quét hậu kiểm.",
                )
            except Exception:
                continue

    if submitted_any:
        getattr(service, "save_audit_logs", service._save_persistent_data)()
        try:
            from app.services.zalo_assistant import zalo_assistant_service

            monitor = getattr(zalo_assistant_service, "repo_alert_monitor", None)
            if monitor is not None:
                for req_obj in existing_by_exact.values():
                    if isinstance(req_obj, dict) and req_obj.get("id"):
                        monitor.push_event(req_obj)
        except Exception:
            pass
    return submitted_any


@router.get("/requests")
def list_repository_requests(p: Principal = Depends(_repository_access("repository:status"))) -> List[Dict[str, Any]]:
    path = "requests" if p.is_admin else "requests?" + urllib.parse.urlencode({"submitter": p.username})
    res = _agent_call(path, method="GET", principal=p)
    if not isinstance(res, list):
        return []
    if p.kind == "repository":
        return [
            item for item in res
            if item.get("target_workspace") == f"managed:{p.workspace_id}" and item.get("submitted_by") == p.username
        ]
    if _sync_workspace_cloned_repositories(p, res):
        refreshed = _agent_call(path, method="GET", principal=p)
        if isinstance(refreshed, list):
            return refreshed
    return res


@router.get("/requests/{request_id}")
def get_repository_request(request_id: str, p: Principal = Depends(_repository_access("repository:status"))) -> Dict[str, Any]:
    _validate_request_id(request_id)
    res = _agent_call(f"requests/{request_id}", method="GET", principal=p)
    _check_repository_owner(res, p)
    return res


@router.post("/requests/{request_id}/decision")
def decide_repository_request(
    request_id: str,
    payload: AdminDecisionRequest,
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    _validate_request_id(request_id)
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
    getattr(service, "save_audit_logs", service._save_persistent_data)()
    if payload.decision == "APPROVED" and isinstance(res, dict):
        try:
            from app.services.zalo_assistant import zalo_assistant_service

            monitor = getattr(zalo_assistant_service, "repo_alert_monitor", None)
            if monitor is not None:
                monitor.suppress_repo_alerts(res, actor=p.username, reason=payload.reason)
            ev_store = getattr(zalo_assistant_service, "v4", None)
            if ev_store is not None:
                ev_store.record_repo_decision_event(request_id=request_id, req_info=res, decision=payload.decision, actor=p.username)
        except Exception:
            pass
    return res


@router.post("/deliver")
def deliver_repository_artifact(
    payload: DeliverRepoRequest,
    p: Principal = Depends(_repository_access("repository:deliver")),
) -> Dict[str, Any]:
    _validate_request_id(payload.request_id)
    clean_dir = "" if p.kind == "repository" else _validate_target_directory(payload.target_directory, p)
    req_info = _agent_call(f"requests/{payload.request_id}", method="GET", principal=p)
    _check_repository_owner(req_info, p)
    if p.kind != "repository" and str(req_info.get("target_workspace") or "").startswith("managed:"):
        raise HTTPException(403, "Bàn giao workspace được quản lý yêu cầu credential riêng")
    if p.kind == "repository":
        workspace = _workspace_for_member(p.username, p.member_id, p.workspace_id, p.node_id)
        mount = workspace.get("container_mount_path") or "/workspace"
        raw = payload.target_directory
        if ".." in raw.split("/") or "\x00" in raw or "\\" in raw:
            raise HTTPException(400, "Thư mục đích phải nằm trong workspace")
        if raw.startswith("/"):
            if not raw.startswith(mount.rstrip("/") + "/"):
                raise HTTPException(400, "Thư mục đích phải nằm trong workspace")
            raw = posixpath.relpath(raw, mount)
        clean_dir = posixpath.normpath(raw)
        if clean_dir == ".":
            raise HTTPException(400, "Thư mục đích phải nằm trong workspace")
    data = {
        "request_id": payload.request_id,
        "target_directory": clean_dir,
        "deliver_as_user": p.username,
    }
    if p.kind == "repository":
        data.update(workspace_id=p.workspace_id, member_id=p.member_id, node_id=p.node_id)
    res = _agent_call("deliver", method="POST", data=data, principal=p, timeout=135.0)
    if not isinstance(res, dict) or res.get("success") is not True:
        raise HTTPException(502, "Agent chưa xác nhận bàn giao artifact")
    service._audit(
        p.username,
        "REPOSITORY_ARTIFACT_DELIVERED",
        "WORKSPACE",
        clean_dir,
        "SUCCESS",
        f"Đã phân phối artifact được phê duyệt vào thư mục {clean_dir}",
    )
    getattr(service, "save_audit_logs", service._save_persistent_data)()
    return res


@router.get("/audit")
def get_repository_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    p: Principal = Depends(require_admin),
) -> List[Dict[str, Any]]:
    res = _agent_call(f"audit?limit={limit}", method="GET", principal=p)
    return res if isinstance(res, list) else []
