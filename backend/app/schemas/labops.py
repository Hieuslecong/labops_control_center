from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any, Literal

class BaseSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

class ServerSummary(BaseSchema):
    total: int
    online: int
    offline: int
    unknown: Optional[int] = None

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
    today: Optional[float] = None
    last_7_days: Optional[float] = Field(default=None, alias="last7Days")
    previous_period_change_percent: Optional[float] = Field(default=None, alias="previousPeriodChangePercent")

class AlertSummary(BaseSchema):
    critical: Optional[int] = None
    warnings: Optional[int] = None
    unresolved_incidents: Optional[int] = Field(default=None, alias="unresolvedIncidents")

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
    mode: str = "FULL_GPU_EXCLUSIVE"
    requested_vram_gb: int = Field(alias="requestedVramGb")
    job_name: str = Field(alias="jobName")

class PrivilegeValidationResponse(BaseSchema):
    unprivileged_container: bool = Field(False, alias="unprivilegedContainer")
    sudo_test_failed_as_expected: bool = Field(False, alias="sudoTestFailedAsExpected")
    no_sudo_group_membership: bool = Field(False, alias="noSudoGroupMembership")
    no_docker_socket_access: bool = Field(False, alias="noDockerSocketAccess")
    no_incus_socket_access: bool = Field(False, alias="noIncusSocketAccess")
    verification_timestamp: Optional[str] = Field(None, alias="verificationTimestamp")
    status: str = "NOT_VERIFIED"

class StoragePoolResponse(BaseSchema):
    id: str
    server_id: str = Field(alias="serverId")
    server_name: str = Field(alias="serverName")
    name: str
    label: str
    disk_identifier: str = Field(alias="diskIdentifier")
    mount_point: str = Field(alias="mountPoint")
    driver: str
    capacity_gb: float = Field(alias="capacityGb")
    used_gb: float = Field(alias="usedGb")
    free_gb: float = Field(alias="freeGb")
    health_status: str = Field(alias="healthStatus")
    is_default: bool = Field(alias="isDefault")

class StorageAllocationResponse(BaseSchema):
    id: str
    member_id: str = Field(alias="memberId")
    pool_id: str = Field(alias="poolId")
    volume_name: str = Field(alias="volumeName")
    mount_path: str = Field(alias="mountPath")
    quota_gb: float = Field(alias="quotaGb")
    used_gb: float = Field(alias="usedGb")
    read_only: bool = Field(alias="readOnly")
    backup_policy: str = Field(alias="backupPolicy")
    shared_datasets: List[str] = Field(alias="sharedDatasets")
    created_at: str = Field(alias="createdAt")

class StorageQuotaUpdateRequest(BaseSchema):
    quota_gb: int = Field(alias="quotaGb")

class SshEndpointResponse(BaseSchema):
    id: str
    member_id: str = Field(alias="memberId")
    server_id: str = Field(alias="serverId")
    listen_address: str = Field(alias="listenAddress")
    listen_port: int = Field(alias="listenPort")
    container_port: int = Field(22, alias="containerPort")
    protocol: str = "tcp"
    status: str
    last_used_at: Optional[str] = Field(None, alias="lastUsedAt")

class SshPortUpdateRequest(BaseSchema):
    listen_port: int = Field(alias="listenPort")

class SshCredentialResponse(BaseSchema):
    id: str
    member_id: str = Field(alias="memberId")
    public_key: str = Field(alias="publicKey")
    fingerprint: str
    key_type: str = Field(alias="keyType")
    enrollment_type: str = Field(alias="enrollmentType")
    status: str
    enrolled_at: Optional[str] = Field(None, alias="enrolledAt")
    expires_at: Optional[str] = Field(None, alias="expiresAt")

class SshKeyEnrollRequest(BaseSchema):
    public_key: str = Field(alias="publicKey")
    enrollment_type: str = Field("IMPORTED", alias="enrollmentType")

