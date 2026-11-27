'use client';

import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig
} from '@/components/ui/chart';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from '@/components/ui/table';
import { Icons } from '@/components/icons';
import { labopsCall } from '@/features/labops/api';
import { useLabOpsStore } from '@/features/labops/store';
import { workloadQueries } from '@/features/jobs/api/queries';
import {
  escapeCsvCell,
  formatStoragePoolShortLabel,
  stableEntityColor
} from '@/features/jobs/api/service';
import type { HistoryRangePreset } from '@/features/jobs/api/types';
import {
  buildMetricTrendModel,
  EMPTY_WORKLOAD_SCOPE,
  StoragePoolsChartCard,
  summarizeMembersFromWorkloads,
  WorkloadCompositionCard
} from './gpu-hours-chart';

interface RawMemberQuotaRow {
  id?: string;
  username?: string;
  fullName?: string;
  full_name?: string;
  role?: string;
  status?: string;
  assignedServer?: string;
  assigned_server?: string;
  cpuLimit?: number;
  cpuLimitCores?: number;
  cpu_limit_cores?: number;
  ramLimitGb?: number;
  ram_limit_gb?: number;
  storageQuotaGb?: number;
  diskQuotaGb?: number;
  storage_quota_gb?: number;
}

const quotaChartConfig = {
  cpuCores: { label: 'CPU Limit (Cores)', color: '#2563b8' },
  ramGb: { label: 'RAM Limit (GiB)', color: '#178e83' }
} satisfies ChartConfig;

const memberUsageConfig = {
  vramGiB: { label: 'VRAM (GiB)', color: '#2563b8' },
  rssGiB: { label: 'RAM RSS (GiB)', color: '#b77919' }
} satisfies ChartConfig;

