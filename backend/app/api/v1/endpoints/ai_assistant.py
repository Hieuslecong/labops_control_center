"""
LabOps AI Assistant & Zalo Management Endpoints
Serves /api/v1/ai-assistant/* for Dashboard and Mobile integrations.
"""
from __future__ import annotations

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
    ai_model: Optional[str] = None
    timeout_seconds: Optional[int] = None


class CommandRequest(BaseModel):
    command: str
    sender_id: Optional[str] = "dashboard-admin"
    idempotency_key: Optional[str] = None


class AskRequest(BaseModel):
    question: str
    sender_id: Optional[str] = "dashboard-user"


@router.get("/overview", summary="Tổng quan AI Assistant & Zalo Quota")
async def get_overview(principal: Principal = Depends(require_user)):
    return zalo_assistant_service.get_overview()


@router.get("/config", summary="Xem cấu hình Zalo Bot và AI Gateway")
async def get_config(principal: Principal = Depends(require_user)):
    return zalo_assistant_service.get_config()


@router.post("/config", summary="Cập nhật cấu hình Zalo Bot và AI Gateway")
async def update_config(payload: ConfigUpdateRequest, principal: Principal = Depends(require_admin)):
    data = payload.model_dump(exclude_unset=True)
    return zalo_assistant_service.update_config(data)


@router.get("/reports", summary="Danh sách báo cáo tuần đã tạo")
async def list_reports(principal: Principal = Depends(require_user)):
    return {"reports": zalo_assistant_service.report_generator.list_reports()}


@router.post("/reports/generate", summary="Khởi tạo báo cáo tuần mới")
async def generate_report(principal: Principal = Depends(require_user)):
    report = zalo_assistant_service.report_generator.generate_weekly_report(
        author=f"Admin ({principal.username})"
    )
    return report


@router.get("/reports/{report_id}", summary="Chi tiết một báo cáo tuần")
async def get_report_detail(report_id: str, principal: Principal = Depends(require_user)):
    report = zalo_assistant_service.report_generator.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo yêu cầu.")
    return report


@router.get("/reports/{report_id}/download", summary="Tải xuống tệp Markdown báo cáo")
async def download_report(report_id: str, principal: Principal = Depends(require_user)):
    report = zalo_assistant_service.report_generator.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo yêu cầu.")
    content = report.get("content", "")
    filename = f"{report_id}.md"
    return Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tools", summary="Quyền hạn và danh mục công cụ bot")
async def get_tools_and_permissions(principal: Principal = Depends(require_user)):
    return zalo_assistant_service.get_tools_and_permissions()


@router.get("/history", summary="Lịch sử tin nhắn, quota outbox và AI requests")
async def get_history(principal: Principal = Depends(require_user)):
    return zalo_assistant_service.get_history()


@router.post("/command", summary="Gửi lệnh tới Zalo Bot Engine")
async def execute_command(req: CommandRequest, principal: Principal = Depends(require_user)):
    sender = req.sender_id or principal.username
    res = zalo_assistant_service.bot_engine.handle_command(
        command_text=req.command,
        sender_id=sender,
        idempotency_key=req.idempotency_key,
    )
    return res


@router.post("/ask", summary="Đặt câu hỏi trực tiếp cho AI Assistant")
async def ask_assistant(req: AskRequest, principal: Principal = Depends(require_user)):
    res = zalo_assistant_service.bot_engine.handle_command(
        command_text=f"/ask {req.question}",
        sender_id=req.sender_id or principal.username,
    )
    return res
