import { create } from 'zustand';
import {
  PhysicalServer,
  PhysicalGpu,
  LabMember,
  ComputeJob,
  SecurityReview,
  LabEvent,
  AuditLog,
  DashboardSummary,
  StoragePool,
  StorageAllocation,
  SshEndpoint,
  SshAccountPolicy,
  SshLoginEvent,
  RealSshSession,
  ServerId,
  JobStatus
} from '@/types/labops';

interface LabOpsState {
  servers: PhysicalServer[];
  gpus: PhysicalGpu[];
  members: LabMember[];
  jobs: ComputeJob[];
  activeSshSessions: RealSshSession[];
  lastSyncTime: string | null;
  syncError: string | null;
  isLivePolling: boolean;
  securityReviews: SecurityReview[];
  events: LabEvent[];
  auditLogs: AuditLog[];
  storagePools: StoragePool[];
  sshEndpoints: SshEndpoint[];
  storageAllocations: StorageAllocation[];
  sshPolicies: SshAccountPolicy[];
  sshLoginEvents: SshLoginEvent[];

  // Live polling actions
  setLivePolling: (enabled: boolean) => void;
  fetchLiveJobs: () => Promise<void>;
  fetchLiveSshSessions: () => Promise<void>;

  // Computed summary
  getSummary: () => DashboardSummary;

  // Actions that mutate state in Demo Mode
  addMember: (
    member: Omit<LabMember, 'id' | 'createdAt' | 'activeJobsCount' | 'totalGpuHours'>
  ) => void;
  updateMemberStatus: (id: string, status: LabMember['status']) => void;
  updateMemberQuotas: (
    id: string,
    quotas: Partial<
      Pick<LabMember, 'cpuLimitCores' | 'ramLimitGb' | 'storageQuotaGb' | 'vramPolicy'>
    >
  ) => void;
  deleteMember: (id: string) => void;
  deleteMemberPermanently: (id: string) => void;

  rotateSshKey: (id: string, newPublicKey: string, newFingerprint: string) => void;
  revokeSshKey: (id: string) => void;
  resetMemberSshPassword: (
    id: string,
    initialPassword?: string,
    requireChange?: boolean
  ) => { success: boolean; tempPassword?: string; message: string };
  requirePasswordChange: (id: string) => void;
  suspendSshAccess: (id: string, reason?: string) => void;
  restoreSshAccess: (id: string) => void;
  getMemberLoginEvents: (username: string) => SshLoginEvent[];
  changeStorageQuota: (id: string, newQuotaGb: number) => { success: boolean; message: string };
  changeSshPort: (id: string, newPort: number) => { success: boolean; message: string };
  archiveWorkspace: (id: string) => void;

  allocateGpu: (payload: {
    gpuId: string;
    username: string;
    mode: 'SHARED' | 'EXCLUSIVE';
    requestedVramGb: number;
    jobName: string;
  }) => void;

  stopJob: (jobId: string) => void;

  updateSecurityReview: (id: string, status: SecurityReview['status'], reviewer: string) => void;
  addSecurityReview: (repoUrl: string, submitter: string) => void;

  addEvent: (event: Omit<LabEvent, 'id'>) => void;
  updateEventStatus: (id: string, status: LabEvent['status']) => void;

  addAuditLog: (log: Omit<AuditLog, 'id' | 'timestamp' | 'correlationId'>) => void;
  fetchLiveState: () => Promise<void>;
}

const INITIAL_SERVERS: PhysicalServer[] = [
  {
    id: 'track',
    name: 'Server 01 — Track',
    hostname: 'track.lab.internal',
    location: 'Phòng Lab Hà Nội (Khu A)',
    status: 'online',
    ipAddress: '10.10.1.10',
    vpnIp: '10.200.0.1',
    uptime: '45 ngày 12 giờ',
    cpuModel: 'AMD EPYC 7763 64-Core Processor (128 vCPUs)',
    cpuCores: 64,
    cpuUsagePercent: 42.5,
    ramTotalGb: 256,
    ramUsedGb: 174.2,
    ramUsagePercent: 68.0,
    diskTotalTb: 8.0,
    diskUsedTb: 4.32,
    diskUsagePercent: 54.0,
    diskType: 'ZFS RAID-Z2 (NVMe Pools)',
    networkInMbps: 620,
    networkOutMbps: 580,
    gpuCount: 3,
    activeMembers: 4,
    activeJobs: 5,
    incusVersion: '6.0.2 LTS',
    driverVersion: '550.120',
    cudaVersion: '12.4',
    lastUpdated: 'Vừa xong'
  },
  {
    id: 'workstation',
    name: 'Server 02 — Workstation',
    hostname: 'ws.lab.internal',
    location: 'Phòng Lab TP.HCM (Khu B)',
    status: 'online',
    ipAddress: '10.10.2.20',
    vpnIp: '10.200.0.2',
    uptime: '18 ngày 06 giờ',
    cpuModel: 'Intel Xeon Gold 6338 32-Core Processor (64 vCPUs)',
    cpuCores: 32,
    cpuUsagePercent: 28.3,
    ramTotalGb: 128,
    ramUsedGb: 57.6,
    ramUsagePercent: 45.0,
    diskTotalTb: 4.0,
    diskUsedTb: 1.52,
    diskUsagePercent: 38.0,
    diskType: 'ZFS Mirror (Enterprise SSD)',
    networkInMbps: 210,
    networkOutMbps: 180,
    gpuCount: 1,
    activeMembers: 1,
    activeJobs: 1,
    incusVersion: '6.0.2 LTS',
    driverVersion: '550.120',
    cudaVersion: '12.4',
    lastUpdated: 'Vừa xong'
  }
];

const INITIAL_GPUS: PhysicalGpu[] = [
  {
    id: 'track-gpu-01',
    displayLabel: 'TRACK-GPU-01',
    serverId: 'track',
    serverName: 'Server Track',
    model: 'NVIDIA A30 24GB HBM2',
    pciBusId: '0000:3b:00.0',
    uuid: 'GPU-9b48a1c2-3e4f-4a89-91a2-7b1c4e8d0001',
    vramTotalMb: 24576,
    vramUsedMb: 21913,
    gpuUtilPercent: 88,
    tempCelsius: 68,
    powerWatts: 142,
    powerLimitWatts: 165,
    allocationMode: 'SHARED',
    enforcementState: 'DRIVER_ENFORCED_VERIFIED',
    activeProcesses: [
      {
        pid: 38192,
        name: 'python (train_llama3_lora.py)',
        username: 'hieulc',
        workspace: 'ws-hieulc',
        vramMb: 14336,
        cpuPercent: 34.2
      },
      {
        pid: 41205,
        name: 'python (clip_feature_extract.py)',
        username: 'tuanpm',
        workspace: 'ws-tuanpm',
        vramMb: 7577,
        cpuPercent: 18.5
      }
    ],
    activeUsers: ['hieulc', 'tuanpm'],
    associatedJobs: ['job-8921', 'job-8924']
  },
  {
    id: 'track-gpu-02',
    displayLabel: 'TRACK-GPU-02',
    serverId: 'track',
    serverName: 'Server Track',
    model: 'NVIDIA A30 24GB HBM2',
    pciBusId: '0000:5e:00.0',
    uuid: 'GPU-9b48a1c2-3e4f-4a89-91a2-7b1c4e8d0002',
    vramTotalMb: 24576,
    vramUsedMb: 23347,
    gpuUtilPercent: 95,
    tempCelsius: 72,
    powerWatts: 158,
    powerLimitWatts: 165,
    allocationMode: 'EXCLUSIVE',
    enforcementState: 'HARDWARE_ENFORCED',
    activeProcesses: [
      {
        pid: 29014,
        name: 'python (whisper_vietnamese_finetune.py)',
        username: 'namnv',
        workspace: 'ws-namnv',
        vramMb: 23347,
        cpuPercent: 55.4
      }
    ],
    activeUsers: ['namnv'],
    associatedJobs: ['job-8919']
  },
  {
    id: 'track-gpu-03',
    displayLabel: 'TRACK-GPU-03',
    serverId: 'track',
    serverName: 'Server Track',
    model: 'NVIDIA A30 24GB HBM2',
    pciBusId: '0000:86:00.0',
    uuid: 'GPU-9b48a1c2-3e4f-4a89-91a2-7b1c4e8d0003',
    vramTotalMb: 24576,
    vramUsedMb: 512,
    gpuUtilPercent: 0,
    tempCelsius: 42,
    powerWatts: 38,
    powerLimitWatts: 165,
    allocationMode: 'SHARED',
    enforcementState: 'MONITOR_ONLY',
    activeProcesses: [],
    activeUsers: [],
    associatedJobs: []
  },
  {
    id: 'ws-gpu-01',
    displayLabel: 'WS-GPU-01',
    serverId: 'workstation',
    serverName: 'Server Workstation',
    model: 'NVIDIA A30 24GB HBM2',
    pciBusId: '0000:1a:00.0',
    uuid: 'GPU-7e12f3d4-1a2b-4c5d-8e9f-0a1b2c3d0004',
    vramTotalMb: 24576,
    vramUsedMb: 18636,
    gpuUtilPercent: 76,
    tempCelsius: 64,
    powerWatts: 125,
    powerLimitWatts: 165,
    allocationMode: 'SHARED',
    enforcementState: 'DRIVER_ENFORCED_VERIFIED',
    activeProcesses: [
      {
        pid: 18274,
        name: 'python (diffusers_sdxl_lora.py)',
        username: 'lanpt',
        workspace: 'ws-lanpt',
        vramMb: 18636,
        cpuPercent: 41.0
      }
    ],
    activeUsers: ['lanpt'],
    associatedJobs: ['job-8922']
  }
];

const INITIAL_STORAGE_POOLS: StoragePool[] = [
  {
    id: 'pool-nvme-fast',
    serverId: 'track',
    serverName: 'Server 01 — Track',
    name: 'pool-nvme-fast',
    label: 'SSD NVMe Siêu tốc — Storage Pool A',
    diskIdentifier: 'wwn-0x5002538f411b4101',
    mountPoint: '/var/lib/incus/storage-pools/pool-nvme-fast',
    driver: 'zfs',
    capacityGb: 8000,
    usedGb: 4320,
    freeGb: 3680,
    healthStatus: 'HEALTHY',
    isDefault: true
  },
  {
    id: 'pool-zfs-bulk',
    serverId: 'track',
    serverName: 'Server 01 — Track',
    name: 'pool-zfs-bulk',
    label: 'HDD Lưu trữ Lớn — Storage Pool B',
    diskIdentifier: 'wwn-0x5000cca268e31249',
    mountPoint: '/var/lib/incus/storage-pools/pool-zfs-bulk',
    driver: 'zfs',
    capacityGb: 16000,
    usedGb: 4600,
    freeGb: 11400,
    healthStatus: 'HEALTHY',
    isDefault: false
  },
  {
    id: 'pool-workstation-ssd',
    serverId: 'workstation',
    serverName: 'Server 02 — Workstation',
    name: 'pool-workstation-ssd',
    label: 'SSD Doanh nghiệp — Storage Pool A',
    diskIdentifier: 'wwn-0x5002538f411b5202',
    mountPoint: '/var/lib/incus/storage-pools/pool-workstation-ssd',
    driver: 'zfs',
    capacityGb: 4000,
    usedGb: 1520,
    freeGb: 2480,
    healthStatus: 'HEALTHY',
    isDefault: true
  }
];

