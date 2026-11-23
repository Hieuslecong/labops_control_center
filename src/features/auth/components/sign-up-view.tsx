'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  IconAlertCircle,
  IconArrowLeft,
  IconCheck,
  IconCpu,
  IconLoader2,
  IconUserPlus,
  IconX
} from '@tabler/icons-react';

interface UsernameCheckState {
  checking: boolean;
  checkedValue: string;
  available: boolean | null;
  message: string;
}

export default function SignUpViewPage() {
  const [memberCategory, setMemberCategory] = useState<'STUDENT' | 'LECTURER'>('STUDENT');
  const [username, setUsername] = useState('');
  const [fullName, setFullName] = useState('');
  const [memberCode, setMemberCode] = useState('');
  const [email, setEmail] = useState('');
  const [departmentClass, setDepartmentClass] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [usernameCheck, setUsernameCheck] = useState<UsernameCheckState>({
    checking: false,
    checkedValue: '',
    available: null,
    message: ''
  });

  // Tự động kiểm tra Username trùng hay chưa (debounce 250ms)
  useEffect(() => {
    const trimmed = username.trim().toLowerCase();
    if (!trimmed) {
      return;
    }

    let cancelled = false;
    const timer = setTimeout(() => {
      fetch(`/api/auth/register?username=${encodeURIComponent(trimmed)}`, { cache: 'no-store' })
        .then((res) => res.json())
        .then((data) => {
          if (cancelled) return;
          setUsernameCheck({
            checking: false,
            checkedValue: trimmed,
            available: Boolean(data?.available),
            message: String(data?.message || '')
          });
        })
        .catch(() => {
          if (cancelled) return;
          setUsernameCheck({
            checking: false,
            checkedValue: trimmed,
            available: null,
            message: ''
          });
        });
    }, 250);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [username]);

  const handleUsernameChange = (val: string) => {
    setUsername(val);
    const trimmed = val.trim().toLowerCase();
    if (!trimmed) {
      setUsernameCheck({ checking: false, checkedValue: '', available: null, message: '' });
    } else {
      setUsernameCheck({ checking: true, checkedValue: trimmed, available: null, message: '' });
    }
  };

  const passwordTooShort = password.length > 0 && password.length < 12;
  const confirmEntered = confirmPassword.length > 0;
  const passwordsMatch = password.length >= 12 && password === confirmPassword;
  const passwordsMismatch = confirmEntered && password !== confirmPassword;

  const canSubmit =
    !loading &&
    !usernameCheck.checking &&
    usernameCheck.available !== false &&
    password.length >= 12 &&
    password === confirmPassword;

  const handleSubmit = async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);
    if (usernameCheck.available === false) {
      setError(usernameCheck.message || 'Tên đăng nhập đã tồn tại hoặc không hợp lệ.');
      return;
    }
    if (password.length < 12) {
      setError('Mật khẩu phải có ít nhất 12 ký tự.');
      return;
    }
    if (password !== confirmPassword) {
      setError('Xác nhận mật khẩu không khớp.');
      return;
    }

    setLoading(true);
    try {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          member_category: memberCategory,
          username: username.trim().toLowerCase(),
          full_name: fullName.trim(),
          member_code: memberCode.trim(),
          email: email.trim(),
          department_class: departmentClass.trim(),
          password
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || 'Đăng ký tài khoản thất bại');
        setLoading(false);
        return;
      }
      window.location.href = data.redirect || '/dashboard/researcher/ssh-request';
    } catch {
      setError('Không kết nối được máy chủ đăng ký');
      setLoading(false);
    }
  };

  return (
    <div className='relative flex min-h-screen flex-col items-center justify-center bg-slate-50 dark:bg-zinc-950 p-4 sm:p-8'>
      <div className='w-full max-w-xl rounded-2xl border border-slate-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-6 sm:p-8 shadow-xl space-y-5'>
        <div className='flex items-center justify-between gap-3 border-b border-slate-200 dark:border-zinc-800 pb-4'>
          <div className='flex items-center gap-3'>
            <div className='flex size-10 items-center justify-center rounded-lg bg-blue-600 text-white'>
              <IconCpu className='size-5' />
            </div>
            <div>
              <h1 className='text-xl font-bold text-slate-900 dark:text-white'>
                Đăng ký Tài khoản LabOps
              </h1>
              <p className='text-xs text-slate-500 dark:text-slate-400'>
                Bước 1: Tạo tài khoản cổng Web → Bước 2: Điền đơn xin cấp quyền SSH &amp; GPU
              </p>
            </div>
          </div>
          <Link
            href='/auth/sign-in'
            className='text-xs font-medium text-blue-600 hover:underline flex items-center gap-1'
          >
            <IconArrowLeft className='size-3.5' />
            Đăng nhập
          </Link>
        </div>

        {error && (
          <div className='p-3 rounded-lg bg-red-50 dark:bg-red-950/50 border border-red-200 dark:border-red-900 flex items-start gap-2.5 text-red-700 dark:text-red-400 text-xs'>
            <IconAlertCircle className='size-4 shrink-0 mt-0.5' />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className='space-y-3.5 text-sm'>
          <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
            <div className='space-y-1'>
              <label htmlFor='reg-category' className='text-xs font-semibold'>
                Loại thành viên *
              </label>
              <select
                id='reg-category'
                value={memberCategory}
                onChange={(e) => setMemberCategory(e.target.value as 'STUDENT' | 'LECTURER')}
                className='w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800'
              >
                <option value='STUDENT'>Sinh viên (STUDENT)</option>
                <option value='LECTURER'>Giảng viên (LECTURER)</option>
              </select>
            </div>

            <div className='space-y-1'>
              <label htmlFor='reg-username' className='text-xs font-semibold'>
                Tên đăng nhập (Username) *
              </label>
              <input
                id='reg-username'
                required
                value={username}
                onChange={(e) => handleUsernameChange(e.target.value)}
                placeholder='vd: sv_nguyenvana'
                className={`w-full px-3 py-2 text-sm rounded-lg border bg-white dark:bg-zinc-800 focus:outline-none focus:ring-2 ${
                  usernameCheck.available === false
                    ? 'border-red-500 focus:ring-red-500'
                    : usernameCheck.available === true
                      ? 'border-emerald-500 focus:ring-emerald-500'
                      : 'border-slate-300 dark:border-zinc-700 focus:ring-blue-500'
                }`}
              />
              {username.trim().length > 0 && (
                <div className='text-xs flex items-center gap-1 pt-0.5'>
                  {usernameCheck.checking ? (
                    <span className='text-slate-500 flex items-center gap-1'>
                      <IconLoader2 className='size-3.5 animate-spin' />
                      Đang kiểm tra tên đăng nhập...
                    </span>
                  ) : usernameCheck.available === true ? (
                    <span className='text-emerald-600 dark:text-emerald-400 flex items-center gap-1'>
                      <IconCheck className='size-3.5' />
                      {usernameCheck.message}
                    </span>
                  ) : usernameCheck.available === false ? (
                    <span className='text-red-600 dark:text-red-400 flex items-center gap-1'>
                      <IconX className='size-3.5' />
                      {usernameCheck.message}
                    </span>
                  ) : null}
                </div>
              )}
            </div>

            <div className='space-y-1'>
              <label htmlFor='reg-fullname' className='text-xs font-semibold'>
                Họ và tên *
              </label>
              <input
                id='reg-fullname'
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder='Nguyễn Văn A'
                className='w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800'
              />
            </div>

            <div className='space-y-1'>
              <label htmlFor='reg-code' className='text-xs font-semibold'>
                {memberCategory === 'STUDENT' ? 'Mã sinh viên *' : 'Mã giảng viên / cán bộ *'}
              </label>
              <input
                id='reg-code'
                required
                value={memberCode}
                onChange={(e) => setMemberCode(e.target.value)}
                placeholder={memberCategory === 'STUDENT' ? '1571020001' : 'GV-AIOT-02'}
                className='w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800'
              />
            </div>

            <div className='space-y-1'>
              <label htmlFor='reg-email' className='text-xs font-semibold'>
                Email
              </label>
              <input
                id='reg-email'
                type='email'
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder='nguyenvana@fitdnu.id.vn'
                className='w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800'
              />
            </div>

            <div className='space-y-1'>
              <label htmlFor='reg-dept' className='text-xs font-semibold'>
                Khoa / Lớp / Bộ môn
              </label>
              <input
                id='reg-dept'
                value={departmentClass}
                onChange={(e) => setDepartmentClass(e.target.value)}
                placeholder='CNTT K16 — AIoT Lab'
                className='w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800'
              />
            </div>
          </div>

          <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
            <div className='space-y-1'>
              <label htmlFor='reg-password' className='text-xs font-semibold'>
                Mật khẩu (tối thiểu 12 ký tự) *
              </label>
              <input
                id='reg-password'
                type='password'
                required
                minLength={12}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder='Nhập mật khẩu tối thiểu 12 ký tự'
                className={`w-full px-3 py-2 text-sm rounded-lg border bg-white dark:bg-zinc-800 focus:outline-none focus:ring-2 ${
                  passwordTooShort
                    ? 'border-red-500 focus:ring-red-500'
                    : password.length >= 12
                      ? 'border-emerald-500 focus:ring-emerald-500'
                      : 'border-slate-300 dark:border-zinc-700 focus:ring-blue-500'
                }`}
              />
              {passwordTooShort && (
                <div className='text-xs text-red-600 dark:text-red-400 flex items-center gap-1 pt-0.5'>
                  <IconX className='size-3.5' />
                  Còn thiếu {12 - password.length} ký tự nữa (tối thiểu 12 ký tự)
                </div>
              )}
              {password.length >= 12 && (
                <div className='text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1 pt-0.5'>
                  <IconCheck className='size-3.5' />
                  Độ dài mật khẩu đạt chuẩn
                </div>
              )}
            </div>

            <div className='space-y-1'>
              <label htmlFor='reg-confirm' className='text-xs font-semibold'>
                Xác nhận mật khẩu *
              </label>
              <input
                id='reg-confirm'
                type='password'
                required
                minLength={12}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder='Nhập lại mật khẩu'
                className={`w-full px-3 py-2 text-sm rounded-lg border bg-white dark:bg-zinc-800 focus:outline-none focus:ring-2 ${
                  passwordsMismatch
                    ? 'border-red-500 focus:ring-red-500'
                    : passwordsMatch
                      ? 'border-emerald-500 focus:ring-emerald-500'
                      : 'border-slate-300 dark:border-zinc-700 focus:ring-blue-500'
                }`}
              />
              {passwordsMismatch && (
                <div className='text-xs text-red-600 dark:text-red-400 flex items-center gap-1 pt-0.5'>
                  <IconX className='size-3.5' />
                  Mật khẩu nhập lại chưa khớp
                </div>
              )}
              {passwordsMatch && (
                <div className='text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1 pt-0.5'>
                  <IconCheck className='size-3.5' />
                  Mật khẩu nhập lại đã khớp
                </div>
              )}
            </div>
          </div>

          <p className='text-xs text-slate-500 dark:text-slate-400 rounded-lg bg-slate-100 dark:bg-zinc-800/70 p-2.5'>
            Lưu ý: Đăng ký chỉ tạo tài khoản đăng nhập Web ban đầu. Quyền SSH, Docker Workspace,
            dung lượng lưu trữ và GPU chỉ được cấp sau khi bạn điền đơn đăng ký và được Admin phê
            duyệt.
          </p>

          <button
            type='submit'
            disabled={!canSubmit}
            className='w-full py-2.5 px-4 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium text-sm shadow-md flex items-center justify-center gap-2 transition disabled:opacity-50 cursor-pointer'
          >
            {loading ? (
              <>
                <IconLoader2 className='size-4 animate-spin' />
                <span>Đang tạo tài khoản...</span>
              </>
            ) : (
              <>
                <IconUserPlus className='size-4' />
                <span>Đăng ký Tài khoản &amp; Tiếp tục</span>
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
