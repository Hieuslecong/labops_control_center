'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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

  // Distinct values for filter dropdowns
  const uniqueUsers = useMemo(() => {
    const set = new Set<string>();
    jobs.forEach((j) => {
      if (j.username) set.add(j.username);
    });
    return Array.from(set);
  }, [jobs]);

  // Filtered jobs
  const filteredJobs = useMemo(() => {
    return jobs.filter((job) => {
      if (serverFilter !== 'all' && job.serverId !== serverFilter) return false;
      if (userFilter !== 'all' && job.username !== userFilter) return false;
      if (gpuFilter === 'gpu_only' && !job.isGpu && job.vramUsedMb <= 0) return false;
      if (gpuFilter === 'cpu_only' && (job.isGpu || job.vramUsedMb > 0)) return false;
      return true;
    });
  }, [jobs, serverFilter, userFilter, gpuFilter]);

  const handleRowClick = (job: ComputeJob) => {
    setSelectedJob(job);
    setDetailOpen(true);
  };

  return (
    <>
      <Card className='border bg-card'>
        <CardHeader className='pb-3 pt-4'>
          <div className='flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between'>
            <div className='flex items-center gap-2'>
              <div className='p-2 rounded-lg bg-violet-500/10 text-violet-600 dark:text-violet-400'>
                <Icons.activity className='size-5' />
              </div>
              <div>
                <div className='flex items-center gap-2'>
                  <CardTitle className='text-base font-bold tracking-tight'>
                    Tiến trình & Workloads Đang Chạy
                  </CardTitle>
                  <Badge
                    variant='outline'
                    className='bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-mono'
                  >
                    <span className='size-1.5 rounded-full bg-emerald-500 animate-pulse mr-1' />
                    {jobs.length} TIẾN TRÌNH THỰC TẾ
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

          {/* Filters Bar */}
          <div className='mt-3 pt-3 border-t grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center text-xs'>
            <div className='flex items-center gap-1.5'>
              <span className='text-muted-foreground text-[11px] font-medium'>Máy chủ:</span>
              <select
                value={serverFilter}
                onChange={(e) => setServerFilter(e.target.value)}
                className='h-7 rounded-md border border-input bg-background px-2 text-xs focus:ring-1 focus:ring-primary outline-none'
              >
                <option value='all'>Tất cả máy chủ</option>
                <option value='workstation'>Server 02 — Workstation (Online)</option>
                <option value='track'>Server 01 — Track (Mất kết nối)</option>
              </select>
            </div>

            <div className='flex items-center gap-1.5'>
              <span className='text-muted-foreground text-[11px] font-medium'>Người dùng:</span>
              <select
                value={userFilter}
                onChange={(e) => setUserFilter(e.target.value)}
                className='h-7 rounded-md border border-input bg-background px-2 text-xs focus:ring-1 focus:ring-primary outline-none'
              >
                <option value='all'>Tất cả tài khoản</option>
                {uniqueUsers.map((u) => (
                  <option key={u} value={u}>
                    @{u}
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
                <option value='gpu_only'>Chỉ tiến trình GPU (CUDA)</option>
                <option value='cpu_only'>Tiến trình CPU thuần</option>
              </select>
            </div>

            {(serverFilter !== 'all' || userFilter !== 'all' || gpuFilter !== 'all') && (
              <Button
                variant='ghost'
                size='sm'
                onClick={() => {
                  setServerFilter('all');
                  setUserFilter('all');
                  setGpuFilter('all');
                }}
                className='h-7 text-xs text-muted-foreground hover:text-foreground'
              >
                Đặt lại bộ lọc
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
                  {filteredJobs.map((job) => (
                    <tr
                      key={job.id}
                      onClick={() => handleRowClick(job)}
                      className='hover:bg-muted/40 cursor-pointer transition-colors'
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
                        {job.isGpu || job.vramUsedMb > 0 ? (
                          <Badge
                            variant='default'
                            className='font-mono text-[10px] bg-indigo-600 hover:bg-indigo-600'
                          >
                            {job.gpuId || 'WS-GPU-01'}
                          </Badge>
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
                                job.sshStatus.includes('Online') ? 'bg-emerald-500' : 'bg-slate-400'
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
                  ))}
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
