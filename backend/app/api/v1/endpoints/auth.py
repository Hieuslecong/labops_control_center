from __future__ import annotations

import hmac
import os
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.dependencies import require_admin, require_user
from app.core.config import settings
from app.core.security import (
    Principal,
    get_revocation_store,
    issue_step_up_token,
    issue_token,
)
from app.services.labops_service import MemberConflictError, service
from app.services.mram_service import MramValidationError, mram_service

router = APIRouter()

_FAILED_LOCK = threading.Lock()
_FAILED_ATTEMPTS: Dict[Tuple[str, str], List[float]] = defaultdict(list)
_RATE_WINDOW_SEC = 900.0
_MAX_ATTEMPTS = 5


class LoginRequest(BaseModel):
    username: str
    password: str


class StepUpRequest(BaseModel):
    password: str
    action: str = "DELETE_MEMBER"
    target_id: str


class ChangeWebPasswordRequest(BaseModel):
    current_password: str
    new_password: str


class RegisterWebAccountRequest(BaseModel):
    username: str
    password: str
    full_name: str
    member_code: str
    email: str = ""
    department_class: str = ""
    member_category: str = "STUDENT"
    primary_supervisor_id: str | None = None
    role: str = "RESEARCHER"


class ZaloInviteSessionRequest(BaseModel):
    request_id: str
    token: str


def _prune_and_check_rate(ip: str, username: str) -> None:
    key = (ip, username.lower())
    now = time.monotonic()
    with _FAILED_LOCK:
        recent = [t for t in _FAILED_ATTEMPTS[key] if now - t < _RATE_WINDOW_SEC]
        _FAILED_ATTEMPTS[key] = recent
        if len(recent) >= _MAX_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Quá nhiều lần đăng nhập thất bại; tài khoản tạm khóa 15 phút",
            )


def _record_failure(ip: str, username: str) -> None:
    key = (ip, username.lower())
    now = time.monotonic()
    with _FAILED_LOCK:
        recent = [t for t in _FAILED_ATTEMPTS[key] if now - t < _RATE_WINDOW_SEC]
        recent.append(now)
        _FAILED_ATTEMPTS[key] = recent


def _clear_failures(ip: str, username: str) -> None:
    key = (ip, username.lower())
    with _FAILED_LOCK:
        _FAILED_ATTEMPTS.pop(key, None)


