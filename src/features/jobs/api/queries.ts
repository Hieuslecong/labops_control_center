import { keepPreviousData, mutationOptions, queryOptions } from '@tanstack/react-query';
import type { HistoryRangePreset, ScopeBase, V31ProcessActionRequest } from './types';
import {
  buildRangeWindow,
  executeV31ProcessAction,
  fetchWorkloadAnalytics,
  fetchWorkloadDetail,
  fetchWorkloadEvents,
  fetchWorkloadHistory,
  fetchWorkloadSnapshot
} from './service';

export const workloadQueries = {
  all: () => ['labops', 'workload-v31'] as const,
  principalScope: (principalKey: string) => ['labops', 'workload-v31', principalKey] as const,

  snapshot: (principalKey: string, scope: ScopeBase) =>
    queryOptions({
      queryKey: ['labops', 'workload-v31', principalKey, 'snapshot', scope] as const,
      queryFn: ({ signal }) => fetchWorkloadSnapshot(scope, signal),
      staleTime: 5_000,
      placeholderData: keepPreviousData
    }),

  analytics: (principalKey: string, scope: ScopeBase, rangePreset: HistoryRangePreset) =>
    queryOptions({
      queryKey: ['labops', 'workload-v31', principalKey, 'analytics', scope, rangePreset] as const,
      queryFn: ({ signal }) => {
        const win = buildRangeWindow(rangePreset);
        return fetchWorkloadAnalytics(scope, win, signal);
      },
      staleTime: 30_000,
      placeholderData: keepPreviousData
    }),

  history: (
    principalKey: string,
    scope: ScopeBase,
    rangePreset: HistoryRangePreset,
    cursor?: string | null
  ) =>
    queryOptions({
      queryKey: [
        'labops',
        'workload-v31',
        principalKey,
        'history',
        scope,
        rangePreset,
        cursor ?? null
      ] as const,
      queryFn: ({ signal }) => {
        const win = buildRangeWindow(rangePreset);
        return fetchWorkloadHistory(scope, { ...win, cursor }, signal);
      },
      staleTime: 30_000,
      placeholderData: keepPreviousData
    }),

  events: (
    principalKey: string,
    scope: ScopeBase,
    rangePreset: HistoryRangePreset,
    cursor?: string | null
  ) =>
    queryOptions({
      queryKey: [
        'labops',
        'workload-v31',
        principalKey,
        'events',
        scope,
        rangePreset,
        cursor ?? null
      ] as const,
      queryFn: ({ signal }) => {
        const win = buildRangeWindow(rangePreset);
        return fetchWorkloadEvents(scope, { ...win, cursor }, signal);
      },
      staleTime: 30_000,
      placeholderData: keepPreviousData
    }),

  detail: (principalKey: string, workloadId: string) =>
    queryOptions({
      queryKey: ['labops', 'workload-v31', principalKey, 'detail', workloadId] as const,
      queryFn: ({ signal }) => fetchWorkloadDetail(workloadId, signal),
      staleTime: 5_000
    })
};

export const workloadMutations = {
  processAction: () =>
    mutationOptions({
      mutationFn: ({ ref, payload }: { ref: string; payload: V31ProcessActionRequest }) =>
        executeV31ProcessAction(ref, payload)
    })
};
