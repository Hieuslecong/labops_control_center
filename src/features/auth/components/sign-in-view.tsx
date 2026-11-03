import { buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { Metadata } from 'next';
import Link from 'next/link';
import { IconCpu, IconShieldCheck, IconArrowRight } from '@tabler/icons-react';

export const metadata: Metadata = {
  title: 'Đăng nhập | LabOps Control Center',
  description: 'Trang xác thực quản trị viên LabOps Control Center'
};

export default function SignInViewPage() {
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
            Nền tảng Quản trị Tính toán AI Độc quyền
          </div>
          <h1 className='text-3xl font-extrabold tracking-tight'>
            Hệ thống Quản lý Cụm 4x NVIDIA A30 & Incus Workspaces
          </h1>
          <p className='text-slate-300 text-sm leading-relaxed'>
            Quản trị tập trung 2 máy chủ vật lý Server Track (Hà Nội, 72GB VRAM) và Server
            Workstation (TP.HCM, 24GB VRAM) qua kênh truyền mã hóa WireGuard.
          </p>
        </div>

        <div className='relative z-20 mt-auto text-xs text-slate-400'>
          © 2026 LabOps Infrastructure Management. Bảo mật cấp phòng thí nghiệm.
        </div>
      </div>

      <div className='flex h-full items-center justify-center p-4 lg:p-8 bg-[#F5F7FB] dark:bg-zinc-950'>
        <div className='flex w-full max-w-md flex-col items-center justify-center space-y-6 bg-white dark:bg-zinc-900 p-8 rounded-xl border border-slate-200 dark:border-zinc-800 shadow-xl'>
          <div className='text-center space-y-2'>
            <div className='flex size-12 mx-auto items-center justify-center rounded-xl bg-blue-50 text-blue-600 dark:bg-blue-950/50 dark:text-blue-400 mb-2'>
              <IconCpu className='size-7' />
            </div>
            <h2 className='text-2xl font-bold tracking-tight text-slate-900 dark:text-white'>
              Đăng nhập Quản trị viên
            </h2>
            <p className='text-sm text-slate-500'>
              Hệ thống đang hoạt động ở chế độ Quản trị / Demo Mode.
            </p>
          </div>

          <div className='w-full space-y-4 pt-2'>
            <div className='rounded-lg bg-blue-50/70 p-4 border border-blue-100 dark:bg-blue-950/30 dark:border-blue-900/40 text-xs text-blue-800 dark:text-blue-300 space-y-1.5'>
              <div className='font-semibold flex items-center gap-1.5'>
                <span className='size-2 rounded-full bg-emerald-500 animate-pulse' />
                Tài khoản mặc định:
              </div>
              <p>
                Email: <code className='font-mono font-bold'>admin@labops.local</code>
              </p>
              <p>
                Quyền hạn:{' '}
                <span className='font-semibold'>System Administrator (Full Root RBAC)</span>
              </p>
            </div>

            <Link
              href='/dashboard/overview'
              className={cn(
                buttonVariants({ size: 'lg' }),
                'w-full bg-blue-600 hover:bg-blue-700 text-white font-medium shadow-md flex items-center justify-center gap-2'
              )}
            >
              Truy cập Bảng Điều Khiển
              <IconArrowRight className='size-4' />
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
