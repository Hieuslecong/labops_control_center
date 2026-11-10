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
  const [sshKeyLabel, setSshKeyLabel] = useState('Laptop cá nhân');
  const [sshPublicKey, setSshPublicKey] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<{
    username: string;
    status: string;
    provisioningState: string;
    sshPort?: number | null;
    sshIp?: string | null;
    sshFingerprint?: string | null;
  } | null>(null);

  const usernameIsValid = validUsername(username);
  const assignedPools = storagePools.filter((pool) => pool.serverId === serverId);
  const assignedGpus = gpus.filter((gpu) => gpu.serverId === serverId);

  const createAccessRequest = async () => {
    if (DEMO_MODE) {
      toast.error('User Management đang ở DEMO; không gửi yêu cầu cấp quyền giả.');
      return;
    }
    setSubmitting(true);
    try {
      const response = await fetch('/api/access/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fullName,
          username,
          email,
          role,
          researchGroup,
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
          sshPublicKey: sshPublicKey.trim() || undefined,
          sshKeyLabel: sshKeyLabel.trim() || 'Khóa SSH mặc định'
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
        username: result.username,
        status: result.status,
        provisioningState: result.provisioningState || result.provisioning_state,
        sshPort: result.sshPort ?? result.ssh_port ?? null,
        sshIp: result.sshIp ?? result.ssh_ip ?? null,
        sshFingerprint: result.sshFingerprint ?? result.ssh_fingerprint ?? null
      });
      setStep(5);
      await useLabOpsStore.getState().fetchAccessMembers();
      toast.success(
        result.sshPort || result.ssh_port
          ? `Đã tạo thành viên @${result.username} và cấp cổng SSH ${result.sshPort ?? result.ssh_port}!`
          : `Đã tạo thành viên @${result.username}!`
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Không thể tạo yêu cầu truy cập.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-2xl max-h-[90vh] overflow-y-auto'>
        <DialogHeader>
          <div className='flex items-center justify-between gap-3'>
            <div>
              <DialogTitle>Cấp quyền Thành viên & Khóa OpenSSH (LabOps V2)</DialogTitle>
              <DialogDescription>
                Tạo tài khoản thành viên, gán máy chủ vật lý, cấu hình khóa công khai OpenSSH và cấp cổng SSH riêng.
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
                  type='email'
                  placeholder='vana@lab.ai.vn'
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
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
                <p className='font-semibold text-sm'>Cấu hình Khóa công khai OpenSSH (Per-Member SSH Key)</p>
                <p className='text-xs text-muted-foreground'>
                  Nhập khóa công khai OpenSSH (<code>ssh-ed25519</code>, <code>ssh-rsa</code>, <code>ecdsa-*</code>)
                  của thành viên để hệ thống tự động cấp cổng SSH riêng và khởi tạo hồ sơ Workspace cô lập.
                </p>
              </div>
            </Card>

            <div className='space-y-1.5'>
              <Label htmlFor='ssh-key-label'>Nhãn thiết bị SSH</Label>
              <Input
                id='ssh-key-label'
                placeholder='VD: MacBook Pro / Laptop Ubuntu'
                value={sshKeyLabel}
                onChange={(e) => setSshKeyLabel(e.target.value)}
              />
            </div>

            <div className='space-y-1.5'>
              <div className='flex items-center justify-between'>
                <Label htmlFor='ssh-public-key'>OpenSSH Public Key (Bắt đầu bằng ssh-ed25519 hoặc ssh-rsa)</Label>
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
                rows={4}
                className='w-full rounded-md border bg-background p-2.5 font-mono text-xs'
                placeholder='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@laptop'
                value={sshPublicKey}
                onChange={(e) => setSshPublicKey(e.target.value)}
              />
              <p className='text-[11px] text-muted-foreground'>
                Lưu ý bảo mật: Chỉ dán <strong>Public Key</strong> (tệp <code>~/.ssh/id_ed25519.pub</code>). Hệ thống
                sẽ từ chối nếu phát hiện Private Key.
              </p>
            </div>
          </div>
        )}

        {step === 5 && (
          <div className='grid gap-3'>
            {created ? (
              <Card className='border-emerald-500/40 bg-emerald-500/5 p-4 space-y-2'>
                <p className='font-semibold text-emerald-700 dark:text-emerald-300'>
                  Đã khởi tạo thành viên @{created.username} thành công!
                </p>
                <p className='text-sm'>
                  Trạng thái: <code>{created.status}</code> · Provisioning: <code>{created.provisioningState}</code>
                </p>
                {created.sshPort && (
                  <div className='rounded-md border bg-background p-3 font-mono text-xs space-y-1'>
                    <p className='font-semibold text-foreground'>Thông tin kết nối SSH đã cấp:</p>
                    <p>
                      Lệnh SSH:{' '}
                      <code className='text-primary font-bold'>
                        ssh -p {created.sshPort} {created.username}@{created.sshIp || '192.168.167.250'}
                      </code>
                    </p>
                    <p>
                      Fingerprint: <code>{created.sshFingerprint}</code>
                    </p>
                  </div>
                )}
              </Card>
            ) : (
              <Card className='space-y-2 p-4 text-sm'>
                <p className='font-semibold'>Xem lại thông tin cấp phát</p>
                <p>
                  {fullName} · @{username} · {email} · {role}
                </p>
                <p>
                  Server: <strong>{serverId}</strong> · Workspace: <code>{workspaceName || `ws-${username}`}</code>
                </p>
                <p>
                  Storage: {assignedPools.find((pool) => pool.id === storagePoolId)?.label || 'Pool mặc định'} ·{' '}
                  {storageQuotaGb} GB · {mountPath} · {backupPolicy}
                </p>
                <p>
                  CPU: {cpuLimitCores} cores · RAM: {ramLimitGb} GB · GPU: {selectedGpu}
                </p>
                <p>
                  Khóa SSH:{' '}
                  {sshPublicKey.trim() ? (
                    <code className='text-xs text-emerald-600'>{sshPublicKey.trim().slice(0, 44)}...</code>
                  ) : (
                    <span className='text-amber-600'>Chưa nhập (Có thể bổ sung sau trong mục Manage)</span>
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
              disabled={step === 1 && (!fullName || !usernameIsValid || !email.includes('@'))}
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
                !fullName ||
                !usernameIsValid ||
                !email.includes('@') ||
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
