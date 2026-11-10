'use client';

import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function ProfileViewPage() {
  return (
    <div className='flex w-full max-w-4xl flex-col gap-6 p-6'>
      <div>
        <h1 className='text-2xl font-bold tracking-tight'>Hồ sơ quản trị</h1>
        <p className='text-sm text-muted-foreground'>Danh tính và đặc quyền chỉ hiển thị khi được đọc từ identity provider.</p>
      </div>
      <Card>
        <CardHeader>
          <div className='flex items-center justify-between gap-3'>
            <div>
              <CardTitle className='text-base'>Admin identity</CardTitle>
              <CardDescription>Trang này chưa có kết nối profile với Clerk.</CardDescription>
            </div>
            <Badge variant='outline'>NOT VERIFIED</Badge>
          </div>
        </CardHeader>
        <CardContent className='space-y-2 text-sm text-muted-foreground'>
          <p>Role, WebAuthn MFA, host sudo và phiên WireGuard chưa được xác minh tại trang hồ sơ này.</p>
          <p>Không có SSH private key hoặc thông tin đăng nhập Linux được lưu hay hiển thị ở đây.</p>
          <Button nativeButton={false} render={<Link href='/dashboard/users' aria-label='Quản lý quyền truy cập' />}>Quản lý quyền truy cập</Button>
        </CardContent>
      </Card>
    </div>
  );
}
