import uuid
from fastapi import APIRouter, Depends
from app.api.dependencies import require_admin
from app.core.security import Principal
from typing import List, Dict, Any
from app.schemas.labops import AuditLogResponse
from app.services.labops_service import service

router = APIRouter()

@router.get("", response_model=List[AuditLogResponse])
async def list_audit_logs(p: Principal = Depends(require_admin)):
    """List immutable administrative audit trails with correlation IDs"""
    return service.audit_logs

@router.post("", response_model=AuditLogResponse)
async def create_audit_log(payload: Dict[str, Any], p: Principal = Depends(require_admin)):
    """Record an audit trail event for administrative action"""
    new_id = f"aud-{uuid.uuid4().hex[:6]}"
    new_log = {
        "id": new_id,
        "actor": p.username,
        "action": payload.get("action", "ADMIN_ACTION"),
        "target_type": payload.get("target_type", "SYSTEM"),
        "target_id": payload.get("target_id", "cluster"),
        "timestamp": "Vừa xong",
        "result": payload.get("result", "SUCCESS"),
        "correlation_id": payload.get("correlation_id", f"corr-{uuid.uuid4().hex[:12]}"),
        "details": payload.get("details", "Thực hiện thao tác quản trị")
    }
    service.audit_logs.insert(0, new_log)
    return new_log