@router.post("/login")
async def login(payload: LoginRequest, request: Request) -> Dict[str, Any]:
    username = payload.username.strip()
    password = payload.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="Thiếu tên đăng nhập hoặc mật khẩu")

    client_ip = request.client.host if request.client else "unknown"
    _prune_and_check_rate(client_ip, username)

    admin_user = (os.getenv("LABOPS_ADMIN_USERNAME") or settings.LABOPS_ADMIN_USERNAME or "admin").strip()
    admin_pass = os.getenv("LABOPS_ADMIN_PASSWORD", "") or getattr(settings, "LABOPS_ADMIN_PASSWORD", "")

    if username.lower() == admin_user.lower():
        if not admin_pass:
            raise HTTPException(status_code=503, detail="LABOPS_ADMIN_PASSWORD chưa được cấu hình")
        if not hmac.compare_digest(password.encode("utf-8"), admin_pass.encode("utf-8")):
            _record_failure(client_ip, username)
            service._audit(username, "AUTH_LOGIN_FAILED", "SESSION", username, "DENIED", "Sai mật khẩu quản trị")
            service._save_persistent_data()
            raise HTTPException(status_code=401, detail="Tài khoản hoặc mật khẩu không chính xác")
        _clear_failures(client_ip, username)
        ttl = 8 * 3600
        tok = issue_token(admin_user, "ADMIN", ttl=ttl)
        service._audit(admin_user, "AUTH_LOGIN", "SESSION", admin_user, "SUCCESS", "Đăng nhập quản trị viên thành công")
        service._save_persistent_data()
        return {
            "access_token": tok,
            "token_type": "bearer",
            "expires_in": ttl,
            "username": admin_user,
            "role": "ADMIN",
            "member_id": None,
            "must_change_password": False,
        }

    # Researcher / member portal login: separate web credentials FIRST, legacy workspace password fallback
    member = next((m for m in service.members if (m.get("username") or "").lower() == username.lower()), None)
    if member and member.get("status") in {
        "SUSPENDED",
        "ARCHIVED",
        "DELETE_ELIGIBLE",
        "DELETION_IN_PROGRESS",
        "DELETED",
        "REVOKED",
    }:
        raise HTTPException(
            status_code=403,
            detail=f"Tài khoản đang ở trạng thái {member.get('status')}; không được phép đăng nhập",
        )

    try:
        try:
            res = mram_service.verify_member_web_password(username=username, password=password)
        except LookupError:
            res = service.verify_member_workspace_password(username=username, password=password)
    except LookupError as exc:
        _record_failure(client_ip, username)
        raise HTTPException(status_code=401, detail="Tài khoản hoặc mật khẩu không chính xác") from exc
    except PermissionError as exc:
        msg = str(exc)
        if "ACCOUNT_LOCKED" in msg:
            raise HTTPException(status_code=429, detail=msg) from exc
        if "ACCOUNT_REVOKED" in msg or "TEMP_PASSWORD_EXPIRED" in msg:
            raise HTTPException(status_code=403, detail=msg) from exc
        _record_failure(client_ip, username)
        raise HTTPException(status_code=401, detail="Tài khoản hoặc mật khẩu không chính xác") from exc

    _clear_failures(client_ip, username)
    must_change = bool(res.get("must_change_password"))
    ttl = 900 if must_change else (8 * 3600)
    member_id = (member or {}).get("id") or res.get("member_id")
    auth_ctx = mram_service.get_auth_context_for_member(username)
    tok = issue_token(username, "RESEARCHER", member_id=member_id, ttl=ttl)
    service._audit(username, "AUTH_LOGIN", "SESSION", username, "SUCCESS", f"Đăng nhập thành viên (must_change={must_change})")
    service._save_persistent_data()
    return {
        "access_token": tok,
        "token_type": "bearer",
        "expires_in": ttl,
        "username": username,
        "role": "RESEARCHER",
        "member_id": member_id,
        "must_change_password": must_change,
        "member_category": auth_ctx.get("member_category", "STUDENT"),
        "ssh_access_state": auth_ctx.get("ssh_access_state", "NONE"),
        "full_name": auth_ctx.get("full_name", username),
    }


@router.get("/public-lecturers")
async def public_lecturers() -> Dict[str, Any]:
    return {"lecturers": mram_service.list_lecturers()}


@router.get("/check-username")
async def check_username_availability(username: str = "") -> Dict[str, Any]:
    return mram_service.check_username_availability(username)


@router.post("/zalo-invite-session")
async def exchange_zalo_invite_session(payload: ZaloInviteSessionRequest) -> Dict[str, Any]:
    """Exchange a valid 32-byte Zalo approval invite token for a scoped Admin session without login."""
    ver = mram_service.verify_approval_invitation(
        request_id=payload.request_id.strip(),
        raw_token=payload.token.strip(),
        admin_username="admin",
    )
    if not ver.get("valid"):
        raise HTTPException(
            status_code=403,
            detail=f"Liên kết phê duyệt Zalo không hợp lệ hoặc đã hết hạn ({ver.get('reason')})",
        )
    ttl = 3600
    tok = issue_token("admin", "ADMIN", ttl=ttl)
    service._audit(
        "admin",
        "AUTH_ZALO_INVITE_SESSION",
        "SSH_REQUEST",
        payload.request_id,
        "SUCCESS",
        f"Mở phiên duyệt nhanh từ liên kết Zalo ({ver.get('invitation_id')})",
    )
    service._save_persistent_data()
    return {
        "access_token": tok,
        "token_type": "bearer",
        "expires_in": ttl,
        "username": "admin",
        "role": "ADMIN",
        "request_id": payload.request_id,
    }


@router.post("/register")
@router.post("/sign-up")
async def public_web_register(payload: RegisterWebAccountRequest, request: Request) -> Dict[str, Any]:
    try:
        created = mram_service.register_public_web_account(payload.model_dump())
    except MramValidationError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
    except MemberConflictError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc

    profile = created["profile"]
    ttl = 8 * 3600
    tok = issue_token(profile["username"], "RESEARCHER", member_id=profile["member_id"], ttl=ttl)
    service._audit(
        profile["username"],
        "AUTH_REGISTER",
        "SESSION",
        profile["username"],
        "SUCCESS",
        f"Tự đăng ký tài khoản web ({profile['member_category']}) — chưa cấp SSH",
    )
    service._save_persistent_data()
    return {
        "access_token": tok,
        "token_type": "bearer",
        "expires_in": ttl,
        "username": profile["username"],
        "role": "RESEARCHER",
        "member_id": profile["member_id"],
        "must_change_password": False,
        "member_category": profile["member_category"],
        "ssh_access_state": "NONE",
        "full_name": profile["full_name"],
        "profile": profile,
    }


