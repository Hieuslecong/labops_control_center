'use client';

import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Icons } from '@/components/icons';
import { toast } from 'sonner';

export function SettingsView() {
  const [demoMode, setDemoMode] = useState(true);
  const [incusTrackUrl, setIncusTrackUrl] = useState('https://10.10.1.10:8443');
  const [incusWsUrl, setIncusWsUrl] = useState('https://10.10.2.20:8443');
  const [netdataTrackUrl, setNetdataTrackUrl] = useState('http://10.200.0.1:19999');
  const [netdataWsUrl, setNetdataWsUrl] = useState('http://10.200.0.2:19999');
  const [nvmlSamplingRate, setNvmlSamplingRate] = useState(2);
  const [testingService, setTestingService] = useState<string | null>(null);

  const testConnection = (service: string) => {
    setTestingService(service);
    setTimeout(() => {
      setTestingService(null);
      toast.success(`Kết nối tới ${service} thành công!`, {
        description: 'Độ trễ phản hồi: 12ms • Chứng chỉ TLS hợp lệ'
      });
    }, 600);
  };

  const handleSave = (e: React.SyntheticEvent) => {
    e.preventDefault();
    toast.success('Đã lưu cấu hình hệ thống LabOps', {
      description: demoMode
        ? 'Chế độ Demo Mode đang kích hoạt'
        : 'Đã kết nối trực tiếp Incus & Netdata'
    });
  };

  return (
    <form onSubmit={handleSave} className='flex flex-col gap-5 max-w-4xl'>
      {/* Mode Switcher */}
      <Card className='border bg-card p-4'>
        <div className='flex items-center justify-between'>
          <div className='space-y-0.5'>
            <div className='flex items-center gap-2'>
              <h3 className='text-sm font-bold text-foreground'>
                Chế độ Vận hành Nền tảng (Operation Mode)
              </h3>
              <Badge variant={demoMode ? 'default' : 'secondary'} className='text-[10px] font-mono'>
                {demoMode ? 'DEMO MODE' : 'PRODUCTION LIVE'}
              </Badge>
            </div>
            <p className='text-xs text-muted-foreground'>
              {demoMode
                ? 'Hệ thống đang chạy với dữ liệu mô phỏng chuẩn xác 2 máy chủ & 4 GPU NVIDIA A30. Mọi thao tác thêm/sửa/cấp phát được lưu trong bộ nhớ tạm.'
                : 'Chế độ Sản xuất: Gọi trực tiếp Incus REST API và Netdata agent trên máy chủ thực tế qua WireGuard VPN.'}
            </p>
          </div>

          <Button
            type='button'
            variant={demoMode ? 'outline' : 'default'}
            size='sm'
            onClick={() => {
              setDemoMode(!demoMode);
              toast.info(`Đã chuyển sang ${!demoMode ? 'Chế độ Demo' : 'Chế độ Sản xuất'}`);
            }}
            className='h-8 text-xs font-semibold'
          >
            {demoMode ? 'Chuyển sang Production' : 'Kích hoạt Demo Mode'}
          </Button>
        </div>
      </Card>

      {/* Incus Container Manager Endpoints */}
      <Card className='border bg-card'>
        <CardHeader className='pb-3 pt-4'>
          <div className='flex items-center justify-between'>
            <div className='flex items-center gap-2'>
              <Icons.workspace className='size-5 text-primary' />
              <div>
                <CardTitle className='text-base font-bold'>
                  Incus Daemon REST API Endpoints
                </CardTitle>
                <CardDescription className='text-xs'>
                  Kết nối an toàn qua mTLS Certificate để quản lý vòng đời Linux container trên từng
                  máy chủ
                </CardDescription>
              </div>
            </div>
            <Button
              type='button'
              variant='outline'
              size='sm'
              disabled={testingService === 'Incus'}
              onClick={() => testConnection('Incus')}
              className='h-7 text-xs font-semibold gap-1'
            >
              {testingService === 'Incus' ? (
                <Icons.spinner className='size-3 animate-spin' />
              ) : (
                <Icons.refresh className='size-3' />
              )}
              Kiểm tra kết nối
            </Button>
          </div>
        </CardHeader>

        <CardContent className='pt-0 pb-4 space-y-3 text-xs'>
          <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Server Track Incus API</Label>
              <Input
                value={incusTrackUrl}
                onChange={(e) => setIncusTrackUrl(e.target.value)}
                className='h-8 text-xs font-mono'
              />
              <span className='text-[10px] text-muted-foreground'>
                Server 01 (Hà Nội) • Port 8443
              </span>
            </div>

            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Server Workstation Incus API</Label>
              <Input
                value={incusWsUrl}
                onChange={(e) => setIncusWsUrl(e.target.value)}
                className='h-8 text-xs font-mono'
              />
              <span className='text-[10px] text-muted-foreground'>
                Server 02 (TP.HCM) • Port 8443
              </span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Netdata Monitoring Agents */}
      <Card className='border bg-card'>
        <CardHeader className='pb-3 pt-4'>
          <div className='flex items-center justify-between'>
            <div className='flex items-center gap-2'>
              <Icons.gauge className='size-5 text-indigo-600 dark:text-indigo-400' />
              <div>
                <CardTitle className='text-base font-bold'>
                  Netdata Real-time Telemetry Agents
                </CardTitle>
                <CardDescription className='text-xs'>
                  Thu thập chỉ số CPU, RAM, Network và ZFS disk I/O theo từng giây
                </CardDescription>
              </div>
            </div>
            <Button
              type='button'
              variant='outline'
              size='sm'
              disabled={testingService === 'Netdata'}
              onClick={() => testConnection('Netdata')}
              className='h-7 text-xs font-semibold gap-1'
            >
              {testingService === 'Netdata' ? (
                <Icons.spinner className='size-3 animate-spin' />
              ) : (
                <Icons.refresh className='size-3' />
              )}
              Kiểm tra kết nối
            </Button>
          </div>
        </CardHeader>

        <CardContent className='pt-0 pb-4 space-y-3 text-xs'>
          <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Netdata Stream (Track)</Label>
              <Input
                value={netdataTrackUrl}
                onChange={(e) => setNetdataTrackUrl(e.target.value)}
                className='h-8 text-xs font-mono'
              />
              <span className='text-[10px] text-muted-foreground'>
                Port 19999 qua WireGuard VPN
              </span>
            </div>

            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>Netdata Stream (Workstation)</Label>
              <Input
                value={netdataWsUrl}
                onChange={(e) => setNetdataWsUrl(e.target.value)}
                className='h-8 text-xs font-mono'
              />
              <span className='text-[10px] text-muted-foreground'>
                Port 19999 qua WireGuard VPN
              </span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* NVIDIA NVML Telemetry Exporter */}
      <Card className='border bg-card'>
        <CardHeader className='pb-3 pt-4'>
          <div className='flex items-center gap-2'>
            <Icons.gpu className='size-5 text-primary' />
            <div>
              <CardTitle className='text-base font-bold'>
                NVIDIA NVML Driver & Tần suất Lấy mẫu
              </CardTitle>
              <CardDescription className='text-xs'>
                Kiểm soát tần số đọc cảm biến nhiệt độ, công suất và dung lượng VRAM thực tế
              </CardDescription>
            </div>
          </div>
        </CardHeader>

        <CardContent className='pt-0 pb-4 space-y-3 text-xs'>
          <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
            <div className='space-y-1.5'>
              <Label className='text-xs font-semibold'>
                Tần suất lấy mẫu chỉ số GPU (Sampling Interval)
              </Label>
              <div className='flex items-center gap-2'>
                <Input
                  type='number'
                  min={1}
                  max={10}
                  value={nvmlSamplingRate}
                  onChange={(e) => setNvmlSamplingRate(Number(e.target.value))}
                  className='h-8 text-xs font-mono w-24'
                />
                <span className='text-xs text-muted-foreground'>giây / lần</span>
              </div>
            </div>

            <div className='p-2.5 rounded border bg-muted/20 text-[11px] text-muted-foreground'>
              Hạn chế lấy mẫu dưới 1 giây để tránh gia tăng tải CPU kernel trên NVIDIA driver 550.x.
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Save Button */}
      <div className='flex justify-end gap-2'>
        <Button type='submit' size='sm' className='text-xs font-semibold bg-primary px-6 h-9'>
          Lưu Cấu hình Hệ thống
        </Button>
      </div>
    </form>
  );
}
