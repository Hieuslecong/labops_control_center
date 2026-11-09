'use client';

import React, { useState } from 'react';
import { Icons } from '@/components/icons';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar
} from '@/components/ui/sidebar';
import { Badge } from '@/components/ui/badge';

interface ClusterScope {
  id: string;
  name: string;
  location: string;
  gpus: string;
}

const CLUSTERS: ClusterScope[] = [
  {
    id: 'all',
    name: 'Toàn bộ Lab (Tất cả cụm)',
    location: 'Hà Nội & TP.HCM',
    gpus: '4x NVIDIA A30 (96 GB)'
  },
  {
    id: 'track',
    name: 'Cụm TRACK (Server 01)',
    location: 'Phòng Lab Hà Nội',
    gpus: '3x NVIDIA A30 (72 GB)'
  },
  {
    id: 'workstation',
    name: 'Cụm WORKSTATION (Server 02)',
    location: 'Phòng Lab TP.HCM',
    gpus: '1x NVIDIA A30 (24 GB)'
  }
];

export function OrgSwitcher() {
  const isDemoMode = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';
  const { state } = useSidebar();
  const [selectedCluster, setSelectedCluster] = useState<ClusterScope>(CLUSTERS[0]);

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <SidebarMenuButton
                size='lg'
                className='data-popup-open:bg-sidebar-accent data-popup-open:text-sidebar-accent-foreground hover:bg-sidebar-accent/80 transition-colors'
              />
            }
          >
            <div className='bg-primary text-primary-foreground flex aspect-square size-8 shrink-0 items-center justify-center rounded-lg shadow-sm'>
              <Icons.server className='size-4.5' />
            </div>
            <div
              className={`grid flex-1 text-left text-sm leading-tight transition-all duration-200 ease-in-out ${
                state === 'collapsed'
                  ? 'invisible max-w-0 overflow-hidden opacity-0'
                  : 'visible max-w-full opacity-100'
              }`}
            >
              <div className='flex items-center gap-1.5'>
                <span className='truncate font-bold tracking-tight'>LabOps Control</span>
                {isDemoMode ? (
                  <Badge
                    variant='outline'
                    className='text-[10px] px-1 py-0 h-4 border-amber-500/40 text-amber-400 bg-amber-500/10 font-mono'
                  >
                    DEMO
                  </Badge>
                ) : (
                  <Badge
                    variant='outline'
                    className='text-[10px] px-1 py-0 h-4 border-emerald-500/40 text-emerald-400 bg-emerald-500/10 font-mono'
                  >
                    LIVE
                  </Badge>
                )}
              </div>
              <span className='text-sidebar-foreground/70 truncate text-xs font-normal'>
                {selectedCluster.name}
              </span>
            </div>
            <Icons.chevronsUpDown
              className={`ml-auto size-4 transition-all duration-200 ease-in-out text-sidebar-foreground/60 ${
                state === 'collapsed'
                  ? 'invisible max-w-0 opacity-0'
                  : 'visible max-w-full opacity-100'
              }`}
            />
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className='w-(--anchor-width) min-w-64 rounded-lg'
            align='start'
            side='bottom'
            sideOffset={4}
          >
            <DropdownMenuGroup>
              <DropdownMenuLabel className='text-muted-foreground text-xs uppercase tracking-wider font-semibold'>
                Phạm vi hạ tầng quản lý
              </DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuGroup>
              {CLUSTERS.map((cluster) => {
                const isActive = cluster.id === selectedCluster.id;
                return (
                  <DropdownMenuItem
                    key={cluster.id}
                    onClick={() => setSelectedCluster(cluster)}
                    className='gap-2.5 p-2.5 cursor-pointer'
                  >
                    <div className='flex size-7 items-center justify-center rounded-md border bg-muted/50'>
                      <Icons.server className='size-4 text-primary' />
                    </div>
                    <div className='flex flex-col flex-1 leading-tight'>
                      <span className='font-medium text-sm'>{cluster.name}</span>
                      <span className='text-muted-foreground text-xs'>
                        {cluster.location} • {cluster.gpus}
                      </span>
                    </div>
                    {isActive && <Icons.check className='ml-auto size-4 text-primary' />}
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <div className='px-2.5 py-1.5 text-[11px] text-muted-foreground leading-normal'>
                <span className='font-semibold text-foreground'>Hệ thống 2 máy chủ vật lý:</span>
                <br />• Track (3x A30 24GB)
                <br />• Workstation (1x A30 24GB)
              </div>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
