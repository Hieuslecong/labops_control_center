'use client';

import React from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import Link from 'next/link';

function getJobStatusBadge(status: string) {
  switch (status) {
    case 'RUNNING':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 font-medium text-[11px]'
        >
          <span className='size-1.5 rounded-full bg-emerald-500 animate-pulse mr-1'></span>
          RUNNING
        </Badge>
      );
    case 'PENDING':
      return (
        <Badge
          variant='outline'
          className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 font-medium text-[11px]'
        >
          PENDING
        </Badge>
      );
    case 'FAILED':
      return (
        <Badge
          variant='outline'
          className='bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30 font-medium text-[11px]'
        >
          FAILED
        </Badge>
      );
    case 'COMPLETED':
    default:
      return (
        <Badge variant='secondary' className='text-muted-foreground font-medium text-[11px]'>
          COMPLETED
        </Badge>
      );
  }
}

export function RunningJobsTable() {
  const jobs = useLabOpsStore((state) => state.jobs);
  const stopJob = useLabOpsStore((state) => state.stopJob);

  return (
    <Card className='border bg-card'>
      <CardHeader className='pb-3 pt-4'>
        <div className='flex items-center justify-between'>
          <div className='flex items-center gap-2'>
            <Icons.job className='size-5 text-violet-600 dark:text-violet-400' />
            <div>
              <CardTitle className='text-base font-bold tracking-tight'>
                Tiến trình Tính toán & Workloads Đang Chạy
              </CardTitle>
              <p className='text-xs text-muted-foreground'>
                Giám sát thời gian thực tiến trình PyTorch, CUDA kernels và tài nguyên container
                Incus
              </p>
            </div>
          </div>
          <Link
            href='/dashboard/jobs'
            className='text-xs font-medium text-primary hover:underline flex items-center gap-1'
          >
            Xem lịch sử tất cả tác vụ <Icons.arrowRight className='size-3.5' />
          </Link>
        </div>
      </CardHeader>

      <CardContent className='pt-0 pb-3'>
        <div className='overflow-x-auto'>
          <table className='w-full text-left text-xs'>
            <thead>
              <tr className='border-b text-muted-foreground font-semibold'>
                <th className='py-2 px-3'>Người dùng</th>
                <th className='py-2 px-3'>Máy chủ & Workspace</th>
                <th className='py-2 px-3'>PID / Lệnh thực thi</th>
                <th className='py-2 px-3'>GPU</th>
                <th className='py-2 px-3'>CPU / RAM</th>
                <th className='py-2 px-3'>VRAM</th>
                <th className='py-2 px-3'>Thời gian chạy</th>
                <th className='py-2 px-3'>SSH / Trạng thái</th>
                <th className='py-2 px-3 text-right'>Thao tác</th>
              </tr>
            </thead>
            <tbody className='divide-y divide-border/60'>
              {jobs.map((job) => (
                <tr key={job.id} className='hover:bg-muted/30 transition-colors'>
                  {/* Người dùng */}
                  <td className='py-2.5 px-3'>
                    <div className='font-semibold text-foreground'>{job.fullName}</div>
                    <div className='text-[11px] text-muted-foreground font-mono'>
                      @{job.username}
                    </div>
                  </td>

                  {/* Máy chủ & Workspace */}
                  <td className='py-2.5 px-3'>
                    <Badge variant='outline' className='font-medium text-[10px] uppercase'>
                      {job.serverId}
                    </Badge>
                    <div className='text-[11px] text-muted-foreground font-mono mt-0.5'>
                      {job.workspace}
                    </div>
                  </td>

                  {/* PID / Lệnh */}
                  <td className='py-2.5 px-3 max-w-xs'>
                    <div className='flex items-center gap-1.5'>
                      <span className='font-mono font-bold text-foreground text-[11px]'>
                        PID: {job.pid > 0 ? job.pid : 'Chờ'}
                      </span>
                    </div>
                    <div
                      className='font-mono text-[11px] text-muted-foreground truncate max-w-[280px]'
                      title={job.script}
                    >
                      {job.script}
                    </div>
                  </td>

                  {/* GPU */}
                  <td className='py-2.5 px-3'>
                    <Badge
                      variant={job.gpuId === 'CPU Only' ? 'secondary' : 'default'}
                      className='font-mono text-[10px]'
                    >
                      {job.gpuId}
                    </Badge>
                  </td>

                  {/* CPU / RAM */}
                  <td className='py-2.5 px-3 font-mono text-[11px]'>
                    <div>{job.cpuPercent > 0 ? `${job.cpuPercent}% CPU` : '-'}</div>
                    <div className='text-muted-foreground'>
                      {job.ramUsedMb > 0 ? `${(job.ramUsedMb / 1024).toFixed(1)} GB RAM` : '-'}
                    </div>
                  </td>

                  {/* VRAM */}
                  <td className='py-2.5 px-3 font-mono text-[11px]'>
                    {job.vramUsedMb > 0 ? (
                      <span className='font-bold text-indigo-600 dark:text-indigo-400'>
                        {(job.vramUsedMb / 1024).toFixed(1)} GB
                      </span>
                    ) : (
                      <span className='text-muted-foreground'>-</span>
                    )}
                  </td>

                  {/* Thời gian */}
                  <td className='py-2.5 px-3'>
                    <div className='font-medium text-foreground'>{job.elapsedTime}</div>
                    <div className='text-[10px] text-muted-foreground'>{job.startTime}</div>
                  </td>

                  {/* SSH / Trạng thái */}
                  <td className='py-2.5 px-3'>
                    <div className='flex flex-col gap-1 items-start'>
                      {getJobStatusBadge(job.status)}
                      <span className='text-[10px] text-muted-foreground flex items-center gap-1'>
                        <span
                          className={`size-1.5 rounded-full ${job.sshStatus === 'SSH Online' ? 'bg-emerald-500' : 'bg-slate-400'}`}
                        ></span>
                        {job.sshStatus}
                      </span>
                    </div>
                  </td>

                  {/* Thao tác */}
                  <td className='py-2.5 px-3 text-right'>
                    {job.status === 'RUNNING' && (
                      <Button
                        size='sm'
                        variant='ghost'
                        onClick={() => stopJob(job.id)}
                        className='h-7 text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-500/10'
                      >
                        <Icons.trash className='size-3.5 mr-1' /> Dừng
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
