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
    """Submit a GitHub repo to the isolated security sandbox for AST static analysis"""
    if not service.demo_mode:
        raise HTTPException(status_code=503, detail="Security scanner is not configured")
    repo_url = payload.get("repo_url") or payload.get("repoUrl")
    if not repo_url:
        raise HTTPException(status_code=400, detail="Thiếu URL kho lưu trữ GitHub")

    repo_name = repo_url.split("/")[-1].replace(".git", "")
    new_id = f"sec-{uuid.uuid4().hex[:6]}"
    new_review = {
        "id": new_id,
        "repo_url": repo_url,
        "repo_name": repo_name,
        "commit_sha": uuid.uuid4().hex[:12],
        "submitted_by": payload.get("submitted_by", "hieulc"),
        "submitted_at": "Vừa xong",
        "status": "APPROVED",
        "risk_score": 5,
        "findings": {
            "high": 0,
            "medium": 0,
            "low": 1,
            "crypto_mining": False,
            "suspicious_networking": False,
            "privilege_escalation": False,
            "verified_clean": True
        },
        "summary": "Kho lưu trữ đã vượt qua toàn bộ 8 bước kiểm duyệt sandbox an toàn.",
        "reviewed_by": "Admin (Hệ thống)"
    }
    service.security_reviews.insert(0, new_review)

    # Audit log
    service.audit_logs.insert(0, {
        "id": f"aud-{uuid.uuid4().hex[:6]}",
        "actor": "admin@labops.local",
        "action": "SECURITY_SCAN_PASSED",
        "target_type": "REPOSITORY",
        "target_id": repo_name,
        "timestamp": "Vừa xong",
        "result": "SUCCESS",
        "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
        "details": f"Quét an ninh thành công kho mã {repo_url} (Điểm rủi ro: 5/100)"
    })

    return new_review

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
