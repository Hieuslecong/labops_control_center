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
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import { ComputeJob } from '@/types/labops';
import { useLabOpsStore } from '@/features/labops/store';
import { toast } from 'sonner';

interface StopProcessDialogProps {
  job: ComputeJob | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: () => void;
}

export function StopProcessDialog({
  job,
  open,
  onOpenChange,
  onSuccess
}: StopProcessDialogProps) {
  const stopProcess = useLabOpsStore((state) => state.stopProcess);
  const [reason, setReason] = useState('Giải phóng tài nguyên hệ thống');
  const [terminateTree, setTerminateTree] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!job) return null;

  const processRef = job.ref || `track:${job.workspace || 'host'}:${job.pid}:0`;

  const handleConfirm = async () => {
    setIsSubmitting(true);
    try {
      const res = await stopProcess(processRef, reason, terminateTree);
      if (res.success) {
        toast.success(res.message);
        onOpenChange(false);
        onSuccess?.();
      } else {
        toast.error(res.message);
      }
    } catch (err: unknown) {
      toast.error((err as Error)?.message || 'Lỗi khi gửi yêu cầu dừng tiến trình');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-md bg-white dark:bg-zinc-950 border text-foreground'>
        <DialogHeader>
          <div className='flex items-center gap-2 mb-1'>
            <div className='p-2 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400'>
              <Icons.warning className='size-5' />
            </div>
            <div>
              <DialogTitle className='text-base font-bold'>Dừng An toàn Tiến trình (SIGTERM)</DialogTitle>
              <DialogDescription className='text-xs text-muted-foreground'>
                Gửi tín hiệu dừng an toàn tới tiến trình của thành viên với thời gian ân hạn 10 giây.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className='space-y-4 py-2 text-xs'>
          {/* Thông tin tiến trình đích */}
          <div className='p-3 rounded-lg border bg-zinc-50 dark:bg-zinc-900/50 space-y-1.5'>
            <div className='flex items-center justify-between'>
              <span className='font-semibold text-muted-foreground'>Chương trình:</span>
              <span className='font-mono font-bold text-foreground'>{job.script}</span>
            </div>
            <div className='flex items-center justify-between'>
              <span className='font-semibold text-muted-foreground'>Host PID:</span>
              <Badge variant='outline' className='font-mono text-xs'>{job.pid}</Badge>
            </div>
            <div className='flex items-center justify-between'>
              <span className='font-semibold text-muted-foreground'>Thành viên:</span>
              <span className='font-bold text-foreground'>@{job.username} ({job.fullName})</span>
            </div>
            <div className='flex items-center justify-between'>
              <span className='font-semibold text-muted-foreground'>Workspace:</span>
              <span className='font-mono text-muted-foreground'>{job.workspace}</span>
            </div>
            {job.vramUsedMb > 0 && (
              <div className='flex items-center justify-between text-indigo-600 font-semibold'>
                <span>VRAM đang chiếm:</span>
                <span>{(job.vramUsedMb / 1024).toFixed(1)} GB ({job.gpuId || 'GPU 0'})</span>
              </div>
            )}
          </div>

          {/* Lý do dừng */}
          <div className='space-y-1.5'>
            <label htmlFor='stop-reason' className='font-semibold text-xs text-foreground block'>
              Lý do dừng <span className='text-rose-500'>*</span>
            </label>
            <Input
              id='stop-reason'
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder='Nhập lý do gửi tín hiệu dừng...'
              className='h-8 text-xs bg-white dark:bg-zinc-900'
            />
            <p className='text-[10px] text-muted-foreground'>
              Lý do này sẽ được ghi vào nhật ký kiểm toán (Audit Trail) phục vụ đối soát.
            </p>
          </div>

          {/* Tùy chọn dừng cả cây tiến trình con */}
          <label className='flex items-center gap-2 p-2 rounded border bg-zinc-50/50 dark:bg-zinc-900/30 cursor-pointer'>
            <input
              type='checkbox'
              checked={terminateTree}
              onChange={(e) => setTerminateTree(e.target.checked)}
              className='size-3.5 rounded border-gray-300 text-amber-600 focus:ring-amber-500'
            />
            <span className='text-xs font-medium text-foreground'>
              Dừng toàn bộ cây tiến trình con (Children Workers & Process Group)
            </span>
          </label>
        </div>

        <DialogFooter className='gap-2 sm:gap-0 pt-2'>
          <Button
            type='button'
            variant='outline'
            size='sm'
            onClick={() => onOpenChange(false)}
            disabled={isSubmitting}
            className='h-8 text-xs'
          >
            Hủy bỏ
          </Button>
          <Button
            type='button'
            size='sm'
            onClick={handleConfirm}
            disabled={isSubmitting || !reason.trim()}
            className='h-8 text-xs bg-amber-600 hover:bg-amber-700 text-white'
          >
            {isSubmitting ? (
              <>
                <Icons.spinner className='size-3.5 mr-1.5 animate-spin' />
                Đang gửi tín hiệu...
              </>
            ) : (
              'Xác nhận Dừng (SIGTERM)'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
