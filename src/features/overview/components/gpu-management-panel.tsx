'use client';

import React from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { PhysicalGpu } from '@/types/labops';
import Link from 'next/link';

interface GpuManagementPanelProps {
  onOpenAllocateDialog?: (gpuId?: string) => void;
}

export function GpuManagementPanel({ onOpenAllocateDialog }: GpuManagementPanelProps) {
  const gpus = useLabOpsStore((state) => state.gpus);
  const isDemoMode = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';

  return (
    <div className='flex flex-col gap-3.5'>
      <div className='flex items-center justify-between'>
        <div className='flex items-center gap-2'>
          <Icons.gpu className='size-5 text-indigo-600 dark:text-indigo-400' />
          <div>
            <h3 className='text-lg font-bold tracking-tight'>
              {isDemoMode
                ? 'Quản trị 4 GPU Vật lý (NVIDIA A30)'
                : `Giám sát GPU Vật lý (${gpus.length} GPU được báo cáo)`}
            </h3>
            <p className='text-xs text-muted-foreground'>
              {isDemoMode
                ? 'Hạ tầng độc lập: 3x A30 trên Server Track (72GB) • 1x A30 trên Server Workstation (24GB)'
                : 'Thông tin phần cứng lấy từ collector; chưa có telemetry thì không hiển thị GPU giả.'}
            </p>
          </div>
        </div>
        <div className='flex items-center gap-2'>
          <Button
            size='sm'
            variant='default'
            onClick={() => onOpenAllocateDialog?.()}
            className='h-8 text-xs font-semibold gap-1.5 bg-primary'
          >
            <Icons.add className='size-3.5' /> Cấp phát GPU
          </Button>
          <Link
            href='/dashboard/resources'
            className='text-xs font-medium text-primary hover:underline flex items-center gap-1 ml-2'
          >
            Quản lý tài nguyên <Icons.arrowRight className='size-3.5' />
          </Link>
        </div>
      </div>

      <div className='grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4 min-w-0 w-full'>
        {gpus.map((gpu) => (
          <GpuCard key={gpu.id} gpu={gpu} onAllocate={() => onOpenAllocateDialog?.(gpu.id)} />
        ))}
      </div>
    </div>
  );
}

function getEnforcementBadge(state: PhysicalGpu['enforcementState']) {
  switch (state) {
    case 'HARDWARE_ENFORCED':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-mono'
        >
          HARDWARE ENFORCED
        </Badge>
      );
    case 'DRIVER_ENFORCED_VERIFIED':
      return (
        <Badge
          variant='outline'
          className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[10px] font-mono'
        >
          DRIVER ENFORCED
        </Badge>
      );
    case 'MONITOR_ONLY':
    default:
      return (
        <Badge
          variant='outline'
          className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[10px] font-mono'
        >
          MONITOR ONLY
        </Badge>
      );
  }
}

function getUtilColor(util: number) {
  if (util >= 90) return 'text-rose-600 dark:text-rose-400';
  if (util >= 50) return 'text-amber-600 dark:text-amber-400';
  if (util > 0) return 'text-blue-600 dark:text-blue-400';
  return 'text-muted-foreground';
}

