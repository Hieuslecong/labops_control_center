/* oxlint-disable react/set-state-in-effect */
'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { ProcessDetailSheet } from '@/features/overview/components/process-detail-sheet';
import { StopProcessDialog } from '@/features/jobs/components/stop-process-dialog';
import { ForceKillDialog } from '@/features/jobs/components/force-kill-dialog';
import { ProcessTreeDialog } from '@/features/jobs/components/process-tree-dialog';
import { ComputeJob } from '@/types/labops';

export function JobsMonitoringView() {
  const jobs = useLabOpsStore((state) => state.jobs);
  const fetchLiveJobs = useLabOpsStore((state) => state.fetchLiveJobs);
  const fetchLiveState = useLabOpsStore((state) => state.fetchLiveState);
  const syncError = useLabOpsStore((state) => state.syncError);

  // View Mode: 'workloads' (Mặc định: chỉ tác vụ thành viên) | 'diagnostics' (Chẩn đoán: toàn bộ tiến trình hệ thống)
  const [viewMode, setViewMode] = useState<'workloads' | 'diagnostics'>('workloads');

  const [allProcesses, setAllProcesses] = useState<ComputeJob[]>([]);
  const [loadingProcesses, setLoadingProcesses] = useState(false);

  const [searchTerm, setSearchTerm] = useState('');
  const [classificationFilter, setClassificationFilter] = useState<string>('all');
  const [userFilter, setUserFilter] = useState<string>('all');
  const [serverFilter, setServerFilter] = useState<string>('all');

  // Selected process for dialogs
  const [selectedProcess, setSelectedProcess] = useState<ComputeJob | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [killOpen, setKillOpen] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);

  const [isAutoRefresh, setIsAutoRefresh] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Fetch full processes list from /api/labops/processes (chỉ phục vụ Chẩn đoán Quản trị Nâng cao)
  const fetchProcessesList = useCallback(async () => {
    try {
      setLoadingProcesses(true);
      const res = await fetch('/api/labops/processes?classification=all').catch(() => null);
      if (res && res.ok) {
        const data = await res.json();
        const items = data.items || [];
        const mapped: ComputeJob[] = items.map((rawP: unknown) => {
          const p = rawP as Record<string, unknown>;
          return {
            id: String(p.pid),
            ref: p.ref ? String(p.ref) : undefined,
            username: String(p.username || 'unknown'),
            fullName: String(p.full_name || `@${String(p.username || 'unknown')}`),
            serverId: ((p.host_id || p.server_id || 'track') as ComputeJob['serverId']),
            serverName: String(p.server_name || 'Server 01 — Server Rack'),
            workspace: String(p.workspace || (p.container_id ? `docker-${String(p.container_id)}` : 'Trực tiếp trên Host')),
            pid: Number(p.pid),
            ppid: p.ppid ? Number(p.ppid) : undefined,
            script: String(p.script_display || p.name || 'cmd'),
            cmdline: p.cmdline ? String(p.cmdline) : undefined,
            gpuId: p.gpu_id ? String(p.gpu_id) : undefined,
            cpuPercent: Number(p.cpu_percent || 0),
            ramUsedMb: Number(p.memory_rss_mb || 0),
            vramUsedMb: Number(p.vram_mb || 0),
            startTime: String(p.start_time || ''),
            elapsedTime: String(p.elapsed_time || ''),
            status: 'RUNNING' as const,
            sshStatus: p.username ? 'SSH Online' : 'Chạy nền',
            containerState: p.container_id ? 'Running' : 'Host Process',
            isGpu: Boolean(p.is_gpu_process || Number(p.vram_mb || 0) > 0),
            classification: p.classification ? String(p.classification) : undefined,
            isProtected: Boolean(p.is_protected),
            childrenCount: Number(p.children_count || 0),
            attribution: p.attribution ? String(p.attribution) : undefined,
            isRootWorkload: Boolean(p.is_root_workload ?? true)
          };
        });
        setAllProcesses(mapped);
      }
    } catch {
      // fallback
    } finally {
      setLoadingProcesses(false);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    setIsRefreshing(true);
    await Promise.all([fetchLiveJobs(), fetchLiveState(), fetchProcessesList()]);
    setIsRefreshing(false);
  }, [fetchLiveJobs, fetchLiveState, fetchProcessesList]);

  useEffect(() => {
    refreshAll();
    if (!isAutoRefresh) return;
    const timer = setInterval(() => {
      fetchLiveJobs();
      fetchLiveState();
      fetchProcessesList();
    }, 4000);
    return () => clearInterval(timer);
  }, [refreshAll, fetchLiveJobs, fetchLiveState, fetchProcessesList, isAutoRefresh]);

  // Nguồn dữ liệu hiển thị theo chế độ
  const displayList = useMemo(() => {
    if (viewMode === 'workloads') {
      return jobs; // Chỉ trả về tác vụ thành viên thực tế từ Go Node Agent qua backend
    }
    return allProcesses; // Chế độ Chẩn đoán nâng cao: toàn bộ raw processes
  }, [viewMode, jobs, allProcesses]);

  // Distinct users and process counts
  const userCounts = useMemo(() => {
    const map: Record<string, { count: number; fullName?: string }> = {};
    displayList.forEach((j) => {
      if (j.username && j.username !== 'unknown') {
        if (!map[j.username]) {
          map[j.username] = { count: 0, fullName: j.fullName };
        }
        map[j.username].count += 1;
      }
    });
    return Object.entries(map).toSorted((a, b) => b[1].count - a[1].count);
  }, [displayList]);

  // KPI Calculations
  const gpuWorkloadsCount = useMemo(
    () => displayList.filter((j) => j.isGpu || j.vramUsedMb > 0).length,
    [displayList]
  );
  const cpuWorkloadsCount = useMemo(
    () => displayList.filter((j) => !j.isGpu && j.vramUsedMb <= 0).length,
    [displayList]
  );
  const totalVramMb = useMemo(
    () => displayList.reduce((acc, j) => acc + (j.vramUsedMb || 0), 0),
    [displayList]
  );

  // Filtered List
  const filteredItems = useMemo(() => {
    return displayList.filter((j) => {
      // 1. Text Search
      if (searchTerm.trim()) {
        const q = searchTerm.toLowerCase();
        const matchPid = String(j.pid).includes(q);
        const matchScript = j.script.toLowerCase().includes(q);
        const matchCmd = j.cmdline ? j.cmdline.toLowerCase().includes(q) : false;
        const matchUser =
          j.username.toLowerCase().includes(q) || j.fullName.toLowerCase().includes(q);
        const matchWorkspace = j.workspace ? j.workspace.toLowerCase().includes(q) : false;
        if (!matchPid && !matchScript && !matchCmd && !matchUser && !matchWorkspace) return false;
      }

      // 2. Classification Filter (Chỉ áp dụng khi ở chế độ diagnostics hoặc khi người dùng lọc)
      if (classificationFilter !== 'all') {
        if (classificationFilter === 'RESEARCH_JOB') {
          if (j.classification !== 'RESEARCH_JOB') return false;
        } else if (classificationFilter === 'MEMBER_PROCESS') {
          if (j.classification !== 'MEMBER_PROCESS' && j.classification !== 'RESEARCH_JOB') return false;
        } else if (classificationFilter === 'GPU_PROCESS') {
          if (!j.isGpu && j.vramUsedMb <= 0) return false;
        } else if (classificationFilter === 'SYSTEM_PROCESS') {
          if (j.classification !== 'SYSTEM_PROCESS') return false;
        }
      }

      // 3. User Filter
      if (userFilter !== 'all' && j.username !== userFilter) return false;

      // 4. Server Filter
      if (serverFilter !== 'all' && j.serverId !== serverFilter) return false;

      return true;
    });
  }, [displayList, searchTerm, classificationFilter, userFilter, serverFilter]);

  return (
    <div className='flex flex-col gap-4 font-sans text-foreground'>
      {/* Top Banner / View Mode Switcher */}
      <div className='flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 bg-white dark:bg-zinc-950 border rounded-lg shadow-xs'>
        <div className='flex items-center gap-2 flex-wrap'>
          <Button
            size='sm'
            variant={viewMode === 'workloads' ? 'default' : 'outline'}
            onClick={() => {
              setViewMode('workloads');
              setClassificationFilter('all');
            }}
            className='h-8 text-xs font-semibold gap-1.5'
          >
            <Icons.activity className='size-3.5' />
            Tác vụ Thành viên (Mặc định)
            <Badge
              variant='secondary'
              className={`ml-1 text-[10px] px-1.5 py-0 h-4 ${
                viewMode === 'workloads'
                  ? 'bg-primary-foreground/20 text-primary-foreground'
                  : 'bg-zinc-200 dark:bg-zinc-800 text-foreground'
              }`}
            >
              {jobs.length}
            </Badge>
          </Button>

          <Button
            size='sm'
            variant={viewMode === 'diagnostics' ? 'default' : 'outline'}
            onClick={() => {
              setViewMode('diagnostics');
              setClassificationFilter('all');
            }}
            className='h-8 text-xs gap-1.5'
          >
            <Icons.settings className='size-3.5 text-muted-foreground' />
            Chẩn đoán Quản trị (Toàn bộ tiến trình)
            <Badge variant='outline' className='ml-1 text-[10px] px-1.5 py-0 h-4'>
              {allProcesses.length}
            </Badge>
          </Button>
        </div>

        <div className='flex items-center gap-2 text-xs'>
          <Button
            size='sm'
            variant='outline'
            onClick={() => setIsAutoRefresh(!isAutoRefresh)}
            className={`h-7 text-xs px-2.5 ${isAutoRefresh ? 'border-emerald-500 text-emerald-600' : ''}`}
          >
            <span className={`size-1.5 rounded-full mr-1.5 ${isAutoRefresh ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-400'}`} />
            {isAutoRefresh ? 'Tự cập nhật 4s' : 'Đã tạm dừng'}
          </Button>

          <Button
            size='sm'
            variant='outline'
            onClick={refreshAll}
            disabled={isRefreshing}
            className='h-7 text-xs px-2.5'
          >
            <Icons.refresh className={`size-3 mr-1 ${isRefreshing ? 'animate-spin' : ''}`} />
            Làm mới
          </Button>
        </div>
      </div>

      {/* Chế độ cảnh báo khi ở Diagnostics */}
      {viewMode === 'diagnostics' && (
        <div className='p-2.5 rounded-lg bg-blue-500/10 border border-blue-500/30 text-blue-800 dark:text-blue-200 text-xs flex items-center gap-2'>
          <Icons.info className='size-4 text-blue-500 shrink-0' />
          <span>
            <strong>Chế độ Chẩn đoán Quản trị:</strong> Đang hiển thị toàn bộ <strong>{allProcesses.length}</strong> tiến trình cấp hệ điều hành (bao gồm system daemons, desktop services và IDE tooling phụ trợ). Không khuyến nghị can thiệp vào các tiến trình hệ thống.
          </span>
        </div>
      )}

      {/* KPI Stats Cards - White Clean Theme */}
      <div className='grid grid-cols-2 sm:grid-cols-4 gap-3'>
        <Card className='p-3.5 bg-white dark:bg-zinc-950 border shadow-xs'>
          <span className='text-[11px] text-muted-foreground font-semibold uppercase tracking-wider'>
            {viewMode === 'workloads' ? 'Tác vụ Đang chạy' : 'Tổng Tiến trình'}
          </span>
          <div className='text-2xl font-bold mt-1 text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5'>
            <Icons.job className='size-5' />
            {viewMode === 'workloads' ? jobs.length : allProcesses.length}
          </div>
          <span className='text-[10px] text-muted-foreground'>
            {viewMode === 'workloads' ? 'Tác vụ thành viên chủ động' : 'Bao gồm background daemons'}
          </span>
        </Card>

        <Card className='p-3.5 bg-white dark:bg-zinc-950 border shadow-xs'>
          <span className='text-[11px] text-muted-foreground font-semibold uppercase tracking-wider'>
            ⚡ Chiếm dụng GPU
          </span>
          <div className='text-2xl font-bold mt-1 text-indigo-600 dark:text-indigo-400 flex items-center gap-1.5'>
            <Icons.gpu className='size-5' />
            {gpuWorkloadsCount}
          </div>
          <span className='text-[10px] text-muted-foreground'>
            Đang dùng {(totalVramMb / 1024).toFixed(1)} GB VRAM
          </span>
        </Card>

        <Card className='p-3.5 bg-white dark:bg-zinc-950 border shadow-xs'>
          <span className='text-[11px] text-muted-foreground font-semibold uppercase tracking-wider'>
            {viewMode === 'workloads' ? 'Tác vụ CPU thuần' : 'Tiến trình Thành viên'}
          </span>
          <div className='text-2xl font-bold mt-1 text-blue-600 dark:text-blue-400 flex items-center gap-1.5'>
            {viewMode === 'workloads' ? <Icons.cpu className='size-5' /> : <Icons.users className='size-5' />}
            {viewMode === 'workloads'
              ? cpuWorkloadsCount
              : allProcesses.filter((p) => p.classification === 'MEMBER_PROCESS' || p.classification === 'RESEARCH_JOB').length}
          </div>
          <span className='text-[10px] text-muted-foreground'>
            {viewMode === 'workloads' ? 'Xử lý trên đa luồng CPU' : 'Tiến trình user space'}
          </span>
        </Card>

        <Card className='p-3.5 bg-white dark:bg-zinc-950 border shadow-xs'>
          <span className='text-[11px] text-muted-foreground font-semibold uppercase tracking-wider'>
            Thành viên Hoạt động
          </span>
          <div className='text-2xl font-bold mt-1 text-foreground flex items-center gap-1.5'>
            <Icons.user className='size-5' />
            {userCounts.length} tài khoản
          </div>
          <span className='text-[10px] text-muted-foreground truncate'>
            {userCounts.length > 0
              ? userCounts.slice(0, 3).map(([u]) => `@${u}`).join(', ')
              : 'Chưa có hoạt động'}
            {userCounts.length > 3 ? '...' : ''}
          </span>
        </Card>
      </div>

      {/* Sync error alert */}
      {syncError && (
        <div className='p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-800 dark:text-amber-200 text-xs flex items-center gap-2'>
          <Icons.warning className='size-4 text-amber-500 shrink-0' />
          <span>
            <strong>Lưu ý:</strong> {syncError}. Dữ liệu đang được tải từ bộ nhớ đệm xác minh.
          </span>
        </div>
      )}

      {/* Filter and Control Bar */}
      <div className='flex flex-col gap-3 p-3 rounded-lg border bg-white dark:bg-zinc-950 shadow-xs'>
        {/* Row 1: Classification Pills khi ở Diagnostics */}
        {viewMode === 'diagnostics' && (
          <div className='flex flex-wrap items-center justify-between gap-2 pb-2 border-b'>
            <div className='flex items-center gap-1.5 flex-wrap'>
              <span className='text-[11px] font-semibold text-muted-foreground mr-1'>Phân loại:</span>
              <Button
                size='sm'
                variant={classificationFilter === 'all' ? 'default' : 'outline'}
                onClick={() => setClassificationFilter('all')}
                className='h-7 text-xs px-2.5 rounded-full'
              >
                Tất cả ({displayList.length})
              </Button>

              <Button
                size='sm'
                variant={classificationFilter === 'RESEARCH_JOB' ? 'default' : 'outline'}
                onClick={() => setClassificationFilter('RESEARCH_JOB')}
                className={`h-7 text-xs px-2.5 rounded-full ${
                  classificationFilter === 'RESEARCH_JOB'
                    ? 'bg-emerald-600 hover:bg-emerald-700 text-white'
                    : 'text-emerald-700 dark:text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/10'
                }`}
              >
                <Icons.job className='size-3 mr-1' />
                Tác vụ AI
              </Button>

              <Button
                size='sm'
                variant={classificationFilter === 'GPU_PROCESS' ? 'default' : 'outline'}
                onClick={() => setClassificationFilter('GPU_PROCESS')}
                className={`h-7 text-xs px-2.5 rounded-full ${
                  classificationFilter === 'GPU_PROCESS'
                    ? 'bg-indigo-600 hover:bg-indigo-700 text-white'
                    : 'text-indigo-600 dark:text-indigo-400 border-indigo-500/30 hover:bg-indigo-500/10'
                }`}
              >
                <Icons.gpu className='size-3 mr-1' />
                Chiếm GPU
              </Button>

              <Button
                size='sm'
                variant={classificationFilter === 'MEMBER_PROCESS' ? 'default' : 'outline'}
                onClick={() => setClassificationFilter('MEMBER_PROCESS')}
                className='h-7 text-xs px-2.5 rounded-full'
              >
                <Icons.user className='size-3 mr-1' />
                Thành viên
              </Button>

              <Button
                size='sm'
                variant={classificationFilter === 'SYSTEM_PROCESS' ? 'default' : 'outline'}
                onClick={() => setClassificationFilter('SYSTEM_PROCESS')}
                className='h-7 text-xs px-2.5 rounded-full text-zinc-600'
              >
                <Icons.shield className='size-3 mr-1' />
                Hệ thống
              </Button>
            </div>
          </div>
        )}

        {/* Search Input and Dropdown Selects */}
        <div className='grid grid-cols-1 sm:grid-cols-4 gap-2.5'>
          <div className='sm:col-span-2 relative'>
            <Icons.search className='size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground' />
            <Input
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder='Tìm kiếm PID, tên chương trình, lệnh, username, workspace...'
              className='h-8 text-xs pl-8 bg-zinc-50/60 dark:bg-zinc-900/60'
            />
          </div>

          <select
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value)}
            className='h-8 rounded-md border border-input bg-zinc-50/60 dark:bg-zinc-900/60 px-2 text-xs font-medium'
          >
            <option value='all'>Tất cả thành viên ({userCounts.length})</option>
            {userCounts.map(([uname, info]) => (
              <option key={uname} value={uname}>
                @{uname} ({info.count} tiến trình)
              </option>
            ))}
          </select>

          <select
            value={serverFilter}
            onChange={(e) => setServerFilter(e.target.value)}
            className='h-8 rounded-md border border-input bg-zinc-50/60 dark:bg-zinc-900/60 px-2 text-xs font-medium'
          >
            <option value='all'>Tất cả máy chủ</option>
            <option value='track'>Server 01 — Server Rack (aiotlab-rack)</option>
            <option value='workstation'>Server 02 — Workstation (Chưa xác minh)</option>
          </select>
        </div>
      </div>

      {/* Main Processes Data Table */}
      <Card className='border bg-white dark:bg-zinc-950 shadow-xs'>
        <CardContent className='p-0'>
          <div className='overflow-x-auto'>
            <table className='w-full text-left text-xs'>
              <thead>
                <tr className='border-b text-muted-foreground font-semibold bg-zinc-50/70 dark:bg-zinc-900/40'>
                  <th className='py-2.5 px-3'>Thành viên</th>
                  <th className='py-2.5 px-3'>Máy chủ & Workspace</th>
                  <th className='py-2.5 px-3'>PID / Lệnh thực thi</th>
                  <th className='py-2.5 px-3'>Phân loại</th>
                  <th className='py-2.5 px-3'>Tải CPU / RAM</th>
                  <th className='py-2.5 px-3'>VRAM GPU</th>
                  <th className='py-2.5 px-3'>Thời gian</th>
                  <th className='py-2.5 px-3 text-right'>Thao tác Quản trị</th>
                </tr>
              </thead>
              <tbody className='divide-y divide-zinc-200/70 dark:divide-zinc-800/60'>
                {filteredItems.length === 0 ? (
                  <tr>
                    <td colSpan={8} className='py-16 text-center text-xs text-muted-foreground'>
                      <Icons.terminal className='size-10 mx-auto text-muted-foreground/30 mb-3' />
                      <p className='text-sm font-semibold text-foreground'>
                        {viewMode === 'workloads'
                          ? 'Hiện không có tác vụ tính toán hoặc workload nào đang chạy'
                          : 'Không tìm thấy tiến trình nào phù hợp với bộ lọc'}
                      </p>
                      <p className='text-xs text-muted-foreground mt-1 max-w-md mx-auto'>
                        {viewMode === 'workloads'
                          ? 'Máy chủ và GPU hiện đang ở trạng thái rảnh (Idle). Khi thành viên khởi chạy Python training, PyTorch workload hoặc script nghiên cứu, hệ thống sẽ tự động hiển thị.'
                          : 'Thử kiểm tra lại từ khóa tìm kiếm hoặc bỏ bớt các bộ lọc phân loại.'}
                      </p>
                      {viewMode === 'workloads' && (
                        <div className='mt-4'>
                          <Button
                            size='sm'
                            variant='outline'
                            onClick={() => setViewMode('diagnostics')}
                            className='h-7 text-xs gap-1.5'
                          >
                            <Icons.settings className='size-3 text-primary' />
                            Mở Chẩn đoán Hệ thống ({allProcesses.length} tiến trình thô)
                          </Button>
                        </div>
                      )}
                    </td>
                  </tr>
                ) : (
                  filteredItems.map((item) => {
                    const isGpu = Boolean(item.isGpu || item.vramUsedMb > 0);
                    const isProtected = Boolean(item.isProtected || item.classification === 'SYSTEM_PROCESS');

                    return (
                      <tr
                        key={item.ref || item.id}
                        onClick={() => {
                          setSelectedProcess(item);
                          setDetailOpen(true);
                        }}
                        className={`cursor-pointer transition-colors ${
                          isGpu
                            ? 'bg-indigo-50/30 dark:bg-indigo-950/10 hover:bg-indigo-50/60 dark:hover:bg-indigo-950/20'
                            : 'hover:bg-zinc-50 dark:hover:bg-zinc-900/50'
                        }`}
                      >
                        {/* Cột 1: Thành viên */}
                        <td className='py-3 px-3'>
                          <div className='font-bold text-foreground'>{item.fullName}</div>
                          <div className='text-[11px] text-muted-foreground font-mono'>
                            @{item.username}
                          </div>
                        </td>

                        {/* Cột 2: Máy chủ & Workspace */}
                        <td className='py-3 px-3'>
                          <Badge variant='outline' className='font-mono text-[10px] uppercase'>
                            {item.serverId === 'track' ? 'Server 01 — Rack' : item.serverId}
                          </Badge>
                          <div className='text-[11px] text-muted-foreground font-mono mt-0.5 truncate max-w-[150px]' title={item.workspace}>
                            {item.workspace}
                          </div>
                        </td>

                        {/* Cột 3: PID / Lệnh */}
                        <td className='py-3 px-3 max-w-sm'>
                          <div className='flex items-center gap-1.5 flex-wrap'>
                            <span className='font-mono font-bold text-foreground text-[11px]'>
                              PID: {item.pid}
                            </span>
                            {item.childrenCount && item.childrenCount > 0 ? (
                              <Badge variant='secondary' className='text-[10px] px-1.5 py-0 h-4 bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20 font-medium'>
                                +{item.childrenCount} tiến trình con
                              </Badge>
                            ) : null}
                            {item.ppid ? (
                              <span className='text-[10px] text-muted-foreground font-mono'>
                                (PPID: {item.ppid})
                              </span>
                            ) : null}
                          </div>
                          <div
                            className='font-mono text-[11px] text-muted-foreground truncate max-w-[280px]'
                            title={item.cmdline || item.script}
                          >
                            {item.script}
                          </div>
                        </td>

                        {/* Cột 4: Phân loại */}
                        <td className='py-3 px-3'>
                          {item.classification === 'RESEARCH_JOB' && (
                            <Badge variant='outline' className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-medium'>
                              Tác vụ AI
                            </Badge>
                          )}
                          {item.classification === 'GPU_PROCESS' && (
                            <Badge variant='outline' className='bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/30 text-[10px] font-medium'>
                              Chiếm GPU
                            </Badge>
                          )}
                          {item.classification === 'MEMBER_PROCESS' && (
                            <Badge variant='outline' className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[10px] font-medium'>
                              Thành viên
                            </Badge>
                          )}
                          {item.classification === 'SYSTEM_PROCESS' && (
                            <Badge variant='secondary' className='text-zinc-600 dark:text-zinc-400 text-[10px] font-medium'>
                              Hệ thống
                            </Badge>
                          )}
                          {!item.classification && (
                            <Badge variant='secondary' className='text-[10px]'>
                              Tiến trình
                            </Badge>
                          )}
                        </td>

                        {/* Cột 5: Tải CPU / RAM */}
                        <td className='py-3 px-3 font-mono text-[11px]'>
                          <div className='font-semibold text-foreground'>{item.cpuPercent > 0 ? `${item.cpuPercent}% CPU` : '-'}</div>
                          <div className='text-muted-foreground'>
                            {item.ramUsedMb > 0 ? `${(item.ramUsedMb / 1024).toFixed(1)} GB RAM` : '-'}
                          </div>
                        </td>

                        {/* Cột 6: VRAM GPU */}
                        <td className='py-3 px-3 font-mono text-[11px]'>
                          {item.vramUsedMb > 0 ? (
                            <div className='flex flex-col'>
                              <span className='font-bold text-indigo-600 dark:text-indigo-400'>
                                {(item.vramUsedMb / 1024).toFixed(1)} GB
                              </span>
                              <span className='text-[10px] text-muted-foreground'>{item.gpuId || 'GPU 0'}</span>
                            </div>
                          ) : (
                            <span className='text-muted-foreground'>-</span>
                          )}
                        </td>

                        {/* Cột 7: Thời gian */}
                        <td className='py-3 px-3'>
                          <div className='font-medium text-foreground'>{item.elapsedTime || '-'}</div>
                          <div className='text-[10px] text-muted-foreground'>{item.startTime}</div>
                        </td>

                        {/* Cột 8: Thao tác Quản trị */}
                        <td className='py-3 px-3 text-right'>
                          <div className='flex items-center justify-end gap-1.5'>
                            <Button
                              size='sm'
                              variant='ghost'
                              aria-label='Xem cây tiến trình'
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedProcess(item);
                                setTreeOpen(true);
                              }}
                              className='h-7 text-xs px-2 text-zinc-600 hover:bg-zinc-100'
                              title='Xem cây tiến trình phân cấp'
                            >
                              <Icons.terminal className='size-3.5' />
                            </Button>

                            <Button
                              size='sm'
                              variant='outline'
                              disabled={isProtected}
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedProcess(item);
                                setStopOpen(true);
                              }}
                              className='h-7 text-xs px-2 text-amber-600 hover:bg-amber-50 hover:text-amber-700 border-amber-200 dark:border-amber-900 disabled:opacity-40'
                              title={isProtected ? 'Tiến trình hệ thống được bảo vệ — Không thể dừng' : 'Dừng an toàn (SIGTERM)'}
                            >
                              Dừng
                            </Button>

                            <Button
                              size='sm'
                              variant='outline'
                              disabled={isProtected}
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedProcess(item);
                                setKillOpen(true);
                              }}
                              className='h-7 text-xs px-2 text-rose-600 hover:bg-rose-50 hover:text-rose-700 border-rose-200 dark:border-rose-900 disabled:opacity-40'
                              title={isProtected ? 'Tiến trình hệ thống được bảo vệ — Không thể buộc dừng' : 'Buộc dừng cưỡng chế (SIGKILL)'}
                            >
                              Buộc dừng
                            </Button>
                          </div>
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

      {/* Sheets and Dialogs */}
      <ProcessDetailSheet
        job={selectedProcess}
        open={detailOpen}
        onOpenChange={setDetailOpen}
        onRefresh={refreshAll}
      />

      <StopProcessDialog
        job={selectedProcess}
        open={stopOpen}
        onOpenChange={setStopOpen}
        onSuccess={refreshAll}
      />

      <ForceKillDialog
        job={selectedProcess}
        open={killOpen}
        onOpenChange={setKillOpen}
        onSuccess={refreshAll}
      />

      <ProcessTreeDialog
        job={selectedProcess}
        open={treeOpen}
        onOpenChange={setTreeOpen}
      />
    </div>
  );
}
