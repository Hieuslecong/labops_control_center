'use client';

import React, { useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
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
import type {
  AnalyticsSeries,
  CapabilityEntry,
  SnapshotGpuItem,
  SnapshotNodeItem,
  SnapshotWorkloadItem,
  WorkloadAnalyticsEnvelope,
  WorkloadEventsEnvelope,
  WorkloadHistoryEnvelope,
  WorkloadSnapshotEnvelope
} from '@/features/jobs/api/types';
import {
  formatBytesGiB,
  formatDurationSeconds,
  formatEntityShortLabel,
  formatIctShort,
  formatIctTime,
  stableEntityColor
} from '@/features/jobs/api/service';

function qualityBadgeVariant(q: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (q === 'FRESH' || q === 'OBSERVED' || q === 'CONSISTENT' || q === 'SUPPORTED') {
    return 'secondary';
  }
  if (q === 'OFFLINE' || q === 'ERROR' || q === 'MISMATCH' || q === 'DENIED') {
    return 'destructive';
  }
  return 'outline';
}

function formatPct(val: number | null | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${val.toFixed(1)}%`;
}

// ---------- Overview Tab Panels (C01-C04 + Attention Strip) ----------

const compositionChartConfig = {
  count: {
    label: 'Số workload',
    color: 'var(--chart-1, #2563b8)'
  }
} satisfies ChartConfig;

const vramReconChartConfig = {
  value_gib: {
    label: 'Dung lượng (GiB)',
    color: 'var(--chart-2, #178e83)'
  }
} satisfies ChartConfig;

const memberBarChartConfig = {
  workloads: {
    label: 'Workloads',
    color: 'var(--chart-1, #2563b8)'
  }
} satisfies ChartConfig;

interface WorkloadOverviewPanelsProps {
  snapshot: WorkloadSnapshotEnvelope;
}

export function WorkloadOverviewPanels({ snapshot }: WorkloadOverviewPanelsProps) {
  const compositionData = useMemo(() => {
    if (!snapshot.composition) return null;
    return [
      {
        key: 'gpu_non_monitoring',
        label: 'GPU Compute',
        count: snapshot.composition.gpu_non_monitoring,
        fill: '#2563b8'
      },
      {
        key: 'non_gpu_compute',
        label: 'CPU / Khác',
        count: snapshot.composition.non_gpu_compute,
        fill: '#178e83'
      },
      {
        key: 'monitoring',
        label: 'Monitoring',
        count: snapshot.composition.monitoring,
        fill: '#64748b'
      },
      {
        key: 'other_unknown',
        label: 'Chưa phân loại',
        count: snapshot.composition.other_unknown,
        fill: '#b77919'
      }
    ];
  }, [snapshot.composition]);

  const vramComparison = useMemo(() => {
    const freshDeviceBytes = snapshot.gpus.reduce(
      (acc, g) => acc + (g.quality === 'FRESH' && g.vram_used_bytes ? g.vram_used_bytes : 0),
      0
    );
    const hasGpuTelemetry = snapshot.gpus.some((g) => g.quality === 'FRESH');
    const processAttributedBytes = snapshot.workloads.reduce((acc, w) => {
      if (w.gpu_allocations && w.gpu_allocations.length > 0) {
        return acc + w.gpu_allocations.reduce((s, a) => s + a.value_bytes, 0);
      }
      return acc + (w.vram.value_bytes ?? 0);
    }, 0);
    const recon = snapshot.device_vram.reconciliation;
    return {
      hasGpuTelemetry,
      freshDeviceBytes,
      processAttributedBytes,
      recon,
      chartRows: [
        {
          name: 'VRAM Thiết bị (NVML)',
          value_gib: Number((freshDeviceBytes / 1024 ** 3).toFixed(2)),
          fill: '#2563b8'
        },
        {
          name: 'VRAM Gán tiến trình',
          value_gib: Number((processAttributedBytes / 1024 ** 3).toFixed(2)),
          fill: recon === 'MISMATCH' ? '#dc2626' : '#178e83'
        }
      ]
    };
  }, [snapshot.gpus, snapshot.workloads, snapshot.device_vram.reconciliation]);

  const memberDistribution = useMemo(() => {
    const counts = new Map<
      string,
      { member: string; entityId: string; workloads: number; gpuCount: number; fill: string }
    >();
    for (const w of snapshot.workloads) {
      const label = w.username || w.member_id || 'Chưa ánh xạ';
      const eid = w.member_id || w.username || 'unmapped';
      const prev = counts.get(eid) ?? {
        member: label,
        entityId: eid,
        workloads: 0,
        gpuCount: 0,
        fill: stableEntityColor(eid)
      };
      prev.workloads += 1;
      if (w.gpu_associated) prev.gpuCount += 1;
      counts.set(eid, prev);
    }
    return Array.from(counts.values())
      .toSorted((a, b) => b.workloads - a.workloads || a.member.localeCompare(b.member))
      .slice(0, 8);
  }, [snapshot.workloads]);

  const offlineNodes = snapshot.nodes.filter(
    (n) => n.quality === 'OFFLINE' || n.quality === 'STALE'
  );

  return (
    <div className='space-y-4'>
      {/* Compact Attention & Reconciliation Strip */}
      <div className='flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-xs'>
        <div className='flex flex-wrap items-center gap-3'>
          <span className='inline-flex items-center gap-1.5 font-medium text-foreground'>
            <Icons.server className='size-3.5 text-muted-foreground' />
            Nodes tươi: {snapshot.quality.fresh_nodes}/{snapshot.quality.expected_nodes ?? '—'}
          </span>
          {offlineNodes.length > 0 && (
            <Badge variant='destructive' className='text-xs'>
              {offlineNodes.map((n) => `${n.id}: ${n.quality}`).join(', ')}
            </Badge>
          )}
          <span className='inline-flex items-center gap-1.5 text-muted-foreground'>
            <Icons.database className='size-3.5' />
            Đối soát VRAM:
            <Badge variant={qualityBadgeVariant(vramComparison.recon)} className='text-xs'>
              {vramComparison.recon}
            </Badge>
          </span>
        </div>
        <span className='text-muted-foreground'>
          Bộ nhớ quy đổi: RSS cộng dồn cây (cảnh báo double-count khi dùng bộ nhớ chia sẻ)
        </span>
      </div>

      <div className='grid grid-cols-1 gap-4 lg:grid-cols-2'>
        {/* C01: Workload Composition */}
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>Cơ cấu Workload (C01)</CardTitle>
            <CardDescription className='text-xs'>
              Phân tách rạch ròi GPU Compute, CPU Compute và tiến trình giám sát (gpustat / nvtop)
            </CardDescription>
          </CardHeader>
          <CardContent>
            {compositionData === null ? (
              <div className='flex h-48 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
                Không đủ dữ liệu tổng hợp cơ cấu trên các node hiện tại.
              </div>
            ) : (
              <ChartContainer config={compositionChartConfig} className='h-48 w-full'>
                <BarChart data={compositionData} layout='vertical' margin={{ left: 8, right: 16 }}>
                  <CartesianGrid strokeDasharray='3 3' horizontal={false} />
                  <XAxis type='number' allowDecimals={false} tick={{ fontSize: 12 }} />
                  <YAxis dataKey='label' type='category' width={108} tick={{ fontSize: 12 }} />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Bar dataKey='count' fill='#2563b8' radius={[0, 4, 4, 0]} />
                </BarChart>
              </ChartContainer>
            )}
          </CardContent>
        </Card>

        {/* C02: Node & GPU Freshness Snapshot */}
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>Tình trạng Node & GPU (C02)</CardTitle>
            <CardDescription className='text-xs'>
              Độ tươi telemetry thời gian thực theo từng máy chủ và GPU
            </CardDescription>
          </CardHeader>
          <CardContent className='space-y-3'>
            <div className='flex flex-wrap gap-2'>
              {snapshot.nodes.map((node: SnapshotNodeItem) => (
                <div
                  key={node.id}
                  className='flex items-center justify-between gap-3 rounded-md border px-2.5 py-1.5 text-xs flex-1 min-w-[200px]'
                >
                  <div>
                    <div className='font-semibold text-foreground'>{node.id}</div>
                    <div className='text-muted-foreground'>
                      {node.observed_at ? formatIctShort(node.observed_at) : 'Chưa có mẫu'}
                    </div>
                  </div>
                  <Badge variant={qualityBadgeVariant(node.quality)} className='text-xs'>
                    {node.quality}
                  </Badge>
                </div>
              ))}
            </div>
            <div className='space-y-1.5 max-h-36 overflow-y-auto pr-1'>
              {snapshot.gpus.length === 0 ? (
                <div className='text-xs text-muted-foreground'>
                  Không có GPU nào trong phạm vi lọc.
                </div>
              ) : (
                snapshot.gpus.map((g: SnapshotGpuItem) => {
                  const pct =
                    g.vram_used_bytes !== null && g.vram_capacity_bytes
                      ? Math.min(100, Math.round((g.vram_used_bytes / g.vram_capacity_bytes) * 100))
                      : 0;
                  return (
                    <div
                      key={g.gpu_uuid}
                      className='space-y-1 rounded border/60 bg-muted/20 p-2 text-xs'
                    >
                      <div className='flex items-center justify-between gap-2'>
                        <span className='font-mono font-medium truncate' title={g.gpu_uuid}>
                          {formatEntityShortLabel(g.gpu_uuid, g.node_id)}
                        </span>
                        <div className='flex items-center gap-2 shrink-0'>
                          <span className='tabular-nums text-muted-foreground'>
                            Util: {formatPct(g.utilization_percent)} •{' '}
                            {formatBytesGiB(g.vram_used_bytes)} /{' '}
                            {formatBytesGiB(g.vram_capacity_bytes)}
                          </span>
                          <Badge variant={qualityBadgeVariant(g.quality)} className='text-xs'>
                            {g.quality}
                          </Badge>
                        </div>
                      </div>
                      <div className='h-1.5 w-full rounded-full bg-muted overflow-hidden'>
                        <div
                          className='h-full rounded-full bg-primary transition-all'
                          style={{ width: `${g.quality === 'FRESH' ? pct : 0}%` }}
                        />
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </CardContent>
        </Card>

        {/* C03: VRAM Reconciliation */}
        <Card>
          <CardHeader className='pb-2'>
            <div className='flex items-center justify-between gap-2'>
              <CardTitle className='text-sm font-semibold'>
                Đối soát VRAM Thiết bị & Tiến trình (C03)
              </CardTitle>
              <Badge variant={qualityBadgeVariant(vramComparison.recon)} className='text-xs'>
                {vramComparison.recon}
              </Badge>
            </div>
            <CardDescription className='text-xs'>
              {snapshot.device_vram.member_filter_applied === false &&
              (snapshot.scope.member_id || snapshot.scope.group_id)
                ? 'Lưu ý: VRAM thiết bị tính toàn GPU; cột tiến trình chỉ tính phạm vi thành viên/nhóm đã lọc (NOT_COMPARABLE).'
                : 'So sánh tổng NVML device used với tổng VRAM gán trực tiếp cho tiến trình (giảm thiểu sai lệch do container/root process).'}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!vramComparison.hasGpuTelemetry ? (
              <div className='flex h-44 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
                Không có telemetry GPU tươi để đối soát.
              </div>
            ) : (
              <ChartContainer config={vramReconChartConfig} className='h-44 w-full'>
                <BarChart
                  data={vramComparison.chartRows}
                  margin={{ top: 8, right: 16, left: 8, bottom: 4 }}
                >
                  <CartesianGrid strokeDasharray='3 3' vertical={false} />
                  <XAxis dataKey='name' tick={{ fontSize: 12 }} />
                  <YAxis unit=' GiB' tick={{ fontSize: 12 }} />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Bar dataKey='value_gib' fill='#178e83' radius={[4, 4, 0, 0]} maxBarSize={64} />
                </BarChart>
              </ChartContainer>
            )}
          </CardContent>
        </Card>

        {/* C04: Member Distribution */}
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>Phân bổ theo Thành viên (C04)</CardTitle>
            <CardDescription className='text-xs'>
              Số lượng workload đang quan sát theo tài khoản thực thi
            </CardDescription>
          </CardHeader>
          <CardContent>
            {memberDistribution.length === 0 ? (
              <div className='flex h-44 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
                Chưa phát hiện workload đang hoạt động trong phạm vi lọc.
              </div>
            ) : (
              <ChartContainer config={memberBarChartConfig} className='h-44 w-full'>
                <BarChart
                  data={memberDistribution}
                  layout='vertical'
                  margin={{ left: 8, right: 16 }}
                >
                  <CartesianGrid strokeDasharray='3 3' horizontal={false} />
                  <XAxis type='number' allowDecimals={false} tick={{ fontSize: 12 }} />
                  <YAxis dataKey='member' type='category' width={104} tick={{ fontSize: 12 }} />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Bar dataKey='workloads' fill='#2563b8' radius={[0, 4, 4, 0]} />
                </BarChart>
              </ChartContainer>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// ---------- Analytics Tab Panels (C05-C11, Capabilities, T02-T04) ----------

interface PivotedSeriesGroup {
  title: string;
  subtitle: string;
  unit: string;
  seriesList: AnalyticsSeries[];
  rows: Array<Record<string, string | number | null>>;
}

function buildMetricGroupChart(
  title: string,
  subtitle: string,
  unit: string,
  seriesList: AnalyticsSeries[]
): PivotedSeriesGroup {
  const tsMap = new Map<string, Record<string, string | number | null>>();
  for (const sr of seriesList) {
    for (const pt of sr.points) {
      let row = tsMap.get(pt.ts);
      if (!row) {
        row = { ts: pt.ts, label: formatIctShort(pt.ts) };
        tsMap.set(pt.ts, row);
      }
      row[sr.entity_id] = pt.quality === 'NO_DATA' || pt.quality === 'OFFLINE' ? null : pt.value;
    }
  }
  const rows = Array.from(tsMap.values()).toSorted((a, b) =>
    String(a.ts).localeCompare(String(b.ts))
  );
  return { title, subtitle, unit, seriesList, rows };
}

const ANALYTICS_SERIES_PALETTE = ['#2563b8', '#178e83', '#d97706', '#6366f1', '#0ea5e9', '#e11d48'];

function AnalyticsSeriesCard({ group }: { group: PivotedSeriesGroup }) {
  const config: ChartConfig = useMemo(() => {
    const out: ChartConfig = {};
    for (const [idx, sr] of group.seriesList.entries()) {
      out[sr.entity_id] = {
        label: formatEntityShortLabel(sr.entity_id),
        color:
          ANALYTICS_SERIES_PALETTE[idx % ANALYTICS_SERIES_PALETTE.length] ||
          stableEntityColor(sr.entity_id)
      };
    }
    return out;
  }, [group.seriesList]);

  const meanCov = useMemo(() => {
    const covs = group.seriesList
      .map((s) => s.coverage_ratio)
      .filter((c): c is number => c !== null && c !== undefined);
    if (covs.length === 0) return null;
    return Math.round((covs.reduce((a, b) => a + b, 0) / covs.length) * 100);
  }, [group.seriesList]);

  return (
    <Card>
      <CardHeader className='pb-2'>
        <div className='flex items-center justify-between gap-2'>
          <CardTitle className='text-sm font-semibold'>{group.title}</CardTitle>
          {meanCov !== null && (
            <Badge variant='outline' className='text-xs'>
              Độ phủ mẫu: {meanCov}%
            </Badge>
          )}
        </div>
        <CardDescription className='text-xs'>{group.subtitle}</CardDescription>
      </CardHeader>
      <CardContent>
        {group.rows.length === 0 ? (
          <div className='flex h-52 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground'>
            NO_DATA — Chưa có mẫu lịch sử cho chuỗi này trong khoảng thời gian đã chọn.
          </div>
        ) : (
          <ChartContainer config={config} className='h-52 w-full'>
            <LineChart data={group.rows} margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
              <CartesianGrid strokeDasharray='3 3' />
              <XAxis dataKey='label' tick={{ fontSize: 12 }} minTickGap={24} />
              <YAxis tick={{ fontSize: 12 }} unit={group.unit ? ` ${group.unit}` : ''} />
              <ChartTooltip content={<ChartTooltipContent />} />
              {group.seriesList.map((sr, idx) => {
                const shortLabel = formatEntityShortLabel(sr.entity_id);
                const color =
                  ANALYTICS_SERIES_PALETTE[idx % ANALYTICS_SERIES_PALETTE.length] ||
                  stableEntityColor(sr.entity_id);
                return (
                  <Line
                    key={sr.entity_id}
                    type='monotone'
                    dataKey={sr.entity_id}
                    name={shortLabel}
                    stroke={color}
                    strokeWidth={2}
                    dot={false}
                    connectNulls={false}
                  />
                );
              })}
            </LineChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  );
}

interface WorkloadAnalyticsPanelsProps {
  analytics: WorkloadAnalyticsEnvelope | undefined;
  snapshot: WorkloadSnapshotEnvelope;
  isLoading: boolean;
  error: Error | null;
}

export function WorkloadAnalyticsPanels({
  analytics,
  snapshot,
  isLoading,
  error
}: WorkloadAnalyticsPanelsProps) {
  const metricGroups = useMemo(() => {
    const series = analytics?.series ?? [];
    const byMetric = (m: string) => series.filter((s) => s.metric === m);
    return [
      buildMetricGroupChart(
        'GPU Utilization theo Thời gian (C07)',
        'Ngắt đường nối tại các khoảng offline/NO_DATA (không nội suy)',
        '%',
        byMetric('gpu_utilization_percent')
      ),
      buildMetricGroupChart(
        'GPU VRAM Đã dùng (C08)',
        'Dung lượng VRAM thiết bị theo từng GPU UUID (GiB)',
        'GiB',
        byMetric('gpu_vram_used_gib')
      ),
      buildMetricGroupChart(
        'CPU Máy chủ (C05)',
        'Tổng phần trăm CPU theo host (telemetry_history_samples)',
        '%',
        byMetric('host_cpu_percent')
      ),
      buildMetricGroupChart(
        'RAM Máy chủ Đã dùng (C06)',
        'Tổng RAM hệ thống theo host (GiB)',
        'GiB',
        byMetric('host_ram_used_gib')
      ),
      buildMetricGroupChart(
        'Nhiệt độ GPU (C09)',
        'Nhiệt độ cảm biến GPU (°C)',
        '°C',
        byMetric('gpu_temperature_celsius')
      ),
      buildMetricGroupChart(
        'Device Occupancy & Compute-Equivalent Hours (C10/C11)',
        'Tính trên thiết bị GPU từ các khoảng mẫu liên tiếp <= 180s (không gán giả cho thành viên)',
        'h',
        [...byMetric('device_occupancy_hours'), ...byMetric('device_compute_equivalent_hours')]
      )
    ];
  }, [analytics?.series]);

  // T02: Live Member Resource Summary from current snapshot
  const memberSummaryRows = useMemo(() => {
    const map = new Map<
      string,
      {
        member: string;
        groupId: string | null;
        count: number;
        gpuCount: number;
        vramBytes: number;
        rssBytes: number;
      }
    >();
    for (const w of snapshot.workloads) {
      const key = w.member_id || w.username || 'unmapped';
      const cur = map.get(key) ?? {
        member: w.username || w.member_id || 'Chưa ánh xạ',
        groupId: w.group_id,
        count: 0,
        gpuCount: 0,
        vramBytes: 0,
        rssBytes: 0
      };
      cur.count += 1;
      if (w.gpu_associated) cur.gpuCount += 1;
      cur.vramBytes += w.vram.value_bytes ?? 0;
      cur.rssBytes += w.memory.value_bytes ?? 0;
      map.set(key, cur);
    }
    return Array.from(map.values()).toSorted(
      (a, b) => b.vramBytes - a.vramBytes || b.count - a.count
    );
  }, [snapshot.workloads]);

  if (error) {
    return (
      <Card className='border-destructive/40 bg-destructive/5'>
        <CardContent className='p-4 text-xs text-destructive'>
          Không thể tải dữ liệu phân tích lịch sử: {error.message}
        </CardContent>
      </Card>
    );
  }

  if (isLoading && !analytics) {
    return (
      <div className='flex items-center gap-2 rounded-lg border p-6 text-xs text-muted-foreground'>
        <Icons.spinner className='size-4 animate-spin' />
        Đang truy vấn chuỗi telemetry lịch sử (chỉ đọc từ SQLite)...
      </div>
    );
  }

  const capabilitiesEntries = Object.entries(analytics?.capabilities ?? {});

  return (
    <div className='space-y-4'>
      {/* Historical Time Series C05-C11 (Promoted to top of Analytics tab) */}
      <div className='grid grid-cols-1 gap-4 lg:grid-cols-2'>
        {metricGroups.map((g) => (
          <AnalyticsSeriesCard key={g.title} group={g} />
        ))}
      </div>

      {/* Explicit Capabilities Contract Matrix */}
      <Card>
        <CardHeader className='pb-2'>
          <CardTitle className='text-sm font-semibold'>
            Khả năng Dữ liệu Lịch sử (Capability Disclosure)
          </CardTitle>
          <CardDescription className='text-xs'>
            Mọi chỉ số chưa được thu thập ở mức tiến trình hoặc nhóm tại thời điểm lịch sử được đánh
            dấu tường minh UNSUPPORTED / DEFERRED
          </CardDescription>
        </CardHeader>
        <CardContent className='grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3'>
          {capabilitiesEntries.map(([key, cap]: [string, CapabilityEntry]) => (
            <div key={key} className='rounded-md border p-2.5 text-xs space-y-1'>
              <div className='flex items-center justify-between gap-2'>
                <span className='font-mono font-semibold text-foreground'>{key}</span>
                <Badge variant={qualityBadgeVariant(cap.status)} className='text-xs'>
                  {cap.status}
                </Badge>
              </div>
              <p className='text-muted-foreground leading-snug'>{cap.reason}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* T02 + T03 + T04 */}
      <div className='grid grid-cols-1 gap-4 lg:grid-cols-2'>
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>
              Tổng hợp theo Thành viên hiện tại (T02)
            </CardTitle>
            <CardDescription className='text-xs'>
              Tính trực tiếp trên snapshot đang quan sát (RSS cộng dồn có thể trùng lặp bộ nhớ chia
              sẻ)
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className='text-xs'>Thành viên</TableHead>
                  <TableHead className='text-xs'>Nhóm (hiện tại)</TableHead>
                  <TableHead className='text-xs text-right'>Workloads</TableHead>
                  <TableHead className='text-xs text-right'>GPU</TableHead>
                  <TableHead className='text-xs text-right'>VRAM</TableHead>
                  <TableHead className='text-xs text-right'>Tree RSS</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {memberSummaryRows.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={6}
                      className='text-center text-xs text-muted-foreground py-6'
                    >
                      Không có workload đang chạy trong bộ lọc hiện tại.
                    </TableCell>
                  </TableRow>
                ) : (
                  memberSummaryRows.map((row) => (
                    <TableRow key={row.member} className='text-xs'>
                      <TableCell className='font-medium'>{row.member}</TableCell>
                      <TableCell className='text-muted-foreground'>{row.groupId ?? '—'}</TableCell>
                      <TableCell className='text-right tabular-nums'>{row.count}</TableCell>
                      <TableCell className='text-right tabular-nums'>{row.gpuCount}</TableCell>
                      <TableCell className='text-right tabular-nums'>
                        {formatBytesGiB(row.vramBytes)}
                      </TableCell>
                      <TableCell className='text-right tabular-nums'>
                        {formatBytesGiB(row.rssBytes)}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>
              Tình trạng Node & GPU Chi tiết (T04)
            </CardTitle>
            <CardDescription className='text-xs'>
              Thông tin thiết bị trong cửa sổ quan sát hiện tại (Nhóm nghiên cứu lịch sử T03:
              UNSUPPORTED)
            </CardDescription>
          </CardHeader>
          <CardContent className='space-y-3'>
            <div className='rounded-md border border-dashed bg-muted/20 p-2.5 text-xs text-muted-foreground'>
              <span className='font-semibold text-foreground'>
                Quy chiếu Nhóm nghiên cứu lịch sử (T03):{' '}
              </span>
              UNSUPPORTED — Telemetry lịch sử không lưu group_id tại thời điểm chạy, hệ thống không
              suy diễn nhóm quá khứ từ hồ sơ hiện tại.
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className='text-xs'>Node / GPU</TableHead>
                  <TableHead className='text-xs'>Trạng thái</TableHead>
                  <TableHead className='text-xs text-right'>Util %</TableHead>
                  <TableHead className='text-xs text-right'>VRAM</TableHead>
                  <TableHead className='text-xs text-right'>Quan sát lúc (ICT)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {snapshot.gpus.map((g) => (
                  <TableRow key={g.gpu_uuid} className='text-xs'>
                    <TableCell className='font-mono'>
                      {g.node_id} / {g.gpu_uuid}
                    </TableCell>
                    <TableCell>
                      <Badge variant={qualityBadgeVariant(g.quality)} className='text-xs'>
                        {g.quality}
                      </Badge>
                    </TableCell>
                    <TableCell className='text-right tabular-nums'>
                      {formatPct(g.utilization_percent)}
                    </TableCell>
                    <TableCell className='text-right tabular-nums'>
                      {formatBytesGiB(g.vram_used_bytes)} / {formatBytesGiB(g.vram_capacity_bytes)}
                    </TableCell>
                    <TableCell className='text-right text-muted-foreground'>
                      {formatIctTime(g.observed_at)}
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

// ---------- History & Events Tab Panels (C12-C14, T05-T06) ----------

interface WorkloadHistoryAndEventsPanelsProps {
  history: WorkloadHistoryEnvelope | undefined;
  events: WorkloadEventsEnvelope | undefined;
  isLoadingHistory: boolean;
  isLoadingEvents: boolean;
  historyCursor: string | null;
  eventsCursor: string | null;
  onHistoryNextCursor: (next: string) => void;
  onHistoryResetCursor: () => void;
  onEventsNextCursor: (next: string) => void;
  onEventsResetCursor: () => void;
}

const stateDistConfig = {
  count: { label: 'Số mục', color: 'var(--chart-1, #2563b8)' }
} satisfies ChartConfig;

const durationDistConfig = {
  count: { label: 'Số mục', color: 'var(--chart-2, #178e83)' }
} satisfies ChartConfig;

const ICT_WEEKDAYS = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];

export function WorkloadHistoryAndEventsPanels({
  history,
  events,
  isLoadingHistory,
  isLoadingEvents,
  historyCursor,
  eventsCursor,
  onHistoryNextCursor,
  onHistoryResetCursor,
  onEventsNextCursor,
  onEventsResetCursor
}: WorkloadHistoryAndEventsPanelsProps) {
  const [subView, setSubView] = useState<'workloads' | 'events'>('workloads');

  const stateCounts = useMemo(() => {
    const items = history?.items ?? [];
    const map = new Map<string, number>();
    for (const item of items) {
      map.set(item.state, (map.get(item.state) ?? 0) + 1);
    }
    return [
      { state: 'OBSERVED', count: map.get('OBSERVED') ?? 0, fill: '#2563b8' },
      { state: 'MISSING_UNCONFIRMED', count: map.get('MISSING_UNCONFIRMED') ?? 0, fill: '#b77919' },
      { state: 'EXIT_CONFIRMED', count: map.get('EXIT_CONFIRMED') ?? 0, fill: '#178e83' },
      { state: 'STALE', count: map.get('STALE') ?? 0, fill: '#64748b' }
    ];
  }, [history?.items]);

  const durationBuckets = useMemo(() => {
    const items = history?.items ?? [];
    const counts = { '<15m': 0, '15m-1h': 0, '1h-6h': 0, '>6h': 0, 'Không rõ': 0 };
    for (const item of items) {
      const s = item.observed_seconds;
      if (s === null || s === undefined) counts['Không rõ'] += 1;
      else if (s < 900) counts['<15m'] += 1;
      else if (s < 3600) counts['15m-1h'] += 1;
      else if (s < 21600) counts['1h-6h'] += 1;
      else counts['>6h'] += 1;
    }
    return Object.entries(counts).map(([bucket, count]) => ({ bucket, count }));
  }, [history?.items]);

  // C14: 7x24 ICT heatmap constructed cleanly from observed history & audit timestamps
  const heatmapGrid = useMemo(() => {
    const counts: number[][] = Array.from({ length: 7 }, () => Array<number>(24).fill(0));
    const timestamps: string[] = [];
    for (const h of history?.items ?? []) {
      if (h.first_seen_at) timestamps.push(h.first_seen_at);
    }
    for (const ev of events?.items ?? []) {
      timestamps.push(ev.occurred_at);
    }
    let maxVal = 0;
    for (const iso of timestamps) {
      const dt = new Date(iso);
      if (Number.isNaN(dt.getTime())) continue;
      // Convert to ICT (UTC+7 fixed offset, no DST in Vietnam)
      const ict = new Date(dt.getTime() + 7 * 3600 * 1000);
      const jsDay = ict.getUTCDay(); // 0=Sun..6=Sat -> Mon=0..Sun=6
      const rowIdx = jsDay === 0 ? 6 : jsDay - 1;
      const hr = ict.getUTCHours();
      counts[rowIdx][hr] += 1;
      if (counts[rowIdx][hr] > maxVal) maxVal = counts[rowIdx][hr];
    }
    return { counts, maxVal };
  }, [history?.items, events?.items]);

  return (
    <div className='space-y-4'>
      {/* C12 + C13 + C14 */}
      <div className='grid grid-cols-1 gap-4 lg:grid-cols-3'>
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>Thời lượng Quan sát (C12)</CardTitle>
            <CardDescription className='text-xs'>
              Phân bổ thời gian quan sát trong trang hiện tại
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={durationDistConfig} className='h-44 w-full'>
              <BarChart data={durationBuckets}>
                <CartesianGrid strokeDasharray='3 3' vertical={false} />
                <XAxis dataKey='bucket' tick={{ fontSize: 12 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar dataKey='count' fill='#178e83' radius={[4, 4, 0, 0]} />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>
              Trạng thái Kết thúc Lịch sử (C13)
            </CardTitle>
            <CardDescription className='text-xs'>
              Phân biệt MISSING_UNCONFIRMED (mất mẫu/offline) với EXIT_CONFIRMED
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={stateDistConfig} className='h-44 w-full'>
              <BarChart
                data={stateCounts}
                layout='vertical'
                margin={{ left: 14, right: 20, top: 4, bottom: 4 }}
              >
                <CartesianGrid strokeDasharray='3 3' horizontal={false} />
                <XAxis type='number' allowDecimals={false} tick={{ fontSize: 12 }} />
                <YAxis dataKey='state' type='category' width={185} tick={{ fontSize: 12 }} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar dataKey='count' fill='#2563b8' radius={[0, 4, 4, 0]} />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>Mật độ Hoạt động 7×24 ICT (C14)</CardTitle>
            <CardDescription className='text-xs'>
              Theo múi giờ Asia/Ho_Chi_Minh (GMT+7)
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className='space-y-1 overflow-x-auto'>
              {ICT_WEEKDAYS.map((dayLabel, rIdx) => (
                <div key={dayLabel} className='flex items-center gap-1'>
                  <span className='w-6 text-xs text-muted-foreground shrink-0'>{dayLabel}</span>
                  <div className='grid grid-cols-24 gap-0.5 flex-1 min-w-[240px]'>
                    {heatmapGrid.counts[rIdx].map((cnt, hIdx) => {
                      const alpha =
                        cnt === 0 || heatmapGrid.maxVal === 0
                          ? 0
                          : Math.max(0.25, cnt / heatmapGrid.maxVal);
                      return (
                        <div
                          key={hIdx}
                          title={`${dayLabel} ${String(hIdx).padStart(2, '0')}:00 ICT — ${cnt} mục`}
                          className='h-3.5 rounded-[2px] border border-border/40 bg-muted/40'
                          style={
                            alpha > 0
                              ? { backgroundColor: `rgba(37, 99, 184, ${alpha.toFixed(2)})` }
                              : undefined
                          }
                        />
                      );
                    })}
                  </div>
                </div>
              ))}
              <div className='flex justify-between pl-7 text-xs text-muted-foreground pt-1'>
                <span>00h</span>
                <span>06h</span>
                <span>12h</span>
                <span>18h</span>
                <span>23h</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Sub-navigation between T05 Historical Workloads & T06 Events */}
      <div className='flex items-center justify-between flex-wrap gap-2'>
        <div className='flex items-center gap-2'>
          <Button
            type='button'
            size='sm'
            variant={subView === 'workloads' ? 'default' : 'outline'}
            onClick={() => setSubView('workloads')}
          >
            Lịch sử Workload (T05)
          </Button>
          <Button
            type='button'
            size='sm'
            variant={subView === 'events' ? 'default' : 'outline'}
            onClick={() => setSubView('events')}
          >
            Nhật ký Sự kiện & Audit (T06)
          </Button>
        </div>
        <Badge variant='outline' className='text-xs'>
          <Icons.lock className='size-3.5 mr-1' />
          Bản ghi lịch sử: Chỉ đọc (actionable: false)
        </Badge>
      </div>

      {subView === 'workloads' ? (
        <Card>
          <CardHeader className='pb-2 flex flex-row items-center justify-between gap-2'>
            <div>
              <CardTitle className='text-sm font-semibold'>
                Danh sách Workload Quan sát Lịch sử (T05)
              </CardTitle>
              <CardDescription className='text-xs'>
                Capability: {history?.capability ?? 'UNKNOWN'} • Không cho phép thao tác tín hiệu
                trực tiếp trên dữ liệu quá khứ
              </CardDescription>
            </div>
            <div className='flex items-center gap-2'>
              {historyCursor && (
                <Button type='button' size='sm' variant='outline' onClick={onHistoryResetCursor}>
                  Đầu danh sách
                </Button>
              )}
              {history?.next_cursor && (
                <Button
                  type='button'
                  size='sm'
                  variant='outline'
                  onClick={() => onHistoryNextCursor(history.next_cursor as string)}
                >
                  Trang sau <Icons.chevronRight className='size-4 ml-1' />
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {isLoadingHistory && !history ? (
              <div className='py-8 text-center text-xs text-muted-foreground'>
                Đang tải lịch sử...
              </div>
            ) : (
              <div className='overflow-x-auto'>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className='px-2 py-1.5 text-xs whitespace-nowrap'>
                        Workload ID
                      </TableHead>
                      <TableHead className='px-2 py-1.5 text-xs whitespace-nowrap'>Node</TableHead>
                      <TableHead className='px-2 py-1.5 text-xs whitespace-nowrap'>
                        Thành viên
                      </TableHead>
                      <TableHead className='px-2 py-1.5 text-xs whitespace-nowrap'>
                        Lần đầu thấy (ICT)
                      </TableHead>
                      <TableHead className='px-2 py-1.5 text-xs whitespace-nowrap'>
                        Lần cuối thấy (ICT)
                      </TableHead>
                      <TableHead className='px-2 py-1.5 text-xs text-right whitespace-nowrap'>
                        Thời lượng
                      </TableHead>
                      <TableHead className='px-2 py-1.5 text-xs whitespace-nowrap'>
                        Trạng thái
                      </TableHead>
                      <TableHead className='px-2 py-1.5 text-xs'>Bằng chứng</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(history?.items ?? []).length === 0 ? (
                      <TableRow>
                        <TableCell
                          colSpan={8}
                          className='text-center text-xs text-muted-foreground py-8'
                        >
                          Không có lịch sử workload trong khoảng thời gian đã chọn.
                        </TableCell>
                      </TableRow>
                    ) : (
                      (history?.items ?? []).map((item) => (
                        <TableRow key={item.id} className='text-xs'>
                          <TableCell className='px-2 py-1.5 font-mono whitespace-nowrap'>
                            {item.id}
                          </TableCell>
                          <TableCell className='px-2 py-1.5 whitespace-nowrap'>
                            {item.node_id}
                          </TableCell>
                          <TableCell className='px-2 py-1.5 whitespace-nowrap'>
                            {item.member_id ?? '—'}
                          </TableCell>
                          <TableCell className='px-2 py-1.5 text-muted-foreground whitespace-nowrap'>
                            {formatIctTime(item.first_seen_at)}
                          </TableCell>
                          <TableCell className='px-2 py-1.5 text-muted-foreground whitespace-nowrap'>
                            {formatIctTime(item.last_seen_at)}
                          </TableCell>
                          <TableCell className='px-2 py-1.5 text-right tabular-nums whitespace-nowrap'>
                            {formatDurationSeconds(item.observed_seconds)}
                          </TableCell>
                          <TableCell className='px-2 py-1.5 whitespace-nowrap'>
                            <Badge variant={qualityBadgeVariant(item.state)} className='text-xs'>
                              {item.state}
                            </Badge>
                          </TableCell>
                          <TableCell
                            className='px-2 py-1.5 text-muted-foreground min-w-[140px] max-w-[200px] whitespace-normal break-words leading-snug'
                            title={item.evidence}
                          >
                            {item.evidence}
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader className='pb-2 flex flex-row items-center justify-between gap-2'>
            <div>
              <CardTitle className='text-sm font-semibold'>
                Nhật ký Sự kiện Hệ thống & Thao tác Tiến trình (T06)
              </CardTitle>
              <CardDescription className='text-xs'>
                Mọi thao tác tiến trình và cảnh báo có kèm correlation_id để đối chiếu
              </CardDescription>
            </div>
            <div className='flex items-center gap-2'>
              {eventsCursor && (
                <Button type='button' size='sm' variant='outline' onClick={onEventsResetCursor}>
                  Đầu danh sách
                </Button>
              )}
              {events?.next_cursor && (
                <Button
                  type='button'
                  size='sm'
                  variant='outline'
                  onClick={() => onEventsNextCursor(events.next_cursor as string)}
                >
                  Trang sau <Icons.chevronRight className='size-4 ml-1' />
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {isLoadingEvents && !events ? (
              <div className='py-8 text-center text-xs text-muted-foreground'>
                Đang tải sự kiện...
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className='text-xs'>Thời gian (ICT)</TableHead>
                    <TableHead className='text-xs'>Loại sự kiện</TableHead>
                    <TableHead className='text-xs'>Đối tượng</TableHead>
                    <TableHead className='text-xs'>Tác nhân</TableHead>
                    <TableHead className='text-xs'>Kết quả</TableHead>
                    <TableHead className='text-xs'>Nội dung</TableHead>
                    <TableHead className='text-xs'>Correlation ID</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(events?.items ?? []).length === 0 ? (
                    <TableRow>
                      <TableCell
                        colSpan={7}
                        className='text-center text-xs text-muted-foreground py-8'
                      >
                        Chưa ghi nhận sự kiện thao tác hoặc bất thường trong khoảng thời gian đã
                        chọn.
                      </TableCell>
                    </TableRow>
                  ) : (
                    (events?.items ?? []).map((ev) => (
                      <TableRow key={ev.id} className='text-xs'>
                        <TableCell className='text-muted-foreground whitespace-nowrap'>
                          {formatIctTime(ev.occurred_at)}
                        </TableCell>
                        <TableCell className='font-mono'>{ev.event_type}</TableCell>
                        <TableCell className='font-mono'>{ev.resource_id}</TableCell>
                        <TableCell>{ev.actor_id ?? '—'}</TableCell>
                        <TableCell>
                          <Badge variant={qualityBadgeVariant(ev.result)} className='text-xs'>
                            {ev.result}
                          </Badge>
                        </TableCell>
                        <TableCell className='max-w-[260px] truncate' title={ev.message}>
                          {ev.message}
                        </TableCell>
                        <TableCell className='font-mono text-muted-foreground'>
                          {ev.correlation_id ?? '—'}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export type { SnapshotWorkloadItem };
