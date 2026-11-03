'use client';

import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { JobStatus } from '@/types/labops';
import { toast } from 'sonner';

function getJobStatusBadge(status: JobStatus) {
  switch (status) {
    case 'RUNNING':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-medium'
        >
          <span className='size-1.5 rounded-full bg-emerald-500 animate-pulse mr-1'></span>
          RUNNING
        </Badge>
      );
    case 'PENDING':
      return (
        <Badge
          variant='outline'
          className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[10px] font-medium'
        >
          PENDING
        </Badge>
      );
    case 'FAILED':
      return (
        <Badge
          variant='outline'
          className='bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30 text-[10px] font-medium'
        >
          FAILED
        </Badge>
      );
    case 'CANCELLED':
      return (
        <Badge
          variant='outline'
          className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[10px] font-medium'
        >
          CANCELLED
        </Badge>
      );
    case 'COMPLETED':
    default:
      return (
        <Badge variant='secondary' className='text-muted-foreground text-[10px] font-medium'>
          COMPLETED
        </Badge>
      );
  }
}

export function JobsMonitoringView() {
  const jobs = useLabOpsStore((state) => state.jobs);
  const stopJob = useLabOpsStore((state) => state.stopJob);

  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [serverFilter, setServerFilter] = useState<string>('all');

  const runningCount = jobs.filter((j) => j.status === 'RUNNING').length;
  const pendingCount = jobs.filter((j) => j.status === 'PENDING').length;
  const failedCount = jobs.filter((j) => j.status === 'FAILED').length;
  const completedCount = jobs.filter((j) => j.status === 'COMPLETED').length;

  const filteredJobs = jobs.filter((j) => {
    const matchesSearch =
      j.fullName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      j.username.toLowerCase().includes(searchTerm.toLowerCase()) ||
      j.script.toLowerCase().includes(searchTerm.toLowerCase()) ||
      j.gpuId.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = statusFilter === 'all' || j.status === statusFilter;
    const matchesServer = serverFilter === 'all' || j.serverId === serverFilter;
    return matchesSearch && matchesStatus && matchesServer;
  });

  return (
    <div className='flex flex-col gap-4'>
      {/* KPI Stats on Jobs */}
      <div className='grid grid-cols-2 sm:grid-cols-4 gap-3'>
        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Đang chạy (Running)</span>
          <div className='text-xl font-bold mt-0.5 text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5'>
            <Icons.job className='size-5' />
            {runningCount} tác vụ
          </div>
          <span className='text-[10px] text-muted-foreground'>Chiếm dụng GPU & CPU</span>
        </Card>

        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Hàng đợi (Pending)</span>
          <div className='text-xl font-bold mt-0.5 text-blue-600 dark:text-blue-400'>
            {pendingCount} tác vụ
          </div>
          <span className='text-[10px] text-muted-foreground'>Chờ giải phóng VRAM</span>
        </Card>

        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Đã hoàn thành</span>
          <div className='text-xl font-bold mt-0.5 text-foreground'>{completedCount} tác vụ</div>
          <span className='text-[10px] text-muted-foreground'>Lưu checkpoint thành công</span>
        </Card>

        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Gặp sự cố / Lỗi</span>
          <div className='text-xl font-bold mt-0.5 text-rose-600 dark:text-rose-400'>
            {failedCount} tác vụ
          </div>
          <span className='text-[10px] text-muted-foreground'>CUDA OOM hoặc lỗi script</span>
        </Card>
      </div>

      {/* Filter and Search Bar */}
      <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-3 rounded-lg border bg-card'>
        <div className='relative flex-1 max-w-md'>
          <Icons.search className='absolute left-2.5 top-2.5 size-4 text-muted-foreground' />
          <Input
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder='Tìm kiếm theo lệnh, người dùng, GPU...'
            className='pl-8 h-9 text-xs'
          />
        </div>

        <div className='flex items-center gap-2 flex-wrap'>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả trạng thái ({jobs.length})</option>
            <option value='RUNNING'>Đang chạy ({runningCount})</option>
            <option value='PENDING'>Hàng đợi ({pendingCount})</option>
            <option value='FAILED'>Thất bại ({failedCount})</option>
            <option value='COMPLETED'>Đã hoàn thành ({completedCount})</option>
          </select>

          <select
            value={serverFilter}
            onChange={(e) => setServerFilter(e.target.value)}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả máy chủ</option>
            <option value='track'>Server Track (Hà Nội)</option>
            <option value='workstation'>Server Workstation (TP.HCM)</option>
          </select>
        </div>
      </div>

      {/* Jobs Table */}
      <Card className='border bg-card'>
        <CardContent className='p-0'>
          <div className='overflow-x-auto'>
            <table className='w-full text-left text-xs'>
              <thead>
                <tr className='border-b text-muted-foreground font-semibold bg-muted/20'>
                  <th className='py-2.5 px-3'>Người dùng</th>
                  <th className='py-2.5 px-3'>Máy chủ & Workspace</th>
                  <th className='py-2.5 px-3'>PID / Lệnh thực thi</th>
                  <th className='py-2.5 px-3'>GPU</th>
                  <th className='py-2.5 px-3'>Tải CPU / RAM</th>
                  <th className='py-2.5 px-3'>VRAM Chiếm dụng</th>
                  <th className='py-2.5 px-3'>Thời gian</th>
                  <th className='py-2.5 px-3'>SSH vs Job</th>
                  <th className='py-2.5 px-3 text-right'>Thao tác</th>
                </tr>
              </thead>
              <tbody className='divide-y divide-border/60'>
                {filteredJobs.map((j) => (
                  <tr key={j.id} className='hover:bg-muted/30 transition-colors'>
                    {/* Người dùng */}
                    <td className='py-3 px-3'>
                      <div className='font-bold text-foreground'>{j.fullName}</div>
                      <div className='text-[11px] text-muted-foreground font-mono'>
                        @{j.username}
                      </div>
                    </td>

                    {/* Máy chủ */}
                    <td className='py-3 px-3'>
                      <Badge variant='outline' className='font-mono text-[10px] uppercase'>
                        {j.serverId}
                      </Badge>
                      <div className='text-[11px] text-muted-foreground font-mono mt-0.5'>
                        {j.workspace}
                      </div>
                    </td>

                    {/* PID / Lệnh */}
                    <td className='py-3 px-3 max-w-sm'>
                      <div className='flex items-center gap-1.5'>
                        <span className='font-mono font-bold text-foreground text-[11px]'>
                          {j.pid > 0 ? `PID: ${j.pid}` : 'Chờ phân bổ'}
                        </span>
                        <span className='text-[10px] text-muted-foreground font-mono'>
                          ({j.id})
                        </span>
                      </div>
                      <div
                        className='font-mono text-[11px] text-muted-foreground truncate max-w-[320px]'
                        title={j.script}
                      >
                        {j.script}
                      </div>
                    </td>

                    {/* GPU */}
                    <td className='py-3 px-3'>
                      <Badge
                        variant={j.gpuId === 'CPU Only' ? 'secondary' : 'default'}
                        className='font-mono text-[10px]'
                      >
                        {j.gpuId}
                      </Badge>
                    </td>

                    {/* CPU & RAM */}
                    <td className='py-3 px-3 font-mono text-[11px]'>
                      <div>{j.cpuPercent > 0 ? `${j.cpuPercent}% CPU` : '-'}</div>
                      <div className='text-muted-foreground'>
                        {j.ramUsedMb > 0 ? `${(j.ramUsedMb / 1024).toFixed(1)} GB RAM` : '-'}
                      </div>
                    </td>

                    {/* VRAM */}
                    <td className='py-3 px-3 font-mono text-[11px]'>
                      {j.vramUsedMb > 0 ? (
                        <span className='font-bold text-indigo-600 dark:text-indigo-400'>
                          {(j.vramUsedMb / 1024).toFixed(1)} GB
                        </span>
                      ) : (
                        <span className='text-muted-foreground'>-</span>
                      )}
                    </td>

                    {/* Thời gian */}
                    <td className='py-3 px-3'>
                      <div className='font-medium text-foreground'>{j.elapsedTime}</div>
                      <div className='text-[10px] text-muted-foreground'>{j.startTime}</div>
                    </td>

                    {/* SSH vs Container */}
                    <td className='py-3 px-3'>
                      <div className='flex flex-col gap-1 items-start'>
                        {getJobStatusBadge(j.status)}
                        <span className='text-[10px] text-muted-foreground flex items-center gap-1'>
                          <span
                            className={`size-1.5 rounded-full ${j.sshStatus === 'SSH Online' ? 'bg-emerald-500' : 'bg-slate-400'}`}
                          ></span>
                          {j.sshStatus}
                        </span>
                      </div>
                    </td>

                    {/* Thao tác */}
                    <td className='py-3 px-3 text-right'>
                      {j.status === 'RUNNING' && (
                        <Button
                          size='sm'
                          variant='ghost'
                          onClick={() => {
                            stopJob(j.id);
                            toast.warning(`Đã dừng tiến trình ${j.pid} của @${j.username}`);
                          }}
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
    </div>
  );
}
