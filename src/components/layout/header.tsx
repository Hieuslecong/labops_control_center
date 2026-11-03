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

export default function Header() {
  return (
    <header className='bg-background/80 sticky top-0 z-20 flex h-16 shrink-0 items-center justify-between gap-2 border-b backdrop-blur-md md:h-14 px-4'>
      <div className='flex items-center gap-2'>
        <SidebarTrigger className='-ml-1' />
        <Separator orientation='vertical' className='mr-2 h-4 data-vertical:self-center' />
        <Breadcrumbs />
      </div>

      <div className='flex items-center gap-2.5'>
        {/* Live Infrastructure Status indicator */}
        <div className='hidden lg:flex items-center gap-2 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-xs text-emerald-700 dark:text-emerald-400 font-medium'>
          <span className='relative flex h-2 w-2'>
            <span className='animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75'></span>
            <span className='relative inline-flex rounded-full h-2 w-2 bg-emerald-500'></span>
          </span>
          <span className='flex items-center gap-1.5'>
            <Icons.server className='size-3.5' />
            Track & Workstation Online
          </span>
          <span className='text-muted-foreground/60'>•</span>
          <span className='font-mono text-[11px]'>4x A30 (96 GB)</span>
        </div>

        <Badge
          variant='outline'
          className='hidden sm:inline-flex bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[11px] font-medium'
        >
          Chế độ Demo
        </Badge>

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