const INITIAL_MEMBERS: LabMember[] = [
  {
    id: 'mem-001',
    fullName: 'Lê Chí Hiếu',
    username: 'hieulc',
    email: 'hieulc@labops.local',
    role: 'SUPER_ADMIN',
    researchGroup: 'NLP & Large Language Models',
    assignedServer: 'track',
    workspaceName: 'ws-hieulc',
    status: 'ACTIVE',
    storagePoolId: 'pool-nvme-fast',
    storagePoolLabel: 'SSD NVMe Siêu tốc (Pool A)',
    mountPath: '/workspace',
    storageQuotaGb: 500,
    storageUsedGb: 182,
    backupPolicy: 'DAILY_SNAPSHOT',
    sharedDatasets: ['/shared/datasets', '/data/models'],
    sshStatus: 'ONLINE',
    sshSessionsCount: 2,
    sshIp: '10.200.0.1',
    sshPort: 22001,
    sshKeyStatus: 'ACTIVE',
    sshFingerprint: 'SHA256:4X7x9fQm8zP1vL0k2j3h4g5f6e7d8c9b0a1s2d3f4e',
    sshPublicKeyPreview: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH... hieulc@macbook',
    sshPublicKey:
      'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH8q9v8nZ+u8d/2X8s6mXvYq9s7t6r5e4w3q2a1s0d9 hieulc@macbook',
    sudoPrivilege: 'DENIED',
    securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED',
    privilegeValidation: {
      unprivilegedContainer: true,
      sudoTestFailedAsExpected: true,
      noSudoGroupMembership: true,
      noDockerSocketAccess: true,
      noIncusSocketAccess: true,
      verificationTimestamp: '2026-03-01T08:00:00Z',
      status: 'VERIFIED_SAFE'
    },
    cpuLimitCores: 16,
    ramLimitGb: 64,
    gpuPermissions: ['TRACK-GPU-01', 'TRACK-GPU-02', 'TRACK-GPU-03', 'WS-GPU-01'],
    vramPolicy: 'Tối đa 24GB (Shared/Exclusive)',
    vramQuotaGb: 24,
    gpuAllocationMode: 'SHARED',
    maxRuntimeHours: 72,
    activeJobsCount: 1,
    totalGpuHours: 142.5,
    sshAuthMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    failedLoginAttempts: 0,
    lastSuccessfulLogin: '2026-03-03T14:20:00Z',
    lastLoginSourceIp: '10.200.0.105',
    createdAt: '2026-01-10',
    expiresAt: '2027-01-10'
  },
  {
    id: 'mem-002',
    fullName: 'Nguyễn Văn Nam',
    username: 'namnv',
    email: 'namnv@labops.local',
    role: 'RESEARCHER',
    researchGroup: 'Speech & Audio Intelligence',
    assignedServer: 'track',
    workspaceName: 'ws-namnv',
    status: 'ACTIVE',
    storagePoolId: 'pool-nvme-fast',
    storagePoolLabel: 'SSD NVMe Siêu tốc (Pool A)',
    mountPath: '/workspace',
    storageQuotaGb: 400,
    storageUsedGb: 145,
    backupPolicy: 'DAILY_SNAPSHOT',
    sharedDatasets: ['/shared/datasets'],
    sshStatus: 'ONLINE',
    sshSessionsCount: 1,
    sshIp: '10.200.0.1',
    sshPort: 22002,
    sshKeyStatus: 'ACTIVE',
    sshFingerprint: 'SHA256:7K9m1nO3p5q7r9s1t3u5v7w9x1y3z5a7b9c1d3e5f7',
    sshPublicKeyPreview: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC... namnv@laptop',
    sshPublicKey: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC+0g... namnv@laptop',
    sudoPrivilege: 'DENIED',
    securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED',
    privilegeValidation: {
      unprivilegedContainer: true,
      sudoTestFailedAsExpected: true,
      noSudoGroupMembership: true,
      noDockerSocketAccess: true,
      noIncusSocketAccess: true,
      verificationTimestamp: '2026-03-01T08:00:00Z',
      status: 'VERIFIED_SAFE'
    },
    cpuLimitCores: 16,
    ramLimitGb: 64,
    gpuPermissions: ['TRACK-GPU-02'],
    vramPolicy: 'Exclusive 24GB',
    vramQuotaGb: 24,
    gpuAllocationMode: 'EXCLUSIVE',
    maxRuntimeHours: 48,
    activeJobsCount: 1,
    totalGpuHours: 98.2,
    sshAuthMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    failedLoginAttempts: 0,
    lastSuccessfulLogin: '2026-03-03T11:15:00Z',
    lastLoginSourceIp: '10.200.0.110',
    createdAt: '2026-01-15'
  },
  {
    id: 'mem-003',
    fullName: 'Trần Thị Lan',
    username: 'lanpt',
    email: 'lanpt@labops.local',
    role: 'RESEARCHER',
    researchGroup: 'Computer Vision & Generative AI',
    assignedServer: 'workstation',
    workspaceName: 'ws-lanpt',
    status: 'ACTIVE',
    storagePoolId: 'pool-workstation-ssd',
    storagePoolLabel: 'SSD Doanh nghiệp (Pool A)',
    mountPath: '/workspace',
    storageQuotaGb: 300,
    storageUsedGb: 98,
    backupPolicy: 'DAILY_SNAPSHOT',
    sharedDatasets: ['/shared/datasets'],
    sshStatus: 'ONLINE',
    sshSessionsCount: 1,
    sshIp: '10.200.0.2',
    sshPort: 22001,
    sshKeyStatus: 'ACTIVE',
    sshFingerprint: 'SHA256:2A4b6c8d0e2f4a6c8e0a2c4e6a8c0e2f4a6c8e0a2b',
    sshPublicKeyPreview: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK... lanpt@workstation',
    sshPublicKey:
      'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK6x7y8z9a0b1c2d3e4f5g6h7i8j9k0l lanpt@workstation',
    sudoPrivilege: 'DENIED',
    securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED',
    privilegeValidation: {
      unprivilegedContainer: true,
      sudoTestFailedAsExpected: true,
      noSudoGroupMembership: true,
      noDockerSocketAccess: true,
      noIncusSocketAccess: true,
      verificationTimestamp: '2026-03-01T08:00:00Z',
      status: 'VERIFIED_SAFE'
    },
    cpuLimitCores: 12,
    ramLimitGb: 48,
    gpuPermissions: ['WS-GPU-01'],
    vramPolicy: 'Quota 20GB',
    vramQuotaGb: 20,
    gpuAllocationMode: 'SHARED',
    maxRuntimeHours: 36,
    activeJobsCount: 1,
    totalGpuHours: 76.4,
    sshAuthMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    failedLoginAttempts: 0,
    lastSuccessfulLogin: '2026-03-03T13:45:00Z',
    lastLoginSourceIp: '10.200.0.115',
    createdAt: '2026-02-01'
  },
  {
    id: 'mem-004',
    fullName: 'Phạm Minh Tuấn',
    username: 'tuanpm',
    email: 'tuanpm@labops.local',
    role: 'RESEARCHER',
    researchGroup: 'Multimodal AI & Robotics',
    assignedServer: 'track',
    workspaceName: 'ws-tuanpm',
    status: 'ACTIVE',
    storagePoolId: 'pool-nvme-fast',
    storagePoolLabel: 'SSD NVMe Siêu tốc (Pool A)',
    mountPath: '/workspace',
    storageQuotaGb: 250,
    storageUsedGb: 64,
    backupPolicy: 'WEEKLY_SYNC',
    sharedDatasets: ['/shared/datasets'],
    sshStatus: 'ONLINE',
    sshSessionsCount: 1,
    sshIp: '10.200.0.1',
    sshPort: 22003,
    sshKeyStatus: 'ACTIVE',
    sshFingerprint: 'SHA256:9Z8y7x6w5v4u3t2s1r0q9p8o7n6m5l4k3j2i1h0g9f',
    sshPublicKeyPreview: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIL... tuanpm@desktop',
    sshPublicKey:
      'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIL0m1n2o3p4q5r6s7t8u9v0w1x2y3z4a tuanpm@desktop',
    sudoPrivilege: 'DENIED',
    securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED',
    privilegeValidation: {
      unprivilegedContainer: true,
      sudoTestFailedAsExpected: true,
      noSudoGroupMembership: true,
      noDockerSocketAccess: true,
      noIncusSocketAccess: true,
      verificationTimestamp: '2026-03-01T08:00:00Z',
      status: 'VERIFIED_SAFE'
    },
    cpuLimitCores: 8,
    ramLimitGb: 32,
    gpuPermissions: ['TRACK-GPU-01', 'TRACK-GPU-03'],
    vramPolicy: 'Quota 12GB',
    vramQuotaGb: 12,
    gpuAllocationMode: 'SHARED',
    maxRuntimeHours: 24,
    activeJobsCount: 1,
    totalGpuHours: 54.0,
    sshAuthMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    failedLoginAttempts: 1,
    lastSuccessfulLogin: '2026-03-03T09:30:00Z',
    lastLoginSourceIp: '10.200.0.120',
    createdAt: '2026-02-10'
  },
  {
    id: 'mem-005',
    fullName: 'Hoàng Đức Anh',
    username: 'anhhd',
    email: 'anhhd@labops.local',
    role: 'LAB_MANAGER',
    researchGroup: 'Infrastructure & MLOps',
    assignedServer: 'track',
    workspaceName: 'ws-anhhd',
    status: 'ACTIVE',
    storagePoolId: 'pool-nvme-fast',
    storagePoolLabel: 'SSD NVMe Siêu tốc (Pool A)',
    mountPath: '/workspace',
    storageQuotaGb: 500,
    storageUsedGb: 210,
    backupPolicy: 'DAILY_SNAPSHOT',
    sharedDatasets: ['/shared/datasets', '/data/models'],
    sshStatus: 'ONLINE',
    sshSessionsCount: 2,
    sshIp: '10.200.0.1',
    sshPort: 22004,
    sshKeyStatus: 'ACTIVE',
    sshFingerprint: 'SHA256:5M4l3k2j1i0h9g8f7e6d5c4b3a2z1y0x9w8v7u6t5s',
    sshPublicKeyPreview: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP... anhhd@thinkpad',
    sshPublicKey:
      'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP1q2w3e4r5t6y7u8i9o0p1a2s3d4f5g anhhd@thinkpad',
    sudoPrivilege: 'DENIED',
    securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED',
    privilegeValidation: {
      unprivilegedContainer: true,
      sudoTestFailedAsExpected: true,
      noSudoGroupMembership: true,
      noDockerSocketAccess: true,
      noIncusSocketAccess: true,
      verificationTimestamp: '2026-03-01T08:00:00Z',
      status: 'VERIFIED_SAFE'
    },
    cpuLimitCores: 16,
    ramLimitGb: 64,
    gpuPermissions: ['TRACK-GPU-01', 'TRACK-GPU-02', 'TRACK-GPU-03'],
    vramPolicy: 'Toàn quyền',
    vramQuotaGb: 24,
    gpuAllocationMode: 'SHARED',
    maxRuntimeHours: 72,
    activeJobsCount: 1,
    totalGpuHours: 110.8,
    sshAuthMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    failedLoginAttempts: 0,
    lastSuccessfulLogin: '2026-03-03T15:00:00Z',
    lastLoginSourceIp: '10.200.0.102',
    createdAt: '2026-01-05'
  },
  {
    id: 'mem-006',
    fullName: 'Vũ Mai Linh',
    username: 'linhvm',
    email: 'linhvm@labops.local',
    role: 'STUDENT',
    researchGroup: 'AI Capstone Projects',
    assignedServer: 'workstation',
    workspaceName: 'ws-linhvm',
    status: 'ACTIVE',
    storagePoolId: 'pool-workstation-ssd',
    storagePoolLabel: 'SSD Doanh nghiệp (Pool A)',
    mountPath: '/workspace',
    storageQuotaGb: 200,
    storageUsedGb: 45,
    backupPolicy: 'WEEKLY_SYNC',
    sharedDatasets: ['/shared/datasets'],
    sshStatus: 'OFFLINE',
    sshSessionsCount: 0,
    sshIp: '10.200.0.2',
    sshPort: 22002,
    sshKeyStatus: 'ACTIVE',
    sshFingerprint: 'SHA256:1Q2w3e4r5t6y7u8i9o0p1a2s3d4f5g6h7j8k9l0z1x',
    sshPublicKeyPreview: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIQ... linhvm@laptop',
    sshPublicKey:
      'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIQ2w3e4r5t6y7u8i9o0p1a2s3d4f5g6h linhvm@laptop',
    sudoPrivilege: 'DENIED',
    securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED',
    privilegeValidation: {
      unprivilegedContainer: true,
      sudoTestFailedAsExpected: true,
      noSudoGroupMembership: true,
      noDockerSocketAccess: true,
      noIncusSocketAccess: true,
      verificationTimestamp: '2026-03-01T08:00:00Z',
      status: 'VERIFIED_SAFE'
    },
    cpuLimitCores: 8,
    ramLimitGb: 32,
    gpuPermissions: ['WS-GPU-01'],
    vramPolicy: 'Quota 8GB',
    vramQuotaGb: 8,
    gpuAllocationMode: 'SHARED',
    maxRuntimeHours: 24,
    activeJobsCount: 0,
    totalGpuHours: 32.1,
    sshAuthMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    failedLoginAttempts: 0,
    lastSuccessfulLogin: '2026-03-02T18:10:00Z',
    lastLoginSourceIp: '10.200.0.130',
    createdAt: '2026-03-01'
  },
  {
    id: 'mem-007',
    fullName: 'Đỗ Quang Huy',
    username: 'huydq',
    email: 'huydq@labops.local',
    role: 'RESEARCHER',
    researchGroup: 'Reinforcement Learning',
    assignedServer: 'track',
    workspaceName: 'ws-huydq',
    status: 'SUSPENDED',
    storagePoolId: 'pool-nvme-fast',
    storagePoolLabel: 'SSD NVMe Siêu tốc (Pool A)',
    mountPath: '/workspace',
    storageQuotaGb: 150,
    storageUsedGb: 52,
    backupPolicy: 'NONE',
    sharedDatasets: [],
    sshStatus: 'OFFLINE',
    sshSessionsCount: 0,
    sshIp: '10.200.0.1',
    sshPort: 22005,
    sshKeyStatus: 'ACTIVE',
    sshFingerprint: 'SHA256:8J7h6g5f4d3s2a1p0o9i8u7y6t5r4e3w2q1z0x9c8v',
    sshPublicKeyPreview: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQD... huydq@home',
    sshPublicKey: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQD... huydq@home',
    sudoPrivilege: 'DENIED',
    securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED',
    privilegeValidation: {
      unprivilegedContainer: true,
      sudoTestFailedAsExpected: true,
      noSudoGroupMembership: true,
      noDockerSocketAccess: true,
      noIncusSocketAccess: true,
      verificationTimestamp: '2026-02-20T10:00:00Z',
      status: 'VERIFIED_SAFE'
    },
    cpuLimitCores: 4,
    ramLimitGb: 16,
    gpuPermissions: [],
    vramPolicy: 'Tạm khóa',
    vramQuotaGb: 0,
    gpuAllocationMode: 'SHARED',
    maxRuntimeHours: 0,
    activeJobsCount: 0,
    totalGpuHours: 18.6,
    sshAuthMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'LOCKED',
    failedLoginAttempts: 3,
    lastSuccessfulLogin: '2026-02-28T16:00:00Z',
    lastLoginSourceIp: '10.200.0.140',
    createdAt: '2026-02-20'
  },
  {
    id: 'mem-008',
    fullName: 'Ngô Bảo Châu',
    username: 'chaunb',
    email: 'chaunb@labops.local',
    role: 'GUEST',
    researchGroup: 'Visiting Scholar Program',
    assignedServer: 'workstation',
    workspaceName: 'ws-chaunb',
    status: 'PENDING_KEY_ENROLLMENT',
    storagePoolId: 'pool-workstation-ssd',
    storagePoolLabel: 'SSD Doanh nghiệp (Pool A)',
    mountPath: '/workspace',
    storageQuotaGb: 100,
    storageUsedGb: 12,
    backupPolicy: 'NONE',
    sharedDatasets: [],
    sshStatus: 'OFFLINE',
    sshSessionsCount: 0,
    sshIp: '10.200.0.2',
    sshPort: 22003,
    sshKeyStatus: 'PENDING_KEY_ENROLLMENT',
    sshFingerprint: 'CHƯA_ĐĂNG_KÝ_KHÓA',
    sshPublicKeyPreview: '(Đang chờ thành viên đăng ký khóa)',
    sudoPrivilege: 'DENIED',
    securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED',
    privilegeValidation: {
      unprivilegedContainer: true,
      sudoTestFailedAsExpected: true,
      noSudoGroupMembership: true,
      noDockerSocketAccess: true,
      noIncusSocketAccess: true,
      verificationTimestamp: '2026-03-15T09:00:00Z',
      status: 'VERIFIED_SAFE'
    },
    cpuLimitCores: 4,
    ramLimitGb: 16,
    gpuPermissions: [],
    vramPolicy: 'Chỉ CPU / Đợi duyệt GPU',
    vramQuotaGb: 0,
    gpuAllocationMode: 'SHARED',
    maxRuntimeHours: 12,
    activeJobsCount: 0,
    totalGpuHours: 4.2,
    sshAuthMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'CHANGE_REQUIRED',
    failedLoginAttempts: 0,
    createdAt: '2026-03-15'
  }
];

