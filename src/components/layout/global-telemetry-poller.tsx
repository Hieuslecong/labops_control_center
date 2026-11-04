'use client';

import { useEffect, useRef } from 'react';
import { useLabOpsStore } from '@/features/labops/store';

/**
 * GlobalTelemetryPoller
 * Runs at the Dashboard Layout level to ensure servers, GPUs, jobs,
 * and SSH sessions are kept continuously fresh across all dashboard routes.
 */
export function GlobalTelemetryPoller() {
  const fetchLiveState = useLabOpsStore((state) => state.fetchLiveState);
  const isPollingRef = useRef(false);

  useEffect(() => {
    // Immediate initial sync
    fetchLiveState();

    // Background interval polling every 4 seconds
    const interval = setInterval(() => {
      // Don't overlap concurrent fetches
      if (isPollingRef.current) return;
      // Pause polling if the tab is hidden to save CPU/battery
      if (document.hidden) return;

      isPollingRef.current = true;
      fetchLiveState().finally(() => {
        isPollingRef.current = false;
      });
    }, 4000);

    // Immediate sync when tab becomes visible again
    const handleVisibilityChange = () => {
      if (!document.hidden) {
        fetchLiveState();
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [fetchLiveState]);

  return null;
}
