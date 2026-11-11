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

interface ForceKillDialogProps {
  job: ComputeJob | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: () => void;
}

export function ForceKillDialog({
  job,
  open,
  onOpenChange,
  onSuccess
}: ForceKillDialogProps) {
  const killProcess = useLabOpsStore((state) => state.killProcess);
  const [reason, setReason] = useState('Tiến trình treo không phản hồi sau thời gian ân hạn');
  const [confirmedRisk, setConfirmedRisk] = useState(false);
  const [killTree, setKillTree] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!job) return null;

  const processRef = job.ref || `track:${job.workspace || 'host'}:${job.pid}:0`;

  const handleConfirm = async () => {
    if (!confirmedRisk) {
      toast.error('Vui lòng tích chọn xác nhận rủi ro mất dữ liệu');
      return;
    }
    setIsSubmitting(true);
    try {
      const res = await killProcess(processRef, reason, killTree);
      if (res.success) {
        toast.success(res.message);
        onOpenChange(false);
        onSuccess?.();
      } else {
        toast.error(res.message);
      }
    } catch (err: unknown) {
      toast.error((err as Error)?.message || 'Lỗi khi gửi yêu cầu buộc dừng');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-md bg-white dark:bg-zinc-950 border border-rose-200 dark:border-rose-900 text-foreground'>
        <DialogHeader>
          <div className='flex items-center gap-2 mb-1'>
            <div className='p-2 rounded-full bg-rose-500/10 text-rose-600 dark:text-rose-400'>
              <Icons.warning className='size-5' />
            </div>
            <div>
              <DialogTitle className='text-base font-bold text-rose-600 dark:text-rose-400'>
                CẢNH BÁO: Buộc dừng Cưỡng chế (SIGKILL)
              </DialogTitle>
              <DialogDescription className='text-xs text-muted-foreground'>
                Hành động này sẽ gửi tín hiệu SIGKILL lập tức tới tiến trình mà không qua thời gian ân hạn.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className='space-y-4 py-2 text-xs'>
          {/* Cảnh báo rủi ro dữ liệu */}
          <div className='p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-900 dark:text-rose-200 leading-relaxed text-[11px] space-y-1'>
            <div className='font-bold flex items-center gap-1.5 text-rose-700 dark:text-rose-300'>
              <Icons.shieldAlert className='size-4' />
              RỦI RO MẤT DỮ LIỆU & CHECKPOINT
            </div>
            <p>
              Mọi dữ liệu tính toán trong bộ nhớ RAM, gradients hoặc checkpoint chưa kịp flush xuống đĩa của thành viên <strong>@{job.username}</strong> sẽ bị xóa sạch ngay lập tức.
            </p>
          </div>

          {/* Thông tin tiến trình đích */}
          <div className='p-3 rounded-lg border bg-zinc-50 dark:bg-zinc-900/50 space-y-1.5 font-mono text-[11px]'>
            <div className='flex items-center justify-between'>
              <span className='font-sans text-muted-foreground'>Lệnh:</span>
              <span className='font-bold text-foreground break-all'>{job.script}</span>
            </div>
            <div className='flex items-center justify-between'>
              <span className='font-sans text-muted-foreground'>PID:</span>
              <Badge variant='outline'>{job.pid}</Badge>
            </div>
            <div className='flex items-center justify-between'>
              <span className='font-sans text-muted-foreground'>Người sở hữu:</span>
              <span className='font-sans font-bold text-foreground'>@{job.username}</span>
            </div>
          </div>

          {/* Lý do buộc dừng */}
          <div className='space-y-1.5'>
            <label htmlFor='kill-reason' className='font-semibold text-xs text-foreground block'>
              Lý do buộc dừng cưỡng chế <span className='text-rose-500'>*</span>
            </label>
            <Input
              id='kill-reason'
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder='Nhập lý do gửi tín hiệu SIGKILL...'
              className='h-8 text-xs bg-white dark:bg-zinc-900'
            />
          </div>

          {/* Tùy chọn dừng cây con */}
          <label className='flex items-center gap-2 p-2 rounded border bg-zinc-50/50 dark:bg-zinc-900/30 cursor-pointer'>
            <input
              type='checkbox'
              checked={killTree}
              onChange={(e) => setKillTree(e.target.checked)}
              className='size-3.5 rounded border-rose-400 text-rose-600 focus:ring-rose-500'
            />
            <span className='text-xs font-medium text-foreground'>
              Buộc dừng toàn bộ cây tiến trình con (Kill Tree)
            </span>
          </label>

          {/* Hộp kiểm xác nhận rủi ro */}
          <label className='flex items-start gap-2.5 p-2.5 rounded-lg border border-rose-200 dark:border-rose-900/60 bg-rose-50/40 dark:bg-rose-950/20 cursor-pointer'>
            <input
              type='checkbox'
              checked={confirmedRisk}
              onChange={(e) => setConfirmedRisk(e.target.checked)}
              className='size-4 mt-0.5 rounded border-rose-400 text-rose-600 focus:ring-rose-500'
            />
            <span className='text-xs font-semibold text-rose-900 dark:text-rose-200 leading-tight'>
              Tôi hiểu rõ rủi ro mất mát checkpoint / dữ liệu huấn luyện và xác nhận gửi tín hiệu SIGKILL cưỡng chế.
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
            disabled={isSubmitting || !confirmedRisk || !reason.trim()}
            className='h-8 text-xs bg-rose-600 hover:bg-rose-700 text-white'
          >
            {isSubmitting ? (
              <>
                <Icons.spinner className='size-3.5 mr-1.5 animate-spin' />
                Đang buộc dừng...
              </>
            ) : (
              'Buộc dừng Lập tức (SIGKILL)'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