const INITIAL_SSH_ENDPOINTS: SshEndpoint[] = [
  {
    id: 'ep-001',
    memberId: 'mem-001',
    serverId: 'track',
    listenAddress: '10.200.0.1',
    listenPort: 22001,
    containerPort: 22,
    protocol: 'tcp',
    status: 'ACTIVE'
  },
  {
    id: 'ep-002',
    memberId: 'mem-002',
    serverId: 'track',
    listenAddress: '10.200.0.1',
    listenPort: 22002,
    containerPort: 22,
    protocol: 'tcp',
    status: 'ACTIVE'
  },
  {
    id: 'ep-003',
    memberId: 'mem-003',
    serverId: 'workstation',
    listenAddress: '10.200.0.2',
    listenPort: 22001,
    containerPort: 22,
    protocol: 'tcp',
    status: 'ACTIVE'
  },
  {
    id: 'ep-004',
    memberId: 'mem-004',
    serverId: 'track',
    listenAddress: '10.200.0.1',
    listenPort: 22003,
    containerPort: 22,
    protocol: 'tcp',
    status: 'ACTIVE'
  },
  {
    id: 'ep-005',
    memberId: 'mem-005',
    serverId: 'track',
    listenAddress: '10.200.0.1',
    listenPort: 22004,
    containerPort: 22,
    protocol: 'tcp',
    status: 'ACTIVE'
  },
  {
    id: 'ep-006',
    memberId: 'mem-006',
    serverId: 'workstation',
    listenAddress: '10.200.0.2',
    listenPort: 22002,
    containerPort: 22,
    protocol: 'tcp',
    status: 'ACTIVE'
  },
  {
    id: 'ep-007',
    memberId: 'mem-007',
    serverId: 'track',
    listenAddress: '10.200.0.1',
    listenPort: 22005,
    containerPort: 22,
    protocol: 'tcp',
    status: 'DISABLED'
  },
  {
    id: 'ep-008',
    memberId: 'mem-008',
    serverId: 'workstation',
    listenAddress: '10.200.0.2',
    listenPort: 22003,
    containerPort: 22,
    protocol: 'tcp',
    status: 'DISABLED'
  }
];

const INITIAL_SSH_POLICIES: SshAccountPolicy[] = [
  {
    workspaceId: 'ws-hieulc',
    linuxUsername: 'hieulc',
    authenticationMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    keyEnrollmentStatus: 'ACTIVE',
    accessStatus: 'ACTIVE',
    minPasswordLength: 12,
    failedLoginAttempts: 0,
    lastSuccessfulLogin: '2026-03-03T14:20:00Z',
    lastLoginSourceIp: '10.200.0.105',
    updatedAt: '2026-01-10T08:00:00Z'
  },
  {
    workspaceId: 'ws-namnv',
    linuxUsername: 'namnv',
    authenticationMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    keyEnrollmentStatus: 'ACTIVE',
    accessStatus: 'ACTIVE',
    minPasswordLength: 12,
    failedLoginAttempts: 0,
    lastSuccessfulLogin: '2026-03-03T11:15:00Z',
    lastLoginSourceIp: '10.200.0.110',
    updatedAt: '2026-01-15T09:00:00Z'
  },
  {
    workspaceId: 'ws-lanpt',
    linuxUsername: 'lanpt',
    authenticationMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    keyEnrollmentStatus: 'ACTIVE',
    accessStatus: 'ACTIVE',
    minPasswordLength: 12,
    failedLoginAttempts: 0,
    lastSuccessfulLogin: '2026-03-03T13:45:00Z',
    lastLoginSourceIp: '10.200.0.115',
    updatedAt: '2026-02-01T10:00:00Z'
  },
  {
    workspaceId: 'ws-tuanpm',
    linuxUsername: 'tuanpm',
    authenticationMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    keyEnrollmentStatus: 'ACTIVE',
    accessStatus: 'ACTIVE',
    minPasswordLength: 12,
    failedLoginAttempts: 1,
    lastSuccessfulLogin: '2026-03-03T09:30:00Z',
    lastLoginSourceIp: '10.200.0.120',
    updatedAt: '2026-02-10T11:00:00Z'
  },
  {
    workspaceId: 'ws-anhhd',
    linuxUsername: 'anhhd',
    authenticationMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    keyEnrollmentStatus: 'ACTIVE',
    accessStatus: 'ACTIVE',
    minPasswordLength: 12,
    failedLoginAttempts: 0,
    lastSuccessfulLogin: '2026-03-03T15:00:00Z',
    lastLoginSourceIp: '10.200.0.102',
    updatedAt: '2026-01-05T08:30:00Z'
  },
  {
    workspaceId: 'ws-linhvm',
    linuxUsername: 'linhvm',
    authenticationMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'ACTIVE',
    keyEnrollmentStatus: 'ACTIVE',
    accessStatus: 'ACTIVE',
    minPasswordLength: 12,
    failedLoginAttempts: 0,
    lastSuccessfulLogin: '2026-03-02T18:10:00Z',
    lastLoginSourceIp: '10.200.0.130',
    updatedAt: '2026-03-01T09:00:00Z'
  },
  {
    workspaceId: 'ws-huydq',
    linuxUsername: 'huydq',
    authenticationMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'LOCKED',
    keyEnrollmentStatus: 'ACTIVE',
    accessStatus: 'SUSPENDED',
    minPasswordLength: 12,
    failedLoginAttempts: 3,
    lastSuccessfulLogin: '2026-02-28T16:00:00Z',
    lastLoginSourceIp: '10.200.0.140',
    updatedAt: '2026-02-20T10:00:00Z'
  },
  {
    workspaceId: 'ws-chaunb',
    linuxUsername: 'chaunb',
    authenticationMode: 'PUBLIC_KEY_AND_PASSWORD',
    passwordStatus: 'CHANGE_REQUIRED',
    keyEnrollmentStatus: 'PENDING_KEY_ENROLLMENT',
    accessStatus: 'ACTIVE',
    minPasswordLength: 12,
    failedLoginAttempts: 0,
    updatedAt: '2026-03-15T09:00:00Z'
  }
];

