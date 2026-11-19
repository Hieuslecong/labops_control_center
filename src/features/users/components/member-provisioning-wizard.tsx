'use client';

import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import {
  GpuAllocationMode,
  MemberRole,
  ServerId,
  HostResourceProfile,
  StoragePoolInfo,
  QuotaBackend,
  StorageVerificationStatus,
  WireGuardProfile,
  WireGuardVpnOverview
} from '@/types/labops';
import { toast } from 'sonner';

interface MemberProvisioningWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';

function validUsername(value: string) {
  return (
    /^[a-z_][a-z0-9_-]{2,31}$/.test(value) &&
    !['root', 'admin', 'sshd', 'docker', 'incus', 'sudo', 'wheel'].includes(value)
  );
}

function generateSampleOpenSshPublicKey(username: string): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  // Build standard OpenSSH ssh-ed25519 wire format: [len=11]["ssh-ed25519"][len=32][32 random pubkey bytes]
  const header = [0, 0, 0, 11, 115, 115, 104, 45, 101, 100, 50, 53, 53, 49, 57, 0, 0, 0, 32];
  const combined = new Uint8Array(header.length + bytes.length);
  combined.set(header, 0);
  combined.set(bytes, header.length);
  let binary = '';
  for (let i = 0; i < combined.length; i++) {
    binary += String.fromCharCode(combined[i]);
  }
  const b64 = btoa(binary);
  return `ssh-ed25519 ${b64} ${username || 'researcher'}@labops-client`;
}

