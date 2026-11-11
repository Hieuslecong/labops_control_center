'use client';

import React, { useState } from 'react';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription
} from '@/components/ui/sheet';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { Separator } from '@/components/ui/separator';
import { ComputeJob } from '@/types/labops';
import { StopProcessDialog } from '@/features/jobs/components/stop-process-dialog';
import { ForceKillDialog } from '@/features/jobs/components/force-kill-dialog';
import { ProcessTreeDialog } from '@/features/jobs/components/process-tree-dialog';

interface ProcessDetailSheetProps {
  job: ComputeJob | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRefresh?: () => void;
}

export function ProcessDetailSheet({ job, open, onOpenChange, onRefresh }: ProcessDetailSheetProps) {
  const [stopOpen, setStopOpen] = useState(false);
  const [killOpen, setKillOpen] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);

  if (!job) return null;

  const isGpu = Boolean(job.isGpu || (job.gpuId && job.gpuId !== 'CPU Only') || job.vramUsedMb > 0);
  const isProtected = Boolean(job.isProtected || job.classification === 'SYSTEM_PROCESS');

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className='w-full sm:max-w-xl overflow-y-auto p-6 bg-white dark:bg-zinc-950 text-foreground'>
          <SheetHeader className='pb-4 border-b text-left'>
            <div className='flex items-center gap-2 mb-1 flex-wrap'>
              <Badge
                variant={isGpu ? 'default' : 'secondary'}
                className={`font-mono text-xs px-2 py-0.5 ${isGpu ? 'bg-indigo-600 text-white' : ''}`}
              >
                {isGpu ? 'GPU WORKLOAD' : 'CPU PROCESS'}
              </Badge>
              {job.classification && (
                <Badge variant='outline' className='font-mono text-[10px]'>
                  {job.classification}
                </Badge>
              )}
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
              Thông số định danh và mức tiêu thụ tài nguyên thực tế thu thập từ Go Node Agent (/proc & NVML)
            </SheetDescription>
          </SheetHeader>

          <div className='space-y-5 py-5 text-xs'>
            {/* Section: Action Controls */}
            <div className='p-3.5 rounded-lg border bg-zinc-50 dark:bg-zinc-900/50 space-y-2.5'>
              <div className='flex items-center justify-between'>
                <span className='font-bold uppercase tracking-wider text-[11px] text-muted-foreground flex items-center gap-1.5'>
                  <Icons.settings className='size-3.5 text-primary' /> Quản trị Điều khiển Tiến trình
                </span>
                {isProtected && (
                  <Badge variant='destructive' className='text-[10px] py-0'>
                    Bảo vệ hệ thống
                  </Badge>
                )}
              </div>

              <div className='grid grid-cols-3 gap-2'>
                <Button
                  size='sm'
                  variant='outline'
                  onClick={() => setTreeOpen(true)}
                  className='h-8 text-xs'
                >
                  <Icons.terminal className='size-3.5 mr-1 text-primary' /> Cây tiến trình
                </Button>

                <Button
                  size='sm'
                  variant='outline'
                  disabled={isProtected}
                  onClick={() => setStopOpen(true)}
                  className='h-8 text-xs text-amber-600 dark:text-amber-400 border-amber-300 dark:border-amber-800 hover:bg-amber-50 dark:hover:bg-amber-950/30 disabled:opacity-50'
                  title={isProtected ? 'Tiến trình hệ thống được bảo vệ — Không thể dừng' : 'Dừng an toàn có ân hạn'}
                >
                  <Icons.warning className='size-3.5 mr-1' /> Dừng (SIGTERM)
                </Button>

                <Button
                  size='sm'
                  variant='outline'
                  disabled={isProtected}
                  onClick={() => setKillOpen(true)}
                  className='h-8 text-xs text-rose-600 dark:text-rose-400 border-rose-300 dark:border-rose-800 hover:bg-rose-50 dark:hover:bg-rose-950/30 disabled:opacity-50'
                  title={isProtected ? 'Tiến trình hệ thống được bảo vệ — Không thể buộc dừng' : 'Buộc dừng cưỡng chế'}
                >
                  <Icons.warning className='size-3.5 mr-1' /> Buộc dừng (SIGKILL)
                </Button>
              </div>

              {isProtected && (
                <p className='text-[10px] text-rose-600 dark:text-rose-400 leading-tight'>
                  * Tiến trình này thuộc danh sách bảo vệ của hệ điều hành và nền tảng LabOps Control Center.
                </p>
              )}
            </div>

            {/* Section 1: Process Identity */}
            <div className='space-y-2.5'>
              <h4 className='font-bold uppercase tracking-wider text-[11px] text-muted-foreground flex items-center gap-1.5'>
                <Icons.terminal className='size-3.5 text-primary' /> Định danh Tiến trình (Host Process)
              </h4>
              <div className='grid grid-cols-2 gap-2 bg-muted/40 p-3 rounded-lg border font-mono text-[11px]'>
                <div>
                  <span className='text-muted-foreground block text-[10px] font-sans'>Host PID</span>
                  <span className='font-bold text-foreground text-sm'>{job.pid}</span>
                </div>
                <div>
                  <span className='text-muted-foreground block text-[10px] font-sans'>Parent PID (PPID)</span>
                  <span className='font-bold text-foreground text-sm'>{job.ppid ?? '-'}</span>
                </div>
                <div className='col-span-2 pt-1 border-t'>
                  <span className='text-muted-foreground block text-[10px] font-sans'>Process Ref (Chống PID reuse)</span>
                  <span className='font-mono text-[10px] text-muted-foreground break-all'>{job.ref || `track:host:${job.pid}:0`}</span>
                </div>
                <div className='col-span-2 pt-1 border-t'>
                  <span className='text-muted-foreground block text-[10px] font-sans'>Chương trình thực thi</span>
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
                  <span className='text-muted-foreground block text-[10px]'>Tên đầy đủ</span>
                  <span className='font-bold text-foreground text-sm'>{job.fullName}</span>
                </div>
                <div>
                  <span className='text-muted-foreground block text-[10px]'>Máy chủ vật lý</span>
                  <span className='font-bold text-foreground'>{job.serverName || job.serverId}</span>
                </div>
                <div>
                  <span className='text-muted-foreground block text-[10px]'>Workspace / Container</span>
                  <span className='font-mono text-foreground font-semibold'>{job.workspace}</span>
                </div>
              </div>
            </div>

            {/* Section 3: Hardware Resource Consumption */}
            <div className='space-y-2.5'>
              <h4 className='font-bold uppercase tracking-wider text-[11px] text-muted-foreground flex items-center gap-1.5'>
                <Icons.cpu className='size-3.5 text-primary' /> Tiêu thụ Tài nguyên Phần cứng
              </h4>
              <div className='grid grid-cols-3 gap-2 bg-muted/40 p-3 rounded-lg border font-mono text-[11px]'>
                <div>
                  <span className='text-muted-foreground block text-[10px] font-sans'>CPU</span>
                  <span className='font-bold text-foreground text-sm'>{job.cpuPercent}%</span>
                </div>
                <div>
                  <span className='text-muted-foreground block text-[10px] font-sans'>RAM (RSS)</span>
                  <span className='font-bold text-foreground text-sm'>{(job.ramUsedMb / 1024).toFixed(1)} GB</span>
                </div>
                <div>
                  <span className='text-muted-foreground block text-[10px] font-sans'>VRAM GPU</span>
                  <span className={`font-bold text-sm ${isGpu ? 'text-indigo-600 dark:text-indigo-400' : 'text-muted-foreground'}`}>
                    {job.vramUsedMb > 0 ? `${(job.vramUsedMb / 1024).toFixed(1)} GB` : '0 MB'}
                  </span>
                </div>
              </div>
            </div>

            {/* Section 4: Runtime Timing */}
            <div className='space-y-2.5'>
              <h4 className='font-bold uppercase tracking-wider text-[11px] text-muted-foreground flex items-center gap-1.5'>
                <Icons.clock className='size-3.5 text-primary' /> Thời gian Thực thi (Runtime)
              </h4>
              <div className='grid grid-cols-2 gap-2 bg-muted/40 p-3 rounded-lg border text-[11px] font-mono'>
                <div>
                  <span className='text-muted-foreground block text-[10px] font-sans'>Thời điểm khởi chạy</span>
                  <span className='text-foreground font-semibold'>{job.startTime}</span>
                </div>
                <div>
                  <span className='text-muted-foreground block text-[10px] font-sans'>Tổng thời gian chạy</span>
                  <span className='text-primary font-bold'>{job.elapsedTime}</span>
                </div>
              </div>
            </div>

            <Separator />

            <div className='bg-zinc-50 dark:bg-zinc-900 border p-3 rounded-lg text-muted-foreground text-xs flex items-start gap-2'>
              <Icons.shieldAlert className='size-4 shrink-0 text-primary mt-0.5' />
              <p className='leading-relaxed text-[11px]'>
                <strong>Chính sách An toàn LabOps:</strong> Mọi thao tác dừng hoặc buộc dừng tiến trình đều được xác thực danh tính PID qua start_time chống tái sử dụng, đồng thời ghi nhận đầy đủ vào nhật ký kiểm toán hệ thống.
              </p>
            </div>
          </div>
        </SheetContent>
      </Sheet>

      {/* Control Modals */}
      <StopProcessDialog
        job={job}
        open={stopOpen}
        onOpenChange={setStopOpen}
        onSuccess={() => {
          onRefresh?.();
          onOpenChange(false);
        }}
      />

      <ForceKillDialog
        job={job}
        open={killOpen}
        onOpenChange={setKillOpen}
        onSuccess={() => {
          onRefresh?.();
          onOpenChange(false);
        }}
      />

      <ProcessTreeDialog
        job={job}
        open={treeOpen}
        onOpenChange={setTreeOpen}
      />
    </>
  );
}
