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
  const gpus = useLabOpsStore((state) => state.gpus);

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

      <div className='grid grid-cols-1 gap-4 lg:grid-cols-2 min-w-0 w-full'>
        {servers.map((server) => {
          const isTrack = server.id === 'track';
          const cpuGaugeColor = server.cpuUsagePercent > 80 ? 'stroke-rose-500' : 'stroke-blue-600';
          const ramGaugeColor =
            server.ramUsagePercent > 80 ? 'stroke-rose-500' : 'stroke-indigo-600';
          const diskGaugeColor =
            server.diskUsagePercent > 80 ? 'stroke-amber-500' : 'stroke-cyan-600';

          const serverGpu = gpus.find((g) => g.serverId === server.id);
          const isGpuRunning = Boolean(
            serverGpu &&
            (serverGpu.vramUsedMb > 512 ||
              serverGpu.gpuUtilPercent > 0 ||
              (serverGpu.activeProcesses?.length || 0) > 0)
          );
          const gpuUsedGb = serverGpu ? (serverGpu.vramUsedMb / 1024).toFixed(1) : '0';
          const gpuVramPercent = serverGpu
            ? Math.round((serverGpu.vramUsedMb / (serverGpu.vramTotalMb || 24576)) * 100)
            : 0;

          return (
            <Card
              key={server.id}
              className='relative overflow-hidden border transition-all duration-200 hover:shadow-md bg-card min-w-0'
            >
              <div
                className={`absolute top-0 left-0 right-0 h-1 ${
                  isTrack ? 'bg-blue-600' : 'bg-purple-600'
                }`}
              />

              <CardHeader className='pb-3 pt-4'>
                <div className='flex items-start justify-between gap-2 min-w-0'>
                  <div className='min-w-0 flex-1'>
                    <div className='flex items-center gap-2 flex-wrap'>
                      <CardTitle className='text-base font-bold tracking-tight truncate'>
                        {server.name}
                      </CardTitle>
                      <Badge
                        variant='outline'
                        className={
                          server.status === 'online'
                            ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-mono px-1.5 py-0 shrink-0'
                            : 'bg-destructive/10 text-destructive border-destructive/30 text-[10px] font-mono px-1.5 py-0 shrink-0'
                        }
                      >
                        <span
                          className={`size-1.5 rounded-full mr-1 ${
                            server.status === 'online'
                              ? 'bg-emerald-500 animate-pulse'
                              : 'bg-destructive'
                          }`}
                        ></span>
                        {server.status === 'online' ? 'ONLINE' : 'MẤT KẾT NỐI'}
                      </Badge>
                      <Badge variant='secondary' className='text-[10px] font-mono shrink-0'>
                        {server.hostname}
                      </Badge>
                    </div>
                    <CardDescription className='text-xs mt-0.5 flex items-center gap-2 flex-wrap'>
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
                    className='text-xs px-2.5 py-1 rounded-md border bg-muted/40 hover:bg-muted font-medium transition-colors flex items-center gap-1 text-muted-foreground hover:text-foreground shrink-0'
                  >
                    Quản trị <Icons.arrowRight className='size-3' />
                  </Link>
                </div>
              </CardHeader>

              <CardContent className='pt-0 pb-4 flex flex-col gap-4 min-w-0'>
                {/* 3 Circular Gauges or Offline alert */}
                {server.status !== 'online' ? (
                  <div className='py-6 px-3 text-center text-xs text-muted-foreground flex flex-col items-center justify-center gap-1.5 bg-destructive/5 rounded-lg border border-destructive/20'>
                    <Icons.warning className='size-5 text-destructive' />
                    <p className='font-semibold text-destructive'>Máy chủ từ xa hiện mất kết nối</p>
                    <p className='text-[11px] text-muted-foreground'>
                      Chưa kết nối đường hầm WireGuard VPN. LabOps từ chối hiển thị dữ liệu giả lập.
                    </p>
                  </div>
                ) : (
                  <div className='grid grid-cols-3 gap-1.5 py-3 px-1.5 rounded-lg bg-muted/30 border border-muted/50 min-w-0 w-full'>
                    <CircularGauge
                      value={server.cpuUsagePercent}
                      size={68}
                      strokeWidth={5.5}
                      label='CPU'
                      sublabel={`${server.cpuCores} Cores`}
                      color={cpuGaugeColor}
                    />
                    <CircularGauge
                      value={server.ramUsagePercent}
                      size={68}
                      strokeWidth={5.5}
                      label='Bộ nhớ RAM'
                      sublabel={`${Math.round(server.ramUsedGb)} / ${server.ramTotalGb} GB`}
                      color={ramGaugeColor}
                    />
                    <CircularGauge
                      value={server.diskUsagePercent}
                      size={68}
                      strokeWidth={5.5}
                      label='Lưu trữ'
                      sublabel={`${server.diskUsedTb.toFixed(1)} / ${server.diskTotalTb} TB`}
                      color={diskGaugeColor}
                    />
                  </div>
                )}

                {/* Key specs and status indicators */}
                <div className='grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs min-w-0'>
                  <div className='p-2 rounded-md bg-background border flex flex-col min-w-0'>
                    <div className='flex items-center justify-between text-[11px] text-muted-foreground'>
                      <span className='truncate'>GPU Vật lý</span>
                      {isGpuRunning && (
                        <span
                          className='size-1.5 rounded-full bg-indigo-500 animate-pulse'
                          title='GPU đang chạy tải huấn luyện'
                        />
                      )}
                    </div>
                    <span className='font-bold text-sm text-foreground flex items-center gap-1 mt-0.5 truncate'>
                      <Icons.gpu
                        className={`size-3.5 shrink-0 ${
                          isGpuRunning ? 'text-indigo-600 dark:text-indigo-400' : 'text-primary'
                        }`}
                      />
                      {server.gpuCount}x NVIDIA A30
                    </span>
                    <span className='text-[10px] font-mono truncate'>
                      {isGpuRunning ? (
                        <span className='text-indigo-600 dark:text-indigo-400 font-semibold'>
                          {gpuUsedGb}/24 GB ({gpuVramPercent}%)
                        </span>
                      ) : (
                        <span className='text-muted-foreground'>
                          {server.status === 'online'
                            ? `${server.gpuCount * 24} GB (Rỗi)`
                            : 'Mất kết nối'}
                        </span>
                      )}
                    </span>
                  </div>

                  <div className='p-2 rounded-md bg-background border flex flex-col min-w-0'>
                    <span className='text-[11px] text-muted-foreground truncate'>
                      Băng thông Mạng
                    </span>
                    <span className='font-bold text-sm text-foreground font-mono mt-0.5 truncate'>
                      ↓{server.networkInMbps} ↑{server.networkOutMbps}
                    </span>
                    <span className='text-[10px] text-muted-foreground truncate'>
                      Mbps (WireGuard)
                    </span>
                  </div>

                  <div className='p-2 rounded-md bg-background border flex flex-col min-w-0'>
                    <span className='text-[11px] text-muted-foreground truncate'>
                      Thành viên / Tác vụ
                    </span>
                    <span className='font-bold text-sm text-foreground mt-0.5 truncate'>
                      {server.activeMembers} người • {server.activeJobs} jobs
                    </span>
                    <span className='text-[10px] text-muted-foreground truncate'>
                      Incus Workspaces
                    </span>
                  </div>

                  <div className='p-2 rounded-md bg-background border flex flex-col min-w-0'>
                    <span className='text-[11px] text-muted-foreground truncate'>
                      Thời gian Uptime
                    </span>
                    <span className='font-bold text-sm text-foreground mt-0.5 truncate'>
                      {server.uptime}
                    </span>
                    <span className='text-[10px] text-emerald-600 dark:text-emerald-400 font-medium truncate'>
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
