'use client';

import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Pie,
  PieChart,
  XAxis,
  YAxis
} from 'recharts';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent
} from '@/components/ui/chart';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { workloadQueries } from '@/features/jobs/api/queries';
import {
  escapeCsvCell,
  formatEntityShortLabel,
  formatIctShort,
  formatStoragePoolShortLabel,
  stableEntityColor
} from '@/features/jobs/api/service';
import type {
  AnalyticsSeries,
  HistoryRangePreset,
  ScopeBase,
  SnapshotWorkloadItem,
  WorkloadAnalyticsEnvelope
} from '@/features/jobs/api/types';

export const EMPTY_WORKLOAD_SCOPE: ScopeBase = {
  node_id: null,
  gpu_uuid: null,
  member_id: null,
  group_id: null
};

export interface TrendChartModel {
  seriesList: AnalyticsSeries[];
  rows: Array<Record<string, string | number | null>>;
  config: ChartConfig;
}

const TREND_SERIES_PALETTE = [
  '#2563b8', // blue
  '#178e83', // teal
  '#d97706', // amber
  '#6366f1', // indigo
  '#0ea5e9', // sky
  '#e11d48' // rose
];

const METRIC_PREFERRED_COLOR: Record<string, string> = {
  gpu_utilization_percent: '#2563b8',
  gpu_vram_utilization_percent: '#8b5cf6',
  host_cpu_percent: '#178e83',
  gpu_vram_used_gib: '#6366f1',
  host_ram_used_gib: '#0ea5e9',
  gpu_temperature_celsius: '#d97706'
};

/** Build Recharts rows & ChartConfig for a specific metric from WorkloadAnalyticsEnvelope. */
export function buildMetricTrendModel(
  analytics: WorkloadAnalyticsEnvelope | undefined,
  metrics: string[]
): TrendChartModel {
  const seriesList = (analytics?.series ?? []).filter((sr) => metrics.includes(sr.metric));
  const tsMap = new Map<string, Record<string, string | number | null>>();
  const config: ChartConfig = {};
  const usedColors = new Set<string>();

  for (const [idx, sr] of seriesList.entries()) {
    const key = `${sr.metric}:${sr.entity_id}`;
    const shortMetric =
      sr.metric === 'gpu_utilization_percent'
        ? 'GPU Compute %'
        : sr.metric === 'gpu_vram_utilization_percent'
          ? 'VRAM %'
          : sr.metric === 'host_cpu_percent'
            ? 'CPU %'
            : sr.metric === 'gpu_vram_used_gib'
              ? 'VRAM GiB'
              : sr.metric === 'host_ram_used_gib'
                ? 'RAM GiB'
                : sr.metric === 'gpu_temperature_celsius'
                  ? 'Nhiệt độ °C'
                  : sr.metric;
    const preferred = METRIC_PREFERRED_COLOR[sr.metric];
    const fallbackPaletteColor =
      TREND_SERIES_PALETTE[idx % TREND_SERIES_PALETTE.length] || stableEntityColor(key);
    const chosenColor =
      preferred && !usedColors.has(preferred)
        ? preferred
        : !usedColors.has(fallbackPaletteColor)
          ? fallbackPaletteColor
          : TREND_SERIES_PALETTE.find((c) => !usedColors.has(c)) || stableEntityColor(key);
    usedColors.add(chosenColor);

    const shortEntity = formatEntityShortLabel(sr.entity_id);
    config[key] = {
      label: `${shortMetric} (${shortEntity})`,
      color: chosenColor
    };

    for (const pt of sr.points) {
      let row = tsMap.get(pt.ts);
      if (!row) {
        row = { ts: pt.ts, label: formatIctShort(pt.ts) };
        tsMap.set(pt.ts, row);
      }
      row[key] = pt.quality === 'NO_DATA' || pt.quality === 'OFFLINE' ? null : pt.value;
    }
  }

  const rows = Array.from(tsMap.values()).toSorted((a, b) =>
    String(a.ts).localeCompare(String(b.ts))
  );
  return { seriesList, rows, config };
}

export interface MemberConsumptionSummary {
  member: string;
  groupId: string | null;
  workloads: number;
  gpuWorkloads: number;
  vramGiB: number;
  rssGiB: number;
  cpuPercent: number;
}

