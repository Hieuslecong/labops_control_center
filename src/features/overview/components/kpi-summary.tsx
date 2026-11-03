'use client';

import React from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import Link from 'next/link';

export function KpiSummary() {
  const summary = useLabOpsStore((state) => state.getSummary());

  return (
    <div className='grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6'>
      {/* Card 1: Máy chủ */}
      <Link href='/dashboard/servers' aria-label='Xem chi tiết máy chủ vật lý' className='group'>
        <Card className='h-full transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider'>
                Máy chủ vật lý
              </CardDescription>
              <div className='flex size-7 items-center justify-center rounded-md bg-blue-500/10 text-blue-600 dark:text-blue-400'>
                <Icons.server className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5'>
              {summary.servers.online}
              <span className='text-xs font-normal text-muted-foreground'>
                / {summary.servers.total} Trực tuyến
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs'>
            <div className='flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-medium'>
              <span className='size-2 rounded-full bg-emerald-500 animate-pulse'></span>
              <span>100% Hoạt động bình thường</span>
            </div>
            <p className='text-muted-foreground text-[11px] mt-1'>Track: Online • WS: Online</p>
          </CardContent>
        </Card>
      </Link>

      {/* Card 2: GPU */}
      <Link href='/dashboard/resources' aria-label='Xem chi tiết GPU NVIDIA A30' className='group'>
        <Card className='h-full transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider'>
                NVIDIA GPU (A30)
              </CardDescription>
              <div className='flex size-7 items-center justify-center rounded-md bg-indigo-500/10 text-indigo-600 dark:text-indigo-400'>
                <Icons.gpu className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5'>
              {summary.gpus.activeCount}
              <span className='text-xs font-normal text-muted-foreground'>
                / {summary.gpus.totalCount} Đang tải
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs'>
            <div className='flex items-center justify-between text-muted-foreground mb-1'>
              <span>VRAM sử dụng:</span>
              <span className='font-semibold text-foreground font-mono'>
                {summary.gpus.usedVramGb} / {summary.gpus.aggregateVramGb} GB
              </span>
            </div>
            <div className='w-full bg-muted rounded-full h-1.5 overflow-hidden'>
              <div
                className='bg-indigo-600 dark:bg-indigo-500 h-1.5 rounded-full transition-all duration-500'
                style={{
                  width: `${Math.round((summary.gpus.usedVramGb / summary.gpus.aggregateVramGb) * 100)}%`
                }}
              />
            </div>
          </CardContent>
        </Card>
      </Link>

      {/* Card 3: Thành viên Online */}
      <Link
        href='/dashboard/users'
        aria-label='Xem danh sách thành viên trực tuyến'
        className='group'
      >
        <Card className='h-full transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider'>
                Thành viên Online
              </CardDescription>
              <div className='flex size-7 items-center justify-center rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'>
                <Icons.teams className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5'>
              {summary.members.onlineSsh}
              <span className='text-xs font-normal text-muted-foreground'>
                / {summary.members.totalRegistered} Thành viên
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs'>
            <div className='flex items-center gap-1.5 text-muted-foreground'>
              <Icons.key className='size-3.5 text-emerald-500' />
              <span>{summary.members.activeSessions} phiên SSH kết nối</span>
            </div>
            <p className='text-muted-foreground text-[11px] mt-1'>Incus Container riêng biệt</p>
          </CardContent>
        </Card>
      </Link>

      {/* Card 4: Công việc đang chạy */}
      <Link
        href='/dashboard/jobs'
        aria-label='Xem các tác vụ tính toán đang chạy'
        className='group'
      >
        <Card className='h-full transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider'>
                Tác vụ tính toán
              </CardDescription>
              <div className='flex size-7 items-center justify-center rounded-md bg-violet-500/10 text-violet-600 dark:text-violet-400'>
                <Icons.job className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5'>
              {summary.jobs.running}
              <span className='text-xs font-normal text-muted-foreground'>Đang chạy</span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs flex items-center justify-between'>
            <Badge
              variant='outline'
              className='bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/30 text-[11px] font-medium'
            >
              {summary.jobs.pending} chờ
            </Badge>
            <Badge
              variant='outline'
              className='bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-500/30 text-[11px] font-medium'
            >
              {summary.jobs.failed} lỗi
            </Badge>
          </CardContent>
        </Card>
      </Link>

      {/* Card 5: GPU-hours */}
      <Link href='/dashboard/resources' aria-label='Xem phân tích GPU hours' className='group'>
        <Card className='h-full transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider'>
                GPU-Hours (Hôm nay)
              </CardDescription>
              <div className='flex size-7 items-center justify-center rounded-md bg-amber-500/10 text-amber-600 dark:text-amber-400'>
                <Icons.clock className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5'>
              {summary.gpuHours.today}h
              <span className='text-xs font-normal text-emerald-600 dark:text-emerald-400 flex items-center'>
                <Icons.trendingUp className='size-3 mr-0.5' />+
                {summary.gpuHours.previousPeriodChangePercent}%
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs'>
            <div className='text-muted-foreground'>
              7 ngày qua:{' '}
              <span className='font-semibold text-foreground font-mono'>
                {summary.gpuHours.last7Days}h
              </span>
            </div>
            <p className='text-muted-foreground text-[11px] mt-1'>Dựa trên tải tính toán thực tế</p>
          </CardContent>
        </Card>
      </Link>

      {/* Card 6: Cảnh báo */}
      <Link href='/dashboard/security' aria-label='Xem cảnh báo an ninh hạ tầng' className='group'>
        <Card className='h-full transition-all duration-200 hover:shadow-md hover:border-primary/40 bg-card'>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between'>
              <CardDescription className='text-xs font-medium text-muted-foreground uppercase tracking-wider'>
                Cảnh báo an ninh
              </CardDescription>
              <div className='flex size-7 items-center justify-center rounded-md bg-rose-500/10 text-rose-600 dark:text-rose-400'>
                <Icons.shield className='size-4' />
              </div>
            </div>
            <CardTitle className='text-2xl font-bold tracking-tight mt-1 flex items-baseline gap-1.5'>
              {summary.alerts.warnings}
              <span className='text-xs font-normal text-muted-foreground'>Cảnh báo</span>
            </CardTitle>
          </CardHeader>
          <CardContent className='pt-0 text-xs'>
            <div className='flex items-center gap-1.5 text-amber-600 dark:text-amber-400 font-medium'>
              <Icons.warning className='size-3.5' />
              <span>1 repo cần phê duyệt</span>
            </div>
            <p className='text-muted-foreground text-[11px] mt-1'>0 sự cố nghiêm trọng</p>
          </CardContent>
        </Card>
      </Link>
    </div>
  );
}
