'use client';

import React, { useState } from 'react';
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
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { MemberRole, ServerId } from '@/types/labops';
import { toast } from 'sonner';

interface MemberProvisioningWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function MemberProvisioningWizard({ open, onOpenChange }: MemberProvisioningWizardProps) {
  const addMember = useLabOpsStore((state) => state.addMember);

  const [step, setStep] = useState(1);
  const [fullName, setFullName] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<MemberRole>('RESEARCHER');
  const [server, setServer] = useState<ServerId>('track');
  const [workspaceName, setWorkspaceName] = useState('');
  const [template, setTemplate] = useState('ubuntu:22.04-cuda12.4');
  const [cpuLimit, setCpuLimit] = useState(8);
  const [ramLimit, setRamLimit] = useState(32);
  const [storageLimit, setStorageLimit] = useState(250);
  const [gpuSelection, setGpuSelection] = useState<string[]>(['TRACK-GPU-01']);
  const [vramPolicy, setVramPolicy] = useState('Quota 12GB (Shared)');
  const [sshPublicKey, setSshPublicKey] = useState('');
  const [ipRestriction, setIpRestriction] = useState('10.200.0.0/16 (WireGuard VPN Only)');

  // Provisioning progress state
  const [provisionProgress, setProvisionProgress] = useState(0);
  const [provisionStatusText, setProvisionStatusText] = useState('');
  const [isDone, setIsDone] = useState(false);

  // Auto-generate workspace name when username changes
  const handleUsernameChange = (val: string) => {
    const clean = val.toLowerCase().replace(/[^a-z0-9_]/g, '');
    setUsername(clean);
    if (!workspaceName || workspaceName === `ws-${username}`) {
      setWorkspaceName(`ws-${clean}`);
    }
  };

  const handleStartProvision = () => {
    setStep(6);
    setProvisionProgress(15);
    setProvisionStatusText('1/5: Đang gửi lệnh khởi tạo container tới Incus daemon...');

    setTimeout(() => {
      setProvisionProgress(40);
      setProvisionStatusText(
        '2/5: Container Incus đã tạo. Đang áp dụng cgroups giới hạn CPU/RAM...'
      );
    }, 600);

    setTimeout(() => {
      setProvisionProgress(70);
      setProvisionStatusText(
        '3/5: Cấu hình tài khoản Linux, nạp SSH public key và phân quyền thư mục...'
      );
    }, 1200);

    setTimeout(() => {
      setProvisionProgress(90);
      setProvisionStatusText(
        '4/5: Thiết lập chính sách mạng WireGuard VPN và kiểm tra thiết bị GPU...'
      );
    }, 1800);

    setTimeout(() => {
      setProvisionProgress(100);
      setProvisionStatusText('5/5: Hoàn tất khởi tạo môi trường nghiên cứu thành công!');
      setIsDone(true);

      // Save to store
      addMember({
        fullName,
        username,
        email,
        role,
        assignedServer: server,
        workspaceName,
        status: 'ACTIVE',
        sshStatus: 'OFFLINE',
        sshSessionsCount: 0,
        sshPublicKeyPreview: sshPublicKey
          ? sshPublicKey.slice(0, 30) + '...'
          : 'ssh-ed25519 AAAAC3Nza... (Auto)',
        cpuLimitCores: cpuLimit,
        ramLimitGb: ramLimit,
        storageQuotaGb: storageLimit,
        gpuPermissions: gpuSelection,
        vramPolicy,
        maxRuntimeHours: 48
      });

      toast.success(`Đã khởi tạo thành công workspace cho ${fullName}`, {
        description: `Container ${workspaceName} sẵn sàng trên Server ${server === 'track' ? 'Track' : 'Workstation'}`
      });
    }, 2400);
  };

