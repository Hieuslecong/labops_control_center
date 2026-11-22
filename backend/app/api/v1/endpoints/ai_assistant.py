"""
LabOps AI Assistant, Incident Center, Identity Linking & Zalo Management Endpoints
Serves /api/v1/ai-assistant/* for Dashboard and Mobile integrations.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app.api.dependencies import require_admin, require_user
from app.core.security import Principal
from app.services.zalo_assistant import zalo_assistant_service

router = APIRouter()


class ConfigUpdateRequest(BaseModel):
    bot_token: Optional[str] = None
    authorized_users: Optional[List[str]] = None
    allowed_chat_ids: Optional[List[str]] = None
    ai_model: Optional[str] = None
    timeout_seconds: Optional[int] = None


class CommandRequest(BaseModel):
    command: str
    sender_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class AskRequest(BaseModel):
    question: str
    sender_id: Optional[str] = None


class IncidentActionRequest(BaseModel):
    note: str = ""


class LinkCodeRequest(BaseModel):
    username: str
    member_id: str = ""
    role: str = "RESEARCHER"
    display_name: str = ""
    ttl_minutes: int = 15


class DirectBindRequest(BaseModel):
    zalo_user_id: str
    username: str
    member_id: str = ""
    role: str = "RESEARCHER"
    display_name: str = ""


class SubscriptionUpdateRequest(BaseModel):
    recipient_zalo_id: str
    enabled: Optional[bool] = None
    receive_p0: Optional[bool] = None
    receive_p1: Optional[bool] = None
    receive_daily_report: Optional[bool] = None
    receive_weekly_report: Optional[bool] = None
    receive_repo_alerts: Optional[bool] = None
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    bypass_quiet_for_p0: Optional[bool] = None


class RepositoryScanEventPayload(BaseModel):
    request_data: Dict[str, Any]


class DailyBriefConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    catchup_hours: Optional[int] = None
    verify_admin_link: Optional[bool] = None
    verified_chat_id: Optional[str] = None


class DailyBriefTestRequest(BaseModel):
    confirm: bool = False
    period_key: Optional[str] = None


@router.get("/overview", summary="Tổng quan AI SRE Agent v2 & Zalo Quota")
async def get_overview(principal: Principal = Depends(require_user)):
    return await asyncio.to_thread(zalo_assistant_service.get_overview)


@router.get("/heartbeat", summary="Heartbeat kiểm tra sức khỏe SRE Worker & DB Lease")
async def get_heartbeat():
    return await asyncio.to_thread(zalo_assistant_service.get_heartbeat)


@router.get("/config", summary="Xem cấu hình Zalo Bot và AI Gateway")
async def get_config(principal: Principal = Depends(require_admin)):
    return await asyncio.to_thread(zalo_assistant_service.get_config)


@router.post("/config", summary="Cập nhật cấu hình Zalo Bot và AI Gateway")
async def update_config(payload: ConfigUpdateRequest, principal: Principal = Depends(require_admin)):
    data = payload.model_dump(exclude_unset=True)
    try:
        return await asyncio.to_thread(zalo_assistant_service.update_config, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reports", summary="Danh sách báo cáo điều hành ngày & báo cáo tuần")
async def list_reports(principal: Principal = Depends(require_user)):
    reports = await asyncio.to_thread(zalo_assistant_service.report_generator.list_reports)
    return {"reports": reports}


@router.post("/reports/generate", summary="Khởi tạo báo cáo tuần mới")
async def generate_report(principal: Principal = Depends(require_admin)):
    return await asyncio.to_thread(
        zalo_assistant_service.report_generator.generate_weekly_report,
        author=f"Admin ({principal.username})",
    )


@router.post("/reports/generate-daily", summary="Khởi tạo Daily Executive Briefing (08:00 ICT)")
async def generate_daily_report(force: bool = Query(False), principal: Principal = Depends(require_admin)):
    return await asyncio.to_thread(
        zalo_assistant_service.report_generator.generate_daily_executive_briefing,
        force=force,
        author=f"Admin ({principal.username})",
        ai_client=zalo_assistant_service.ai_client,
        telemetry_collector=zalo_assistant_service.telemetry_collector,
        incident_engine=zalo_assistant_service.incident_engine,
    )


@router.get("/daily-brief/status", summary="Trạng thái lịch gửi, xác minh Hiếu Lê và lịch sử Daily Executive Brief")
async def get_daily_brief_status(principal: Principal = Depends(require_admin)):
    return await asyncio.to_thread(zalo_assistant_service.get_daily_brief_status)


@router.post("/daily-brief/config", summary="Cấu hình bật/tắt Daily Brief & xác minh Admin Zalo")
async def update_daily_brief_config(payload: DailyBriefConfigRequest, principal: Principal = Depends(require_admin)):
    try:
        return await asyncio.to_thread(
            zalo_assistant_service.update_daily_brief_config,
            payload.model_dump(exclude_unset=True),
            actor=principal.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/daily-brief/preview", summary="Dry-run tạo bản xem trước Daily Brief từ dữ liệu thật (không gửi Zalo)")
async def preview_daily_brief(force: bool = Query(True), principal: Principal = Depends(require_admin)):
    return await asyncio.to_thread(
        zalo_assistant_service.preview_daily_brief,
        author=f"Admin Preview ({principal.username})",
        force=force,
    )


@router.post("/daily-brief/test-delivery", summary="Gửi thử 01 tin nhắn Daily Brief tới Hiếu Lê (yêu cầu xác nhận)")
async def test_daily_brief_delivery(payload: DailyBriefTestRequest, principal: Principal = Depends(require_admin)):
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Yêu cầu confirm=true trước khi gửi tin nhắn kiểm thử thật qua Zalo.")
    try:
        res = await zalo_assistant_service.deliver_daily_brief(period_key=payload.period_key, is_test=True)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not res.get("success") and res.get("status") == "RECIPIENT_NOT_VERIFIED":
        raise HTTPException(status_code=400, detail=res.get("verification", {}).get("instruction") or "RECIPIENT_NOT_VERIFIED")
    return res


@router.post("/daily-brief/retry/{period_key}", summary="Gửi lại Daily Brief đủ điều kiện retry")
async def retry_daily_brief_delivery(period_key: str, principal: Principal = Depends(require_admin)):
    existing = await asyncio.to_thread(zalo_assistant_service.report_generator.get_daily_report_by_period, period_key)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy Daily Brief kỳ {period_key}.")
    st = str(existing.get("status") or "").upper()
    if st in {"SENT", "DELIVERY_UNKNOWN"}:
        raise HTTPException(status_code=409, detail=f"Kỳ {period_key} đang ở trạng thái {st}, không thể gửi lại để tránh trùng lặp.")
    if st not in {"READY", "FAILED", "RETRY_PENDING", "RECIPIENT_NOT_VERIFIED", "MISSED_WINDOW"}:
        raise HTTPException(status_code=400, detail=f"Trạng thái {st} không đủ điều kiện retry.")
    try:
        return await zalo_assistant_service.deliver_daily_brief(period_key=period_key, is_test=False)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reports/{report_id}", summary="Chi tiết báo cáo")
async def get_report_detail(report_id: str, principal: Principal = Depends(require_user)):
    report = await asyncio.to_thread(zalo_assistant_service.report_generator.get_report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo yêu cầu.")
    return report


@router.get("/reports/{report_id}/download", summary="Tải xuống tệp Markdown báo cáo")
async def download_report(report_id: str, principal: Principal = Depends(require_user)):
    report = await asyncio.to_thread(zalo_assistant_service.report_generator.get_report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo yêu cầu.")
    content = report.get("content", "")
    filename = f"{report_id}.md"
    return Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tools", summary="Quyền hạn, danh mục công cụ và Tool Audit")
async def get_tools_and_permissions(principal: Principal = Depends(require_admin)):
    return await asyncio.to_thread(zalo_assistant_service.get_tools_and_permissions)


@router.get("/history", summary="Lịch sử tin nhắn, quota outbox và delivery attempts")
async def get_history(principal: Principal = Depends(require_admin)):
    return await asyncio.to_thread(zalo_assistant_service.get_history)


# ---------------- Incident Center ----------------


@router.get("/incidents", summary="Danh sách sự cố hạ tầng & bảo mật")
async def list_incidents(
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_user),
):
    owner_filter = None if principal.is_admin else principal.username
    items = await asyncio.to_thread(
        zalo_assistant_service.incident_engine.list_incidents,
        status=status,
        severity=severity,
        source=source,
        owner_id=owner_filter,
        limit=limit,
    )
    return {"incidents": items}


@router.get("/incidents/{incident_id}", summary="Chi tiết & timeline của một sự cố")
async def get_incident_detail(incident_id: str, principal: Principal = Depends(require_user)):
    inc = await asyncio.to_thread(zalo_assistant_service.incident_engine.get_incident, incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự cố.")
    if not principal.is_admin and inc.get("owner_id") != principal.username:
        raise HTTPException(status_code=403, detail="Bạn không có quyền xem sự cố của thành viên khác.")
    return inc


@router.post("/incidents/{incident_id}/ack", summary="Xác nhận đang xử lý sự cố")
async def acknowledge_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    principal: Principal = Depends(require_user),
):
    try:
        return await asyncio.to_thread(
            zalo_assistant_service.incident_engine.acknowledge_incident,
            incident_id,
            actor=principal.username,
            role=principal.role,
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/incidents/{incident_id}/resolve", summary="Đóng sự cố (RESOLVED - Chỉ Admin)")
async def resolve_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    principal: Principal = Depends(require_admin),
):
    try:
        return await asyncio.to_thread(
            zalo_assistant_service.incident_engine.resolve_incident,
            incident_id,
            actor=principal.username,
            role=principal.role,
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/incidents/evaluate", summary="Chạy đánh giá Telemetry & Rule Engine ngay lập tức")
async def trigger_incident_evaluation(principal: Principal = Depends(require_admin)):
    def _run():
        snap = zalo_assistant_service.telemetry_collector.collect_snapshot()
        zalo_assistant_service.telemetry_collector.record_sample(snap)
        events = zalo_assistant_service.incident_engine.evaluate_snapshot(snap)
        return {"overall_health": snap.get("overall_health"), "evaluated_events": len(events), "events": events}

    return await asyncio.to_thread(_run)


# ---------------- Zalo Identity Linking & Subscriptions ----------------


@router.get("/identity-links", summary="Danh sách liên kết Zalo User ID <-> LabOps Account")
async def list_identity_links(principal: Principal = Depends(require_admin)):
    links = await asyncio.to_thread(zalo_assistant_service.identity_rbac.list_links)
    return {"links": links}


@router.post("/identity-links/code", summary="Tạo mã liên kết một lần (/link CODE)")
async def generate_identity_link_code(payload: LinkCodeRequest, principal: Principal = Depends(require_admin)):
    try:
        return await asyncio.to_thread(
            zalo_assistant_service.identity_rbac.generate_link_code,
            username=payload.username,
            member_id=payload.member_id,
            role=payload.role,
            actor=principal.username,
            display_name=payload.display_name,
            ttl_minutes=payload.ttl_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/identity-links/bind", summary="Admin gán trực tiếp Zalo User ID vào tài khoản LabOps")
async def bind_identity_direct(payload: DirectBindRequest, principal: Principal = Depends(require_admin)):
    try:
        return await asyncio.to_thread(
            zalo_assistant_service.identity_rbac.admin_bind_identity,
            zalo_user_id=payload.zalo_user_id,
            username=payload.username,
            member_id=payload.member_id,
            role=payload.role,
            display_name=payload.display_name,
            admin_actor=principal.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/identity-links/{link_id}", summary="Thu hồi liên kết Zalo User ID")
async def revoke_identity_link(link_id: str, principal: Principal = Depends(require_admin)):
    ok = await asyncio.to_thread(zalo_assistant_service.identity_rbac.revoke_link, link_id, principal.username)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy liên kết đang hoạt động.")
    return {"revoked": True, "link_id": link_id}


@router.get("/subscriptions", summary="Danh sách cấu hình nhận cảnh báo theo người nhận")
async def list_subscriptions(principal: Principal = Depends(require_admin)):
    subs = await asyncio.to_thread(zalo_assistant_service.identity_rbac.list_subscriptions)
    return {"subscriptions": subs}


@router.post("/subscriptions", summary="Cập nhật cấu hình nhận cảnh báo & Quiet Hours")
async def update_subscription(payload: SubscriptionUpdateRequest, principal: Principal = Depends(require_admin)):
    try:
        return await asyncio.to_thread(
            zalo_assistant_service.identity_rbac.update_subscription,
            payload.recipient_zalo_id,
            payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/events/repository-scan", summary="Event webhook nội bộ khi Repository Guard quét xong")
async def receive_repository_scan_event(
    payload: RepositoryScanEventPayload,
    principal: Principal = Depends(require_user),
):
    await asyncio.to_thread(zalo_assistant_service.repo_alert_monitor.push_event, payload.request_data)
    return {"queued": True, "request_id": payload.request_data.get("id")}


# ---------------- Command & AI Investigation Endpoints ----------------


@router.post("/command", summary="Gửi lệnh tới Zalo Bot Engine")
async def execute_command(req: CommandRequest, principal: Principal = Depends(require_user)):
    res = await zalo_assistant_service.bot_engine.ahandle_command(
        command_text=req.command,
        sender_id=principal.username,
        idempotency_key=req.idempotency_key,
        source="dashboard",
        principal_role=principal.role,
    )
    if res.get("error") == "FORBIDDEN":
        raise HTTPException(status_code=403, detail=res.get("reply") or "Forbidden")
    if res.get("error") == "RATE_LIMITED":
        raise HTTPException(status_code=429, detail=res.get("reply") or "Rate limit exceeded")
    return res


@router.post("/ask", summary="Đặt câu hỏi điều tra hạ tầng cho AI SRE Agent v2")
async def ask_assistant(req: AskRequest, principal: Principal = Depends(require_user)):
    res = await zalo_assistant_service.bot_engine.ahandle_command(
        command_text=f"/ask {req.question}",
        sender_id=principal.username,
        source="dashboard",
        principal_role=principal.role,
    )
    if res.get("error") == "RATE_LIMITED":
        raise HTTPException(status_code=429, detail=res.get("reply") or "Rate limit exceeded")
    return res
