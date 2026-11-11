'use client';

import React from 'react';
import { SidebarTrigger } from '../ui/sidebar';
import { Separator } from '../ui/separator';
import { Breadcrumbs } from '../breadcrumbs';
import SearchInput from '../search-input';
import { ThemeModeToggle } from '../themes/theme-mode-toggle';
import { NotificationCenter } from '@/features/notifications/components/notification-center';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { useAuthSession } from '@/hooks/use-auth-session';

export default function Header() {
  const isDemoMode = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';
  const servers = useLabOpsStore((s) => s.servers);
  const gpus = useLabOpsStore((s) => s.gpus);
  const { isResearcher, role } = useAuthSession();

  const trackServer = servers.find((s) => s.id === 'track');
  const wsServer = servers.find((s) => s.id === 'workstation');

  const trackOnline = trackServer?.status === 'online';
  const wsStatus = wsServer?.status || 'unknown';

  let hostStatusText = 'Khởi tạo kết nối...';
  let hostStatusColor = 'amber';

  if (trackOnline && wsStatus === 'online') {
    hostStatusText = 'Rack & Workstation: ONLINE';
    hostStatusColor = 'emerald';
  } else if (trackOnline && wsStatus === 'unknown') {
    hostStatusText = 'Rack: ONLINE • Workstation: Chưa xác minh';
    hostStatusColor = 'emerald';
  } else if (trackOnline && wsStatus === 'offline') {
    hostStatusText = 'Rack: ONLINE • Workstation: OFFLINE';
    hostStatusColor = 'amber';
  } else if (!trackOnline && wsStatus === 'online') {
    hostStatusText = 'Workstation: ONLINE • Rack: OFFLINE';
    hostStatusColor = 'amber';
  } else {
    hostStatusText = 'Hạ tầng: Đang kiểm tra trạng thái';
    hostStatusColor = 'amber';
  }

  const verifiedGpus = gpus.filter((g) => (g.vramTotalMb || 0) > 0);
  const totalVramMb = verifiedGpus.reduce((acc, g) => acc + (g.vramTotalMb || 0), 0);
  const totalVramGb = Math.round(totalVramMb / 1024);
  const usedVramMb = verifiedGpus.reduce((acc, g) => acc + (g.vramUsedMb || 0), 0);
  const usedVramGb = (usedVramMb / 1024).toFixed(1);
  const activeGpusCount = verifiedGpus.filter(
    (g) =>
      (g.vramUsedMb || 0) > 512 ||
      (g.gpuUtilPercent || 0) > 0 ||
      (g.activeProcesses?.length || 0) > 0
  ).length;
  const isGpuRunning = activeGpusCount > 0;

  return (
    <header className='bg-white/90 dark:bg-slate-950/90 sticky top-0 z-20 flex h-16 shrink-0 items-center justify-between gap-2 border-b border-slate-200 dark:border-slate-800 backdrop-blur-md md:h-14 px-4'>
      <div className='flex items-center gap-2'>
        <SidebarTrigger className='-ml-1' />
        <Separator orientation='vertical' className='mr-2 h-4 data-vertical:self-center' />
        <Breadcrumbs />
      </div>

      <div className='flex items-center gap-2.5'>
        {/* Status Indicator: Tailored per role */}
        {isResearcher ? (
          <div className='hidden sm:flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-medium border bg-blue-500/10 border-blue-500/20 text-blue-700 dark:text-blue-400'>
            <span className='relative flex h-2 w-2'>
              <span className='animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75'></span>
              <span className='relative inline-flex rounded-full h-2 w-2 bg-blue-500'></span>
            </span>
            <span className='flex items-center gap-1.5'>
              <Icons.terminal className='size-3.5' />
              <span>Cổng Nghiên cứu viên • Không gian cá nhân</span>
            </span>
          </div>
        ) : (
          <div
            className={`hidden lg:flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-medium border ${
              hostStatusColor === 'emerald'
                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-700 dark:text-emerald-400'
                : 'bg-amber-500/10 border-amber-500/20 text-amber-700 dark:text-amber-400'
            }`}
          >
            <span className='relative flex h-2 w-2'>
              <span
                className={`animate-ping absolute inline-flex h-full w-full rounded-full ${
                  hostStatusColor === 'emerald' ? 'bg-emerald-400' : 'bg-amber-400'
                } opacity-75`}
              ></span>
              <span
                className={`relative inline-flex rounded-full h-2 w-2 ${
                  hostStatusColor === 'emerald' ? 'bg-emerald-500' : 'bg-amber-500'
                }`}
              ></span>
            </span>
            <span className='flex items-center gap-1.5 font-mono text-[11px]'>
              <Icons.server className='size-3.5' />
              {hostStatusText}
            </span>
            {verifiedGpus.length > 0 && (
              <>
                <span className='text-muted-foreground/60'>•</span>
                {isGpuRunning ? (
                  <span
                    className='font-mono text-[11px] text-indigo-600 dark:text-indigo-400 font-semibold flex items-center gap-1.5'
                    title={`GPU đang chạy tác vụ (${usedVramGb} / ${totalVramGb} GB VRAM)`}
                  >
                    <span className='size-1.5 rounded-full bg-indigo-500 animate-pulse' />
                    <span>
                      GPU: {usedVramGb}/{totalVramGb} GB (
                      {Math.round((usedVramMb / (totalVramMb || 1)) * 100)}%)
                    </span>
                  </span>
                ) : (
                  <span className='font-mono text-[11px] text-muted-foreground'>
                    {verifiedGpus.length}x A30 ({totalVramGb} GB)
                  </span>
                )}
              </>
            )}
          </div>
        )}

        {isDemoMode ? (
          <Badge
            variant='outline'
            className='hidden sm:inline-flex bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[11px] font-medium'
          >
            Chế độ Demo
          </Badge>
        ) : (
          <Badge
            variant='outline'
            className='hidden sm:inline-flex bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border-slate-300 dark:border-slate-700 text-[11px] font-medium'
          >
            {role === 'ADMIN' ? 'Quản trị' : 'Nghiên cứu'}
          </Badge>
        )}

        <div className='hidden md:flex'>
          <SearchInput />
        </div>
        <ThemeModeToggle />
        <NotificationCenter />
      </div>
    </header>
  );
}
