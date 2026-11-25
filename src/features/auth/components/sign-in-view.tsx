'use client';

import React, { useState } from 'react';
import Link from 'next/link';
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
  const [mode, setMode] = useState<'login' | 'recover'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [memberCode, setMemberCode] = useState('');
  const [email, setEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(initialError || null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);
    setNotice(null);
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

  const handleRecoverPassword = async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);
    setNotice(null);

    const uname = username.trim().toLowerCase();
    if (uname === 'admin') {
      setError(
        'Chức năng lấy lại mật khẩu Web chỉ dành cho Sinh viên / Nghiên cứu viên, không áp dụng cho Quản trị viên.'
      );
      return;
    }
    if (newPassword.length < 12) {
      setError('Mật khẩu mới phải có ít nhất 12 ký tự');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('Mật khẩu xác nhận không khớp');
      return;
    }

    setLoading(true);
    try {
      const res = await fetch('/api/auth/recover-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: uname,
          member_code: memberCode.trim(),
          email: email.trim(),
          new_password: newPassword
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || 'Không thể đặt lại mật khẩu tài khoản Web');
        setLoading(false);
        return;
      }

      setNotice(
        data.message ||
          'Đã đặt lại mật khẩu tài khoản Web thành công. Hãy đăng nhập bằng mật khẩu mới.'
      );
      setPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setMode('login');
      setLoading(false);
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
            Quản trị tập trung Server Rack (3× A30 24GB) và Server Workstation (1× A30 24GB) qua
            mạng WireGuard mã hóa, kết hợp Go Node Agent và kiểm duyệt mã nguồn Repository Guard.
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
              {mode === 'login'
                ? 'Đăng nhập LabOps Control Center'
                : 'Khôi phục Mật khẩu Tài khoản Web'}
            </h2>
            <p className='text-xs text-slate-500'>
              {mode === 'login'
                ? 'Đăng nhập bằng tài khoản Quản trị viên hoặc tài khoản Nghiên cứu viên.'
                : 'Chỉ áp dụng cho Sinh viên / Nghiên cứu viên đã đăng ký trên Cổng Web (không áp dụng cho Admin).'}
            </p>
          </div>

          {notice && (
            <div className='w-full flex items-center gap-2 p-3 text-xs text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-lg dark:bg-emerald-950/30 dark:border-emerald-900/50 dark:text-emerald-300'>
              <IconShieldCheck className='size-4 shrink-0' />
              <span>{notice}</span>
            </div>
          )}

          {error && (
            <div className='w-full flex items-center gap-2 p-3 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded-lg dark:bg-rose-950/30 dark:border-rose-900/50 dark:text-rose-400'>
              <IconAlertCircle className='size-4 shrink-0' />
              <span>{error}</span>
            </div>
          )}

          {mode === 'login' ? (
            <form
              method='post'
              action='/api/auth/login'
              onSubmit={handleSubmit}
              className='w-full space-y-4'
            >
              <div className='space-y-1.5'>
                <label
                  htmlFor='login-username'
                  className='text-xs font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5'
                >
                  <IconUser className='size-3.5' />
                  Tên đăng nhập
                </label>
                <input
                  id='login-username'
                  name='username'
                  type='text'
                  autoComplete='username'
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder='admin hoặc username nghiên cứu viên'
                  disabled={loading}
                  className='w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 transition'
                />
              </div>

              <div className='space-y-1.5'>
                <div className='flex items-center justify-between'>
                  <label
                    htmlFor='admin-password'
                    className='text-xs font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5'
                  >
                    <IconLock className='size-3.5' />
                    Mật khẩu
                  </label>
                  <button
                    type='button'
                    onClick={() => {
                      setError(null);
                      setNotice(null);
                      setMode('recover');
                    }}
                    className='text-xs font-medium text-blue-600 hover:underline dark:text-blue-400 cursor-pointer'
                  >
                    Quên mật khẩu tài khoản Web?
                  </button>
                </div>
                <input
                  id='admin-password'
                  name='password'
                  type='password'
                  autoComplete='current-password'
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

              <div className='pt-2 border-t border-slate-200 dark:border-zinc-800 space-y-2 text-center'>
                <p className='text-xs text-slate-500 dark:text-slate-400'>
                  Thành viên mới chưa có tài khoản cổng Web?
                </p>
                <Link
                  href='/auth/sign-up'
                  className='w-full py-2.5 px-4 rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 hover:bg-slate-50 dark:hover:bg-zinc-700 text-slate-800 dark:text-slate-100 font-medium text-sm flex items-center justify-center gap-2 transition'
                >
                  <span>Đăng ký tài khoản</span>
                </Link>
              </div>
            </form>
          ) : (
            <form onSubmit={handleRecoverPassword} className='w-full space-y-3.5'>
              <div className='space-y-1'>
                <label
                  htmlFor='recover-username'
                  className='text-xs font-semibold text-slate-700 dark:text-slate-300'
                >
                  Tên đăng nhập Web *
                </label>
                <input
                  id='recover-username'
                  type='text'
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder='vd: sv_nguyenhoabinh'
                  disabled={loading}
                  className='w-full px-3.5 py-2 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500'
                />
              </div>

              <div className='space-y-1'>
                <label
                  htmlFor='recover-member-code'
                  className='text-xs font-semibold text-slate-700 dark:text-slate-300'
                >
                  Mã Sinh viên / Mã Giảng viên đã đăng ký *
                </label>
                <input
                  id='recover-member-code'
                  type='text'
                  required
                  value={memberCode}
                  onChange={(e) => setMemberCode(e.target.value)}
                  placeholder='vd: 1671040004'
                  disabled={loading}
                  className='w-full px-3.5 py-2 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500'
                />
              </div>

              <div className='space-y-1'>
                <label
                  htmlFor='recover-email'
                  className='text-xs font-semibold text-slate-700 dark:text-slate-300'
                >
                  Email đã đăng ký *
                </label>
                <input
                  id='recover-email'
                  type='email'
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder='vd: binhneymar10@gmail.com'
                  disabled={loading}
                  className='w-full px-3.5 py-2 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500'
                />
              </div>

              <div className='space-y-1'>
                <label
                  htmlFor='recover-new-password'
                  className='text-xs font-semibold text-slate-700 dark:text-slate-300'
                >
                  Mật khẩu Web mới (tối thiểu 12 ký tự) *
                </label>
                <input
                  id='recover-new-password'
                  type='password'
                  required
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder='Tối thiểu 12 ký tự'
                  disabled={loading}
                  className='w-full px-3.5 py-2 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500'
                />
              </div>

              <div className='space-y-1'>
                <label
                  htmlFor='recover-confirm-password'
                  className='text-xs font-semibold text-slate-700 dark:text-slate-300'
                >
                  Xác nhận mật khẩu Web mới *
                </label>
                <input
                  id='recover-confirm-password'
                  type='password'
                  required
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder='Nhập lại mật khẩu mới'
                  disabled={loading}
                  className='w-full px-3.5 py-2 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500'
                />
              </div>

              <button
                type='submit'
                disabled={loading}
                className='w-full py-2.5 px-4 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium text-sm shadow-md flex items-center justify-center gap-2 transition disabled:opacity-70 cursor-pointer'
              >
                {loading ? (
                  <>
                    <IconLoader2 className='size-4 animate-spin' />
                    <span>Đang đặt lại mật khẩu...</span>
                  </>
                ) : (
                  <span>Xác nhận Đặt lại Mật khẩu Web</span>
                )}
              </button>

              <button
                type='button'
                disabled={loading}
                onClick={() => {
                  setError(null);
                  setMode('login');
                }}
                className='w-full py-2 px-4 rounded-lg border border-slate-300 dark:border-zinc-700 text-xs font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-zinc-800 cursor-pointer'
              >
                ← Quay lại Đăng nhập
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
