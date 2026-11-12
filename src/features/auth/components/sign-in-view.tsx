'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  IconCpu,
  IconShieldCheck,
  IconLock,
  IconUser,
  IconArrowRight,
  IconLoader2,
  IconAlertCircle,
  IconServer
} from '@tabler/icons-react';

interface SignInViewPageProps {
  error?: string;
}

export default function SignInViewPage({ error: initialError }: SignInViewPageProps) {
  const router = useRouter();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(initialError || null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });

      const data = await res.json();

      if (!res.ok) {
        setError(data.error || 'Tài khoản hoặc mật khẩu không chính xác');
        setLoading(false);
        return;
      }

      // Đăng nhập thành công -> chuyển hướng về Dashboard
      window.location.href = data.redirect || '/dashboard/overview';
    } catch {
      setError('Không thể kết nối tới máy chủ xác thực');
      setLoading(false);
    }
  };

  return (
    <div className='relative flex min-h-screen flex-col items-center justify-center overflow-hidden md:grid lg:max-w-none lg:grid-cols-2 lg:px-0'>
      {/* Banner giới thiệu bên trái */}
      <div className='relative hidden h-full flex-col p-10 lg:flex bg-[#0f172a] text-white border-r border-slate-800'>
        <div className='relative z-20 flex items-center text-xl font-bold tracking-tight'>
          <div className='flex size-10 items-center justify-center rounded-lg bg-blue-600 text-white mr-3 shadow-lg'>
            <IconCpu className='size-6' />
          </div>
          LabOps Control Center V2
        </div>

        <div className='relative z-20 my-auto max-w-lg space-y-5'>
          <div className='inline-flex items-center gap-2 rounded-full bg-blue-500/10 px-3.5 py-1 text-xs font-semibold text-blue-400 border border-blue-500/20'>
            <IconShieldCheck className='size-4 text-emerald-400' />
            Zero-Trust AI Cluster Architecture
          </div>

          <h1 className='text-3xl font-extrabold tracking-tight leading-snug'>
            Hệ thống Quản trị & Điều phối Cụm 4× NVIDIA A30
          </h1>

          <p className='text-slate-300 text-sm leading-relaxed'>
            Quản trị tập trung Server Rack (3× A30 24GB) và Server Workstation (1× A30 24GB) qua mạng WireGuard mã hóa, kết hợp Go Node Agent và kiểm duyệt mã nguồn Repository Guard.
          </p>

          <div className='grid grid-cols-2 gap-3 pt-2 text-xs text-slate-300'>
            <div className='flex items-center gap-2 p-2.5 rounded-lg bg-slate-900/60 border border-slate-800'>
              <IconServer className='size-4 text-blue-400' />
              <span>Rack 192.168.167.250</span>
            </div>
            <div className='flex items-center gap-2 p-2.5 rounded-lg bg-slate-900/60 border border-slate-800'>
              <IconServer className='size-4 text-emerald-400' />
              <span>WS1 192.168.167.251</span>
            </div>
          </div>
        </div>

        <div className='relative z-20 mt-auto text-xs text-slate-500'>
          © 2026 LabOps Control Center. Bản quyền phòng thí nghiệm AIoT Lab.
        </div>
      </div>

      {/* Form đăng nhập bên phải */}
      <div className='flex h-full w-full items-center justify-center p-4 lg:p-8 bg-slate-50 dark:bg-zinc-950'>
        <div className='flex w-full max-w-md flex-col items-center justify-center space-y-6 bg-white dark:bg-zinc-900 p-8 rounded-2xl border border-slate-200 dark:border-zinc-800 shadow-xl'>
          <div className='text-center space-y-2 w-full'>
            <div className='flex size-12 mx-auto items-center justify-center rounded-xl bg-blue-50 text-blue-600 dark:bg-blue-950/50 dark:text-blue-400 mb-1'>
              <IconLock className='size-6' />
            </div>
            <h2 className='text-2xl font-bold tracking-tight text-slate-900 dark:text-white'>
              Đăng nhập Quản trị viên
            </h2>
            <p className='text-xs text-slate-500'>
              Nhập tài khoản và mật khẩu hệ thống để truy cập Control Center.
            </p>
          </div>

          {error && (
            <div className='w-full flex items-center gap-2 p-3 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded-lg dark:bg-rose-950/30 dark:border-rose-900/50 dark:text-rose-400'>
              <IconAlertCircle className='size-4 shrink-0' />
              <span>{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className='w-full space-y-4'>
            <div className='space-y-1.5'>
              <label className='text-xs font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5'>
                <IconUser className='size-3.5' />
                Tài khoản Quản trị
              </label>
              <input
                type='text'
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder='admin'
                disabled={loading}
                className='w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 transition'
              />
            </div>

            <div className='space-y-1.5'>
              <label className='text-xs font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5'>
                <IconLock className='size-3.5' />
                Mật khẩu
              </label>
              <input
                type='password'
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder='Mật khẩu hệ thống'
                disabled={loading}
                className='w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 transition'
              />
            </div>

            <button
              type='submit'
              disabled={loading}
              className='w-full mt-2 py-2.5 px-4 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium text-sm shadow-md hover:shadow-lg flex items-center justify-center gap-2 transition disabled:opacity-70 cursor-pointer'
            >
              {loading ? (
                <>
                  <IconLoader2 className='size-4 animate-spin' />
                  <span>Đang xác thực...</span>
                </>
              ) : (
                <>
                  <span>Đăng nhập Hệ thống</span>
                  <IconArrowRight className='size-4' />
                </>
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
