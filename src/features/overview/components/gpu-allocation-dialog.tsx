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
  const allocateGpu = useLabOpsStore((state) => state.allocateGpu);

  const [customGpuId, setCustomGpuId] = useState<string | null>(null);
  const [selectedUsername, setSelectedUsername] = useState('tuanpm');
  const [mode, setMode] = useState<'SHARED' | 'EXCLUSIVE'>('SHARED');
  const [vramGb, setVramGb] = useState(12);
  const [jobName, setJobName] = useState('fine_tune_vision_encoder.py');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const selectedGpuId = customGpuId ?? (defaultGpuId || 'track-gpu-03');
  const setSelectedGpuId = (id: string) => setCustomGpuId(id);

  const selectedGpu = gpus.find((g) => g.id === selectedGpuId) || gpus[0];
  const selectedMember = members.find((m) => m.username === selectedUsername) || members[0];

  const handleSubmit = (e: React.SyntheticEvent) => {
    e.preventDefault();
    setIsSubmitting(true);

    setTimeout(() => {
      allocateGpu({
        gpuId: selectedGpuId,
        username: selectedUsername,
        mode,
        requestedVramGb: vramGb,
        jobName
      });

      setIsSubmitting(false);
      onOpenChange(false);
      toast.success(`Cấp phát thành công GPU ${selectedGpu.displayLabel}`, {
        description: `Đã cấp ${vramGb}GB VRAM (${mode}) cho thành viên ${selectedMember.fullName}`
      });
    }, 400);
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
                  Phân bổ bộ nhớ VRAM và gán quyền truy cập GPU vật lý cho thành viên nghiên cứu
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
                  const freeGb = Math.max(0, (gpu.vramTotalMb - gpu.vramUsedMb) / 1024).toFixed(1);
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
                        Còn trống: <strong className='text-foreground'>{freeGb} GB</strong> / 24GB
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
                className='w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-medium focus:ring-1 focus:ring-primary'
              >
                {members.map((m) => (
                  <option key={m.id} value={m.username}>
                    {m.fullName} (@{m.username} •{' '}
                    {m.assignedServer === 'track' ? 'Server Track' : 'Server Workstation'})
                  </option>
                ))}
              </select>
            </div>

            {/* Chế độ cấp phát */}
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Chế độ phân bổ</Label>
              <div className='grid grid-cols-2 gap-2'>
                <button
                  type='button'
                  onClick={() => setMode('SHARED')}
                  className={`p-2 rounded-md border text-left text-xs ${
                    mode === 'SHARED'
                      ? 'border-primary bg-primary/10 font-semibold'
                      : 'border-border'
                  }`}
                >
                  <div className='font-bold text-foreground'>Shared (Chia sẻ)</div>
                  <div className='text-[10px] text-muted-foreground'>
                    Nhiều user chạy song song qua MPS/Quota
                  </div>
                </button>
                <button
                  type='button'
                  onClick={() => setMode('EXCLUSIVE')}
                  className={`p-2 rounded-md border text-left text-xs ${
                    mode === 'EXCLUSIVE'
                      ? 'border-purple-600 bg-purple-500/10 font-semibold'
                      : 'border-border'
                  }`}
                >
                  <div className='font-bold text-foreground'>Exclusive (Độc quyền)</div>
                  <div className='text-[10px] text-muted-foreground'>
                    Chiếm toàn bộ 24GB VRAM cho 1 tác vụ lớn
                  </div>
                </button>
              </div>
            </div>

            {/* VRAM Limit */}
            <div className='space-y-1.5'>
              <div className='flex justify-between items-center'>
                <Label className='text-xs font-semibold'>Hạn ngạch VRAM yêu cầu</Label>
                <span className='font-mono font-bold text-primary text-xs'>{vramGb} GB</span>
              </div>
              <input
                type='range'
                min={4}
                max={24}
                step={2}
                value={vramGb}
                onChange={(e) => setVramGb(Number(e.target.value))}
                className='w-full accent-primary'
              />
              <div className='flex justify-between text-[10px] text-muted-foreground'>
                <span>4 GB</span>
                <span>8 GB</span>
                <span>12 GB</span>
                <span>16 GB</span>
                <span>20 GB</span>
                <span>24 GB (Toàn bộ)</span>
              </div>
            </div>

            {/* Tên Script / Job */}
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Tên lệnh / Script thực thi</Label>
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
              {isSubmitting ? 'Đang xử lý...' : 'Xác nhận cấp phát'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
