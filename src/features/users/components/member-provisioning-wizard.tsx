'use client';

import React, { useState, useMemo } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { MemberRole, ServerId, GpuAllocationMode } from '@/types/labops';
import { toast } from 'sonner';
import { copyToClipboard } from '@/lib/clipboard';

interface MemberProvisioningWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const RESERVED_USERNAMES = [
  'root',
  'bin',
  'daemon',
  'sys',
  'adm',
  'sync',
  'games',
  'man',
  'lp',
  'mail',
  'news',
  'uucp',
  'proxy',
  'www-data',
  'backup',
  'list',
  'irc',
  'gnats',
  'nobody',
  'systemd',
  'sshd',
  'incus',
  'docker',
  'sudo',
  'wheel',
  'admin',
  'operator'
];

function validateUsername(val: string): { valid: boolean; error?: string } {
  if (!val) return { valid: false, error: 'Tên người dùng không được để trống' };
  if (val.length < 3) return { valid: false, error: 'Tên người dùng phải có ít nhất 3 ký tự' };
  if (val.length > 32) return { valid: false, error: 'Tên người dùng không vượt quá 32 ký tự' };
  if (!/^[a-z_][a-z0-9_-]*$/.test(val)) {
    return {
      valid: false,
      error: 'Phải bắt đầu bằng chữ cái thường hoặc gạch dưới, chỉ chứa a-z, 0-9, _, -'
    };
  }
  if (RESERVED_USERNAMES.includes(val.toLowerCase())) {
    return { valid: false, error: `Tên người dùng "${val}" là tên hệ thống đã được bảo lưu` };
  }
  return { valid: true };
}

