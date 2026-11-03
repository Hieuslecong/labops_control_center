'use client';

import React from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { CircularGauge } from './circular-gauge';
import Link from 'next/link';

export function ServerMonitoringPanel() {
  const servers = useLabOpsStore((state) => state.servers);

  return (
    <div className='flex flex-col gap-3.5'>
      <div className='flex items-center justify-between'>
        <div className='flex items-center gap-2'>
          <Icons.server className='size-5 text-primary' />
          <h3 className='text-lg font-bold tracking-tight'>Giám sát Máy chủ Vật lý</h3>
        </div>
        <Link
          href='/dashboard/servers'
          className='text-xs font-medium text-primary hover:underline flex items-center gap-1'
        >
          Xem chi tiết cả 2 máy chủ <Icons.arrowRight className='size-3.5' />
        </Link>
      </div>

      <div className='grid grid-cols-1 gap-4 lg:grid-cols-2'>
        {servers.map((server) => {
          const isTrack = server.id === 'track';
          const cpuGaugeColor = server.cpuUsagePercent > 80 ? 'stroke-rose-500' : 'stroke-blue-600';
          const ramGaugeColor =
            server.ramUsagePercent > 80 ? 'stroke-rose-500' : 'stroke-indigo-600';
          const diskGaugeColor =
            server.diskUsagePercent > 80 ? 'stroke-amber-500' : 'stroke-cyan-600';

          return (
            <Card
              key={server.id}
              className='relative overflow-hidden border transition-all duration-200 hover:shadow-md bg-card'
            >
              <div
                className={`absolute top-0 left-0 right-0 h-1 ${
                  isTrack ? 'bg-blue-600' : 'bg-purple-600'
                }`}
              />

              <CardHeader className='pb-3 pt-4'>
                <div className='flex items-start justify-between'>
                  <div>
                    <div className='flex items-center gap-2'>
                      <CardTitle className='text-base font-bold tracking-tight'>
                        {server.name}
                      </CardTitle>
                      <Badge
                        variant='outline'
                        className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-mono px-1.5 py-0'
                      >
                        <span className='size-1.5 rounded-full bg-emerald-500 animate-pulse mr-1'></span>
                        ONLINE
                      </Badge>
                      <Badge variant='secondary' className='text-[10px] font-mono'>
                        {server.hostname}
                      </Badge>
                    </div>
                    <CardDescription className='text-xs mt-0.5 flex items-center gap-2'>
                      <span>{server.location}</span>
                      <span>•</span>
                      <span className='font-mono text-foreground font-medium'>
                        IP: {server.ipAddress}
                      </span>
                      <span>•</span>
                      <span className='font-mono text-muted-foreground'>VPN: {server.vpnIp}</span>
                    </CardDescription>
                  </div>

                  <Link
                    href={`/dashboard/servers?id=${server.id}`}
                    className='text-xs px-2.5 py-1 rounded-md border bg-muted/40 hover:bg-muted font-medium transition-colors flex items-center gap-1 text-muted-foreground hover:text-foreground'
                  >
                    Quản trị <Icons.arrowRight className='size-3' />
                  </Link>
                </div>
              </CardHeader>

              <CardContent className='pt-0 pb-4 flex flex-col gap-4'>
                {/* 3 Circular Gauges */}
                <div className='grid grid-cols-3 gap-2 py-3 px-2 rounded-lg bg-muted/30 border border-muted/50'>
                  <CircularGauge
                    value={server.cpuUsagePercent}
                    size={76}
                    strokeWidth={6}
                    label='CPU'
                    sublabel={`${server.cpuCores} Cores`}
                    color={cpuGaugeColor}
                  />
                  <CircularGauge
                    value={server.ramUsagePercent}
                    size={76}
                    strokeWidth={6}
                    label='Bộ nhớ RAM'
                    sublabel={`${Math.round(server.ramUsedGb)} / ${server.ramTotalGb} GB`}
                    color={ramGaugeColor}
                  />
                  <CircularGauge
                    value={server.diskUsagePercent}
                    size={76}
                    strokeWidth={6}
                    label='ZFS Lưu trữ'
                    sublabel={`${server.diskUsedTb.toFixed(1)} / ${server.diskTotalTb} TB`}
                    color={diskGaugeColor}
                  />
                </div>

                {/* Key specs and status indicators */}
                <div className='grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs'>
                  <div className='p-2 rounded-md bg-background border flex flex-col'>
                    <span className='text-[11px] text-muted-foreground'>GPU Vật lý</span>
                    <span className='font-bold text-sm text-foreground flex items-center gap-1 mt-0.5'>
                      <Icons.gpu className='size-3.5 text-primary' />
                      {server.gpuCount}x NVIDIA A30
                    </span>
                    <span className='text-[10px] text-muted-foreground font-mono'>
                      {server.gpuCount * 24} GB VRAM
                    </span>
                  </div>

                  <div className='p-2 rounded-md bg-background border flex flex-col'>
                    <span className='text-[11px] text-muted-foreground'>Băng thông Mạng</span>
                    <span className='font-bold text-sm text-foreground font-mono mt-0.5'>
                      ↓{server.networkInMbps} ↑{server.networkOutMbps}
                    </span>
                    <span className='text-[10px] text-muted-foreground'>Mbps (WireGuard)</span>
                  </div>

                  <div className='p-2 rounded-md bg-background border flex flex-col'>
                    <span className='text-[11px] text-muted-foreground'>Thành viên / Tác vụ</span>
                    <span className='font-bold text-sm text-foreground mt-0.5'>
                      {server.activeMembers} người • {server.activeJobs} jobs
                    </span>
                    <span className='text-[10px] text-muted-foreground'>Incus Workspaces</span>
                  </div>

                  <div className='p-2 rounded-md bg-background border flex flex-col'>
                    <span className='text-[11px] text-muted-foreground'>Thời gian Uptime</span>
                    <span className='font-bold text-sm text-foreground mt-0.5'>
                      {server.uptime}
                    </span>
                    <span className='text-[10px] text-emerald-600 dark:text-emerald-400 font-medium'>
                      Ổn định tuyệt đối
                    </span>
                  </div>
                </div>

                <div className='flex items-center justify-between text-[11px] text-muted-foreground border-t pt-2'>
                  <span className='truncate font-mono'>
                    Incus {server.incusVersion} • Driver {server.driverVersion} • CUDA{' '}
                    {server.cudaVersion}
                  </span>
                  <span className='shrink-0 text-muted-foreground/80 font-mono'>
                    Cập nhật: {server.lastUpdated}
                  </span>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
