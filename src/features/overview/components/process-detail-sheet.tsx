'use client';

import React from 'react';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription
} from '@/components/ui/sheet';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import { Separator } from '@/components/ui/separator';
import { ComputeJob } from '@/types/labops';

interface ProcessDetailSheetProps {
  job: ComputeJob | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ProcessDetailSheet({ job, open, onOpenChange }: ProcessDetailSheetProps) {
  if (!job) return null;

  const isGpu = Boolean(job.isGpu || (job.gpuId && job.gpuId !== 'CPU Only') || job.vramUsedMb > 0);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className='w-full sm:max-w-xl overflow-y-auto p-6'>
        <SheetHeader className='pb-4 border-b text-left'>
          <div className='flex items-center gap-2 mb-1'>
            <Badge
              variant={isGpu ? 'default' : 'secondary'}
              className='font-mono text-xs px-2 py-0.5'
            >
              {isGpu ? 'GPU WORKLOAD' : 'CPU PROCESS'}
            </Badge>
            <Badge
              variant='outline'
              className='bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 text-xs'
            >
              <span className='size-1.5 rounded-full bg-emerald-500 animate-pulse mr-1' />
              {job.status}
            </Badge>
            <span className='text-xs font-mono text-muted-foreground ml-auto'>PID: {job.pid}</span>
          </div>
          <SheetTitle className='text-lg font-bold tracking-tight'>
            Chi tiết Tiến trình Máy chủ & GPU
          </SheetTitle>
          <SheetDescription className='text-xs text-muted-foreground'>
            Thông số định danh và mức tiêu thụ tài nguyên thực tế thu thập từ Linux /proc và NVML
          </SheetDescription>
        </SheetHeader>

        <div className='space-y-5 py-5 text-xs'>
          {/* Section 1: Process Identity */}
          <div className='space-y-2.5'>
            <h4 className='font-bold uppercase tracking-wider text-[11px] text-muted-foreground flex items-center gap-1.5'>
              <Icons.terminal className='size-3.5 text-primary' /> Định danh Tiến trình (Host
              Process)
            </h4>
            <div className='grid grid-cols-2 gap-2 bg-muted/40 p-3 rounded-lg border font-mono text-[11px]'>
              <div>
                <span className='text-muted-foreground block text-[10px] font-sans'>Host PID</span>
                <span className='font-bold text-foreground text-sm'>{job.pid}</span>
              </div>
              <div>
                <span className='text-muted-foreground block text-[10px] font-sans'>
                  Parent PID (PPID)
                </span>
                <span className='font-bold text-foreground text-sm'>{job.ppid ?? '-'}</span>
              </div>
              <div className='col-span-2 pt-1 border-t'>
                <span className='text-muted-foreground block text-[10px] font-sans'>
                  Chương trình thực thi
                </span>
                <span className='font-bold text-foreground break-all'>{job.script}</span>
              </div>
              {job.cmdline && (
                <div className='col-span-2 pt-1 border-t'>
                  <span className='text-muted-foreground block text-[10px] font-sans'>
                    Lệnh khởi chạy (Đã lọc bảo mật)
                  </span>
                  <div className='bg-background/80 p-2 rounded border text-[10px] break-all leading-relaxed max-h-32 overflow-y-auto text-muted-foreground'>
                    {job.cmdline}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Section 2: User & Environment */}
          <div className='space-y-2.5'>
            <h4 className='font-bold uppercase tracking-wider text-[11px] text-muted-foreground flex items-center gap-1.5'>
              <Icons.users className='size-3.5 text-primary' /> Người dùng & Môi trường Thực thi
            </h4>
            <div className='grid grid-cols-2 gap-3 bg-muted/40 p-3 rounded-lg border'>
              <div>
                <span className='text-muted-foreground block text-[10px]'>Tài khoản Linux</span>
                <span className='font-mono font-bold text-foreground text-sm'>@{job.username}</span>
              </div>
              <div>
                <span className='text-muted-foreground block text-[10px]'>Thành viên gán</span>
                <span className='font-semibold text-foreground'>{job.fullName}</span>
              </div>
              <div className='pt-2 border-t'>
                <span className='text-muted-foreground block text-[10px]'>Máy chủ vật lý</span>
                <span className='font-medium text-foreground'>
                  {job.serverName ||
                    (job.serverId === 'track' ? 'Server 01 — Track' : 'Server 02 — Workstation')}
                </span>
              </div>
              <div className='pt-2 border-t'>
                <span className='text-muted-foreground block text-[10px]'>
                  Không gian / Workspace
                </span>
                <span className='font-mono text-foreground'>{job.workspace}</span>
              </div>
              <div className='pt-2 border-t'>
                <span className='text-muted-foreground block text-[10px]'>
                  Trạng thái Container
                </span>
                <Badge variant='outline' className='text-[10px] mt-0.5 font-normal'>
                  {job.containerState}
                </Badge>
              </div>
              <div className='pt-2 border-t'>
                <span className='text-muted-foreground block text-[10px]'>Phiên SSH</span>
                <Badge variant='outline' className='text-[10px] mt-0.5 font-normal'>
                  {job.sshStatus}
                </Badge>
              </div>
            </div>
          </div>

          {/* Section 3: Resource Consumption */}
          <div className='space-y-2.5'>
            <h4 className='font-bold uppercase tracking-wider text-[11px] text-muted-foreground flex items-center gap-1.5'>
              <Icons.activity className='size-3.5 text-primary' /> Tiêu thụ Tài nguyên Thời gian
              thực
            </h4>
            <div className='grid grid-cols-3 gap-2'>
              <div className='bg-blue-500/10 border border-blue-500/20 p-2.5 rounded-lg'>
                <span className='text-[10px] text-blue-600 dark:text-blue-400 font-semibold block'>
                  Mức tải CPU
                </span>
                <span className='text-base font-bold font-mono text-foreground'>
                  {job.cpuPercent > 0 ? `${job.cpuPercent}%` : '0%'}
                </span>
                <span className='text-[10px] text-muted-foreground block mt-0.5'>Host Cores</span>
              </div>

              <div className='bg-purple-500/10 border border-purple-500/20 p-2.5 rounded-lg'>
                <span className='text-[10px] text-purple-600 dark:text-purple-400 font-semibold block'>
                  Bộ nhớ RAM
                </span>
                <span className='text-base font-bold font-mono text-foreground'>
                  {job.ramUsedMb > 0 ? `${(job.ramUsedMb / 1024).toFixed(1)} GB` : '0 GB'}
                </span>
                <span className='text-[10px] text-muted-foreground block mt-0.5'>
                  Resident Set Size
                </span>
              </div>

              <div className='bg-indigo-500/10 border border-indigo-500/20 p-2.5 rounded-lg'>
                <span className='text-[10px] text-indigo-600 dark:text-indigo-400 font-semibold block'>
                  GPU VRAM
                </span>
                <span className='text-base font-bold font-mono text-foreground'>
                  {job.vramUsedMb > 0 ? `${(job.vramUsedMb / 1024).toFixed(1)} GB` : '-'}
                </span>
                <span className='text-[10px] text-muted-foreground block mt-0.5'>
                  {job.gpuId || 'Không dùng'}
                </span>
              </div>
            </div>
          </div>

          {/* Section 4: Execution Timing */}
          <div className='space-y-2.5'>
            <h4 className='font-bold uppercase tracking-wider text-[11px] text-muted-foreground flex items-center gap-1.5'>
              <Icons.clock className='size-3.5 text-primary' /> Thời gian Thực thi (Runtime)
            </h4>
            <div className='grid grid-cols-2 gap-2 bg-muted/40 p-3 rounded-lg border text-[11px] font-mono'>
              <div>
                <span className='text-muted-foreground block text-[10px] font-sans'>
                  Thời điểm khởi chạy
                </span>
                <span className='text-foreground font-semibold'>{job.startTime}</span>
              </div>
              <div>
                <span className='text-muted-foreground block text-[10px] font-sans'>
                  Tổng thời gian chạy
                </span>
                <span className='text-primary font-bold'>{job.elapsedTime}</span>
              </div>
            </div>
          </div>

          <Separator />

          <div className='bg-amber-500/10 border border-amber-500/30 p-3 rounded-lg text-amber-900 dark:text-amber-200 text-xs flex items-start gap-2'>
            <Icons.shieldAlert className='size-4 shrink-0 text-amber-600 mt-0.5' />
            <p className='leading-relaxed text-[11px]'>
              <strong>Chính sách Bảo vệ Workload Nghiên cứu:</strong> Tiến trình đang chạy thực tế
              trên máy chủ. Hệ thống LabOps Control Center chỉ thực hiện giám sát chỉ đọc (read-only
              telemetry), không tự ý ngắt hay can thiệp tác vụ khi chưa có chỉ định rõ ràng của Quản
              trị viên.
            </p>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
