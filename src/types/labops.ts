/**
 * LabOps Control Center - Core Infrastructure Types
 */

export type ServerId = 'track' | 'workstation';

export type ServerStatus = 'online' | 'offline' | 'degraded' | 'unknown';

export interface PhysicalServer {
  id: ServerId;
  name: string;
  hostname: string;
  location: string;
  status: ServerStatus;
  ipAddress: string;
  vpnIp: string;
  uptime: string;
  cpuModel: string;
  cpuCores: number;
  cpuUsagePercent: number;
  ramTotalGb: number;
  ramUsedGb: number;
  ramUsagePercent: number;
  diskTotalTb: number;
  diskUsedTb: number;
  diskUsagePercent: number;
  diskType: string;
  networkInMbps: number;
  networkOutMbps: number;
  gpuCount: number;
  activeMembers: number;
  activeJobs: number;
  incusVersion: string;
  driverVersion: string;
  cudaVersion: string;
  lastUpdated: string;
}

export type GpuAllocationMode = 'SHARED' | 'EXCLUSIVE' | 'MIG';

export type GpuEnforcementState =
  | 'HARDWARE_ENFORCED'
  | 'DRIVER_ENFORCED_VERIFIED'
  | 'APPLICATION_ENFORCED'
  | 'MONITOR_ONLY'
  | 'UNSUPPORTED';

export interface GpuProcess {
  pid: number;
  name: string;
  username: string;
  workspace: string;
  vramMb: number;
  cpuPercent: number;
}

export interface PhysicalGpu {
  id: string;
  displayLabel: string;
  serverId: ServerId;
  serverName: string;
  model: string;
  pciBusId: string;
  uuid: string;
  vramTotalMb: number;
  vramUsedMb: number;
  gpuUtilPercent: number;
  tempCelsius: number;
  powerWatts: number;
  powerLimitWatts: number;
  allocationMode: GpuAllocationMode;
  enforcementState: GpuEnforcementState;
  activeProcesses: GpuProcess[];
  activeUsers: string[];
  associatedJobs: string[];
}

export type MemberRole = 'SUPER_ADMIN' | 'LAB_MANAGER' | 'RESEARCHER' | 'STUDENT' | 'GUEST' | 'ADMIN';
export type MemberStatus =
  | 'CREATED'
  | 'PROVISIONING'
  | 'PENDING_PROVISIONING'
  | 'VERIFYING'
  | 'READY'
  | 'NOT_VERIFIED'
  | 'NOT_ENFORCEABLE'
  | 'PARTIAL_FAILURE'
  | 'PENDING_KEY_ENROLLMENT'
  | 'PENDING_REVOKE'
  | 'VERIFIED_REVOKED'
  | 'FAILED'
  | 'ACTIVE'
  | 'SUSPENDED'
  | 'PROVISIONING_FAILED'
  | 'ARCHIVED'
  | 'DELETED';
export type SshStatus =
  | 'ONLINE'
  | 'OFFLINE'
  | 'READY'
  | 'READY_PENDING_RUNTIME'
  | 'VERIFYING'
  | 'NOT_ENFORCEABLE'
  | 'PENDING_REVOKE'
  | 'VERIFIED_REVOKED'
  | 'FAILED'
  | 'PARTIAL_FAILURE'
  | 'NOT_VERIFIED';
export type SshKeyEnrollmentStatus =
  | 'PENDING_KEY_ENROLMENT'
  | 'PENDING_KEY_ENROLLMENT'
  | 'PENDING_SYNC'
  | 'PENDING_REVOKE'
  | 'VERIFIED_REVOKED'
  | 'FAILED'
  | 'ACTIVE'
  | 'ROTATING'
  | 'REVOKED'
  | 'NOT_CONFIGURED';

export interface StoragePool {
  id: string;
  serverId: ServerId;
  serverName: string;
  name: string;
  label: string;
  diskIdentifier: string; // e.g. "wwn-0x5002538f411b4101"
  mountPoint: string;
  driver: 'zfs' | 'btrfs' | 'lvm' | 'dir';
  capacityGb: number;
  usedGb: number;
  freeGb: number;
  healthStatus: 'HEALTHY' | 'DEGRADED' | 'FAULTED';
  isDefault: boolean;
}

export interface StorageAllocation {
  id: string;
  memberId: string;
  poolId: string;
  volumeName: string;
  mountPath: string; // approved: "/workspace", "/home/username", "/data"
  quotaGb: number;
  usedGb: number;
  readOnly: boolean;
  backupPolicy: 'DAILY_SNAPSHOT' | 'WEEKLY_SYNC' | 'NONE';
  sharedDatasets: string[];
  createdAt: string;
}

export interface SshEndpoint {
  id: string;
  memberId: string;
  serverId: ServerId;
  listenAddress: string; // e.g. "10.200.0.1" (Track) or "10.200.0.2" (Workstation)
  listenPort: number; // 22000-29999
  containerPort: number; // 22
  protocol: 'tcp';
  status: 'ACTIVE' | 'DISABLED';
  lastUsedAt?: string;
}

