'use client';

import React, { useState } from 'react';
import { Card, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { CircularGauge } from '@/features/overview/components/circular-gauge';
import { ServerId } from '@/types/labops';
import { ChartConfig, ChartContainer } from '@/components/ui/chart';
import { AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';

const cpuRamChartConfig = {
  cpu: {
    label: 'CPU Util %',
    color: 'oklch(0.55 0.22 260)'
  },
  ram: {
    label: 'RAM Util %',
    color: 'oklch(0.58 0.22 295)'
  }
} satisfies ChartConfig;

const TIME_SERIES_CPU_RAM = [
  { time: '14:00', cpu: 38, ram: 65, net: 410 },
  { time: '15:00', cpu: 45, ram: 68, net: 580 },
  { time: '16:00', cpu: 52, ram: 71, net: 690 },
  { time: '17:00', cpu: 48, ram: 69, net: 630 },
  { time: '18:00', cpu: 40, ram: 67, net: 510 },
  { time: '19:00', cpu: 44, ram: 68, net: 590 },
  { time: '20:00', cpu: 42, ram: 68, net: 620 }
];

export function ServerDetailView() {
  const servers = useLabOpsStore((state) => state.servers);
  const gpus = useLabOpsStore((state) => state.gpus);
  const jobs = useLabOpsStore((state) => state.jobs);
  const members = useLabOpsStore((state) => state.members);
  const fetchLiveState = useLabOpsStore((state) => state.fetchLiveState);
  const fetchLiveJobs = useLabOpsStore((state) => state.fetchLiveJobs);

  React.useEffect(() => {
    fetchLiveState();
    fetchLiveJobs();
    const interval = setInterval(() => {
      fetchLiveJobs();
    }, 4000);
    return () => clearInterval(interval);
  }, [fetchLiveState, fetchLiveJobs]);

  const [selectedServerId, setSelectedServerId] = useState<ServerId>('track');
  const [jobSearchTerm, setJobSearchTerm] = useState('');
  const [jobResourceFilter, setJobResourceFilter] = useState<'all' | 'gpu' | 'cpu'>('all');
  const [jobUserFilter, setJobUserFilter] = useState('all');

  const server = servers.find((s) => s.id === selectedServerId) || servers[0];
  const serverGpus = gpus.filter((g) => g.serverId === selectedServerId);
  const serverJobs = jobs.filter((j) => j.serverId === selectedServerId);
  const serverMembers = members.filter((m) => m.assignedServer === selectedServerId);

  const serverUserCounts = React.useMemo(() => {
    const map: Record<string, number> = {};
    serverJobs.forEach((j) => {
      if (j.username) {
        map[j.username] = (map[j.username] || 0) + 1;
      }
    });
    return Object.entries(map).toSorted((a, b) => b[1] - a[1]);
  }, [serverJobs]);

  const serverGpuCount = React.useMemo(
    () => serverJobs.filter((j) => j.isGpu || j.vramUsedMb > 0).length,
    [serverJobs]
  );
  const serverCpuCount = React.useMemo(
    () => serverJobs.filter((j) => !j.isGpu && j.vramUsedMb <= 0).length,
    [serverJobs]
  );

  const filteredServerJobs = React.useMemo(() => {
    return serverJobs.filter((j) => {
      if (jobSearchTerm.trim()) {
        const q = jobSearchTerm.toLowerCase();
        const matchPid = String(j.pid).includes(q);
        const matchScript = j.script.toLowerCase().includes(q);
        const matchCmd = j.cmdline ? j.cmdline.toLowerCase().includes(q) : false;
        const matchUser =
          j.username.toLowerCase().includes(q) || j.fullName.toLowerCase().includes(q);
        if (!matchPid && !matchScript && !matchCmd && !matchUser) return false;
      }
      if (jobResourceFilter === 'gpu' && !j.isGpu && j.vramUsedMb <= 0) return false;
      if (jobResourceFilter === 'cpu' && (j.isGpu || j.vramUsedMb > 0)) return false;
      if (jobUserFilter !== 'all' && j.username !== jobUserFilter) return false;
      return true;
    });
  }, [serverJobs, jobSearchTerm, jobResourceFilter, jobUserFilter]);

  return (
    <div className='flex flex-col gap-5'>
      {/* Server Switcher Header */}
      <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-4 rounded-xl border bg-card'>
        <div>
          <div className='flex items-center gap-2'>
            <h2 className='text-xl font-bold tracking-tight'>{server.name}</h2>
            <Badge
              variant='outline'
              className={
                server.status === 'online'
                  ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 font-mono text-[11px]'
                  : 'bg-destructive/10 text-destructive border-destructive/30 font-mono text-[11px]'
              }
            >
              <span
                className={`size-2 rounded-full mr-1 ${
                  server.status === 'online' ? 'bg-emerald-500 animate-pulse' : 'bg-destructive'
                }`}
              ></span>
              {server.status === 'online' ? 'TRỰC TUYẾN' : 'MẤT KẾT NỐI (Chưa kết nối VPN)'}
            </Badge>
            <Badge variant='secondary' className='font-mono text-xs'>
              {server.hostname}
            </Badge>
          </div>
          <p className='text-xs text-muted-foreground mt-1'>
            {server.location} • IP:{' '}
            <span className='font-mono font-medium text-foreground'>{server.ipAddress}</span> • VPN
            WireGuard: <span className='font-mono font-medium text-foreground'>{server.vpnIp}</span>{' '}
            • Uptime: {server.uptime}
          </p>
          {server.status !== 'online' && (
            <div className='mt-2.5 p-2 rounded-md bg-destructive/10 border border-destructive/20 text-xs text-destructive flex items-center gap-2'>
              <Icons.warning className='size-4 shrink-0' />
              <span>
                <strong>Cảnh báo kết nối:</strong> Máy chủ này hiện không phản hồi qua đường hầm
                WireGuard VPN. LabOps từ chối hiển thị dữ liệu giả định. Vui lòng kiểm tra dịch vụ{' '}
                <code>wg-quick@wg0</code> trên máy chủ từ xa.
              </span>
            </div>
          )}
        </div>

        <div className='flex items-center gap-2'>
          <Button
            variant={selectedServerId === 'track' ? 'default' : 'outline'}
            size='sm'
            onClick={() => setSelectedServerId('track')}
            className='h-8 text-xs font-semibold gap-1.5'
          >
            <Icons.server className='size-3.5' /> Server Track (3x A30)
          </Button>
          <Button
            variant={selectedServerId === 'workstation' ? 'default' : 'outline'}
            size='sm'
            onClick={() => setSelectedServerId('workstation')}
            className='h-8 text-xs font-semibold gap-1.5'
          >
            <Icons.server className='size-3.5' /> Server Workstation (1x A30)
          </Button>
        </div>
      </div>

      {/* 9 Server Detail Tabs */}
      <Tabs defaultValue='overview' className='w-full'>
        <TabsList className='w-full justify-start overflow-x-auto flex flex-nowrap border-b rounded-none p-0 h-auto bg-transparent'>
          <TabsTrigger
            value='overview'
            className='data-[state=active]:border-primary data-[state=active]:bg-background border-b-2 border-transparent rounded-none px-4 py-2.5 text-xs font-medium'
          >
            Tổng quan
          </TabsTrigger>
          <TabsTrigger
            value='gpus'
            className='data-[state=active]:border-primary data-[state=active]:bg-background border-b-2 border-transparent rounded-none px-4 py-2.5 text-xs font-medium'
          >
            GPU ({serverGpus.length})
          </TabsTrigger>
          <TabsTrigger
            value='cpu-ram'
            className='data-[state=active]:border-primary data-[state=active]:bg-background border-b-2 border-transparent rounded-none px-4 py-2.5 text-xs font-medium'
          >
            CPU & RAM
          </TabsTrigger>
          <TabsTrigger
            value='storage'
            className='data-[state=active]:border-primary data-[state=active]:bg-background border-b-2 border-transparent rounded-none px-4 py-2.5 text-xs font-medium'
          >
            Lưu trữ ZFS
          </TabsTrigger>
          <TabsTrigger
            value='workspaces'
            className='data-[state=active]:border-primary data-[state=active]:bg-background border-b-2 border-transparent rounded-none px-4 py-2.5 text-xs font-medium'
          >
            Workspaces Incus ({serverMembers.length})
          </TabsTrigger>
          <TabsTrigger
            value='ssh'
            className='data-[state=active]:border-primary data-[state=active]:bg-background border-b-2 border-transparent rounded-none px-4 py-2.5 text-xs font-medium'
          >
            Phiên SSH ({serverMembers.reduce((a, b) => a + b.sshSessionsCount, 0)})
          </TabsTrigger>
          <TabsTrigger
            value='jobs'
            className='data-[state=active]:border-primary data-[state=active]:bg-background border-b-2 border-transparent rounded-none px-4 py-2.5 text-xs font-medium'
          >
            Tiến trình ({serverJobs.length})
          </TabsTrigger>
          <TabsTrigger
            value='logs'
            className='data-[state=active]:border-primary data-[state=active]:bg-background border-b-2 border-transparent rounded-none px-4 py-2.5 text-xs font-medium'
          >
            Nhật ký Dmesg
          </TabsTrigger>
          <TabsTrigger
            value='alerts'
            className='data-[state=active]:border-primary data-[state=active]:bg-background border-b-2 border-transparent rounded-none px-4 py-2.5 text-xs font-medium'
          >
            Cảnh báo
          </TabsTrigger>
        </TabsList>

        {/* TAB 1: TỔNG QUAN */}
        <TabsContent value='overview' className='mt-4 space-y-4'>
          <div className='grid grid-cols-1 md:grid-cols-3 gap-4'>
            <Card className='p-4 flex items-center justify-between bg-card'>
              <div>
                <span className='text-xs font-medium text-muted-foreground'>
                  Tải CPU ({server.cpuCores} Cores)
                </span>
                <div className='text-2xl font-bold font-mono mt-1'>{server.cpuUsagePercent}%</div>
                <span className='text-[11px] text-muted-foreground truncate block max-w-[200px]'>
                  {server.cpuModel}
                </span>
              </div>
              <CircularGauge
                value={server.cpuUsagePercent}
                size={64}
                strokeWidth={6}
                color='stroke-blue-600'
              />
            </Card>

            <Card className='p-4 flex items-center justify-between bg-card'>
              <div>
                <span className='text-xs font-medium text-muted-foreground'>Bộ nhớ RAM</span>
                <div className='text-2xl font-bold font-mono mt-1'>
                  {Math.round(server.ramUsedGb)} / {server.ramTotalGb} GB
                </div>
                <span className='text-[11px] text-muted-foreground'>
                  {server.ramUsagePercent}% đã sử dụng
                </span>
              </div>
              <CircularGauge
                value={server.ramUsagePercent}
                size={64}
                strokeWidth={6}
                color='stroke-indigo-600'
              />
            </Card>

            <Card className='p-4 flex items-center justify-between bg-card'>
              <div>
                <span className='text-xs font-medium text-muted-foreground'>ZFS Pools Lưu trữ</span>
                <div className='text-2xl font-bold font-mono mt-1'>
                  {server.diskUsedTb.toFixed(1)} / {server.diskTotalTb} TB
                </div>
                <span className='text-[11px] text-muted-foreground'>{server.diskType}</span>
              </div>
              <CircularGauge
                value={server.diskUsagePercent}
                size={64}
                strokeWidth={6}
                color='stroke-cyan-600'
              />
            </Card>
          </div>

          <Card className='p-4 bg-card'>
            <CardTitle className='text-sm font-bold mb-3'>Thông tin Nền tảng & Ảo hóa</CardTitle>
            <div className='grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs'>
              <div className='p-2.5 rounded border bg-muted/20'>
                <span className='text-muted-foreground block text-[11px]'>Container Manager</span>
                <span className='font-bold text-foreground'>Incus {server.incusVersion}</span>
              </div>
              <div className='p-2.5 rounded border bg-muted/20'>
                <span className='text-muted-foreground block text-[11px]'>NVIDIA Driver</span>
                <span className='font-bold text-foreground'>{server.driverVersion}</span>
              </div>
              <div className='p-2.5 rounded border bg-muted/20'>
                <span className='text-muted-foreground block text-[11px]'>CUDA Toolkit</span>
                <span className='font-bold text-foreground'>{server.cudaVersion}</span>
              </div>
              <div className='p-2.5 rounded border bg-muted/20'>
                <span className='text-muted-foreground block text-[11px]'>Kernel Linux</span>
                <span className='font-mono font-bold text-foreground'>5.15.0-126-generic</span>
              </div>
            </div>
          </Card>
        </TabsContent>

        {/* TAB 2: GPUS */}
        <TabsContent value='gpus' className='mt-4 space-y-4'>
          {serverGpus.length === 0 ? (
            <div className='p-8 rounded-lg border border-dashed text-center text-xs text-muted-foreground flex flex-col items-center justify-center gap-2 bg-muted/20'>
              <Icons.warning className='size-6 text-amber-500' />
              <span className='font-semibold text-foreground text-sm'>
                {selectedServerId === 'track'
                  ? 'Server Track (3x NVIDIA A30) hiện ngoại tuyến qua WireGuard VPN'
                  : 'Không có thiết bị GPU nào được tìm thấy trên máy chủ này'}
              </span>
              <span className='max-w-md text-[11px]'>
                {selectedServerId === 'track'
                  ? 'Toàn bộ 3x GPU NVIDIA A30 (72 GB VRAM) trên Server Track sẽ tự động xuất hiện khi kết nối VPN 10.200.0.1 được thiết lập.'
                  : 'Kiểm tra driver NVIDIA hoặc quyền truy cập của agent giám sát.'}
              </span>
            </div>
          ) : (
            <div className='grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4'>
              {serverGpus.map((gpu) => {
                const isRunning =
                  gpu.vramUsedMb > 512 ||
                  gpu.gpuUtilPercent > 0 ||
                  (gpu.activeProcesses && gpu.activeProcesses.length > 0);
                const vramPercent = Math.round((gpu.vramUsedMb / gpu.vramTotalMb) * 100);

                return (
                  <Card
                    key={gpu.id}
                    className='relative overflow-hidden p-4 bg-card min-w-0 border'
                  >
                    <div
                      className={`absolute top-0 left-0 right-0 h-1 ${
                        isRunning ? 'bg-indigo-600' : 'bg-emerald-500'
                      }`}
                    />
                    <div className='flex items-center justify-between gap-1 flex-wrap'>
                      <div className='flex items-center gap-1.5'>
                        <span className='font-bold text-sm'>{gpu.displayLabel}</span>
                        {isRunning ? (
                          <Badge className='bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/30 font-medium text-[10px] px-1.5 py-0'>
                            <span className='size-1.5 rounded-full bg-indigo-500 animate-pulse mr-1' />
                            ĐANG CHẠY
                          </Badge>
                        ) : (
                          <Badge
                            variant='outline'
                            className='text-muted-foreground text-[10px] px-1.5 py-0'
                          >
                            RỖI
                          </Badge>
                        )}
                      </div>
                      <Badge variant='outline' className='font-mono text-[10px]'>
                        {gpu.allocationMode}
                      </Badge>
                    </div>
                    <div className='text-xs text-muted-foreground mt-1'>{gpu.model}</div>
                    <div className='my-3'>
                      <div className='flex justify-between text-xs mb-1'>
                        <span className='text-muted-foreground font-medium'>Bộ nhớ VRAM:</span>
                        <span className='font-mono font-bold text-foreground'>
                          {(gpu.vramUsedMb / 1024).toFixed(1)} / 24 GB{' '}
                          <span className='font-normal text-[11px] text-muted-foreground'>
                            ({vramPercent}%)
                          </span>
                        </span>
                      </div>
                      <div className='w-full bg-muted rounded-full h-2 overflow-hidden'>
                        <div
                          className={`h-2 rounded-full transition-all duration-500 ${
                            vramPercent > 80
                              ? 'bg-rose-500'
                              : isRunning
                                ? 'bg-indigo-600'
                                : 'bg-emerald-500'
                          }`}
                          style={{ width: `${vramPercent}%` }}
                        />
                      </div>
                    </div>
                    <div className='text-[11px] text-muted-foreground space-y-1'>
                      <div className='flex items-center justify-between'>
                        <span>
                          Nhiệt độ: <strong className='text-foreground'>{gpu.tempCelsius}°C</strong>
                        </span>
                        <span>
                          Điện: <strong className='text-foreground'>{gpu.powerWatts}W</strong>
                        </span>
                        <span>
                          Tải:{' '}
                          <strong className='text-foreground font-mono'>
                            {gpu.gpuUtilPercent}%
                          </strong>
                        </span>
                      </div>
                      <div className='truncate font-mono text-[10px]'>
                        UUID: <span>{gpu.uuid.slice(0, 22)}...</span>
                      </div>
                    </div>

                    {/* Active processes on this GPU */}
                    <div className='mt-3 pt-2.5 border-t space-y-1.5'>
                      <span className='text-[11px] font-semibold text-muted-foreground block'>
                        Tiến trình chiếm dụng ({gpu.activeProcesses.length}):
                      </span>
                      {gpu.activeProcesses.length > 0 ? (
                        <div className='space-y-1 max-h-36 overflow-y-auto pr-0.5'>
                          {gpu.activeProcesses.map((proc) => (
                            <div
                              key={proc.pid}
                              className='p-1.5 rounded bg-muted/40 border text-[11px] flex items-center justify-between gap-1'
                            >
                              <div className='truncate min-w-0 flex-1'>
                                <span className='font-bold text-foreground font-mono'>
                                  @{proc.username}
                                </span>
                                <span className='text-muted-foreground text-[10px] ml-1 font-mono'>
                                  (PID: {proc.pid})
                                </span>
                                <div className='font-mono text-[10px] text-muted-foreground truncate'>
                                  {proc.name}
                                </div>
                              </div>
                              <Badge
                                variant='outline'
                                className='font-mono text-[10px] shrink-0 font-bold text-indigo-600 dark:text-indigo-400'
                              >
                                {(proc.vramMb / 1024).toFixed(1)} GB
                              </Badge>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className='text-[10px] text-muted-foreground italic py-1'>
                          Không có tiến trình chiếm dụng VRAM.
                        </div>
                      )}
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </TabsContent>

        {/* TAB 3: CPU & RAM CHART */}
        <TabsContent value='cpu-ram' className='mt-4 space-y-4'>
          <Card className='p-4 bg-card'>
            <CardTitle className='text-sm font-bold mb-1'>
              Biểu đồ Sử dụng CPU & RAM theo thời gian
            </CardTitle>
            <CardDescription className='text-xs mb-4'>
              Dữ liệu giám sát thời gian thực từ Netdata Agent
            </CardDescription>
            <div className='h-[260px] w-full'>
              <ChartContainer config={cpuRamChartConfig} className='aspect-auto h-[260px] w-full'>
                <AreaChart data={TIME_SERIES_CPU_RAM}>
                  <CartesianGrid strokeDasharray='3 3' opacity={0.2} />
                  <XAxis dataKey='time' tickLine={false} tick={{ fontSize: 11 }} />
                  <YAxis tickLine={false} tick={{ fontSize: 11 }} unit='%' />
                  <Tooltip />
                  <Area
                    type='monotone'
                    dataKey='cpu'
                    stroke='oklch(0.55 0.22 260)'
                    fill='oklch(0.55 0.22 260 / 0.2)'
                    name='CPU Util %'
                    isAnimationActive={false}
                  />
                  <Area
                    type='monotone'
                    dataKey='ram'
                    stroke='oklch(0.58 0.22 295)'
                    fill='oklch(0.58 0.22 295 / 0.2)'
                    name='RAM Util %'
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ChartContainer>
            </div>
          </Card>
        </TabsContent>

        {/* TAB 4: STORAGE */}
        <TabsContent value='storage' className='mt-4 space-y-3'>
          <Card className='p-4 bg-card'>
            <CardTitle className='text-sm font-bold mb-2'>Trạng thái ZFS Storage Pool</CardTitle>
            <div className='space-y-2 text-xs'>
              <div className='p-3 rounded-lg border bg-muted/20 flex items-center justify-between'>
                <div>
                  <span className='font-bold font-mono text-sm'>pool-nvme-fast</span>
                  <p className='text-muted-foreground text-[11px]'>
                    Dung lượng: {server.diskUsedTb} TB / {server.diskTotalTb} TB • RAID-Z2 • Trạng
                    thái: HEALTHY
                  </p>
                </div>
                <Badge
                  variant='outline'
                  className='text-emerald-600 border-emerald-500/30 text-xs font-mono'
                >
                  ONLINE
                </Badge>
              </div>
              <div className='p-3 rounded-lg border bg-muted/20 flex items-center justify-between'>
                <div>
                  <span className='font-bold font-mono text-sm'>tank/datasets/shared</span>
                  <p className='text-muted-foreground text-[11px]'>
                    Tập dữ liệu nghiên cứu dùng chung (ImageNet, C4 Vietnamese, OpenHermes)
                  </p>
                </div>
                <span className='font-mono font-bold text-xs'>1.8 TB</span>
              </div>
            </div>
          </Card>
        </TabsContent>

        {/* TAB 5: WORKSPACES */}
        <TabsContent value='workspaces' className='mt-4 space-y-3'>
          <Card className='p-4 bg-card'>
            <CardTitle className='text-sm font-bold mb-3'>
              Container Incus của Thành viên trên {server.name}
            </CardTitle>
            <div className='space-y-2'>
              {serverMembers.map((m) => (
                <div
                  key={m.id}
                  className='p-3 rounded-lg border bg-muted/20 flex items-center justify-between text-xs'
                >
                  <div>
                    <span className='font-bold font-mono text-sm text-primary'>
                      {m.workspaceName}
                    </span>
                    <span className='text-muted-foreground ml-2 font-medium'>
                      ({m.fullName} - @{m.username})
                    </span>
                    <div className='text-[11px] text-muted-foreground mt-0.5'>
                      Hạn ngạch: {m.cpuLimitCores} Cores CPU • {m.ramLimitGb}GB RAM •{' '}
                      {m.storageQuotaGb}GB ZFS
                    </div>
                  </div>
                  <Badge
                    variant='outline'
                    className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px]'
                  >
                    RUNNING
                  </Badge>
                </div>
              ))}
            </div>
          </Card>
        </TabsContent>

        {/* TAB 6: SSH */}
        <TabsContent value='ssh' className='mt-4 space-y-3'>
          <Card className='p-4 bg-card'>
            <CardTitle className='text-sm font-bold mb-3'>Phiên SSH Đang Kết Nối</CardTitle>
            <div className='space-y-2 text-xs'>
              {serverMembers.filter((m) => m.sshSessionsCount > 0).length === 0 ? (
                <div className='py-6 text-center text-xs text-muted-foreground border border-dashed rounded-lg'>
                  Không có phiên SSH nào đang kết nối tới máy chủ này.
                </div>
              ) : (
                serverMembers
                  .filter((m) => m.sshSessionsCount > 0)
                  .map((m) => (
                    <div
                      key={m.id}
                      className='p-3 rounded-lg border bg-muted/20 flex items-center justify-between'
                    >
                      <div>
                        <span className='font-mono font-bold text-foreground'>
                          ssh {m.username}@{server.vpnIp}
                        </span>
                        <div className='text-[11px] text-muted-foreground mt-0.5'>
                          Người dùng: {m.fullName} • {m.sshSessionsCount} phiên hoạt động • Khóa:{' '}
                          {m.sshPublicKeyPreview.slice(0, 32)}...
                        </div>
                      </div>
                      <Badge
                        variant='outline'
                        className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px]'
                      >
                        ACTIVE
                      </Badge>
                    </div>
                  ))
              )}
            </div>
          </Card>
        </TabsContent>

        {/* TAB 7: JOBS */}
        <TabsContent value='jobs' className='mt-4 space-y-3'>
          <Card className='p-4 bg-card'>
            <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-3'>
              <div>
                <CardTitle className='text-sm font-bold'>
                  Tiến trình Tính toán trên {server.name}
                </CardTitle>
                <p className='text-xs text-muted-foreground'>
                  {filteredServerJobs.length} / {serverJobs.length} tiến trình đang thực thi
                </p>
              </div>

              {/* Quick Pills */}
              <div className='flex items-center gap-1.5 flex-wrap'>
                <Button
                  size='sm'
                  variant={jobResourceFilter === 'all' ? 'default' : 'outline'}
                  onClick={() => setJobResourceFilter('all')}
                  className='h-6 text-[11px] px-2 rounded-full'
                >
                  Tất cả ({serverJobs.length})
                </Button>
                <Button
                  size='sm'
                  variant={jobResourceFilter === 'gpu' ? 'default' : 'outline'}
                  onClick={() => setJobResourceFilter('gpu')}
                  className={`h-6 text-[11px] px-2 rounded-full ${
                    jobResourceFilter === 'gpu'
                      ? 'bg-indigo-600 hover:bg-indigo-700 text-white'
                      : 'text-indigo-600 border-indigo-500/30'
                  }`}
                >
                  <Icons.gpu className='size-3 mr-1' />⚡ GPU ({serverGpuCount})
                </Button>
                <Button
                  size='sm'
                  variant={jobResourceFilter === 'cpu' ? 'default' : 'outline'}
                  onClick={() => setJobResourceFilter('cpu')}
                  className={`h-6 text-[11px] px-2 rounded-full ${
                    jobResourceFilter === 'cpu'
                      ? 'bg-blue-600 hover:bg-blue-700 text-white'
                      : 'text-blue-600 border-blue-500/30'
                  }`}
                >
                  <Icons.cpu className='size-3 mr-1' />
                  🖥️ CPU ({serverCpuCount})
                </Button>
              </div>
            </div>

            {/* Filter Bar */}
            <div className='flex flex-wrap items-center gap-2 mb-3 pb-3 border-b text-xs'>
              <div className='relative flex-1 min-w-[160px] max-w-xs'>
                <Icons.search className='absolute left-2.5 top-2 size-3.5 text-muted-foreground' />
                <Input
                  value={jobSearchTerm}
                  onChange={(e) => setJobSearchTerm(e.target.value)}
                  placeholder='Tìm theo PID, script, lệnh, user...'
                  className='pl-8 h-7 text-xs'
                />
              </div>

              <div className='flex items-center gap-1.5'>
                <span className='text-muted-foreground text-[11px] font-medium'>Người dùng:</span>
                <select
                  value={jobUserFilter}
                  onChange={(e) => setJobUserFilter(e.target.value)}
                  className='h-7 rounded-md border border-input bg-background px-2 text-xs outline-none'
                >
                  <option value='all'>Tất cả người dùng ({serverUserCounts.length})</option>
                  {serverUserCounts.map(([uname, count]) => (
                    <option key={uname} value={uname}>
                      @{uname} ({count})
                    </option>
                  ))}
                </select>
              </div>

              <div className='flex items-center gap-1.5'>
                <span className='text-muted-foreground text-[11px] font-medium'>Tài nguyên:</span>
                <select
                  value={jobResourceFilter}
                  onChange={(e) => setJobResourceFilter(e.target.value as 'all' | 'gpu' | 'cpu')}
                  className='h-7 rounded-md border border-input bg-background px-2 text-xs outline-none'
                >
                  <option value='all'>Tất cả</option>
                  <option value='gpu'>⚡ Chỉ GPU CUDA</option>
                  <option value='cpu'>🖥️ Chỉ CPU</option>
                </select>
              </div>

              {(jobSearchTerm.trim() !== '' ||
                jobResourceFilter !== 'all' ||
                jobUserFilter !== 'all') && (
                <Button
                  size='sm'
                  variant='ghost'
                  onClick={() => {
                    setJobSearchTerm('');
                    setJobResourceFilter('all');
                    setJobUserFilter('all');
                  }}
                  className='h-7 text-xs text-muted-foreground hover:text-foreground'
                >
                  <Icons.close className='size-3 mr-1' /> Đặt lại
                </Button>
              )}
            </div>

            <div className='space-y-2 text-xs'>
              {filteredServerJobs.length === 0 ? (
                <div className='py-6 text-center text-xs text-muted-foreground border border-dashed rounded-lg'>
                  {serverJobs.length === 0
                    ? 'Không có tiến trình tính toán nào đang chạy trên máy chủ này.'
                    : 'Không tìm thấy tiến trình nào phù hợp với bộ lọc.'}
                </div>
              ) : (
                filteredServerJobs.map((j) => (
                  <div
                    key={j.id}
                    className='p-3 rounded-lg border bg-muted/20 flex items-center justify-between hover:bg-muted/40 transition-colors'
                  >
                    <div>
                      <div className='flex items-center gap-2'>
                        <span className='font-mono font-bold'>PID {j.pid}</span>
                        <Badge
                          variant={j.isGpu ? 'default' : 'outline'}
                          className={`text-[10px] font-mono ${
                            j.isGpu ? 'bg-indigo-600 hover:bg-indigo-700' : 'text-muted-foreground'
                          }`}
                        >
                          {j.gpuId || 'CPU Only'}
                        </Badge>
                        <span className='text-muted-foreground font-semibold'>@{j.username}</span>
                        <span className='text-[11px] text-muted-foreground'>({j.fullName})</span>
                      </div>
                      <div
                        className='font-mono text-muted-foreground text-[11px] mt-1 truncate max-w-xl'
                        title={j.cmdline || j.script}
                      >
                        {j.script}
                      </div>
                    </div>
                    <div className='text-right shrink-0 ml-2'>
                      <span className='font-mono font-bold text-primary block'>
                        {j.vramUsedMb > 0
                          ? `${(j.vramUsedMb / 1024).toFixed(1)} GB VRAM`
                          : `${j.cpuPercent}% CPU`}
                      </span>
                      <div className='text-[10px] text-muted-foreground font-mono'>
                        {j.elapsedTime}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </Card>
        </TabsContent>

        {/* TAB 8: LOGS */}
        <TabsContent value='logs' className='mt-4 space-y-3'>
          <Card className='p-4 bg-card'>
            <CardTitle className='text-sm font-bold mb-2'>
              Nhật ký Kernel & NVML (Trích xuất 5 bản ghi gần nhất)
            </CardTitle>
            <div className='bg-muted/80 p-3 rounded-lg font-mono text-[11px] space-y-1 text-muted-foreground overflow-x-auto'>
              <div>
                [45123.120] nvidia-nvml: GPU 0000:3b:00.0 memory allocation verified (21400 MB
                allocated).
              </div>
              <div>[45123.450] incus-daemon: container ws-hieulc network veth42a1 link UP.</div>
              <div>[45124.010] zfs: pool tank scrub completed with 0 errors.</div>
              <div>
                [45125.890] sshd[38192]: Accepted publickey for hieulc from 10.200.0.100 port 54122
                ssh2.
              </div>
              <div>[45129.300] nvidia-uvm: Loaded CUDA driver 550.120 with MPS enabled.</div>
            </div>
          </Card>
        </TabsContent>

        {/* TAB 9: ALERTS */}
        <TabsContent value='alerts' className='mt-4 space-y-3'>
          <Card className='p-4 bg-card'>
            <CardTitle className='text-sm font-bold mb-2'>Trạng thái Cảnh báo Hạ tầng</CardTitle>
            <div className='p-3 rounded-lg border bg-emerald-500/10 border-emerald-500/20 text-emerald-800 dark:text-emerald-300 text-xs flex items-center gap-2'>
              <Icons.check className='size-4 text-emerald-600' />
              <span>
                Máy chủ hoạt động tối ưu. Không ghi nhận lỗi quá nhiệt, sụt áp hay lỗi I/O đĩa cứng.
              </span>
            </div>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
