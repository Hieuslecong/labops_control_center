'use client';

import { useAuthSession } from '@/hooks/use-auth-session';
import { useLabOpsTelemetrySyncQuery } from '@/features/labops/queries';
import { useLabOpsStore } from '@/features/labops/store';

/**
 * GlobalTelemetryPoller
 * Runs at the Dashboard Layout level via TanStack Query visibility-aware polling
 * (`refetchIntervalInBackground: false`) so telemetry stays fresh without overlapping timers.
 */
export function GlobalTelemetryPoller() {
  const { session, isAdmin, isLoading } = useAuthSession();
  const isLivePolling = useLabOpsStore((state) => state.isLivePolling);

  useLabOpsTelemetrySyncQuery({
    enabled: !isLoading && Boolean(session?.authenticated) && isLivePolling,
    isAdmin,
    intervalMs: 10_000
  });

  return null;
}
