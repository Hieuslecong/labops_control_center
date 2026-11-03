'use client';

import React from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import Link from 'next/link';

interface QuickActionsBarProps {
  onOpenAllocateDialog: () => void;
}

export function QuickActionsBar({ onOpenAllocateDialog }: QuickActionsBarProps) {
  return (
    <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-4 rounded-xl border bg-gradient-to-r from-blue-900/10 via-indigo-900/5 to-card backdrop-blur-xs'>
      <div>
        <div className='flex items-center gap-2 flex-wrap'>
          <h2 className='text-xl font-bold tracking-tight text-foreground'>
            Bảng Điều khiển LabOps
          </h2>
          <Badge
            variant='outline'
            className='bg-primary/10 text-primary border-primary/30 text-[11px] font-semibold'
          >
            Ubuntu 22.04 LTS
          </Badge>
          <Badge
            variant='outline'
            className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[11px] font-mono'
          >
            Incus 6.0 LTS • CUDA 12.4
          </Badge>
        </div>
        <p className='text-xs text-muted-foreground mt-0.5'>
          Hạ tầng phân tán 2 cụm vật lý: <strong>Server Track</strong> (3x NVIDIA A30 24GB) &{' '}
          <strong>Server Workstation</strong> (1x NVIDIA A30 24GB)
        </p>
      </div>

      <div className='flex items-center gap-2 flex-wrap'>
        <Button
          size='sm'
          variant='default'
          onClick={onOpenAllocateDialog}
          className='h-8 text-xs font-semibold gap-1.5 shadow-sm bg-primary'
        >
          <Icons.gpu className='size-3.5' /> Cấp phát GPU
        </Button>

        <Button
          size='sm'
          variant='outline'
          nativeButton={false}
          render={<Link href='/dashboard/users' aria-label='Thêm thành viên' />}
          className='h-8 text-xs font-semibold gap-1.5'
        >
          <Icons.add className='size-3.5' /> Thêm thành viên
        </Button>

        <Button
          size='sm'
          variant='outline'
          nativeButton={false}
          render={<Link href='/dashboard/resources' aria-label='Tạo Workspace Incus' />}
          className='h-8 text-xs font-semibold gap-1.5'
        >
          <Icons.workspace className='size-3.5' /> Tạo Workspace
        </Button>
      </div>
    </div>
  );
}
