'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { parseAsStringEnum, parseAsString, useQueryStates } from 'nuqs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { Icons } from '@/components/icons';
import { useAuthSession } from '@/hooks/use-auth-session';
import type { HistoryRangePreset, ScopeBase, WorkloadTabId } from '@/features/jobs/api/types';
import { formatEntityShortLabel, formatIctTime } from '@/features/jobs/api/service';
import { workloadQueries } from '@/features/jobs/api/queries';
import { WorkloadKpiGrid } from '@/features/jobs/components/workload-kpi-grid';
import {
  WorkloadAnalyticsPanels,
  WorkloadHistoryAndEventsPanels,
  WorkloadOverviewPanels
} from '@/features/jobs/components/workload-charts';
import {
  WorkloadDetailDrawer,
  WorkloadLiveTable,
  WorkloadOverviewPreviewTable
} from '@/features/jobs/components/workload-table-and-drawer';

const v31SearchParsers = {
  tab: parseAsStringEnum<WorkloadTabId>(['overview', 'live', 'analytics', 'history']).withDefault(
    'overview'
  ),
  node_id: parseAsString.withDefault(''),
  group_id: parseAsString.withDefault(''),
  member_id: parseAsString.withDefault(''),
  gpu_uuid: parseAsString.withDefault(''),
  range: parseAsStringEnum<HistoryRangePreset>(['1h', '6h', '24h', '7d', '30d']).withDefault('24h')
};