export interface SshCredential {
  id: string;
  memberId: string;
  workspaceId?: string;
  publicKey: string;
  fingerprint: string;
  keyType: 'ed25519' | 'rsa' | 'ecdsa';
  enrollmentType: 'BROWSER_GENERATED' | 'IMPORTED' | 'PENDING';
  status: SshKeyEnrollmentStatus;
  enrolledAt?: string;
  revokedAt?: string;
  expiresAt?: string;
}

export type SshAuthMode = 'PUBLIC_KEY_AND_PASSWORD' | 'OPENSSH_PUBLIC_KEY' | 'TELEPORT_CERTIFICATE_PENDING';
export type SshPasswordStatus =
  | 'ACTIVE'
  | 'CHANGE_REQUIRED'
  | 'TEMP_PASSWORD_EXPIRED'
  | 'REVOKED'
  | 'LOCKED'
  | 'EXPIRED'
  | 'LEGACY_NOT_VERIFIED'
  | 'NOT_APPLICABLE';
export type SshAccessStatus = 'ACTIVE' | 'SUSPENDED' | 'LOCKED';

export interface SshLoginEvent {
  id: string;
  memberId: string;
  workspaceId: string;
  linuxUsername: string;
  sourceIp: string;
  listenPort: number;
  authStage: 'STAGE_1_PUBLICKEY' | 'STAGE_2_PASSWORD' | 'COMPLETED';
  authenticationResult: 'SUCCESS' | 'PUBLICKEY_FAILED' | 'PASSWORD_FAILED' | 'BOTH_FAILED';
  failureReason?: string;
  timestamp: string;
}

export interface SshAccountPolicy {
  workspaceId: string;
  linuxUsername: string;
  authenticationMode: SshAuthMode;
  passwordStatus: SshPasswordStatus;
  keyEnrollmentStatus: SshKeyEnrollmentStatus;
  accessStatus: SshAccessStatus;
  minPasswordLength: number;
  failedLoginAttempts: number;
  lastSuccessfulLogin?: string;
  lastLoginSourceIp?: string;
  updatedAt: string;
}

export interface SshDualAuthVerificationResult {
  sshdSyntaxValid: boolean;
  authenticationMethods: string; // "publickey,password"
  pubkeyAuthentication: boolean;
  passwordAuthentication: boolean;
  permitRootLogin: boolean;
  permitEmptyPasswords: boolean;
  usePam: boolean;
  testMatrix: Record<string, string>;
  status: 'DUAL_AUTH_ENFORCED_VERIFIED' | 'CONFIGURATION_ERROR';
  effectiveConfigSummary: string;
}

export interface PrivilegeValidationResult {
  unprivilegedContainer: boolean;
  sudoTestFailedAsExpected: boolean; // sudo -n true failed (safe)
  noSudoGroupMembership: boolean;
  noDockerSocketAccess: boolean;
  noIncusSocketAccess: boolean;
  verificationTimestamp: string;
  status: 'VERIFIED_SAFE' | 'VIOLATION_DETECTED' | 'NOT_VERIFIED';
}

export interface WireGuardDevice {
  deviceId: string;
  id?: string;
  external_id?: number | string;
  provider?: string;
  name: string;
  publicKey?: string;
  public_key?: string;
  clientIp?: string;
  vpn_ip?: string;
  serverId?: ServerId;
  serverPublicKey?: string;
  serverEndpoint?: string;
  allowedSubnets?: string;
  status: 'ACTIVE' | 'PENDING_HANDSHAKE' | 'REVOKED' | 'DISABLED' | 'PARTIAL_FAILURE';
  enabled?: boolean;
  createdAt?: number | string;
  created_at?: string;
  lastHandshake?: number | string | null;
  last_handshake?: number | string | null;
  transferRx?: number;
  transferTx?: number;
  transfer_rx?: number;
  transfer_tx?: number;
}

export interface WireGuardVpnOverview {
  active_provider: string;
  status: 'HEALTHY' | 'DEGRADED' | 'UNHEALTHY' | string;
  public_endpoint: string;
  cidr: string;
  total_clients: number;
  active_clients: number;
  total_rx_bytes: number;
  total_tx_bytes: number;
  web_ui_internal?: string;
  legacy_interface?: string;
}

export interface WireGuardProfile {
  vpnIp?: string;
  clientIp?: string;
  publicKey?: string;
  serverPublicKey?: string;
  serverEndpoint?: string;
  allowedSubnets?: string;
  status: 'ACTIVE' | 'PENDING' | 'PENDING_HANDSHAKE' | 'NOT_CONFIGURED' | 'NOT_VERIFIED' | 'REVOKED' | 'PARTIAL_FAILURE';
  lastHandshake?: string;
  transferRxBytes?: number;
  transferTxBytes?: number;
  devices?: WireGuardDevice[];
}

