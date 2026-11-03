'use client';

import React from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Icons } from '@/components/icons';
import { toast } from 'sonner';

export default function ProfileViewPage() {
  const [isSaving, setIsSaving] = React.useState(false);

  const handleSave = (e: React.SyntheticEvent) => {
    e.preventDefault();
    setIsSaving(true);
    setTimeout(() => {
      setIsSaving(false);
      toast.success('Đã lưu thông tin tài khoản thành công');
    }, 400);
  };

  return (
    <div className='flex w-full flex-col gap-6 p-6 max-w-4xl'>
      <div>
        <h1 className='text-2xl font-bold tracking-tight'>Thông tin Tài khoản Quản trị</h1>
        <p className='text-sm text-muted-foreground'>
          Quản lý danh tính, quyền điều hành hạ tầng LabOps và khóa xác thực SSH cá nhân
        </p>
      </div>

      <div className='grid grid-cols-1 md:grid-cols-3 gap-6'>
        {/* User Card */}
        <Card className='md:col-span-1 bg-card'>
          <CardHeader className='text-center pb-2'>
            <div className='mx-auto size-20 rounded-full bg-primary/10 border-2 border-primary/20 flex items-center justify-center text-primary text-2xl font-bold'>
              HA
            </div>
            <CardTitle className='text-lg font-bold mt-2'>Quản trị viên LabOps</CardTitle>
            <CardDescription className='text-xs'>admin@labops.local</CardDescription>
            <div className='flex justify-center gap-1.5 mt-2'>
              <Badge
                variant='outline'
                className='bg-primary/10 text-primary text-[10px] font-semibold'
              >
                Super Admin
              </Badge>
              <Badge
                variant='outline'
                className='bg-emerald-500/10 text-emerald-600 border-emerald-500/30 text-[10px] font-mono'
              >
                Online
              </Badge>
            </div>
          </CardHeader>
          <CardContent className='pt-2 text-xs text-muted-foreground space-y-2 border-t mt-3'>
            <div className='flex justify-between py-1'>
              <span>Vai trò hệ thống:</span>
              <span className='font-semibold text-foreground'>Infrastructure Owner</span>
            </div>
            <div className='flex justify-between py-1'>
              <span>Quyền Host Sudo:</span>
              <span className='font-semibold text-emerald-600'>FULL HOST ACCESS</span>
            </div>
            <div className='flex justify-between py-1'>
              <span>Phạm vi cụm:</span>
              <span className='font-semibold text-foreground'>Track & Workstation</span>
            </div>
            <div className='flex justify-between py-1'>
              <span>Phiên làm việc:</span>
              <span className='font-mono text-foreground'>WireGuard VPN (Active)</span>
            </div>
          </CardContent>
        </Card>

        {/* Profile Details Form */}
        <Card className='md:col-span-2 bg-card'>
          <CardHeader>
            <CardTitle className='text-base font-bold'>Chi tiết Hồ sơ</CardTitle>
            <CardDescription className='text-xs'>
              Cập nhật thông tin hiển thị và cấu hình tài khoản
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSave} className='space-y-4 text-xs'>
              <div className='grid grid-cols-1 sm:grid-cols-2 gap-4'>
                <div className='space-y-1.5'>
                  <Label htmlFor='fullName' className='text-xs font-semibold'>
                    Họ và tên
                  </Label>
                  <Input id='fullName' defaultValue='Quản trị viên LabOps' />
                </div>
                <div className='space-y-1.5'>
                  <Label htmlFor='username' className='text-xs font-semibold'>
                    Tên đăng nhập (Linux User)
                  </Label>
                  <Input
                    id='username'
                    defaultValue='labops_admin'
                    disabled
                    className='bg-muted/40 font-mono'
                  />
                </div>
              </div>

              <div className='space-y-1.5'>
                <Label htmlFor='email' className='text-xs font-semibold'>
                  Email liên hệ
                </Label>
                <Input id='email' type='email' defaultValue='admin@labops.local' />
              </div>

              <div className='space-y-1.5'>
                <Label htmlFor='sshPubKey' className='text-xs font-semibold'>
                  Khóa SSH Ed25519 Quản trị (Root Host Key)
                </Label>
                <textarea
                  id='sshPubKey'
                  readOnly
                  rows={3}
                  className='w-full font-mono text-[11px] p-2.5 rounded-md border bg-muted/30 text-foreground resize-none focus:outline-none'
                  defaultValue='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOrX8N4+gL8N88Pq4J8p0U1+s0J7cQ9bE8k3V1xY2zL8 admin@labops-controller'
                />
              </div>

              <div className='flex justify-end pt-2'>
                <Button
                  type='submit'
                  size='sm'
                  disabled={isSaving}
                  className='h-8 text-xs font-medium'
                >
                  {isSaving ? (
                    <>
                      <Icons.spinner className='mr-1.5 size-3.5 animate-spin' />
                      Đang lưu...
                    </>
                  ) : (
                    <>
                      <Icons.check className='mr-1.5 size-3.5' /> Lưu thay đổi
                    </>
                  )}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
