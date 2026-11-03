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

export type MemberRole = 'SUPER_ADMIN' | 'LAB_MANAGER' | 'RESEARCHER' | 'GUEST';
export type MemberStatus = 'ACTIVE' | 'SUSPENDED' | 'ARCHIVED';
export type SshStatus = 'ONLINE' | 'OFFLINE';

export interface LabMember {
  id: string;
  fullName: string;
  username: string;
  email: string;
  role: MemberRole;
  assignedServer: ServerId;
  workspaceName: string;
  status: MemberStatus;
  sshStatus: SshStatus;
  sshSessionsCount: number;
  sshPublicKeyPreview: string;
  cpuLimitCores: number;
  ramLimitGb: number;
  storageQuotaGb: number;
  gpuPermissions: string[];
  vramPolicy: string;
  maxRuntimeHours: number;
  activeJobsCount: number;
  totalGpuHours: number;
  createdAt: string;
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