function GpuCard({ gpu, onAllocate }: { gpu: PhysicalGpu; onAllocate: () => void }) {
  const usedGb = (gpu.vramUsedMb / 1024).toFixed(1);
  const totalGb = (gpu.vramTotalMb / 1024).toFixed(0);
  const vramPercent = Math.round((gpu.vramUsedMb / gpu.vramTotalMb) * 100);

  const isRunning =
    gpu.vramUsedMb > 512 ||
    gpu.gpuUtilPercent > 0 ||
    (gpu.activeProcesses && gpu.activeProcesses.length > 0);
  const isNearlyFull = vramPercent >= 80;
  const isAvailable =
    !isNearlyFull && gpu.vramUsedMb < gpu.vramTotalMb - 1024 && gpu.allocationMode !== 'EXCLUSIVE';

  return (
    <Card className='relative overflow-hidden border transition-all duration-200 hover:shadow-md flex flex-col justify-between bg-card min-w-0'>
      {/* Top indicator stripe */}
      <div
        className={`absolute top-0 left-0 right-0 h-1 ${
          gpu.allocationMode === 'EXCLUSIVE'
            ? 'bg-purple-600'
            : isRunning
              ? 'bg-indigo-600'
              : 'bg-emerald-500'
        }`}
      />

      <CardHeader className='pb-3 pt-4'>
        <div className='flex items-start justify-between gap-1'>
          <div>
            <div className='flex items-center gap-1.5 flex-wrap'>
              <CardTitle className='text-base font-bold tracking-tight'>
                {gpu.displayLabel}
              </CardTitle>
              <Badge
                variant={gpu.allocationMode === 'EXCLUSIVE' ? 'default' : 'secondary'}
                className='text-[10px] px-1.5 py-0 font-medium'
              >
                {gpu.allocationMode}
              </Badge>
              {isRunning ? (
                <Badge className='bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/30 text-[10px] px-1.5 py-0 font-medium'>
                  <span className='size-1.5 rounded-full bg-indigo-500 animate-pulse mr-1' />
                  ĐANG CHẠY
                </Badge>
              ) : (
                <Badge
                  variant='outline'
                  className='text-muted-foreground text-[10px] px-1.5 py-0 font-medium'
                >
                  SẴN SÀNG
                </Badge>
              )}
            </div>
            <p className='text-xs text-muted-foreground mt-0.5'>
              {gpu.serverName} • <span className='font-mono'>{gpu.pciBusId}</span>
            </p>
          </div>

          <div className='text-right shrink-0'>
            <span className={`text-xl font-bold font-mono ${getUtilColor(gpu.gpuUtilPercent)}`}>
              {gpu.gpuUtilPercent}%
            </span>
            <div className='text-[10px] text-muted-foreground'>Compute Load</div>
          </div>
        </div>
      </CardHeader>

      <CardContent className='pt-0 pb-4 flex flex-col gap-3 flex-1 justify-between'>
        {/* VRAM Progress */}
        <div className='space-y-1.5'>
          <div className='flex items-center justify-between text-xs'>
            <span className='font-medium text-muted-foreground'>Bộ nhớ VRAM:</span>
            <span className='font-mono font-bold text-foreground'>
              {usedGb} / {totalGb} GB{' '}
              <span className='text-[11px] font-normal text-muted-foreground'>
                ({vramPercent}%)
              </span>
            </span>
          </div>
          <div className='w-full bg-muted rounded-full h-2 overflow-hidden'>
            <div
              className={`h-2 rounded-full transition-all duration-500 ${
                vramPercent > 90
                  ? 'bg-rose-500'
                  : vramPercent > 70
                    ? 'bg-indigo-600'
                    : 'bg-blue-600'
              }`}
              style={{ width: `${vramPercent}%` }}
            />
          </div>
        </div>

        {/* Telemetry metrics: Temp & Power */}
        <div className='grid grid-cols-2 gap-2 text-xs py-2 px-2.5 rounded-md bg-muted/40 border border-muted/60'>
          <div className='flex items-center gap-1.5'>
            <Icons.gauge className='size-3.5 text-muted-foreground' />
            <div>
              <span className='text-[10px] text-muted-foreground block'>Nhiệt độ</span>
              <span
                className={`font-mono font-bold ${gpu.tempCelsius > 75 ? 'text-rose-500' : 'text-foreground'}`}
              >
                {gpu.tempCelsius}°C
              </span>
            </div>
          </div>
          <div className='flex items-center gap-1.5'>
            <Icons.power className='size-3.5 text-muted-foreground' />
            <div>
              <span className='text-[10px] text-muted-foreground block'>Điện năng tiêu thụ</span>
              <span className='font-mono font-bold text-foreground'>
                {gpu.powerWatts}W{' '}
                <span className='text-[10px] text-muted-foreground font-normal'>
                  / {gpu.powerLimitWatts}W
                </span>
              </span>
            </div>
          </div>
        </div>

        {/* Active Processes list */}
        <div className='space-y-1.5'>
          <div className='flex items-center justify-between text-[11px] text-muted-foreground'>
            <span>Tiến trình đang chạy ({gpu.activeProcesses.length}):</span>
            {getEnforcementBadge(gpu.enforcementState)}
          </div>

          {gpu.activeProcesses.length > 0 ? (
            <div className='space-y-1 max-h-24 overflow-y-auto pr-1'>
              {gpu.activeProcesses.map((proc) => (
                <div
                  key={proc.pid}
                  className='p-1.5 rounded border bg-background/80 text-[11px] flex items-center justify-between'
                >
                  <div className='truncate flex-1 mr-1'>
                    <span className='font-mono font-semibold text-primary'>{proc.username}</span>
                    <span className='text-muted-foreground text-[10px] ml-1 truncate'>
                      (PID: {proc.pid})
                    </span>
                    <div className='truncate text-[10px] text-foreground font-mono'>
                      {proc.name}
                    </div>
                  </div>
                  <Badge variant='outline' className='font-mono text-[10px] px-1 py-0 shrink-0'>
                    {(proc.vramMb / 1024).toFixed(1)} GB
                  </Badge>
                </div>
              ))}
            </div>
          ) : (
            <div className='py-3 text-center rounded border border-dashed text-xs text-muted-foreground'>
              GPU đang rỗi — Sẵn sàng cấp phát
            </div>
          )}
        </div>

        {/* Card Footer: UUID & Action */}
        <div className='pt-2 border-t flex items-center justify-between gap-2 mt-auto'>
          <span
            className='text-[10px] text-muted-foreground font-mono truncate max-w-[140px]'
            title={gpu.uuid}
          >
            {gpu.uuid.substring(0, 16)}...
          </span>

          <Button
            size='sm'
            variant={isAvailable ? 'outline' : 'secondary'}
            onClick={onAllocate}
            disabled={!isAvailable}
            className='h-7 text-xs font-semibold px-2.5'
          >
            {gpu.allocationMode === 'EXCLUSIVE'
              ? 'Đang độc quyền'
              : isNearlyFull
                ? `Đầy tải (${vramPercent}%)`
                : isAvailable
                  ? '+ Cấp phát'
                  : 'Hết bộ nhớ'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
