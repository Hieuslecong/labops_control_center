'use client';

import { useState } from 'react';
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
import { GpuAllocationMode, MemberRole, ServerId } from '@/types/labops';
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

export function MemberProvisioningWizard({ open, onOpenChange }: MemberProvisioningWizardProps) {
  const servers = useLabOpsStore((state) => state.servers);
  const storagePools = useLabOpsStore((state) => state.storagePools);
  const gpus = useLabOpsStore((state) => state.gpus);
  const [step, setStep] = useState(1);
  const [fullName, setFullName] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<MemberRole>('RESEARCHER');
  const [researchGroup, setResearchGroup] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [serverId, setServerId] = useState<ServerId>('track');
  const [storagePoolId, setStoragePoolId] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  const [mountPath, setMountPath] = useState('/workspace');
  const [storageQuotaGb, setStorageQuotaGb] = useState(100);
  const [backupPolicy, setBackupPolicy] = useState<'DAILY_SNAPSHOT' | 'WEEKLY_SYNC' | 'NONE'>('DAILY_SNAPSHOT');
  const [cpuLimitCores, setCpuLimitCores] = useState(4);
  const [ramLimitGb, setRamLimitGb] = useState(16);
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
    status: string;
    provisioningState: string;
    sshPort?: number | null;
    sshIp?: string | null;
    sshFingerprint?: string | null;
    passwordStatus?: string;
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

  const usernameIsValid = validUsername(username);
  const effectiveEmail = email.trim()
    ? email.includes('@')
      ? email.trim()
      : `${email.trim()}@lab.ai.vn`
    : username
      ? `${username}@lab.ai.vn`
      : '';
  const assignedPools = storagePools.filter((pool) => pool.serverId === serverId);
  const assignedGpus = gpus.filter((gpu) => gpu.serverId === serverId);

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
          storagePoolId: storagePoolId || null,
          workspaceName: workspaceName || `ws-${username}`,
          mountPath,
          storageQuotaGb,
          backupPolicy,
          cpuLimitCores,
          ramLimitGb,
          gpuPermissions: selectedGpu === 'NONE' ? [] : [selectedGpu],
          vramQuotaGb,
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
      setCreated({
        id: result.id,
        username: result.username,
        status: result.status,
        provisioningState: result.provisioningState || result.provisioning_state,
        sshPort: result.sshPort ?? result.ssh_port ?? null,
        sshIp: result.sshIp ?? result.ssh_ip ?? null,
        sshFingerprint: result.sshFingerprint ?? result.ssh_fingerprint ?? null,
        passwordStatus: result.passwordStatus ?? result.password_status,
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

  const downloadTextFile = (filename: string, content: string) => {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
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
      const response = await fetch(`/api/labops/v2/ssh-access/${encodeURIComponent(created.id)}/claim-key-bundle`, {
        method: 'POST',
        cache: 'no-store'
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Không thể tải gói khóa riêng (đã tải trước đó).');
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
      const response = await fetch(`/api/labops/v2/ssh-access/${encodeURIComponent(created.id)}/claim-secrets`, {
        method: 'POST',
        cache: 'no-store'
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Passphrase & mật khẩu tạm đã được lấy trước đó.');
      }
      setClaimedSecrets(data);
      toast.success('Đã hiển thị Passphrase & Mật khẩu tạm (1 lần duy nhất). Hãy gửi qua kênh riêng!');
      await useLabOpsStore.getState().fetchAccessMembers();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Lỗi nhận thông tin bí mật.');
    } finally {
      setClaimingSecrets(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-2xl max-h-[90vh] overflow-y-auto'>
        <DialogHeader>
          <div className='flex items-center justify-between gap-3'>
            <div>
              <DialogTitle>Tạo quyền truy cập SSH cho Thành viên (LabOps V2)</DialogTitle>
              <DialogDescription>
                Admin tạo workspace riêng, hệ thống tự động sinh cặp khóa SSH mã hóa bằng passphrase và mật khẩu tạm lần đầu.
              </DialogDescription>
            </div>
            <Badge variant={DEMO_MODE ? 'secondary' : 'outline'}>{DEMO_MODE ? 'DEMO' : `${step}/5`}</Badge>
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
                    if (!workspaceName || workspaceName === `ws-${username}`) setWorkspaceName(`ws-${value}`);
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
                  <p className='text-[11px] text-muted-foreground'>
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
                      setStoragePoolId('');
                      setSelectedGpu('NONE');
                    }}
                    className={`rounded-md border p-3 text-left ${serverId === server.id ? 'border-primary bg-primary/10' : 'border-border'}`}
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
            <div className='space-y-1.5'>
              <Label htmlFor='storage-pool'>Storage pool</Label>
              <select
                id='storage-pool'
                className='h-9 w-full rounded-md border bg-background px-3 text-sm'
                value={storagePoolId}
                onChange={(event) => setStoragePoolId(event.target.value)}
              >
                <option value=''>Backend chọn pool mặc định</option>
                {assignedPools.map((pool) => (
                  <option key={pool.id} value={pool.id}>
                    {pool.label} · còn {pool.freeGb} GB
                  </option>
                ))}
              </select>
            </div>
            <div className='grid gap-3 sm:grid-cols-2'>
              <div className='space-y-1.5'>
                <Label htmlFor='mount-path'>Mount path</Label>
                <select
                  id='mount-path'
                  className='h-9 w-full rounded-md border bg-background px-3 text-sm'
                  value={mountPath}
                  onChange={(event) => setMountPath(event.target.value)}
                >
                  <option value='/workspace'>/workspace</option>
                  <option value={`/home/${username}`}>/home/{username || 'username'}</option>
                  <option value='/data'>/data</option>
                </select>
              </div>
              <div className='space-y-1.5'>
                <Label htmlFor='storage-quota'>Storage quota (GB)</Label>
                <Input
                  id='storage-quota'
                  type='number'
                  min={1}
                  value={storageQuotaGb}
                  onChange={(event) => setStorageQuotaGb(Number(event.target.value))}
                />
              </div>
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='backup-policy'>Backup policy</Label>
              <select
                id='backup-policy'
                className='h-9 w-full rounded-md border bg-background px-3 text-sm'
                value={backupPolicy}
                onChange={(event) => setBackupPolicy(event.target.value as typeof backupPolicy)}
              >
                <option value='DAILY_SNAPSHOT'>Daily snapshot</option>
                <option value='WEEKLY_SYNC'>Weekly sync</option>
                <option value='NONE'>None</option>
              </select>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className='grid gap-4 sm:grid-cols-2'>
            <div className='space-y-1.5'>
              <Label htmlFor='cpu-limit'>CPU cores</Label>
              <Input
                id='cpu-limit'
                type='number'
                min={1}
                value={cpuLimitCores}
                onChange={(event) => setCpuLimitCores(Number(event.target.value))}
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='ram-limit'>RAM (GB)</Label>
              <Input
                id='ram-limit'
                type='number'
                min={1}
                value={ramLimitGb}
                onChange={(event) => setRamLimitGb(Number(event.target.value))}
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='gpu-choice'>GPU</Label>
              <select
                id='gpu-choice'
                className='h-9 w-full rounded-md border bg-background px-3 text-sm'
                value={selectedGpu}
                onChange={(event) => setSelectedGpu(event.target.value)}
              >
                <option value='NONE'>Chỉ CPU</option>
                {assignedGpus.map((gpu) => (
                  <option key={gpu.id} value={gpu.id}>
                    {gpu.displayLabel} · {gpu.model}
                  </option>
                ))}
              </select>
              {!assignedGpus.length && (
                <p className='text-xs text-muted-foreground'>Chưa có GPU telemetry xác minh cho server này.</p>
              )}
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='gpu-mode'>GPU mode</Label>
              <select
                id='gpu-mode'
                className='h-9 w-full rounded-md border bg-background px-3 text-sm'
                value={gpuAllocationMode}
                onChange={(event) => setGpuAllocationMode(event.target.value as GpuAllocationMode)}
              >
                <option value='EXCLUSIVE'>Full GPU Exclusive (Khuyến nghị V2)</option>
                <option value='SHARED'>Shared</option>
              </select>
            </div>
            <div className='space-y-1.5 sm:col-span-2'>
              <Label htmlFor='vram-quota'>VRAM quota (GB)</Label>
              <Input
                id='vram-quota'
                type='number'
                min={0}
                value={selectedGpu === 'NONE' ? 0 : vramQuotaGb}
                disabled={selectedGpu === 'NONE'}
                onChange={(event) => setVramQuotaGb(Number(event.target.value))}
              />
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
                  Hệ thống tự động sinh cặp khóa <code>ssh-ed25519</code> thật bằng <code>ssh-keygen</code>, bảo vệ
                  Private Key bằng Passphrase mạnh, cài đặt Public Key vào thư mục Workspace riêng (<code>0700</code>)
                  và cấp <strong>Mật khẩu tạm đăng nhập lần đầu</strong> (bắt buộc đổi ngay khi đăng nhập).
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
                    <span className='block text-emerald-800 dark:text-emerald-300'>⚡ Kết nối 1 chạm (Không Passphrase)</span>
                    <span className='block text-[11px] text-muted-foreground mt-0.5'>
                      Thành viên gõ lệnh là vào thẳng terminal ngay, không bị hỏi mật khẩu
                    </span>
                  </button>
                  <button
                    type='button'
                    onClick={() => setRequirePassphrase(true)}
                    className={`rounded border p-2.5 text-left text-xs ${requirePassphrase ? 'border-primary bg-primary/10 font-semibold' : 'border-border'}`}
                  >
                    <span className='block'>🔒 Đặt Passphrase bảo vệ</span>
                    <span className='block text-[11px] text-muted-foreground mt-0.5'>
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
                        Lệnh SSH:{' '}
                        <code className='text-primary font-bold'>
                          ssh -i id_ed25519_{created.username} -p {created.sshPort} {created.username}@
                          {created.sshIp || '192.168.167.250'}
                        </code>
                      </p>
                      <p>
                        Fingerprint: <code>{created.sshFingerprint}</code>
                      </p>
                    </div>
                  )}

                  {created.runtimeVerification && (
                    <div className='rounded border bg-background p-3 text-xs space-y-1.5'>
                      <p className='font-semibold'>Bằng chứng kiểm tra Runtime thực tế:</p>
                      <div className='grid grid-cols-2 gap-1 font-mono text-[11px]'>
                        <span>Host Online: {created.runtimeVerification.host_online ? 'ONLINE' : 'OFFLINE'}</span>
                        <span>Cách ly thư mục (0700): {created.runtimeVerification.directory_isolation}</span>
                        <span>Khóa SSH (0600): {created.runtimeVerification.ssh_key_installed}</span>
                        <span>Không Sudo / Docker sock: {created.runtimeVerification.privilege_isolation}</span>
                        <span>Giới hạn CPU/RAM: {created.runtimeVerification.cpu_ram_limits}</span>
                        <span>Storage Quota: {created.runtimeVerification.storage_quota}</span>
                        <span>SSH Daemon Runtime: {created.runtimeVerification.ssh_runtime}</span>
                        <span>Tổng kết: {created.runtimeVerification.overall_status}</span>
                      </div>
                      {created.runtimeVerification.reasons && created.runtimeVerification.reasons.length > 0 && (
                        <ul className='list-disc pl-4 text-[11px] text-amber-700 dark:text-amber-300 space-y-0.5'>
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
                      {downloadingZip ? 'Đang đóng gói và tải ZIP…' : '📦 Tải trọn gói kết nối (.zip) dùng ngay'}
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
                        Các tệp cấu hình dùng ngay cho @{claimedBundle.username} (Đã tải tự động — có thể bấm tải lại ngay trong phiên này):
                      </p>
                      <div className='flex flex-wrap gap-2'>
                        {claimedBundle.wireguard_conf && (
                          <Button
                            type='button'
                            size='sm'
                            variant='outline'
                            onClick={() =>
                              downloadTextFile(`${claimedBundle.username}-labops-wg0.conf`, claimedBundle.wireguard_conf!)
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
                              downloadTextFile(`setup-ssh-${claimedBundle.username}.sh`, claimedBundle.quick_setup_script!)
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
                              downloadTextFile(`ssh_config_${claimedBundle.username}`, claimedBundle.ssh_config_snippet!)
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
                            downloadTextFile(`id_ed25519_${claimedBundle.username}`, claimedBundle.encrypted_private_key)
                          }
                        >
                          Tải riêng Key id_ed25519_{claimedBundle.username}
                        </Button>
                      </div>
                      <p className='font-mono text-[11px] text-muted-foreground'>
                        Sau khi chạy <code>bash setup-ssh-{claimedBundle.username}.sh</code>, thành viên chỉ cần gõ:{' '}
                        <strong className='text-foreground'>
                          {claimedBundle.ssh_alias_command || `ssh labops-${claimedBundle.username}`}
                        </strong>
                      </p>
                    </div>
                  )}

                  {claimedSecrets && (
                    <div className='rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs font-mono space-y-1.5'>
                      <p className='font-sans font-semibold text-emerald-800 dark:text-emerald-200'>
                        Gửi 2 thông tin dưới đây cho @{created.username} qua kênh liên lạc riêng (KHÔNG gửi chung với tệp Private Key):
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
                      <p className='text-[11px] text-muted-foreground font-sans'>
                        Hạn sử dụng mật khẩu tạm: {claimedSecrets.temp_password_expires_at} · Thành viên bắt buộc đổi mật khẩu mới ngay ở lần đăng nhập đầu tiên.
                      </p>
                    </div>
                  )}
                </Card>
              </div>
            ) : (
              <Card className='space-y-2 p-4 text-sm'>
                <p className='font-semibold'>Xem lại thông tin cấp phát quyền truy cập SSH</p>
                <p>
                  {fullName} · @{username} · {effectiveEmail} · {role}
                </p>
                <p>
                  Host: <strong>{serverId}</strong> · Workspace: <code>{workspaceName || `ws-${username}`}</code>
                </p>
                <p>
                  Storage: {assignedPools.find((pool) => pool.id === storagePoolId)?.label || 'Pool mặc định'} ·{' '}
                  {storageQuotaGb} GB · {mountPath} · {backupPolicy}
                </p>
                <p>
                  CPU: {cpuLimitCores} cores · RAM: {ramLimitGb} GB · GPU: {selectedGpu}
                </p>
                <p>
                  Chế độ khóa SSH:{' '}
                  {autoGenerateSsh ? (
                    <span className='font-medium text-emerald-700 dark:text-emerald-300'>
                      Hệ thống tự động tạo cặp khóa Ed25519 (mã hóa Passphrase) + Mật khẩu tạm lần đầu
                    </span>
                  ) : sshPublicKey.trim() ? (
                    <code className='text-xs text-emerald-600'>{sshPublicKey.trim().slice(0, 44)}...</code>
                  ) : (
                    <span className='text-amber-600'>Chưa nhập khóa</span>
                  )}
                </p>
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
              disabled={step === 1 && (!fullName.trim() || !usernameIsValid || !effectiveEmail.includes('@'))}
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
                storageQuotaGb < 1 ||
                cpuLimitCores < 1 ||
                ramLimitGb < 1
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
                setCreated(null);
                setStep(1);
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
