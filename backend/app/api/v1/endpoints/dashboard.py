from fastapi import APIRouter, Depends
from app.api.dependencies import require_admin
from app.core.security import Principal
from app.schemas.labops import DashboardSummaryResponse
from app.services.labops_service import service

router = APIRouter()

@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(p: Principal = Depends(require_admin)):
    """Retrieve top-level operational KPIs for the two-node GPU lab cluster"""
    return service.get_summary()

@router.get("/health")
async def get_system_health(p: Principal = Depends(require_admin)):
    """Health check for backend API and node integrations"""
    return service.get_monitoring_health()