const INITIAL_SSH_LOGIN_EVENTS: SshLoginEvent[] = [
  {
    id: 'evt-log-001',
    memberId: 'mem-001',
    workspaceId: 'ws-hieulc',
    linuxUsername: 'hieulc',
    sourceIp: '10.200.0.105',
    listenPort: 22001,
    authStage: 'COMPLETED',
    authenticationResult: 'SUCCESS',
    timestamp: '2026-03-03 14:20:12'
  },
  {
    id: 'evt-log-002',
    memberId: 'mem-004',
    workspaceId: 'ws-tuanpm',
    linuxUsername: 'tuanpm',
    sourceIp: '10.200.0.120',
    listenPort: 22003,
    authStage: 'STAGE_2_PASSWORD',
    authenticationResult: 'PASSWORD_FAILED',
    failureReason: 'Sai mật khẩu PAM Linux (Lần 1)',
    timestamp: '2026-03-03 09:28:44'
  },
  {
    id: 'evt-log-003',
    memberId: 'mem-004',
    workspaceId: 'ws-tuanpm',
    linuxUsername: 'tuanpm',
    sourceIp: '10.200.0.120',
    listenPort: 22003,
    authStage: 'COMPLETED',
    authenticationResult: 'SUCCESS',
    timestamp: '2026-03-03 09:30:05'
  },
  {
    id: 'evt-log-004',
    memberId: 'mem-007',
    workspaceId: 'ws-huydq',
    linuxUsername: 'huydq',
    sourceIp: '10.200.0.140',
    listenPort: 22005,
    authStage: 'STAGE_1_PUBLICKEY',
    authenticationResult: 'BOTH_FAILED',
    failureReason: 'Tài khoản đang bị quản trị viên TẠM KHÓA',
    timestamp: '2026-03-01 11:05:22'
  }
];

const INITIAL_STORAGE_ALLOCATIONS: StorageAllocation[] = [
  {
    id: 'sa-001',
    memberId: 'mem-001',
    poolId: 'pool-nvme-fast',
    volumeName: 'data-hieulc',
    mountPath: '/workspace',
    quotaGb: 500,
    usedGb: 182,
    readOnly: false,
    backupPolicy: 'DAILY_SNAPSHOT',
    sharedDatasets: ['/shared/datasets', '/data/models'],
    createdAt: '2026-01-10'
  },
  {
    id: 'sa-002',
    memberId: 'mem-002',
    poolId: 'pool-nvme-fast',
    volumeName: 'data-namnv',
    mountPath: '/workspace',
    quotaGb: 400,
    usedGb: 145,
    readOnly: false,
    backupPolicy: 'DAILY_SNAPSHOT',
    sharedDatasets: ['/shared/datasets'],
    createdAt: '2026-01-15'
  },
  {
    id: 'sa-003',
    memberId: 'mem-003',
    poolId: 'pool-workstation-ssd',
    volumeName: 'data-lanpt',
    mountPath: '/workspace',
    quotaGb: 300,
    usedGb: 98,
    readOnly: false,
    backupPolicy: 'DAILY_SNAPSHOT',
    sharedDatasets: ['/shared/datasets'],
    createdAt: '2026-02-01'
  },
  {
    id: 'sa-004',
    memberId: 'mem-004',
    poolId: 'pool-nvme-fast',
    volumeName: 'data-tuanpm',
    mountPath: '/workspace',
    quotaGb: 250,
    usedGb: 64,
    readOnly: false,
    backupPolicy: 'WEEKLY_SYNC',
    sharedDatasets: ['/shared/datasets'],
    createdAt: '2026-02-10'
  },
  {
    id: 'sa-005',
    memberId: 'mem-005',
    poolId: 'pool-nvme-fast',
    volumeName: 'data-anhhd',
    mountPath: '/workspace',
    quotaGb: 500,
    usedGb: 210,
    readOnly: false,
    backupPolicy: 'DAILY_SNAPSHOT',
    sharedDatasets: ['/shared/datasets', '/data/models'],
    createdAt: '2026-01-05'
  },
  {
    id: 'sa-006',
    memberId: 'mem-006',
    poolId: 'pool-workstation-ssd',
    volumeName: 'data-linhvm',
    mountPath: '/workspace',
    quotaGb: 200,
    usedGb: 45,
    readOnly: false,
    backupPolicy: 'WEEKLY_SYNC',
    sharedDatasets: ['/shared/datasets'],
    createdAt: '2026-03-01'
  },
  {
    id: 'sa-007',
    memberId: 'mem-007',
    poolId: 'pool-nvme-fast',
    volumeName: 'data-huydq',
    mountPath: '/workspace',
    quotaGb: 150,
    usedGb: 52,
    readOnly: false,
    backupPolicy: 'NONE',
    sharedDatasets: [],
    createdAt: '2026-02-20'
  },
  {
    id: 'sa-008',
    memberId: 'mem-008',
    poolId: 'pool-workstation-ssd',
    volumeName: 'data-chaunb',
    mountPath: '/workspace',
    quotaGb: 100,
    usedGb: 12,
    readOnly: false,
    backupPolicy: 'NONE',
    sharedDatasets: [],
    createdAt: '2026-03-15'
  }
];

const INITIAL_JOBS: ComputeJob[] = [
  {
    id: 'job-8921',
    username: 'hieulc',
    fullName: 'Lê Chí Hiếu',
    serverId: 'track',
    workspace: 'ws-hieulc',
    pid: 38192,
    script: 'python train_llama3_lora.py --batch_size 8 --lr 2e-4',
    gpuId: 'TRACK-GPU-01',
    cpuPercent: 34.2,
    ramUsedMb: 18432,
    vramUsedMb: 14336,
    startTime: '2026-11-03 14:15',
    elapsedTime: '06h 05m',
    status: 'RUNNING',
    sshStatus: 'SSH Online',
    containerState: 'Running'
  },
  {
    id: 'job-8924',
    username: 'tuanpm',
    fullName: 'Phạm Minh Tuấn',
    serverId: 'track',
    workspace: 'ws-tuanpm',
    pid: 41205,
    script: 'python clip_feature_extract.py --dataset vi_image_text',
    gpuId: 'TRACK-GPU-01',
    cpuPercent: 18.5,
    ramUsedMb: 9216,
    vramUsedMb: 7577,
    startTime: '2026-11-03 17:30',
    elapsedTime: '02h 50m',
    status: 'RUNNING',
    sshStatus: 'SSH Online',
    containerState: 'Running'
  },
  {
    id: 'job-8919',
    username: 'namnv',
    fullName: 'Nguyễn Văn Nam',
    serverId: 'track',
    workspace: 'ws-namnv',
    pid: 29014,
    script: 'python whisper_vietnamese_finetune.py --epochs 20',
    gpuId: 'TRACK-GPU-02',
    cpuPercent: 55.4,
    ramUsedMb: 32768,
    vramUsedMb: 23347,
    startTime: '2026-11-03 09:00',
    elapsedTime: '11h 20m',
    status: 'RUNNING',
    sshStatus: 'SSH Online',
    containerState: 'Running'
  },
  {
    id: 'job-8922',
    username: 'lanpt',
    fullName: 'Trần Thị Lan',
    serverId: 'workstation',
    workspace: 'ws-lanpt',
    pid: 18274,
    script: 'python diffusers_sdxl_lora.py --model stabilityai/sdxl',
    gpuId: 'WS-GPU-01',
    cpuPercent: 41.0,
    ramUsedMb: 24576,
    vramUsedMb: 18636,
    startTime: '2026-11-03 15:40',
    elapsedTime: '04h 40m',
    status: 'RUNNING',
    sshStatus: 'SSH Online',
    containerState: 'Running'
  },
  {
    id: 'job-8925',
    username: 'anhhd',
    fullName: 'Hoàng Đức Anh',
    serverId: 'track',
    workspace: 'ws-anhhd',
    pid: 50122,
    script: 'python benchmark_flash_attention.py --profile ncu',
    gpuId: 'TRACK-GPU-01',
    cpuPercent: 12.0,
    ramUsedMb: 6144,
    vramUsedMb: 0,
    startTime: '2026-11-03 19:10',
    elapsedTime: '01h 10m',
    status: 'RUNNING',
    sshStatus: 'SSH Online',
    containerState: 'Running'
  },
  {
    id: 'job-8926',
    username: 'hieulc',
    fullName: 'Lê Chí Hiếu',
    serverId: 'track',
    workspace: 'ws-hieulc',
    pid: 51200,
    script: 'bash preprocess_tokenization.sh --corpus c4_vietnamese',
    gpuId: 'CPU Only',
    cpuPercent: 88.0,
    ramUsedMb: 40960,
    vramUsedMb: 0,
    startTime: '2026-11-03 19:45',
    elapsedTime: '35m',
    status: 'RUNNING',
    sshStatus: 'SSH Online',
    containerState: 'Running'
  },
  {
    id: 'job-8915',
    username: 'linhvm',
    fullName: 'Vũ Mai Linh',
    serverId: 'workstation',
    workspace: 'ws-linhvm',
    pid: 0,
    script: 'python train_gnn_citation.py --dataset cora',
    gpuId: 'WS-GPU-01',
    cpuPercent: 0,
    ramUsedMb: 0,
    vramUsedMb: 0,
    startTime: '2026-11-03 20:00',
    elapsedTime: '20m',
    status: 'PENDING',
    sshStatus: 'SSH Offline',
    containerState: 'Running'
  },
  {
    id: 'job-8912',
    username: 'tuanpm',
    fullName: 'Phạm Minh Tuấn',
    serverId: 'track',
    workspace: 'ws-tuanpm',
    pid: 31045,
    script: 'python evaluate_vision_backbone.py --weights resnet50',
    gpuId: 'TRACK-GPU-03',
    cpuPercent: 0,
    ramUsedMb: 0,
    vramUsedMb: 0,
    startTime: '2026-11-03 11:20',
    elapsedTime: '45m',
    status: 'FAILED',
    sshStatus: 'SSH Offline',
    containerState: 'Stopped'
  }
];

