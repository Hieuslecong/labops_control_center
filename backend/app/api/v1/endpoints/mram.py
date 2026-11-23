"""
FastAPI endpoints for LabOps Member & Research Access Management (MRAM v1.0).
Provides clean REST routes under /api/v1/* for Admin and Researcher/Lecturer workflows.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.api.dependencies import require_admin, require_user
from app.core.security import AuthError, Principal, verify_step_up_token
from app.services.labops_service import MemberConflictError, service as labops_service
from app.services.mram_service import MramConflictError, MramValidationError, mram_service

router = APIRouter()


def _handle_mram_err(exc: Exception) -> None:
    if isinstance(exc, MramValidationError):
        raise HTTPException(status_code=exc.http_status, detail={"code": exc.code, "message": exc.message, "details": exc.details}) from exc
    if isinstance(exc, (MramConflictError, MemberConflictError)):
        raise HTTPException(status_code=exc.http_status, detail={"code": exc.code, "message": exc.message, "details": exc.details}) from exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


def _require_step_up_header(
    p: Principal,
    step_up_token: Optional[str],
    *,
    allowed_actions: set[str],
    expected_target: Optional[str] = None,
) -> Dict[str, Any]:
    if not step_up_token:
        raise HTTPException(status_code=403, detail="Thiếu mã xác thực nâng cao (X-Step-Up-Token)")
    last_err: Optional[Exception] = None
    for act in sorted(allowed_actions):
        try:
            return verify_step_up_token(
                step_up_token,
                expected_username=p.username,
                expected_action=act,
                expected_target_id=expected_target or "",
            )
        except AuthError as exc:
            last_err = exc
            if "STEP_UP_TARGET_MISMATCH" in str(exc):
                continue
            raise HTTPException(status_code=403, detail=f"Xác thực nâng cao không hợp lệ: {exc}") from exc
    raise HTTPException(status_code=403, detail=f"Xác thực nâng cao không hợp lệ: {last_err}")


# ------------------------------------------------------------------
# Admin Member Web-Only Accounts & Supervisor Grouping (P1 & P2)
# ------------------------------------------------------------------
@router.post("/admin/member-web-accounts")
async def admin_create_member_web_account(
    payload: Dict[str, Any],
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        return mram_service.create_web_member_account(payload, actor=p.username)
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.put("/admin/members/{member_id}/supervisor")
async def admin_assign_member_supervisor(
    member_id: str,
    payload: Dict[str, Any],
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        sup_id = str(payload.get("primary_supervisor_id") or "").strip()
        if not sup_id:
            raise MramValidationError("MISSING_SUPERVISOR_ID", "Vui lòng chọn primary_supervisor_id")
        return mram_service.assign_supervisor(
            student_member_id=member_id,
            primary_supervisor_id=sup_id,
            direct_manager_id=str(payload.get("direct_manager_id") or "").strip() or None,
            actor=p.username,
            change_reason=str(payload.get("change_reason") or "Admin cập nhật GVHD"),
        )
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.get("/admin/supervisor-groups")
async def admin_list_supervisor_groups(p: Principal = Depends(require_admin)) -> Dict[str, Any]:
    return mram_service.get_supervisor_groups()


@router.get("/me/supervised-students")
async def list_my_supervised_students(p: Principal = Depends(require_user)) -> Dict[str, Any]:
    try:
        return mram_service.get_supervised_students(p.username, is_admin=p.is_admin)
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.get("/me/lecturers")
async def list_verified_lecturers(p: Principal = Depends(require_user)) -> Dict[str, Any]:
    return {"lecturers": mram_service.list_lecturers()}


# ------------------------------------------------------------------
# Member SSH Requests, Re-apply, Renewal & Handover (P3 & P7)
# ------------------------------------------------------------------
@router.post("/me/ssh-requests")
async def member_submit_ssh_request(
    payload: Dict[str, Any],
    request: Request,
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    try:
        client_ip = request.client.host if request.client else ""
        return mram_service.submit_ssh_request(username=p.username, payload=payload, client_ip=client_ip)
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.get("/me/ssh-requests")
async def member_list_my_ssh_requests(p: Principal = Depends(require_user)) -> Dict[str, Any]:
    return {"requests": mram_service.list_requests_for_member(p.username)}


@router.get("/me/ssh-requests/{request_id}")
async def member_get_ssh_request(request_id: str, p: Principal = Depends(require_user)) -> Dict[str, Any]:
    try:
        return mram_service.get_request_detail(request_id, viewer_username=p.username, is_admin=p.is_admin)
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.post("/me/ssh-requests/{request_id}/reapply")
async def member_reapply_ssh_request(
    request_id: str,
    request: Request,
    payload: Optional[Dict[str, Any]] = None,
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    try:
        client_ip = request.client.host if request.client else ""
        return mram_service.reapply_ssh_request(
            username=p.username,
            request_id=request_id,
            payload=payload or {},
            client_ip=client_ip,
        )
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.post("/me/ssh-requests/{request_id}/renew")
async def member_renew_ssh_request(
    request_id: str,
    payload: Dict[str, Any],
    request: Request,
    p: Principal = Depends(require_user),
) -> Dict[str, Any]:
    try:
        client_ip = request.client.host if request.client else ""
        data = dict(payload)
        data["parent_request_id"] = request_id
        return mram_service.submit_renewal_request(username=p.username, payload=data, client_ip=client_ip)
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.get("/me/ssh-access")
async def member_get_ssh_access_handover(p: Principal = Depends(require_user)) -> Dict[str, Any]:
    try:
        return mram_service.get_member_ssh_access_handover(p.username)
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.post("/me/ssh-access/claim")
async def member_claim_ssh_secret(
    p: Principal = Depends(require_user),
    step_up_token: Optional[str] = Header(default=None, alias="X-Step-Up-Token"),
) -> Dict[str, Any]:
    try:
        claims = _require_step_up_header(
            p,
            step_up_token,
            allowed_actions={"CLAIM_SSH_SECRET", "CLAIM_SSH_BUNDLE"},
            expected_target=p.username,
        )
        return mram_service.claim_member_ssh_secret(username=p.username, step_up_jti=str(claims.get("jti") or ""))
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.post("/me/ssh-access/reissue")
async def member_reissue_ssh_secret(
    p: Principal = Depends(require_user),
    step_up_token: Optional[str] = Header(default=None, alias="X-Step-Up-Token"),
) -> Dict[str, Any]:
    try:
        claims = _require_step_up_header(
            p,
            step_up_token,
            allowed_actions={"REISSUE_SSH_SECRET", "CLAIM_SSH_SECRET"},
            expected_target=p.username,
        )
        return mram_service.reissue_member_ssh_secret(username=p.username, step_up_jti=str(claims.get("jti") or ""))
    except Exception as exc:
        _handle_mram_err(exc)
        raise


# ------------------------------------------------------------------
# Admin SSH Approval Queue, Validation, Invitations & Sweep (P4, P5, P6)
# ------------------------------------------------------------------
@router.get("/admin/ssh-requests")
async def admin_list_ssh_requests(
    status: Optional[str] = Query(default=None),
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    return {"requests": mram_service.list_requests_for_admin(status_filter=status)}


@router.get("/admin/ssh-requests/{request_id}")
async def admin_get_ssh_request(
    request_id: str,
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        req = mram_service.get_request_detail(request_id, viewer_username=p.username, is_admin=True)
        hosts = labops_service.get_v2_hosts()
        from app.services.storage_inventory_service import storage_service

        pools_by_host = {str(h["id"]): storage_service.get_storage_pools(str(h["id"])) for h in hosts}
        gpus_by_host: Dict[str, Any] = {}
        for g in labops_service.gpus:
            hid = str(g.get("server_id") or g.get("host_id") or "workstation").lower()
            gpus_by_host.setdefault(hid, []).append(g)
        if not gpus_by_host.get("workstation"):
            gpus_by_host["workstation"] = [
                {
                    "id": "workstation-gpu-01",
                    "uuid": "GPU-workstation-a30-01",
                    "name": "NVIDIA A30 (24 GB VRAM)",
                    "server_id": "workstation",
                    "vram_total_mb": 24576,
                }
            ]
        return {
            "request": req,
            "hosts": hosts,
            "pools_by_host": pools_by_host,
            "gpus_by_host": gpus_by_host,
        }
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.get("/admin/approval-invitations/verify")
async def admin_verify_approval_invitation(
    request_id: str = Query(...),
    token: str = Query(...),
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    """Read-only GET check of Zalo invitation token — never consumes or mutates state."""
    return mram_service.verify_approval_invitation(
        request_id=request_id,
        raw_token=token,
        admin_username=p.username,
    )


@router.post("/admin/ssh-requests/{request_id}/validate")
async def admin_validate_ssh_request(
    request_id: str,
    payload: Dict[str, Any],
    p: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        return mram_service.validate_request_config(request_id, payload)
    except Exception as exc:
        _handle_mram_err(exc)
        raise


def _authorize_admin_decision(
    p: Principal,
    *,
    request_id: str,
    payload: Dict[str, Any],
    step_up_token: Optional[str],
    allowed_actions: set[str],
) -> None:
    inv_tok = str(payload.get("invitation_token") or "").strip()
    if inv_tok:
        ver = mram_service.verify_approval_invitation(
            request_id=request_id,
            raw_token=inv_tok,
            admin_username=p.username,
        )
        if ver.get("valid"):
            return
        raise HTTPException(
            status_code=403,
            detail=f"Liên kết phê duyệt Zalo không hợp lệ hoặc đã dùng ({ver.get('reason')})",
        )
    _require_step_up_header(
        p,
        step_up_token,
        allowed_actions=allowed_actions,
        expected_target=request_id,
    )


@router.post("/admin/ssh-requests/{request_id}/approve")
async def admin_approve_ssh_request(
    request_id: str,
    payload: Dict[str, Any],
    p: Principal = Depends(require_admin),
    step_up_token: Optional[str] = Header(default=None, alias="X-Step-Up-Token"),
) -> Dict[str, Any]:
    try:
        _authorize_admin_decision(
            p,
            request_id=request_id,
            payload=payload,
            step_up_token=step_up_token,
            allowed_actions={"APPROVE_SSH_REQUEST"},
        )
        return mram_service.decide_ssh_request(
            request_id=request_id,
            decision="APPROVED",
            approver_username=p.username,
            reason=str(payload.get("reason") or "Đã kiểm tra và phê duyệt cấp phát"),
            config=payload.get("config") or payload,
            expected_version=payload.get("expected_version"),
            invitation_token=payload.get("invitation_token"),
        )
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.post("/admin/ssh-requests/{request_id}/reject")
async def admin_reject_ssh_request(
    request_id: str,
    payload: Dict[str, Any],
    p: Principal = Depends(require_admin),
    step_up_token: Optional[str] = Header(default=None, alias="X-Step-Up-Token"),
) -> Dict[str, Any]:
    try:
        _authorize_admin_decision(
            p,
            request_id=request_id,
            payload=payload,
            step_up_token=step_up_token,
            allowed_actions={"REJECT_SSH_REQUEST", "APPROVE_SSH_REQUEST"},
        )
        return mram_service.decide_ssh_request(
            request_id=request_id,
            decision="REJECTED",
            approver_username=p.username,
            reason=str(payload.get("reason") or ""),
            expected_version=payload.get("expected_version"),
            invitation_token=payload.get("invitation_token"),
        )
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.post("/admin/ssh-requests/{request_id}/request-info")
async def admin_request_more_info(
    request_id: str,
    payload: Dict[str, Any],
    p: Principal = Depends(require_admin),
    step_up_token: Optional[str] = Header(default=None, alias="X-Step-Up-Token"),
) -> Dict[str, Any]:
    try:
        _authorize_admin_decision(
            p,
            request_id=request_id,
            payload=payload,
            step_up_token=step_up_token,
            allowed_actions={"REQUEST_INFO_SSH_REQUEST", "APPROVE_SSH_REQUEST"},
        )
        return mram_service.decide_ssh_request(
            request_id=request_id,
            decision="NEED_INFO",
            approver_username=p.username,
            reason=str(payload.get("reason") or ""),
            expected_version=payload.get("expected_version"),
            invitation_token=payload.get("invitation_token"),
        )
    except Exception as exc:
        _handle_mram_err(exc)
        raise


@router.post("/admin/mram/sweep-sla")
async def admin_sweep_sla_and_expiry(p: Principal = Depends(require_admin)) -> Dict[str, Any]:
    expired_reqs = mram_service.expire_overdue_requests()
    expired_ssh = mram_service.sweep_expired_ssh_access()
    return {
        "expired_requests": expired_reqs,
        "expired_ssh_accounts": expired_ssh,
    }