export function WorkloadIntelligenceV31View() {
  const { user, isAdmin } = useAuthSession();
  const queryClient = useQueryClient();
  const principalKey = `${user?.username || 'anon'}:${isAdmin ? 'admin' : 'user'}`;
  const prevPrincipalRef = useRef<string>(principalKey);

  useEffect(() => {
    if (prevPrincipalRef.current !== principalKey) {
      queryClient.removeQueries({
        queryKey: workloadQueries.principalScope(prevPrincipalRef.current)
      });
      prevPrincipalRef.current = principalKey;
    }
  }, [principalKey, queryClient]);

  const [urlState, setUrlState] = useQueryStates(v31SearchParsers, { history: 'replace' });
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [selectedWorkloadId, setSelectedWorkloadId] = useState<string | null>(null);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [eventsCursor, setEventsCursor] = useState<string | null>(null);

  const scope: ScopeBase = useMemo(
    () => ({
      node_id: urlState.node_id || null,
      gpu_uuid: urlState.gpu_uuid || null,
      member_id: urlState.member_id || null,
      group_id: urlState.group_id || null
    }),
    [urlState.node_id, urlState.gpu_uuid, urlState.member_id, urlState.group_id]
  );

  // Single authoritative snapshot query (polled cleanly via TanStack Query refetchInterval)
  const snapshotQuery = useQuery({
    ...workloadQueries.snapshot(principalKey, scope),
    refetchInterval: autoRefresh ? 10_000 : false
  });

  // Tab-gated analytics/history queries — never fire unless active tab needs them
  const analyticsQuery = useQuery({
    ...workloadQueries.analytics(principalKey, scope, urlState.range),
    enabled: urlState.tab === 'analytics'
  });

  const historyQuery = useQuery({
    ...workloadQueries.history(principalKey, scope, urlState.range, historyCursor),
    enabled: urlState.tab === 'history'
  });

  const eventsQuery = useQuery({
    ...workloadQueries.events(principalKey, scope, urlState.range, eventsCursor),
    enabled: urlState.tab === 'history'
  });

  const snapshot = snapshotQuery.data;

  // Cascading filter options from current snapshot
  const nodeOptions = useMemo(() => {
    const ids = new Set<string>();
    for (const n of snapshot?.nodes ?? []) ids.add(n.id);
    return Array.from(ids).toSorted();
  }, [snapshot?.nodes]);

  const gpuOptions = useMemo(() => {
    const list = snapshot?.gpus ?? [];
    return list
      .filter((g) => !urlState.node_id || g.node_id === urlState.node_id)
      .toSorted((a, b) => a.gpu_uuid.localeCompare(b.gpu_uuid));
  }, [snapshot?.gpus, urlState.node_id]);

  const groupOptions = useMemo(() => {
    const ids = new Set<string>();
    for (const w of snapshot?.workloads ?? []) {
      if (w.group_id) ids.add(w.group_id);
    }
    return Array.from(ids).toSorted();
  }, [snapshot?.workloads]);

  const memberOptions = useMemo(() => {
    const map = new Map<string, string>();
    for (const w of snapshot?.workloads ?? []) {
      if (urlState.group_id && w.group_id !== urlState.group_id) continue;
      const key = w.member_id || w.username;
      if (key) map.set(key, w.username || key);
    }
    return Array.from(map.entries()).toSorted((a, b) => a[1].localeCompare(b[1]));
  }, [snapshot?.workloads, urlState.group_id]);

  const hasScopeFilter = Boolean(
    urlState.node_id || urlState.gpu_uuid || urlState.member_id || urlState.group_id
  );
  const hasMemberOrGroupFilter = Boolean(urlState.member_id || urlState.group_id);

  const handleManualRefresh = () => {
    void snapshotQuery.refetch();
    if (urlState.tab === 'analytics') void analyticsQuery.refetch();
    if (urlState.tab === 'history') {
      void historyQuery.refetch();
      void eventsQuery.refetch();
    }
  };

  const handleResetFilters = () => {
    setHistoryCursor(null);
    setEventsCursor(null);
    void setUrlState({
      node_id: '',
      group_id: '',
      member_id: '',
      gpu_uuid: '',
      range: '24h'
    });
  };

  const qStatus = snapshot?.quality.status ?? 'UNKNOWN';
  const showDegradedBanner =
    Boolean(snapshot) &&
    (qStatus === 'PARTIAL' ||
      qStatus === 'STALE' ||
      qStatus === 'OFFLINE' ||
      (snapshot?.warnings.length ?? 0) > 0);

  return (
    <div className='space-y-4' data-testid='workload-v31-root'>
      {/* Header Control Bar */}
      <header className='flex flex-wrap items-start justify-between gap-3'>
        <div className='space-y-1'>
          <div className='text-xs font-semibold uppercase tracking-wider text-primary'>
            COMPUTE OPERATIONS
          </div>
          <h1 className='text-2xl font-bold tracking-tight text-foreground'>
            Tác vụ &amp; Tiến trình
          </h1>
          <p className='text-xs text-muted-foreground'>
            Giám sát workload và phân tích tài nguyên theo chuẩn dữ liệu V3.1
          </p>
        </div>

        <div className='flex flex-wrap items-center gap-2'>
          <Button
            type='button'
            size='sm'
            variant={autoRefresh ? 'secondary' : 'outline'}
            onClick={() => setAutoRefresh((v) => !v)}
          >
            {autoRefresh ? (
              <>
                <Icons.pause className='size-3.5 mr-1.5 text-emerald-600' />
                Tự cập nhật 10s
              </>
            ) : (
              <>
                <Icons.play className='size-3.5 mr-1.5' />
                Đã tạm dừng
              </>
            )}
          </Button>

          <div
            className='flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground'
            aria-live='polite'
          >
            {snapshotQuery.isFetching ? (
              <Icons.spinner className='size-3.5 animate-spin text-primary' />
            ) : (
              <Icons.clock className='size-3.5' />
            )}
            <span>Quan sát (ICT): {formatIctTime(snapshot?.observed_at)}</span>
          </div>

          <Button
            type='button'
            size='sm'
            variant='outline'
            onClick={handleManualRefresh}
            disabled={snapshotQuery.isFetching}
          >
            <Icons.refresh className='size-3.5 mr-1.5' />
            Làm mới
          </Button>
        </div>
      </header>

      {/* Error / Degraded Partial Coverage Banner */}
      {snapshotQuery.isError && (
        <div
          role='alert'
          className='rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-xs text-destructive flex items-center gap-2'
        >
          <Icons.alertCircle className='size-4 shrink-0' />
          <span>
            Không thể tải Snapshot Workload V3.1:{' '}
            {snapshotQuery.error instanceof Error
              ? snapshotQuery.error.message
              : 'Lỗi không xác định'}
          </span>
        </div>
      )}

      {showDegradedBanner && snapshot && (
        <div
          role='status'
          className='rounded-lg border border-amber-500/50 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-900 dark:text-amber-200 flex flex-wrap items-center justify-between gap-2'
        >
          <div className='flex items-start gap-2'>
            <Icons.warning className='size-4 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400' />
            <div className='space-y-0.5'>
              <div className='font-semibold'>
                Dữ liệu chưa đầy đủ ({snapshot.quality.status}) — Node tươi{' '}
                {snapshot.quality.fresh_nodes}/{snapshot.quality.expected_nodes ?? '—'} • GPU
                telemetry {snapshot.quality.fresh_gpus}/{snapshot.quality.expected_gpus ?? '—'}
              </div>
              {snapshot.warnings.map((w) => (
                <div key={w.code}>
                  [{w.code}] {w.message}
                </div>
              ))}
            </div>
          </div>
          {snapshot.quality.last_success_at && (
            <Badge variant='outline' className='text-xs'>
              Lần cuối thành công: {formatIctTime(snapshot.quality.last_success_at)}
            </Badge>
          )}
        </div>
      )}

      {/* Cascading Global Filter Bar */}
      <section
        aria-label='Bộ lọc phạm vi giám sát'
        className='flex flex-wrap items-center gap-2.5 rounded-lg border bg-card p-3'
      >
        <div className='flex items-center gap-1.5 text-xs font-medium text-muted-foreground mr-1'>
          <Icons.adjustments className='size-4' />
          Bộ lọc:
        </div>

        <NativeSelect
          id='v31-filter-node'
          name='nodeFilter'
          size='sm'
          aria-label='Lọc theo máy chủ'
          value={urlState.node_id}
          onChange={(e) => {
            const nextNode = e.target.value;
            setHistoryCursor(null);
            setEventsCursor(null);
            void setUrlState({ node_id: nextNode, gpu_uuid: '' });
          }}
        >
          <NativeSelectOption value=''>Tất cả máy chủ</NativeSelectOption>
          {nodeOptions.map((n) => (
            <NativeSelectOption key={n} value={n}>
              Máy chủ: {n}
            </NativeSelectOption>
          ))}
        </NativeSelect>

        <NativeSelect
          id='v31-filter-group'
          name='groupFilter'
          size='sm'
          aria-label='Lọc theo nhóm nghiên cứu'
          value={urlState.group_id}
          onChange={(e) => {
            const nextGroup = e.target.value;
            setHistoryCursor(null);
            setEventsCursor(null);
            void setUrlState({ group_id: nextGroup, member_id: '' });
          }}
        >
          <NativeSelectOption value=''>Tất cả nhóm</NativeSelectOption>
          {groupOptions.map((g) => (
            <NativeSelectOption key={g} value={g}>
              Nhóm: {g}
            </NativeSelectOption>
          ))}
        </NativeSelect>

        <NativeSelect
          id='v31-filter-member'
          name='memberFilter'
          size='sm'
          aria-label='Lọc theo thành viên'
          value={urlState.member_id}
          onChange={(e) => {
            setHistoryCursor(null);
            setEventsCursor(null);
            void setUrlState({ member_id: e.target.value });
          }}
        >
          <NativeSelectOption value=''>Tất cả thành viên</NativeSelectOption>
          {memberOptions.map(([id, label]) => (
            <NativeSelectOption key={id} value={id}>
              {label}
            </NativeSelectOption>
          ))}
        </NativeSelect>

        <NativeSelect
          id='v31-filter-gpu'
          name='gpuFilter'
          size='sm'
          aria-label='Lọc theo GPU'
          value={urlState.gpu_uuid}
          onChange={(e) => {
            setHistoryCursor(null);
            setEventsCursor(null);
            void setUrlState({ gpu_uuid: e.target.value });
          }}
        >
          <NativeSelectOption value=''>Tất cả GPU</NativeSelectOption>
          {gpuOptions.map((g) => (
            <NativeSelectOption key={g.gpu_uuid} value={g.gpu_uuid}>
              {formatEntityShortLabel(g.gpu_uuid, g.node_id)}
            </NativeSelectOption>
          ))}
        </NativeSelect>

        <NativeSelect
          id='v31-filter-range'
          name='rangeFilter'
          size='sm'
          aria-label='Khoảng thời gian lịch sử'
          value={urlState.range}
          onChange={(e) => {
            setHistoryCursor(null);
            setEventsCursor(null);
            void setUrlState({ range: e.target.value as HistoryRangePreset });
          }}
        >
          <NativeSelectOption value='1h'>Lịch sử: 1 giờ</NativeSelectOption>
          <NativeSelectOption value='6h'>Lịch sử: 6 giờ</NativeSelectOption>
          <NativeSelectOption value='24h'>Lịch sử: 24 giờ</NativeSelectOption>
          <NativeSelectOption value='7d'>Lịch sử: 7 ngày</NativeSelectOption>
          <NativeSelectOption value='30d'>Lịch sử: 30 ngày</NativeSelectOption>
        </NativeSelect>

        {(hasScopeFilter || urlState.range !== '24h') && (
          <Button type='button' size='sm' variant='ghost' onClick={handleResetFilters}>
            Đặt lại
          </Button>
        )}
      </section>

      {/* 6 Primary KPI Cards */}
      <WorkloadKpiGrid
        kpis={snapshot?.kpis}
        quality={snapshot?.quality}
        deviceVram={snapshot?.device_vram}
        hasScopeFilter={hasScopeFilter}
        hasMemberOrGroupFilter={hasMemberOrGroupFilter}
        isLoading={snapshotQuery.isLoading}
      />

      {/* 4 Functional Tabs */}
      <Tabs
        value={urlState.tab}
        onValueChange={(nextTab) => {
          void setUrlState({ tab: nextTab as WorkloadTabId });
        }}
        className='w-full'
      >
        <TabsList className='flex flex-wrap h-auto w-full justify-start gap-1 p-1'>
          <TabsTrigger value='overview' className='text-xs py-2 px-3'>
            <Icons.dashboard className='size-3.5 mr-1.5' />
            Tổng quan &amp; Điều hành
          </TabsTrigger>
          <TabsTrigger value='live' className='text-xs py-2 px-3'>
            <Icons.activity className='size-3.5 mr-1.5' />
            Tác vụ trực tiếp ({snapshot?.workloads.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value='analytics' className='text-xs py-2 px-3'>
            <Icons.trendingUp className='size-3.5 mr-1.5' />
            Phân tích tài nguyên
          </TabsTrigger>
          <TabsTrigger value='history' className='text-xs py-2 px-3'>
            <Icons.history className='size-3.5 mr-1.5' />
            Lịch sử &amp; Sự kiện
          </TabsTrigger>
        </TabsList>

        <TabsContent value='overview' className='mt-4 space-y-4'>
          {snapshot ? (
            <>
              <WorkloadOverviewPanels snapshot={snapshot} />
              <WorkloadOverviewPreviewTable
                workloads={snapshot.workloads}
                onSelectWorkload={(id) => setSelectedWorkloadId(id)}
                onViewAll={() => void setUrlState({ tab: 'live' })}
              />
            </>
          ) : null}
        </TabsContent>

        <TabsContent value='live' className='mt-4'>
          <WorkloadLiveTable
            workloads={snapshot?.workloads ?? []}
            scope={scope}
            isAdmin={isAdmin}
            autoRefresh={autoRefresh}
            onSelectWorkload={(id) => setSelectedWorkloadId(id)}
          />
        </TabsContent>

        <TabsContent value='analytics' className='mt-4'>
          {snapshot && (
            <WorkloadAnalyticsPanels
              analytics={analyticsQuery.data}
              snapshot={snapshot}
              isLoading={analyticsQuery.isLoading}
              error={analyticsQuery.error instanceof Error ? analyticsQuery.error : null}
            />
          )}
        </TabsContent>

        <TabsContent value='history' className='mt-4'>
          <WorkloadHistoryAndEventsPanels
            history={historyQuery.data}
            events={eventsQuery.data}
            isLoadingHistory={historyQuery.isLoading}
            isLoadingEvents={eventsQuery.isLoading}
            historyCursor={historyCursor}
            eventsCursor={eventsCursor}
            onHistoryNextCursor={(next) => setHistoryCursor(next)}
            onHistoryResetCursor={() => setHistoryCursor(null)}
            onEventsNextCursor={(next) => setEventsCursor(next)}
            onEventsResetCursor={() => setEventsCursor(null)}
          />
        </TabsContent>
      </Tabs>

      <WorkloadDetailDrawer
        principalKey={principalKey}
        workloadId={selectedWorkloadId}
        snapshotWorkloads={snapshot?.workloads ?? []}
        isAdmin={isAdmin}
        onClose={() => setSelectedWorkloadId(null)}
        onActionCompleted={() => {
          void snapshotQuery.refetch();
        }}
      />
    </div>
  );
}