const INITIAL_SECURITY_REVIEWS: SecurityReview[] = [
  {
    id: 'sec-001',
    repoUrl: 'https://github.com/huggingface/transformers',
    repoName: 'huggingface/transformers',
    commitSha: 'a3f120e',
    submittedBy: 'hieulc',
    submittedAt: '2026-11-01 10:20',
    status: 'APPROVED',
    riskScore: 4,
    findings: { high: 0, medium: 1, low: 3 },
    summary:
      'Thư viện mã nguồn mở uy tín. Đã quét SAST và không phát hiện lỗ hổng thực thi lệnh tùy ý.',
    reviewedBy: 'anhhd (Admin)'
  },
  {
    id: 'sec-002',
    repoUrl: 'https://github.com/vllm-project/vllm',
    repoName: 'vllm-project/vllm',
    commitSha: '8c91b42',
    submittedBy: 'namnv',
    submittedAt: '2026-11-02 08:45',
    status: 'APPROVED',
    riskScore: 8,
    findings: { high: 0, medium: 2, low: 5 },
    summary: 'CUDA kernels tùy chỉnh đã kiểm tra an toàn bộ nhớ. Cho phép cài đặt trong workspace.',
    reviewedBy: 'hieulc (Super Admin)'
  },
  {
    id: 'sec-003',
    repoUrl: 'https://github.com/vietnamese-ai/whisper-custom-decoder',
    repoName: 'vietnamese-ai/whisper-custom-decoder',
    commitSha: '4f2910a',
    submittedBy: 'namnv',
    submittedAt: '2026-11-03 14:10',
    status: 'MANUAL_REVIEW',
    riskScore: 35,
    findings: { high: 1, medium: 3, low: 2 },
    summary: 'Phát hiện tệp nhị phân .so biên dịch sẵn chưa rõ nguồn gốc trong thư mục /lib.',
    reviewedBy: null
  },
  {
    id: 'sec-004',
    repoUrl: 'https://github.com/unknown-dev/fast-attention-kernel',
    repoName: 'unknown-dev/fast-attention-kernel',
    commitSha: '9e1102b',
    submittedBy: 'chaunb',
    submittedAt: '2026-11-03 16:30',
    status: 'QUARANTINED',
    riskScore: 78,
    findings: { high: 3, medium: 4, low: 6 },
    summary: 'Cách ly tự động: Phát hiện kết nối socket ngoại vi trong script setup.py.',
    reviewedBy: null
  }
];

const INITIAL_EVENTS: LabEvent[] = [
  {
    id: 'evt-001',
    title: 'Seminar: Tối ưu hóa suy luận LLM với TensorRT & vLLM',
    type: 'SEMINAR',
    date: '2026-11-05',
    startTime: '09:30',
    endTime: '11:30',
    location: 'Phòng Hội thảo A2 & Google Meet',
    organizer: 'Lê Chí Hiếu',
    relatedServer: 'track',
    status: 'SCHEDULED',
    description:
      'Chia sẻ kinh nghiệm nén mô hình FP8, PagedAttention và tăng throughput trên NVIDIA A30.'
  },
  {
    id: 'evt-002',
    title: 'Bảo trì định kỳ: Cập nhật Incus 6.0.3 & Kiểm tra NVLink',
    type: 'MAINTENANCE',
    date: '2026-11-07',
    startTime: '23:00',
    endTime: '01:00 (Hôm sau)',
    location: 'Phòng Máy chủ Track (Hà Nội)',
    organizer: 'Hoàng Đức Anh',
    relatedServer: 'track',
    status: 'SCHEDULED',
    description:
      'Bảo trì hệ thống kernel và cập nhật snapshot ZFS. Lưu ý lưu checkpoint các tác vụ đang chạy trước 22:30.'
  },
  {
    id: 'evt-003',
    title: 'Họp Lab hàng tuần & Đánh giá phân bổ GPU Q4',
    type: 'LAB_MEETING',
    date: '2026-11-08',
    startTime: '14:00',
    endTime: '15:30',
    location: 'Phòng họp Lab 304',
    organizer: 'Hoàng Đức Anh',
    relatedServer: 'all',
    status: 'SCHEDULED',
    description:
      'Rà soát hạn ngạch GPU-hours của các nhóm nghiên cứu và duyệt các đề xuất cấp phát độc quyền.'
  },
  {
    id: 'evt-004',
    title: 'Workshop: Huấn luyện phân tán đa GPU với PyTorch FSDP',
    type: 'WORKSHOP',
    date: '2026-11-12',
    startTime: '14:00',
    endTime: '17:00',
    location: 'Phòng máy tính Khu B (TP.HCM)',
    organizer: 'Trần Thị Lan',
    relatedServer: 'workstation',
    status: 'SCHEDULED',
    description:
      'Thực hành cấu hình huấn luyện song song dữ liệu và pipeline parallelism trên container Incus.'
  }
];

const INITIAL_AUDIT_LOGS: AuditLog[] = [
  {
    id: 'log-001',
    actor: 'hieulc (Super Admin)',
    action: 'Cấp phát GPU độc quyền',
    targetType: 'GPU_ALLOCATION',
    targetId: 'TRACK-GPU-02',
    timestamp: '2026-11-03 08:58:20',
    result: 'SUCCESS',
    correlationId: 'tx-8192a01',
    details: 'Cấp phát độc quyền 24GB VRAM cho thành viên namnv (Tác vụ Whisper fine-tuning)'
  },
  {
    id: 'log-002',
    actor: 'anhhd (Lab Manager)',
    action: 'Điều chỉnh hạn ngạch tài nguyên',
    targetType: 'WORKSPACE',
    targetId: 'ws-lanpt',
    timestamp: '2026-11-03 10:14:05',
    result: 'SUCCESS',
    correlationId: 'tx-8192a02',
    details: 'Tăng RAM limit từ 32GB lên 48GB trên Server Workstation (Incus resource update)'
  },
  {
    id: 'log-003',
    actor: 'Hệ thống Bảo mật AI',
    action: 'Cách ly Repository khả nghi',
    targetType: 'SECURITY_SCAN',
    targetId: 'fast-attention-kernel',
    timestamp: '2026-11-03 16:30:12',
    result: 'WARNING',
    correlationId: 'tx-8192a03',
    details: 'Tự động đưa repo vào Sandbox Quarantine do phát hiện outbound socket trong setup.py'
  },
  {
    id: 'log-004',
    actor: 'hieulc (Super Admin)',
    action: 'Khởi tạo Workspace mới',
    targetType: 'WORKSPACE',
    targetId: 'ws-chaunb',
    timestamp: '2026-11-03 17:02:40',
    result: 'SUCCESS',
    correlationId: 'tx-8192a04',
    details: 'Tạo container Incus ubuntu:22.04 cho thành viên chaunb trên Server Workstation'
  },
  {
    id: 'log-005',
    actor: 'anhhd (Lab Manager)',
    action: 'Tạm khóa tài khoản thành viên',
    targetType: 'USER_ACCOUNT',
    targetId: 'huydq',
    timestamp: '2026-11-02 18:20:00',
    result: 'SUCCESS',
    correlationId: 'tx-8192a05',
    details: 'Khóa truy cập SSH do vi phạm chính sách chia sẻ thông tin đăng nhập'
  }
];

const isDemoMode = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';

const PROD_SERVERS: PhysicalServer[] = [
  {
    id: 'track',
    name: 'Server 01 — Track (Từ xa)',
    hostname: 'track.lab.internal',
    location: 'Phòng Lab Hà Nội (Khu A)',
    status: 'offline',
    ipAddress: '10.10.1.10',
    vpnIp: '10.200.0.1',
    uptime: 'Không khả dụng (Mất kết nối)',
    cpuModel: '3x NVIDIA A30 Cluster Node',
    cpuCores: 64,
    cpuUsagePercent: 0.0,
    ramTotalGb: 256,
    ramUsedGb: 0.0,
    ramUsagePercent: 0.0,
    diskTotalTb: 8.0,
    diskUsedTb: 0.0,
    diskUsagePercent: 0.0,
    diskType: 'ZFS RAID-Z2 (Chưa kết nối)',
    networkInMbps: 0,
    networkOutMbps: 0,
    gpuCount: 0,
    activeMembers: 0,
    activeJobs: 0,
    incusVersion: 'Chưa kết nối',
    driverVersion: 'Chưa kết nối',
    cudaVersion: 'Chưa kết nối',
    lastUpdated: 'Mất kết nối (Chưa kết nối WireGuard VPN)'
  },
  {
    id: 'workstation',
    name: 'Server 02 — Workstation (Cục bộ)',
    hostname: 'workstation.lab.local',
    location: 'Phòng Lab TP.HCM (Khu B)',
    status: 'online',
    ipAddress: '127.0.0.1',
    vpnIp: '10.200.0.2',
    uptime: '14 ngày 14 giờ',
    cpuModel: 'Intel(R) Xeon(R) Silver 4210R CPU @ 2.40GHz (40 vCPUs)',
    cpuCores: 20,
    cpuUsagePercent: 15.7,
    ramTotalGb: 125.5,
    ramUsedGb: 49.4,
    ramUsagePercent: 39.4,
    diskTotalTb: 1.79,
    diskUsedTb: 1.15,
    diskUsagePercent: 64.2,
    diskType: 'LVM Ext4 (Primary) + Ext4 (Secondary Data)',
    networkInMbps: 45,
    networkOutMbps: 62,
    gpuCount: 1,
    activeMembers: 0,
    activeJobs: 4,
    incusVersion: 'Chưa cài đặt',
    driverVersion: '595.71.05',
    cudaVersion: '13.2',
    lastUpdated: 'Vừa xong (Live Telemetry)'
  }
];

const PROD_GPUS: PhysicalGpu[] = [
  {
    id: 'ws-gpu-01',
    displayLabel: 'WS-GPU-01',
    serverId: 'workstation',
    serverName: 'Server 02 — Workstation',
    model: 'NVIDIA A30 24GB HBM2',
    pciBusId: '00000000:D8:00.0',
    uuid: 'GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc',
    vramTotalMb: 24576,
    vramUsedMb: 0,
    gpuUtilPercent: 0,
    tempCelsius: 0,
    powerWatts: 0,
    powerLimitWatts: 165,
    allocationMode: 'SHARED',
    enforcementState: 'DRIVER_ENFORCED_VERIFIED',
    activeProcesses: [],
    activeUsers: [],
    associatedJobs: []
  }
];

const PROD_STORAGE_POOLS: StoragePool[] = [
  {
    id: 'pool-root',
    serverId: 'workstation',
    serverName: 'Server 02 — Workstation (Cục bộ)',
    name: 'pool-root',
    label: 'Ổ đĩa Hệ thống (LVM Ext4 /)',
    diskIdentifier: '/dev/mapper/ubuntu--vg-ubuntu--lv',
    mountPoint: '/',
    driver: 'dir',
    capacityGb: 876.1,
    usedGb: 747.9,
    freeGb: 83.6,
    healthStatus: 'HEALTHY',
    isDefault: true
  },
  {
    id: 'pool-data-sdb',
    serverId: 'workstation',
    serverName: 'Server 02 — Workstation (Cục bộ)',
    name: 'pool-data-sdb',
    label: 'Ổ cứng Lưu trữ Thứ cấp (/hdd1/hieulc/tmp)',
    diskIdentifier: '/dev/sdb',
    mountPoint: '/hdd1/hieulc/tmp',
    driver: 'dir',
    capacityGb: 915.8,
    usedGb: 402.4,
    freeGb: 466.8,
    healthStatus: 'HEALTHY',
    isDefault: false
  },
  {
    id: 'pool-nvme-fast',
    serverId: 'track',
    serverName: 'Server 01 — Track (Từ xa)',
    name: 'pool-nvme-fast',
    label: 'SSD NVMe — Track (Mất kết nối VPN)',
    diskIdentifier: 'wwn-0x5002538f411b4101',
    mountPoint: '/var/lib/incus/storage-pools/pool-nvme-fast',
    driver: 'zfs',
    capacityGb: 8000.0,
    usedGb: 0.0,
    freeGb: 0.0,
    healthStatus: 'FAULTED',
    isDefault: false
  }
];

const PROD_JOBS: ComputeJob[] = [];

