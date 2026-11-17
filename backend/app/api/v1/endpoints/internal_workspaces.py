"""Node Agent endpoints for workspace runtime attestation and registry lookup."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.api.dependencies import require_agent
from app.core.security import Principal
from app.services.labops_service import service
from app.services.workspace_controller import WorkspaceSecurityError

router = APIRouter()


class RuntimeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    container_name: str = Field(min_length=1, max_length=128)
    labels: dict[str, str]
    state: str = Field(pattern=r"^running$")
    network_mode: str = Field(default="UNKNOWN", min_length=1, max_length=128)
    privileged: StrictBool
    docker_socket_mounted: StrictBool
    cpu_cores: StrictInt = Field(gt=0)
    ram_bytes: StrictInt = Field(gt=0)
    pids_limit: StrictInt = Field(gt=0)


@router.post("/{workspace_id}/runtime-observation")
async def record_workspace_runtime(
    workspace_id: str,
    payload: RuntimeObservation,
    actor: Principal = Depends(require_agent),
):
    try:
        return service.workspace_controller.record_runtime_observation(
            workspace_id, str(actor.node_id), payload.model_dump()
        )
    except WorkspaceSecurityError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.get("/by-container/{container_id}")
async def get_workspace_by_container(
    container_id: str,
    actor: Principal = Depends(require_agent),
):
    record = service.workspace_controller.lookup_container_record(container_id, str(actor.node_id))
    if record is None:
        raise HTTPException(status_code=404, detail="Registered workspace container not found")
    return record