class SshAccountPolicyResponse(BaseSchema):
    workspace_id: str = Field(alias="workspaceId")
    linux_username: str = Field(alias="linuxUsername")
    authentication_mode: str = Field("PUBLIC_KEY_AND_PASSWORD", alias="authenticationMode")
    password_status: str = Field("ACTIVE", alias="passwordStatus") # ACTIVE, CHANGE_REQUIRED, LOCKED, EXPIRED
    key_enrollment_status: str = Field("ENROLLED", alias="keyEnrollmentStatus") # ENROLLED, PENDING, REVOKED
    access_status: str = Field("ACTIVE", alias="accessStatus") # ACTIVE, SUSPENDED, LOCKED
    min_password_length: int = Field(12, alias="minPasswordLength")
    failed_login_attempts: int = Field(0, alias="failedLoginAttempts")
    last_successful_login: Optional[str] = Field(None, alias="lastSuccessfulLogin")
    last_login_source_ip: Optional[str] = Field(None, alias="lastLoginSourceIp")
    updated_at: str = Field(alias="updatedAt")

class SshLoginEventResponse(BaseSchema):
    id: str
    member_id: str = Field(alias="memberId")
    workspace_id: str = Field(alias="workspaceId")
    linux_username: str = Field(alias="linuxUsername")
    source_ip: str = Field(alias="sourceIp")
    listen_port: int = Field(alias="listenPort")
    auth_stage: str = Field(alias="authStage") # STAGE_1_PUBLICKEY, STAGE_2_PASSWORD, COMPLETED
    authentication_result: str = Field(alias="authenticationResult") # SUCCESS, PUBLICKEY_FAILED, PASSWORD_FAILED, BOTH_FAILED
    failure_reason: Optional[str] = Field(None, alias="failureReason")
    timestamp: str

class SshPasswordResetRequest(BaseSchema):
    new_password: Optional[str] = Field(None, alias="newPassword")
    require_change_on_next_login: bool = Field(True, alias="requireChangeOnNextLogin")

class SshPasswordResetResponse(BaseSchema):
    success: bool
    member_id: str = Field(alias="memberId")
    password_status: str = Field(alias="passwordStatus")
    temporary_password: Optional[str] = Field(None, alias="temporaryPassword")
    message: str

class SshDualAuthVerificationResponse(BaseSchema):
    sshd_syntax_valid: bool = Field(alias="sshdSyntaxValid")
    authentication_methods: str = Field("publickey,password", alias="authenticationMethods")
    pubkey_authentication: bool = Field(True, alias="pubkeyAuthentication")
    password_authentication: bool = Field(True, alias="passwordAuthentication")
    permit_root_login: bool = Field(False, alias="permitRootLogin")
    permit_empty_passwords: bool = Field(False, alias="permitEmptyPasswords")
    use_pam: bool = Field(True, alias="usePam")
    test_matrix: Dict[str, str] = Field(alias="testMatrix")
    status: str = Field("DUAL_AUTH_ENFORCED_VERIFIED", alias="status")
    effective_config_summary: str = Field(alias="effectiveConfigSummary")

class WireGuardDeviceSchema(BaseSchema):
    device_id: str = Field(alias="deviceId")
    name: str
    public_key: Optional[str] = Field("", alias="publicKey")
    client_ip: Optional[str] = Field("", alias="clientIp")
    server_id: Optional[str] = Field("track", alias="serverId")
    server_public_key: Optional[str] = Field("", alias="serverPublicKey")
    server_endpoint: Optional[str] = Field("118.70.169.236:2204", alias="serverEndpoint")
    allowed_subnets: Optional[str] = Field("10.88.40.0/24", alias="allowedSubnets")
    status: str = "ACTIVE"
    enabled: Optional[bool] = Field(True, alias="enabled")
    created_at: Optional[Any] = Field(None, alias="createdAt")
    last_handshake: Optional[Any] = Field(None, alias="lastHandshake")
    transfer_rx: Optional[int] = Field(0, alias="transferRx")
    transfer_tx: Optional[int] = Field(0, alias="transferTx")


