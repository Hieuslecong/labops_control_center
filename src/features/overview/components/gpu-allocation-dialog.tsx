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
import { toast } from 'sonner';

interface GpuAllocationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultGpuId?: string;
}

export function GpuAllocationDialog({
  open,
  onOpenChange,
  defaultGpuId
}: GpuAllocationDialogProps) {
  const gpus = useLabOpsStore((state) => state.gpus);
  const allMembers = useLabOpsStore((state) => state.members);
  const members = React.useMemo(
    () => allMembers.filter((m) => m.status === 'ACTIVE'),
    [allMembers]
  );
  const [customGpuId, setCustomGpuId] = useState<string | null>(null);
  const [selectedUsername, setSelectedUsername] = useState('');
  const [jobName, setJobName] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const selectedGpuId = customGpuId ?? (defaultGpuId || gpus[0]?.id || '');
  const setSelectedGpuId = (id: string) => setCustomGpuId(id);

  const selectedGpu = gpus.find((g) => g.id === selectedGpuId) || gpus[0];

  const handleSubmit = async (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (!selectedGpu) return;
    setIsSubmitting(true);
    try {
      const res = await fetch('/api/labops/gpus/allocate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({
          gpu_id: selectedGpuId,
          username: selectedUsername || undefined,
          mode: 'FULL_GPU_EXCLUSIVE',
          job_name: jobName
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data?.detail;
        toast.error('Không thể giữ GPU', {
          description: typeof detail === 'string' ? detail : detail?.message || `Lỗi HTTP ${res.status}`
        });
        return;
      }
      onOpenChange(false);
      toast.info(`Đã giữ GPU ${selectedGpu.displayLabel} — chờ xác minh`, {
        description:
          'Trạng thái: PENDING_VERIFICATION. Tác vụ chỉ được coi là đang chạy khi Node Agent xác minh process thật.'
      });
    } catch {
      toast.error('Không kết nối được backend LabOps');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-[480px]'>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <div className='flex items-center gap-2'>
              <div className='flex size-8 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-600 dark:text-indigo-400'>
                <Icons.gpu className='size-5' />
              </div>
              <div>
                <DialogTitle className='text-base font-bold'>Cấp phát Tài nguyên GPU</DialogTitle>
                <DialogDescription className='text-xs'>
                  Giữ độc quyền một GPU vật lý (theo UUID) cho thành viên nghiên cứu
                </DialogDescription>
              </div>
            </div>
          </DialogHeader>

          <div className='grid gap-4 py-4 text-xs'>
            {/* Chọn GPU */}
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Chọn GPU vật lý</Label>
              <div className='grid grid-cols-2 gap-2'>
                {gpus.map((gpu) => {
                  const isSelected = gpu.id === selectedGpuId;
                  const busy = (gpu.activeProcesses?.length ?? 0) > 0;
                  return (
                    <button
                      key={gpu.id}
                      type='button'
                      onClick={() => setSelectedGpuId(gpu.id)}
                      className={`p-2.5 rounded-lg border text-left transition-all ${
                        isSelected
                          ? 'border-primary bg-primary/5 ring-1 ring-primary'
                          : 'border-border bg-card hover:bg-muted/30'
                      }`}
                    >
                      <div className='flex items-center justify-between'>
                        <span className='font-bold text-xs'>{gpu.displayLabel}</span>
                        <Badge variant='outline' className='text-[9px] px-1 py-0 font-mono'>
                          {gpu.serverName}
                        </Badge>
                      </div>
                      <div className='text-[10px] text-muted-foreground mt-1'>
                        {busy ? (
                          <span className='text-amber-600'>Đang có process — không thể giữ</span>
                        ) : (
                          <span className='text-emerald-600'>Không có process quan sát được</span>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Chọn thành viên */}
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Thành viên tiếp nhận</Label>
              <select
                value={selectedUsername}
                onChange={(e) => setSelectedUsername(e.target.value)}
                aria-label='Thành viên tiếp nhận'
                className='w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-medium focus:ring-1 focus:ring-primary'
              >
                <option value=''>Chính tôi (người đang đăng nhập)</option>
                {members.map((m) => (
                  <option key={m.id} value={m.username}>
                    {m.fullName} (@{m.username} •{' '}
                    {m.assignedServer})
                  </option>
                ))}
              </select>
            </div>

            {/* Chế độ cấp phát: V1 chỉ hỗ trợ độc quyền toàn bộ GPU */}
            <div className='rounded-md border border-blue-200 bg-blue-50 p-2.5 text-[11px] text-blue-900 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-200'>
              <div className='font-semibold'>Chế độ: Độc quyền toàn bộ GPU (FULL_GPU_EXCLUSIVE)</div>
              <div className='mt-0.5'>
                Docker không giới hạn VRAM theo GB, nên hệ thống không cho chọn hạn ngạch VRAM. GPU
                chỉ được giữ khi NVML xác nhận không có process ngoài đang chạy.
              </div>
            </div>

            {/* Tên Script / Job */}
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Tên tác vụ (ghi chú)</Label>
              <Input
                value={jobName}
                onChange={(e) => setJobName(e.target.value)}
                placeholder='ví dụ: train_diffusion_unet.py --batch 16'
                className='text-xs font-mono h-8'
                required
              />
            </div>
          </div>

          <DialogFooter className='gap-2 sm:gap-0'>
            <Button
              type='button'
              variant='outline'
              size='sm'
              onClick={() => onOpenChange(false)}
              className='text-xs'
            >
              Hủy
            </Button>
            <Button
              type='submit'
              size='sm'
              disabled={isSubmitting}
              className='text-xs font-semibold bg-primary'
            >
              {isSubmitting ? 'Đang xử lý...' : 'Giữ GPU'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
