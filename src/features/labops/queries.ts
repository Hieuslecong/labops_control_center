'use client';

import { useQuery } from '@tanstack/react-query';
import { labopsCall } from '@/features/labops/api';
import { useLabOpsStore } from '@/features/labops/store';
import type { ComputeJob, RepositoryGuardRequest } from '@/types/labops';

export interface RepositoryGuardAuditEntry {
  id?: string;
  timestamp?: string;
  actor?: string;
  action?: string;
  target?: string;
  detail?: string;
}

export function useLabOpsTelemetrySyncQuery(
  options: {
    enabled?: boolean;
    isAdmin?: boolean;
    intervalMs?: number;
  } = {}
) {
  const { enabled = true, isAdmin = true, intervalMs = 10_000 } = options;

  return useQuery({
    queryKey: ['labops', 'telemetry-sync', Boolean(isAdmin)],
    queryFn: async () => {
      await useLabOpsStore.getState().fetchLiveState({ isAdmin });
      return { syncedAt: Date.now() };
    },
    enabled,
    staleTime: 5_000,
    refetchInterval: enabled ? intervalMs : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true
  });
}

export function useLabOpsProcessesQuery(
  options: {
    enabled?: boolean;
    intervalMs?: number;
  } = {}
) {
  const { enabled = false, intervalMs = 10_000 } = options;

  return useQuery<ComputeJob[]>({
    queryKey: ['labops', 'processes', 'all'],
    queryFn: async () => {
      const res = await fetch('/api/labops/processes?classification=all', {
        cache: 'no-store'
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = (await res.json()) as { items?: Record<string, unknown>[] };
      const items = Array.isArray(data?.items) ? data.items : [];
      return items.map((p) => ({
        id: String(p.pid),
        ref: p.ref ? String(p.ref) : undefined,
        username: String(p.username || 'unknown'),
        fullName: String(p.full_name || `@${String(p.username || 'unknown')}`),
        serverId: (p.host_id || p.server_id || 'workstation') as ComputeJob['serverId'],
        serverName: String(p.server_name || 'Server 02 — Workstation'),
        workspace: String(
          p.workspace ||
            (p.container_id ? `docker-${String(p.container_id)}` : 'Trực tiếp trên Host')
        ),
        pid: Number(p.pid),
        ppid: p.ppid ? Number(p.ppid) : undefined,
        script: String(p.script_display || p.name || 'cmd'),
        cmdline: p.cmdline ? String(p.cmdline) : undefined,
        gpuId: p.gpu_id ? String(p.gpu_id) : undefined,
        cpuPercent: Number(p.cpu_percent || 0),
        ramUsedMb: Number(p.memory_rss_mb || 0),
        vramUsedMb: Number(p.vram_mb || 0),
        startTime: String(p.start_time || ''),
        elapsedTime: String(p.elapsed_time || ''),
        status: 'RUNNING' as const,
        sshStatus: p.username ? 'SSH Online' : 'Chạy nền',
        containerState: p.container_id ? 'Running' : 'Host Process',
        isGpu: Boolean(p.is_gpu_process || Number(p.vram_mb || 0) > 0),
        classification: p.classification ? String(p.classification) : undefined,
        isProtected: Boolean(p.is_protected),
        childrenCount: Number(p.children_count || 0),
        attribution: p.attribution ? String(p.attribution) : undefined,
        isRootWorkload: Boolean(p.is_root_workload ?? true)
      }));
    },
    enabled,
    staleTime: 5_000,
    refetchInterval: enabled ? intervalMs : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true
  });
}

export function useRepositoryRequestsQuery(
  options: {
    enabled?: boolean;
    intervalMs?: number;
  } = {}
) {
  const { enabled = true, intervalMs = 15_000 } = options;

  return useQuery<RepositoryGuardRequest[]>({
    queryKey: ['labops', 'repositories', 'requests'],
    queryFn: async () => {
      const res = await labopsCall<RepositoryGuardRequest[]>('repositories/requests');
      if (!res.ok || !Array.isArray(res.data)) {
        return [];
      }
      return res.data;
    },
    enabled,
    staleTime: 5_000,
    refetchInterval: enabled ? intervalMs : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true
  });
}

export function useRepositoryAuditQuery(
  options: {
    enabled?: boolean;
    limit?: number;
    intervalMs?: number;
  } = {}
) {
  const { enabled = false, limit = 100, intervalMs = 15_000 } = options;

  return useQuery<RepositoryGuardAuditEntry[]>({
    queryKey: ['labops', 'repositories', 'audit', limit],
    queryFn: async () => {
      const res = await labopsCall<RepositoryGuardAuditEntry[]>(
        `repositories/audit?limit=${encodeURIComponent(String(limit))}`
      );
      if (!res.ok || !Array.isArray(res.data)) {
        return [];
      }
      return res.data;
    },
    enabled,
    staleTime: 5_000,
    refetchInterval: enabled ? intervalMs : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true
  });
}