class WireGuardProfileSchema(BaseSchema):
    vpn_ip: Optional[str] = Field(None, alias="vpnIp")
    client_ip: Optional[str] = Field(None, alias="clientIp")
    public_key: Optional[str] = Field(None, alias="publicKey")
    server_public_key: Optional[str] = Field(None, alias="serverPublicKey")
    server_endpoint: Optional[str] = Field(None, alias="serverEndpoint")
    allowed_subnets: Optional[str] = Field(None, alias="allowedSubnets")
    status: str = "NOT_CONFIGURED"
    last_handshake: Optional[str] = Field(None, alias="lastHandshake")
    transfer_rx_bytes: Optional[int] = Field(0, alias="transferRxBytes")
    transfer_tx_bytes: Optional[int] = Field(0, alias="transferTxBytes")
    devices: List[WireGuardDeviceSchema] = Field(default_factory=list)


class WireGuardDeviceCreateRequest(BaseSchema):
    name: str
    public_key: str = Field(alias="publicKey")

class WireGuardRegenerateRequest(BaseSchema):
    public_key: Optional[str] = Field(None, alias="publicKey")

class MemberCreateRequest(BaseSchema):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True, extra="forbid")

    full_name: str = Field(alias="fullName", min_length=1, max_length=120)
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-z_][a-z0-9_-]{2,31}$")
    email: str = Field(min_length=3, max_length=254)
    role: Literal["RESEARCHER", "ADMIN"] = "RESEARCHER"
    research_group: Optional[str] = Field("AI Research Lab", alias="researchGroup", max_length=120)
    assigned_server: Literal["track", "workstation"] = Field("track", alias="assignedServer")
    workspace_name: str = Field(alias="workspaceName", min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    storage_pool_id: Optional[str] = Field(None, alias="storagePoolId")
    mount_path: str = Field("/workspace", alias="mountPath", max_length=128)
    storage_quota_gb: int = Field(250, alias="storageQuotaGb", ge=1, le=1000000)
    backup_policy: Literal["DAILY_SNAPSHOT", "WEEKLY_SYNC", "NONE"] = Field("DAILY_SNAPSHOT", alias="backupPolicy")
    shared_datasets: List[str] = Field(default_factory=lambda: ["/shared/datasets"], alias="sharedDatasets", max_length=32)
    cpu_limit_cores: int = Field(8, alias="cpuLimitCores", ge=1, le=512)
    ram_limit_gb: int = Field(32, alias="ramLimitGb", ge=1, le=4096)
    gpu_permissions: List[str] = Field(default_factory=list, alias="gpuPermissions", max_length=16)
    vram_policy: str = Field("Quota 12GB (Shared)", alias="vramPolicy", max_length=64)
    vram_quota_gb: Optional[int] = Field(12, alias="vramQuotaGb", ge=0, le=256)
    gpu_allocation_mode: Literal["SHARED", "EXCLUSIVE", "MIG"] = Field("SHARED", alias="gpuAllocationMode")
    expires_at: Optional[str] = Field(None, alias="expiresAt")
    ssh_public_key: Optional[str] = Field(None, alias="sshPublicKey")
    ssh_key_label: Optional[str] = Field(None, alias="sshKeyLabel")
    provision_ssh_access: Optional[bool] = Field(None, alias="provisionSshAccess")
    require_passphrase: Optional[bool] = Field(None, alias="requirePassphrase")
    enforce_canary_runtime: Optional[bool] = Field(None, alias="enforceCanaryRuntime")

class MemberResponse(BaseSchema):
    id: str
    full_name: str = Field(alias="fullName")
    username: str
    email: str
    role: str
    research_group: Optional[str] = Field(None, alias="researchGroup")
    assigned_server: str = Field(alias="assignedServer")
    workspace_name: str = Field(alias="workspaceName")
    status: str
    provisioning_state: str = Field("PENDING", alias="provisioningState")
    workspace_status: str = Field("NOT_VERIFIED", alias="workspaceStatus")
    teleport_status: str = Field("NOT_CONFIGURED", alias="teleportStatus")
    storage_pool_id: Optional[str] = Field(None, alias="storagePoolId")
    storage_pool_label: Optional[str] = Field(None, alias="storagePoolLabel")
    mount_path: str = Field("/workspace", alias="mountPath")
    storage_quota_gb: int = Field(alias="storageQuotaGb")
    storage_used_gb: int = Field(0, alias="storageUsedGb")
    backup_policy: str = Field("DAILY_SNAPSHOT", alias="backupPolicy")
    shared_datasets: List[str] = Field([], alias="sharedDatasets")
    ssh_status: str = Field(alias="sshStatus")
    ssh_sessions_count: int = Field(alias="sshSessionsCount")
    ssh_ip: Optional[str] = Field(None, alias="sshIp")
    ssh_port: Optional[int] = Field(None, alias="sshPort")
    ssh_key_status: str = Field("ACTIVE", alias="sshKeyStatus")
    ssh_fingerprint: Optional[str] = Field(None, alias="sshFingerprint")
    ssh_public_key_preview: Optional[str] = Field(None, alias="sshPublicKeyPreview")
    ssh_public_key: Optional[str] = Field(None, alias="sshPublicKey")
    ssh_key_label: Optional[str] = Field(None, alias="sshKeyLabel")
    ssh_auth_mode: str = Field("PUBLIC_KEY_AND_PASSWORD", alias="sshAuthMode")
    password_status: str = Field("ACTIVE", alias="passwordStatus")
    failed_login_attempts: int = Field(0, alias="failedLoginAttempts")
    last_successful_login: Optional[str] = Field(None, alias="lastSuccessfulLogin")
    last_login_source_ip: Optional[str] = Field(None, alias="lastLoginSourceIp")
    sudo_privilege: str = Field("DENIED", alias="sudoPrivilege")
    security_isolation_level: str = Field("UNPRIVILEGED_CGROUPS_ISOLATED", alias="securityIsolationLevel")
    privilege_validation: Optional[PrivilegeValidationResponse] = Field(None, alias="privilegeValidation")
    cpu_limit_cores: int = Field(alias="cpuLimitCores")
    ram_limit_gb: int = Field(alias="ramLimitGb")
    gpu_permissions: List[str] = Field(alias="gpuPermissions")
    vram_policy: str = Field(alias="vramPolicy")
    vram_quota_gb: Optional[int] = Field(None, alias="vramQuotaGb")
    gpu_allocation_mode: str = Field("SHARED", alias="gpuAllocationMode")
    max_runtime_hours: int = Field(alias="maxRuntimeHours")
    active_jobs_count: int = Field(alias="activeJobsCount")
    total_gpu_hours: float = Field(alias="totalGpuHours")
    created_at: str = Field(alias="createdAt")
    expires_at: Optional[str] = Field(None, alias="expiresAt")
    wireguard: Optional[WireGuardProfileSchema] = None
    workspace_dir: Optional[str] = Field(None, alias="workspaceDir")
    temp_password_expires_at: Optional[str] = Field(None, alias="tempPasswordExpiresAt")
    runtime_verification: Optional[Dict[str, Any]] = Field(None, alias="runtimeVerification")
    one_time_package: Optional[Dict[str, Any]] = Field(None, alias="oneTimePackage")

class JobResponse(BaseSchema):
    id: str
    member_id: Optional[str] = Field(None, alias="memberId")
    username: str
    full_name: str = Field(alias="fullName")
    server_id: str = Field(alias="serverId")
    server_name: Optional[str] = Field(None, alias="serverName")
    workspace: Optional[str] = Field("default", alias="workspace")
    pid: int
    ppid: Optional[int] = Field(None, alias="ppid")
    script: str
    cmdline: Optional[str] = Field(None, alias="cmdline")
    gpu_id: Optional[str] = Field(None, alias="gpuId")
    cpu_percent: float = Field(0.0, alias="cpuPercent")
    ram_used_mb: float = Field(0.0, alias="ramUsedMb")
    vram_used_mb: int = Field(0, alias="vramUsedMb")
    start_time: str = Field(alias="startTime")
    elapsed_time: str = Field(alias="elapsedTime")
    status: str
    ssh_status: str = Field(alias="sshStatus")
    container_state: str = Field(alias="containerState")
    is_gpu: Optional[bool] = Field(False, alias="isGpu")
    children_count: Optional[int] = Field(0, alias="childrenCount")
    attribution: Optional[str] = Field("VERIFIED_USER_WORKLOAD", alias="attribution")
    is_root_workload: Optional[bool] = Field(True, alias="isRootWorkload")

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
