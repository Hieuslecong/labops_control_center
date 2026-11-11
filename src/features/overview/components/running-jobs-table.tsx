'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { ProcessDetailSheet } from './process-detail-sheet';
import { ComputeJob } from '@/types/labops';
import Link from 'next/link';

function getJobStatusBadge(status: string) {
  switch (status) {
    case 'RUNNING':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 font-medium text-[11px]'
        >
          <span className='size-1.5 rounded-full bg-emerald-500 animate-pulse mr-1' />
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
  const fetchLiveJobs = useLabOpsStore((state) => state.fetchLiveJobs);
  const lastSyncTime = useLabOpsStore((state) => state.lastSyncTime);
  const syncError = useLabOpsStore((state) => state.syncError);

  // Detail Sheet state
  const [selectedJob, setSelectedJob] = useState<ComputeJob | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  // Auto-refresh state (every 4 seconds)
  const [isAutoRefresh, setIsAutoRefresh] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Filters state
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [serverFilter, setServerFilter] = useState<string>('all');
  const [userFilter, setUserFilter] = useState<string>('all');
  const [gpuFilter, setGpuFilter] = useState<string>('all');

  // Initial and periodic fetch
  useEffect(() => {
    fetchLiveJobs();
    if (!isAutoRefresh) return;

    const timer = setInterval(() => {
      fetchLiveJobs();
    }, 4000);

    return () => clearInterval(timer);
  }, [fetchLiveJobs, isAutoRefresh]);

  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    await fetchLiveJobs();
    setTimeout(() => setIsRefreshing(false), 400);
  };

  // Distinct values for user filter with process count
  const userCounts = useMemo(() => {
    const map: Record<string, number> = {};
    jobs.forEach((j) => {
      if (j.username) {
        map[j.username] = (map[j.username] || 0) + 1;
      }
    });
    return Object.entries(map).toSorted((a, b) => b[1] - a[1]);
  }, [jobs]);

  const gpuJobsCount = useMemo(
    () => jobs.filter((j) => j.isGpu || j.vramUsedMb > 0).length,
    [jobs]
  );
  const cpuJobsCount = useMemo(
    () => jobs.filter((j) => !j.isGpu && j.vramUsedMb <= 0).length,
    [jobs]
  );

  // Filtered jobs
  const filteredJobs = useMemo(() => {
    return jobs.filter((job) => {
      if (searchTerm.trim()) {
        const q = searchTerm.toLowerCase();
        const matchPid = String(job.pid).includes(q);
        const matchScript = job.script.toLowerCase().includes(q);
        const matchCmd = job.cmdline ? job.cmdline.toLowerCase().includes(q) : false;
        const matchUser =
          job.username.toLowerCase().includes(q) || job.fullName.toLowerCase().includes(q);
        const matchGpu = job.gpuId ? job.gpuId.toLowerCase().includes(q) : false;
        if (!matchPid && !matchScript && !matchCmd && !matchUser && !matchGpu) return false;
      }
      if (serverFilter !== 'all' && job.serverId !== serverFilter) return false;
      if (userFilter !== 'all' && job.username !== userFilter) return false;
      if (gpuFilter === 'gpu_only' && !job.isGpu && job.vramUsedMb <= 0) return false;
      if (gpuFilter === 'cpu_only' && (job.isGpu || job.vramUsedMb > 0)) return false;
      return true;
    });
  }, [jobs, searchTerm, serverFilter, userFilter, gpuFilter]);

  const handleRowClick = (job: ComputeJob) => {
    setSelectedJob(job);
    setDetailOpen(true);
  };

  return (
    <>
      <Card className='border bg-card min-w-0 w-full'>
        <CardHeader className='pb-3 pt-4'>
          <div className='flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between'>
            <div className='flex items-center gap-2'>
              <div className='p-2 rounded-lg bg-violet-500/10 text-violet-600 dark:text-violet-400'>
                <Icons.activity className='size-5' />
              </div>
              <div>
                <div className='flex items-center gap-2'>
                  <CardTitle className='text-base font-bold tracking-tight'>
                    Tác vụ & Workloads Đang Chạy
                  </CardTitle>
                  <Badge
                    variant='outline'
                    className={`${
                      jobs.length > 0
                        ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                        : 'bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 border-zinc-500/30'
                    } text-[10px] font-mono`}
                  >
                    <span
                      className={`size-1.5 rounded-full ${
                        jobs.length > 0 ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-400'
                      } mr-1`}
                    />
                    {jobs.length > 0 ? `${jobs.length} TÁC VỤ ĐANG CHẠY` : 'HỆ THỐNG RẢNH (IDLE)'}
                  </Badge>
                </div>
                <p className='text-xs text-muted-foreground mt-0.5'>
                  Dữ liệu thời gian thực từ Linux host (/proc, psutil) và NVIDIA NVML. Tự động đồng
                  bộ mỗi 4 giây.
                </p>
              </div>
            </div>

            <div className='flex items-center gap-2 text-xs'>
              {/* Sync status */}
              <div className='text-[11px] text-muted-foreground flex items-center gap-1 font-mono'>
                <span
                  className={`size-1.5 rounded-full ${syncError ? 'bg-amber-500' : 'bg-emerald-500'}`}
                />
                {lastSyncTime ? `Đồng bộ: ${lastSyncTime}` : 'Đang đồng bộ...'}
              </div>

              {/* Pause/Resume Auto-refresh */}
              <Button
                variant='outline'
                size='sm'
                onClick={() => setIsAutoRefresh(!isAutoRefresh)}
                className='h-7 text-xs px-2'
                title={isAutoRefresh ? 'Tạm dừng tự động đồng bộ' : 'Tiếp tục tự động đồng bộ'}
              >
                {isAutoRefresh ? (
                  <>
                    <Icons.pause className='size-3 mr-1 text-amber-500' /> Dừng cập nhật
                  </>
                ) : (
                  <>
                    <Icons.play className='size-3 mr-1 text-emerald-500' /> Tự động
                  </>
                )}
              </Button>

              {/* Manual Refresh Button */}
              <Button
                variant='outline'
                size='sm'
                onClick={handleManualRefresh}
                disabled={isRefreshing}
                className='h-7 text-xs px-2'
              >
                <Icons.refresh
                  className={`size-3 mr-1 ${isRefreshing ? 'animate-spin text-primary' : ''}`}
                />
                Làm mới
              </Button>

              <Link
                href='/dashboard/jobs'
                className='text-xs font-medium text-primary hover:underline flex items-center gap-1 ml-1'
              >
                Xem chi tiết <Icons.arrowRight className='size-3.5' />
              </Link>
            </div>
          </div>

          {/* Stale warning banner if sync failed */}
          {syncError && (
            <div className='mt-2.5 p-2 rounded bg-amber-500/10 border border-amber-500/30 text-amber-800 dark:text-amber-200 text-xs flex items-center gap-2'>
              <Icons.alertCircle className='size-4 text-amber-500 shrink-0' />
              <span>
                <strong>Cảnh báo dữ liệu:</strong> {syncError}. Dữ liệu hiển thị có thể chưa phản
                ánh tiến trình mới nhất.
              </span>
            </div>
          )}

          {/* Quick Filter Pills */}
          <div className='mt-2.5 flex items-center gap-1.5 flex-wrap'>
            <Button
              size='sm'
              variant={gpuFilter === 'all' ? 'default' : 'outline'}
              onClick={() => setGpuFilter('all')}
              className='h-6 text-[11px] px-2.5 rounded-full'
            >
              Tất cả ({jobs.length})
            </Button>
            <Button
              size='sm'
              variant={gpuFilter === 'gpu_only' ? 'default' : 'outline'}
              onClick={() => setGpuFilter('gpu_only')}
              className={`h-6 text-[11px] px-2.5 rounded-full ${
                gpuFilter === 'gpu_only'
                  ? 'bg-indigo-600 hover:bg-indigo-700 text-white'
                  : 'text-indigo-600 dark:text-indigo-400 border-indigo-500/30 hover:bg-indigo-500/10'
              }`}
            >
              <Icons.gpu className='size-3 mr-1' />⚡ GPU ({gpuJobsCount})
            </Button>
            <Button
              size='sm'
              variant={gpuFilter === 'cpu_only' ? 'default' : 'outline'}
              onClick={() => setGpuFilter('cpu_only')}
              className={`h-6 text-[11px] px-2.5 rounded-full ${
                gpuFilter === 'cpu_only'
                  ? 'bg-blue-600 hover:bg-blue-700 text-white'
                  : 'text-blue-600 dark:text-blue-400 border-blue-500/30 hover:bg-blue-500/10'
              }`}
            >
              <Icons.cpu className='size-3 mr-1' />
              🖥️ CPU ({cpuJobsCount})
            </Button>
          </div>

          {/* Filters Bar */}
          <div className='mt-3 pt-3 border-t flex flex-wrap items-center gap-2 text-xs'>
            {/* Search Box */}
            <div className='relative flex-1 min-w-[200px] max-w-xs'>
              <Icons.search className='absolute left-2.5 top-2 size-3.5 text-muted-foreground' />
              <Input
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder='Tìm theo PID, script, lệnh, user...'
                className='pl-8 h-7 text-xs'
              />
            </div>

            <div className='flex items-center gap-1.5'>
              <span className='text-muted-foreground text-[11px] font-medium'>Máy chủ:</span>
              <select
                value={serverFilter}
                onChange={(e) => setServerFilter(e.target.value)}
                className='h-7 rounded-md border border-input bg-background px-2 text-xs focus:ring-1 focus:ring-primary outline-none'
              >
                <option value='all'>Tất cả máy chủ</option>
                <option value='workstation'>Server 02 — Workstation</option>
                <option value='track'>Server 01 — Track</option>
              </select>
            </div>

            <div className='flex items-center gap-1.5'>
              <span className='text-muted-foreground text-[11px] font-medium'>Người dùng:</span>
              <select
                value={userFilter}
                onChange={(e) => setUserFilter(e.target.value)}
                className='h-7 rounded-md border border-input bg-background px-2 text-xs focus:ring-1 focus:ring-primary outline-none'
              >
                <option value='all'>Tất cả tài khoản ({userCounts.length})</option>
                {userCounts.map(([uname, count]) => (
                  <option key={uname} value={uname}>
                    @{uname} ({count})
                  </option>
                ))}
              </select>
            </div>

            <div className='flex items-center gap-1.5'>
              <span className='text-muted-foreground text-[11px] font-medium'>Tài nguyên:</span>
              <select
                value={gpuFilter}
                onChange={(e) => setGpuFilter(e.target.value)}
                className='h-7 rounded-md border border-input bg-background px-2 text-xs focus:ring-1 focus:ring-primary outline-none'
              >
                <option value='all'>Tất cả (GPU & CPU)</option>
                <option value='gpu_only'>⚡ Chỉ GPU (CUDA)</option>
                <option value='cpu_only'>🖥️ Chỉ CPU thuần</option>
              </select>
            </div>

            {(serverFilter !== 'all' ||
              userFilter !== 'all' ||
              gpuFilter !== 'all' ||
              searchTerm.trim() !== '') && (
              <Button
                variant='ghost'
                size='sm'
                onClick={() => {
                  setSearchTerm('');
                  setServerFilter('all');
                  setUserFilter('all');
                  setGpuFilter('all');
                }}
                className='h-7 text-xs text-muted-foreground hover:text-foreground'
              >
                <Icons.close className='size-3 mr-1' /> Đặt lại bộ lọc
              </Button>
            )}

            <span className='ml-auto text-[11px] text-muted-foreground hidden lg:inline'>
              Hiển thị {filteredJobs.length} / {jobs.length} tiến trình
            </span>
          </div>
        </CardHeader>

        <CardContent className='pt-0 pb-3'>
          {filteredJobs.length === 0 ? (
            <div className='py-12 text-center text-muted-foreground'>
              <Icons.terminal className='size-8 mx-auto text-muted-foreground/40 mb-2' />
              <p className='text-sm font-medium'>Không có tiến trình nào khớp với bộ lọc</p>
              <p className='text-xs text-muted-foreground/80 mt-1 max-w-md mx-auto'>
                Hệ thống chỉ báo cáo tiến trình thật sự đang chạy trên máy chủ. Tuyệt đối không sinh
                dữ liệu giả định.
              </p>
            </div>
          ) : (
            <div className='overflow-x-auto'>
              <table className='w-full text-left text-xs'>
                <thead>
                  <tr className='border-b text-muted-foreground font-semibold'>
                    <th className='py-2 px-3'>Máy chủ</th>
                    <th className='py-2 px-3'>Thành viên</th>
                    <th className='py-2 px-3'>Workspace</th>
                    <th className='py-2 px-3'>PID</th>
                    <th className='py-2 px-3'>Chương trình</th>
                    <th className='py-2 px-3'>GPU</th>
                    <th className='py-2 px-3'>CPU</th>
                    <th className='py-2 px-3'>RAM</th>
                    <th className='py-2 px-3'>VRAM</th>
                    <th className='py-2 px-3'>Thời gian chạy</th>
                    <th className='py-2 px-3'>Trạng thái</th>
                    <th className='py-2 px-3 text-right'>Chi tiết</th>
                  </tr>
                </thead>
                <tbody className='divide-y divide-border/60'>
                  {filteredJobs.map((job) => {
                    const isGpuJob = job.isGpu || (job.vramUsedMb || 0) > 0;
                    return (
                      <tr
                        key={job.id}
                        onClick={() => handleRowClick(job)}
                        className={`cursor-pointer transition-colors ${
                          isGpuJob
                            ? 'bg-indigo-500/5 hover:bg-indigo-500/10 border-l-4 border-l-indigo-600'
                            : 'hover:bg-muted/40'
                        }`}
                        title='Nhấp để xem đầy đủ thông số định danh và lệnh khởi chạy'
                      >
                        {/* Máy chủ */}
                        <td className='py-2.5 px-3 whitespace-nowrap'>
                          <Badge
                            variant='outline'
                            className={`font-mono text-[10px] uppercase ${
                              job.serverId === 'track'
                                ? 'border-blue-500/40 text-blue-500'
                                : 'border-purple-500/40 text-purple-500'
                            }`}
                          >
                            {job.serverId}
                          </Badge>
                        </td>

                        {/* Thành viên */}
                        <td className='py-2.5 px-3'>
                          <div className='font-semibold text-foreground'>{job.fullName}</div>
                          <div className='text-[11px] text-muted-foreground font-mono'>
                            @{job.username}
                          </div>
                        </td>

                        {/* Workspace */}
                        <td className='py-2.5 px-3 font-mono text-[11px] text-muted-foreground'>
                          {job.workspace}
                        </td>

                        {/* PID */}
                        <td className='py-2.5 px-3'>
                          <span className='font-mono font-bold text-foreground text-xs'>
                            {job.pid}
                          </span>
                        </td>

                        {/* Chương trình / Script */}
                        <td className='py-2.5 px-3 max-w-xs'>
                          <div
                            className='font-mono text-[11px] text-foreground font-medium truncate max-w-[240px]'
                            title={job.cmdline || job.script}
                          >
                            {job.script}
                          </div>
                          {job.cmdline && (
                            <div className='text-[10px] text-muted-foreground truncate max-w-[240px] font-mono'>
                              {job.cmdline.slice(0, 50)}...
                            </div>
                          )}
                        </td>

                        {/* GPU */}
                        <td className='py-2.5 px-3 whitespace-nowrap'>
                          {isGpuJob ? (
                            <div className='flex flex-col gap-0.5 items-start'>
                              <Badge
                                variant='default'
                                className='font-mono text-[10px] bg-indigo-600 hover:bg-indigo-600 text-white gap-1'
                              >
                                <span className='size-1 rounded-full bg-white animate-pulse' />
                                {job.gpuId || 'WS-GPU-01'}
                              </Badge>
                              <span className='text-[10px] text-indigo-600 dark:text-indigo-400 font-mono font-bold'>
                                {((job.vramUsedMb || 0) / 1024).toFixed(1)} GB
                              </span>
                            </div>
                          ) : (
                            <span className='text-muted-foreground font-mono text-[11px]'>-</span>
                          )}
                        </td>

                        {/* CPU */}
                        <td className='py-2.5 px-3 font-mono text-[11px] whitespace-nowrap'>
                          <span className={job.cpuPercent > 50 ? 'text-amber-500 font-bold' : ''}>
                            {job.cpuPercent > 0 ? `${job.cpuPercent}%` : '0%'}
                          </span>
                        </td>

                        {/* RAM */}
                        <td className='py-2.5 px-3 font-mono text-[11px] whitespace-nowrap'>
                          {job.ramUsedMb > 0 ? (
                            <span>{(job.ramUsedMb / 1024).toFixed(1)} GB</span>
                          ) : (
                            <span className='text-muted-foreground'>-</span>
                          )}
                        </td>

                        {/* VRAM */}
                        <td className='py-2.5 px-3 font-mono text-[11px] whitespace-nowrap'>
                          {job.vramUsedMb > 0 ? (
                            <span className='font-bold text-indigo-600 dark:text-indigo-400'>
                              {(job.vramUsedMb / 1024).toFixed(1)} GB
                            </span>
                          ) : (
                            <span className='text-muted-foreground'>-</span>
                          )}
                        </td>

                        {/* Thời gian chạy */}
                        <td className='py-2.5 px-3 whitespace-nowrap'>
                          <div className='font-medium text-foreground font-mono text-[11px]'>
                            {job.elapsedTime}
                          </div>
                          <div className='text-[10px] text-muted-foreground'>{job.startTime}</div>
                        </td>

                        {/* Trạng thái */}
                        <td className='py-2.5 px-3 whitespace-nowrap'>
                          <div className='flex flex-col gap-1 items-start'>
                            {getJobStatusBadge(job.status)}
                            <span className='text-[10px] text-muted-foreground flex items-center gap-1'>
                              <span
                                className={`size-1.5 rounded-full ${
                                  job.sshStatus.includes('Online')
                                    ? 'bg-emerald-500'
                                    : 'bg-slate-400'
                                }`}
                              />
                              {job.sshStatus}
                            </span>
                          </div>
                        </td>

                        {/* Nút Xem chi tiết */}
                        <td className='py-2.5 px-3 text-right whitespace-nowrap'>
                          <Button
                            size='sm'
                            variant='ghost'
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRowClick(job);
                            }}
                            className='h-7 text-xs text-primary hover:bg-primary/10'
                          >
                            <Icons.view className='size-3.5 mr-1' /> Chi tiết
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Slide-over Sheet for Real Process Details */}
      <ProcessDetailSheet job={selectedJob} open={detailOpen} onOpenChange={setDetailOpen} />
    </>
  );
}
