'use client';

import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import type { DeviceVramMeta, SnapshotKpis, SnapshotQuality } from '@/features/jobs/api/types';
import { formatBytesGiB } from '@/features/jobs/api/service';

interface WorkloadKpiGridProps {
  kpis: SnapshotKpis | undefined;
  quality: SnapshotQuality | undefined;
  deviceVram: DeviceVramMeta | undefined;
  hasScopeFilter: boolean;
  hasMemberOrGroupFilter: boolean;
  isLoading: boolean;
}

interface KpiCardItem {
  id: string;
  label: string;
  value: string;
  subtext: string;
  scopeBadge: string;
  tone: 'neutral' | 'good' | 'warn' | 'danger';
  icon: React.ReactNode;
}

const TONE_CLASSES: Record<KpiCardItem['tone'], string> = {
  neutral: 'border-border/80 bg-card',
  good: 'border-emerald-500/30 bg-emerald-500/5 dark:bg-emerald-950/15',
  warn: 'border-amber-500/40 bg-amber-500/5 dark:bg-amber-950/20',
  danger: 'border-red-500/40 bg-red-500/5 dark:bg-red-950/20'
};

export function WorkloadKpiGrid({
  kpis,
  quality,
  deviceVram,
  hasScopeFilter,
  hasMemberOrGroupFilter,
  isLoading
}: WorkloadKpiGridProps) {
  if (isLoading && !kpis) {
    return (
      <div className='grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6'>
        {Array.from({ length: 6 }).map((_, idx) => (
          <Card key={idx} className='animate-pulse border bg-card'>
            <CardContent className='p-4 space-y-2'>
              <div className='h-3 w-24 rounded bg-muted' />
              <div className='h-7 w-16 rounded bg-muted' />
              <div className='h-3 w-32 rounded bg-muted' />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  const scopeLabel = hasScopeFilter ? 'Theo bộ lọc' : 'Toàn cụm';
  const vramScopeLabel =
    deviceVram?.scope === 'DEVICE_WIDE'
      ? hasMemberOrGroupFilter
        ? 'Toàn GPU (không lọc user)'
        : 'Toàn GPU thiết bị'
      : scopeLabel;

  const isPartialSnapshot =
    quality?.status === 'PARTIAL' || quality?.status === 'STALE' || quality?.status === 'OFFLINE';

  const observedVal =
    kpis?.observed_workloads === null || kpis?.observed_workloads === undefined
      ? '—'
      : String(kpis.observed_workloads);
  const lastKnownNote =
    kpis?.last_known_workloads !== null &&
    kpis?.last_known_workloads !== undefined &&
    kpis.last_known_workloads !== kpis.observed_workloads
      ? `Lần biết cuối (node offline/stale): ${kpis.last_known_workloads}`
      : isPartialSnapshot
        ? 'Chỉ tính node còn tươi (FRESH)'
        : 'Tiến trình tính toán đang quan sát';

  const gpuAssocVal =
    kpis?.gpu_associated_workloads === null || kpis?.gpu_associated_workloads === undefined
      ? '—'
      : String(kpis.gpu_associated_workloads);
  const gpuUnknownCount = kpis?.gpu_association_unknown ?? 0;

  const membersVal =
    kpis?.observed_members === null || kpis?.observed_members === undefined
      ? '—'
      : String(kpis.observed_members);
  const unmappedCount = kpis?.unmapped_member_workloads ?? 0;

  const vramUsedFormatted = formatBytesGiB(kpis?.device_vram_used_bytes);
  const vramCapFormatted = formatBytesGiB(kpis?.device_vram_capacity_bytes);
  const vramDisplay =
    kpis?.device_vram_used_bytes === null || kpis?.device_vram_used_bytes === undefined
      ? '—'
      : `${vramUsedFormatted} / ${vramCapFormatted}`;

  const gpuFresh = kpis?.gpu_coverage.fresh ?? 0;
  const gpuExpected = kpis?.gpu_coverage.expected;
  const gpuCoverageVal =
    gpuExpected === null || gpuExpected === undefined
      ? `${gpuFresh} / —`
      : `${gpuFresh} / ${gpuExpected}`;
  const gpuCoverageMissing =
    gpuExpected !== null && gpuExpected !== undefined && gpuFresh < gpuExpected;

  const attentionCount = kpis?.attention_count;
  const attentionVal =
    attentionCount === null || attentionCount === undefined ? '—' : String(attentionCount);

  const reconStatus = deviceVram?.reconciliation ?? 'UNKNOWN';

  const items: KpiCardItem[] = [
    {
      id: 'observed_workloads',
      label: 'Workload quan sát',
      value: observedVal,
      subtext: lastKnownNote,
      scopeBadge: isPartialSnapshot ? `${scopeLabel} • Bán phần` : scopeLabel,
      tone: isPartialSnapshot ? 'warn' : 'neutral',
      icon: <Icons.activity className='size-4 text-primary' />
    },
    {
      id: 'gpu_associated',
      label: 'Tác vụ có GPU',
      value: gpuAssocVal,
      subtext:
        gpuUnknownCount > 0
          ? `${gpuUnknownCount} tác vụ chưa xác định GPU`
          : 'Gắn trực tiếp qua telemetry GPU',
      scopeBadge: scopeLabel,
      tone: gpuUnknownCount > 0 ? 'warn' : 'neutral',
      icon: <Icons.gpu className='size-4 text-emerald-600 dark:text-emerald-400' />
    },
    {
      id: 'observed_members',
      label: 'Thành viên hoạt động',
      value: membersVal,
      subtext:
        unmappedCount > 0
          ? `${unmappedCount} workload chưa ánh xạ thành viên`
          : 'Ánh xạ theo tài khoản đã xác minh',
      scopeBadge: scopeLabel,
      tone: unmappedCount > 0 ? 'warn' : 'neutral',
      icon: <Icons.users className='size-4 text-sky-600 dark:text-sky-400' />
    },
    {
      id: 'device_vram',
      label: 'VRAM thiết bị',
      value: vramDisplay,
      subtext:
        reconStatus === 'MISMATCH'
          ? 'Cảnh báo: tổng VRAM tiến trình vượt thiết bị'
          : reconStatus === 'CONSISTENT'
            ? 'Đối soát thiết bị: Nhất quán'
            : 'Nguồn: NVML / Go Node Agent',
      scopeBadge: vramScopeLabel,
      tone: reconStatus === 'MISMATCH' ? 'warn' : 'neutral',
      icon: <Icons.database className='size-4 text-indigo-600 dark:text-indigo-400' />
    },
    {
      id: 'gpu_coverage',
      label: 'GPU có telemetry',
      value: gpuCoverageVal,
      subtext: gpuCoverageMissing
        ? 'Một số GPU thiếu telemetry hoặc node offline'
        : 'Tất cả GPU kỳ vọng đang gửi mẫu tươi',
      scopeBadge: scopeLabel,
      tone: gpuCoverageMissing ? 'warn' : 'good',
      icon: <Icons.gauge className='size-4 text-teal-600 dark:text-teal-400' />
    },
    {
      id: 'attention_count',
      label: 'Cần chú ý',
      value: attentionVal,
      subtext:
        attentionCount && attentionCount > 0
          ? 'Node offline/stale, mismatch VRAM hoặc thiếu telemetry'
          : 'Không có bất thường telemetry',
      scopeBadge: 'Cảnh báo cụm',
      tone: attentionCount && attentionCount > 0 ? 'danger' : 'good',
      icon: <Icons.warning className='size-4 text-amber-600 dark:text-amber-400' />
    }
  ];

  return (
    <section
      aria-label='Chỉ số tổng quan Workload'
      className='grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6'
    >
      {items.map((item) => (
        <Card key={item.id} className={TONE_CLASSES[item.tone]}>
          <CardContent className='p-4 flex flex-col justify-between gap-2 h-full'>
            <div className='flex items-start justify-between gap-2'>
              <span className='text-xs font-medium text-muted-foreground leading-snug'>
                {item.label}
              </span>
              <span className='shrink-0'>{item.icon}</span>
            </div>
            <div className='text-2xl font-bold tracking-tight tabular-nums text-foreground'>
              {item.value}
            </div>
            <div className='space-y-1.5 pt-1 border-t border-border/50'>
              <p className='text-xs text-muted-foreground leading-snug line-clamp-2'>
                {item.subtext}
              </p>
              <Badge variant='outline' className='text-xs font-normal'>
                {item.scopeBadge}
              </Badge>
            </div>
          </CardContent>
        </Card>
      ))}
    </section>
  );
}
