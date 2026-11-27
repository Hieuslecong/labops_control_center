from __future__ import annotations

from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


QualityStatus = Literal[
    "FRESH",
    "STALE",
    "OFFLINE",
    "NOT_CONFIGURED",
    "UNSUPPORTED",
    "NO_DATA",
    "ERROR",
    "UNKNOWN",
    "PARTIAL",
]
WorkloadKind = Literal[
    "TRAINING",
    "INFERENCE",
    "NOTEBOOK",
    "GENERAL_COMPUTE",
    "MONITORING",
    "SYSTEM",
    "OTHER",
    "UNKNOWN",
]
KindConfidence = Literal["VERIFIED", "HEURISTIC", "UNKNOWN"]
CpuBasis = Literal["PER_CORE_PERCENT", "HOST_CAPACITY_PERCENT", "UNKNOWN"]
MemoryMethod = Literal[
    "PROCESS_RSS",
    "TREE_RSS_SUM",
    "TREE_PSS_SUM",
    "WORKSPACE_CGROUP_CURRENT",
    "UNKNOWN",
]
VramAttribution = Literal["PER_DEVICE_VERIFIED", "LEGACY_AGGREGATE", "UNKNOWN"]
WorkloadState = Literal["OBSERVED", "STALE", "MISSING_UNCONFIRMED", "EXIT_CONFIRMED", "UNKNOWN"]
ReconciliationStatus = Literal["CONSISTENT", "MISMATCH", "NOT_COMPARABLE", "UNKNOWN"]
CapabilityStatus = Literal["SUPPORTED", "PARTIAL", "UNSUPPORTED", "NO_DATA", "DEFERRED"]
ActionKind = Literal["SIGTERM", "SIGKILL"]
ActionResultStatus = Literal[
    "TERMINATED",
    "KILLED",
    "TIMEOUT_STILL_RUNNING",
    "FAILED_STILL_RUNNING",
    "PARTIAL_FAILURE",
    "UNKNOWN",
    "FAILED",
    "DENIED",
]
ActionVerification = Literal["VERIFIED", "PARTIAL_FAILURE", "STILL_RUNNING", "FAILED", "UNKNOWN"]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SnapshotScope(StrictSchema):
    node_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    gpu_uuid: Optional[str] = Field(default=None, min_length=1, max_length=256)
    member_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    group_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    mode: Literal["workloads"] = "workloads"


class ScopeBase(StrictSchema):
    node_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    gpu_uuid: Optional[str] = Field(default=None, min_length=1, max_length=256)
    member_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    group_id: Optional[str] = Field(default=None, min_length=1, max_length=256)


class SnapshotQuality(StrictSchema):
    status: QualityStatus
    expected_nodes: Optional[int] = Field(default=None, ge=0)
    fresh_nodes: int = Field(ge=0)
    fresh_workload_nodes: int = Field(ge=0)
    expected_gpus: Optional[int] = Field(default=None, ge=0)
    fresh_gpus: int = Field(ge=0)
    last_success_at: Optional[str] = None


class SnapshotNodeItem(StrictSchema):
    id: str = Field(min_length=1, max_length=256)
    quality: QualityStatus
    workload_quality: QualityStatus
    observed_at: Optional[str] = None
    last_success_at: Optional[str] = None


class SnapshotGpuItem(StrictSchema):
    gpu_uuid: str = Field(min_length=1, max_length=256)
    node_id: str = Field(min_length=1, max_length=256)
    quality: QualityStatus
    observed_at: Optional[str] = None
    vram_used_bytes: Optional[int] = Field(default=None, ge=0)
    vram_capacity_bytes: Optional[int] = Field(default=None, ge=0)
    utilization_percent: Optional[float] = Field(default=None, ge=0, le=100)


class GpuCoveragePair(StrictSchema):
    fresh: int = Field(ge=0)
    expected: Optional[int] = Field(default=None, ge=0)


class SnapshotKpis(StrictSchema):
    observed_workloads: Optional[int] = Field(default=None, ge=0)
    last_known_workloads: Optional[int] = Field(default=None, ge=0)
    gpu_associated_workloads: Optional[int] = Field(default=None, ge=0)
    gpu_association_unknown: Optional[int] = Field(default=None, ge=0)
    observed_members: Optional[int] = Field(default=None, ge=0)
    unmapped_member_workloads: Optional[int] = Field(default=None, ge=0)
    device_vram_used_bytes: Optional[int] = Field(default=None, ge=0)
    device_vram_capacity_bytes: Optional[int] = Field(default=None, ge=0)
    attention_count: Optional[int] = Field(default=None, ge=0)
    gpu_coverage: GpuCoveragePair


