'use client';

import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { ChartConfig, ChartContainer } from '@/components/ui/chart';
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, CartesianGrid } from 'recharts';

const chartConfig = {
  track: {
    label: 'Server Track (3x A30)',
    color: 'oklch(0.55 0.22 260)'
  },
  workstation: {
    label: 'Server Workstation (1x A30)',
    color: 'oklch(0.58 0.22 295)'
  },
  hours: {
    label: 'Số giờ GPU',
    color: 'oklch(0.55 0.22 260)'
  }
} satisfies ChartConfig;

interface ChartTooltipProps {
  active?: boolean;
  payload?: ReadonlyArray<{ value?: React.ReactNode; payload?: Record<string, unknown> }>;
  label?: React.ReactNode;
}

const TimelineTooltip = ({ active, payload, label }: ChartTooltipProps) => {
  if (active && payload && payload.length) {
    return (
      <div className='rounded-lg border bg-background p-2.5 shadow-md text-xs space-y-1'>
        <div className='font-bold text-foreground'>Ngày: {label}</div>
        <div className='flex items-center gap-2 text-blue-600 dark:text-blue-400'>
          <span className='size-2 rounded-full bg-blue-600' />
          <span>Server Track: {payload[0]?.value}h</span>
        </div>
        <div className='flex items-center gap-2 text-purple-600 dark:text-purple-400'>
          <span className='size-2 rounded-full bg-purple-600' />
          <span>Workstation: {payload[1]?.value}h</span>
        </div>
        <div className='border-t pt-1 font-semibold text-foreground'>
          Tổng cộng: {((payload[0]?.value as number) || 0) + ((payload[1]?.value as number) || 0)}h
        </div>
      </div>
    );
  }
  return null;
};

const UserTooltip = ({ active, payload }: ChartTooltipProps) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload as { user: string; server: string; hours: number };
    return (
      <div className='rounded-lg border bg-background p-2.5 shadow-md text-xs space-y-1'>
        <div className='font-bold text-foreground'>{data.user}</div>
        <div>Máy chủ: {data.server}</div>
        <div className='font-mono font-semibold text-primary'>Tổng số: {data.hours} GPU-hours</div>
      </div>
    );
  }
  return null;
};

const DAILY_USAGE_DATA = [
  { day: '28/10', track: 48.2, workstation: 14.5, total: 62.7 },
  { day: '29/10', track: 52.1, workstation: 16.0, total: 68.1 },
  { day: '30/10', track: 61.4, workstation: 18.2, total: 79.6 },
  { day: '31/10', track: 58.0, workstation: 12.4, total: 70.4 },
  { day: '01/11', track: 44.5, workstation: 15.1, total: 59.6 },
  { day: '02/11', track: 39.8, workstation: 11.2, total: 51.0 },
  { day: '03/11', track: 43.6, workstation: 14.8, total: 58.4 }
];

const USER_DISTRIBUTION_DATA = [
  { user: 'Lê Chí Hiếu', hours: 142.5, server: 'Track' },
  { user: 'Hoàng Đức Anh', hours: 110.8, server: 'Track' },
  { user: 'Nguyễn Văn Nam', hours: 98.2, server: 'Track' },
  { user: 'Trần Thị Lan', hours: 76.4, server: 'Workstation' },
  { user: 'Phạm Minh Tuấn', hours: 54.0, server: 'Track' },
  { user: 'Vũ Mai Linh', hours: 32.1, server: 'Workstation' }
];

const legendFormatter = (value: string) =>
  value === 'track' ? 'Server Track (3x A30)' : 'Server Workstation (1x A30)';

