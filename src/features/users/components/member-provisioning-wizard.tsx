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
  return /^[a-z_][a-z0-9_-]{2,31}$/.test(value) &&
    !['root', 'admin', 'sshd', 'docker', 'incus', 'sudo', 'wheel'].includes(value);
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
  const [serverId, setServerId] = useState<ServerId>('workstation');
  const [storagePoolId, setStoragePoolId] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  const [mountPath, setMountPath] = useState('/workspace');
  const [storageQuotaGb, setStorageQuotaGb] = useState(100);
  const [backupPolicy, setBackupPolicy] = useState<'DAILY_SNAPSHOT' | 'WEEKLY_SYNC' | 'NONE'>('DAILY_SNAPSHOT');
  const [cpuLimitCores, setCpuLimitCores] = useState(4);
  const [ramLimitGb, setRamLimitGb] = useState(16);
  const [selectedGpu, setSelectedGpu] = useState('NONE');
  const [gpuAllocationMode, setGpuAllocationMode] = useState<GpuAllocationMode>('SHARED');
  const [vramQuotaGb, setVramQuotaGb] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<{ username: string; status: string; provisioningState: string } | null>(null);

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
          expiresAt: expiresAt || null
        })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(result.detail || `Backend returned ${response.status}`);
      }
      setCreated({
        username: result.username,
        status: result.status,
        provisioningState: result.provisioningState
      });
      setStep(5);
      await useLabOpsStore.getState().fetchAccessMembers();
      toast.success('Đã ghi nhận yêu cầu; tài khoản vẫn đang chờ xác minh.');
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
              <DialogTitle>Cấp quyền truy cập LabOps</DialogTitle>
              <DialogDescription>Yêu cầu được ghi nhận trước; chỉ xác minh dịch vụ thật mới kích hoạt quyền.</DialogDescription>
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
              <div className='space-y-1.5'><Label htmlFor='access-full-name'>Tên hiển thị</Label><Input id='access-full-name' value={fullName} onChange={(event) => setFullName(event.target.value)} /></div>
              <div className='space-y-1.5'><Label htmlFor='access-username'>Username</Label><Input id='access-username' autoCapitalize='none' value={username} onChange={(event) => { const value = event.target.value.toLowerCase(); setUsername(value); if (!workspaceName || workspaceName === `ws-${username}`) setWorkspaceName(`ws-${value}`); }} />{username && !usernameIsValid && <p className='text-xs text-destructive'>Dùng 3–32 ký tự a-z, 0-9, _ hoặc -, không dùng tên hệ thống.</p>}</div>
            </div>
            <div className='grid gap-3 sm:grid-cols-2'>
              <div className='space-y-1.5'><Label htmlFor='access-email'>Email nhận lời mời</Label><Input id='access-email' type='email' value={email} onChange={(event) => setEmail(event.target.value)} /></div>
              <div className='space-y-1.5'><Label htmlFor='access-group'>Nhóm nghiên cứu</Label><Input id='access-group' value={researchGroup} onChange={(event) => setResearchGroup(event.target.value)} /></div>
            </div>
            <div className='space-y-1.5'>
              <Label>Vai trò</Label>
              <div className='grid grid-cols-2 gap-2'>
                {(['RESEARCHER', 'ADMIN'] as const).map((value) => (
                  <button key={value} type='button' onClick={() => setRole(value)} className={`rounded-md border p-3 text-left text-sm ${role === value ? 'border-primary bg-primary/10' : 'border-border'}`}>
                    <span className='font-semibold'>{value === 'ADMIN' ? 'Quản trị viên' : 'Nghiên cứu viên'}</span>
                    <span className='mt-1 block text-xs text-muted-foreground'>{value === 'ADMIN' ? 'TTL tối đa 2 giờ, MFA mỗi phiên' : 'TTL tối đa 12 giờ, chỉ workspace được cấp'}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className='space-y-1.5'><Label htmlFor='access-expiry'>Ngày hết hạn (tùy chọn)</Label><Input id='access-expiry' type='date' value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} /></div>
          </div>
        )}

        {step === 2 && (
          <div className='grid gap-4'>
            <div className='space-y-1.5'><Label>Server</Label><div className='grid gap-2 sm:grid-cols-2'>{servers.map((server) => <button key={server.id} type='button' onClick={() => { setServerId(server.id); setStoragePoolId(''); setSelectedGpu('NONE'); }} className={`rounded-md border p-3 text-left ${serverId === server.id ? 'border-primary bg-primary/10' : 'border-border'}`}><span className='block font-semibold'>{server.name}</span><span className='text-xs text-muted-foreground'>{server.status === 'online' ? 'Đang phản hồi' : server.status === 'offline' ? 'Không phản hồi' : 'Chưa xác minh kết nối'}</span></button>)}</div></div>
            <div className='space-y-1.5'><Label htmlFor='workspace-name'>Workspace</Label><Input id='workspace-name' value={workspaceName || `ws-${username}`} onChange={(event) => setWorkspaceName(event.target.value)} /></div>
            <div className='space-y-1.5'><Label htmlFor='storage-pool'>Storage pool</Label><select id='storage-pool' className='h-9 w-full rounded-md border bg-background px-3 text-sm' value={storagePoolId} onChange={(event) => setStoragePoolId(event.target.value)}><option value=''>Backend chọn pool mặc định</option>{assignedPools.map((pool) => <option key={pool.id} value={pool.id}>{pool.label} · còn {pool.freeGb} GB</option>)}</select></div>
            <div className='grid gap-3 sm:grid-cols-2'>
              <div className='space-y-1.5'><Label htmlFor='mount-path'>Mount path</Label><select id='mount-path' className='h-9 w-full rounded-md border bg-background px-3 text-sm' value={mountPath} onChange={(event) => setMountPath(event.target.value)}><option value='/workspace'>/workspace</option><option value={`/home/${username}`}>/home/{username || 'username'}</option><option value='/data'>/data</option></select></div>
              <div className='space-y-1.5'><Label htmlFor='storage-quota'>Storage quota (GB)</Label><Input id='storage-quota' type='number' min={1} value={storageQuotaGb} onChange={(event) => setStorageQuotaGb(Number(event.target.value))} /></div>
            </div>
            <div className='space-y-1.5'><Label htmlFor='backup-policy'>Backup policy</Label><select id='backup-policy' className='h-9 w-full rounded-md border bg-background px-3 text-sm' value={backupPolicy} onChange={(event) => setBackupPolicy(event.target.value as typeof backupPolicy)}><option value='DAILY_SNAPSHOT'>Daily snapshot</option><option value='WEEKLY_SYNC'>Weekly sync</option><option value='NONE'>None</option></select></div>
            <p className='text-xs text-muted-foreground'>{assignedPools.length ? 'Backend sẽ kiểm tra quota trên pool đã quan sát; đây chưa phải cấp phát storage thực tế.' : 'Chưa có storage pool được xác minh; request vẫn có thể được ghi nhận ở trạng thái pending.'}</p>
          </div>
        )}

        {step === 3 && (
          <div className='grid gap-4 sm:grid-cols-2'>
            <div className='space-y-1.5'><Label htmlFor='cpu-limit'>CPU cores</Label><Input id='cpu-limit' type='number' min={1} value={cpuLimitCores} onChange={(event) => setCpuLimitCores(Number(event.target.value))} /></div>
            <div className='space-y-1.5'><Label htmlFor='ram-limit'>RAM (GB)</Label><Input id='ram-limit' type='number' min={1} value={ramLimitGb} onChange={(event) => setRamLimitGb(Number(event.target.value))} /></div>
            <div className='space-y-1.5'><Label htmlFor='gpu-choice'>GPU</Label><select id='gpu-choice' className='h-9 w-full rounded-md border bg-background px-3 text-sm' value={selectedGpu} onChange={(event) => setSelectedGpu(event.target.value)}><option value='NONE'>Chỉ CPU</option>{assignedGpus.map((gpu) => <option key={gpu.id} value={gpu.id}>{gpu.displayLabel} · {gpu.model}</option>)}</select>{!assignedGpus.length && <p className='text-xs text-muted-foreground'>Chưa có GPU telemetry xác minh cho server này.</p>}</div>
            <div className='space-y-1.5'><Label htmlFor='gpu-mode'>GPU mode</Label><select id='gpu-mode' className='h-9 w-full rounded-md border bg-background px-3 text-sm' value={gpuAllocationMode} onChange={(event) => setGpuAllocationMode(event.target.value as GpuAllocationMode)}><option value='SHARED'>Shared</option><option value='EXCLUSIVE'>Exclusive</option></select></div>
            <div className='space-y-1.5 sm:col-span-2'><Label htmlFor='vram-quota'>VRAM quota (GB)</Label><Input id='vram-quota' type='number' min={0} value={selectedGpu === 'NONE' ? 0 : vramQuotaGb} disabled={selectedGpu === 'NONE'} onChange={(event) => setVramQuotaGb(Number(event.target.value))} /></div>
            <Card className='sm:col-span-2 p-3 text-xs text-muted-foreground'>CPU/RAM/GPU là yêu cầu ghi nhận. Backend hiện chưa kết nối bộ cấp phát workspace nên không trừ quota hay báo tài nguyên đã cấp.</Card>
          </div>
        )}

        {step === 4 && (
          <div className='grid gap-3'>
            <Card className='flex gap-3 p-4'><Icons.shield className='size-5 shrink-0 text-primary' /><div><p className='font-semibold'>WireGuard theo thiết bị</p><p className='mt-1 text-sm text-muted-foreground'>Private key phải được tạo trên thiết bị. Gateway, route chỉ tới Teleport Proxy và workspace hiện chưa được xác minh; vì vậy bước đăng ký peer đang chờ.</p></div></Card>
            <Card className='flex gap-3 p-4'><Icons.lock className='size-5 shrink-0 text-primary' /><div><p className='font-semibold'>Teleport Community</p><p className='mt-1 text-sm text-muted-foreground'>Cần cluster HTTPS riêng tư. WebAuthn bắt buộc khi đăng nhập; certificate researcher tối đa 12 giờ, admin tối đa 2 giờ. Chưa gửi lời mời cho đến khi workspace và WireGuard được xác minh.</p></div></Card>
            <p className='text-xs text-muted-foreground'>Không tạo mật khẩu Linux, SSH port, khóa SSH hay file cấu hình chứa private key.</p>
          </div>
        )}

        {step === 5 && (
          <div className='grid gap-3'>
            {created ? (
              <Card className='border-amber-500/40 bg-amber-500/5 p-4'>
                <p className='font-semibold'>Đã ghi nhận yêu cầu của @{created.username}</p>
                <p className='mt-1 text-sm'>Trạng thái: <code>{created.status}</code> · Provisioning: <code>{created.provisioningState}</code></p>
                <p className='mt-2 text-sm text-muted-foreground'>Workspace, peer WireGuard, Teleport MFA và SSH chưa được xác minh. Tài khoản chưa ACTIVE.</p>
              </Card>
            ) : (
              <Card className='space-y-2 p-4 text-sm'><p className='font-semibold'>Xem lại yêu cầu</p><p>{fullName} · @{username} · {email} · {role}</p><p>Server: {serverId} · Workspace: {workspaceName || `ws-${username}`}</p><p>Storage: {assignedPools.find((pool) => pool.id === storagePoolId)?.label || 'Pool mặc định'} · {storageQuotaGb} GB · {mountPath} · {backupPolicy}</p><p>CPU: {cpuLimitCores} cores · RAM: {ramLimitGb} GB · GPU: {selectedGpu}{selectedGpu !== 'NONE' && ` · ${gpuAllocationMode} · ${vramQuotaGb} GB VRAM`}</p><p>Nhóm: {researchGroup || '—'} · Hết hạn: {expiresAt || 'Không đặt'}</p></Card>
            )}
            {!created && <div className='text-sm text-muted-foreground'>Yêu cầu chỉ tạo bản ghi <code>PENDING_PROVISIONING</code>. Backend chưa đánh dấu tài khoản ACTIVE khi hạ tầng chưa xác minh.</div>}
          </div>
        )}

        <DialogFooter className='gap-2 border-t pt-3'>
          {step > 1 && !created && <Button type='button' variant='outline' onClick={() => setStep(step - 1)}>Quay lại</Button>}
          {step < 4 && <Button type='button' disabled={step === 1 && (!fullName || !usernameIsValid || !email.includes('@'))} onClick={() => setStep(step + 1)}>Tiếp tục</Button>}
          {step === 4 && <Button type='button' onClick={() => setStep(5)}>Xem lại yêu cầu</Button>}
          {step === 5 && !created && <Button type='button' disabled={submitting || DEMO_MODE || !fullName || !usernameIsValid || !email.includes('@') || storageQuotaGb < 1 || cpuLimitCores < 1 || ramLimitGb < 1} onClick={createAccessRequest}>{submitting ? 'Đang gửi…' : 'Ghi nhận yêu cầu'}</Button>}
          {step === 5 && created && <Button type='button' onClick={() => onOpenChange(false)}>Đóng</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
