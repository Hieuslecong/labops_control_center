from fastapi import APIRouter, Depends

from app.api.dependencies import require_labops_admin
from app.services.labops_service import service

router = APIRouter(dependencies=[Depends(require_labops_admin)])


@router.get("/health")
async def access_health():
    return await service.get_access_health()
