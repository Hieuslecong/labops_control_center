import uuid
from fastapi import APIRouter, Depends, HTTPException
from app.api.dependencies import require_admin, require_user
from app.core.security import Principal
from typing import List, Dict, Any
from app.schemas.labops import SecurityReviewResponse
from app.services.labops_service import service

router = APIRouter()

@router.get("/reviews", response_model=List[SecurityReviewResponse])
async def list_security_reviews(p: Principal = Depends(require_admin)):
    """List GitHub repository security reviews and sandbox verification reports"""
    return service.security_reviews

@router.post("/reviews", response_model=SecurityReviewResponse)
async def submit_security_review(payload: Dict[str, str], p: Principal = Depends(require_admin)):
    """Legacy endpoint: Không tạo kết quả APPROVED giả lập. Chuyển hướng sang V2 Scanner."""
    raise HTTPException(
        status_code=400,
        detail="API legacy /reviews đã bị vô hiệu hóa vì không tạo kết quả kiểm duyệt thực tế. Vui lòng sử dụng /api/v1/security/scans hoặc Repository Guard."
    )

@router.post("/reviews/{review_id}/decision")
async def update_security_review_decision(review_id: str, payload: Dict[str, str], p: Principal = Depends(require_admin)):
    if not service.demo_mode:
        raise HTTPException(status_code=503, detail="Security review decisions are unavailable without a configured scanner")
    """Approve or reject a repository submitted for review"""
    decision = payload.get("decision")
    if decision not in ["APPROVED", "REJECTED"]:
        raise HTTPException(status_code=400, detail="Invalid decision, must be APPROVED or REJECTED")

    review = next((r for r in service.security_reviews if r["id"] == review_id), None)
    if not review:
        raise HTTPException(status_code=404, detail="Review record not found")

    review["status"] = decision
    review["reviewed_by"] = payload.get("reviewer", "admin@labops.local")

    service.audit_logs.insert(0, {
        "id": f"aud-{uuid.uuid4().hex[:6]}",
        "actor": "admin@labops.local",
        "action": f"SECURITY_REVIEW_{decision}",
        "target_type": "REPOSITORY",
        "target_id": review["repo_name"],
        "timestamp": "Vừa xong",
        "result": "SUCCESS",
        "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
        "details": f"Admin đã duyệt quyết định {decision} cho kho {review['repo_name']}"
    })

    return {"success": True, "id": review_id, "status": decision}



# ---------------- V2: Trivy scans ----------------
@router.get("/scans")
async def list_scans(p: Principal = Depends(require_user)):
    """Admin thấy mọi bản quét; nghiên cứu viên chỉ thấy bản quét của mình."""
    return service.security_scanner.list_scans(None if p.is_admin else p.username)


@router.post("/scans")
async def submit_scan(payload: Dict[str, str], p: Principal = Depends(require_user)):
    try:
        return service.security_scanner.submit(payload.get("repo_url", ""), payload.get("commit_sha", ""), p.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/scans/{scan_id}/decision")
async def decide_scan(scan_id: str, payload: Dict[str, str], p: Principal = Depends(require_admin)):
    try:
        return service.security_scanner.decide(scan_id, payload.get("decision", ""), p.username)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