function mapRawJob(j: Record<string, any>): ComputeJob {
  const isGpu = Boolean(
    j.isGpu ??
    j.is_gpu_process ??
    (j.gpuId && j.gpuId !== 'none') ??
    (j.gpu_id && j.gpu_id !== 'none') ??
    Number(j.vramUsedMb ?? j.vram_mb ?? 0) > 0
  );

  return {
    id: String(j.id || `proc-${j.pid}`),
    username: String(j.username || 'unknown'),
    fullName: String(j.fullName || j.full_name || `@${j.username || 'unknown'}`),
    serverId: (j.serverId || j.server_id || 'workstation') as ServerId,
    serverName: String(
      j.serverName ||
        j.server_name ||
        (j.serverId === 'track' ? 'Server 01 — Track' : 'Server 02 — Workstation')
    ),
    workspace: String(j.workspace || `ws-${j.username || 'user'}`),
    pid: Number(j.pid || 0),
    ppid: j.ppid ? Number(j.ppid) : undefined,
    script: String(j.script || j.script_display || j.name || 'python'),
    cmdline: j.cmdline ? String(j.cmdline) : undefined,
    gpuId: isGpu ? String(j.gpuId || j.gpu_id || 'WS-GPU-01') : null,
    cpuPercent: Number(j.cpuPercent ?? j.cpuUsagePercent ?? j.cpu_percent ?? 0),
    ramUsedMb: Number(j.ramUsedMb ?? j.ramUsageMb ?? j.memory_rss_mb ?? 0),
    vramUsedMb: Number(j.vramUsedMb ?? j.vram_mb ?? 0),
    startTime: String(j.startTime || j.start_time || 'Vừa xong'),
    elapsedTime: String(j.elapsedTime || j.elapsed_time || '00m 00s'),
    status: (j.status || 'RUNNING') as JobStatus,
    sshStatus: String(
      j.sshStatus ||
        j.ssh_status ||
        (j.username in { tungtt: 1, nhannv: 1 } ? 'SSH Online' : 'Chạy nền (Không SSH)')
    ),
    containerState: String(
      j.containerState || j.container_state || (j.container_id ? 'Running' : 'Trực tiếp trên Host')
    ),
    isGpu
  };
}

function mapRawSshSession(s: Record<string, any>): RealSshSession {
  return {
    id: String(s.id || `ssh-sess-${s.username}-${s.terminal || 'pts'}`),
    username: String(s.username || 'unknown'),
    terminal: String(s.terminal || 'pts/?'),
    sourceIp: String(s.source_ip || s.sourceIp || '127.0.0.1'),
    startedAt: String(s.started_at || s.startedAt || 'Vừa xong'),
    sessionDuration: String(s.session_duration || s.sessionDuration || '00m 00s'),
    serverId: (s.server_id || s.serverId || 'workstation') as ServerId,
    serverName: String(s.server_name || s.serverName || 'Server 02 — Workstation'),
    workspace: String(s.workspace || `ws-${s.username || 'user'}`),
    status: String(s.status || 'ACTIVE')
  };
}

function mapRawServer(s: Record<string, any>): PhysicalServer {
  return {
    id: s.id,
    name: s.name,
    hostname: s.hostname,
    location: s.location,
    status: s.status,
    ipAddress: s.ipAddress ?? s.ip_address ?? '',
    vpnIp: s.vpnIp ?? s.vpn_ip ?? '',
    uptime: s.uptime ?? '',
    cpuModel: s.cpuModel ?? s.cpu_model ?? '',
    cpuCores: Number(s.cpuCores ?? s.cpu_cores ?? 0),
    cpuUsagePercent: Number(s.cpuUsagePercent ?? s.cpu_usage_percent ?? 0),
    ramTotalGb: Number(s.ramTotalGb ?? s.ram_total_gb ?? 0),
    ramUsedGb: Number(s.ramUsedGb ?? s.ram_used_gb ?? 0),
    ramUsagePercent: Number(s.ramUsagePercent ?? s.ram_usage_percent ?? 0),
    diskTotalTb: Number(s.diskTotalTb ?? s.disk_total_tb ?? 0),
    diskUsedTb: Number(s.diskUsedTb ?? s.disk_used_tb ?? 0),
    diskUsagePercent: Number(s.diskUsagePercent ?? s.disk_usage_percent ?? 0),
    diskType: s.diskType ?? s.disk_type ?? '',
    networkInMbps: Number(s.networkInMbps ?? s.network_in_mbps ?? 0),
    networkOutMbps: Number(s.networkOutMbps ?? s.network_out_mbps ?? 0),
    gpuCount: Number(s.gpuCount ?? s.gpu_count ?? 0),
    activeMembers: Number(s.activeMembers ?? s.active_members ?? 0),
    activeJobs: Number(s.activeJobs ?? s.active_jobs ?? 0),
    incusVersion: s.incusVersion ?? s.incus_version ?? '',
    driverVersion: s.driverVersion ?? s.driver_version ?? '',
    cudaVersion: s.cudaVersion ?? s.cuda_version ?? '',
    lastUpdated: s.lastUpdated ?? s.last_updated ?? ''
  };
}

function mapRawGpu(g: Record<string, any>): PhysicalGpu {
  return {
    id: g.id,
    displayLabel: g.displayLabel ?? g.display_label ?? g.id,
    serverId: g.serverId ?? g.server_id,
    serverName: g.serverName ?? g.server_name ?? '',
    model: g.model ?? '',
    pciBusId: g.pciBusId ?? g.pci_bus_id ?? '',
    uuid: g.uuid ?? '',
    vramTotalMb: Number(g.vramTotalMb ?? g.vram_total_mb ?? 0),
    vramUsedMb: Number(g.vramUsedMb ?? g.vram_used_mb ?? 0),
    gpuUtilPercent: Number(g.gpuUtilPercent ?? g.gpu_util_percent ?? 0),
    tempCelsius: Number(g.tempCelsius ?? g.temp_celsius ?? 0),
    powerWatts: Number(g.powerWatts ?? g.power_watts ?? 0),
    powerLimitWatts: Number(g.powerLimitWatts ?? g.power_limit_watts ?? 0),
    allocationMode: g.allocationMode ?? g.allocation_mode ?? 'SHARED',
    enforcementState: g.enforcementState ?? g.enforcement_state ?? 'MONITOR_ONLY',
    activeProcesses: Array.isArray(g.activeProcesses ?? g.active_processes)
      ? (g.activeProcesses ?? g.active_processes).map((p: Record<string, any>) => ({
          pid: Number(p.pid),
          name: String(p.name || ''),
          username: String(p.username || ''),
          workspace: String(p.workspace || ''),
          vramMb: Number(p.vramMb ?? p.vram_mb ?? 0),
          cpuPercent: Number(p.cpuPercent ?? p.cpu_percent ?? 0)
        }))
      : [],
    activeUsers: g.activeUsers ?? g.active_users ?? [],
    associatedJobs: g.associatedJobs ?? g.associated_jobs ?? []
  };
}

