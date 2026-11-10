'use client';

import React, { useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { GpuAllocationDialog } from '@/features/overview/components/gpu-allocation-dialog';
import { toast } from 'sonner';
import { labopsCall, toastVerification } from '@/features/labops/api';

export function ResourcesManagementView() {
  const gpus = useLabOpsStore((state) => state.gpus);
  const jobs = useLabOpsStore((state) => state.jobs);
  const fetchLiveState = useLabOpsStore((state) => state.fetchLiveState);
  const lastSyncTime = useLabOpsStore((state) => state.lastSyncTime);

  const [allocateOpen, setAllocateOpen] = useState(false);
  const [selectedGpuId, setSelectedGpuId] = useState<string | undefined>(undefined);

  // Poll live telemetry every 4 seconds to ensure GPU states are always synchronized
  useEffect(() => {
    fetchLiveState();
    const timer = setInterval(() => {
      fetchLiveState();
    }, 4000);
    return () => clearInterval(timer);
  }, [fetchLiveState]);

  const totalUsedVramMb = gpus.reduce((acc, g) => acc + (g.vramUsedMb || 0), 0);
  const reportedVramMb = gpus.reduce((acc, g) => acc + g.vramTotalMb, 0);
  const totalUsedVramGb = (totalUsedVramMb / 1024).toFixed(1);
  const reportedFreeVramGb = Math.max(0, (reportedVramMb - totalUsedVramMb) / 1024).toFixed(1);
  const reportedVramPercent =
    reportedVramMb > 0 ? Math.round((totalUsedVramMb / reportedVramMb) * 100) : 0;

  return (
    <div className='flex flex-col gap-5 min-w-0 w-full'>
      {/* Overview Statistics Banner */}
      <div className='grid grid-cols-2 sm:grid-cols-4 gap-3'>
        <Card className='p-3.5 bg-card min-w-0'>
          <span className='text-[11px] text-muted-foreground font-medium'>GPU được báo cáo</span>
          <div className='text-xl font-bold mt-0.5 text-foreground flex items-center gap-1.5'>
            <Icons.gpu className='size-5 text-primary' />
            {gpus.length} GPU
          </div>
          <span className='text-[10px] text-muted-foreground'>
            Số lượng từ telemetry backend
          </span>
        </Card>

        <Card className='p-3.5 bg-card min-w-0'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Tổng VRAM được báo cáo
          </span>
          <div className='text-xl font-bold mt-0.5 font-mono text-primary'>
            {Math.round(reportedVramMb / 1024)} GB
          </div>
          <span className='text-[10px] text-muted-foreground'>
            Dung lượng chưa được xác minh nếu collector không có dữ liệu
          </span>
        </Card>

        <Card className='p-3.5 bg-card min-w-0'>
          <span className='text-[11px] text-muted-foreground font-medium'>VRAM đang sử dụng</span>
          <div className='text-xl font-bold mt-0.5 font-mono text-indigo-600 dark:text-indigo-400'>
            {totalUsedVramGb} GB
          </div>
          <span className='text-[10px] text-muted-foreground'>
            {gpus.length ? `${reportedVramPercent}% VRAM đang dùng` : 'Chưa có telemetry GPU'}
          </span>
        </Card>

        <Card className='p-3.5 bg-card min-w-0'>
          <span className='text-[11px] text-muted-foreground font-medium'>VRAM còn khả dụng</span>
          <div className='text-xl font-bold mt-0.5 font-mono text-emerald-600 dark:text-emerald-400'>
            {reportedFreeVramGb} GB
          </div>
          <span className='text-[10px] text-muted-foreground'>Phần VRAM trống quan sát được</span>
        </Card>
      </div>

      {/* Enforcement Architecture Note */}
      <Card className='border bg-gradient-to-r from-blue-900/10 via-background to-card p-4'>
        <div className='flex items-start gap-3'>
          <div className='flex size-8 shrink-0 items-center justify-center rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400 mt-0.5'>
            <Icons.shield className='size-4' />
          </div>
          <div className='space-y-1 text-xs'>
            <h4 className='font-bold text-foreground text-sm'>
              Cơ chế Giới hạn & Thực thi Bộ nhớ GPU (VRAM Enforcement Policies)
            </h4>
            <p className='text-muted-foreground leading-relaxed'>
              Hệ thống phân biệt rõ ràng giữa <strong>Hạn ngạch yêu cầu (Requested Quota)</strong>{' '}
              và <strong>Cơ chế cưỡng chế thực tế</strong>:
            </p>
            <div className='grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1'>
              <div className='p-2 rounded border bg-background/80'>
                <Badge
                  variant='outline'
                  className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[9px] font-mono'
                >
                  HARDWARE_ENFORCED
                </Badge>
                <p className='text-[11px] text-muted-foreground mt-1'>
                  Chiếm trọn vẹn GPU vật lý ở chế độ Exclusive hoặc phân hoạch MIG phần cứng.
                </p>
              </div>
              <div className='p-2 rounded border bg-background/80'>
                <Badge
                  variant='outline'
                  className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[9px] font-mono'
                >
                  DRIVER_ENFORCED_VERIFIED
                </Badge>
                <p className='text-[11px] text-muted-foreground mt-1'>
                  Áp dụng cgroups memory controller và NVIDIA MPS Server giới hạn cứng bộ nhớ.
                </p>
              </div>
              <div className='p-2 rounded border bg-background/80'>
                <Badge
                  variant='outline'
                  className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[9px] font-mono'
                >
                  MONITOR_ONLY
                </Badge>
                <p className='text-[11px] text-muted-foreground mt-1'>
                  Chế độ giám sát ngưỡng cảnh báo; không kill tiến trình khi vượt ngưỡng nhẹ.
                </p>
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* GPU Devices Visual Breakdown */}
      <div className='space-y-3'>
        <div className='flex items-center justify-between'>
          <h3 className='text-base font-bold tracking-tight'>
            Chi tiết 4 Thiết bị GPU Vật lý & Phân bổ Bộ nhớ
          </h3>
          <div className='flex items-center gap-2'>
            {lastSyncTime && (
              <span className='text-[10px] text-muted-foreground font-mono flex items-center gap-1'>
                <span className='size-1.5 rounded-full bg-emerald-500 animate-pulse' />
                {lastSyncTime}
              </span>
            )}
            <Button
              size='sm'
              onClick={() => {
                setSelectedGpuId(undefined);
                setAllocateOpen(true);
              }}
              className='h-8 text-xs font-semibold gap-1.5 bg-primary'
            >
              <Icons.add className='size-3.5' /> Cấp phát GPU mới
            </Button>
          </div>
        </div>

        <div className='grid grid-cols-1 gap-4 lg:grid-cols-2 min-w-0'>
          {gpus.map((gpu) => {
            const usedGb = (gpu.vramUsedMb / 1024).toFixed(1);
            const percent = Math.round((gpu.vramUsedMb / gpu.vramTotalMb) * 100);
            const isRunning =
              gpu.vramUsedMb > 512 ||
              gpu.gpuUtilPercent > 0 ||
              (gpu.activeProcesses && gpu.activeProcesses.length > 0);
            const isNearlyFull = percent >= 80;

            return (
              <Card key={gpu.id} className='relative overflow-hidden border bg-card min-w-0'>
                {/* Top indicator stripe */}
                <div
                  className={`absolute top-0 left-0 right-0 h-1 ${
                    gpu.allocationMode === 'EXCLUSIVE'
                      ? 'bg-purple-600'
                      : isRunning
                        ? 'bg-indigo-600'
                        : 'bg-emerald-500'
                  }`}
                />

                <CardHeader className='pb-2 pt-4'>
                  <div className='flex items-start justify-between gap-1'>
                    <div>
                      <div className='flex items-center gap-1.5 flex-wrap'>
                        <CardTitle className='text-sm font-bold'>{gpu.displayLabel}</CardTitle>
                        <Badge
                          variant={gpu.allocationMode === 'EXCLUSIVE' ? 'default' : 'secondary'}
                          className='text-[10px]'
                        >
                          {gpu.allocationMode}
                        </Badge>
                        {isRunning ? (
                          <Badge className='bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/30 text-[10px] font-medium'>
                            <span className='size-1.5 rounded-full bg-indigo-500 animate-pulse mr-1' />
                            ĐANG CHẠY
                          </Badge>
                        ) : (
                          <Badge variant='outline' className='text-muted-foreground text-[10px]'>
                            SẴN SÀNG
                          </Badge>
                        )}
                        <Badge variant='outline' className='text-[10px] font-mono'>
                          {gpu.serverName}
                        </Badge>
                      </div>
                      <CardDescription className='text-xs mt-0.5 font-mono truncate'>
                        UUID: {gpu.uuid} • Bus: {gpu.pciBusId}
                      </CardDescription>
                    </div>

                    <div className='text-right shrink-0'>
                      <span className='font-mono font-bold text-base text-primary'>
                        {usedGb} / 24 GB
                      </span>
                      <div className='text-[10px] text-muted-foreground'>{percent}% VRAM</div>
                    </div>
                  </div>
                </CardHeader>

                <CardContent className='pt-0 pb-4 space-y-3 text-xs'>
                  {/* Visual memory bar */}
                  <div className='space-y-1'>
                    <div className='w-full bg-muted rounded-full h-3 overflow-hidden flex'>
                      {gpu.activeProcesses.map((p, idx) => {
                        const sliceWidth = (p.vramMb / gpu.vramTotalMb) * 100;
                        const colors = [
                          'bg-blue-600',
                          'bg-indigo-600',
                          'bg-purple-600',
                          'bg-cyan-600'
                        ];
                        return (
                          <div
                            key={p.pid}
                            title={`${p.username}: ${(p.vramMb / 1024).toFixed(1)}GB`}
                            className={`${colors[idx % colors.length]} h-full transition-all`}
                            style={{ width: `${sliceWidth}%` }}
                          />
                        );
                      })}
                    </div>
                    <div className='flex justify-between text-[10px] text-muted-foreground font-mono'>
                      <span>0 GB</span>
                      <span>12 GB</span>
                      <span>24 GB (Tối đa)</span>
                    </div>
                  </div>

                  {/* Telemetry row */}
                  <div className='grid grid-cols-3 gap-2 py-2 px-2.5 rounded bg-muted/20 text-[11px]'>
                    <div>
                      <span className='text-muted-foreground block text-[10px]'>Tải tính toán</span>
                      <span className='font-bold font-mono'>{gpu.gpuUtilPercent}%</span>
                    </div>
                    <div>
                      <span className='text-muted-foreground block text-[10px]'>Nhiệt độ</span>
                      <span className='font-bold font-mono'>{gpu.tempCelsius}°C</span>
                    </div>
                    <div>
                      <span className='text-muted-foreground block text-[10px]'>Công suất</span>
                      <span className='font-bold font-mono'>
                        {gpu.powerWatts}W / {gpu.powerLimitWatts}W
                      </span>
                    </div>
                  </div>

                  {/* Active processes in this GPU */}
                  <div className='space-y-1.5'>
                    <span className='text-[11px] font-semibold text-muted-foreground block'>
                      Tiến trình đang chiếm dụng ({gpu.activeProcesses.length}):
                    </span>
                    {gpu.activeProcesses.length > 0 ? (
                      gpu.activeProcesses.map((proc) => (
                        <div
                          key={proc.pid}
                          className='p-2 rounded border bg-background flex items-center justify-between text-xs'
                        >
                          <div>
                            <span className='font-bold text-foreground'>@{proc.username}</span>
                            <span className='text-muted-foreground ml-1 font-mono text-[11px]'>
                              ({proc.workspace})
                            </span>
                            <div className='font-mono text-[11px] text-muted-foreground truncate max-w-[280px]'>
                              {proc.name} (PID: {proc.pid})
                            </div>
                          </div>
                          <div className='flex items-center gap-2'>
                            <span className='font-mono font-bold text-indigo-600 dark:text-indigo-400'>
                              {(proc.vramMb / 1024).toFixed(1)} GB
                            </span>
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className='py-2 text-center text-[11px] text-muted-foreground rounded border border-dashed'>
                        Không có tiến trình chiếm dụng VRAM. Sẵn sàng nhận tác vụ mới.
                      </div>
                    )}
                  </div>

                  <div className='flex justify-end pt-1'>
                    <Button
                      size='sm'
                      variant='outline'
                      disabled={isNearlyFull}
                      onClick={() => {
                        setSelectedGpuId(gpu.id);
                        setAllocateOpen(true);
                      }}
                      className='h-7 text-xs font-semibold'
                    >
                      {isNearlyFull ? `Đầy tải (${percent}%)` : '+ Cấp phát trên GPU này'}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>

      {/* Active GPU Workloads Table */}
      <Card className='border bg-card'>
        <CardHeader className='pb-2 pt-4'>
          <CardTitle className='text-sm font-bold'>
            Bảng Phân bổ Quyền sử dụng GPU Hiện hành
          </CardTitle>
          <CardDescription className='text-xs'>
            Danh sách các tiến trình được cấp phát tài nguyên tính toán và đang chiếm VRAM vật lý
          </CardDescription>
        </CardHeader>
        <CardContent className='p-0'>
          <div className='overflow-x-auto'>
            <table className='w-full text-left text-xs'>
              <thead>
                <tr className='border-b text-muted-foreground font-semibold bg-muted/20'>
                  <th className='py-2.5 px-3'>Thành viên</th>
                  <th className='py-2.5 px-3'>Thiết bị GPU</th>
                  <th className='py-2.5 px-3'>PID / Lệnh thực thi</th>
                  <th className='py-2.5 px-3'>VRAM Chiếm dụng</th>
                  <th className='py-2.5 px-3'>Thời gian chạy</th>
                  <th className='py-2.5 px-3'>Trạng thái</th>
                  <th className='py-2.5 px-3 text-right'>Thao tác</th>
                </tr>
              </thead>
              <tbody className='divide-y divide-border/60'>
                {jobs.filter((j) => j.vramUsedMb > 0 || j.isGpu).length === 0 ? (
                  <tr>
                    <td colSpan={7} className='py-8 text-center text-xs text-muted-foreground'>
                      Chưa phát hiện tiến trình nào chiếm dụng GPU VRAM.
                    </td>
                  </tr>
                ) : (
                  jobs
                    .filter((j) => j.vramUsedMb > 0 || j.isGpu)
                    .map((j) => (
                      <tr key={j.id} className='hover:bg-muted/30 transition-colors'>
                        <td className='py-2.5 px-3'>
                          <div className='font-bold text-foreground'>{j.fullName}</div>
                          <div className='text-[11px] text-muted-foreground font-mono'>
                            @{j.username}
                          </div>
                        </td>
                        <td className='py-2.5 px-3'>
                          <Badge variant='default' className='font-mono text-[10px]'>
                            {j.gpuId}
                          </Badge>
                          <div className='text-[10px] text-muted-foreground font-mono uppercase mt-0.5'>
                            {j.serverId}
                          </div>
                        </td>
                        <td className='py-2.5 px-3 max-w-xs'>
                          <div className='font-mono font-bold text-[11px]'>PID: {j.pid}</div>
                          <div
                            className='font-mono text-muted-foreground text-[11px] truncate max-w-[280px]'
                            title={j.script}
                          >
                            {j.script}
                          </div>
                        </td>
                        <td className='py-2.5 px-3 font-mono font-bold text-indigo-600 dark:text-indigo-400'>
                          {(j.vramUsedMb / 1024).toFixed(1)} GB VRAM
                        </td>
                        <td className='py-2.5 px-3 text-foreground font-mono'>{j.elapsedTime}</td>
                        <td className='py-2.5 px-3'>
                          <Badge
                            variant='outline'
                            className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px]'
                          >
                            ACTIVE
                          </Badge>
                        </td>
                        <td className='py-2.5 px-3 text-right'>
                          <Button
                            size='sm'
                            variant='ghost'
                            onClick={async () => {
                              const r = await labopsCall<{ verification?: string; state?: string }>(
                                `jobs/${encodeURIComponent(j.id)}/stop`,
                                { method: 'POST' }
                              );
                              toastVerification(`Thu hồi tác vụ ${j.id}`, r);
                            }}
                            className='h-7 text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-500/10'
                          >
                            Thu hồi
                          </Button>
                        </td>
                      </tr>
                    ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Allocation Dialog */}
      <GpuAllocationDialog
        open={allocateOpen}
        onOpenChange={setAllocateOpen}
        defaultGpuId={selectedGpuId}
      />
    </div>
  );
}
