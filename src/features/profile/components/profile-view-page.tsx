'use client';

import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuthSession } from '@/hooks/use-auth-session';

export default function ProfileViewPage() {
  const { session, user, role, isLoading, logout } = useAuthSession();
  const isAuthenticated = Boolean(session?.authenticated);

  return (
    <div className='flex w-full max-w-4xl flex-col gap-6 p-6'>
      <div>
        <h1 className='text-2xl font-bold tracking-tight'>Hồ sơ phiên đăng nhập</h1>
        <p className='text-sm text-muted-foreground'>
          Thông tin danh tính và vai trò được xác thực trực tiếp từ phiên LabOps phía máy chủ.
        </p>
      </div>
      <Card>
        <CardHeader>
          <div className='flex items-center justify-between gap-3'>
            <div>
              <CardTitle className='text-base'>
                {isLoading ? 'Đang xác minh phiên...' : user.fullName}
              </CardTitle>
              <CardDescription className='font-mono text-xs'>
                {user.username ? `@${user.username}` : 'Chưa có tên tài khoản'} • Vai trò:{' '}
                {role === 'ADMIN' ? 'Quản trị viên (ADMIN)' : 'Nghiên cứu viên (RESEARCHER)'}
              </CardDescription>
            </div>
            <Badge
              variant='outline'
              className={
                isAuthenticated
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 text-xs font-mono'
                  : 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400 text-xs font-mono'
              }
            >
              {isAuthenticated ? 'PHIÊN ĐÃ XÁC THỰC' : 'CHƯA XÁC THỰC'}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className='space-y-4 text-sm text-muted-foreground'>
          <p>
            Cookie phiên LabOps được ký HMAC-SHA256 phía máy chủ và kiểm tra hiệu lực cùng cơ chế
            thu hồi JTI trên Backend FastAPI.
          </p>
          <p>
            Không có SSH private key hoặc thông tin mật khẩu đăng nhập Linux được lưu hoặc hiển thị
            tại giao diện Web.
          </p>
          <div className='flex flex-wrap items-center gap-2 pt-1'>
            {role === 'ADMIN' ? (
              <>
                <Button
                  nativeButton={false}
                  render={<Link href='/dashboard/users' aria-label='Quản lý quyền truy cập' />}
                >
                  Quản lý thành viên &amp; truy cập
                </Button>
                <Button
                  variant='outline'
                  nativeButton={false}
                  render={<Link href='/dashboard/settings' aria-label='Cài đặt hệ thống' />}
                >
                  Cài đặt hệ thống
                </Button>
              </>
            ) : (
              <Button
                nativeButton={false}
                render={
                  <Link href='/dashboard/researcher/connect' aria-label='Kết nối và đổi mật khẩu' />
                }
              >
                Kết nối &amp; Đổi mật khẩu
              </Button>
            )}
            <Button variant='ghost' onClick={() => void logout()}>
              Đăng xuất
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
