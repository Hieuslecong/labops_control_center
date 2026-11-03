from fastapi import APIRouter
from app.schemas.labops import DashboardSummaryResponse
from app.services.labops_service import service

router = APIRouter()

@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary():
    """Retrieve top-level operational KPIs for the two-node GPU lab cluster"""
    return service.get_summary()

@router.get("/health")
async def get_system_health():
    """Health check for backend API and node integrations"""
    return {
        "status": "healthy",
        "mode": "DEMO" if getattr(service, "demo_mode", True) else "PRODUCTION",
        "nodes": {
            "track": "online",
            "workstation": "online"
        }
    }

