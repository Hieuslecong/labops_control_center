'use client';

import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { GpuAccountAllocation, GpuDeviceAllocationGroup, ServerId } from '@/types/labops';

interface GpuAccountAllocationMatrixProps {
  serverIdFilter?: ServerId | 'all';
  onAllocateGpu?: (gpuId: string) => void;
}

function getAccountPillClasses(status: GpuAccountAllocation['status']): string {
  switch (status) {
    case 'IN_USE':
      return 'bg-indigo-500/15 text-indigo-700 dark:text-indigo-300 border-indigo-500/40 font-semibold';
    case 'RESERVED':
      return 'bg-purple-500/15 text-purple-700 dark:text-purple-300 border-purple-500/40 font-semibold';
    case 'IDLE':
      return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/30';
    case 'OFFLINE_HOST':
    default:
      return 'bg-muted/60 text-muted-foreground border-border/80';
  }
}

function renderStatusBadge(alloc: GpuAccountAllocation) {
  if (alloc.status === 'IN_USE') {
    return (
      <Badge className='bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/30 text-xs font-medium'>
        <span className='size-1.5 rounded-full bg-indigo-500 animate-pulse mr-1' />
        ĐANG SỬ DỤNG
      </Badge>
    );
  }
  if (alloc.status === 'RESERVED') {
    return (
      <Badge className='bg-purple-500/10 text-purple-700 dark:text-purple-400 border-purple-500/30 text-xs font-medium'>
        ĐANG GIỮ CHỖ
      </Badge>
    );
  }
  if (alloc.status === 'IDLE') {
    return (
      <Badge
        variant='outline'
        className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-xs'
      >
        SẴN SÀNG
      </Badge>
    );
  }
  return (
    <Badge variant='outline' className='text-muted-foreground text-xs'>
      HOST NGOẠI TUYẾN
    </Badge>
  );
}

function GpuMasterCell({
  group,
  onAllocateGpu
}: {
  group: GpuDeviceAllocationGroup;
  onAllocateGpu?: (gpuId: string) => void;
}) {
  return (
    <td
      rowSpan={Math.max(1, group.accounts.length)}
      className='py-3 px-3.5 align-top border-r bg-muted/15 min-w-[250px] w-[270px]'
    >
      <div className='space-y-2'>
        <div className='flex items-center justify-between gap-1.5 flex-wrap'>
          <div className='flex items-center gap-1.5'>
            <Icons.gpu
              className={`size-4 shrink-0 ${
                group.isOnline ? 'text-primary' : 'text-muted-foreground'
              }`}
            />
            <span className='font-bold text-sm font-mono text-foreground'>{group.gpuLabel}</span>
          </div>
          {group.isOnline ? (
            <Badge
              variant='outline'
              className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-xs font-mono px-1.5 py-0'
            >
              <span className='size-1.5 rounded-full bg-emerald-500 animate-pulse mr-1' />
              ONLINE
            </Badge>
          ) : (
            <Badge
              variant='outline'
              className='text-muted-foreground text-xs font-mono px-1.5 py-0'
            >
              OFFLINE
            </Badge>
          )}
        </div>

        <div className='text-xs text-muted-foreground space-y-0.5'>
          <div className='font-medium text-foreground/90'>
            {group.gpuModel} • {Math.round(group.vramTotalGb)} GB
          </div>
          <div className='truncate' title={group.serverName}>
            {group.serverName}
          </div>
          {group.pciBusId && (
            <div className='font-mono text-xs text-muted-foreground/80'>Bus: {group.pciBusId}</div>
          )}
        </div>

        <div className='flex items-center gap-1.5 flex-wrap'>
          <Badge variant='secondary' className='text-xs font-medium'>
            {group.accountsCount > 0
              ? `${group.accountsCount} tài khoản được cấp`
              : 'Chưa cấp tài khoản'}
          </Badge>
          {group.activeAccountsCount > 0 && (
            <Badge className='bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/30 text-xs font-medium'>
              {group.activeAccountsCount} đang hoạt động
            </Badge>
          )}
        </div>

        {group.isOnline && (
          <div className='space-y-1 pt-1'>
            <div className='w-full bg-muted rounded-full h-1.5 overflow-hidden'>
              <div
                className={`h-full transition-all ${
                  group.vramUsagePercent > 85
                    ? 'bg-rose-500'
                    : group.vramUsagePercent > 50
                      ? 'bg-indigo-600'
                      : 'bg-primary'
                }`}
                style={{ width: `${group.vramUsagePercent}%` }}
              />
            </div>
            <div className='text-xs font-mono text-muted-foreground'>
              {group.vramUsedGb} / {group.vramTotalGb} GB VRAM ({group.vramUsagePercent}%) •{' '}
              {group.gpuUtilPercent}% tải • {group.tempCelsius}°C
            </div>
          </div>
        )}

        {onAllocateGpu && group.isOnline && (
          <div className='pt-1'>
            <Button
              size='sm'
              variant='outline'
              onClick={() => onAllocateGpu(group.gpuId)}
              className='h-6 text-xs font-semibold px-2'
            >
              + Cấp phát vào GPU này
            </Button>
          </div>
        )}
      </div>
    </td>
  );
}