class WorkloadComposition(StrictSchema):
    gpu_non_monitoring: int = Field(ge=0)
    non_gpu_compute: int = Field(ge=0)
    monitoring: int = Field(ge=0)
    other_unknown: int = Field(ge=0)


class WorkloadCpuMetric(StrictSchema):
    value_percent: Optional[float] = Field(default=None, ge=0)
    basis: CpuBasis = "UNKNOWN"


class WorkloadMemoryMetric(StrictSchema):
    value_bytes: Optional[int] = Field(default=None, ge=0)
    method: MemoryMethod = "UNKNOWN"
    unique_memory: Optional[bool] = None


class WorkloadVramMetric(StrictSchema):
    value_bytes: Optional[int] = Field(default=None, ge=0)
    attribution: VramAttribution = "UNKNOWN"


class WorkloadGpuAllocation(StrictSchema):
    gpu_uuid: str = Field(min_length=1, max_length=256)
    value_bytes: int = Field(ge=0)
    quality: QualityStatus = "FRESH"


class SnapshotWorkloadItem(StrictSchema):
    id: str = Field(min_length=1, max_length=256)
    ref: Optional[str] = Field(default=None, min_length=1, max_length=256)
    node_id: str = Field(min_length=1, max_length=256)
    boot_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    host_pid: int = Field(ge=1)
    start_ticks: Optional[int] = Field(default=None, ge=0)
    container_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    uid: Optional[int] = Field(default=None, ge=0)
    member_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    username: Optional[str] = Field(default=None, min_length=1, max_length=256)
    group_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    workspace: Optional[str] = Field(default=None, min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)
    workload_kind: WorkloadKind = "UNKNOWN"
    kind_confidence: KindConfidence = "UNKNOWN"
    kind_evidence: List[str] = Field(default_factory=list, max_length=12)
    gpu_associated: Optional[bool] = None
    cpu: WorkloadCpuMetric
    memory: WorkloadMemoryMetric
    vram: WorkloadVramMetric
    gpu_allocations: Optional[List[WorkloadGpuAllocation]] = Field(default=None, max_length=64)
    elapsed_seconds: Optional[int] = Field(default=None, ge=0)
    started_at: Optional[str] = None
    state: WorkloadState = "OBSERVED"
    quality: QualityStatus = "FRESH"
    observed_at: Optional[str] = None
    is_protected: bool = False
    actions_allowed: bool = False
    action_block_reason: Optional[str] = Field(default=None, max_length=2048)


class DeviceVramMeta(StrictSchema):
    scope: Literal["DEVICE_WIDE"] = "DEVICE_WIDE"
    member_filter_applied: Literal[False] = False
    reconciliation: ReconciliationStatus = "UNKNOWN"


class SnapshotWarning(StrictSchema):
    code: str = Field(min_length=1, max_length=256)
    message: str = Field(max_length=2048)


class WorkloadSnapshotEnvelope(StrictSchema):
    schema_version: Literal[1] = 1
    snapshot_id: str = Field(min_length=1, max_length=256)
    observed_at: Optional[str] = None
    received_at: str
    scope: SnapshotScope
    quality: SnapshotQuality
    nodes: List[SnapshotNodeItem] = Field(default_factory=list, max_length=100)
    gpus: List[SnapshotGpuItem] = Field(default_factory=list, max_length=256)
    kpis: SnapshotKpis
    composition: Optional[WorkloadComposition] = None
    workloads: List[SnapshotWorkloadItem] = Field(default_factory=list, max_length=2000)
    device_vram: DeviceVramMeta
    warnings: List[SnapshotWarning] = Field(default_factory=list, max_length=100)


class AnalyticsPoint(StrictSchema):
    ts: str
    value: Optional[float] = None
    quality: QualityStatus = "FRESH"


class AnalyticsSeries(StrictSchema):
    metric: str = Field(min_length=1, max_length=256)
    entity_id: str = Field(min_length=1, max_length=256)
    unit: str = Field(min_length=1, max_length=256)
    aggregation: str = Field(min_length=1, max_length=256)
    coverage_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    points: List[AnalyticsPoint] = Field(default_factory=list, max_length=600)


