'use client';

import React, { useState } from 'react';
import { QuickActionsBar } from './quick-actions-bar';
import { KpiSummary } from './kpi-summary';
import { ServerMonitoringPanel } from './server-monitoring-panel';
import { GpuManagementPanel } from './gpu-management-panel';
import { RunningJobsTable } from './running-jobs-table';
import { GpuHoursChart } from './gpu-hours-chart';
import { LabEventsPanel } from './lab-events-panel';
import { SecuritySummaryPanel } from './security-summary-panel';
import { GpuAllocationDialog } from './gpu-allocation-dialog';

export function OverviewContent() {
  const [allocateOpen, setAllocateOpen] = useState(false);
  const [selectedGpuId, setSelectedGpuId] = useState<string | undefined>(undefined);

  const handleOpenAllocate = (gpuId?: string) => {
    setSelectedGpuId(gpuId);
    setAllocateOpen(true);
  };

  return (
    <div className='flex flex-1 flex-col gap-5 pb-8'>
      {/* Quick Actions & Header Banner */}
      <QuickActionsBar onOpenAllocateDialog={() => handleOpenAllocate()} />

      {/* SECTION A — KPI Summary (6 Cards) */}
      <KpiSummary />

      {/* SECTION B — Server Monitoring (Track & Workstation with Circular Gauges) */}
      <ServerMonitoringPanel />

      {/* SECTION C — 4 Physical GPU Management Cards */}
      <GpuManagementPanel onOpenAllocateDialog={handleOpenAllocate} />

      {/* SECTION D — Running Jobs & Process Monitoring Table */}
      <RunningJobsTable />

      {/* SECTION E — GPU-Hours Analytics Chart */}
      <GpuHoursChart />

      {/* SECTION F — Lab Events & Security Center */}
      <div className='grid grid-cols-1 gap-5 lg:grid-cols-2'>
        <LabEventsPanel />
        <SecuritySummaryPanel />
      </div>

      {/* Quick GPU Allocation Dialog */}
      <GpuAllocationDialog
        open={allocateOpen}
        onOpenChange={setAllocateOpen}
        defaultGpuId={selectedGpuId}
      />
    </div>
  );
}
