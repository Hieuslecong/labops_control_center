'use client';

import React, { useState, useEffect } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { ProcessDetailSheet } from '@/features/overview/components/process-detail-sheet';
import { JobStatus, ComputeJob } from '@/types/labops';

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
  const fetchLiveJobs = useLabOpsStore((state) => state.fetchLiveJobs);
  const fetchLiveState = useLabOpsStore((state) => state.fetchLiveState);
  const lastSyncTime = useLabOpsStore((state) => state.lastSyncTime);
  const syncError = useLabOpsStore((state) => state.syncError);

  const [searchTerm, setSearchTerm] = useState('');
  const [resourceFilter, setResourceFilter] = useState<'all' | 'gpu' | 'cpu'>('all');
  const [userFilter, setUserFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [serverFilter, setServerFilter] = useState<string>('all');
  const [selectedJob, setSelectedJob] = useState<ComputeJob | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [isAutoRefresh, setIsAutoRefresh] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);

  useEffect(() => {
    fetchLiveJobs();
    fetchLiveState();
    if (!isAutoRefresh) return;
    const timer = setInterval(() => {
      fetchLiveJobs();
      fetchLiveState();
    }, 4000);
    return () => clearInterval(timer);
  }, [fetchLiveJobs, fetchLiveState, isAutoRefresh]);

  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    await Promise.all([fetchLiveJobs(), fetchLiveState()]);
    setTimeout(() => setIsRefreshing(false), 400);
  };

  const handleResetFilters = () => {
    setSearchTerm('');
    setResourceFilter('all');
    setUserFilter('all');
    setStatusFilter('all');
    setServerFilter('all');
  };

  const hasActiveFilters =
    searchTerm.trim() !== '' ||
    resourceFilter !== 'all' ||
    userFilter !== 'all' ||
    statusFilter !== 'all' ||
    serverFilter !== 'all';

  // Distinct users and process counts
  const userCounts = React.useMemo(() => {
    const map: Record<string, { count: number; fullName?: string }> = {};
    jobs.forEach((j) => {
      if (j.username) {
        if (!map[j.username]) {
          map[j.username] = { count: 0, fullName: j.fullName };
        }
        map[j.username].count += 1;
      }
    });
    return Object.entries(map).toSorted((a, b) => b[1].count - a[1].count);
  }, [jobs]);

  const runningCount = jobs.filter((j) => j.status === 'RUNNING').length;
  const pendingCount = jobs.filter((j) => j.status === 'PENDING').length;
  const failedCount = jobs.filter((j) => j.status === 'FAILED').length;
  const completedCount = jobs.filter((j) => j.status === 'COMPLETED').length;

  const gpuJobsCount = jobs.filter((j) => j.isGpu || j.vramUsedMb > 0).length;
  const cpuJobsCount = jobs.filter((j) => !j.isGpu && j.vramUsedMb <= 0).length;
  const totalVramMb = jobs.reduce((acc, j) => acc + (j.vramUsedMb || 0), 0);

  const filteredJobs = React.useMemo(() => {
    return jobs.filter((j) => {
      // 1. Text Search (PID, script, cmdline, username, fullName, gpuId)
      if (searchTerm.trim()) {
        const q = searchTerm.toLowerCase();
        const matchPid = String(j.pid).includes(q);
        const matchScript = j.script.toLowerCase().includes(q);
        const matchCmd = j.cmdline ? j.cmdline.toLowerCase().includes(q) : false;
        const matchUser =
          j.username.toLowerCase().includes(q) || j.fullName.toLowerCase().includes(q);
        const matchGpu = j.gpuId ? j.gpuId.toLowerCase().includes(q) : false;
        if (!matchPid && !matchScript && !matchCmd && !matchUser && !matchGpu) return false;
      }

      // 2. Resource Filter (GPU vs CPU)
      if (resourceFilter === 'gpu' && !j.isGpu && j.vramUsedMb <= 0) return false;
      if (resourceFilter === 'cpu' && (j.isGpu || j.vramUsedMb > 0)) return false;

      // 3. User Filter
      if (userFilter !== 'all' && j.username !== userFilter) return false;

      // 4. Server Filter
      if (serverFilter !== 'all' && j.serverId !== serverFilter) return false;

      // 5. Status Filter
      if (statusFilter !== 'all' && j.status !== statusFilter) return false;

      return true;
    });
  }, [jobs, searchTerm, resourceFilter, userFilter, serverFilter, statusFilter]);

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
          <span className='text-[10px] text-muted-foreground'>Theo dõi tiến trình Linux host</span>
        </Card>

        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            ⚡ Tác vụ GPU (CUDA)
          </span>
          <div className='text-xl font-bold mt-0.5 text-indigo-600 dark:text-indigo-400 flex items-center gap-1.5'>
            <Icons.gpu className='size-5' />
            {gpuJobsCount} tác vụ
          </div>
          <span className='text-[10px] text-muted-foreground'>
            Chiếm {(totalVramMb / 1024).toFixed(1)} GB VRAM
          </span>
        </Card>

        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>🖥️ Tác vụ CPU</span>
          <div className='text-xl font-bold mt-0.5 text-blue-600 dark:text-blue-400 flex items-center gap-1.5'>
            <Icons.cpu className='size-5' />
            {cpuJobsCount} tác vụ
          </div>
          <span className='text-[10px] text-muted-foreground'>Tiến trình tính toán thuần CPU</span>
        </Card>

        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Người dùng đang chạy
          </span>
          <div className='text-xl font-bold mt-0.5 text-foreground flex items-center gap-1.5'>
            <Icons.user className='size-5' />
            {userCounts.length} tài khoản
          </div>
          <span className='text-[10px] text-muted-foreground'>
            {userCounts
              .slice(0, 3)
              .map(([u]) => `@${u}`)
              .join(', ')}
            {userCounts.length > 3 ? '...' : ''}
          </span>
        </Card>
      </div>

      {/* Sync error alert */}
      {syncError && (
        <div className='p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-800 dark:text-amber-200 text-xs flex items-center gap-2'>
          <Icons.warning className='size-4 text-amber-500 shrink-0' />
          <span>
            <strong>Cảnh báo kết nối:</strong> {syncError}. Dữ liệu có thể chưa phản ánh tiến trình
            mới nhất.
          </span>
        </div>
      )}

      {/* Quick Filter Pills & Polling Controls */}
      <div className='flex flex-wrap items-center justify-between gap-2.5 p-2.5 rounded-lg border bg-card/60'>
        {/* Quick Resource Pills */}
        <div className='flex items-center gap-1.5 flex-wrap'>
          <span className='text-[11px] font-semibold text-muted-foreground mr-1'>Lọc nhanh:</span>
          <Button
            size='sm'
            variant={resourceFilter === 'all' ? 'default' : 'outline'}
            onClick={() => setResourceFilter('all')}
            className='h-7 text-xs px-2.5 rounded-full'
          >
            Tất cả ({jobs.length})
          </Button>

          <Button
            size='sm'
            variant={resourceFilter === 'gpu' ? 'default' : 'outline'}
            onClick={() => setResourceFilter(resourceFilter === 'gpu' ? 'all' : 'gpu')}
            className={`h-7 text-xs px-2.5 rounded-full ${
              resourceFilter === 'gpu'
                ? 'bg-indigo-600 hover:bg-indigo-700 text-white'
                : 'text-indigo-600 dark:text-indigo-400 border-indigo-500/30 hover:bg-indigo-500/10'
            }`}
          >
            <Icons.gpu className='size-3 mr-1' />
            {gpuJobsCount > 0 && (
              <span className='size-1.5 rounded-full bg-indigo-400 animate-pulse mr-1' />
            )}
            ⚡ GPU CUDA ({gpuJobsCount})
          </Button>

          <Button
            size='sm'
            variant={resourceFilter === 'cpu' ? 'default' : 'outline'}
            onClick={() => setResourceFilter('cpu')}
            className={`h-7 text-xs px-2.5 rounded-full ${
              resourceFilter === 'cpu'
                ? 'bg-blue-600 hover:bg-blue-700 text-white'
                : 'text-blue-600 dark:text-blue-400 border-blue-500/30 hover:bg-blue-500/10'
            }`}
          >
            <Icons.cpu className='size-3 mr-1' />
            🖥️ Chỉ CPU ({cpuJobsCount})
          </Button>

          {hasActiveFilters && (
            <Button
              size='sm'
              variant='ghost'
              onClick={handleResetFilters}
              className='h-7 text-xs px-2 text-muted-foreground hover:text-foreground'
            >
              <Icons.close className='size-3 mr-1' /> Xóa bộ lọc
            </Button>
          )}
        </div>

        {/* Polling & Refresh Actions */}
        <div className='flex items-center gap-2'>
          <Button
            size='sm'
            variant='outline'
            onClick={() => setIsAutoRefresh(!isAutoRefresh)}
            className='h-7 text-xs px-2 text-muted-foreground'
            title={isAutoRefresh ? 'Tạm dừng tự động cập nhật' : 'Bật tự động cập nhật'}
          >
            {isAutoRefresh ? (
              <>
                <Icons.pause className='size-3 mr-1 text-amber-500' /> Tạm dừng
              </>
            ) : (
              <>
                <Icons.play className='size-3 mr-1 text-emerald-500' /> Tiếp tục
              </>
            )}
          </Button>

          <Button
            size='sm'
            variant='outline'
            onClick={handleManualRefresh}
            disabled={isRefreshing}
            className='h-7 text-xs gap-1.5'
          >
            <Icons.refresh className={`size-3.5 ${isRefreshing ? 'animate-spin' : ''}`} /> Làm mới
          </Button>

          {lastSyncTime && (
            <span className='text-[10px] text-muted-foreground flex items-center gap-1 font-mono'>
              <span
                className={`size-1.5 rounded-full ${isAutoRefresh ? 'bg-emerald-500 animate-pulse' : 'bg-amber-400'}`}
              ></span>
              {lastSyncTime}
            </span>
          )}
        </div>
      </div>

      {/* Advanced Filter and Search Bar */}
      <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-3 rounded-lg border bg-card'>
        <div className='relative flex-1 max-w-md'>
          <Icons.search className='absolute left-2.5 top-2.5 size-4 text-muted-foreground' />
          <Input
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder='Tìm kiếm theo PID, lệnh thực thi, script, GPU, người dùng...'
            className='pl-8 h-9 text-xs'
          />
        </div>

        <div className='flex items-center gap-2 flex-wrap'>
          {/* Lọc theo Tài nguyên */}
          <select
            value={resourceFilter}
            onChange={(e) => setResourceFilter(e.target.value as 'all' | 'gpu' | 'cpu')}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả tài nguyên ({jobs.length})</option>
            <option value='gpu'>⚡ Chỉ GPU CUDA ({gpuJobsCount})</option>
            <option value='cpu'>🖥️ Chỉ CPU ({cpuJobsCount})</option>
          </select>

          {/* Lọc theo Người dùng */}
          <select
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value)}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả người dùng ({userCounts.length})</option>
            {userCounts.map(([uname, info]) => (
              <option key={uname} value={uname}>
                @{uname} ({info.count} tác vụ)
              </option>
            ))}
          </select>

          {/* Lọc theo Máy chủ */}
          <select
            value={serverFilter}
            onChange={(e) => setServerFilter(e.target.value)}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả máy chủ</option>
            <option value='workstation'>Server 02 — Workstation</option>
            <option value='track'>Server 01 — Track</option>
          </select>

          {/* Lọc theo Trạng thái */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả trạng thái</option>
            <option value='RUNNING'>Đang chạy ({runningCount})</option>
            <option value='PENDING'>Hàng đợi ({pendingCount})</option>
            <option value='FAILED'>Thất bại ({failedCount})</option>
            <option value='COMPLETED'>Đã hoàn thành ({completedCount})</option>
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
                {filteredJobs.length === 0 ? (
                  <tr>
                    <td colSpan={9} className='py-12 text-center text-xs text-muted-foreground'>
                      {jobs.length === 0 ? (
                        <div className='flex flex-col items-center justify-center gap-2'>
                          <Icons.spinner className='size-5 animate-spin text-primary' />
                          <span>Đang đồng bộ tiến trình thời gian thực từ máy chủ...</span>
                        </div>
                      ) : (
                        <div className='flex flex-col items-center justify-center gap-1'>
                          <Icons.search className='size-5 text-muted-foreground/60' />
                          <span>Không tìm thấy tiến trình nào phù hợp với bộ lọc hiện tại.</span>
                        </div>
                      )}
                    </td>
                  </tr>
                ) : (
                  filteredJobs.map((j) => {
                    const isGpuJob = j.isGpu || (j.vramUsedMb || 0) > 0;
                    return (
                      <tr
                        key={j.id}
                        onClick={() => {
                          setSelectedJob(j);
                          setDetailOpen(true);
                        }}
                        className={`cursor-pointer transition-colors ${
                          isGpuJob
                            ? 'bg-indigo-500/5 hover:bg-indigo-500/10 border-l-4 border-l-indigo-600'
                            : 'hover:bg-muted/40'
                        }`}
                        title='Nhấp để xem đầy đủ chi tiết tiến trình'
                      >
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
                            title={j.cmdline || j.script}
                          >
                            {j.script}
                          </div>
                        </td>

                        {/* GPU */}
                        <td className='py-3 px-3'>
                          {isGpuJob ? (
                            <div className='flex flex-col gap-0.5 items-start'>
                              <Badge
                                variant='default'
                                className='font-mono text-[10px] bg-indigo-600 hover:bg-indigo-600 text-white gap-1'
                              >
                                <span className='size-1 rounded-full bg-white animate-pulse' />
                                {j.gpuId || 'WS-GPU-01'}
                              </Badge>
                              <span className='text-[10px] text-indigo-600 dark:text-indigo-400 font-mono font-bold'>
                                {((j.vramUsedMb || 0) / 1024).toFixed(1)} GB
                              </span>
                            </div>
                          ) : (
                            <span className='text-muted-foreground font-mono text-[11px]'>-</span>
                          )}
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
                                className={`size-1.5 rounded-full ${j.sshStatus.includes('Online') ? 'bg-emerald-500' : 'bg-slate-400'}`}
                              ></span>
                              {j.sshStatus}
                            </span>
                          </div>
                        </td>

                        {/* Thao tác */}
                        <td className='py-3 px-3 text-right'>
                          <Button
                            size='sm'
                            variant='ghost'
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedJob(j);
                              setDetailOpen(true);
                            }}
                            className='h-7 text-xs text-primary hover:bg-primary/10'
                          >
                            <Icons.view className='size-3.5 mr-1' /> Chi tiết
                          </Button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <ProcessDetailSheet job={selectedJob} open={detailOpen} onOpenChange={setDetailOpen} />
    </div>
  );
}
