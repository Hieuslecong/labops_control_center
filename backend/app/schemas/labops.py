from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any

class BaseSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

class ServerSummary(BaseSchema):
    total: int
    online: int
    offline: int

class GpuSummary(BaseSchema):
    total_count: int = Field(alias="totalCount")
    model: str
    aggregate_vram_gb: int = Field(alias="aggregateVramGb")
    active_count: int = Field(alias="activeCount")
    used_vram_gb: int = Field(alias="usedVramGb")

class MemberSummary(BaseSchema):
    online_ssh: int = Field(alias="onlineSsh")
    active_sessions: int = Field(alias="activeSessions")
    total_registered: int = Field(alias="totalRegistered")

class JobSummary(BaseSchema):
    running: int
    pending: int
    failed: int

class GpuHoursSummary(BaseSchema):
    today: float
    last_7_days: float = Field(alias="last7Days")
    previous_period_change_percent: float = Field(alias="previousPeriodChangePercent")

class AlertSummary(BaseSchema):
    critical: int
    warnings: int
    unresolved_incidents: int = Field(alias="unresolvedIncidents")

class DashboardSummaryResponse(BaseSchema):
    servers: ServerSummary
    gpus: GpuSummary
    members: MemberSummary
    jobs: JobSummary
    gpu_hours: GpuHoursSummary = Field(alias="gpuHours")
    alerts: AlertSummary

class PhysicalServerResponse(BaseSchema):
    id: str
    name: str
    hostname: str
    location: str
    status: str
    ip_address: str = Field(alias="ipAddress")
    vpn_ip: str = Field(alias="vpnIp")
    uptime: str
    cpu_model: str = Field(alias="cpuModel")
    cpu_cores: int = Field(alias="cpuCores")
    cpu_usage_percent: float = Field(alias="cpuUsagePercent")
    ram_total_gb: float = Field(alias="ramTotalGb")
    ram_used_gb: float = Field(alias="ramUsedGb")
    ram_usage_percent: float = Field(alias="ramUsagePercent")
    disk_total_tb: float = Field(alias="diskTotalTb")
    disk_used_tb: float = Field(alias="diskUsedTb")
    disk_usage_percent: float = Field(alias="diskUsagePercent")
    disk_type: str = Field(alias="diskType")
    network_in_mbps: int = Field(alias="networkInMbps")
    network_out_mbps: int = Field(alias="networkOutMbps")
    gpu_count: int = Field(alias="gpuCount")
    active_members: int = Field(alias="activeMembers")
    active_jobs: int = Field(alias="activeJobs")
    incus_version: str = Field(alias="incusVersion")
    driver_version: str = Field(alias="driverVersion")
    cuda_version: str = Field(alias="cudaVersion")
    last_updated: str = Field(alias="lastUpdated")

class GpuProcessResponse(BaseSchema):
    pid: int
    name: str
    username: str
    workspace: str
    vram_mb: int = Field(alias="vramMb")
    cpu_percent: float = Field(alias="cpuPercent")

class PhysicalGpuResponse(BaseSchema):
    id: str
    display_label: str = Field(alias="displayLabel")
    server_id: str = Field(alias="serverId")
    server_name: str = Field(alias="serverName")
    model: str
    pci_bus_id: str = Field(alias="pciBusId")
    uuid: str
    vram_total_mb: int = Field(alias="vramTotalMb")
    vram_used_mb: int = Field(alias="vramUsedMb")
    gpu_util_percent: int = Field(alias="gpuUtilPercent")
    temp_celsius: int = Field(alias="tempCelsius")
    power_watts: int = Field(alias="powerWatts")
    power_limit_watts: int = Field(alias="powerLimitWatts")
    allocation_mode: str = Field(alias="allocationMode")
    enforcement_state: str = Field(alias="enforcementState")
    active_processes: List[GpuProcessResponse] = Field(alias="activeProcesses")
    active_users: List[str] = Field(alias="activeUsers")
    associated_jobs: List[str] = Field(alias="associatedJobs")

class GpuAllocateRequest(BaseSchema):
    gpu_id: str = Field(alias="gpuId")
    username: str
    mode: str = "SHARED"
    requested_vram_gb: int = Field(alias="requestedVramGb")
    job_name: str = Field(alias="jobName")

class MemberCreateRequest(BaseSchema):
    full_name: str = Field(alias="fullName")
    username: str
    email: str
    role: str
    assigned_server: str = Field(alias="assignedServer")
    workspace_name: str = Field(alias="workspaceName")
    cpu_limit_cores: int = Field(8, alias="cpuLimitCores")
    ram_limit_gb: int = Field(32, alias="ramLimitGb")
    storage_quota_gb: int = Field(200, alias="storageQuotaGb")
    vram_policy: str = Field("Quota 12GB (Shared)", alias="vramPolicy")
    ssh_public_key: Optional[str] = Field(None, alias="sshPublicKey")

class MemberResponse(BaseSchema):
    id: str
    full_name: str = Field(alias="fullName")
    username: str
    email: str
    role: str
    assigned_server: str = Field(alias="assignedServer")
    workspace_name: str = Field(alias="workspaceName")
    status: str
    ssh_status: str = Field(alias="sshStatus")
    ssh_sessions_count: int = Field(alias="sshSessionsCount")
    ssh_public_key_preview: str = Field(alias="sshPublicKeyPreview")
    cpu_limit_cores: int = Field(alias="cpuLimitCores")
    ram_limit_gb: int = Field(alias="ramLimitGb")
    storage_quota_gb: int = Field(alias="storageQuotaGb")
    gpu_permissions: List[str] = Field(alias="gpuPermissions")
    vram_policy: str = Field(alias="vramPolicy")
    max_runtime_hours: int = Field(alias="maxRuntimeHours")
    active_jobs_count: int = Field(alias="activeJobsCount")
    total_gpu_hours: float = Field(alias="totalGpuHours")
    created_at: str = Field(alias="createdAt")

class JobResponse(BaseSchema):
    id: str
    username: str
    full_name: str = Field(alias="fullName")
    server_id: str = Field(alias="serverId")
    workspace: str
    pid: int
    script: str
    gpu_id: str = Field(alias="gpuId")
    cpu_percent: float = Field(alias="cpuPercent")
    ram_used_mb: int = Field(alias="ramUsedMb")
    vram_used_mb: int = Field(alias="vramUsedMb")
    start_time: str = Field(alias="startTime")
    elapsed_time: str = Field(alias="elapsedTime")
    status: str
    ssh_status: str = Field(alias="sshStatus")
    container_state: str = Field(alias="containerState")

class SecurityReviewResponse(BaseSchema):
    id: str
    repo_url: str = Field(alias="repoUrl")
    repo_name: str = Field(alias="repoName")
    commit_sha: str = Field(alias="commitSha")
    submitted_by: str = Field(alias="submittedBy")
    submitted_at: str = Field(alias="submittedAt")
    status: str
    risk_score: int = Field(alias="riskScore")
    findings: dict
    summary: str
    reviewed_by: Optional[str] = Field(None, alias="reviewedBy")

class EventResponse(BaseSchema):
    id: str
    title: str
    type: str
    date: str
    start_time: str = Field(alias="startTime")
    end_time: str = Field(alias="endTime")
    location: str
    organizer: str
    related_server: Optional[str] = Field(None, alias="relatedServer")
    status: str
    description: str

class AuditLogResponse(BaseSchema):
    id: str
    actor: str
    action: str
    target_type: str = Field(alias="targetType")
    target_id: str = Field(alias="targetId")
    timestamp: str
    result: str
    correlation_id: str = Field(alias="correlationId")
    details: str