  const resetForm = () => {
    setStep(1);
    setFullName('');
    setUsername('');
    setEmail('');
    setWorkspaceName('');
    setSshPublicKey('');
    setProvisionProgress(0);
    setIsDone(false);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(val) => {
        onOpenChange(val);
        if (!val) resetForm();
      }}
    >
      <DialogContent className='sm:max-w-[620px]'>
        <DialogHeader>
          <div className='flex items-center gap-2'>
            <div className='flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary'>
              <Icons.teams className='size-5' />
            </div>
            <div>
              <DialogTitle className='text-base font-bold'>
                Thêm Thành viên & Khởi tạo Môi trường Nghiên cứu
              </DialogTitle>
              <DialogDescription className='text-xs'>
                Quy trình 6 bước tự động khởi tạo Linux container Incus, cấu hình SSH và hạn ngạch
                GPU
              </DialogDescription>
            </div>
          </div>

          {/* Stepper progress indicator */}
          <div className='flex items-center justify-between pt-3 pb-1 border-b text-[11px] font-medium'>
            {['Thông tin', 'Máy chủ', 'Workspace', 'Hạn ngạch', 'Khóa SSH', 'Khởi tạo'].map(
              (title, idx) => {
                const num = idx + 1;
                const isActive = step === num;
                const isPassed = step > num;
                return (
                  <div key={title} className='flex items-center gap-1'>
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
                          : 'text-muted-foreground hidden sm:inline'
                      }
                    >
                      {title}
                    </span>
                  </div>
                );
              }
            )}
          </div>
        </DialogHeader>

        {/* STEP 1: THÔNG TIN CƠ BẢN */}
        {step === 1 && (
          <div className='grid gap-3.5 py-3 text-xs'>
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Họ và tên thành viên</Label>
              <Input
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder='ví dụ: Nguyễn Hoàng Long'
                className='h-8 text-xs'
                required
              />
            </div>
            <div className='grid grid-cols-2 gap-3'>
              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>Tên người dùng (Linux Username)</Label>
                <Input
                  value={username}
                  onChange={(e) => handleUsernameChange(e.target.value)}
                  placeholder='ví dụ: longnh'
                  className='h-8 text-xs font-mono'
                  required
                />
              </div>
              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>Địa chỉ Email</Label>
                <Input
                  type='email'
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder='longnh@labops.local'
                  className='h-8 text-xs'
                  required
                />
              </div>
            </div>
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Vai trò trong Phòng Lab</Label>
              <div className='grid grid-cols-3 gap-2'>
                {(['RESEARCHER', 'LAB_MANAGER', 'GUEST'] as MemberRole[]).map((r) => (
                  <button
                    key={r}
                    type='button'
                    onClick={() => setRole(r)}
                    className={`p-2 rounded-md border text-center text-xs transition-colors ${
                      role === r
                        ? 'border-primary bg-primary/10 font-bold text-foreground'
                        : 'border-border bg-card hover:bg-muted/30 text-muted-foreground'
                    }`}
                  >
                    {r === 'RESEARCHER'
                      ? 'Nghiên cứu viên'
                      : r === 'LAB_MANAGER'
                        ? 'Quản lý Lab'
                        : 'Khách mời'}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* STEP 2: CHỌN MÁY CHỦ */}
        {step === 2 && (
          <div className='grid gap-3.5 py-3 text-xs'>
            <Label className='text-xs font-semibold'>Chọn Máy chủ Vật lý Cấp phát</Label>
            <div className='grid grid-cols-2 gap-3'>
              <button
                type='button'
                onClick={() => {
                  setServer('track');
                  setGpuSelection(['TRACK-GPU-01']);
                }}
                className={`p-3.5 rounded-xl border text-left transition-all ${
                  server === 'track'
                    ? 'border-blue-600 bg-blue-500/10 ring-1 ring-blue-600'
                    : 'border-border bg-card hover:bg-muted/30'
                }`}
              >
                <div className='flex items-center justify-between'>
                  <span className='font-bold text-sm text-foreground'>Server 01 — Track</span>
                  <Badge variant='outline' className='text-[10px] font-mono'>
                    Hà Nội
                  </Badge>
                </div>
                <p className='text-[11px] text-muted-foreground mt-1'>
                  3x NVIDIA A30 24GB (72GB) • AMD EPYC 64-Core • 256GB RAM • 8TB ZFS
                </p>
                <div className='mt-2 text-[10px] text-emerald-600 dark:text-emerald-400 font-medium'>
                  ● Phù hợp huấn luyện mô hình lớn đa GPU
                </div>
              </button>

              <button
                type='button'
                onClick={() => {
                  setServer('workstation');
                  setGpuSelection(['WS-GPU-01']);
                }}
                className={`p-3.5 rounded-xl border text-left transition-all ${
                  server === 'workstation'
                    ? 'border-purple-600 bg-purple-500/10 ring-1 ring-purple-600'
                    : 'border-border bg-card hover:bg-muted/30'
                }`}
              >
                <div className='flex items-center justify-between'>
                  <span className='font-bold text-sm text-foreground'>Server 02 — Workstation</span>
                  <Badge variant='outline' className='text-[10px] font-mono'>
                    TP.HCM
                  </Badge>
                </div>
                <p className='text-[11px] text-muted-foreground mt-1'>
                  1x NVIDIA A30 24GB • Intel Xeon 32-Core • 128GB RAM • 4TB ZFS
                </p>
                <div className='mt-2 text-[10px] text-purple-600 dark:text-purple-400 font-medium'>
                  ● Phù hợp thử nghiệm, inference & tinh chỉnh nhỏ
                </div>
              </button>
            </div>
          </div>
        )}

        {/* STEP 3: WORKSPACE INCUS */}
        {step === 3 && (
          <div className='grid gap-3.5 py-3 text-xs'>
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Tên Container Incus</Label>
              <Input
                value={workspaceName}
                onChange={(e) => setWorkspaceName(e.target.value)}
                placeholder='ws-username'
                className='h-8 text-xs font-mono'
                required
              />
              <span className='text-[11px] text-muted-foreground block'>
                Container được cách ly hoàn toàn qua PID, Mount namespace và mạng riêng biệt
              </span>
            </div>

            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Hệ điều hành / Template Môi trường</Label>
              <select
                value={template}
                onChange={(e) => setTemplate(e.target.value)}
                className='w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-medium'
              >
                <option value='ubuntu:22.04-cuda12.4'>
                  Ubuntu 22.04 LTS (NVIDIA CUDA 12.4 + cuDNN 9)
                </option>
                <option value='ubuntu:24.04-pytorch2.4'>
                  Ubuntu 24.04 LTS (PyTorch 2.4 pre-installed)
                </option>
                <option value='debian:12-minimal'>Debian 12 Bookworm (Minimal AI Research)</option>
              </select>
            </div>
          </div>
        )}

        {/* STEP 4: HẠN NGẠCH TÀI NGUYÊN */}
        {step === 4 && (
          <div className='grid gap-3.5 py-3 text-xs'>
            <div className='grid grid-cols-3 gap-3'>
              <div className='space-y-1'>
                <Label className='text-xs font-semibold'>CPU Limit</Label>
                <Input
                  type='number'
                  min={2}
                  max={server === 'track' ? 32 : 16}
                  value={cpuLimit}
                  onChange={(e) => setCpuLimit(Number(e.target.value))}
                  className='h-8 text-xs font-mono'
                />
                <span className='text-[10px] text-muted-foreground'>Cores vCPU</span>
              </div>
              <div className='space-y-1'>
                <Label className='text-xs font-semibold'>RAM Limit</Label>
                <Input
                  type='number'
                  min={8}
                  max={server === 'track' ? 128 : 64}
                  value={ramLimit}
                  onChange={(e) => setRamLimit(Number(e.target.value))}
                  className='h-8 text-xs font-mono'
                />
                <span className='text-[10px] text-muted-foreground'>GB RAM</span>
              </div>
              <div className='space-y-1'>
                <Label className='text-xs font-semibold'>Storage ZFS</Label>
                <Input
                  type='number'
                  min={50}
                  max={1000}
                  value={storageLimit}
                  onChange={(e) => setStorageLimit(Number(e.target.value))}
                  className='h-8 text-xs font-mono'
                />
                <span className='text-[10px] text-muted-foreground'>GB Dung lượng</span>
              </div>
            </div>

            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Quyền truy cập GPU & Chính sách VRAM</Label>
              <select
                value={vramPolicy}
                onChange={(e) => setVramPolicy(e.target.value)}
                className='w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-medium'
              >
                <option value='Quota 8GB (Shared)'>Quota 8GB (Shared MPS)</option>
                <option value='Quota 12GB (Shared)'>Quota 12GB (Shared MPS)</option>
                <option value='Quota 16GB (Shared)'>Quota 16GB (Shared MPS)</option>
                <option value='Exclusive 24GB (Toàn quyền)'>
                  Exclusive 24GB (Chiếm toàn bộ 1 GPU)
                </option>
                <option value='Chỉ CPU (Không cấp GPU)'>Chỉ CPU (Đợi phê duyệt GPU)</option>
              </select>
            </div>
          </div>
        )}

        {/* STEP 5: KHÓA SSH & AN NINH */}
        {step === 5 && (
          <div className='grid gap-3.5 py-3 text-xs'>
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Khóa SSH Public Key của Thành viên</Label>
              <textarea
                value={sshPublicKey}
                onChange={(e) => setSshPublicKey(e.target.value)}
                placeholder='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@machine'
                rows={4}
                className='w-full rounded-md border border-input bg-background p-2.5 text-xs font-mono'
              />
              <span className='text-[11px] text-muted-foreground block'>
                Chỉ cho phép xác thực bằng SSH keypair, vô hiệu hóa hoàn toàn đăng nhập mật khẩu
              </span>
            </div>

            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Giới hạn Dải mạng Truy cập</Label>
              <Input
                value={ipRestriction}
                onChange={(e) => setIpRestriction(e.target.value)}
                className='h-8 text-xs font-mono bg-muted/30'
                readOnly
              />
            </div>
          </div>
        )}

        {/* STEP 6: TIẾN TRÌNH KHỞI TẠO TỰ ĐỘNG */}
        {step === 6 && (
          <div className='py-6 px-2 flex flex-col items-center justify-center text-center space-y-4'>
            <div className='size-14 rounded-full bg-primary/10 flex items-center justify-center text-primary'>
              {isDone ? (
                <Icons.check className='size-8 text-emerald-600' />
              ) : (
                <Icons.spinner className='size-8 animate-spin text-primary' />
              )}
            </div>

            <div className='space-y-1 w-full max-w-md'>
              <h4 className='text-base font-bold text-foreground'>
                {isDone ? 'Khởi tạo Workspace Thành công!' : 'Đang thiết lập môi trường...'}
              </h4>
              <p className='text-xs text-muted-foreground font-mono'>{provisionStatusText}</p>
            </div>

            <div className='w-full max-w-md bg-muted rounded-full h-2 overflow-hidden'>
              <div
                className='bg-primary h-2 rounded-full transition-all duration-500'
                style={{ width: `${provisionProgress}%` }}
              />
            </div>
          </div>
        )}

        <DialogFooter className='gap-2 sm:gap-0 pt-2 border-t'>
          {step > 1 && step < 6 && (
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

          {step < 5 && (
            <Button
              type='button'
              size='sm'
              disabled={step === 1 && (!fullName || !username)}
              onClick={() => setStep(step + 1)}
              className='text-xs font-semibold bg-primary'
            >
              Tiếp tục
            </Button>
          )}

          {step === 5 && (
            <Button
              type='button'
              size='sm'
              onClick={handleStartProvision}
              className='text-xs font-semibold bg-primary gap-1.5'
            >
              <Icons.server className='size-3.5' /> Xác nhận & Khởi tạo
            </Button>
          )}

          {isDone && (
            <Button
              type='button'
              size='sm'
              onClick={() => onOpenChange(false)}
              className='text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white'
            >
              Đóng
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