export function GpuHoursChart() {
  const [viewMode, setViewMode] = useState<'timeline' | 'users'>('timeline');

  if (process.env.NEXT_PUBLIC_DEMO_MODE !== 'true') {
    return (
      <Card className='border bg-card min-w-0 w-full'>
        <CardHeader>
          <CardTitle>GPU-hours</CardTitle>
          <CardDescription>Chưa có bộ thu thập GPU-hours đã xác minh.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const handleExportCsv = () => {
    const csvContent =
      'data:text/csv;charset=utf-8,' +
      'Ngay,Server_Track_Hours,Server_Workstation_Hours,Total_Hours\n' +
      DAILY_USAGE_DATA.map((row) => `${row.day},${row.track},${row.workstation},${row.total}`).join(
        '\n'
      );

    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `labops_gpu_hours_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <Card className='border bg-card min-w-0 w-full'>
      <CardHeader className='pb-2 pt-4'>
        <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2'>
          <div>
            <div className='flex items-center gap-2'>
              <Icons.clock className='size-5 text-amber-500' />
              <CardTitle className='text-base font-bold tracking-tight'>
                Phân tích Tiêu thụ GPU-Hours (7 Ngày Qua)
              </CardTitle>
            </div>
            <CardDescription className='text-xs mt-0.5'>
              Tổng cộng: <span className='font-bold text-foreground font-mono'>412.8 giờ</span> tính
              toán • Tăng 14.2% so với tuần trước
            </CardDescription>
          </div>

          <div className='flex items-center gap-2'>
            <div className='inline-flex rounded-md border p-0.5 bg-muted/40'>
              <Button
                variant={viewMode === 'timeline' ? 'secondary' : 'ghost'}
                size='sm'
                onClick={() => setViewMode('timeline')}
                className='h-7 text-xs px-2.5 font-medium'
              >
                Theo ngày
              </Button>
              <Button
                variant={viewMode === 'users' ? 'secondary' : 'ghost'}
                size='sm'
                onClick={() => setViewMode('users')}
                className='h-7 text-xs px-2.5 font-medium'
              >
                Theo thành viên
              </Button>
            </div>

            <Button
              variant='outline'
              size='sm'
              onClick={handleExportCsv}
              className='h-7 text-xs gap-1 font-medium'
            >
              <Icons.download className='size-3.5' /> Xuất CSV
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent className='pt-2 pb-4'>
        <div className='h-[260px] w-full'>
          <ChartContainer config={chartConfig} className='aspect-auto h-[260px] w-full'>
            {viewMode === 'timeline' ? (
              <BarChart
                data={DAILY_USAGE_DATA}
                margin={{ top: 10, right: 10, left: -20, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray='3 3' vertical={false} opacity={0.2} />
                <XAxis dataKey='day' tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
                <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 11 }} unit='h' />
                <Tooltip content={TimelineTooltip} />
                <Legend
                  wrapperStyle={{ fontSize: 11, paddingTop: 6 }}
                  formatter={legendFormatter}
                />
                <Bar
                  dataKey='track'
                  stackId='a'
                  fill='oklch(0.55 0.22 260)'
                  radius={[0, 0, 0, 0]}
                  isAnimationActive={false}
                />
                <Bar
                  dataKey='workstation'
                  stackId='a'
                  fill='oklch(0.58 0.22 295)'
                  radius={[4, 4, 0, 0]}
                  isAnimationActive={false}
                />
              </BarChart>
            ) : (
              <BarChart
                data={USER_DISTRIBUTION_DATA}
                layout='vertical'
                margin={{ top: 10, right: 20, left: 40, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray='3 3' horizontal={false} opacity={0.2} />
                <XAxis
                  type='number'
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 11 }}
                  unit='h'
                />
                <YAxis
                  dataKey='user'
                  type='category'
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 11 }}
                />
                <Tooltip content={UserTooltip} />
                <Bar
                  dataKey='hours'
                  fill='oklch(0.55 0.22 260)'
                  radius={[0, 4, 4, 0]}
                  isAnimationActive={false}
                />
              </BarChart>
            )}
          </ChartContainer>
        </div>

        <div className='flex items-center gap-2 mt-2 pt-2 border-t text-[11px] text-muted-foreground'>
          <Icons.info className='size-3.5 text-primary shrink-0' />
          <span>
            <strong>Định nghĩa tính toán:</strong> GPU-Hours được đo lường dựa trên thời gian GPU có
            tiến trình tính toán CUDA hoạt động (Compute Active Hours), không tính thời gian giữ chỗ
            rỗng.
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
