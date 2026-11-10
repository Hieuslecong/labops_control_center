'use client';

import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export function SettingsView() {
  const isDemoMode = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';

  return (
    <div className='flex max-w-4xl flex-col gap-5'>
      <Card>
        <CardHeader>
          <div className='flex items-center justify-between gap-3'>
            <div>
              <CardTitle>Cấu hình LabOps</CardTitle>
              <CardDescription>Cấu hình triển khai được quản lý qua environment và deployment, chưa có API lưu từ dashboard.</CardDescription>
            </div>
            <Badge variant='outline'>{isDemoMode ? 'UI DEMO' : 'PRODUCTION CONFIG — NOT VERIFIED'}</Badge>
          </div>
        </CardHeader>
        <CardContent className='space-y-3 text-sm text-muted-foreground'>
          <p>Các form kiểm tra Incus/Netdata trước đây chỉ tạo thông báo thành công cục bộ. Chúng đã được gỡ để không nhầm với phép thử kết nối thật.</p>
          <p>Trang này không thay đổi WireGuard, Teleport, firewall, GPU hoặc host. Trạng thái access hiện có trong User Management được đọc từ backend.</p>
          <Button nativeButton={false} render={<Link href='/dashboard/users' aria-label='Mở trạng thái truy cập' />}>Mở trạng thái truy cập</Button>
        </CardContent>
      </Card>
    </div>
  );
}