export function GpuAccountAllocationMatrix({
  serverIdFilter = 'all',
  onAllocateGpu
}: GpuAccountAllocationMatrixProps) {
  const gpuAllocationGroups = useLabOpsStore((state) => state.gpuAllocationGroups);
  const [selectedServer, setSelectedServer] = useState<ServerId | 'all'>('all');

  const activeServerFilter: ServerId | 'all' =
    serverIdFilter !== 'all' ? serverIdFilter : selectedServer;

  const visibleGroups = gpuAllocationGroups.filter(
    (g) => activeServerFilter === 'all' || g.serverId === activeServerFilter
  );

  const totalGroupsCount = gpuAllocationGroups.length;
  const workstationCount = gpuAllocationGroups.filter((g) => g.serverId === 'workstation').length;
  const trackCount = gpuAllocationGroups.filter((g) => g.serverId === 'track').length;
  const totalAssignedAccounts = visibleGroups.reduce((acc, g) => acc + g.accountsCount, 0);

  return (
    <Card className='border bg-card min-w-0 w-full'>
      <CardHeader className='pb-3 pt-4 flex flex-col gap-3'>
        <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2'>
          <div>
            <CardTitle className='text-sm font-bold flex items-center gap-2'>
              <Icons.users className='size-4 text-primary' />
              Bảng Thống Kê Tài Khoản Được Cấp Theo Từng Thiết Bị GPU
            </CardTitle>
            <CardDescription className='text-xs mt-0.5'>
              Hiển thị trực quan mỗi GPU vật lý đang được cấp cho những tài khoản thành viên nào,
              hạn mức VRAM và tác vụ đang chiếm dụng thực tế
            </CardDescription>
          </div>
          <div className='flex items-center gap-2 flex-wrap self-start sm:self-auto'>
            <Badge variant='outline' className='font-mono text-xs'>
              {visibleGroups.length} GPU • {totalAssignedAccounts} lượt cấp tài khoản
            </Badge>
          </div>
        </div>

        {serverIdFilter === 'all' && (
          <div className='flex items-center gap-1.5 flex-wrap'>
            <Button
              type='button'
              size='sm'
              variant={selectedServer === 'all' ? 'default' : 'outline'}
              onClick={() => setSelectedServer('all')}
              className='h-7 text-xs font-medium px-2.5'
            >
              Tất cả GPU ({totalGroupsCount})
            </Button>
            <Button
              type='button'
              size='sm'
              variant={selectedServer === 'workstation' ? 'default' : 'outline'}
              onClick={() => setSelectedServer('workstation')}
              className='h-7 text-xs font-medium px-2.5'
            >
              Workstation • Cục bộ ({workstationCount})
            </Button>
            <Button
              type='button'
              size='sm'
              variant={selectedServer === 'track' ? 'default' : 'outline'}
              onClick={() => setSelectedServer('track')}
              className='h-7 text-xs font-medium px-2.5'
            >
              Server Rack ({trackCount})
            </Button>
          </div>
        )}
      </CardHeader>

      <CardContent className='p-0 space-y-4'>
        {/* Top Quick-Summary Strip: GPU -> Accounts pills */}
        {visibleGroups.length > 0 && (
          <div className='px-4 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2.5'>
            {visibleGroups.map((group) => (
              <div
                key={`summary-${group.gpuId}`}
                className='p-2.5 rounded-lg border bg-muted/20 flex flex-col gap-2 min-w-0'
              >
                <div className='flex items-center justify-between gap-1.5'>
                  <div className='flex items-center gap-1.5 min-w-0'>
                    <span
                      className={`size-2 rounded-full shrink-0 ${
                        group.isOnline ? 'bg-emerald-500 animate-pulse' : 'bg-muted-foreground/50'
                      }`}
                    />
                    <span className='font-mono font-bold text-xs text-foreground truncate'>
                      {group.gpuLabel}
                    </span>
                  </div>
                  <Badge variant='outline' className='text-xs font-mono px-1.5 py-0 shrink-0'>
                    {group.accountsCount > 0 ? `${group.accountsCount} tài khoản` : 'Chưa cấp'}
                  </Badge>
                </div>

                <div className='flex flex-wrap gap-1'>
                  {group.accounts.length > 0 ? (
                    group.accounts.map((acc) => (
                      <span
                        key={`pill-${group.gpuId}-${acc.username}`}
                        title={`${acc.fullName} • ${acc.allocationMode} • Quota ${acc.vramQuotaGb} GB`}
                        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-xs font-mono ${getAccountPillClasses(
                          acc.status
                        )}`}
                      >
                        @{acc.username}
                        {acc.vramUsedGb > 0 && (
                          <span className='text-xs opacity-85'>({acc.vramUsedGb}G)</span>
                        )}
                      </span>
                    ))
                  ) : (
                    <span className='text-xs italic text-muted-foreground'>
                      Chưa có tài khoản được cấp
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Main GPU-to-Accounts Grouped Table */}
        <div className='overflow-x-auto border-t'>
          <table className='w-full text-left text-xs'>
            <thead>
              <tr className='border-b text-muted-foreground font-semibold bg-muted/20'>
                <th className='py-2.5 px-3.5 border-r'>Thiết bị GPU & Máy chủ</th>
                <th className='py-2.5 px-3'>Tài khoản Được Cấp</th>
                <th className='py-2.5 px-3'>Chế độ & Hạn mức</th>
                <th className='py-2.5 px-3'>VRAM Đang Dùng</th>
                <th className='py-2.5 px-3'>Tác vụ & Workers</th>
                <th className='py-2.5 px-3'>Trạng thái</th>
              </tr>
            </thead>
            <tbody>
              {visibleGroups.length === 0 ? (
                <tr>
                  <td colSpan={6} className='py-8 text-center text-xs text-muted-foreground'>
                    Không có thiết bị GPU nào khớp với bộ lọc hiện tại.
                  </td>
                </tr>
              ) : (
                visibleGroups.map((group, groupIdx) => {
                  const groupBorderClass = groupIdx > 0 ? 'border-t-2 border-border' : '';

                  if (group.accounts.length === 0) {
                    return (
                      <tr key={group.gpuId} className={groupBorderClass}>
                        <GpuMasterCell group={group} onAllocateGpu={onAllocateGpu} />
                        <td
                          colSpan={5}
                          className='py-5 px-4 text-xs italic text-muted-foreground text-center bg-background'
                        >
                          Chưa có tài khoản nào được cấp quyền trên thiết bị GPU này.
                        </td>
                      </tr>
                    );
                  }

                  return group.accounts.map((alloc, accIdx) => {
                    const rowBorderClass =
                      accIdx === 0 ? groupBorderClass : 'border-t border-border/60';
                    return (
                      <tr
                        key={`${group.gpuId}-${alloc.username}`}
                        className={`${rowBorderClass} hover:bg-muted/25 transition-colors`}
                      >
                        {accIdx === 0 && (
                          <GpuMasterCell group={group} onAllocateGpu={onAllocateGpu} />
                        )}
                        <td className='py-2.5 px-3'>
                          <div className='font-bold text-foreground'>{alloc.fullName}</div>
                          <div className='text-xs text-muted-foreground font-mono flex items-center gap-1.5'>
                            <span>@{alloc.username}</span>
                            <span className='size-1 rounded-full bg-border' />
                            <span className='text-xs uppercase font-semibold text-primary'>
                              {alloc.role}
                            </span>
                          </div>
                        </td>
                        <td className='py-2.5 px-3'>
                          <div className='flex items-center gap-1.5 flex-wrap'>
                            <Badge
                              variant={
                                alloc.allocationMode === 'EXCLUSIVE' ? 'default' : 'secondary'
                              }
                              className='text-xs font-mono'
                            >
                              {alloc.allocationMode}
                            </Badge>
                          </div>
                          <div className='text-xs font-mono text-muted-foreground mt-0.5'>
                            Quota:{' '}
                            <span className='font-semibold text-foreground'>
                              {alloc.vramQuotaGb} GB
                            </span>
                          </div>
                        </td>
                        <td className='py-2.5 px-3'>
                          {alloc.vramUsedGb > 0 ? (
                            <div className='space-y-0.5'>
                              <span className='font-mono font-bold text-indigo-600 dark:text-indigo-400'>
                                {alloc.vramUsedGb} GB
                              </span>
                              <div className='text-xs text-muted-foreground font-mono'>
                                {alloc.vramUsedMb} MiB
                              </div>
                            </div>
                          ) : (
                            <span className='text-muted-foreground font-mono text-xs'>
                              0 GB (Trống)
                            </span>
                          )}
                        </td>
                        <td className='py-2.5 px-3 max-w-xs'>
                          <div
                            className='font-mono font-medium text-xs text-foreground truncate max-w-[260px]'
                            title={alloc.primaryScript}
                          >
                            {alloc.primaryScript}
                          </div>
                          {(alloc.processCount > 0 || alloc.primaryPid) && (
                            <div className='text-xs text-muted-foreground font-mono'>
                              {alloc.processCount > 0 ? `${alloc.processCount} tiến trình` : ''}
                              {alloc.processCount > 0 && alloc.primaryPid ? ' • ' : ''}
                              {alloc.primaryPid ? `PID chính: ${alloc.primaryPid}` : ''}
                            </div>
                          )}
                        </td>
                        <td className='py-2.5 px-3'>{renderStatusBadge(alloc)}</td>
                      </tr>
                    );
                  });
                })
              )}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