function downloadTextFile(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function MemberProvisioningWizard({ open, onOpenChange }: MemberProvisioningWizardProps) {
  const servers = useLabOpsStore((state) => state.servers);
  const gpus = useLabOpsStore((state) => state.gpus);
  const [step, setStep] = useState(1);
  const [fullName, setFullName] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<MemberRole>('RESEARCHER');
  const [researchGroup, setResearchGroup] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [serverId, setServerId] = useState<ServerId>(() => {
    const online = servers.find((s) => s.status === 'online');
    return online ? online.id : 'workstation';
  });
  const [vpnOverview, setVpnOverview] = useState<WireGuardVpnOverview | null>(null);
  const [storagePoolId, setStoragePoolId] = useState('');
  const [hostStoragePools, setHostStoragePools] = useState<StoragePoolInfo[]>([]);
  const [loadingPools, setLoadingPools] = useState(false);
  const [selectedDrive, setSelectedDrive] = useState<StoragePoolInfo | null>(null);
  const [resourceProfile, setResourceProfile] = useState<HostResourceProfile | null>(null);
  const [workspaceName, setWorkspaceName] = useState('');
  const [storageQuotaGb, setStorageQuotaGb] = useState(100);
  const [backupPolicy, setBackupPolicy] = useState<'DAILY_SNAPSHOT' | 'WEEKLY_SYNC' | 'NONE'>(
    'DAILY_SNAPSHOT'
  );
  const [selectedGpu, setSelectedGpu] = useState('NONE');
  const [gpuAllocationMode, setGpuAllocationMode] = useState<GpuAllocationMode>('EXCLUSIVE');
  const [vramQuotaGb, setVramQuotaGb] = useState(24);
  const [sshKeyLabel, setSshKeyLabel] = useState('Admin-Managed Ed25519 Key');
  const [sshPublicKey, setSshPublicKey] = useState('');
  const [autoGenerateSsh, setAutoGenerateSsh] = useState(true);
  const [requirePassphrase, setRequirePassphrase] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [downloadingZip, setDownloadingZip] = useState(false);
  const [claimingBundle, setClaimingBundle] = useState(false);
  const [claimingSecrets, setClaimingSecrets] = useState(false);
  const [claimedBundle, setClaimedBundle] = useState<{
    ssh_host: string;
    ssh_port: number;
    username: string;
    host_alias?: string;
    ssh_command: string;
    ssh_alias_command?: string;
    vpn_guide: string;
    encrypted_private_key: string;
    ssh_config_snippet?: string;
    quick_setup_script?: string;
    wireguard_conf?: string;
    wireguard_client_ip?: string;
    fingerprint: string;
  } | null>(null);
  const [claimedSecrets, setClaimedSecrets] = useState<{
    key_passphrase?: string;
    temporary_password?: string;
    temp_password_expires_at?: string;
  } | null>(null);
  const [created, setCreated] = useState<{
    id: string;
    username: string;
    assignedServer?: ServerId;
    status: string;
    provisioningState: string;
    sshPort?: number | null;
    sshIp?: string | null;
    sshLoginUser?: string | null;
    sshFingerprint?: string | null;
    passwordStatus?: string;
    wireguard?: WireGuardProfile | null;
    runtimeVerification?: {
      host_online?: boolean;
      directory_isolation?: string;
      ssh_key_installed?: string;
      privilege_isolation?: string;
      cpu_ram_limits?: string;
      storage_quota?: string;
      ssh_runtime?: string;
      overall_status?: string;
      reasons?: string[];
    };
    oneTimePackage?: {
      key_bundle_available?: boolean;
      channel_secrets_available?: boolean;
    };
  } | null>(null);

  const resetWizard = () => {
    setStep(1);
    setFullName('');
    setUsername('');
    setEmail('');
    setResearchGroup('');
    setExpiresAt('');
    setWorkspaceName('');
    setSelectedGpu('NONE');
    setSshPublicKey('');
    setAutoGenerateSsh(true);
    setRequirePassphrase(false);
    setClaimedBundle(null);
    setClaimedSecrets(null);
    setCreated(null);
    const online = servers.find((s) => s.status === 'online');
    if (online) {
      setServerId(online.id);
    }
  };

  useEffect(() => {
    if (!open || DEMO_MODE) return;
    let active = true;
    if (step === 1 && !created && servers.length > 0) {
      const current = servers.find((s) => s.id === serverId);
      const online = servers.find((s) => s.status === 'online');
      if (online && (!current || current.status !== 'online')) {
        Promise.resolve().then(() => {
          if (active) setServerId(online.id);
        });
      }
    }
    fetch('/api/labops/v2/vpn/overview', { cache: 'no-store' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (active && data) {
          setVpnOverview(data as WireGuardVpnOverview);
        }
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [open, step, created, servers, serverId]);

  const usernameIsValid = validUsername(username);
  const effectiveEmail = email.trim()
    ? email.includes('@')
      ? email.trim()
      : `${email.trim()}@lab.ai.vn`
    : username
      ? `${username}@lab.ai.vn`
      : '';
  const assignedGpus = gpus.filter((gpu) => gpu.serverId === serverId);

  useEffect(() => {
    let isMounted = true;
    Promise.resolve().then(() => {
      if (isMounted) {
        setLoadingPools(true);
        setSelectedDrive(null);
      }
    });

    fetch(`/api/labops/storage-pools?host_id=${serverId}`)
      .then((res) => (res.ok ? res.json() : []))
      .then((data) => {
        if (!isMounted) return;
        if (Array.isArray(data)) {
          const pools: StoragePoolInfo[] = data.map((item) => {
            const d = item as Record<string, unknown>;
            const rawReason = d.ineligibilityReason || d.ineligibility_reason;
            return {
              hostId: (d.hostId || d.host_id || serverId) as ServerId,
              storageId: String(d.storageId || d.storage_id || d.id || ''),
              label: String(d.label || d.name || d.mountPoint || d.mount_point || ''),
              isDefault: Boolean(d.isDefault ?? d.is_default ?? false),
              device: String(d.device || d.diskIdentifier || d.disk_identifier || ''),
              filesystemUuid: String(d.filesystemUuid || d.filesystem_uuid || ''),
              mountPoint: String(d.mountPoint || d.mount_point || ''),
              filesystem: String(d.filesystem || d.driver || 'ext4'),
              capacityGib: Number(d.capacityGib ?? d.capacity_gib ?? d.capacityGb ?? 0),
              usedGib: Number(d.usedGib ?? d.used_gib ?? d.usedGb ?? 0),
              freeGib: Number(d.freeGib ?? d.free_gib ?? d.freeGb ?? 0),
              allocatableGib: Number(d.allocatableGib ?? d.allocatable_gib ?? d.freeGib ?? 0),
              reservedGib: Number(d.reservedGib ?? d.reserved_gib ?? 0),
              quotaBackend: (d.quotaBackend || d.quota_backend || 'PROJECT_QUOTA') as QuotaBackend,
              quotaSupported: Boolean(d.quotaSupported ?? d.quota_supported ?? true),
              quotaEnforced: Boolean(d.quotaEnforced ?? d.quota_enforced ?? true),
              readOnly: Boolean(d.readOnly ?? d.read_only ?? false),
              mounted: Boolean(d.mounted ?? true),
              eligible: d.eligible !== false,
              ineligibilityReason: rawReason ? String(rawReason) : undefined,
              lastVerifiedAt: String(d.lastVerifiedAt || d.last_verified_at || ''),
              verificationStatus: (d.verificationStatus ||
                d.verification_status ||
                'VERIFIED') as StorageVerificationStatus
            };
          });
          pools.sort(
            (a, b) =>
              Number(b.eligible) - Number(a.eligible) ||
              Number(Boolean(b.isDefault)) - Number(Boolean(a.isDefault)) ||
              b.allocatableGib - a.allocatableGib
          );
          setHostStoragePools(pools);
          const eligible =
            pools.find((p) => p.eligible && p.isDefault) || pools.find((p) => p.eligible);
          if (eligible) {
            setSelectedDrive(eligible);
            setStoragePoolId(eligible.storageId);
          } else {
            setSelectedDrive(null);
            setStoragePoolId('');
          }
        }
      })
      .catch(() => {})
      .finally(() => {
        if (isMounted) setLoadingPools(false);
      });

    fetch(`/api/labops/resources/profile?host_id=${serverId}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (isMounted && data) {
          setResourceProfile({
            hostId: (data.hostId || data.host_id || serverId) as ServerId,
            totalCores: Number(data.totalCores ?? data.total_cores ?? 16),
            cpuSchedulingMode:
              data.cpuSchedulingMode || data.cpu_scheduling_mode || 'SHARED_WEIGHTED',
            defaultCpuWeight: Number(data.defaultCpuWeight ?? data.default_cpu_weight ?? 100),
            pidsLimit: Number(data.pidsLimit ?? data.pids_limit ?? 4096),
            totalRamBytes: Number(
              data.totalRamBytes ?? data.total_ram_bytes ?? 128 * 1024 * 1024 * 1024
            ),
            hostReserveBytes: Number(
              data.hostReserveBytes ?? data.host_reserve_bytes ?? 25.6 * 1024 * 1024 * 1024
            ),
            sharedBudgetBytes: Number(
              data.sharedBudgetBytes ?? data.shared_budget_bytes ?? 102.4 * 1024 * 1024 * 1024
            ),
            memoryProtectionMode:
              data.memoryProtectionMode || data.memory_protection_mode || 'SHARED_HOST_PROTECTED',
            memoryPressure: data.memoryPressure || data.memory_pressure
          });
        }
      })
      .catch(() => {});

    return () => {
      isMounted = false;
    };
  }, [serverId]);

  const createAccessRequest = async () => {
    if (DEMO_MODE) {
      toast.error('User Management đang ở DEMO; không gửi yêu cầu cấp quyền giả.');
      return;
    }
    setSubmitting(true);
    setClaimedBundle(null);
    setClaimedSecrets(null);
    try {
      const response = await fetch('/api/access/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fullName,
          username,
          email: effectiveEmail,
          role,
          researchGroup: researchGroup || 'AI Research Lab',
          assignedServer: serverId,
          storagePoolId: selectedDrive?.storageId || storagePoolId || null,
          storageId: selectedDrive?.storageId || storagePoolId || null,
          hostStorageRoot: selectedDrive?.mountPoint || null,
          memberStorageDirectory: selectedDrive
            ? `${selectedDrive.mountPoint}/labops/users/${username}`
            : null,
          containerMountPath: '/workspace',
          workspaceName: workspaceName || `ws-${username}`,
          mountPath: '/workspace',
          storageQuotaGb,
          backupPolicy,
          cpuPolicy: 'SHARED_WEIGHTED',
          cpuWeight: 100,
          cpuLimitCores: 8,
          memoryPolicy: 'SHARED_HOST_PROTECTED',
          ramLimitGb: 32,
          gpuPermissions: selectedGpu === 'NONE' ? [] : [selectedGpu],
          vramPolicy:
            selectedGpu === 'NONE'
              ? 'None'
              : gpuAllocationMode === 'EXCLUSIVE'
                ? 'Dedicated Full VRAM'
                : `Quota ${vramQuotaGb}GB (Shared)`,
          vramQuotaGb:
            selectedGpu === 'NONE' ? 0 : gpuAllocationMode === 'EXCLUSIVE' ? 24 : vramQuotaGb,
          gpuAllocationMode,
          expiresAt: expiresAt || null,
          provisionSshAccess: autoGenerateSsh,
          requirePassphrase,
          sshPublicKey: !autoGenerateSsh && sshPublicKey.trim() ? sshPublicKey.trim() : undefined,
          sshKeyLabel: sshKeyLabel.trim() || 'Admin-Managed Ed25519 Key'
        })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const msg =
          typeof result.detail === 'string'
            ? result.detail
            : result.detail?.message || `Lỗi từ Backend (${response.status})`;
        throw new Error(msg);
      }
      const rawWg = result.wireguard as Record<string, unknown> | undefined;
      const parsedWg: WireGuardProfile | null = rawWg
        ? {
            clientIp: (rawWg.clientIp || rawWg.client_ip) as string | undefined,
            vpnIp: (rawWg.vpnIp || rawWg.vpn_ip) as string | undefined,
            allowedSubnets: (rawWg.allowedSubnets || rawWg.allowed_subnets) as string | undefined,
            serverEndpoint: (rawWg.serverEndpoint || rawWg.server_endpoint) as string | undefined,
            status: (rawWg.status || 'PENDING_PROVISIONING') as WireGuardProfile['status'],
            registeredOnGateway: (rawWg.registeredOnGateway ?? rawWg.registered_on_gateway) as
              | boolean
              | undefined,
            gatewayError: (rawWg.gatewayError ?? rawWg.gateway_error ?? null) as string | null
          }
        : null;
      setCreated({
        id: result.id,
        username: result.username,
        assignedServer: (result.assignedServer || result.assigned_server || serverId) as ServerId,
        status: result.status,
        provisioningState: result.provisioningState || result.provisioning_state,
        sshPort: result.sshPort ?? result.ssh_port ?? null,
        sshIp: result.sshIp ?? result.ssh_ip ?? null,
        sshLoginUser: result.sshLoginUser ?? result.ssh_login_user ?? null,
        sshFingerprint: result.sshFingerprint ?? result.ssh_fingerprint ?? null,
        passwordStatus: result.passwordStatus ?? result.password_status,
        wireguard: parsedWg,
        runtimeVerification: result.runtimeVerification ?? result.runtime_verification,
        oneTimePackage: result.oneTimePackage ?? result.one_time_package
      });
      setStep(5);
      await useLabOpsStore.getState().fetchAccessMembers();
      toast.success(
        `Đã khởi tạo quyền truy cập SSH cho @${result.username} (Trạng thái runtime: ${result.provisioningState || result.provisioning_state}).`
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Không thể tạo yêu cầu truy cập.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDownloadZip = async () => {
    if (!created?.id) return;
    setDownloadingZip(true);
    try {
      const response = await fetch(
        `/api/labops/v2/ssh-access/${encodeURIComponent(created.id)}/download-zip`,
        { cache: 'no-store' }
      );
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(
          typeof errorData.detail === 'string'
            ? errorData.detail
            : `Không thể tải file ZIP (Mã lỗi ${response.status})`
        );
      }
      const blob = await response.blob();
      const contentDisposition = response.headers.get('Content-Disposition');
      let filename = `LabOps_Access_${created.username}.zip`;
      if (contentDisposition) {
        const match = contentDisposition.match(/filename="?([^";]+)"?/i);
        if (match && match[1]) filename = match[1];
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(`Đã tải thành công bộ kết nối trọn gói: ${filename}`);
      await useLabOpsStore.getState().fetchAccessMembers();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Lỗi tải bộ kết nối ZIP.');
    } finally {
      setDownloadingZip(false);
    }
  };

  const handleClaimKeyBundle = async () => {
    if (!created?.id) return;
    setClaimingBundle(true);
    try {
      const response = await fetch(
        `/api/labops/v2/ssh-access/${encodeURIComponent(created.id)}/claim-key-bundle`,
        {
          method: 'POST',
          cache: 'no-store'
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof data.detail === 'string'
            ? data.detail
            : 'Không thể tải gói khóa riêng (đã tải trước đó).'
        );
      }
      setClaimedBundle(data);
      // 1. Tự động tải file cấu hình WireGuard (.conf) để import luôn vào WireGuard
      if (data.wireguard_conf) {
        downloadTextFile(`${data.username}-labops-wg0.conf`, data.wireguard_conf);
      }
      // 2. Tự động tải script cài đặt 1 chạm cho SSH (tự tạo ~/.ssh/id_ed25519_<username> + ~/.ssh/config)
      if (data.quick_setup_script) {
        downloadTextFile(`setup-ssh-${data.username}.sh`, data.quick_setup_script);
      }
      toast.success(
        `Đã tải file cấu hình WireGuard (${data.username}-labops-wg0.conf) & Script cài đặt SSH tự động (setup-ssh-${data.username}.sh)!`
      );
      await useLabOpsStore.getState().fetchAccessMembers();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Lỗi tải gói kết nối.');
    } finally {
      setClaimingBundle(false);
    }
  };

  const handleClaimSecrets = async () => {
    if (!created?.id) return;
    setClaimingSecrets(true);
    try {
      const response = await fetch(
        `/api/labops/v2/ssh-access/${encodeURIComponent(created.id)}/claim-secrets`,
        {
          method: 'POST',
          cache: 'no-store'
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof data.detail === 'string'
            ? data.detail
            : 'Passphrase & mật khẩu tạm đã được lấy trước đó.'
        );
      }
      setClaimedSecrets(data);
      toast.success(
        'Đã hiển thị Passphrase & Mật khẩu tạm (1 lần duy nhất). Hãy gửi qua kênh riêng!'
      );
      await useLabOpsStore.getState().fetchAccessMembers();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Lỗi nhận thông tin bí mật.');
    } finally {
      setClaimingSecrets(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && created) {
          resetWizard();
        }
        onOpenChange(nextOpen);
      }}
    >
      <DialogContent className='sm:max-w-2xl max-h-[90vh] overflow-y-auto'>
        <DialogHeader>
          <div className='flex items-center justify-between gap-3'>
            <div>
              <DialogTitle>Tạo quyền truy cập SSH cho Thành viên (LabOps V2)</DialogTitle>
              <DialogDescription>
                Admin tạo workspace riêng, hệ thống tự động sinh cặp khóa SSH mã hóa bằng passphrase
                và mật khẩu tạm lần đầu.
              </DialogDescription>
            </div>
            <Badge variant={DEMO_MODE ? 'secondary' : 'outline'}>
              {DEMO_MODE ? 'DEMO' : `${step}/5`}
            </Badge>
          </div>
        </DialogHeader>

        {DEMO_MODE && (
          <Card className='border-amber-500/40 bg-amber-500/5 p-3 text-sm text-amber-900 dark:text-amber-200'>
            DEMO mode: không tạo user thật và không hiển thị trạng thái WireGuard/SSH giả.
          </Card>
        )}

        {step === 1 && (
          <div className='grid gap-4'>
            <div className='grid gap-3 sm:grid-cols-2'>
              <div className='space-y-1.5'>
                <Label htmlFor='access-full-name'>Tên hiển thị</Label>
                <Input
                  id='access-full-name'
                  placeholder='VD: Nguyễn Văn A'
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                />
              </div>
              <div className='space-y-1.5'>
                <Label htmlFor='access-username'>Username Linux</Label>
                <Input
                  id='access-username'
                  autoCapitalize='none'
                  placeholder='VD: nguyenvana'
                  value={username}
                  onChange={(event) => {
                    const value = event.target.value.toLowerCase();
                    setUsername(value);
                    if (!workspaceName || workspaceName === `ws-${username}`)
                      setWorkspaceName(`ws-${value}`);
                  }}
                />
                {username && !usernameIsValid && (
                  <p className='text-xs text-destructive'>
                    Dùng 3–32 ký tự a-z, 0-9, _ hoặc -, không dùng tên hệ thống.
                  </p>
                )}
              </div>
            </div>
            <div className='grid gap-3 sm:grid-cols-2'>
              <div className='space-y-1.5'>
                <Label htmlFor='access-email'>Email thành viên</Label>
                <Input
                  id='access-email'
                  type='text'
                  placeholder='vana@lab.ai.vn (hoặc vana)'
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
                {email.trim() && !email.includes('@') && (
                  <p className='text-xs text-muted-foreground'>
                    Tự động bổ sung tên miền: <code>{effectiveEmail}</code>
                  </p>
                )}
              </div>
              <div className='space-y-1.5'>
                <Label htmlFor='access-group'>Nhóm nghiên cứu</Label>
                <Input
                  id='access-group'
                  placeholder='Computer Vision / LLM'
                  value={researchGroup}
                  onChange={(event) => setResearchGroup(event.target.value)}
                />
              </div>
            </div>
            <div className='space-y-1.5'>
              <Label>Vai trò</Label>
              <div className='grid grid-cols-2 gap-2'>
                {(['RESEARCHER', 'ADMIN'] as const).map((value) => (
                  <button
                    key={value}
                    type='button'
                    onClick={() => setRole(value)}
                    className={`rounded-md border p-3 text-left text-sm ${role === value ? 'border-primary bg-primary/10' : 'border-border'}`}
                  >
                    <span className='font-semibold'>
                      {value === 'ADMIN' ? 'Quản trị viên' : 'Nghiên cứu viên'}
                    </span>
                    <span className='mt-1 block text-xs text-muted-foreground'>
                      {value === 'ADMIN'
                        ? 'Quản trị hạ tầng và phê duyệt bảo mật'
                        : 'Truy cập SSH vào Workspace cô lập được cấp'}
                    </span>
                  </button>
                ))}
              </div>
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='access-expiry'>Ngày hết hạn (tùy chọn)</Label>
              <Input
                id='access-expiry'
                type='date'
                value={expiresAt}
                onChange={(event) => setExpiresAt(event.target.value)}
              />
            </div>
          </div>
        )}

        {step === 2 && (
          <div className='grid gap-4'>
            <div className='space-y-1.5'>
              <Label>Máy chủ vật lý</Label>
              <div className='grid gap-2 sm:grid-cols-2'>
                {servers.map((server) => (
                  <button
                    key={server.id}
                    type='button'
                    onClick={() => {
                      setServerId(server.id);
                      setSelectedDrive(null);
                      setStoragePoolId('');
                      setSelectedGpu('NONE');
                    }}
                    className={`rounded-md border p-3 text-left transition-all ${
                      serverId === server.id
                        ? 'border-primary bg-primary/10 shadow-sm'
                        : 'border-border hover:border-primary/40'
                    }`}
                  >
                    <span className='block font-semibold'>{server.name}</span>
                    <span className='text-xs text-muted-foreground'>
                      {server.status === 'online'
                        ? `Đang trực tuyến (${server.ipAddress})`
                        : server.status === 'offline'
                          ? 'Không phản hồi'
                          : 'Chưa kết nối agent từ xa'}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            <div className='space-y-1.5'>
              <Label htmlFor='workspace-name'>Tên Workspace</Label>
              <Input
                id='workspace-name'
                value={workspaceName || `ws-${username}`}
                onChange={(event) => setWorkspaceName(event.target.value)}
              />
            </div>

            <div className='space-y-2'>
              <div className='flex items-center justify-between'>
                <Label>
                  Chọn Ổ đĩa / Phân vùng lưu trữ trên{' '}
                  {serverId === 'workstation' ? 'Workstation (aiot-ws1)' : 'Rack (aiotlab-rack)'}
                </Label>
                {loadingPools && (
                  <span className='text-xs text-muted-foreground animate-pulse'>
                    Đang quét thiết bị...
                  </span>
                )}
              </div>

              {hostStoragePools.length === 0 && !loadingPools ? (
                <Card className='p-4 text-center text-sm text-muted-foreground border-dashed'>
                  Không tìm thấy ổ đĩa nào hoặc Node Agent trên máy chủ chưa kích hoạt.
                </Card>
              ) : (
                <div className='grid gap-2.5 max-h-[280px] overflow-y-auto pr-1'>
                  {hostStoragePools.map((drive) => {
                    const isSelected = selectedDrive?.storageId === drive.storageId;
                    const usedPct = Math.min(
                      100,
                      Math.round((drive.usedGib / (drive.capacityGib || 1)) * 100)
                    );
                    const freePct = Math.min(
                      100,
                      Math.round((drive.freeGib / (drive.capacityGib || 1)) * 100)
                    );

                    return (
                      <button
                        type='button'
                        key={drive.storageId}
                        onClick={() => {
                          if (drive.eligible) {
                            setSelectedDrive(drive);
                            setStoragePoolId(drive.storageId);
                          } else {
                            toast.error(
                              `Ổ đĩa này không đủ điều kiện: ${drive.ineligibilityReason || 'Không khả dụng'}`
                            );
                          }
                        }}
                        className={`w-full text-left rounded-lg border p-3 cursor-pointer transition-all ${
                          !drive.eligible
                            ? 'opacity-60 bg-muted/30 border-dashed cursor-not-allowed'
                            : isSelected
                              ? 'border-primary bg-primary/10 ring-1 ring-primary shadow-sm'
                              : 'border-border hover:border-primary/40 bg-card'
                        }`}
                      >
                        <div className='flex items-start justify-between gap-2 mb-1.5'>
                          <div className='min-w-0'>
                            <div className='flex flex-wrap items-center gap-1.5'>
                              <span className='font-semibold text-sm'>
                                {drive.label || drive.mountPoint}
                              </span>
                              {drive.isDefault && (
                                <Badge
                                  variant='outline'
                                  className='text-[10px] border-primary/40 text-primary bg-primary/5'
                                >
                                  Mặc định
                                </Badge>
                              )}
                              <Badge
                                variant='secondary'
                                className='text-[10px] font-mono uppercase'
                              >
                                {drive.filesystem} · {drive.device}
                              </Badge>
                            </div>
                            <div className='text-[11px] font-mono text-muted-foreground truncate mt-0.5'>
                              Mount: {drive.mountPoint}
                            </div>
                            <p className='text-xs text-muted-foreground mt-0.5'>
                              {drive.quotaSupported ? (
                                <span className='text-emerald-700 dark:text-emerald-400 font-medium'>
                                  🛡️ {drive.quotaBackend} (Cưỡng chế cứng)
                                </span>
                              ) : (
                                <span className='text-amber-700 dark:text-amber-400'>
                                  ⚠️ Không hỗ trợ quota filesystem
                                </span>
                              )}
                            </p>
                          </div>
                          <div className='shrink-0'>
                            {drive.eligible ? (
                              <Badge
                                variant='outline'
                                className='border-emerald-500/40 text-emerald-600 bg-emerald-500/10 text-xs'
                              >
                                Đủ điều kiện
                              </Badge>
                            ) : (
                              <Badge variant='destructive' className='text-xs'>
                                {drive.ineligibilityReason || 'Không khả dụng'}
                              </Badge>
                            )}
                          </div>
                        </div>

                        {/* Visual capacity progress bar */}
                        <div className='space-y-1 mt-2'>
                          <div className='flex justify-between text-xs text-muted-foreground'>
                            <span>
                              Đã dùng: <strong>{drive.usedGib} GiB</strong> ({usedPct}%)
                            </span>
                            <span>
                              Khả dụng:{' '}
                              <strong className='text-emerald-600'>
                                {drive.allocatableGib} GiB
                              </strong>
                            </span>
                            <span>Tổng: {drive.capacityGib} GiB</span>
                          </div>
                          <div className='w-full h-2 rounded-full bg-muted overflow-hidden flex'>
                            <div style={{ width: `${usedPct}%` }} className='bg-primary/70' />
                            <div style={{ width: `${freePct}%` }} className='bg-emerald-500/60' />
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            <div className='grid gap-3 sm:grid-cols-2'>
              <div className='space-y-1.5'>
                <Label htmlFor='storage-quota'>Dung lượng hạn ngạch cấp phát (GiB)</Label>
                <Input
                  id='storage-quota'
                  type='number'
                  min={1}
                  max={
                    selectedDrive?.allocatableGib
                      ? Math.floor(selectedDrive.allocatableGib)
                      : undefined
                  }
                  value={storageQuotaGb}
                  onChange={(event) => setStorageQuotaGb(Number(event.target.value))}
                />
                {selectedDrive && storageQuotaGb > selectedDrive.allocatableGib && (
                  <p className='text-xs text-destructive font-medium'>
                    ❌ Vượt quá dung lượng có thể cấp phát của ổ đĩa này (tối đa{' '}
                    {selectedDrive.allocatableGib} GiB).
                  </p>
                )}
              </div>

              <div className='space-y-1.5'>
                <Label htmlFor='backup-policy'>Chính sách sao lưu</Label>
                <select
                  id='backup-policy'
                  className='h-9 w-full rounded-md border bg-background px-3 text-sm'
                  value={backupPolicy}
                  onChange={(event) => setBackupPolicy(event.target.value as typeof backupPolicy)}
                >
                  <option value='DAILY_SNAPSHOT'>Daily snapshot (Tự động hằng ngày)</option>
                  <option value='WEEKLY_SYNC'>Weekly sync (Đồng bộ hàng tuần)</option>
                  <option value='NONE'>Không sao lưu (None)</option>
                </select>
              </div>
            </div>

            {/* Auto-calculated paths preview */}
            <Card className='p-3.5 bg-muted/30 border-dashed space-y-2 text-xs'>
              <div className='flex items-center justify-between'>
                <span className='font-semibold text-muted-foreground uppercase tracking-wider text-[11px]'>
                  Đường dẫn thư mục riêng trên Host:
                </span>
                <Badge variant='outline' className='text-[10px] text-primary border-primary/30'>
                  Quyền cách ly: 0700
                </Badge>
              </div>
              <code className='block p-2 rounded bg-background font-mono text-xs text-emerald-800 dark:text-emerald-300 break-all border'>
                {selectedDrive
                  ? `${selectedDrive.mountPoint}/labops/users/${username || 'username'}`
                  : 'Chưa chọn ổ đĩa khả dụng trên máy chủ'}
              </code>
              <div className='flex items-center gap-2 pt-1'>
                <span className='text-muted-foreground'>Gắn vào container (Mount path):</span>
                <Badge variant='secondary' className='font-mono text-xs'>
                  /workspace (Cố định an toàn)
                </Badge>
              </div>
            </Card>
          </div>
        )}

        {step === 3 && (
          <div className='grid gap-4'>
            {/* Host Overview Header */}
            <Card className='p-3.5 bg-primary/5 border-primary/20 flex flex-wrap items-center justify-between gap-3 text-xs'>
              <div>
                <p className='font-semibold text-sm'>
                  Tài nguyên máy chủ:{' '}
                  {serverId === 'workstation'
                    ? 'Workstation (aiot-ws1)'
                    : 'Rack Cluster (aiotlab-rack)'}
                </p>
                <p className='text-muted-foreground'>
                  Mô hình tài nguyên động: CPU Shared Weighted & RAM Host Protected
                </p>
              </div>
              <div className='flex gap-2'>
                <Badge variant='outline' className='border-primary/40 bg-background'>
                  {resourceProfile?.totalCores || 16} CPU Cores
                </Badge>
                <Badge variant='outline' className='border-primary/40 bg-background'>
                  {Math.round((resourceProfile?.totalRamBytes || 0) / 1024 ** 3)} GiB RAM
                </Badge>
              </div>
            </Card>

            {/* Block 1: Shared Weighted CPU */}
            <Card className='p-4 border-border space-y-2'>
              <div className='flex items-center justify-between'>
                <div className='flex items-center gap-2'>
                  <Icons.cpu className='size-4 text-primary' />
                  <span className='font-semibold text-sm'>
                    Chính sách CPU: Dùng chung có trọng số
                  </span>
                </div>
                <Badge variant='secondary' className='font-mono text-xs font-semibold text-primary'>
                  SHARED_WEIGHTED
                </Badge>
              </div>
              <p className='text-xs text-muted-foreground leading-relaxed'>
                Không gán cứng số core cố định. Thành viên được tự do tăng tốc (burst) lên toàn bộ{' '}
                <strong>{resourceProfile?.totalCores || 16} CPU cores</strong> khi cụm máy nhàn rỗi.
                Khi xảy ra tranh chấp tải, cgroup tự động phân bổ tỷ lệ công bằng theo trọng số (CPU
                Shares: 100 / 1024).
              </p>
              <div className='flex items-center gap-2 pt-1 text-xs text-emerald-800 dark:text-emerald-400'>
                <Icons.check className='size-3.5 shrink-0' />
                <span>
                  Bảo vệ hệ thống: Giới hạn <strong>4,096 tiến trình (PIDs limit)</strong> để triệt
                  tiêu nguy cơ Fork-Bomb.
                </span>
              </div>
            </Card>

            {/* Block 2: Shared Host-Protected RAM */}
            <Card className='p-4 border-border space-y-2'>
              <div className='flex items-center justify-between'>
                <div className='flex items-center gap-2'>
                  <Icons.activity className='size-4 text-emerald-600' />
                  <span className='font-semibold text-sm'>
                    Chính sách RAM: Bộ nhớ dùng chung có bảo vệ Host
                  </span>
                </div>
                <Badge
                  variant='secondary'
                  className='font-mono text-xs font-semibold text-emerald-600'
                >
                  SHARED_HOST_PROTECTED
                </Badge>
              </div>
              <div className='grid grid-cols-3 gap-2 py-1 text-center text-xs'>
                <div className='rounded bg-muted/40 p-2'>
                  <span className='block text-muted-foreground text-[10px] uppercase'>
                    Tổng RAM Host
                  </span>
                  <span className='font-semibold text-sm'>
                    {Math.round((resourceProfile?.totalRamBytes || 0) / 1024 ** 3)} GiB
                  </span>
                </div>
                <div className='rounded bg-amber-500/10 p-2 border border-amber-500/20'>
                  <span className='block text-amber-700 dark:text-amber-300 text-[10px] uppercase font-medium'>
                    Dự trữ an toàn OS
                  </span>
                  <span className='font-semibold text-sm text-amber-700 dark:text-amber-300'>
                    {Math.round((resourceProfile?.hostReserveBytes || 0) / 1024 ** 3)} GiB (20%)
                  </span>
                </div>
                <div className='rounded bg-emerald-500/10 p-2 border border-emerald-500/20'>
                  <span className='block text-emerald-700 dark:text-emerald-300 text-[10px] uppercase font-medium'>
                    Ngân sách Containers
                  </span>
                  <span className='font-semibold text-sm text-emerald-700 dark:text-emerald-300'>
                    {Math.round((resourceProfile?.sharedBudgetBytes || 0) / 1024 ** 3)} GiB
                  </span>
                </div>
              </div>
              <p className='text-xs text-muted-foreground leading-relaxed'>
                Bộ nhớ được phân bổ linh hoạt trong Ngân sách Dùng chung. Cgroup cưỡng chế bảo vệ
                vùng Dự trữ Hệ thống, đảm bảo an toàn tuyệt đối cho Debian OS, PostgreSQL, SSH và
                Docker daemon, không bao giờ bị OOM Crash.
              </p>
            </Card>

            {/* Block 3: GPU & VRAM Allocation */}
            <div className='grid gap-3 sm:grid-cols-2'>
              <div className='space-y-1.5'>
                <Label htmlFor='gpu-choice'>Lựa chọn GPU</Label>
                <select
                  id='gpu-choice'
                  className='h-9 w-full rounded-md border bg-background px-3 text-sm'
                  value={selectedGpu}
                  onChange={(event) => setSelectedGpu(event.target.value)}
                >
                  <option value='NONE'>Chỉ CPU (Không dùng GPU)</option>
                  {assignedGpus.map((gpu) => (
                    <option key={gpu.id} value={gpu.id}>
                      {gpu.displayLabel} · {gpu.model} (24 GB VRAM)
                    </option>
                  ))}
                </select>
              </div>

              <div className='space-y-1.5'>
                <Label htmlFor='gpu-mode'>Chế độ cấp phát GPU</Label>
                <select
                  id='gpu-mode'
                  disabled={selectedGpu === 'NONE'}
                  className='h-9 w-full rounded-md border bg-background px-3 text-sm disabled:opacity-50'
                  value={gpuAllocationMode}
                  onChange={(event) =>
                    setGpuAllocationMode(event.target.value as GpuAllocationMode)
                  }
                >
                  <option value='EXCLUSIVE'>Độc quyền toàn bộ GPU (Khuyến nghị V2)</option>
                  <option value='SHARED'>Dùng chung có hạn ngạch (Shared)</option>
                </select>
              </div>

              {selectedGpu !== 'NONE' && (
                <div className='space-y-1.5 sm:col-span-2'>
                  <div className='flex items-center justify-between'>
                    <Label htmlFor='vram-quota'>Hạn ngạch VRAM (GB)</Label>
                    <Badge variant='outline' className='text-xs text-muted-foreground'>
                      {gpuAllocationMode === 'EXCLUSIVE'
                        ? '100% VRAM Độc quyền (24 GB)'
                        : 'Chính sách giám sát (Policy-Monitored)'}
                    </Badge>
                  </div>
                  <Input
                    id='vram-quota'
                    type='number'
                    min={0}
                    max={24}
                    value={gpuAllocationMode === 'EXCLUSIVE' ? 24 : vramQuotaGb}
                    disabled={gpuAllocationMode === 'EXCLUSIVE'}
                    onChange={(event) => setVramQuotaGb(Number(event.target.value))}
                  />
                  <p className='text-xs text-muted-foreground'>
                    {gpuAllocationMode === 'EXCLUSIVE'
                      ? 'Container được gán riêng biệt GPU A30 qua device passthrough (NVIDIA_VISIBLE_DEVICES).'
                      : 'Hạn mức VRAM được hệ thống theo dõi và báo cáo giám sát liên tục (Policy / Telemetry Monitored).'}
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

        {step === 4 && (
          <div className='grid gap-4'>
            <Card className='flex gap-3 p-4 border-primary/30 bg-primary/5'>
              <Icons.terminal className='size-5 shrink-0 text-primary' />
              <div className='space-y-1'>
                <p className='font-semibold text-sm'>
                  Chế độ Admin-Managed SSH (Thành viên không cần tự tạo SSH Key)
                </p>
                <p className='text-xs text-muted-foreground'>
                  Hệ thống tự động sinh cặp khóa <code>ssh-ed25519</code> thật bằng{' '}
                  <code>ssh-keygen</code>, bảo vệ Private Key bằng Passphrase mạnh, cài đặt Public
                  Key vào thư mục Workspace riêng (<code>0700</code>) và cấp{' '}
                  <strong>Mật khẩu tạm đăng nhập lần đầu</strong> (bắt buộc đổi ngay khi đăng nhập).
                </p>
              </div>
            </Card>

            <div className='grid grid-cols-2 gap-2'>
              <button
                type='button'
                onClick={() => setAutoGenerateSsh(true)}
                className={`rounded-md border p-3 text-left text-sm ${autoGenerateSsh ? 'border-primary bg-primary/10' : 'border-border'}`}
              >
                <span className='font-semibold block'>Tự động tạo cặp khóa SSH (Khuyên dùng)</span>
                <span className='mt-1 block text-xs text-muted-foreground'>
                  Hệ thống sinh sẵn khóa Ed25519, file WireGuard .conf và script kết nối 1 chạm
                </span>
              </button>
              <button
                type='button'
                onClick={() => setAutoGenerateSsh(false)}
                className={`rounded-md border p-3 text-left text-sm ${!autoGenerateSsh ? 'border-primary bg-primary/10' : 'border-border'}`}
              >
                <span className='font-semibold block'>Dán Public Key sẵn có của thành viên</span>
                <span className='mt-1 block text-xs text-muted-foreground'>
                  Dành cho trường hợp thành viên tự gửi tệp .pub
                </span>
              </button>
            </div>

            {autoGenerateSsh && (
              <div className='rounded-md border p-3 space-y-2 bg-muted/20'>
                <Label className='font-semibold text-xs'>Tùy chọn bảo vệ Private Key:</Label>
                <div className='grid grid-cols-2 gap-2'>
                  <button
                    type='button'
                    onClick={() => setRequirePassphrase(false)}
                    className={`rounded border p-2.5 text-left text-xs ${!requirePassphrase ? 'border-emerald-600 bg-emerald-500/10 font-semibold' : 'border-border'}`}
                  >
                    <span className='block text-emerald-800 dark:text-emerald-300'>
                      ⚡ Kết nối 1 chạm (Không Passphrase)
                    </span>
                    <span className='block text-xs text-muted-foreground mt-0.5'>
                      Thành viên gõ lệnh là vào thẳng terminal ngay, không bị hỏi mật khẩu
                    </span>
                  </button>
                  <button
                    type='button'
                    onClick={() => setRequirePassphrase(true)}
                    className={`rounded border p-2.5 text-left text-xs ${requirePassphrase ? 'border-primary bg-primary/10 font-semibold' : 'border-border'}`}
                  >
                    <span className='block'>🔒 Đặt Passphrase bảo vệ</span>
                    <span className='block text-xs text-muted-foreground mt-0.5'>
                      Bắt buộc nhập passphrase mỗi lần kết nối SSH để tăng cường bảo mật
                    </span>
                  </button>
                </div>
              </div>
            )}

            <div className='space-y-1.5'>
              <Label htmlFor='ssh-key-label'>Nhãn khóa SSH</Label>
              <Input
                id='ssh-key-label'
                placeholder='VD: Admin-Managed Ed25519 Key'
                value={sshKeyLabel}
                onChange={(e) => setSshKeyLabel(e.target.value)}
              />
            </div>

            {!autoGenerateSsh && (
              <div className='space-y-1.5'>
                <div className='flex items-center justify-between'>
                  <Label htmlFor='ssh-public-key'>OpenSSH Public Key (ssh-ed25519 / ssh-rsa)</Label>
                  <Button
                    type='button'
                    size='sm'
                    variant='outline'
                    className='h-7 text-xs'
                    onClick={() => {
                      const sample = generateSampleOpenSshPublicKey(username);
                      setSshPublicKey(sample);
                      toast.info('Đã điền khóa công khai OpenSSH Ed25519 hợp lệ.');
                    }}
                  >
                    Tạo nhanh khóa Ed25519 mẫu
                  </Button>
                </div>
                <textarea
                  id='ssh-public-key'
                  rows={3}
                  className='w-full rounded-md border bg-background p-2.5 font-mono text-xs'
                  placeholder='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@laptop'
                  value={sshPublicKey}
                  onChange={(e) => setSshPublicKey(e.target.value)}
                />
              </div>
            )}

            <Card
              className={`p-3 text-xs space-y-1 ${
                vpnOverview?.status === 'HEALTHY' || vpnOverview?.status === 'VPN_API_HEALTHY'
                  ? 'border-emerald-500/40 bg-emerald-500/5 text-emerald-800 dark:text-emerald-200'
                  : 'border-amber-500/40 bg-amber-500/5 text-amber-900 dark:text-amber-200'
              }`}
            >
              <div className='flex items-center justify-between gap-2'>
                <span className='font-semibold'>Trạng thái WireGuard VPN Gateway (wg-easy):</span>
                <Badge variant='outline' className='font-mono text-[10px]'>
                  {vpnOverview?.status || 'ĐANG KIỂM TRA...'}
                </Badge>
              </div>
              {vpnOverview?.status === 'HEALTHY' || vpnOverview?.status === 'VPN_API_HEALTHY' ? (
                <p>
                  WireGuard Gateway (wg-easy): {vpnOverview.status} ({vpnOverview.public_endpoint})
                  — sẽ tự động cấp peer VPN theo đúng máy chủ (
                  {serverId === 'workstation' ? '192.168.167.251/32' : '192.168.167.250/32'}).
                </p>
              ) : (
                <p>
                  WireGuard Gateway (wg-easy): {vpnOverview?.status || 'OFFLINE'} — tài khoản vẫn
                  tạo SSH thành công để kết nối trực tiếp trong mạng LAN; VPN từ xa chưa cấp file{' '}
                  <code>.conf</code> cho đến khi gateway sẵn sàng và nhất quán định tuyến.
                </p>
              )}
            </Card>
          </div>
        )}

        {step === 5 && (
          <div className='grid gap-3'>
            {created ? (
              <div className='space-y-3'>
                <Card className='border-primary/40 bg-primary/5 p-4 space-y-2'>
                  <div className='flex flex-wrap items-center justify-between gap-2'>
                    <p className='font-semibold'>
                      Đã khởi tạo quyền truy cập cho @{created.username}
                    </p>
                    <Badge variant='outline'>{created.provisioningState}</Badge>
                  </div>
                  <p className='text-xs text-muted-foreground'>
                    Trạng thái tài khoản: <code>{created.status}</code> · Mật khẩu lần đầu:{' '}
                    <code>{created.passwordStatus || 'CHANGE_REQUIRED'}</code>
                  </p>
                  {created.sshPort && (
                    <div className='rounded-md border bg-background p-3 font-mono text-xs space-y-1'>
                      <p className='font-semibold text-foreground'>Thông tin kết nối SSH:</p>
                      <p>
                        Lệnh SSH 1 chạm (sau khi chạy script cài đặt):{' '}
                        <code className='text-primary font-bold select-all'>
                          ssh labops-{created.username}
                        </code>
                      </p>
                      <p>
                        Lệnh trực tiếp:{' '}
                        <code className='text-foreground select-all'>
                          ssh -i ~/.ssh/id_ed25519_{created.username} -p {created.sshPort}{' '}
                          {created.sshLoginUser || created.username}@
                          {created.sshIp ||
                            (created.assignedServer === 'workstation'
                              ? '192.168.167.251'
                              : '192.168.167.250')}
                        </code>
                      </p>
                      {created.sshLoginUser && created.sshLoginUser !== created.username && (
                        <p className='text-[11px] font-sans text-muted-foreground'>
                          Ghi chú cách ly: sshd không đặc quyền đăng nhập qua OS user{' '}
                          <code>{created.sshLoginUser}</code> và ánh xạ ForceCommand vào thư mục
                          workspace cô lập của @{created.username}.
                        </p>
                      )}
                      <p>
                        Fingerprint: <code>{created.sshFingerprint}</code>
                      </p>
                    </div>
                  )}

                  {created.wireguard && (
                    <div
                      className={`rounded-md border p-3 text-xs space-y-1 ${
                        created.wireguard.registeredOnGateway
                          ? 'border-emerald-500/40 bg-emerald-500/5'
                          : 'border-amber-500/40 bg-amber-500/5'
                      }`}
                    >
                      <div className='flex items-center justify-between gap-2'>
                        <p className='font-semibold text-foreground'>Trạng thái WireGuard VPN:</p>
                        <Badge variant='outline' className='font-mono text-[10px]'>
                          {created.wireguard.status}
                        </Badge>
                      </div>
                      <p className='font-mono'>
                        IP VPN: <strong>{created.wireguard.clientIp || 'Chưa cấp'}</strong> · Route:{' '}
                        <strong>{created.wireguard.allowedSubnets || 'Chưa xác định'}</strong>
                      </p>
                      {created.wireguard.registeredOnGateway === false && (
                        <p className='text-amber-800 dark:text-amber-300'>
                          ⚠️ Peer chưa được đăng ký trên wg-easy Gateway (
                          {created.wireguard.gatewayError || 'wg-easy đang offline'}). Trong mạng
                          LAN phòng Lab, hãy kết nối trực tiếp bằng{' '}
                          <code>ssh labops-{created.username}</code> (không bật WireGuard).
                        </p>
                      )}
                    </div>
                  )}

                  {created.runtimeVerification && (
                    <div className='rounded border bg-background p-3 text-xs space-y-1.5'>
                      <p className='font-semibold'>Bằng chứng kiểm tra Runtime thực tế:</p>
                      <div className='grid grid-cols-2 gap-1 font-mono text-xs'>
                        <span>
                          Host Online:{' '}
                          {created.runtimeVerification.host_online ? 'ONLINE' : 'OFFLINE'}
                        </span>
                        <span>
                          Cách ly thư mục (0700): {created.runtimeVerification.directory_isolation}
                        </span>
                        <span>
                          Khóa SSH (0600): {created.runtimeVerification.ssh_key_installed}
                        </span>
                        <span>
                          Không Sudo / Docker sock:{' '}
                          {created.runtimeVerification.privilege_isolation}
                        </span>
                        <span>Giới hạn CPU/RAM: {created.runtimeVerification.cpu_ram_limits}</span>
                        <span>Storage Quota: {created.runtimeVerification.storage_quota}</span>
                        <span>SSH Daemon Runtime: {created.runtimeVerification.ssh_runtime}</span>
                        <span>Tổng kết: {created.runtimeVerification.overall_status}</span>
                      </div>
                      {created.runtimeVerification.reasons &&
                        created.runtimeVerification.reasons.length > 0 && (
                          <ul className='list-disc pl-4 text-xs text-amber-700 dark:text-amber-300 space-y-0.5'>
                            {created.runtimeVerification.reasons.map((reason, idx) => (
                              <li key={idx}>{reason}</li>
                            ))}
                          </ul>
                        )}
                    </div>
                  )}
                </Card>

                <Card className='p-4 space-y-3 border-amber-500/40'>
                  <p className='font-semibold text-sm'>
                    Bàn giao thông tin cho thành viên (Tải trọn bộ .ZIP hoặc tách kênh bảo mật)
                  </p>
                  <div className='flex flex-wrap items-center gap-2'>
                    <Button
                      type='button'
                      size='sm'
                      className='font-bold'
                      disabled={downloadingZip}
                      onClick={() => void handleDownloadZip()}
                    >
                      {downloadingZip
                        ? 'Đang đóng gói và tải ZIP…'
                        : '📦 Tải trọn gói kết nối (.zip) dùng ngay'}
                    </Button>
                    <Button
                      type='button'
                      size='sm'
                      variant='secondary'
                      disabled={claimingSecrets || Boolean(claimedSecrets)}
                      onClick={() => void handleClaimSecrets()}
                    >
                      {claimedSecrets
                        ? 'Đã lấy Passphrase & Mật khẩu tạm (Đã xóa khỏi RAM)'
                        : claimingSecrets
                          ? 'Đang lấy…'
                          : '🔑 Hiển thị Passphrase & Mật khẩu tạm (Kênh riêng)'}
                    </Button>
                  </div>

                  <div className='pt-1'>
                    <details className='text-xs text-muted-foreground cursor-pointer'>
                      <summary className='font-medium text-primary hover:underline py-0.5'>
                        ▶ Hoặc giải nén / lấy từng file cấu hình riêng lẻ (.conf, .sh, Private Key)
                      </summary>
                      <div className='mt-2.5 space-y-2 pl-2 border-l-2 border-muted'>
                        <Button
                          type='button'
                          size='sm'
                          variant='outline'
                          disabled={claimingBundle || Boolean(claimedBundle)}
                          onClick={() => void handleClaimKeyBundle()}
                        >
                          {claimedBundle
                            ? 'Đã mở khóa Gói Cấu hình (Xem bên dưới)'
                            : claimingBundle
                              ? 'Đang tạo file config…'
                              : 'Tải rời: WireGuard .conf & Script SSH 1 chạm'}
                        </Button>
                      </div>
                    </details>
                  </div>

                  {claimedBundle && (
                    <div className='rounded-md border border-primary/40 bg-primary/5 p-3 text-xs space-y-2'>
                      <p className='font-semibold'>
                        Các tệp cấu hình dùng ngay cho @{claimedBundle.username} (Đã tải tự động —
                        có thể bấm tải lại ngay trong phiên này):
                      </p>
                      <div className='flex flex-wrap gap-2'>
                        {claimedBundle.wireguard_conf && (
                          <Button
                            type='button'
                            size='sm'
                            variant='outline'
                            onClick={() =>
                              downloadTextFile(
                                `${claimedBundle.username}-labops-wg0.conf`,
                                claimedBundle.wireguard_conf!
                              )
                            }
                          >
                            Tải {claimedBundle.username}-labops-wg0.conf (Import vào WireGuard)
                          </Button>
                        )}
                        {claimedBundle.quick_setup_script && (
                          <Button
                            type='button'
                            size='sm'
                            variant='outline'
                            onClick={() =>
                              downloadTextFile(
                                `setup-ssh-${claimedBundle.username}.sh`,
                                claimedBundle.quick_setup_script!
                              )
                            }
                          >
                            Tải setup-ssh-{claimedBundle.username}.sh (Tự động cài SSH & Key)
                          </Button>
                        )}
                        {claimedBundle.ssh_config_snippet && (
                          <Button
                            type='button'
                            size='sm'
                            variant='outline'
                            onClick={() =>
                              downloadTextFile(
                                `ssh_config_${claimedBundle.username}`,
                                claimedBundle.ssh_config_snippet!
                              )
                            }
                          >
                            Tải ssh_config_{claimedBundle.username}
                          </Button>
                        )}
                        <Button
                          type='button'
                          size='sm'
                          variant='outline'
                          onClick={() =>
                            downloadTextFile(
                              `id_ed25519_${claimedBundle.username}`,
                              claimedBundle.encrypted_private_key
                            )
                          }
                        >
                          Tải riêng Key id_ed25519_{claimedBundle.username}
                        </Button>
                      </div>
                      <p className='font-mono text-xs text-muted-foreground'>
                        Sau khi chạy <code>bash setup-ssh-{claimedBundle.username}.sh</code>, thành
                        viên chỉ cần gõ:{' '}
                        <strong className='text-foreground'>
                          {claimedBundle.ssh_alias_command ||
                            `ssh labops-${claimedBundle.username}`}
                        </strong>
                      </p>
                    </div>
                  )}

                  {claimedSecrets && (
                    <div className='rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs font-mono space-y-1.5'>
                      <p className='font-sans font-semibold text-emerald-800 dark:text-emerald-200'>
                        Gửi 2 thông tin dưới đây cho @{created.username} qua kênh liên lạc riêng
                        (KHÔNG gửi chung với tệp Private Key):
                      </p>
                      <p>
                        Passphrase mở khóa Private Key:{' '}
                        <strong className='select-all bg-background px-1.5 py-0.5 rounded border'>
                          {claimedSecrets.key_passphrase}
                        </strong>
                      </p>
                      <p>
                        Mật khẩu tạm đăng nhập lần đầu:{' '}
                        <strong className='select-all bg-background px-1.5 py-0.5 rounded border'>
                          {claimedSecrets.temporary_password}
                        </strong>
                      </p>
                      <p className='text-xs text-muted-foreground font-sans'>
                        Hạn sử dụng mật khẩu tạm: {claimedSecrets.temp_password_expires_at} · Thành
                        viên bắt buộc đổi mật khẩu mới ngay ở lần đăng nhập đầu tiên.
                      </p>
                    </div>
                  )}
                </Card>
              </div>
            ) : (
              <Card className='space-y-3 p-4 text-sm'>
                <p className='font-semibold text-base border-b pb-2'>
                  Xem lại thông tin cấp phát tài nguyên & SSH
                </p>
                <div className='grid gap-2 text-xs sm:grid-cols-2'>
                  <div>
                    <span className='text-muted-foreground'>Thành viên:</span>{' '}
                    <strong>{fullName}</strong> (@{username})
                  </div>
                  <div>
                    <span className='text-muted-foreground'>Email / Vai trò:</span> {effectiveEmail}{' '}
                    ({role})
                  </div>
                  <div>
                    <span className='text-muted-foreground'>Máy chủ:</span>{' '}
                    <strong>
                      {serverId === 'workstation'
                        ? 'Workstation (aiot-ws1)'
                        : 'Rack Cluster (aiotlab-rack)'}
                    </strong>
                  </div>
                  <div>
                    <span className='text-muted-foreground'>Workspace:</span>{' '}
                    <code>{workspaceName || `ws-${username}`}</code>
                  </div>
                </div>

                <div className='rounded-md border p-2.5 bg-muted/20 space-y-1.5 text-xs'>
                  <div className='flex items-center justify-between'>
                    <span className='font-semibold text-foreground'>Ổ đĩa & Hạn ngạch:</span>
                    <Badge variant='outline' className='text-[10px] font-mono'>
                      {selectedDrive
                        ? selectedDrive.quotaSupported && selectedDrive.quotaEnforced
                          ? `${selectedDrive.quotaBackend} (Cưỡng chế cứng)`
                          : `${selectedDrive.quotaBackend} (Giám sát — không quota cứng)`
                        : 'Chưa chọn ổ đĩa'}
                    </Badge>
                  </div>
                  <p>
                    Ổ đĩa:{' '}
                    <strong>
                      {selectedDrive
                        ? `${selectedDrive.mountPoint} (${selectedDrive.device})`
                        : 'Chưa chọn'}
                    </strong>{' '}
                    · Hạn ngạch: <strong className='text-primary'>{storageQuotaGb} GiB</strong>
                  </p>
                  <p className='text-muted-foreground font-mono text-[11px] break-all'>
                    Host dir:{' '}
                    {selectedDrive
                      ? `${selectedDrive.mountPoint}/labops/users/${username || 'username'}`
                      : 'Chưa chọn ổ đĩa'}{' '}
                    → Container: /workspace
                  </p>
                </div>

                <div className='rounded-md border p-2.5 bg-muted/20 space-y-1 text-xs'>
                  <div className='flex items-center justify-between gap-2'>
                    <span className='font-semibold text-foreground'>WireGuard VPN Gateway:</span>
                    <Badge variant='outline' className='text-[10px] font-mono'>
                      {vpnOverview?.status || 'CHƯA RÕ'}
                    </Badge>
                  </div>
                  <p className='text-muted-foreground'>
                    {vpnOverview?.status === 'HEALTHY' || vpnOverview?.status === 'VPN_API_HEALTHY'
                      ? `Tự động cấp peer VPN qua wg-easy (${vpnOverview.public_endpoint}).`
                      : `wg-easy ở trạng thái ${vpnOverview?.status || 'OFFLINE'} — SSH LAN vẫn hoạt động ngay; VPN từ xa sẽ chờ kích hoạt.`}
                  </p>
                </div>

                <div className='rounded-md border p-2.5 bg-muted/20 space-y-1 text-xs'>
                  <span className='font-semibold text-foreground'>Chính sách CPU / RAM / GPU:</span>
                  <div className='flex flex-wrap gap-1.5 pt-0.5'>
                    <Badge variant='secondary' className='text-[10px]'>
                      CPU: SHARED_WEIGHTED (Trọng số 100/1024 · 4096 PIDs)
                    </Badge>
                    <Badge variant='secondary' className='text-[10px]'>
                      RAM: SHARED_HOST_PROTECTED (Bảo vệ 20% Host OS)
                    </Badge>
                    <Badge variant='secondary' className='text-[10px]'>
                      GPU:{' '}
                      {selectedGpu === 'NONE' ? 'Chỉ CPU' : `${selectedGpu} (${gpuAllocationMode})`}
                    </Badge>
                  </div>
                </div>

                <div className='text-xs space-y-1 pt-1'>
                  <p className='font-semibold'>Chế độ khóa SSH & Xác thực:</p>
                  <p className='text-muted-foreground'>
                    {autoGenerateSsh ? (
                      <span className='font-medium text-emerald-700 dark:text-emerald-300'>
                        ⚡ Tự động sinh cặp khóa Ed25519{' '}
                        {requirePassphrase ? '(Có Passphrase)' : '(Không Passphrase, 1 chạm)'} + Mật
                        khẩu tạm lần đầu
                      </span>
                    ) : sshPublicKey.trim() ? (
                      <code className='text-xs text-emerald-600'>
                        {sshPublicKey.trim().slice(0, 44)}...
                      </code>
                    ) : (
                      <span className='text-amber-600'>Chưa nhập khóa</span>
                    )}
                  </p>
                </div>
              </Card>
            )}
          </div>
        )}

        <DialogFooter className='gap-2 border-t pt-3'>
          {step > 1 && !created && (
            <Button type='button' variant='outline' onClick={() => setStep(step - 1)}>
              Quay lại
            </Button>
          )}
          {step < 4 && (
            <Button
              type='button'
              disabled={
                (step === 1 &&
                  (!fullName.trim() || !usernameIsValid || !effectiveEmail.includes('@'))) ||
                (step === 2 &&
                  (!selectedDrive ||
                    !selectedDrive.eligible ||
                    storageQuotaGb < 1 ||
                    storageQuotaGb > selectedDrive.allocatableGib))
              }
              onClick={() => setStep(step + 1)}
            >
              Tiếp tục
            </Button>
          )}
          {step === 4 && (
            <Button type='button' onClick={() => setStep(5)}>
              Xem lại & Xác nhận
            </Button>
          )}
          {step === 5 && !created && (
            <Button
              type='button'
              disabled={
                submitting ||
                DEMO_MODE ||
                !fullName.trim() ||
                !usernameIsValid ||
                !effectiveEmail.includes('@') ||
                !selectedDrive ||
                !selectedDrive.eligible ||
                storageQuotaGb < 1 ||
                storageQuotaGb > selectedDrive.allocatableGib
              }
              onClick={createAccessRequest}
            >
              {submitting ? 'Đang tạo & cấp SSH…' : 'Tạo Thành viên & Cấp SSH'}
            </Button>
          )}
          {step === 5 && created && (
            <Button
              type='button'
              onClick={() => {
                resetWizard();
                onOpenChange(false);
              }}
            >
              Đóng
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