class CapabilityEntry(StrictSchema):
    status: CapabilityStatus
    reason: str = Field(max_length=2048)


class WorkloadAnalyticsEnvelope(StrictSchema):
    schema_version: Literal[1] = 1
    scope: ScopeBase
    from_ts: str = Field(alias="from")
    to_ts: str = Field(alias="to")
    bucket_seconds: int = Field(ge=1)
    coverage_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    series: List[AnalyticsSeries] = Field(default_factory=list, max_length=20)
    capabilities: Dict[str, CapabilityEntry] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class HistoryWorkloadItem(StrictSchema):
    id: str = Field(min_length=1, max_length=256)
    node_id: str = Field(min_length=1, max_length=256)
    member_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    group_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    observed_seconds: Optional[int] = Field(default=None, ge=0)
    state: WorkloadState = "MISSING_UNCONFIRMED"
    exit_code: Optional[int] = None
    exit_reason: Optional[str] = Field(default=None, max_length=2048)
    evidence: str = Field(max_length=2048)
    actionable: Literal[False] = False


class WorkloadHistoryEnvelope(StrictSchema):
    schema_version: Literal[1] = 1
    scope: ScopeBase
    from_ts: str = Field(alias="from")
    to_ts: str = Field(alias="to")
    items: List[HistoryWorkloadItem] = Field(default_factory=list, max_length=100)
    next_cursor: Optional[str] = Field(default=None, min_length=1, max_length=256)
    capability: CapabilityStatus = "NO_DATA"

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class WorkloadEventItem(StrictSchema):
    id: str = Field(min_length=1, max_length=256)
    occurred_at: str
    event_type: str = Field(min_length=1, max_length=256)
    resource_id: str = Field(min_length=1, max_length=256)
    actor_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    result: str = Field(min_length=1, max_length=256)
    message: str = Field(max_length=2048)
    evidence: str = Field(max_length=2048)
    correlation_id: Optional[str] = Field(default=None, min_length=1, max_length=256)


class WorkloadEventsEnvelope(StrictSchema):
    schema_version: Literal[1] = 1
    scope: ScopeBase
    from_ts: str = Field(alias="from")
    to_ts: str = Field(alias="to")
    items: List[WorkloadEventItem] = Field(default_factory=list, max_length=100)
    next_cursor: Optional[str] = Field(default=None, min_length=1, max_length=256)
    capability: CapabilityStatus = "NO_DATA"

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ProcessActionResultEnvelope(StrictSchema):
    schema_version: Literal[1] = 1
    action_id: str = Field(min_length=1, max_length=256)
    action: ActionKind
    status: ActionResultStatus
    verification: ActionVerification
    pids_remaining: List[int] = Field(default_factory=list)
    skipped_descendants: List[str] = Field(default_factory=list)
    message: str = Field(max_length=2048)
    correlation_id: str = Field(min_length=1, max_length=256)
    executed_at: Optional[str] = None


class V31ProcessActionRequest(BaseModel):
    action: ActionKind
    reason: str = Field(min_length=3, max_length=500)
    operation_key: str = Field(min_length=8, max_length=160)
    terminate_tree: bool = True
    timeout_seconds: int = Field(default=10, ge=1, le=30)
    expected_boot_id: Optional[str] = Field(default=None, max_length=256)
    expected_start_ticks: Optional[int] = Field(default=None, ge=0)
    expected_uid: Optional[int] = Field(default=None, ge=0)
    expected_container_id: Optional[str] = Field(default=None, max_length=256)
    force_confirmed: bool = False
    typed_confirmation: Optional[str] = Field(default=None, max_length=256)


class WorkloadDetailResponse(BaseModel):
    schema_version: Literal[1] = 1
    workload: SnapshotWorkloadItem
    node_name: str
    member_full_name: Optional[str] = None
    gpu_labels: Dict[str, str] = Field(default_factory=dict)
    cmdline_sanitized: Optional[str] = None
    children_count: int = 0
    pss_bytes: Optional[int] = None
    pss_status: Literal["SUPPORTED", "UNSUPPORTED", "PERMISSION_DENIED"] = "UNSUPPORTED"
    group_attribution_mode: Literal["OBSERVED_AT", "CURRENT_MEMBER_PROFILE", "UNVERIFIED"] = "UNVERIFIED"
    metric_definitions: Dict[str, str] = Field(default_factory=dict)
    actions_enabled_by_flag: bool = False