/** Aggregate live per-member resource consumption from observed workloads. */
export function summarizeMembersFromWorkloads(
  workloads: SnapshotWorkloadItem[]
): MemberConsumptionSummary[] {
  const map = new Map<string, MemberConsumptionSummary>();
  for (const w of workloads) {
    const key = w.username || w.member_id || 'Chưa ánh xạ';
    const cur = map.get(key) ?? {
      member: key,
      groupId: w.group_id,
      workloads: 0,
      gpuWorkloads: 0,
      vramGiB: 0,
      rssGiB: 0,
      cpuPercent: 0
    };
    cur.workloads += 1;
    if (w.gpu_associated) cur.gpuWorkloads += 1;
    cur.vramGiB += (w.vram.value_bytes ?? 0) / 1024 ** 3;
    cur.rssGiB += (w.memory.value_bytes ?? 0) / 1024 ** 3;
    cur.cpuPercent += w.cpu.value_percent ?? 0;
    map.set(key, cur);
  }
  return Array.from(map.values())
    .map((item) => ({
      ...item,
      vramGiB: Number(item.vramGiB.toFixed(2)),
      rssGiB: Number(item.rssGiB.toFixed(2)),
      cpuPercent: Number(item.cpuPercent.toFixed(1))
    }))
    .toSorted((a, b) => b.vramGiB - a.vramGiB || b.cpuPercent - a.cpuPercent);
}

const memberChartConfig = {
  vramGiB: { label: 'VRAM (GiB)', color: '#2563b8' },
  cpuPercent: { label: 'CPU (%)', color: '#178e83' }
} satisfies ChartConfig;

const resourceChartConfig = {
  usedGb: { label: 'Đang dùng', color: '#2563b8' },
  freeGb: { label: 'Còn trống', color: '#10b981' }
} satisfies ChartConfig;

const overviewPieConfig = {
  count: { label: 'Số tiến trình', color: '#2563b8' }
} satisfies ChartConfig;

