'use client';

import { buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import Link from 'next/link';
import { IconCpu, IconShieldCheck, IconArrowRight, IconLock } from '@tabler/icons-react';

export default function SignUpViewPage() {
  return (
    <div className='relative flex min-h-screen flex-col items-center justify-center overflow-hidden md:grid lg:max-w-none lg:grid-cols-2 lg:px-0'>
      <div className='relative hidden h-full flex-col p-10 lg:flex dark:border-r bg-[#10233F] text-white'>
        <div className='relative z-20 flex items-center text-xl font-bold tracking-tight'>
          <div className='flex size-10 items-center justify-center rounded-lg bg-blue-600 text-white mr-3 shadow-md'>
            <IconCpu className='size-6' />
          </div>
          LabOps Control Center
        </div>

        <div className='relative z-20 my-auto max-w-lg space-y-4'>
          <div className='inline-flex items-center gap-2 rounded-full bg-blue-500/20 px-3 py-1 text-xs font-semibold text-blue-300 border border-blue-400/30'>
            <IconShieldCheck className='size-4' />
            Chính sách Cấp quyền Tài khoản Nghiên cứu
          </div>
          <h1 className='text-3xl font-extrabold tracking-tight'>
            Tài khoản Nghiên cứu được Cấp phát bởi Quản trị viên
          </h1>
          <p className='text-slate-300 text-sm leading-relaxed'>
            Để bảo mật tài nguyên GPU NVIDIA A30 và lưu trữ ZFS, tài khoản không mở đăng ký tự do.
            Vui lòng liên hệ Quản trị viên Phòng Lab để được khởi tạo Workspace Incus và cấp SSH
            key.
          </p>
        </div>

        <div className='relative z-20 mt-auto text-xs text-slate-400'>
          © 2026 LabOps Infrastructure Management. Bảo mật cấp phòng thí nghiệm.
        </div>
      </div>

      <div className='flex h-full items-center justify-center p-6 lg:p-12 bg-background'>
        <Card className='w-full max-w-md shadow-lg border-muted/80'>
          <CardHeader className='text-center space-y-1 pb-4'>
            <div className='mx-auto size-12 rounded-full bg-primary/10 flex items-center justify-center text-primary mb-2'>
              <IconLock className='size-6' />
            </div>
            <CardTitle className='text-xl font-bold'>Đăng ký Thành viên Nghiên cứu</CardTitle>
            <CardDescription className='text-xs'>
              Quy trình cấp tài khoản thực hiện qua quy trình kiểm duyệt nội bộ
            </CardDescription>
          </CardHeader>
          <CardContent className='space-y-4 text-xs'>
            <div className='p-3.5 rounded-lg border bg-muted/30 text-muted-foreground space-y-2 leading-relaxed'>
              <p className='font-semibold text-foreground'>Các bước nhận tài khoản tính toán:</p>
              <p>1. Gửi đề xuất đề tài nghiên cứu và nhu cầu VRAM GPU cho Quản trị viên Lab.</p>
              <p>
                2. Quản trị viên sử dụng wizard <strong>Thành viên → Thêm thành viên</strong> để tạo
                container unprivileged và mount thư mục ZFS riêng.
              </p>
              <p>3. Nhận cặp khóa SSH Ed25519 và cổng truy cập riêng (22000-29999) qua VPN.</p>
            </div>

            <div className='pt-2 flex flex-col gap-2'>
              <Link
                href='/auth/sign-in'
                className={cn(
                  buttonVariants({ variant: 'default' }),
                  'w-full text-xs font-semibold'
                )}
              >
                Quay lại Đăng nhập Quản trị <IconArrowRight className='size-3.5 ml-1' />
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
