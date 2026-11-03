/**
 * LabOps Control Center - Core Infrastructure Types
 */

export type ServerId = 'track' | 'workstation';

export type ServerStatus = 'online' | 'offline' | 'degraded';

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

export type MemberRole = 'SUPER_ADMIN' | 'LAB_MANAGER' | 'RESEARCHER' | 'STUDENT' | 'GUEST';
export type MemberStatus =
  | 'CREATED'
  | 'PROVISIONING'
  | 'PENDING_KEY_ENROLLMENT'
  | 'ACTIVE'
  | 'SUSPENDED'
  | 'PROVISIONING_FAILED'
  | 'ARCHIVED'
  | 'DELETED';
export type SshStatus = 'ONLINE' | 'OFFLINE';
export type SshKeyEnrollmentStatus = 'PENDING_KEY_ENROLLMENT' | 'ACTIVE' | 'ROTATING' | 'REVOKED';

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
  publicKey: string;
  fingerprint: string;
  keyType: 'ed25519' | 'rsa' | 'ecdsa';
  enrollmentType: 'BROWSER_GENERATED' | 'IMPORTED' | 'PENDING';
  status: SshKeyEnrollmentStatus;
  enrolledAt?: string;
  expiresAt?: string;
}

export interface PrivilegeValidationResult {
  unprivilegedContainer: boolean;
  sudoTestFailedAsExpected: boolean; // sudo -n true failed (safe)
  noSudoGroupMembership: boolean;
  noDockerSocketAccess: boolean;
  noIncusSocketAccess: boolean;
  verificationTimestamp: string;
  status: 'VERIFIED_SAFE' | 'VIOLATION_DETECTED';
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
  sshIp: string;
  sshPort: number;
  sshKeyStatus: SshKeyEnrollmentStatus;
  sshFingerprint: string;
  sshPublicKeyPreview: string;
  sshPublicKey?: string;
  // Security & Privileges
  sudoPrivilege: 'DENIED'; // Strict: always DENIED
  securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED';
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
}

export type JobStatus = 'RUNNING' | 'PENDING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';

export interface ComputeJob {
  id: string;
  username: string;
  fullName: string;
  serverId: ServerId;
  workspace: string;
  pid: number;
  script: string;
  gpuId: string;
  cpuPercent: number;
  ramUsedMb: number;
  vramUsedMb: number;
  startTime: string;
  elapsedTime: string;
  status: JobStatus;
  sshStatus: 'SSH Online' | 'SSH Offline';
  containerState: 'Running' | 'Stopped';
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

export interface DashboardSummary {
  servers: {
    total: number;
    online: number;
    offline: number;
  };
  gpus: {
    totalCount: number;
    model: string;
    aggregateVramGb: number;
    activeCount: number;
    usedVramGb: number;
  };
  members: {
    onlineSsh: number;
    activeSessions: number;
    totalRegistered: number;
  };
  jobs: {
    running: number;
    pending: number;
    failed: number;
  };
  gpuHours: {
    today: number;
    last7Days: number;
    previousPeriodChangePercent: number;
  };
  alerts: {
    critical: number;
    warnings: number;
    unresolvedIncidents: number;
  };
}