export const useLabOpsStore = create<LabOpsState>((set, get) => ({
  servers: isDemoMode ? INITIAL_SERVERS : PROD_SERVERS,
  gpus: isDemoMode ? INITIAL_GPUS : PROD_GPUS,
  members: INITIAL_MEMBERS,
  jobs: isDemoMode ? INITIAL_JOBS : PROD_JOBS,
  activeSshSessions: [],
  lastSyncTime: null,
  syncError: null,
  isLivePolling: true,
  securityReviews: INITIAL_SECURITY_REVIEWS,
  events: INITIAL_EVENTS,
  auditLogs: INITIAL_AUDIT_LOGS,
  storagePools: isDemoMode ? INITIAL_STORAGE_POOLS : PROD_STORAGE_POOLS,
  sshEndpoints: INITIAL_SSH_ENDPOINTS,
  storageAllocations: INITIAL_STORAGE_ALLOCATIONS,
  sshPolicies: INITIAL_SSH_POLICIES,
  sshLoginEvents: INITIAL_SSH_LOGIN_EVENTS,

  setLivePolling: (enabled: boolean) => set({ isLivePolling: enabled }),

  fetchLiveJobs: async () => {
    try {
      const res = await fetch('/api/v1/jobs/active').catch(() => null);
      if (res && res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          set({
            jobs: data.map(mapRawJob),
            lastSyncTime: new Date().toLocaleTimeString('vi-VN'),
            syncError: null
          });
        }
      } else {
        set({ syncError: 'Không thể tải danh sách tiến trình từ API' });
      }
    } catch (err: any) {
      set({ syncError: err?.message || 'Lỗi kết nối khi đồng bộ tiến trình' });
    }
  },

  fetchLiveSshSessions: async () => {
    try {
      const res = await fetch('/api/v1/servers/workstation/ssh-sessions').catch(() => null);
      if (res && res.ok) {
        const data = await res.json();
        const obs = data.actual_observations || [];
        set({ activeSshSessions: obs.map(mapRawSshSession) });
      }
    } catch {
      // In offline or static environments, retain current verified state
    }
  },

  fetchLiveState: async () => {
    try {
      const [resServers, resGpus, resJobs, resSsh] = await Promise.all([
        fetch('/api/v1/servers').catch(() => null),
        fetch('/api/v1/gpus').catch(() => null),
        fetch('/api/v1/jobs/active').catch(() => null),
        fetch('/api/v1/servers/workstation/ssh-sessions').catch(() => null)
      ]);
      const updates: Partial<LabOpsState> = {
        lastSyncTime: new Date().toLocaleTimeString('vi-VN'),
        syncError: null
      };

      if (resServers && resServers.ok) {
        const data = await resServers.json();
        if (Array.isArray(data) && data.length > 0) {
          updates.servers = data.map(mapRawServer);
        }
      }
      if (resGpus && resGpus.ok) {
        const data = await resGpus.json();
        if (Array.isArray(data) && data.length > 0) {
          updates.gpus = data.map(mapRawGpu);
        }
      }
      if (resJobs && resJobs.ok) {
        const data = await resJobs.json();
        if (Array.isArray(data)) {
          updates.jobs = data.map(mapRawJob);
        }
      }
      if (resSsh && resSsh.ok) {
        const data = await resSsh.json();
        const obs = data.actual_observations || [];
        updates.activeSshSessions = obs.map(mapRawSshSession);
      }
      set(updates);
    } catch (err: any) {
      set({ syncError: err?.message || 'Mất kết nối tới máy chủ giám sát' });
    }
  },

  getSummary: () => {
    const { servers, gpus, members, jobs } = get();
    const onlineServers = servers.filter((s) => s.status === 'online').length;
    const offlineServers = servers.filter((s) => s.status === 'offline').length;

    const activeGpus = gpus.filter(
      (g) =>
        (g.vramUsedMb || 0) > 512 ||
        (g.gpuUtilPercent || 0) > 0 ||
        (g.activeProcesses?.length || 0) > 0
    ).length;
    const usedVramGb = Math.round(gpus.reduce((acc, g) => acc + g.vramUsedMb, 0) / 1024);

    const onlineMembers = members.filter((m) => m.sshStatus === 'ONLINE').length;
    const totalSessions = members.reduce((acc, m) => acc + m.sshSessionsCount, 0);

    const runningJobs = jobs.filter((j) => j.status === 'RUNNING').length;
    const pendingJobs = jobs.filter((j) => j.status === 'PENDING').length;
    const failedJobs = jobs.filter((j) => j.status === 'FAILED').length;

    return {
      servers: {
        total: servers.length,
        online: onlineServers,
        offline: offlineServers
      },
      gpus: {
        totalCount: gpus.length,
        model: 'NVIDIA A30 24GB',
        aggregateVramGb: 96,
        activeCount: activeGpus,
        usedVramGb
      },
      members: {
        onlineSsh: onlineMembers,
        activeSessions: totalSessions,
        totalRegistered: members.length
      },
      jobs: {
        running: runningJobs,
        pending: pendingJobs,
        failed: failedJobs
      },
      gpuHours: {
        today: 58.4,
        last7Days: 412.8,
        previousPeriodChangePercent: 14.2
      },
      alerts: {
        critical: 0,
        warnings: 2,
        unresolvedIncidents: 0
      }
    };
  },

  addMember: (memberData) => {
    const newId = `mem-${String(Date.now()).slice(-4)}`;
    const newMember: LabMember = {
      ...memberData,
      id: newId,
      activeJobsCount: 0,
      totalGpuHours: 0,
      createdAt: new Date().toISOString().split('T')[0]
    };

    const newEndpoint: SshEndpoint = {
      id: `ep-${Date.now()}`,
      memberId: newId,
      serverId: newMember.assignedServer,
      listenAddress: newMember.sshIp,
      listenPort: newMember.sshPort,
      containerPort: 22,
      protocol: 'tcp',
      status: newMember.status === 'ACTIVE' ? 'ACTIVE' : 'DISABLED'
    };

    const newAllocation: StorageAllocation = {
      id: `sa-${Date.now()}`,
      memberId: newId,
      poolId: newMember.storagePoolId,
      volumeName: `data-${newMember.username}`,
      mountPath: newMember.mountPath || '/workspace',
      quotaGb: newMember.storageQuotaGb,
      usedGb: newMember.storageUsedGb || 0,
      readOnly: false,
      backupPolicy: newMember.backupPolicy || 'DAILY_SNAPSHOT',
      sharedDatasets: newMember.sharedDatasets || ['/shared/datasets'],
      createdAt: new Date().toISOString().split('T')[0]
    };

    const newPolicy: SshAccountPolicy = {
      workspaceId: `ws-${newMember.username}`,
      linuxUsername: newMember.username,
      authenticationMode: 'PUBLIC_KEY_AND_PASSWORD',
      passwordStatus: newMember.passwordStatus || 'ACTIVE',
      keyEnrollmentStatus:
        newMember.sshKeyStatus === 'ACTIVE' ? 'ACTIVE' : 'PENDING_KEY_ENROLLMENT',
      accessStatus: 'ACTIVE',
      minPasswordLength: 12,
      failedLoginAttempts: 0,
      updatedAt: new Date().toISOString()
    };

    set((state) => ({
      members: [newMember, ...state.members],
      sshEndpoints: [newEndpoint, ...state.sshEndpoints],
      storageAllocations: [newAllocation, ...state.storageAllocations],
      sshPolicies: [newPolicy, ...state.sshPolicies],
      auditLogs: [
        {
          id: `log-${Date.now()}`,
          actor: 'Quản trị viên (Demo)',
          action: 'Thêm thành viên mới',
          targetType: 'MEMBER',
          targetId: newMember.username,
          timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
          result: 'SUCCESS',
          correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
          details: `Đã tạo tài khoản cho ${newMember.fullName} (@${newMember.username}) trên ${newMember.assignedServer}, Cổng SSH ${newMember.sshPort}, Dung lượng ${newMember.storageQuotaGb}GB ZFS`
        },
        ...state.auditLogs
      ]
    }));
  },

  updateMemberStatus: (id, status) => {
    set((state) => {
      const member = state.members.find((m) => m.id === id);
      const updatedMembers = state.members.map((m) =>
        m.id === id
          ? { ...m, status, sshStatus: status === 'SUSPENDED' ? ('OFFLINE' as const) : m.sshStatus }
          : m
      );

      const auditAction = status === 'SUSPENDED' ? 'Tạm khóa tài khoản' : 'Khôi phục tài khoản';
      return {
        members: updatedMembers,
        auditLogs: [
          {
            id: `log-${Date.now()}`,
            actor: 'Quản trị viên (Demo)',
            action: auditAction,
            targetType: 'MEMBER',
            targetId: member?.username || id,
            timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
            result: 'SUCCESS',
            correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
            details: `Trạng thái tài khoản chuyển sang ${status}`
          },
          ...state.auditLogs
        ]
      };
    });
  },

  updateMemberQuotas: (id, quotas) => {
    set((state) => ({
      members: state.members.map((m) => (m.id === id ? { ...m, ...quotas } : m))
    }));
  },

  deleteMember: (id) => {
    set((state) => {
      const member = state.members.find((m) => m.id === id);
      return {
        members: state.members.filter((m) => m.id !== id),
        sshEndpoints: state.sshEndpoints.filter((ep) => ep.memberId !== id),
        storageAllocations: state.storageAllocations.filter((sa) => sa.memberId !== id),
        auditLogs: [
          {
            id: `log-${Date.now()}`,
            actor: 'Quản trị viên (Demo)',
            action: 'Xóa thành viên',
            targetType: 'MEMBER',
            targetId: member?.username || id,
            timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
            result: 'SUCCESS',
            correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
            details: `Đã xóa thành viên @${member?.username || id} khỏi hệ thống`
          },
          ...state.auditLogs
        ]
      };
    });
  },

  deleteMemberPermanently: (id) => {
    set((state) => {
      const member = state.members.find((m) => m.id === id);
      return {
        members: state.members.filter((m) => m.id !== id),
        sshEndpoints: state.sshEndpoints.filter((ep) => ep.memberId !== id),
        storageAllocations: state.storageAllocations.filter((sa) => sa.memberId !== id),
        auditLogs: [
          {
            id: `log-${Date.now()}`,
            actor: 'Quản trị viên (Demo)',
            action: 'Hủy vĩnh viễn thành viên & Workspace',
            targetType: 'MEMBER',
            targetId: member?.username || id,
            timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
            result: 'SUCCESS',
            correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
            details: `Đã hủy container ${member?.workspaceName}, xóa ZFS volume và thu hồi cổng SSH ${member?.sshPort}`
          },
          ...state.auditLogs
        ]
      };
    });
  },

  rotateSshKey: (id, newPublicKey, newFingerprint) => {
    set((state) => {
      const member = state.members.find((m) => m.id === id);
      const updatedMembers = state.members.map((m) =>
        m.id === id
          ? {
              ...m,
              sshPublicKey: newPublicKey,
              sshFingerprint: newFingerprint,
              sshPublicKeyPreview: newPublicKey.slice(0, 30) + '...',
              sshKeyStatus: 'ACTIVE' as const
            }
          : m
      );

      return {
        members: updatedMembers,
        auditLogs: [
          {
            id: `log-${Date.now()}`,
            actor: 'Quản trị viên (Demo)',
            action: 'Cập nhật/Đổi khóa SSH (Key Rotation)',
            targetType: 'SSH_KEY',
            targetId: member?.username || id,
            timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
            result: 'SUCCESS',
            correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
            details: `Đã cập nhật khóa SSH Ed25519 mới, fingerprint: ${newFingerprint}`
          },
          ...state.auditLogs
        ]
      };
    });
  },

  revokeSshKey: (id) => {
    set((state) => {
      const member = state.members.find((m) => m.id === id);
      const updatedMembers = state.members.map((m) =>
        m.id === id
          ? {
              ...m,
              sshKeyStatus: 'REVOKED' as const,
              sshStatus: 'OFFLINE' as const,
              sshPublicKeyPreview: '(Khóa đã bị thu hồi)'
            }
          : m
      );

      return {
        members: updatedMembers,
        auditLogs: [
          {
            id: `log-${Date.now()}`,
            actor: 'Quản trị viên (Demo)',
            action: 'Thu hồi khóa SSH',
            targetType: 'SSH_KEY',
            targetId: member?.username || id,
            timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
            result: 'WARNING',
            correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
            details: `Đã thu hồi và xóa khóa khỏi authorized_keys của @${member?.username}`
          },
          ...state.auditLogs
        ]
      };
    });
  },

  resetMemberSshPassword: (id, initialPassword, requireChange = true) => {
    const state = get();
    const member = state.members.find((m) => m.id === id);
    if (!member) return { success: false, message: 'Không tìm thấy thành viên' };

    const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*';
    let tempPass = initialPassword;
    if (!tempPass || tempPass.length < 12) {
      tempPass = Array.from(
        { length: 16 },
        () => chars[Math.floor(Math.random() * chars.length)]
      ).join('');
    }

    set((s) => ({
      members: s.members.map((m) =>
        m.id === id
          ? {
              ...m,
              passwordStatus: requireChange ? 'CHANGE_REQUIRED' : 'ACTIVE',
              failedLoginAttempts: 0
            }
          : m
      ),
      sshPolicies: s.sshPolicies.map((p) =>
        p.linuxUsername === member.username
          ? {
              ...p,
              passwordStatus: requireChange ? 'CHANGE_REQUIRED' : 'ACTIVE',
              failedLoginAttempts: 0,
              updatedAt: new Date().toISOString()
            }
          : p
      ),
      auditLogs: [
        {
          id: `log-${Date.now()}`,
          actor: 'Quản trị viên (Demo)',
          action: 'Đặt lại Mật khẩu Linux SSH',
          targetType: 'SSH_PASSWORD',
          targetId: member.username,
          timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
          result: 'SUCCESS',
          correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
          details: `Đã thiết lập mật khẩu PAM an toàn mới (${tempPass.length} ký tự, PAM shadow hash cập nhật)`
        },
        ...s.auditLogs
      ]
    }));

    return {
      success: true,
      tempPassword: tempPass,
      message: 'Đặt lại mật khẩu Linux SSH thành công'
    };
  },

  requirePasswordChange: (id) => {
    const state = get();
    const member = state.members.find((m) => m.id === id);
    if (!member) return;
    set((s) => ({
      members: s.members.map((m) =>
        m.id === id ? { ...m, passwordStatus: 'CHANGE_REQUIRED' } : m
      ),
      sshPolicies: s.sshPolicies.map((p) =>
        p.linuxUsername === member.username ? { ...p, passwordStatus: 'CHANGE_REQUIRED' } : p
      )
    }));
  },

  suspendSshAccess: (id, reason = 'Quản trị viên tạm khóa truy cập') => {
    const state = get();
    const member = state.members.find((m) => m.id === id);
    if (!member) return;
    set((s) => ({
      members: s.members.map((m) =>
        m.id === id ? { ...m, sshStatus: 'OFFLINE', status: 'SUSPENDED' } : m
      ),
      sshPolicies: s.sshPolicies.map((p) =>
        p.linuxUsername === member.username ? { ...p, accessStatus: 'SUSPENDED' } : p
      ),
      sshEndpoints: s.sshEndpoints.map((ep) =>
        ep.memberId === id ? { ...ep, status: 'DISABLED' } : ep
      ),
      auditLogs: [
        {
          id: `log-${Date.now()}`,
          actor: 'Quản trị viên (Demo)',
          action: 'Khóa truy cập SSH (Suspend SSH)',
          targetType: 'SSH_ACCESS',
          targetId: member.username,
          timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
          result: 'WARNING',
          correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
          details: `Đã vô hiệu hóa cổng SSH proxy container và khóa tài khoản: ${reason}`
        },
        ...s.auditLogs
      ]
    }));
  },

  restoreSshAccess: (id) => {
    const state = get();
    const member = state.members.find((m) => m.id === id);
    if (!member) return;
    set((s) => ({
      members: s.members.map((m) => (m.id === id ? { ...m, status: 'ACTIVE' } : m)),
      sshPolicies: s.sshPolicies.map((p) =>
        p.linuxUsername === member.username
          ? { ...p, accessStatus: 'ACTIVE', failedLoginAttempts: 0 }
          : p
      ),
      sshEndpoints: s.sshEndpoints.map((ep) =>
        ep.memberId === id ? { ...ep, status: 'ACTIVE' } : ep
      ),
      auditLogs: [
        {
          id: `log-${Date.now()}`,
          actor: 'Quản trị viên (Demo)',
          action: 'Khôi phục truy cập SSH (Restore SSH)',
          targetType: 'SSH_ACCESS',
          targetId: member.username,
          timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
          result: 'SUCCESS',
          correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
          details: `Đã mở khóa cổng SSH proxy và khôi phục quyền truy cập hai lớp (Dual-Auth)`
        },
        ...s.auditLogs
      ]
    }));
  },

  getMemberLoginEvents: (username) => {
    return get().sshLoginEvents.filter((e) => e.linuxUsername === username);
  },

  changeStorageQuota: (id, newQuotaGb) => {
    const state = get();
    const member = state.members.find((m) => m.id === id);
    if (!member) return { success: false, message: 'Không tìm thấy thành viên' };

    const pool = state.storagePools.find((p) => p.id === member.storagePoolId);
    if (pool) {
      const quotaDiff = newQuotaGb - member.storageQuotaGb;
      if (quotaDiff > pool.freeGb) {
        return {
          success: false,
          message: `Storage Pool chỉ còn trống ${pool.freeGb} GB, không đủ cấp phát thêm ${quotaDiff} GB!`
        };
      }
    }

    set((s) => ({
      members: s.members.map((m) => (m.id === id ? { ...m, storageQuotaGb: newQuotaGb } : m)),
      storageAllocations: s.storageAllocations.map((sa) =>
        sa.memberId === id ? { ...sa, quotaGb: newQuotaGb } : sa
      ),
      auditLogs: [
        {
          id: `log-${Date.now()}`,
          actor: 'Quản trị viên (Demo)',
          action: 'Điều chỉnh Hạn ngạch Ổ đĩa',
          targetType: 'STORAGE',
          targetId: member.username,
          timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
          result: 'SUCCESS',
          correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
          details: `Thay đổi quota ZFS của @${member.username} từ ${member.storageQuotaGb}GB lên ${newQuotaGb}GB`
        },
        ...s.auditLogs
      ]
    }));

    return { success: true, message: `Đã cập nhật hạn ngạch thành ${newQuotaGb} GB thành công` };
  },

  changeSshPort: (id, newPort) => {
    const state = get();
    const member = state.members.find((m) => m.id === id);
    if (!member) return { success: false, message: 'Không tìm thấy thành viên' };

    if (newPort < 22000 || newPort > 29999) {
      return { success: false, message: 'Cổng SSH phải nằm trong dải hợp lệ 22000 - 29999' };
    }

    const collision = state.sshEndpoints.find(
      (ep) =>
        ep.serverId === member.assignedServer && ep.listenPort === newPort && ep.memberId !== id
    );

    if (collision) {
      return {
        success: false,
        message: `Cổng ${newPort} đã được sử dụng bởi thành viên khác trên máy chủ ${member.assignedServer}!`
      };
    }

    set((s) => ({
      members: s.members.map((m) => (m.id === id ? { ...m, sshPort: newPort } : m)),
      sshEndpoints: s.sshEndpoints.map((ep) =>
        ep.memberId === id ? { ...ep, listenPort: newPort } : ep
      ),
      auditLogs: [
        {
          id: `log-${Date.now()}`,
          actor: 'Quản trị viên (Demo)',
          action: 'Đổi Cổng SSH Endpoint',
          targetType: 'NETWORK',
          targetId: member.username,
          timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
          result: 'SUCCESS',
          correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
          details: `Đã chuyển cổng SSH Incus proxy từ ${member.sshPort} sang ${newPort} cho @${member.username}`
        },
        ...s.auditLogs
      ]
    }));

    return { success: true, message: `Đã chuyển sang cổng SSH ${newPort} thành công` };
  },

  archiveWorkspace: (id) => {
    set((state) => {
      const member = state.members.find((m) => m.id === id);
      const updatedMembers = state.members.map((m) =>
        m.id === id
          ? {
              ...m,
              status: 'ARCHIVED' as const,
              sshStatus: 'OFFLINE' as const
            }
          : m
      );

      return {
        members: updatedMembers,
        sshEndpoints: state.sshEndpoints.map((ep) =>
          ep.memberId === id ? { ...ep, status: 'DISABLED' as const } : ep
        ),
        auditLogs: [
          {
            id: `log-${Date.now()}`,
            actor: 'Quản trị viên (Demo)',
            action: 'Lưu trữ Workspace (Archive)',
            targetType: 'WORKSPACE',
            targetId: member?.username || id,
            timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
            result: 'SUCCESS',
            correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
            details: `Đã dừng container ${member?.workspaceName}, ngắt kết nối SSH và lưu trữ volume ZFS`
          },
          ...state.auditLogs
        ]
      };
    });
  },

  allocateGpu: ({ gpuId, username, mode, requestedVramGb, jobName }) => {
    set((state) => {
      const targetGpu = state.gpus.find((g) => g.id === gpuId);
      if (!targetGpu) return state;

      const member = state.members.find((m) => m.username === username);
      const newPid = Math.floor(20000 + Math.random() * 30000);
      const newJobId = `job-${Math.floor(8930 + Math.random() * 100)}`;
      const vramMb = requestedVramGb * 1024;

      const newProcess = {
        pid: newPid,
        name: `python (${jobName})`,
        username,
        workspace: member?.workspaceName || `ws-${username}`,
        vramMb,
        cpuPercent: 25.0
      };

      const updatedGpus = state.gpus.map((g) => {
        if (g.id !== gpuId) return g;
        const newUsedMb = Math.min(g.vramTotalMb, g.vramUsedMb + vramMb);
        return {
          ...g,
          vramUsedMb: newUsedMb,
          gpuUtilPercent: Math.min(100, Math.max(g.gpuUtilPercent, 75)),
          allocationMode: mode,
          activeProcesses: [...g.activeProcesses, newProcess],
          activeUsers: Array.from(new Set([...g.activeUsers, username])),
          associatedJobs: [...g.associatedJobs, newJobId]
        };
      });

      const newJob: ComputeJob = {
        id: newJobId,
        username,
        fullName: member?.fullName || username,
        serverId: targetGpu.serverId,
        workspace: member?.workspaceName || `ws-${username}`,
        pid: newPid,
        script: `python ${jobName}`,
        gpuId: targetGpu.displayLabel,
        cpuPercent: 25.0,
        ramUsedMb: 12288,
        vramUsedMb: vramMb,
        startTime: 'Vừa xong',
        elapsedTime: '01m',
        status: 'RUNNING',
        sshStatus: 'SSH Online',
        containerState: 'Running'
      };

      return {
        gpus: updatedGpus,
        jobs: [newJob, ...state.jobs],
        auditLogs: [
          {
            id: `log-${Date.now()}`,
            actor: 'Quản trị viên (Demo)',
            action: 'Cấp phát GPU',
            targetType: 'GPU',
            targetId: targetGpu.displayLabel,
            timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
            result: 'SUCCESS',
            correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
            details: `Cấp ${requestedVramGb}GB VRAM (${mode}) cho ${username} chạy ${jobName}`
          },
          ...state.auditLogs
        ]
      };
    });
  },

  stopJob: (jobId) => {
    set((state) => {
      const targetJob = state.jobs.find((j) => j.id === jobId);
      if (!targetJob) return state;

      const updatedJobs = state.jobs.map((j) =>
        j.id === jobId
          ? { ...j, status: 'COMPLETED' as const, elapsedTime: `${j.elapsedTime} (Dừng)` }
          : j
      );

      // Free GPU process
      const updatedGpus = state.gpus.map((g) => {
        if (g.displayLabel !== targetJob.gpuId) return g;
        const filteredProc = g.activeProcesses.filter((p) => p.pid !== targetJob.pid);
        const newUsedMb = filteredProc.reduce((acc, p) => acc + p.vramMb, 0);
        return {
          ...g,
          vramUsedMb: newUsedMb,
          gpuUtilPercent: filteredProc.length === 0 ? 0 : Math.max(10, g.gpuUtilPercent - 40),
          activeProcesses: filteredProc,
          activeUsers: Array.from(new Set(filteredProc.map((p) => p.username)))
        };
      });

      return {
        jobs: updatedJobs,
        gpus: updatedGpus,
        auditLogs: [
          {
            id: `log-${Date.now()}`,
            actor: 'Quản trị viên (Demo)',
            action: 'Dừng tiến trình GPU',
            targetType: 'JOB',
            targetId: jobId,
            timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
            result: 'SUCCESS',
            correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
            details: `Dừng PID ${targetJob.pid} (${targetJob.script}) của ${targetJob.username}`
          },
          ...state.auditLogs
        ]
      };
    });
  },

  updateSecurityReview: (id, status, reviewer) => {
    set((state) => ({
      securityReviews: state.securityReviews.map((r) =>
        r.id === id ? { ...r, status, reviewedBy: reviewer } : r
      ),
      auditLogs: [
        {
          id: `log-${Date.now()}`,
          actor: reviewer,
          action: `Duyệt an ninh Repository: ${status}`,
          targetType: 'SECURITY_REVIEW',
          targetId: id,
          timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
          result: status === 'APPROVED' ? 'SUCCESS' : 'WARNING',
          correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`,
          details: `Quyết định duyệt mã nguồn: ${status}`
        },
        ...state.auditLogs
      ]
    }));
  },

  addSecurityReview: (repoUrl, submitter) => {
    const repoParts = repoUrl.replace('https://github.com/', '').split('/');
    const repoName = repoParts.slice(0, 2).join('/') || 'custom-repo';
    const newReview: SecurityReview = {
      id: `sec-${String(Date.now()).slice(-3)}`,
      repoUrl,
      repoName,
      commitSha: Math.random().toString(16).substring(2, 9),
      submittedBy: submitter,
      submittedAt: new Date().toISOString().replace('T', ' ').substring(0, 16),
      status: 'SCANNING',
      riskScore: 12,
      findings: { high: 0, medium: 1, low: 2 },
      summary:
        'Đang tiến hành phân tích mã nguồn và kiểm tra lỗ hổng phụ thuộc trong môi trường sandbox.',
      reviewedBy: null
    };

    set((state) => ({
      securityReviews: [newReview, ...state.securityReviews]
    }));
  },

  addEvent: (eventData) => {
    const newEvent: LabEvent = {
      ...eventData,
      id: `evt-${String(Date.now()).slice(-3)}`
    };
    set((state) => ({
      events: [newEvent, ...state.events]
    }));
  },

  updateEventStatus: (id, status) => {
    set((state) => ({
      events: state.events.map((e) => (e.id === id ? { ...e, status } : e))
    }));
  },

  addAuditLog: (log) => {
    set((state) => ({
      auditLogs: [
        {
          ...log,
          id: `log-${Date.now()}`,
          timestamp: new Date().toISOString().replace('T', ' ').substring(0, 19),
          correlationId: `tx-${Math.random().toString(36).substring(2, 9)}`
        },
        ...state.auditLogs
      ]
    }));
  }
}));