export function WorkloadCompositionCard({
  composition
}: {
  composition:
    | {
        gpu_non_monitoring: number;
        non_gpu_compute: number;
        monitoring: number;
        other_unknown: number;
      }
    | null
    | undefined;
}) {
  const pieData = useMemo(() => {
    if (!composition) return [];
    return [
      { name: 'GPU Compute', value: composition.gpu_non_monitoring, fill: '#2563b8' },
      { name: 'CPU / Khác', value: composition.non_gpu_compute, fill: '#178e83' },
      { name: 'Monitoring', value: composition.monitoring, fill: '#64748b' },
      { name: 'Chưa phân loại', value: composition.other_unknown, fill: '#b77919' }
    ].filter((d) => d.value > 0);
  }, [composition]);

  return (
    <Card className='border bg-card min-w-0'>
      <CardHeader className='pb-2'>
        <CardTitle className='text-sm font-semibold'>Cơ cấu Phân loại Workload</CardTitle>
        <CardDescription className='text-xs'>
          Phân nhóm tiến trình đang hoạt động trên cụm máy chủ
        </CardDescription>
      </CardHeader>
      <CardContent>
        {pieData.length === 0 ? (
          <div className='flex h-52 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
            Hiện chưa có workload tính toán nào đang chạy.
          </div>
        ) : (
          <div className='space-y-2'>
            <ChartContainer config={overviewPieConfig} className='h-44 w-full'>
              <PieChart>
                <ChartTooltip content={<ChartTooltipContent />} />
                <Pie
                  data={pieData}
                  dataKey='value'
                  nameKey='name'
                  innerRadius={40}
                  outerRadius={68}
                  paddingAngle={3}
                  isAnimationActive={false}
                />
              </PieChart>
            </ChartContainer>
            <div className='flex flex-wrap justify-center gap-3 text-xs'>
              {pieData.map((d) => (
                <div key={d.name} className='flex items-center gap-1.5'>
                  <span
                    className='size-2.5 rounded-full shrink-0'
                    style={{ backgroundColor: d.fill }}
                  />
                  <span className='text-muted-foreground'>
                    {d.name}: <strong className='text-foreground'>{d.value}</strong>
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function StoragePoolsChartCard({
  rows
}: {
  rows: Array<{ name: string; usedGb: number; freeGb: number }>;
}) {
  return (
    <Card className='border bg-card min-w-0'>
      <CardHeader className='pb-2'>
        <CardTitle className='text-sm font-semibold'>Phân bổ Dung lượng Lưu trữ (GB)</CardTitle>
        <CardDescription className='text-xs'>
          Dung lượng đã dùng vs còn trống trên từng phân vùng vật lý
        </CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <div className='flex h-52 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
            Chưa đồng bộ được thông tin storage pools.
          </div>
        ) : (
          <>
            <ChartContainer config={resourceChartConfig} className='aspect-auto h-52 w-full'>
              <BarChart data={rows} margin={{ top: 8, right: 12, left: 4, bottom: 8 }}>
                <CartesianGrid strokeDasharray='3 3' vertical={false} opacity={0.25} />
                <XAxis
                  dataKey='name'
                  tickLine={false}
                  axisLine={false}
                  interval={0}
                  angle={-22}
                  textAnchor='end'
                  height={48}
                  tick={{ fontSize: 12 }}
                />
                <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 12 }} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar
                  dataKey='usedGb'
                  name='Đã dùng (GB)'
                  stackId='disk'
                  fill='#6366f1'
                  isAnimationActive={false}
                />
                <Bar
                  dataKey='freeGb'
                  name='Còn trống (GB)'
                  stackId='disk'
                  fill='#10b981'
                  radius={[4, 4, 0, 0]}
                  isAnimationActive={false}
                />
              </BarChart>
            </ChartContainer>
            <div className='mt-2 flex flex-wrap items-center justify-center gap-4 text-xs text-muted-foreground'>
              <span className='inline-flex items-center gap-1.5'>
                <span className='size-2.5 rounded-xs bg-[#6366f1]' />
                Đã dùng (GB)
              </span>
              <span className='inline-flex items-center gap-1.5'>
                <span className='size-2.5 rounded-xs bg-[#10b981]' />
                Còn trống (GB)
              </span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function GpuHoursChart() {
  const [viewMode, setViewMode] = useState<'trend' | 'members' | 'infra'>('trend');
  const [rangePreset, setRangePreset] = useState<HistoryRangePreset>('24h');

  const gpus = useLabOpsStore((s) => s.gpus);
  const storagePools = useLabOpsStore((s) => s.storagePools);

  const snapshotQuery = useQuery(workloadQueries.snapshot('overview', EMPTY_WORKLOAD_SCOPE));
  const analyticsQuery = useQuery(
    workloadQueries.analytics('overview', EMPTY_WORKLOAD_SCOPE, rangePreset)
  );

  const snapshot = snapshotQuery.data;
  const analytics = analyticsQuery.data;
  const snapGpus = snapshot?.gpus;

  const trendModel = useMemo(
    () =>
      buildMetricTrendModel(analytics, [
        'gpu_utilization_percent',
        'gpu_vram_utilization_percent',
        'host_cpu_percent'
      ]),
    [analytics]
  );

  const memberRows = useMemo(
    () => summarizeMembersFromWorkloads(snapshot?.workloads ?? []).slice(0, 8),
    [snapshot?.workloads]
  );

  const gpuInfraRows = useMemo(() => {
    if (snapGpus && snapGpus.length > 0) {
      return snapGpus.map((g) => {
        const capGb = Number(((g.vram_capacity_bytes ?? 0) / 1024 ** 3).toFixed(1));
        const usedGb =
          g.quality === 'FRESH' ? Number(((g.vram_used_bytes ?? 0) / 1024 ** 3).toFixed(1)) : 0;
        return {
          name: formatEntityShortLabel(g.gpu_uuid, g.node_id),
          usedGb,
          freeGb: Number(Math.max(0, capGb - usedGb).toFixed(1))
        };
      });
    }
    return gpus.map((g) => {
      const capGb = Number(((g.vramTotalMb || 24576) / 1024).toFixed(1));
      const usedGb = Number(((g.vramUsedMb || 0) / 1024).toFixed(1));
      return {
        name: g.displayLabel || g.id,
        usedGb,
        freeGb: Number(Math.max(0, capGb - usedGb).toFixed(1))
      };
    });
  }, [gpus, snapGpus]);

  const storageInfraRows = useMemo(
    () =>
      storagePools.map((p) => ({
        name: formatStoragePoolShortLabel(p),
        usedGb: Number((p.usedGb || 0).toFixed(1)),
        freeGb: Number((p.freeGb || 0).toFixed(1))
      })),
    [storagePools]
  );

  const handleExportCsv = () => {
    const header = ['mode', 'label', 'metric_1', 'metric_2'].map(escapeCsvCell).join(',');
    const bodyRows =
      viewMode === 'members'
        ? memberRows.map((m) =>
            [
              'member_usage',
              m.member,
              `${m.vramGiB} GiB VRAM`,
              `${m.cpuPercent}% CPU (${m.workloads} jobs)`
            ]
              .map(escapeCsvCell)
              .join(',')
          )
        : viewMode === 'infra'
          ? [...gpuInfraRows, ...storageInfraRows].map((r) =>
              ['infra_capacity', r.name, `used=${r.usedGb}`, `free=${r.freeGb}`]
                .map(escapeCsvCell)
                .join(',')
            )
          : trendModel.rows.map((r) =>
              ['telemetry_trend', String(r.label ?? r.ts), JSON.stringify(r), '']
                .map(escapeCsvCell)
                .join(',')
            );
    const csvContent = '\uFEFF' + [header, ...bodyRows].join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `labops_telemetry_${viewMode}_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const totalObservedVramGiB = useMemo(() => {
    const bytes = snapshot?.kpis.device_vram_used_bytes ?? 0;
    return (bytes / 1024 ** 3).toFixed(1);
  }, [snapshot?.kpis.device_vram_used_bytes]);

  const totalCapacityVramGiB = useMemo(() => {
    const bytes = snapshot?.kpis.device_vram_capacity_bytes ?? 0;
    return (bytes / 1024 ** 3).toFixed(1);
  }, [snapshot?.kpis.device_vram_capacity_bytes]);

  const liveVramPercent = useMemo(() => {
    const used = snapshot?.kpis.device_vram_used_bytes ?? 0;
    const cap = snapshot?.kpis.device_vram_capacity_bytes ?? 0;
    if (cap <= 0) return '0.0';
    return ((used / cap) * 100).toFixed(1);
  }, [snapshot?.kpis.device_vram_used_bytes, snapshot?.kpis.device_vram_capacity_bytes]);

  const liveGpuUtilPercent = useMemo(() => {
    const freshGpus = (snapGpus ?? []).filter(
      (g) => g.quality === 'FRESH' && typeof g.utilization_percent === 'number'
    );
    if (freshGpus.length > 0) {
      const avg =
        freshGpus.reduce((a, g) => a + (g.utilization_percent ?? 0), 0) / freshGpus.length;
      return `${Math.round(avg)}%`;
    }
    const storeVals = gpus
      .map((g) => g.gpuUtilPercent)
      .filter((v): v is number => typeof v === 'number');
    if (storeVals.length > 0) {
      const avg = storeVals.reduce((a, v) => a + v, 0) / storeVals.length;
      return `${Math.round(avg)}%`;
    }
    return '—';
  }, [gpus, snapGpus]);

  return (
    <div className='space-y-4 w-full min-w-0'>
      <Card className='border bg-card min-w-0 w-full'>
        <CardHeader className='pb-2 pt-4'>
          <div className='flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3'>
            <div className='space-y-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <Icons.activity className='size-5 text-primary' />
                <CardTitle className='text-base font-bold tracking-tight'>
                  Biểu đồ Telemetry &amp; Tiêu thụ Tài nguyên Thực tế
                </CardTitle>
                <Badge variant='outline' className='text-xs font-mono'>
                  {snapshot?.workloads?.length ?? 0} workload đang chạy
                </Badge>
              </div>
              <CardDescription className='text-xs'>
                VRAM hiện tại:{' '}
                <strong className='text-foreground font-mono'>
                  {totalObservedVramGiB} / {totalCapacityVramGiB} GiB ({liveVramPercent}%)
                </strong>
                {' • '}
                Tải nhân GPU hiện tại:{' '}
                <strong className='text-foreground font-mono'>{liveGpuUtilPercent}</strong>
                {' • '}
                Dữ liệu thời gian thực từ Go Node Agent &amp; SQLite (
                {analytics?.series?.length ?? 0} chuỗi lịch sử)
              </CardDescription>
            </div>

            <div className='flex flex-wrap items-center gap-2'>
              {viewMode === 'trend' && (
                <div className='inline-flex rounded-md border p-0.5 bg-muted/40'>
                  {(['6h', '24h', '7d'] as HistoryRangePreset[]).map((preset) => (
                    <Button
                      key={preset}
                      type='button'
                      variant={rangePreset === preset ? 'secondary' : 'ghost'}
                      size='sm'
                      onClick={() => setRangePreset(preset)}
                      className='h-7 text-xs px-2'
                    >
                      {preset}
                    </Button>
                  ))}
                </div>
              )}

              <div className='inline-flex flex-wrap rounded-md border p-0.5 bg-muted/40'>
                <Button
                  type='button'
                  variant={viewMode === 'trend' ? 'secondary' : 'ghost'}
                  size='sm'
                  onClick={() => setViewMode('trend')}
                  className='h-7 text-xs px-2.5'
                >
                  Xu hướng Tải
                </Button>
                <Button
                  type='button'
                  variant={viewMode === 'members' ? 'secondary' : 'ghost'}
                  size='sm'
                  onClick={() => setViewMode('members')}
                  className='h-7 text-xs px-2.5'
                >
                  Theo Thành viên
                </Button>
                <Button
                  type='button'
                  variant={viewMode === 'infra' ? 'secondary' : 'ghost'}
                  size='sm'
                  onClick={() => setViewMode('infra')}
                  className='h-7 text-xs px-2.5'
                >
                  GPU &amp; Lưu trữ
                </Button>
              </div>

              <Button
                type='button'
                variant='outline'
                size='sm'
                onClick={handleExportCsv}
                className='h-7 text-xs gap-1'
              >
                <Icons.download className='size-3.5' /> Xuất CSV
              </Button>
            </div>
          </div>
        </CardHeader>

        <CardContent className='pt-2 pb-4'>
          {viewMode === 'trend' && (
            <div className='space-y-2 w-full'>
              <div className='h-[270px] w-full'>
                {trendModel.rows.length === 0 ? (
                  <div className='flex h-full items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
                    {analyticsQuery.isLoading
                      ? 'Đang tải chuỗi telemetry lịch sử...'
                      : 'Chưa có mẫu telemetry lịch sử trong khung giờ này.'}
                  </div>
                ) : (
                  <ChartContainer
                    config={trendModel.config}
                    className='aspect-auto h-[270px] w-full'
                  >
                    <AreaChart
                      data={trendModel.rows}
                      margin={{ top: 10, right: 12, left: -12, bottom: 0 }}
                    >
                      <CartesianGrid strokeDasharray='3 3' vertical={false} opacity={0.25} />
                      <XAxis
                        dataKey='label'
                        tickLine={false}
                        axisLine={false}
                        tick={{ fontSize: 12 }}
                        minTickGap={24}
                      />
                      <YAxis
                        tickLine={false}
                        axisLine={false}
                        tick={{ fontSize: 12 }}
                        unit='%'
                        domain={[0, 100]}
                      />
                      <ChartTooltip content={<ChartTooltipContent />} />
                      {trendModel.seriesList.map((sr) => {
                        const key = `${sr.metric}:${sr.entity_id}`;
                        const color = String(
                          trendModel.config[key]?.color || stableEntityColor(key)
                        );
                        return (
                          <Area
                            key={key}
                            type='monotone'
                            dataKey={key}
                            name={String(trendModel.config[key]?.label ?? key)}
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
                )}
              </div>
              {trendModel.seriesList.length > 0 && (
                <div className='flex flex-wrap items-center gap-4 text-xs text-muted-foreground'>
                  {trendModel.seriesList.map((sr) => {
                    const key = `${sr.metric}:${sr.entity_id}`;
                    const color = String(trendModel.config[key]?.color || stableEntityColor(key));
                    return (
                      <span key={key} className='inline-flex items-center gap-1.5'>
                        <span className='size-2.5 rounded-xs' style={{ backgroundColor: color }} />
                        {String(trendModel.config[key]?.label ?? key)}
                      </span>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {viewMode === 'members' && (
            <div className='h-[270px] w-full'>
              {memberRows.length === 0 ? (
                <div className='flex h-full items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
                  Hiện chưa có workload thành viên đang hoạt động để tổng hợp.
                </div>
              ) : (
                <ChartContainer config={memberChartConfig} className='aspect-auto h-[270px] w-full'>
                  <BarChart
                    data={memberRows}
                    layout='vertical'
                    margin={{ top: 10, right: 18, left: 24, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray='3 3' horizontal={false} opacity={0.25} />
                    <XAxis
                      type='number'
                      tickLine={false}
                      axisLine={false}
                      tick={{ fontSize: 12 }}
                    />
                    <YAxis
                      dataKey='member'
                      type='category'
                      tickLine={false}
                      axisLine={false}
                      width={100}
                      tick={{ fontSize: 12 }}
                    />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Bar
                      dataKey='vramGiB'
                      name='VRAM (GiB)'
                      fill='#2563b8'
                      radius={[0, 4, 4, 0]}
                      isAnimationActive={false}
                    />
                    <Bar
                      dataKey='cpuPercent'
                      name='CPU (%)'
                      fill='#178e83'
                      radius={[0, 4, 4, 0]}
                      isAnimationActive={false}
                    />
                  </BarChart>
                </ChartContainer>
              )}
            </div>
          )}

          {viewMode === 'infra' && (
            <div className='grid grid-cols-1 lg:grid-cols-2 gap-4'>
              <div className='space-y-1.5'>
                <div className='text-xs font-semibold text-foreground'>Bộ nhớ GPU (GiB)</div>
                <ChartContainer
                  config={resourceChartConfig}
                  className='aspect-auto h-[230px] w-full'
                >
                  <BarChart data={gpuInfraRows} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                    <CartesianGrid strokeDasharray='3 3' vertical={false} opacity={0.25} />
                    <XAxis
                      dataKey='name'
                      tickLine={false}
                      axisLine={false}
                      tick={{ fontSize: 12 }}
                    />
                    <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 12 }} unit='G' />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Bar
                      dataKey='usedGb'
                      name='Đang dùng (GiB)'
                      stackId='vram'
                      fill='#2563b8'
                      isAnimationActive={false}
                    />
                    <Bar
                      dataKey='freeGb'
                      name='Còn trống (GiB)'
                      stackId='vram'
                      fill='#10b981'
                      radius={[4, 4, 0, 0]}
                      isAnimationActive={false}
                    />
                  </BarChart>
                </ChartContainer>
              </div>

              <div className='space-y-1.5'>
                <div className='text-xs font-semibold text-foreground'>Lưu trữ theo Pool (GB)</div>
                <ChartContainer
                  config={resourceChartConfig}
                  className='aspect-auto h-[230px] w-full'
                >
                  <BarChart
                    data={storageInfraRows}
                    margin={{ top: 8, right: 8, left: 4, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray='3 3' vertical={false} opacity={0.25} />
                    <XAxis
                      dataKey='name'
                      tickLine={false}
                      axisLine={false}
                      interval={0}
                      tick={{ fontSize: 12 }}
                    />
                    <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 12 }} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Bar
                      dataKey='usedGb'
                      name='Đã dùng (GB)'
                      stackId='disk'
                      fill='#6366f1'
                      isAnimationActive={false}
                    />
                    <Bar
                      dataKey='freeGb'
                      name='Còn trống (GB)'
                      stackId='disk'
                      fill='#178e83'
                      radius={[4, 4, 0, 0]}
                      isAnimationActive={false}
                    />
                  </BarChart>
                </ChartContainer>
                <div className='flex flex-wrap items-center justify-center gap-4 text-xs text-muted-foreground'>
                  <span className='inline-flex items-center gap-1.5'>
                    <span className='size-2.5 rounded-xs bg-[#6366f1]' />
                    Đã dùng (GB)
                  </span>
                  <span className='inline-flex items-center gap-1.5'>
                    <span className='size-2.5 rounded-xs bg-[#178e83]' />
                    Còn trống (GB)
                  </span>
                </div>
              </div>
            </div>
          )}

          <div className='flex items-center gap-2 mt-3 pt-2.5 border-t text-xs text-muted-foreground'>
            <Icons.info className='size-3.5 text-primary shrink-0' />
            <span>
              Biểu đồ tổng hợp trực tiếp từ chuỗi telemetry lịch sử (không nội suy qua khoảng
              offline) và snapshot Workload V3.1 hiện tại.
            </span>
          </div>
        </CardContent>
      </Card>

      <div className='grid grid-cols-1 lg:grid-cols-2 gap-4'>
        <WorkloadCompositionCard composition={snapshot?.composition} />
        <StoragePoolsChartCard rows={storageInfraRows} />
      </div>
    </div>
  );
}