export function UsageAnalyticsView() {
  const [rangePreset, setRangePreset] = useState<HistoryRangePreset>('24h');

  const storagePools = useLabOpsStore((s) => s.storagePools);
  const gpus = useLabOpsStore((s) => s.gpus);

  const snapshotQuery = useQuery(workloadQueries.snapshot('usage', EMPTY_WORKLOAD_SCOPE));
  const analyticsQuery = useQuery(
    workloadQueries.analytics('usage', EMPTY_WORKLOAD_SCOPE, rangePreset)
  );
  const membersQuery = useQuery({
    queryKey: ['labops', 'usage', 'members-quota'] as const,
    queryFn: async () => {
      const res = await labopsCall<RawMemberQuotaRow[]>('users');
      if (!res.ok) throw new Error(res.error || 'Không thể tải danh sách thành viên');
      return Array.isArray(res.data) ? res.data : [];
    },
    staleTime: 30_000
  });

  const snapshot = snapshotQuery.data;
  const analytics = analyticsQuery.data;

  const utilCpuModel = useMemo(
    () =>
      buildMetricTrendModel(analytics, [
        'gpu_utilization_percent',
        'gpu_vram_utilization_percent',
        'host_cpu_percent'
      ]),
    [analytics]
  );

  const memTempModel = useMemo(
    () => buildMetricTrendModel(analytics, ['gpu_vram_used_gib', 'gpu_temperature_celsius']),
    [analytics]
  );

  const memberConsumption = useMemo(
    () => summarizeMembersFromWorkloads(snapshot?.workloads ?? []),
    [snapshot?.workloads]
  );

  const memberConsumptionMap = useMemo(() => {
    const map = new Map<string, (typeof memberConsumption)[number]>();
    for (const row of memberConsumption) {
      map.set(row.member, row);
    }
    return map;
  }, [memberConsumption]);

  const poolRows = useMemo(
    () =>
      storagePools.map((p) => ({
        name: formatStoragePoolShortLabel(p),
        usedGb: Number((p.usedGb || 0).toFixed(1)),
        freeGb: Number((p.freeGb || 0).toFixed(1))
      })),
    [storagePools]
  );

  const normalizedMembers = useMemo(() => {
    return (membersQuery.data ?? []).map((m) => {
      const username = m.username || 'unknown';
      const usage = memberConsumptionMap.get(username);
      return {
        id: m.id || username,
        username,
        fullName: m.fullName || m.full_name || username,
        role: m.role || 'RESEARCHER',
        status: String(m.status || 'ACTIVE').toUpperCase(),
        server: m.assignedServer || m.assigned_server || 'workstation',
        cpuCores: Number(m.cpuLimitCores ?? m.cpu_limit_cores ?? m.cpuLimit ?? 0),
        ramGb: Number(m.ramLimitGb ?? m.ram_limit_gb ?? 0),
        diskGb: Number(m.storageQuotaGb ?? m.storage_quota_gb ?? m.diskQuotaGb ?? 0),
        liveWorkloads: usage?.workloads ?? 0,
        liveVramGiB: usage?.vramGiB ?? 0,
        liveCpuPct: usage?.cpuPercent ?? 0
      };
    });
  }, [membersQuery.data, memberConsumptionMap]);

  const quotaChartRows = useMemo(
    () =>
      normalizedMembers
        .filter((m) => m.status !== 'ARCHIVED' && (m.cpuCores > 0 || m.ramGb > 0))
        .slice(0, 10)
        .map((m) => ({
          username: m.username,
          cpuCores: m.cpuCores,
          ramGb: m.ramGb
        })),
    [normalizedMembers]
  );

  const vramUsedGiB = useMemo(() => {
    const b = snapshot?.kpis.device_vram_used_bytes ?? 0;
    if (b > 0) return Number((b / 1024 ** 3).toFixed(1));
    return Number((gpus.reduce((a, g) => a + (g.vramUsedMb || 0), 0) / 1024).toFixed(1));
  }, [gpus, snapshot?.kpis.device_vram_used_bytes]);

  const vramTotalGiB = useMemo(() => {
    const b = snapshot?.kpis.device_vram_capacity_bytes ?? 0;
    if (b > 0) return Number((b / 1024 ** 3).toFixed(1));
    return Number((gpus.reduce((a, g) => a + (g.vramTotalMb || 24576), 0) / 1024).toFixed(1));
  }, [gpus, snapshot?.kpis.device_vram_capacity_bytes]);

  const avgGpuTemp = useMemo(() => {
    const temps = gpus.map((g) => g.tempCelsius).filter((t) => typeof t === 'number' && t > 0);
    if (!temps.length) return '—';
    return `${Math.round(temps.reduce((a, b) => a + b, 0) / temps.length)}°C`;
  }, [gpus]);

  const storageSummary = useMemo(() => {
    const capGb = storagePools.reduce((a, p) => a + (p.capacityGb || 0), 0);
    const usedGb = storagePools.reduce((a, p) => a + (p.usedGb || 0), 0);
    const pct = capGb > 0 ? Math.round((usedGb / capGb) * 100) : 0;
    return {
      capTb: (capGb / 1024).toFixed(2),
      usedTb: (usedGb / 1024).toFixed(2),
      pct
    };
  }, [storagePools]);

  const handleExportCsv = () => {
    const header = [
      'username',
      'full_name',
      'role',
      'status',
      'server',
      'cpu_limit_cores',
      'ram_limit_gb',
      'storage_quota_gb',
      'live_workloads',
      'live_vram_gib',
      'live_cpu_percent'
    ]
      .map(escapeCsvCell)
      .join(',');
    const rows = normalizedMembers.map((m) =>
      [
        m.username,
        m.fullName,
        m.role,
        m.status,
        m.server,
        m.cpuCores,
        m.ramGb,
        m.diskGb,
        m.liveWorkloads,
        m.liveVramGiB,
        m.liveCpuPct
      ]
        .map(escapeCsvCell)
        .join(',')
    );
    const csv = '\uFEFF' + [header, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `labops_resource_usage_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className='space-y-5 w-full min-w-0'>
      {/* Controls Bar */}
      <div className='flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-3.5'>
        <div className='space-y-0.5'>
          <div className='text-sm font-semibold text-foreground'>
            Phân tích Sử dụng Tài nguyên &amp; Định mức Thành viên
          </div>
          <div className='text-xs text-muted-foreground'>
            Kết hợp telemetry lịch sử SQLite, snapshot tiến trình V3.1 và hạn ngạch lưu trữ thực tế
          </div>
        </div>
        <div className='flex flex-wrap items-center gap-2'>
          <div className='inline-flex rounded-md border p-0.5 bg-muted/40'>
            {(['1h', '6h', '24h', '7d'] as HistoryRangePreset[]).map((preset) => (
              <Button
                key={preset}
                type='button'
                size='sm'
                variant={rangePreset === preset ? 'secondary' : 'ghost'}
                onClick={() => setRangePreset(preset)}
                className='h-7 px-2.5 text-xs'
              >
                {preset}
              </Button>
            ))}
          </div>
          <Button
            type='button'
            size='sm'
            variant='outline'
            onClick={() => {
              void snapshotQuery.refetch();
              void analyticsQuery.refetch();
              void membersQuery.refetch();
            }}
            className='h-7 text-xs gap-1'
          >
            <Icons.refresh className='size-3.5' /> Làm mới
          </Button>
          <Button
            type='button'
            size='sm'
            variant='outline'
            onClick={handleExportCsv}
            disabled={normalizedMembers.length === 0}
            className='h-7 text-xs gap-1'
          >
            <Icons.download className='size-3.5' /> Xuất CSV
          </Button>
        </div>
      </div>

      {/* 4 KPI Summary Cards */}
      <div className='grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3'>
        <Card className='p-4'>
          <div className='text-xs text-muted-foreground font-medium'>VRAM Đang sử dụng</div>
          <div className='text-2xl font-bold font-mono text-primary mt-1'>
            {vramUsedGiB} / {vramTotalGiB} GiB
          </div>
          <div className='text-xs text-muted-foreground mt-1'>
            {vramTotalGiB > 0 ? Math.round((vramUsedGiB / vramTotalGiB) * 100) : 0}% dung lượng GPU
            quan sát
          </div>
        </Card>

        <Card className='p-4'>
          <div className='text-xs text-muted-foreground font-medium'>
            Nhiệt độ &amp; GPU Hoạt động
          </div>
          <div className='text-2xl font-bold font-mono text-amber-600 dark:text-amber-400 mt-1'>
            {avgGpuTemp}
          </div>
          <div className='text-xs text-muted-foreground mt-1'>
            {snapshot?.kpis.gpu_coverage.fresh ?? gpus.length}/
            {snapshot?.kpis.gpu_coverage.expected ?? gpus.length} GPU đang gửi mẫu FRESH
          </div>
        </Card>

        <Card className='p-4'>
          <div className='text-xs text-muted-foreground font-medium'>
            Workload &amp; Thành viên Chạy
          </div>
          <div className='text-2xl font-bold font-mono text-foreground mt-1'>
            {snapshot?.kpis.observed_workloads ?? 0} tác vụ
          </div>
          <div className='text-xs text-muted-foreground mt-1'>
            {snapshot?.kpis.observed_members ?? memberConsumption.length} thành viên đang sử dụng
            tài nguyên
          </div>
        </Card>

        <Card className='p-4'>
          <div className='text-xs text-muted-foreground font-medium'>
            Lưu trữ Đã dùng ({storagePools.length} Pool)
          </div>
          <div className='text-2xl font-bold font-mono text-indigo-600 dark:text-indigo-400 mt-1'>
            {storageSummary.usedTb} / {storageSummary.capTb} TB
          </div>
          <div className='text-xs text-muted-foreground mt-1'>
            Chiếm {storageSummary.pct}% tổng dung lượng vật lý
          </div>
        </Card>
      </div>

      {/* Row 2: Dual Time Series AreaCharts */}
      <div className='grid grid-cols-1 lg:grid-cols-2 gap-4'>
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>
              Diễn biến Tải GPU &amp; CPU Máy chủ ({rangePreset})
            </CardTitle>
            <CardDescription className='text-xs'>
              Phần trăm sử dụng GPU (NVML) và CPU toàn host theo mẫu lịch sử
            </CardDescription>
          </CardHeader>
          <CardContent>
            {utilCpuModel.rows.length === 0 ? (
              <div className='flex h-56 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
                Chưa có chuỗi dữ liệu trong khung {rangePreset}.
              </div>
            ) : (
              <>
                <ChartContainer config={utilCpuModel.config} className='h-56 w-full'>
                  <AreaChart
                    data={utilCpuModel.rows}
                    margin={{ top: 8, right: 12, left: -12, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray='3 3' vertical={false} opacity={0.25} />
                    <XAxis dataKey='label' tick={{ fontSize: 12 }} minTickGap={24} />
                    <YAxis tick={{ fontSize: 12 }} unit='%' domain={[0, 100]} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    {utilCpuModel.seriesList.map((sr) => {
                      const key = `${sr.metric}:${sr.entity_id}`;
                      const color = String(
                        utilCpuModel.config[key]?.color || stableEntityColor(key)
                      );
                      return (
                        <Area
                          key={key}
                          type='monotone'
                          dataKey={key}
                          name={String(utilCpuModel.config[key]?.label ?? key)}
                          stroke={color}
                          fill={color}
                          fillOpacity={0.14}
                          strokeWidth={2}
                          dot={false}
                          connectNulls={false}
                          isAnimationActive={false}
                        />
                      );
                    })}
                  </AreaChart>
                </ChartContainer>
                <div className='mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground'>
                  {utilCpuModel.seriesList.map((sr) => {
                    const key = `${sr.metric}:${sr.entity_id}`;
                    const color = String(utilCpuModel.config[key]?.color || stableEntityColor(key));
                    return (
                      <span key={key} className='inline-flex items-center gap-1.5'>
                        <span className='size-2.5 rounded-xs' style={{ backgroundColor: color }} />
                        {String(utilCpuModel.config[key]?.label ?? key)}
                      </span>
                    );
                  })}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>
              Diễn biến VRAM (GiB) &amp; Nhiệt độ GPU (°C) ({rangePreset})
            </CardTitle>
            <CardDescription className='text-xs'>
              Theo dõi biến động bộ nhớ HBM2 và nhiệt độ cảm biến theo thiết bị
            </CardDescription>
          </CardHeader>
          <CardContent>
            {memTempModel.rows.length === 0 ? (
              <div className='flex h-56 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
                Chưa có chuỗi dữ liệu trong khung {rangePreset}.
              </div>
            ) : (
              <>
                <ChartContainer config={memTempModel.config} className='h-56 w-full'>
                  <AreaChart
                    data={memTempModel.rows}
                    margin={{ top: 8, right: 12, left: 4, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray='3 3' vertical={false} opacity={0.25} />
                    <XAxis dataKey='label' tick={{ fontSize: 12 }} minTickGap={24} />
                    <YAxis yAxisId='vram' tick={{ fontSize: 12 }} unit=' GiB' width={56} />
                    <YAxis
                      yAxisId='temp'
                      orientation='right'
                      tick={{ fontSize: 12 }}
                      unit='°C'
                      width={46}
                    />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    {memTempModel.seriesList.map((sr) => {
                      const key = `${sr.metric}:${sr.entity_id}`;
                      const color = String(
                        memTempModel.config[key]?.color || stableEntityColor(key)
                      );
                      return (
                        <Area
                          key={key}
                          yAxisId={sr.metric === 'gpu_temperature_celsius' ? 'temp' : 'vram'}
                          type='monotone'
                          dataKey={key}
                          name={String(memTempModel.config[key]?.label ?? key)}
                          stroke={color}
                          fill={color}
                          fillOpacity={0.12}
                          strokeWidth={2}
                          dot={false}
                          connectNulls={false}
                          isAnimationActive={false}
                        />
                      );
                    })}
                  </AreaChart>
                </ChartContainer>
                <div className='mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground'>
                  {memTempModel.seriesList.map((sr) => {
                    const key = `${sr.metric}:${sr.entity_id}`;
                    const color = String(memTempModel.config[key]?.color || stableEntityColor(key));
                    return (
                      <span key={key} className='inline-flex items-center gap-1.5'>
                        <span className='size-2.5 rounded-xs' style={{ backgroundColor: color }} />
                        {String(memTempModel.config[key]?.label ?? key)}
                      </span>
                    );
                  })}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Row 3: Composition Pie + Active Member Bar + Storage Pools Bar */}
      <div className='grid grid-cols-1 lg:grid-cols-3 gap-4'>
        <WorkloadCompositionCard composition={snapshot?.composition} />

        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>
              Tiêu thụ VRAM &amp; RSS theo Thành viên
            </CardTitle>
            <CardDescription className='text-xs'>
              Tổng hợp trực tiếp từ snapshot workload V3.1 hiện tại
            </CardDescription>
          </CardHeader>
          <CardContent>
            {memberConsumption.length === 0 ? (
              <div className='flex h-52 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
                Chưa có thành viên nào đang giữ GPU/CPU.
              </div>
            ) : (
              <ChartContainer config={memberUsageConfig} className='h-52 w-full'>
                <BarChart
                  data={memberConsumption}
                  layout='vertical'
                  margin={{ top: 4, right: 16, left: 16, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray='3 3' horizontal={false} opacity={0.25} />
                  <XAxis type='number' tick={{ fontSize: 12 }} unit='G' />
                  <YAxis dataKey='member' type='category' width={88} tick={{ fontSize: 12 }} />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Bar
                    dataKey='vramGiB'
                    name='VRAM (GiB)'
                    fill='#2563b8'
                    radius={[0, 4, 4, 0]}
                    isAnimationActive={false}
                  />
                  <Bar
                    dataKey='rssGiB'
                    name='RAM RSS (GiB)'
                    fill='#b77919'
                    radius={[0, 4, 4, 0]}
                    isAnimationActive={false}
                  />
                </BarChart>
              </ChartContainer>
            )}
          </CardContent>
        </Card>

        <StoragePoolsChartCard rows={poolRows} />
      </div>

      {/* Row 4: Member Quota BarChart + Accounting Table */}
      <div className='grid grid-cols-1 xl:grid-cols-5 gap-4'>
        <Card className='xl:col-span-2'>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>
              Định mức CPU &amp; RAM theo Thành viên
            </CardTitle>
            <CardDescription className='text-xs'>
              Top 10 tài khoản ACTIVE theo giới hạn vCPU &amp; RAM đã cấp phát
            </CardDescription>
          </CardHeader>
          <CardContent>
            {quotaChartRows.length === 0 ? (
              <div className='flex h-64 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
                Chưa có dữ liệu định mức thành viên.
              </div>
            ) : (
              <>
                <ChartContainer config={quotaChartConfig} className='h-72 w-full'>
                  <BarChart
                    data={quotaChartRows}
                    margin={{ top: 8, right: 16, left: 60, bottom: 36 }}
                  >
                    <CartesianGrid strokeDasharray='3 3' vertical={false} opacity={0.25} />
                    <XAxis
                      dataKey='username'
                      tick={{ fontSize: 12 }}
                      interval={0}
                      angle={-42}
                      textAnchor='end'
                      height={84}
                      padding={{ left: 26, right: 10 }}
                    />
                    <YAxis tick={{ fontSize: 12 }} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Bar
                      dataKey='cpuCores'
                      name='CPU (Cores)'
                      fill='#2563b8'
                      radius={[4, 4, 0, 0]}
                      isAnimationActive={false}
                    />
                    <Bar
                      dataKey='ramGb'
                      name='RAM (GiB)'
                      fill='#178e83'
                      radius={[4, 4, 0, 0]}
                      isAnimationActive={false}
                    />
                  </BarChart>
                </ChartContainer>
                <div className='mt-2 flex flex-wrap items-center justify-center gap-4 text-xs text-muted-foreground'>
                  <span className='inline-flex items-center gap-1.5'>
                    <span className='size-2.5 rounded-xs bg-[#2563b8]' />
                    CPU (Cores)
                  </span>
                  <span className='inline-flex items-center gap-1.5'>
                    <span className='size-2.5 rounded-xs bg-[#178e83]' />
                    RAM (GiB)
                  </span>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card className='xl:col-span-3'>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>
              Bảng Tổng hợp Tài nguyên &amp; Tải Thực tế ({normalizedMembers.length} thành viên)
            </CardTitle>
            <CardDescription className='text-xs'>
              Đối chiếu hạn mức được cấp với mức tiêu thụ tiến trình thời gian thực
            </CardDescription>
          </CardHeader>
          <CardContent className='max-h-72 overflow-y-auto'>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className='text-xs'>Thành viên</TableHead>
                  <TableHead className='text-xs'>Máy chủ</TableHead>
                  <TableHead className='text-xs text-right'>Định mức C/R/D</TableHead>
                  <TableHead className='text-xs text-right'>Jobs chạy</TableHead>
                  <TableHead className='text-xs text-right'>VRAM / CPU thực</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {normalizedMembers.map((m) => (
                  <TableRow key={m.id} className='text-xs'>
                    <TableCell>
                      <div className='font-medium text-foreground'>@{m.username}</div>
                      <div className='text-muted-foreground'>{m.fullName}</div>
                    </TableCell>
                    <TableCell className='font-mono'>{m.server}</TableCell>
                    <TableCell className='text-right font-mono tabular-nums'>
                      {m.cpuCores}C / {m.ramGb}G / {m.diskGb}G
                    </TableCell>
                    <TableCell className='text-right font-mono tabular-nums'>
                      {m.liveWorkloads}
                    </TableCell>
                    <TableCell className='text-right font-mono tabular-nums'>
                      {m.liveVramGiB > 0 || m.liveCpuPct > 0
                        ? `${m.liveVramGiB} GiB / ${m.liveCpuPct}%`
                        : 'Nhàn rỗi'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