@router.post("/logout")
async def logout(p: Principal = Depends(require_user)) -> Dict[str, Any]:
    if p.jti:
        get_revocation_store().revoke_jti(p.jti, int(time.time()) + 8 * 3600, reason="logout")
    else:
        get_revocation_store().revoke_subject(p.username, reason="logout")
    service._audit(p.username, "AUTH_LOGOUT", "SESSION", p.username, "SUCCESS", "Đăng xuất và thu hồi phiên JWT")
    service._save_persistent_data()
    return {"success": True, "revoked": True}


@router.get("/me")
async def get_me(p: Principal = Depends(require_user)) -> Dict[str, Any]:
    ctx = mram_service.get_auth_context_for_member(p.username) if not p.is_admin else {}
    return {
        "username": p.username,
        "role": p.role,
        "member_id": p.member_id or ctx.get("member_id"),
        "is_admin": p.is_admin,
        "jti": p.jti,
        "iat": p.iat,
        "member_category": "ADMIN" if p.is_admin else ctx.get("member_category", "STUDENT"),
        "ssh_access_state": "ADMIN" if p.is_admin else ctx.get("ssh_access_state", "NONE"),
        "web_account_status": "ACTIVE" if p.is_admin else ctx.get("web_account_status", "WEB_ACTIVE"),
        "must_change_password": False if p.is_admin else bool(ctx.get("must_change_password", False)),
        "full_name": "Administrator" if p.is_admin else ctx.get("full_name", p.username),
        "ssh_expires_at": None if p.is_admin else ctx.get("ssh_expires_at"),
        "primary_supervisor_id": None if p.is_admin else ctx.get("primary_supervisor_id"),
    }


@router.post("/change-web-password")
async def change_web_password(payload: ChangeWebPasswordRequest, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    if p.is_admin:
        raise HTTPException(status_code=400, detail="Admin không dùng luồng đổi mật khẩu web thành viên")
    try:
        return mram_service.change_member_web_password(
            username=p.username,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except MramValidationError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/step-up")
async def issue_step_up(payload: StepUpRequest, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    if p.is_admin:
        admin_pass = os.getenv("LABOPS_ADMIN_PASSWORD", "") or getattr(settings, "LABOPS_ADMIN_PASSWORD", "")
        if not admin_pass:
            raise HTTPException(status_code=503, detail="LABOPS_ADMIN_PASSWORD chưa được cấu hình")
        if not hmac.compare_digest(payload.password.encode("utf-8"), admin_pass.encode("utf-8")):
            service._audit(p.username, "AUTH_STEP_UP_FAILED", payload.action, payload.target_id, "DENIED", "Sai mật khẩu xác thực nâng cao")
            service._save_persistent_data()
            raise HTTPException(status_code=401, detail="Mật khẩu xác nhận không chính xác")
    else:
        allowed_member_actions = {"CLAIM_SSH_SECRET", "REISSUE_SSH_SECRET", "CLAIM_SSH_BUNDLE"}
        if payload.action not in allowed_member_actions:
            raise HTTPException(status_code=403, detail="Thành viên không có quyền thực hiện thao tác step-up quản trị")
        if payload.target_id not in {p.username, p.member_id}:
            raise HTTPException(status_code=403, detail="Chỉ được xác thực step-up cho chính tài khoản của bạn")
        try:
            try:
                mram_service.verify_member_web_password(p.username, payload.password)
            except LookupError:
                service.verify_member_workspace_password(p.username, payload.password)
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Mật khẩu xác thực thành viên không chính xác") from exc

    step_tok = issue_step_up_token(p.username, payload.action, payload.target_id, ttl=300)
    return {
        "step_up_token": step_tok,
        "expires_in": 300,
        "action": payload.action,
        "target_id": payload.target_id,
    }
