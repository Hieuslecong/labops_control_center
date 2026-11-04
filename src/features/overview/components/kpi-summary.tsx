'use client';

import React from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import Link from 'next/link';

export function KpiSummary() {
  const servers = useLabOpsStore((state) => state.servers);
  const gpus = useLabOpsStore((state) => state.gpus);
  const members = useLabOpsStore((state) => state.members);
  const jobs = useLabOpsStore((state) => state.jobs);

  const summary = React.useMemo(() => {
    const totalVramGb = Math.round(
      gpus.reduce((acc, g) => acc + (g.vramTotalMb || 24576), 0) / 1024
    );
    const usedVramGb = Math.round(gpus.reduce((acc, g) => acc + g.vramUsedMb, 0) / 1024);
    const activeGpus = gpus.filter((g) => g.vramUsedMb > 0).length;
    const onlineSsh = members.filter((m) => m.sshStatus === 'ONLINE').length;
    const activeSessions = members.reduce((acc, m) => acc + m.sshSessionsCount, 0);
    const runningJobs = jobs.filter((j) => j.status === 'RUNNING').length;
    const cudaProcCount = gpus.reduce((acc, g) => acc + (g.activeProcesses?.length || 0), 0);

    return {
      servers: {
        total: servers.length,
        online: servers.filter((s) => s.status === 'online').length,
        offline: servers.filter((s) => s.status !== 'online').length
      },
      gpus: {
        totalCount: gpus.length,
        model: 'NVIDIA A30 24GB',
        aggregateVramGb: totalVramGb > 0 ? totalVramGb : 24,
        activeCount: activeGpus,
        usedVramGb
      },
      members: {
        onlineSsh,
        activeSessions,
        totalRegistered: members.length
      },
      jobs: {
        running: runningJobs,
        cudaProcs: cudaProcCount,
        pending: 0,
        failed: 0
      },
      gpuHours: {
        today: 48.6,
        last7Days: 384.2,
        previousPeriodChangePercent: 14.8
      },
      alerts: {
        critical: 0,
        warnings: 1,
        unresolvedIncidents: 0
      }
    };
  }, [servers, gpus, members, jobs]);

  return (
    <div className='grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6 min-w-0 w-full'>
      {/* Card 1: Máy chủ */}
      <Link
        href='/dashboard/servers'
        aria-label='Xem chi tiết máy chủ vật lý'
        className='group min-w-0'
      >
        <Card className='h-full min-w-0 transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card overflow-hidden'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider truncate'>
                Máy chủ vật lý
              </CardDescription>
              <div className='flex size-7 shrink-0 items-center justify-center rounded-md bg-blue-500/10 text-blue-600 dark:text-blue-400'>
                <Icons.server className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5 truncate'>
              {summary.servers.online}
              <span className='text-xs font-normal text-muted-foreground truncate'>
                / {summary.servers.total} Trực tuyến
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs min-w-0'>
            {summary.servers.offline > 0 ? (
              <>
                <div className='flex items-center gap-1.5 text-amber-600 dark:text-amber-400 font-medium truncate'>
                  <span className='size-2 shrink-0 rounded-full bg-amber-500 animate-pulse'></span>
                  <span className='truncate'>{summary.servers.offline} Cụm Mất kết nối VPN</span>
                </div>
                <p className='text-muted-foreground text-[11px] mt-1 truncate'>
                  Workstation: Online • Track: Offline
                </p>
              </>
            ) : (
              <>
                <div className='flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-medium truncate'>
                  <span className='size-2 shrink-0 rounded-full bg-emerald-500 animate-pulse'></span>
                  <span className='truncate'>100% Hoạt động bình thường</span>
                </div>
                <p className='text-muted-foreground text-[11px] mt-1 truncate'>
                  Track: Online • WS: Online
                </p>
              </>
            )}
          </CardContent>
        </Card>
      </Link>

      {/* Card 2: GPU */}
      <Link
        href='/dashboard/resources'
        aria-label='Xem chi tiết GPU NVIDIA A30'
        className='group min-w-0'
      >
        <Card className='h-full min-w-0 transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card overflow-hidden'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider truncate'>
                NVIDIA GPU (A30)
              </CardDescription>
              <div className='flex size-7 shrink-0 items-center justify-center rounded-md bg-indigo-500/10 text-indigo-600 dark:text-indigo-400'>
                <Icons.gpu className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5 truncate'>
              {summary.gpus.activeCount}
              <span className='text-xs font-normal text-muted-foreground truncate'>
                / {summary.gpus.totalCount} Đang tải
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs min-w-0'>
            <div className='flex items-center justify-between gap-1'>
              <span className='text-muted-foreground shrink-0'>Đã cấp phát:</span>
              <span className='font-semibold text-foreground font-mono text-[11px] truncate'>
                {summary.gpus.usedVramGb} / {summary.gpus.aggregateVramGb} GB VRAM
              </span>
            </div>
            <div className='w-full bg-muted rounded-full h-1.5 mt-2 overflow-hidden'>
              <div
                className='bg-indigo-600 h-1.5 rounded-full transition-all duration-500'
                style={{
                  width: `${Math.min(100, (summary.gpus.usedVramGb / summary.gpus.aggregateVramGb) * 100)}%`
                }}
              ></div>
            </div>
          </CardContent>
        </Card>
      </Link>

      {/* Card 3: Thành viên Online */}
      <Link href='/dashboard/users' aria-label='Xem danh sách thành viên' className='group min-w-0'>
        <Card className='h-full min-w-0 transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card overflow-hidden'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider truncate'>
                Thành viên Lab
              </CardDescription>
              <div className='flex size-7 shrink-0 items-center justify-center rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'>
                <Icons.user className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5 truncate'>
              {summary.members.onlineSsh}
              <span className='text-xs font-normal text-muted-foreground truncate'>
                / {summary.members.totalRegistered} Online
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs min-w-0'>
            <div className='flex items-center justify-between gap-1'>
              <span className='text-muted-foreground shrink-0'>Phiên SSH:</span>
              <Badge variant='secondary' className='text-[10px] font-mono px-1.5 py-0 shrink-0'>
                {summary.members.activeSessions} active
              </Badge>
            </div>
            <p className='text-muted-foreground text-[11px] mt-1 truncate'>
              Incus container cgroups
            </p>
          </CardContent>
        </Card>
      </Link>

      {/* Card 4: Tác vụ tính toán */}
      <Link href='/dashboard/jobs' aria-label='Xem các tác vụ đang chạy' className='group min-w-0'>
        <Card className='h-full min-w-0 transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card overflow-hidden'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider truncate'>
                Tác vụ chạy
              </CardDescription>
              <div className='flex size-7 shrink-0 items-center justify-center rounded-md bg-amber-500/10 text-amber-600 dark:text-amber-400'>
                <Icons.job className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5 truncate'>
              {summary.jobs.running}
              <span className='text-xs font-normal text-muted-foreground truncate'>
                Đang huấn luyện
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs min-w-0'>
            <div className='flex items-center gap-1.5 text-amber-600 dark:text-amber-400 font-medium truncate'>
              <span className='size-2 shrink-0 rounded-full bg-amber-500 animate-pulse'></span>
              <span className='truncate'>{summary.jobs.cudaProcs} tiến trình CUDA active</span>
            </div>
            <p className='text-muted-foreground text-[11px] mt-1 truncate'>0 chờ • 0 thất bại</p>
          </CardContent>
        </Card>
      </Link>

      {/* Card 5: GPU Hours */}
      <Link
        href='/dashboard/resources'
        aria-label='Xem thống kê GPU-hours'
        className='group min-w-0'
      >
        <Card className='h-full min-w-0 transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card overflow-hidden'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider truncate'>
                GPU-hours (7 ngày)
              </CardDescription>
              <div className='flex size-7 shrink-0 items-center justify-center rounded-md bg-purple-500/10 text-purple-600 dark:text-purple-400'>
                <Icons.history className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5 truncate'>
              {summary.gpuHours.last7Days}
              <span className='text-xs font-normal text-muted-foreground'>giờ</span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs min-w-0'>
            <div className='flex items-center gap-1.5 text-purple-600 dark:text-purple-400 font-medium truncate'>
              <span>+{summary.gpuHours.previousPeriodChangePercent}%</span>
              <span className='text-muted-foreground font-normal truncate'>so với tuần trước</span>
            </div>
            <p className='text-muted-foreground text-[11px] mt-1 truncate'>
              Hôm nay: {summary.gpuHours.today}h
            </p>
          </CardContent>
        </Card>
      </Link>

      {/* Card 6: An ninh Sandbox */}
      <Link href='/dashboard/security' aria-label='Xem trung tâm an ninh' className='group min-w-0'>
        <Card className='h-full min-w-0 transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card overflow-hidden'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider truncate'>
                Cảnh báo an ninh
              </CardDescription>
              <div className='flex size-7 shrink-0 items-center justify-center rounded-md bg-rose-500/10 text-rose-600 dark:text-rose-400'>
                <Icons.shield className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5 truncate'>
              {summary.alerts.warnings}
              <span className='text-xs font-normal text-muted-foreground truncate'>
                Cần kiểm duyệt
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs min-w-0'>
            <div className='flex items-center gap-1.5 text-rose-600 dark:text-rose-400 font-medium truncate'>
              <span className='size-2 shrink-0 rounded-full bg-rose-500'></span>
              <span className='truncate'>1 repo chờ duyệt AST</span>
            </div>
            <p className='text-muted-foreground text-[11px] mt-1 truncate'>
              0 sự cố bảo mật nghiêm trọng
            </p>
          </CardContent>
        </Card>
      </Link>
    </div>
  );
}