export interface LabMember {
  id: string;
  fullName: string;
  username: string;
  email: string;
  role: MemberRole;
  researchGroup?: string;
  assignedServer: ServerId;
  workspaceName: string;
  status: MemberStatus;
  provisioningState?: string;
  workspaceStatus?: string;
  teleportStatus?: string;
  // WireGuard VPN Mesh
  wireguard?: WireGuardProfile;
  // Storage Details
  storagePoolId: string;
  storagePoolLabel: string;
  mountPath: string; // approved path, default /workspace
  storageQuotaGb: number;
  storageUsedGb: number;
  backupPolicy: 'DAILY_SNAPSHOT' | 'WEEKLY_SYNC' | 'NONE';
  sharedDatasets: string[];
  // SSH & Networking
  sshStatus: SshStatus;
  sshSessionsCount: number;
  sshIp?: string;
  sshPort?: number | null;
  sshKeyStatus: SshKeyEnrollmentStatus;
  sshFingerprint?: string;
  sshPublicKeyPreview: string;
  sshPublicKey?: string;
  // Mandatory Dual SSH Authentication
  sshAuthMode: SshAuthMode;
  passwordStatus: SshPasswordStatus;
  failedLoginAttempts: number;
  lastSuccessfulLogin?: string;
  lastLoginSourceIp?: string;
  // Security & Privileges
  sudoPrivilege: 'DENIED'; // Strict: always DENIED
  securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED' | 'NOT_VERIFIED';
  privilegeValidation?: PrivilegeValidationResult;
  // Hardware Quotas
  cpuLimitCores: number;
  ramLimitGb: number;
  gpuPermissions: string[];
  vramPolicy: string;
  vramQuotaGb?: number;
  gpuAllocationMode: GpuAllocationMode;
  maxRuntimeHours: number;
  activeJobsCount: number;
  totalGpuHours: number;
  createdAt: string;
  expiresAt?: string;
  lastLoginAt?: string;
  workspaceDir?: string;
  tempPasswordExpiresAt?: string;
  runtimeVerification?: {
    verified_at?: string;
    host_online?: boolean;
    directory_isolation?: string;
    ssh_key_installed?: string;
    privilege_isolation?: string;
    cpu_ram_limits?: string;
    storage_quota?: string;
    gpu_isolation?: string;
    ssh_runtime?: string;
    overall_status?: string;
    reasons?: string[];
  };
  oneTimePackage?: {
    key_bundle_available?: boolean;
    key_bundle_expires_at?: string;
    channel_secrets_available?: boolean;
    channel_secrets_expires_at?: string;
  };
}

export type JobStatus = 'RUNNING' | 'PENDING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';

export interface ComputeJob {
  id: string;
  username: string;
  fullName: string;
  serverId: ServerId;
  serverName?: string;
  workspace: string;
  pid: number;
  ppid?: number;
  script: string;
  cmdline?: string;
  gpuId?: string | null;
  cpuPercent: number;
  ramUsedMb: number;
  vramUsedMb: number;
  startTime: string;
  elapsedTime: string;
  status: JobStatus;
  sshStatus: string;
  containerState: string;
  isGpu?: boolean;
}

export interface RealSshSession {
  id: string;
  username: string;
  terminal: string;
  sourceIp: string;
  startedAt: string;
  sessionDuration: string;
  serverId: ServerId;
  serverName: string;
  workspace: string;
  status: string;
}

export type SecurityReviewStatus =
  | 'SUBMITTED'
  | 'QUARANTINED'
  | 'SCANNING'
  | 'SANDBOX_ALLOWED'
  | 'MANUAL_REVIEW'
  | 'APPROVED'
  | 'REJECTED'
  | 'REVOKED';

export interface SecurityReview {
  id: string;
  repoUrl: string;
  repoName: string;
  commitSha: string;
  submittedBy: string;
  submittedAt: string;
  status: SecurityReviewStatus;
  riskScore: number;
  findings: {
    high: number;
    medium: number;
    low: number;
  };
  summary: string;
  reviewedBy: string | null;
}

export type EventType = 'SEMINAR' | 'MAINTENANCE' | 'LAB_MEETING' | 'WORKSHOP' | 'PRESENTATION';

export interface LabEvent {
  id: string;
  title: string;
  type: EventType;
  date: string;
  startTime: string;
  endTime: string;
  location: string;
  organizer: string;
  relatedServer: ServerId | 'all' | null;
  status: 'SCHEDULED' | 'IN_PROGRESS' | 'COMPLETED' | 'CANCELLED';
  description: string;
}

export interface AuditLog {
  id: string;
  actor: string;
  action: string;
  targetType: string;
  targetId: string;
  timestamp: string;
  result: 'SUCCESS' | 'FAILED' | 'WARNING';
  correlationId: string;
  details: string;
}