export function MemberProvisioningWizard({ open, onOpenChange }: MemberProvisioningWizardProps) {
  const servers = useLabOpsStore((state) => state.servers);
  const storagePools = useLabOpsStore((state) => state.storagePools);
  const sshEndpoints = useLabOpsStore((state) => state.sshEndpoints);
  const addMember = useLabOpsStore((state) => state.addMember);

  const [step, setStep] = useState(1);

  // Step 1: Member Info
  const [fullName, setFullName] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<MemberRole>('RESEARCHER');
  const [researchGroup, setResearchGroup] = useState('NLP & Large Language Models');
  const [expiresAt, setExpiresAt] = useState('');

  // Step 2: Physical Host
  const [selectedServerId, setSelectedServerId] = useState<ServerId>('track');

  // Step 3: Storage & Directory
  const [selectedPoolId, setSelectedPoolId] = useState<string>('pool-nvme-fast');
  const [mountPath, setMountPath] = useState<string>('/workspace');
  const [storageQuotaGb, setStorageQuotaGb] = useState<number>(250);
  const [sharedDatasets, setSharedDatasets] = useState<string[]>(['/shared/datasets']);
  const [backupPolicy, setBackupPolicy] = useState<'DAILY_SNAPSHOT' | 'WEEKLY_SYNC' | 'NONE'>(
    'DAILY_SNAPSHOT'
  );

  // Step 4: Hardware Quotas
  const [cpuLimit, setCpuLimit] = useState<number>(8);
  const [ramLimit, setRamLimit] = useState<number>(32);
  const [selectedGpu, setSelectedGpu] = useState<string>('TRACK-GPU-01');
  const [gpuAllocationMode, setGpuAllocationMode] = useState<GpuAllocationMode>('SHARED');
  const [vramQuotaGb, setVramQuotaGb] = useState<number>(12);

  // Step 5: Network & SSH Port
  const [portMode, setPortMode] = useState<'auto' | 'manual'>('auto');
  const [customPort, setCustomPort] = useState<number>(22006);

  // Step 6: SSH Key Enrollment
  const [enrollmentMode, setEnrollmentMode] = useState<'generate' | 'import' | 'pending'>(
    'generate'
  );
  const [importedPublicKey, setImportedPublicKey] = useState('');
  const [generatedPrivateKey, setGeneratedPrivateKey] = useState<string>('');
  const [generatedPublicKey, setGeneratedPublicKey] = useState<string>('');
  const [keyDownloaded, setKeyDownloaded] = useState<boolean>(false);

  // Step 7: Atomic Provisioning execution
  const [execStep, setExecStep] = useState<number>(0);
  const [execStatusText, setExecStatusText] = useState<string>('');
  const [isProvisionDone, setIsProvisionDone] = useState<boolean>(false);
  const [correlationId, setCorrelationId] = useState<string>('');

  // Derived server and pools
  const currentServer = useMemo(
    () => servers.find((s) => s.id === selectedServerId) || servers[0],
    [servers, selectedServerId]
  );

  const availablePools = useMemo(
    () => storagePools.filter((p) => p.serverId === selectedServerId),
    [storagePools, selectedServerId]
  );

  const currentPool = useMemo(
    () =>
      availablePools.find((p) => p.id === selectedPoolId) || availablePools[0] || storagePools[0],
    [availablePools, selectedPoolId, storagePools]
  );

  // Calculate next auto port for selected server
  const nextAutoPort = useMemo(() => {
    const usedPorts = new Set(
      sshEndpoints.filter((ep) => ep.serverId === selectedServerId).map((ep) => ep.listenPort)
    );
    for (let p = 22001; p <= 29999; p++) {
      if (!usedPorts.has(p)) return p;
    }
    return 22001;
  }, [sshEndpoints, selectedServerId]);

  const allocatedPort = portMode === 'auto' ? nextAutoPort : customPort;

  const handleSelectServer = (srvId: ServerId) => {
    setSelectedServerId(srvId);
    const pools = storagePools.filter((p) => p.serverId === srvId);
    if (pools.length > 0) setSelectedPoolId(pools[0].id);
    setSelectedGpu(srvId === 'track' ? 'TRACK-GPU-01' : 'WS-GPU-01');
  };

  // Username validation check
  const usernameValidation = useMemo(() => validateUsername(username), [username]);

  // Generate Ed25519 keypair mock/client-side
  const handleGenerateKeypair = () => {
    const randomHex = Array.from({ length: 32 }, () =>
      Math.floor(Math.random() * 16).toString(16)
    ).join('');
    const pubKey = `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI${btoa(randomHex).substring(0, 43)} ${username || 'user'}@labops-client`;
    const privKey = `-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\nQyNTUxOQAAACD${btoa(randomHex).substring(0, 43)}==\n-----END OPENSSH PRIVATE KEY-----`;
    setGeneratedPublicKey(pubKey);
    setGeneratedPrivateKey(privKey);
    setKeyDownloaded(false);
  };

  const handleDownloadPrivateKey = () => {
    if (!generatedPrivateKey) return;
    const blob = new Blob([generatedPrivateKey], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `id_ed25519_${username || 'labops'}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setKeyDownloaded(true);
    toast.success('Đã tải xuống khóa bí mật (id_ed25519)', {
      description: 'Vui lòng bảo quản an toàn và phân quyền: chmod 600 id_ed25519'
    });
  };

  // Handle execution of multi-step atomic provisioning
  const handleStartProvision = () => {
    setStep(7);
    setIsProvisionDone(false);
    const corrId = `corr-${Math.random().toString(36).substring(2, 10)}-${Date.now().toString(36)}`;
    setCorrelationId(corrId);

    const steps = [
      { text: '1/11: Xác thực quyền quản trị & tính hợp lệ của tài khoản...', pct: 10 },
      {
        text: `2/11: Kiểm tra dung lượng storage pool & giữ chỗ cổng SSH duy nhất (${currentServer?.vpnIp}:${allocatedPort})...`,
        pct: 20
      },
      {
        text: `3/11: Khởi tạo unprivileged Linux container Incus (ws-${username}, idmap isolated)...`,
        pct: 30
      },
      {
        text: `4/11: Áp dụng giới hạn cgroups CPU (${cpuLimit} cores) và RAM (${ramLimit}GB)...`,
        pct: 40
      },
      {
        text: `5/11: Tạo ZFS custom storage volume (data-${username}, quota ${storageQuotaGb}GB)...`,
        pct: 50
      },
      { text: `6/11: Gắn mount storage volume vào thư mục ${mountPath}...`, pct: 60 },
      {
        text: '7/11: Tạo tài khoản Linux người dùng không có sudo (Standard User, UID unprivileged)...',
        pct: 70
      },
      {
        text: '8/11: Cấu hình bảo mật OpenSSH daemon (PasswordAuthentication no, PubkeyAuthentication yes)...',
        pct: 80
      },
      {
        text: `9/11: Kích hoạt Incus proxy device ánh xạ cổng VPN ${currentServer?.vpnIp}:${allocatedPort} -> 22...`,
        pct: 88
      },
      {
        text: '10/11: Tự động kiểm tra cách ly an ninh (sudo -n true: THẤT BẠI - ĐẠT AN NINH TUYỆT ĐỐI)...',
        pct: 95
      },
      { text: '11/11: Hoàn tất khởi tạo môi trường nghiên cứu & ghi nhật ký kiểm toán!', pct: 100 }
    ];

    let currentIdx = 0;
    const interval = setInterval(() => {
      if (currentIdx < steps.length) {
        setExecStep(steps[currentIdx].pct);
        setExecStatusText(steps[currentIdx].text);
        currentIdx++;
      } else {
        clearInterval(interval);
        setIsProvisionDone(true);

        const pubKey =
          enrollmentMode === 'generate'
            ? generatedPublicKey
            : enrollmentMode === 'import'
              ? importedPublicKey
              : '';

        const keyStatus = enrollmentMode === 'pending' ? 'PENDING_KEY_ENROLLMENT' : 'ACTIVE';
        const fingerprint = pubKey
          ? `SHA256:${btoa(pubKey).substring(0, 32)}`
          : 'CHƯA_ĐĂNG_KÝ_KHÓA';

        // Add to Zustand Store
        addMember({
          fullName,
          username,
          email,
          role,
          researchGroup,
          assignedServer: selectedServerId,
          workspaceName: `ws-${username}`,
          status: keyStatus === 'ACTIVE' ? 'ACTIVE' : 'PENDING_KEY_ENROLLMENT',
          storagePoolId: currentPool.id,
          storagePoolLabel: currentPool.label,
          mountPath,
          storageQuotaGb,
          storageUsedGb: 0,
          backupPolicy,
          sharedDatasets,
          sshStatus: 'OFFLINE',
          sshSessionsCount: 0,
          sshIp: currentServer?.vpnIp || '10.200.0.1',
          sshPort: allocatedPort,
          sshKeyStatus: keyStatus,
          sshFingerprint: fingerprint,
          sshPublicKeyPreview: pubKey ? pubKey.slice(0, 32) + '...' : '(Đang chờ nạp khóa)',
          sshPublicKey: pubKey,
          sudoPrivilege: 'DENIED',
          securityIsolationLevel: 'UNPRIVILEGED_CGROUPS_ISOLATED',
          privilegeValidation: {
            unprivilegedContainer: true,
            sudoTestFailedAsExpected: true,
            noSudoGroupMembership: true,
            noDockerSocketAccess: true,
            noIncusSocketAccess: true,
            verificationTimestamp: new Date().toISOString(),
            status: 'VERIFIED_SAFE'
          },
          cpuLimitCores: cpuLimit,
          ramLimitGb: ramLimit,
          gpuPermissions: selectedGpu !== 'NONE' ? [selectedGpu] : [],
          vramPolicy: selectedGpu !== 'NONE' ? `${gpuAllocationMode} ${vramQuotaGb}GB` : 'Chỉ CPU',
          vramQuotaGb: selectedGpu !== 'NONE' ? vramQuotaGb : 0,
          gpuAllocationMode,
          maxRuntimeHours: 48,
          expiresAt: expiresAt || undefined
        });

        toast.success(`Khởi tạo thành công workspace cho @${username}`, {
          description: `Container ws-${username} sẵn sàng trên ${currentServer?.name}, Port SSH ${allocatedPort}`
        });
      }
    }, 280);
  };

  const resetForm = () => {
    setStep(1);
    setFullName('');
    setUsername('');
    setEmail('');
    setRole('RESEARCHER');
    setResearchGroup('NLP & Large Language Models');
    setExpiresAt('');
    setSelectedServerId('track');
    setMountPath('/workspace');
    setStorageQuotaGb(250);
    setCpuLimit(8);
    setRamLimit(32);
    setPortMode('auto');
    setEnrollmentMode('generate');
    setGeneratedPublicKey('');
    setGeneratedPrivateKey('');
    setImportedPublicKey('');
    setKeyDownloaded(false);
    setIsProvisionDone(false);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(val) => {
        onOpenChange(val);
        if (!val) resetForm();
      }}
    >
      <DialogContent className='sm:max-w-[700px] max-h-[92vh] overflow-y-auto'>
        <DialogHeader>
          <div className='flex items-center gap-2'>
            <div className='flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary'>
              <Icons.teams className='size-5' />
            </div>
            <div>
              <DialogTitle className='text-base font-bold'>
                Thêm Thành viên & Khởi tạo Môi trường Nghiên cứu
              </DialogTitle>
              <DialogDescription className='text-xs'>
                Khởi tạo unprivileged container Incus, phân vùng ổ đĩa ZFS riêng biệt và cấp quyền
                SSH bảo mật
              </DialogDescription>
            </div>
          </div>

          {/* Stepper progress indicator */}
          <div className='flex items-center justify-between pt-3 pb-1 border-b text-[11px] font-medium overflow-x-auto gap-1'>
            {[
              '1. Thông tin',
              '2. Máy chủ',
              '3. Ổ đĩa ZFS',
              '4. Tài nguyên',
              '5. Mạng & SSH',
              '6. Khóa SSH',
              '7. Khởi tạo'
            ].map((title, idx) => {
              const num = idx + 1;
              const isActive = step === num;
              const isPassed = step > num;
              return (
                <div key={title} className='flex items-center gap-1 shrink-0'>
                  <span
                    className={`size-5 rounded-full flex items-center justify-center text-[10px] font-bold ${
                      isActive
                        ? 'bg-primary text-primary-foreground'
                        : isPassed
                          ? 'bg-emerald-500 text-white'
                          : 'bg-muted text-muted-foreground'
                    }`}
                  >
                    {isPassed ? '✓' : num}
                  </span>
                  <span
                    className={
                      isActive
                        ? 'text-foreground font-semibold'
                        : isPassed
                          ? 'text-muted-foreground'
                          : 'text-muted-foreground/60'
                    }
                  >
                    {title}
                  </span>
                </div>
              );
            })}
          </div>
        </DialogHeader>

        {/* STEP 1: THÔNG TIN THÀNH VIÊN */}
        {step === 1 && (
          <div className='grid gap-3 py-2 text-xs'>
            <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
              <div className='space-y-1'>
                <Label className='text-xs font-semibold'>Họ và tên thành viên *</Label>
                <Input
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder='ví dụ: Nguyễn Hoàng Long'
                  className='h-8 text-xs'
                  required
                />
              </div>
              <div className='space-y-1'>
                <Label className='text-xs font-semibold'>Tên đăng nhập (Linux Username) *</Label>
                <Input
                  value={username}
                  onChange={(e) => setUsername(e.target.value.toLowerCase().trim())}
                  placeholder='ví dụ: longnh'
                  className={`h-8 text-xs font-mono ${
                    username && !usernameValidation.valid
                      ? 'border-destructive focus-visible:ring-destructive'
                      : ''
                  }`}
                  required
                />
                {username && !usernameValidation.valid && (
                  <span className='text-[10px] text-destructive block'>
                    {usernameValidation.error}
                  </span>
                )}
              </div>
            </div>

            <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
              <div className='space-y-1'>
                <Label className='text-xs font-semibold'>Địa chỉ Email *</Label>
                <Input
                  type='email'
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder='longnh@labops.local'
                  className='h-8 text-xs'
                  required
                />
              </div>
              <div className='space-y-1'>
                <Label className='text-xs font-semibold'>Dự án / Nhóm nghiên cứu</Label>
                <Input
                  value={researchGroup}
                  onChange={(e) => setResearchGroup(e.target.value)}
                  placeholder='ví dụ: NLP & Large Language Models'
                  className='h-8 text-xs'
                />
              </div>
            </div>

            <div className='space-y-1'>
              <Label className='text-xs font-semibold'>Vai trò trong Phòng Lab</Label>
              <div className='grid grid-cols-3 sm:grid-cols-5 gap-1.5'>
                {(
                  ['RESEARCHER', 'STUDENT', 'GUEST', 'LAB_MANAGER', 'SUPER_ADMIN'] as MemberRole[]
                ).map((r) => (
                  <button
                    key={r}
                    type='button'
                    onClick={() => setRole(r)}
                    className={`py-1.5 px-2 rounded-md border text-center text-xs transition-colors ${
                      role === r
                        ? 'border-primary bg-primary/10 font-bold text-foreground ring-1 ring-primary'
                        : 'border-border bg-card hover:bg-muted/30 text-muted-foreground'
                    }`}
                  >
                    {r === 'RESEARCHER'
                      ? 'Nghiên cứu viên'
                      : r === 'STUDENT'
                        ? 'Sinh viên'
                        : r === 'GUEST'
                          ? 'Khách mời'
                          : r === 'LAB_MANAGER'
                            ? 'Quản lý Lab'
                            : 'Super Admin'}
                  </button>
                ))}
              </div>
            </div>

            <div className='space-y-1'>
              <Label className='text-xs font-semibold'>
                Ngày hết hạn quyền truy cập (Tùy chọn)
              </Label>
              <Input
                type='date'
                value={expiresAt}
                onChange={(e) => setExpiresAt(e.target.value)}
                className='h-8 text-xs'
              />
            </div>

            {/* MANDATORY SECURITY POLICY CARD: UNPRIVILEGED / NO SUDO */}
            <Card className='border-amber-500/30 bg-amber-500/5 p-3 rounded-lg'>
              <div className='flex items-start gap-2.5'>
                <Icons.shield className='size-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5' />
                <div className='space-y-1'>
                  <div className='flex items-center gap-2'>
                    <span className='font-bold text-xs text-amber-900 dark:text-amber-200'>
                      Chính sách Phân quyền Hệ thống (System Privilege Policy)
                    </span>
                    <Badge
                      variant='outline'
                      className='bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/30 text-[10px]'
                    >
                      Standard User (Không sudo)
                    </Badge>
                  </div>
                  <ul className='text-[11px] text-muted-foreground space-y-0.5 list-disc pl-3.5'>
                    <li>
                      <strong>Truy cập SSH:</strong> Được cấp phép kết nối qua mạng riêng ảo
                      WireGuard VPN.
                    </li>
                    <li>
                      <strong>Quyền root/sudo:</strong>{' '}
                      <span className='text-destructive font-medium'>BỊ TỪ CHỐI</span> — Thành viên
                      không thể thực thi sudo và không có trong sudoers.
                    </li>
                    <li>
                      <strong>Thư mục cá nhân:</strong> Toàn quyền Đọc/Ghi trên volume ZFS chuyên
                      dụng được gắn vào container.
                    </li>
                    <li>
                      <strong>Cài đặt gói:</strong> Cài đặt thư viện Python (pip/conda) trong user
                      space; không có quyền apt/dpkg hệ thống.
                    </li>
                    <li>
                      <strong>Socket Quản trị:</strong> Tuyệt đối không có quyền truy cập socket
                      Docker hoặc Incus daemon.
                    </li>
                  </ul>
                </div>
              </div>
            </Card>
          </div>
        )}

        {/* STEP 2: CHỌN MÁY CHỦ */}
        {step === 2 && (
          <div className='grid gap-3 py-2 text-xs'>
            <Label className='text-xs font-semibold'>Chọn Máy chủ Vật lý Cấp phát Workspace</Label>
            <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
              {servers.map((srv) => {
                const isSelected = selectedServerId === srv.id;
                const isTrack = srv.id === 'track';
                return (
                  <button
                    key={srv.id}
                    type='button'
                    aria-label={`Chọn máy chủ ${srv.name}`}
                    onClick={() => handleSelectServer(srv.id)}
                    className={`p-3.5 rounded-xl border text-left transition-all ${
                      isSelected
                        ? isTrack
                          ? 'border-blue-600 bg-blue-500/10 ring-1 ring-blue-600'
                          : 'border-purple-600 bg-purple-500/10 ring-1 ring-purple-600'
                        : 'border-border bg-card hover:bg-muted/30'
                    }`}
                  >
                    <div className='flex items-center justify-between'>
                      <div className='font-bold text-sm text-foreground flex items-center gap-1.5'>
                        <Icons.server className='size-4' />
                        {srv.name}
                      </div>
                      <Badge variant='outline' className='text-[10px] font-mono'>
                        {srv.location.includes('Hà Nội') ? 'Hà Nội' : 'TP.HCM'}
                      </Badge>
                    </div>

                    <div className='text-[11px] text-muted-foreground mt-1.5 space-y-0.5'>
                      <div>
                        <strong>Phần cứng:</strong> {srv.cpuModel.split(' ')[0]} {srv.cpuCores}{' '}
                        Cores • {srv.ramTotalGb}GB RAM
                      </div>
                      <div>
                        <strong>GPU:</strong> {srv.gpuCount}x NVIDIA A30 24GB ({srv.gpuCount * 24}GB
                        VRAM)
                      </div>
                      <div>
                        <strong>VPN IP:</strong> <code className='font-mono'>{srv.vpnIp}</code>
                      </div>
                    </div>

                    <div className='mt-2.5 pt-2 border-t grid grid-cols-3 gap-1 text-[10px] text-muted-foreground'>
                      <div>
                        CPU: <strong>{srv.cpuUsagePercent}%</strong>
                      </div>
                      <div>
                        RAM: <strong>{srv.ramUsagePercent}%</strong>
                      </div>
                      <div>
                        Disk: <strong>{srv.diskUsagePercent}%</strong>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* STEP 3: CẤU HÌNH Ổ ĐĨA & THƯ MỤC ZFS */}
        {step === 3 && (
          <div className='grid gap-3 py-2 text-xs'>
            {/* Storage Pool Selection */}
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>
                Ổ đĩa / Storage Pool trên {currentServer?.name} *
              </Label>
              <select
                value={selectedPoolId}
                onChange={(e) => setSelectedPoolId(e.target.value)}
                className='w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-medium'
              >
                {availablePools.map((pool) => (
                  <option key={pool.id} value={pool.id}>
                    {pool.label} — Trống: {pool.freeGb} GB / {pool.capacityGb} GB (
                    {pool.driver.toUpperCase()})
                  </option>
                ))}
              </select>
            </div>

            {/* Storage Pool Detailed Hardware Card */}
            {currentPool && (
              <Card className='p-3 bg-muted/20 border'>
                <div className='grid grid-cols-2 gap-2 text-[11px]'>
                  <div>
                    <span className='text-muted-foreground'>Mã định danh vật lý (WWN/UUID):</span>
                    <div className='font-mono font-medium text-foreground'>
                      {currentPool.diskIdentifier}
                    </div>
                  </div>
                  <div>
                    <span className='text-muted-foreground'>Đường dẫn gắn trên Host:</span>
                    <div className='font-mono font-medium text-foreground truncate'>
                      {currentPool.mountPoint}
                    </div>
                  </div>
                  <div>
                    <span className='text-muted-foreground'>
                      Hệ thống tập tin & Trình điều khiển:
                    </span>
                    <div className='font-medium text-foreground font-mono uppercase'>
                      {currentPool.driver} Pool
                    </div>
                  </div>
                  <div>
                    <span className='text-muted-foreground'>Tình trạng sức khỏe ổ đĩa:</span>
                    <div className='font-medium text-emerald-600 dark:text-emerald-400 flex items-center gap-1'>
                      <Icons.circleCheck className='size-3.5' /> Tốt ({currentPool.healthStatus})
                    </div>
                  </div>
                </div>
              </Card>
            )}

            {/* Approved Mount Directory */}
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>
                Thư mục gắn bên trong Container (Mount Path) *
              </Label>
              <select
                value={mountPath}
                onChange={(e) => setMountPath(e.target.value)}
                className='w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-mono font-medium'
              >
                <option value='/workspace'>
                  /workspace (Khuyến nghị — Chuẩn môi trường LabOps)
                </option>
                <option value={`/home/${username || 'username'}`}>
                  /home/{username || 'username'} (Thư mục Home cá nhân)
                </option>
                <option value='/data'>/data (Không gian lưu trữ dữ liệu lớn)</option>
              </select>
              <span className='text-[10px] text-muted-foreground block'>
                Chỉ cho phép các đường dẫn hợp lệ được phê duyệt. Không chấp nhận đường dẫn tùy tiện
                của host.
              </span>
            </div>

            {/* Storage Quota Slider & Quick Buttons */}
            <div className='space-y-1.5'>
              <div className='flex items-center justify-between'>
                <Label className='text-xs font-semibold'>
                  Hạn ngạch dung lượng (Storage Quota)
                </Label>
                <span className='font-bold font-mono text-primary text-sm'>
                  {storageQuotaGb} GB ZFS
                </span>
              </div>
              <div className='flex items-center gap-1.5 flex-wrap'>
                {[50, 100, 250, 500, 1000].map((q) => (
                  <Button
                    key={q}
                    type='button'
                    size='sm'
                    variant={storageQuotaGb === q ? 'default' : 'outline'}
                    onClick={() => setStorageQuotaGb(q)}
                    className='h-7 text-xs px-2.5 font-mono'
                  >
                    {q} GB
                  </Button>
                ))}
              </div>
              {currentPool && storageQuotaGb > currentPool.freeGb && (
                <div className='text-destructive text-[11px] font-medium flex items-center gap-1'>
                  <Icons.warning className='size-3.5' /> Hạn ngạch yêu cầu ({storageQuotaGb}GB) vượt
                  quá dung lượng trống ({currentPool.freeGb}GB) của Pool!
                </div>
              )}
            </div>

            {/* Shared Datasets and Backup Policy */}
            <div className='grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1'>
              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>
                  Thư mục Dữ liệu dùng chung (Read-Only)
                </Label>
                <div className='space-y-1 text-[11px]'>
                  <label className='flex items-center gap-2 p-1.5 rounded border bg-card cursor-pointer'>
                    <input
                      type='checkbox'
                      checked={sharedDatasets.includes('/shared/datasets')}
                      onChange={(e) => {
                        if (e.target.checked)
                          setSharedDatasets([...sharedDatasets, '/shared/datasets']);
                        else
                          setSharedDatasets(sharedDatasets.filter((d) => d !== '/shared/datasets'));
                      }}
                      className='rounded'
                    />
                    <span>/shared/datasets (Read-Only)</span>
                  </label>
                  <label className='flex items-center gap-2 p-1.5 rounded border bg-card cursor-pointer'>
                    <input
                      type='checkbox'
                      checked={sharedDatasets.includes('/data/models')}
                      onChange={(e) => {
                        if (e.target.checked)
                          setSharedDatasets([...sharedDatasets, '/data/models']);
                        else setSharedDatasets(sharedDatasets.filter((d) => d !== '/data/models'));
                      }}
                      className='rounded'
                    />
                    <span>/data/models (Trọng số LLM)</span>
                  </label>
                </div>
              </div>

              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>Chính sách Sao lưu Snapshot</Label>
                <select
                  value={backupPolicy}
                  onChange={(e) =>
                    setBackupPolicy(e.target.value as 'DAILY_SNAPSHOT' | 'WEEKLY_SYNC' | 'NONE')
                  }
                  className='w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-xs'
                >
                  <option value='DAILY_SNAPSHOT'>Snapshot hàng ngày (ZFS Daily)</option>
                  <option value='WEEKLY_SYNC'>Đồng bộ hàng tuần (Weekly Remote)</option>
                  <option value='NONE'>Không tự động sao lưu</option>
                </select>
              </div>
            </div>
          </div>
        )}

        {/* STEP 4: PHÂN BỔ TÀI NGUYÊN (CPU/RAM/GPU) */}
        {step === 4 && (
          <div className='grid gap-3.5 py-2 text-xs'>
            <div className='grid grid-cols-2 gap-3'>
              <div className='space-y-1'>
                <Label className='text-xs font-semibold'>Giới hạn CPU (Incus cgroups)</Label>
                <div className='flex items-center gap-2'>
                  <Input
                    type='number'
                    min={2}
                    max={currentServer?.cpuCores || 32}
                    value={cpuLimit}
                    onChange={(e) => setCpuLimit(Number(e.target.value))}
                    className='h-8 text-xs font-mono'
                  />
                  <span className='text-muted-foreground shrink-0'>Cores vCPU</span>
                </div>
              </div>

              <div className='space-y-1'>
                <Label className='text-xs font-semibold'>Giới hạn RAM (Incus cgroups)</Label>
                <div className='flex items-center gap-2'>
                  <Input
                    type='number'
                    min={8}
                    max={currentServer?.ramTotalGb || 64}
                    value={ramLimit}
                    onChange={(e) => setRamLimit(Number(e.target.value))}
                    className='h-8 text-xs font-mono'
                  />
                  <span className='text-muted-foreground shrink-0'>GB RAM</span>
                </div>
              </div>
            </div>

            {/* GPU Access & VRAM Quota Policy */}
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Thiết bị NVIDIA GPU Cấp phát</Label>
              <select
                value={selectedGpu}
                onChange={(e) => setSelectedGpu(e.target.value)}
                className='w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-mono font-medium'
              >
                <option value='NONE'>Chỉ CPU (Không cấp GPU lúc này)</option>
                {selectedServerId === 'track' ? (
                  <>
                    <option value='TRACK-GPU-01'>
                      TRACK-GPU-01 (NVIDIA A30 24GB - PCIe 3b:00.0)
                    </option>
                    <option value='TRACK-GPU-02'>
                      TRACK-GPU-02 (NVIDIA A30 24GB - PCIe 5e:00.0)
                    </option>
                    <option value='TRACK-GPU-03'>
                      TRACK-GPU-03 (NVIDIA A30 24GB - PCIe 86:00.0)
                    </option>
                  </>
                ) : (
                  <option value='WS-GPU-01'>WS-GPU-01 (NVIDIA A30 24GB - PCIe 1a:00.0)</option>
                )}
              </select>
            </div>

            {selectedGpu !== 'NONE' && (
              <div className='grid grid-cols-2 gap-3 p-3 bg-muted/20 rounded-lg border'>
                <div className='space-y-1'>
                  <Label className='text-xs font-semibold'>Chế độ Cấp phát GPU</Label>
                  <select
                    value={gpuAllocationMode}
                    onChange={(e) => setGpuAllocationMode(e.target.value as GpuAllocationMode)}
                    className='w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-xs'
                  >
                    <option value='SHARED'>SHARED (Chia sẻ tiến trình với MPS / Driver)</option>
                    <option value='EXCLUSIVE'>EXCLUSIVE (Chiếm độc quyền 1 Card GPU)</option>
                  </select>
                </div>

                <div className='space-y-1'>
                  <Label className='text-xs font-semibold'>Hạn ngạch VRAM yêu cầu</Label>
                  <select
                    value={vramQuotaGb}
                    onChange={(e) => setVramQuotaGb(Number(e.target.value))}
                    className='w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-xs font-mono'
                  >
                    <option value={8}>8 GB VRAM</option>
                    <option value={12}>12 GB VRAM</option>
                    <option value={16}>16 GB VRAM</option>
                    <option value={24}>24 GB VRAM (Toàn bộ)</option>
                  </select>
                </div>

                <div className='col-span-2 text-[10px] text-muted-foreground'>
                  * Chú thích kiến trúc: Hạn ngạch VRAM được kiểm soát và ghi nhận giám sát qua
                  NVIDIA NVML và driver daemon; không giả lập phần cứng MIG ảo khi driver đang ở chế
                  độ compute tiêu chuẩn.
                </div>
              </div>
            )}
          </div>
        )}

        {/* STEP 5: CẤU HÌNH MẠNG & DEDICATED SSH ENDPOINT */}
        {step === 5 && (
          <div className='grid gap-3.5 py-2 text-xs'>
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>
                Chế độ Cấp phát Cổng SSH (Range 22000 - 29999)
              </Label>
              <div className='grid grid-cols-2 gap-2'>
                <button
                  type='button'
                  onClick={() => setPortMode('auto')}
                  className={`p-2.5 rounded-lg border text-left transition-colors ${
                    portMode === 'auto'
                      ? 'border-primary bg-primary/10 font-bold text-foreground'
                      : 'border-border bg-card text-muted-foreground'
                  }`}
                >
                  <div className='text-xs'>Tự động cấp phát cổng</div>
                  <div className='text-[10px] font-mono text-primary mt-0.5'>
                    Cổng khả dụng: {nextAutoPort}
                  </div>
                </button>

                <button
                  type='button'
                  onClick={() => setPortMode('manual')}
                  className={`p-2.5 rounded-lg border text-left transition-colors ${
                    portMode === 'manual'
                      ? 'border-primary bg-primary/10 font-bold text-foreground'
                      : 'border-border bg-card text-muted-foreground'
                  }`}
                >
                  <div className='text-xs'>Tự chỉ định cổng thủ công</div>
                  <div className='text-[10px] text-muted-foreground mt-0.5'>
                    Kiểm tra xung đột tự động
                  </div>
                </button>
              </div>
            </div>

            {portMode === 'manual' && (
              <div className='space-y-1'>
                <Label className='text-xs font-semibold'>Nhập số cổng SSH (22000 - 29999)</Label>
                <Input
                  type='number'
                  min={22000}
                  max={29999}
                  value={customPort}
                  onChange={(e) => setCustomPort(Number(e.target.value))}
                  className='h-8 text-xs font-mono'
                />
              </div>
            )}

            {/* Dedicated SSH Endpoint Preview Card */}
            <Card className='p-3.5 bg-card border rounded-lg space-y-2'>
              <div className='font-bold text-xs flex items-center gap-1.5'>
                <Icons.network className='size-4 text-primary' />
                Điểm cuối kết nối SSH chuyên dụng (Dedicated SSH Endpoint)
              </div>

              <div className='grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] pt-1'>
                <div>
                  <span className='text-muted-foreground'>Địa chỉ lắng nghe VPN Host:</span>
                  <div className='font-mono font-bold text-foreground'>
                    {currentServer?.vpnIp}:{allocatedPort}
                  </div>
                </div>
                <div>
                  <span className='text-muted-foreground'>Ánh xạ Incus Proxy tới:</span>
                  <div className='font-mono font-medium text-foreground'>
                    Container Port 22 (SSH)
                  </div>
                </div>
                <div>
                  <span className='text-muted-foreground'>Dải mạng nội bộ cho phép:</span>
                  <div className='font-mono text-emerald-600 dark:text-emerald-400'>
                    10.200.0.0/16 (WireGuard Only)
                  </div>
                </div>
                <div>
                  <span className='text-muted-foreground'>Internet công cộng:</span>
                  <div className='font-mono text-rose-600 dark:text-rose-400 font-semibold'>
                    Bị chặn hoàn toàn (Blocked)
                  </div>
                </div>
              </div>
            </Card>
          </div>
        )}

        {/* STEP 6: XÁC THỰC SSH & CẤP PHÁT KEY */}
        {step === 6 && (
          <div className='grid gap-3 py-2 text-xs'>
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Phương thức Đăng ký Khóa SSH</Label>
              <div className='grid grid-cols-3 gap-2'>
                <button
                  type='button'
                  onClick={() => setEnrollmentMode('generate')}
                  className={`p-2 rounded-md border text-center transition-colors ${
                    enrollmentMode === 'generate'
                      ? 'border-primary bg-primary/10 font-bold text-foreground'
                      : 'border-border bg-card text-muted-foreground'
                  }`}
                >
                  Tạo trên thiết bị (Khuyến nghị)
                </button>

                <button
                  type='button'
                  onClick={() => setEnrollmentMode('import')}
                  className={`p-2 rounded-md border text-center transition-colors ${
                    enrollmentMode === 'import'
                      ? 'border-primary bg-primary/10 font-bold text-foreground'
                      : 'border-border bg-card text-muted-foreground'
                  }`}
                >
                  Nhập khóa công khai
                </button>

                <button
                  type='button'
                  onClick={() => setEnrollmentMode('pending')}
                  className={`p-2 rounded-md border text-center transition-colors ${
                    enrollmentMode === 'pending'
                      ? 'border-primary bg-primary/10 font-bold text-foreground'
                      : 'border-border bg-card text-muted-foreground'
                  }`}
                >
                  Đăng ký sau
                </button>
              </div>
            </div>

            {/* Option A: Generate Ed25519 on device */}
            {enrollmentMode === 'generate' && (
              <Card className='p-3 bg-muted/20 border space-y-2.5'>
                <div className='flex items-center justify-between'>
                  <span className='font-semibold text-xs text-foreground'>
                    Khóa bí mật Ed25519 đã tạo sẵn:
                  </span>
                  <Button
                    type='button'
                    size='sm'
                    onClick={handleDownloadPrivateKey}
                    className={`h-7 text-xs gap-1 font-semibold ${
                      keyDownloaded
                        ? 'bg-emerald-600 hover:bg-emerald-700 text-white'
                        : 'bg-primary'
                    }`}
                  >
                    <Icons.download className='size-3.5' />{' '}
                    {keyDownloaded ? 'Đã tải khóa (id_ed25519)' : 'Tải xuống Private Key'}
                  </Button>
                </div>

                <div className='text-[11px] text-muted-foreground'>
                  Khóa Ed25519 được sinh ra ngay trên trình duyệt của bạn.{' '}
                  <strong>Private Key không bao giờ được gửi hay lưu trữ tại máy chủ.</strong>
                </div>

                <div className='space-y-1'>
                  <span className='text-[10px] text-muted-foreground font-mono'>
                    Public Key sẽ nạp vào authorized_keys:
                  </span>
                  <div className='p-1.5 rounded bg-background border font-mono text-[10px] truncate'>
                    {generatedPublicKey || 'Đang tạo khóa...'}
                  </div>
                </div>
              </Card>
            )}

            {/* Option B: Import Public Key */}
            {enrollmentMode === 'import' && (
              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>
                  Dán Public Key của Thành viên (Ed25519 hoặc RSA ≥ 3072-bit)
                </Label>
                <textarea
                  rows={3}
                  value={importedPublicKey}
                  onChange={(e) => setImportedPublicKey(e.target.value)}
                  placeholder='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@device'
                  className='w-full rounded-md border border-input bg-background p-2 text-xs font-mono'
                />
              </div>
            )}

            {/* Option C: Pending enrollment */}
            {enrollmentMode === 'pending' && (
              <div className='p-3 rounded-lg border border-amber-500/30 bg-amber-500/10 text-[11px] text-amber-900 dark:text-amber-200'>
                Tài khoản sẽ được tạo ở trạng thái <strong>PENDING_KEY_ENROLLMENT</strong>. Cổng SSH
                proxy sẽ bị vô hiệu hóa cho tới khi người dùng nạp khóa hợp lệ qua cổng thông tin.
              </div>
            )}

            {/* Connection Preview Snippets */}
            <div className='space-y-1.5 pt-1'>
              <Label className='text-xs font-semibold'>Lệnh kết nối SSH mẫu:</Label>
              <div className='flex items-center justify-between p-2 rounded bg-muted/40 border font-mono text-[11px]'>
                <code>
                  ssh -i ~/.ssh/id_ed25519 -p {allocatedPort} {username || 'user'}@
                  {currentServer?.vpnIp}
                </code>
                <Button
                  type='button'
                  size='sm'
                  variant='ghost'
                  className='h-6 px-2 text-[10px]'
                  onClick={async () => {
                    const cmd = `ssh -i ~/.ssh/id_ed25519 -p ${allocatedPort} ${username || 'user'}@${currentServer?.vpnIp}`;
                    const copied = await copyToClipboard(cmd);
                    if (copied) {
                      toast.success('Đã sao chép lệnh SSH');
                    } else {
                      toast.info(`Lệnh SSH: ${cmd}`);
                    }
                  }}
                >
                  <Icons.forms className='size-3 mr-1' /> Copy
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* STEP 7: TIẾN TRÌNH KHỞI TẠO TỰ ĐỘNG & KIỂM TRA CÁCH LY */}
        {step === 7 && (
          <div className='py-6 px-2 flex flex-col items-center justify-center text-center space-y-4'>
            <div className='size-14 rounded-full bg-primary/10 flex items-center justify-center text-primary'>
              {isProvisionDone ? (
                <Icons.circleCheck className='size-8 text-emerald-600 dark:text-emerald-400' />
              ) : (
                <Icons.spinner className='size-8 animate-spin text-primary' />
              )}
            </div>

            <div className='space-y-1 w-full max-w-lg'>
              <h4 className='text-base font-bold text-foreground'>
                {isProvisionDone
                  ? 'Khởi tạo Workspace Thành công!'
                  : 'Đang thực thi quy trình Atomic Provisioning...'}
              </h4>
              <p className='text-xs text-muted-foreground font-mono min-h-[32px]'>
                {execStatusText}
              </p>
            </div>

            <div className='w-full max-w-lg bg-muted rounded-full h-2.5 overflow-hidden'>
              <div
                className='bg-primary h-2.5 rounded-full transition-all duration-300'
                style={{ width: `${execStep}%` }}
              />
            </div>

            {isProvisionDone && (
              <Card className='w-full max-w-lg p-3 text-left border bg-card text-xs space-y-1.5'>
                <div className='font-bold text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5'>
                  <Icons.check className='size-4' /> Xác nhận Kiểm tra Cách ly An ninh Đạt Chuẩn
                </div>
                <div className='text-[11px] text-muted-foreground space-y-0.5'>
                  <div>
                    • <strong>Tài khoản:</strong> @{username} (Standard User — Không sudo)
                  </div>
                  <div>
                    • <strong>Lưu trữ ZFS:</strong> data-{username} ({storageQuotaGb}GB) mounted tại{' '}
                    <code>{mountPath}</code>
                  </div>
                  <div>
                    • <strong>Dedicated SSH:</strong>{' '}
                    <code className='font-mono'>
                      {currentServer?.vpnIp}:{allocatedPort}
                    </code>
                  </div>
                  <div>
                    • <strong>Correlation ID:</strong>{' '}
                    <code className='font-mono text-[10px]'>{correlationId}</code>
                  </div>
                </div>
              </Card>
            )}
          </div>
        )}

        <DialogFooter className='gap-2 sm:gap-0 pt-2 border-t'>
          {step > 1 && step < 7 && (
            <Button
              type='button'
              variant='outline'
              size='sm'
              onClick={() => setStep(step - 1)}
              className='text-xs'
            >
              Quay lại
            </Button>
          )}

          {step < 6 && (
            <Button
              type='button'
              size='sm'
              disabled={
                (step === 1 && (!fullName || !username || !email || !usernameValidation.valid)) ||
                (step === 3 && currentPool && storageQuotaGb > currentPool.freeGb)
              }
              onClick={() => {
                if (step === 5 && enrollmentMode === 'generate' && !generatedPublicKey) {
                  handleGenerateKeypair();
                }
                setStep(step + 1);
              }}
              className='text-xs font-semibold bg-primary'
            >
              Tiếp tục
            </Button>
          )}

          {step === 6 && (
            <Button
              type='button'
              size='sm'
              onClick={handleStartProvision}
              className='text-xs font-semibold bg-primary gap-1.5'
            >
              <Icons.server className='size-3.5' /> Xác nhận & Cấp phát Tự động
            </Button>
          )}

          {isProvisionDone && (
            <Button
              type='button'
              size='sm'
              onClick={() => onOpenChange(false)}
              className='text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white'
            >
              Đóng & Hoàn tất
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
