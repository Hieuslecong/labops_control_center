'use client';

import React from 'react';
import { SidebarTrigger } from '../ui/sidebar';
import { Separator } from '../ui/separator';
import { Breadcrumbs } from '../breadcrumbs';
import SearchInput from '../search-input';
import { ThemeSelector } from '../themes/theme-selector';
import { ThemeModeToggle } from '../themes/theme-mode-toggle';
import { NotificationCenter } from '@/features/notifications/components/notification-center';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';

export default function Header() {
  const isDemoMode = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';
  const servers = useLabOpsStore((s) => s.servers);
  const gpus = useLabOpsStore((s) => s.gpus);

  const onlineServers = servers.filter((s) => s.status === 'online').length;
  const isFullyOnline = onlineServers === servers.length;
  const totalVramMb = gpus.reduce((acc, g) => acc + (g.vramTotalMb || 24576), 0);
  const totalVramGb = Math.round(totalVramMb / 1024);
  const usedVramMb = gpus.reduce((acc, g) => acc + (g.vramUsedMb || 0), 0);
  const usedVramGb = (usedVramMb / 1024).toFixed(1);
  const activeGpusCount = gpus.filter(
    (g) =>
      (g.vramUsedMb || 0) > 512 ||
      (g.gpuUtilPercent || 0) > 0 ||
      (g.activeProcesses?.length || 0) > 0
  ).length;
  const isGpuRunning = activeGpusCount > 0;

  return (
    <header className='bg-background/80 sticky top-0 z-20 flex h-16 shrink-0 items-center justify-between gap-2 border-b backdrop-blur-md md:h-14 px-4'>
      <div className='flex items-center gap-2'>
        <SidebarTrigger className='-ml-1' />
        <Separator orientation='vertical' className='mr-2 h-4 data-vertical:self-center' />
        <Breadcrumbs />
      </div>

      <div className='flex items-center gap-2.5'>
        {/* Live Infrastructure Status indicator */}
        <div
          className={`hidden lg:flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-medium border ${
            isFullyOnline
              ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-700 dark:text-emerald-400'
              : 'bg-amber-500/10 border-amber-500/20 text-amber-700 dark:text-amber-400'
          }`}
        >
          <span className='relative flex h-2 w-2'>
            {isFullyOnline ? (
              <>
                <span className='animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75'></span>
                <span className='relative inline-flex rounded-full h-2 w-2 bg-emerald-500'></span>
              </>
            ) : (
              <>
                <span className='animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75'></span>
                <span className='relative inline-flex rounded-full h-2 w-2 bg-amber-500'></span>
              </>
            )}
          </span>
          <span className='flex items-center gap-1.5'>
            <Icons.server className='size-3.5' />
            {isFullyOnline
              ? 'Track & Workstation Online'
              : 'Workstation Online • Track Mất kết nối'}
          </span>
          <span className='text-muted-foreground/60'>•</span>
          {isGpuRunning ? (
            <span
              className='font-mono text-[11px] text-indigo-600 dark:text-indigo-400 font-semibold flex items-center gap-1.5'
              title={`GPU đang chạy tác vụ tính toán (${usedVramGb} / ${totalVramGb} GB VRAM)`}
            >
              <span className='size-1.5 rounded-full bg-indigo-500 animate-pulse' />
              <span>
                ⚡ GPU: {usedVramGb}/{totalVramGb} GB (
                {Math.round((usedVramMb / (totalVramMb || 1)) * 100)}%)
              </span>
            </span>
          ) : (
            <span className='font-mono text-[11px] text-muted-foreground'>
              {gpus.length}x A30 ({totalVramGb} GB)
            </span>
          )}
        </div>

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
            className='hidden sm:inline-flex bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[11px] font-medium'
          >
            Sản xuất (Live)
          </Badge>
        )}

        <div className='hidden md:flex'>
          <SearchInput />
        </div>
        <ThemeModeToggle />
        <div className='hidden sm:block'>
          <ThemeSelector />
        </div>
        <NotificationCenter />
      </div>
    </header>
  );
}
