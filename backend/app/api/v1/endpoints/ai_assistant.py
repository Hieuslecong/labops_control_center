"""
LabOps AI Assistant, Incident Center, Identity Linking & Zalo Management Endpoints
Serves /api/v1/ai-assistant/* for Dashboard and Mobile integrations.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel

from app.api.dependencies import require_admin, require_user
from app.core.security import AuthError, Principal, verify_step_up_token
from app.services.assistant_v4.task_scheduler import WatchSpec
from app.services.zalo_assistant import zalo_assistant_service
from app.services.zalo_assistant.identity_rbac import ZaloPrincipal

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
async def get_overview(principal: Principal = Depends(require_admin)):
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
async def list_reports(principal: Principal = Depends(require_admin)):
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
async def get_report_detail(report_id: str, principal: Principal = Depends(require_admin)):
    report = await asyncio.to_thread(zalo_assistant_service.report_generator.get_report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo yêu cầu.")
    return report


@router.get("/reports/{report_id}/download", summary="Tải xuống tệp Markdown báo cáo")
async def download_report(report_id: str, principal: Principal = Depends(require_admin)):
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


# ---------------- Personal AI Agent V4 Endpoints ----------------


class V4TaskCreateRequest(BaseModel):
    title: str
    run_at: str
    cadence_minutes: Optional[int] = None
    missed_run_policy: str = "skip"
    expires_hours: Optional[int] = None
    recipient_id: Optional[str] = None


class V4WatchCreateRequest(BaseModel):
    target_id: str
    metric: str
    comparator: str
    threshold: float
    unit: str = ""
    sustained_seconds: int = 300
    once: bool = True
    expires_hours: int = 24
    title: str = ""


class V4CancelRequest(BaseModel):
    reason: str = "USER_CANCELLED"


class V4InvestigationFeedbackRequest(BaseModel):
    feedback: str


class V4ApprovalConfirmRequest(BaseModel):
    reason: str = ""
    step_up_token: Optional[str] = None


class V4PreferenceUpdateRequest(BaseModel):
    key: str = ""
    value: Any = None
    timezone: Optional[str] = None


class V4MemoryClearRequest(BaseModel):
    channel: str = "zalo"
    chat_id: Optional[str] = None


class V4RetentionRunRequest(BaseModel):
    dry_run: Optional[bool] = None
    force: bool = True


def _principal_to_zalo(p: Principal) -> ZaloPrincipal:
    return ZaloPrincipal(
        zalo_user_id=p.username,
        username=p.username,
        member_id=p.member_id or p.username,
        role=p.role,
        display_name=p.username,
        is_authorized=True,
        is_linked=True,
    )


def _require_v4():
    v4 = getattr(zalo_assistant_service, "v4", None)
    if v4 is None:
        raise HTTPException(status_code=503, detail="Personal AI V4 facade chưa được khởi tạo")
    return v4


@router.get("/v4/health", summary="Trạng thái & feature flags Personal AI V4")
async def get_v4_health(principal: Principal = Depends(require_user)):
    v4 = _require_v4()
    return await asyncio.to_thread(v4.get_health)


@router.get("/v4/metrics", summary="Chỉ số outbox, quota và watchdog V4 (Chỉ Admin)")
async def get_v4_metrics(principal: Principal = Depends(require_admin)):
    v4 = _require_v4()
    return await asyncio.to_thread(v4.get_metrics)


@router.post("/v4/retention/run", summary="Chạy bảo trì lưu trữ V4 có kiểm soát (mặc định dry-run, Chỉ Admin)")
async def run_v4_retention(
    req: V4RetentionRunRequest,
    principal: Principal = Depends(require_admin),
):
    v4 = _require_v4()
    return await asyncio.to_thread(
        v4.retention.run,
        dry_run=True if req.dry_run is None and not getattr(v4.config.retention, "enabled", False) else req.dry_run,
        force=req.force,
        actor=principal.username,
    )


@router.post("/v4/config/reload", summary="Nạp lại cấu hình YAML/env cho V4 facade (Chỉ Admin)")
async def reload_v4_config(principal: Principal = Depends(require_admin)):
    v4 = _require_v4()
    return await asyncio.to_thread(v4.reload_config, actor=principal.username)


@router.get("/v4/events/coverage", summary="Ma trận bao phủ nguồn sự kiện V4 (Chỉ Admin)")
async def get_v4_event_coverage(principal: Principal = Depends(require_admin)):
    v4 = _require_v4()
    return await asyncio.to_thread(v4.get_event_coverage)


@router.get("/v4/inbox", summary="Lịch sử durable inbox tin nhắn V4")
async def list_v4_inbox(
    limit: int = Query(default=30, ge=1, le=200),
    principal: Principal = Depends(require_user),
):
    v4 = _require_v4()
    pid = None if principal.is_admin else principal.username
    items = await asyncio.to_thread(v4.inbox.list_recent, limit=limit, principal_id=pid)
    return {"items": items}


@router.get("/v4/tasks", summary="Danh sách tác vụ & watch rules V4")
async def list_v4_tasks(
    status: Optional[str] = Query(default=None),
    principal: Principal = Depends(require_user),
):
    v4 = _require_v4()
    owner_filter = None if principal.is_admin else principal.username
    items = await asyncio.to_thread(
        v4.tasks.list_tasks,
        owner_id=owner_filter,
        is_admin=principal.is_admin,
        status=status,
    )
    return {"tasks": items}


@router.post("/v4/tasks", summary="Tạo tác vụ nhắc việc / báo cáo định kỳ V4")
async def create_v4_task(req: V4TaskCreateRequest, principal: Principal = Depends(require_user)):
    v4 = _require_v4()
    if not getattr(v4.config, "task_manager", False):
        raise HTTPException(status_code=400, detail="TASK_MANAGER_DISABLED: assistant_v4.task_manager đang tắt")
    if not principal.is_admin and req.recipient_id and req.recipient_id.strip() != principal.username:
        raise HTTPException(status_code=403, detail="Non-admin cannot schedule reminders for another recipient")
    try:
        run_dt = datetime.fromisoformat(req.run_at.strip().replace("Z", "+00:00"))
        if run_dt.tzinfo is None:
            run_dt = run_dt.replace(tzinfo=timezone.utc)
        task = await asyncio.to_thread(
            v4.tasks.create_reminder,
            owner_id=principal.username,
            owner_role=principal.role,
            title=req.title,
            run_at=run_dt,
            cadence_minutes=req.cadence_minutes,
            missed_run_policy=req.missed_run_policy,
            recipient_id=req.recipient_id or principal.username,
        )
        return {"task": task}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v4/tasks/{task_id}/cancel", summary="Hủy tác vụ V4")
async def cancel_v4_task(
    task_id: str,
    req: V4CancelRequest,
    principal: Principal = Depends(require_user),
):
    v4 = _require_v4()
    try:
        task = await asyncio.to_thread(
            v4.tasks.cancel_task,
            task_id,
            actor_id=principal.username,
            actor_role=principal.role,
            reason=req.reason,
        )
        return {"task": task}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/v4/watches", summary="Danh sách watch rules đang theo dõi")
async def list_v4_watches(
    status: Optional[str] = Query(default=None),
    principal: Principal = Depends(require_user),
):
    v4 = _require_v4()
    owner_filter = None if principal.is_admin else principal.username
    items = await asyncio.to_thread(
        v4.tasks.list_tasks,
        owner_id=owner_filter,
        is_admin=principal.is_admin,
        status=status,
    )
    watches = [t for t in items if "watch_rule" in t]
    return {"watches": watches}


@router.post("/v4/watches", summary="Tạo watch rule theo dõi ngưỡng V4")
async def create_v4_watch(req: V4WatchCreateRequest, principal: Principal = Depends(require_user)):
    v4 = _require_v4()
    if not getattr(v4.config, "task_manager", False):
        raise HTTPException(status_code=400, detail="TASK_MANAGER_DISABLED: assistant_v4.task_manager đang tắt")
    try:
        spec = WatchSpec(
            target_id=req.target_id,
            metric=req.metric,
            comparator=req.comparator,
            threshold=req.threshold,
            unit=req.unit,
            sustained_seconds=req.sustained_seconds,
            once=req.once,
            expires_hours=req.expires_hours,
        )
        task = await asyncio.to_thread(
            v4.tasks.create_watch,
            owner_id=principal.username,
            owner_role=principal.role,
            spec=spec,
            title=req.title,
        )
        return {"watch": task}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v4/watches/{task_id}/cancel", summary="Hủy watch rule V4")
async def cancel_v4_watch(
    task_id: str,
    req: V4CancelRequest,
    principal: Principal = Depends(require_user),
):
    v4 = _require_v4()
    try:
        task = await asyncio.to_thread(
            v4.tasks.cancel_task,
            task_id,
            actor_id=principal.username,
            actor_role=principal.role,
            reason=req.reason,
        )
        return {"watch": task}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/v4/investigations", summary="Danh sách điều tra RCA dựa trên bằng chứng (Chỉ Admin)")
async def list_v4_investigations(
    incident_id: Optional[str] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    principal: Principal = Depends(require_admin),
):
    v4 = _require_v4()
    items = await asyncio.to_thread(v4.investigations.list_investigations, incident_id=incident_id, limit=limit)
    return {"investigations": items}


@router.get("/v4/investigations/{run_id}", summary="Chi tiết một lần điều tra RCA V4 (Chỉ Admin)")
async def get_v4_investigation(run_id: str, principal: Principal = Depends(require_admin)):
    v4 = _require_v4()
    item = await asyncio.to_thread(v4.investigations.get_investigation, run_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Investigation '{run_id}' not found")
    return {"investigation": item}


@router.post("/v4/investigations/{run_id}/feedback", summary="Gửi nhận xét đánh giá điều tra RCA V4 (Chỉ Admin)")
async def submit_v4_investigation_feedback(
    run_id: str,
    req: V4InvestigationFeedbackRequest,
    principal: Principal = Depends(require_admin),
):
    v4 = _require_v4()
    try:
        updated = await asyncio.to_thread(
            v4.investigations.submit_feedback,
            run_id,
            reviewer=principal.username,
            feedback=req.feedback,
        )
        return {"investigation": updated}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/v4/approvals", summary="Danh sách Action Proposals chờ xác nhận V4")
async def list_v4_approvals(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_user),
):
    v4 = _require_v4()
    items = await asyncio.to_thread(
        v4.approvals.list_proposals,
        actor_id=principal.username,
        is_admin=principal.is_admin,
        limit=limit,
    )
    return {"approvals": items}


@router.post("/v4/approvals/{action_id}/confirm", summary="Xác nhận thực thi Action Proposal qua Dashboard POST (Chỉ Admin, Step-Up với R3)")
async def confirm_v4_approval(
    action_id: str,
    req: V4ApprovalConfirmRequest,
    x_labops_step_up_token: Optional[str] = Header(default=None, alias="X-LabOps-Step-Up-Token"),
    x_step_up_token: Optional[str] = Header(default=None, alias="X-Step-Up-Token"),
    principal: Principal = Depends(require_admin),
):
    v4 = _require_v4()
    match = await asyncio.to_thread(v4.approvals.get_proposal, action_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Action proposal '{action_id}' not found")

    step_up_ok = False
    if str(match.get("risk_tier") or "").upper() == "R3":
        tok = (x_labops_step_up_token or x_step_up_token or req.step_up_token or "").strip()
        try:
            verify_step_up_token(
                tok,
                expected_username=principal.username,
                expected_action="v4_confirm_action",
                expected_target_id=action_id,
            )
            step_up_ok = True
        except AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    try:
        return await asyncio.to_thread(
            v4.approvals.confirm_dashboard_action,
            action_id=action_id,
            actor_username=principal.username,
            actor_role=principal.role,
            step_up_ok=step_up_ok,
            reason=req.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/v4/preferences", summary="Đọc tùy chọn cá nhân V4")
async def get_v4_preferences(principal: Principal = Depends(require_user)):
    v4 = _require_v4()
    return await asyncio.to_thread(v4.memory.get_preferences, principal.username)


@router.post("/v4/preferences", summary="Cập nhật tùy chọn cá nhân opt-in V4")
async def update_v4_preferences(req: V4PreferenceUpdateRequest, principal: Principal = Depends(require_user)):
    v4 = _require_v4()
    return await asyncio.to_thread(
        v4.memory.set_preference,
        principal.username,
        req.key,
        req.value,
        timezone_name=req.timezone,
    )


@router.get("/v4/memory", summary="Tổng quan phiên bộ nhớ hội thoại theo scope người dùng")
async def get_v4_memory(
    channel: str = Query(default="zalo"),
    chat_id: str = Query(default=""),
    principal: Principal = Depends(require_user),
):
    v4 = _require_v4()
    return await asyncio.to_thread(
        v4.memory.get_memory_overview,
        principal_id=principal.username,
        channel=channel,
        chat_id=chat_id,
    )


@router.post("/v4/memory/clear", summary="Xóa ngữ cảnh hội thoại hiện tại (không xóa audit log)")
async def clear_v4_memory(req: V4MemoryClearRequest, principal: Principal = Depends(require_user)):
    v4 = _require_v4()
    return await asyncio.to_thread(
        v4.memory.clear_active_context,
        principal_id=principal.username,
        channel=req.channel,
        chat_id=req.chat_id or principal.username,
    )


@router.get("/v4/connectors", summary="Trạng thái các connector mở rộng V4 (Chỉ Admin)")
async def get_v4_connectors(principal: Principal = Depends(require_admin)):
    v4 = _require_v4()
    try:
        connectors = await asyncio.to_thread(v4.connectors.get_connector_statuses, _principal_to_zalo(principal))
        return {"connectors": connectors}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/v4/webhooks/github", summary="Webhook GitHub đã xác thực HMAC-SHA256")
async def ingest_v4_github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default="", alias="X-Hub-Signature-256"),
    x_github_delivery: str = Header(default="", alias="X-GitHub-Delivery"),
    x_github_event: str = Header(default="push", alias="X-GitHub-Event"),
):
    v4 = _require_v4()
    raw_body = await request.body()
    secret = os.getenv("ASSISTANT_V4_GITHUB_WEBHOOK_SECRET", "")
    try:
        return await asyncio.to_thread(
            v4.connectors.ingest_github_webhook,
            delivery_id=x_github_delivery,
            event_name=x_github_event,
            signature_header=x_hub_signature_256,
            webhook_secret=secret,
            raw_body=raw_body,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
