'use client';

import React, { useMemo, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { labopsCall } from '@/features/labops/api';
import { WebPortalAccountItem } from '../types';
import { toast } from 'sonner';

interface WebAccountsPanelProps {
  accounts: WebPortalAccountItem[];
  loading?: boolean;
  demoMode?: boolean;
  onRefresh: () => void;
  onDeleteWebAccount?: (memberId: string) => void;
}

type WebAccountFilter = 'ALL' | 'SSH_LINKED' | 'PENDING_SSH' | 'UNLINKED';

function formatRequestBadge(status?: string) {
  switch (status) {
    case 'VERIFIED_READY':
    case 'APPROVED':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
        >
          Đã phê duyệt
        </Badge>
      );
    case 'PENDING_APPROVAL':
      return (
        <Badge variant='outline' className='bg-amber-500/10 text-amber-800 dark:text-amber-300'>
          Chờ duyệt (SLA 24h)
        </Badge>
      );
    case 'NEED_INFO':
      return (
        <Badge variant='outline' className='bg-blue-500/10 text-blue-700 dark:text-blue-300'>
          Cần bổ sung
        </Badge>
      );
    case 'REJECTED':
      return (
        <Badge variant='outline' className='bg-rose-500/10 text-rose-700 dark:text-rose-300'>
          Đã từ chối
        </Badge>
      );
    case 'EXPIRED':
      return (
        <Badge variant='outline' className='bg-slate-500/10 text-slate-700 dark:text-slate-300'>
          Quá hạn 24h
        </Badge>
      );
    default:
      return (
        <Badge variant='outline' className='text-muted-foreground'>
          Chưa nộp đơn SSH
        </Badge>
      );
  }
}

export function WebAccountsPanel({
  accounts,
  loading = false,
  demoMode = false,
  onRefresh,
  onDeleteWebAccount
}: WebAccountsPanelProps) {
  const [filterMode, setFilterMode] = useState<WebAccountFilter>('ALL');
  const [query, setQuery] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reissuedResult, setReissuedResult] = useState<{
    username: string;
    fullName: string;
    password: string;
  } | null>(null);

  const stats = useMemo(() => {
    const total = accounts.length;
    const linked = accounts.filter((a) => a.is_ssh_linked).length;
    const pending = accounts.filter(
      (a) =>
        !a.is_ssh_linked &&
        (a.latest_request?.status === 'PENDING_APPROVAL' ||
          a.latest_request?.status === 'NEED_INFO')
    ).length;
    const unlinked = total - linked;
    return { total, linked, pending, unlinked };
  }, [accounts]);

  const filteredAccounts = useMemo(() => {
    const q = query.trim().toLowerCase();
    return accounts.filter((acc) => {
      if (filterMode === 'SSH_LINKED' && !acc.is_ssh_linked) return false;
      if (
        filterMode === 'PENDING_SSH' &&
        (acc.is_ssh_linked ||
          !['PENDING_APPROVAL', 'NEED_INFO'].includes(acc.latest_request?.status || ''))
      ) {
        return false;
      }
      if (filterMode === 'UNLINKED' && acc.is_ssh_linked) return false;
      if (!q) return true;
      return [
        acc.username,
        acc.full_name,
        acc.member_code,
        acc.email,
        acc.department_class,
        acc.supervisor_name || '',
        acc.linked_ssh_username || ''
      ].some((v) => v.toLowerCase().includes(q));
    });
  }, [accounts, filterMode, query]);

  const handleReissueWebPassword = async (acc: WebPortalAccountItem) => {
    if (acc.username.toLowerCase() === 'admin') {
      toast.error('Không áp dụng cấp lại mật khẩu Web cho tài khoản Admin.');
      return;
    }
    const confirmed = window.confirm(
      `Xác nhận cấp lại mật khẩu đăng nhập Cổng Web mới cho @${acc.username} (${acc.full_name})?\n\n` +
        'Mật khẩu Web cũ sẽ hết hiệu lực ngay lập tức. Tài khoản SSH (nếu có) không bị thay đổi.'
    );
    if (!confirmed) return;

    setBusyId(acc.member_id);
    const res = await labopsCall<{
      success: boolean;
      username: string;
      full_name: string;
      reissued_web_password: string;
    }>(`admin/web-accounts/${encodeURIComponent(acc.member_id)}/reissue-password`, {
      method: 'POST',
      body: {}
    });
    setBusyId(null);

    if (!res.ok || !res.data) {
      toast.error('Không thể cấp lại mật khẩu Web', { description: res.error });
      return;
    }

    setReissuedResult({
      username: res.data.username,
      fullName: res.data.full_name,
      password: res.data.reissued_web_password
    });
    toast.success(`Đã cấp lại mật khẩu đăng nhập Web cho @${res.data.username}`);
    onRefresh();
  };

  return (
    <div className='space-y-4'>
      <div className='grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs'>
        <div className='rounded-lg border bg-card p-3'>
          <div className='text-muted-foreground'>Tổng TK Đăng ký Web</div>
          <div className='text-lg font-bold font-mono mt-0.5'>{stats.total}</div>
        </div>
        <div className='rounded-lg border bg-card p-3'>
          <div className='text-muted-foreground'>Đã tự động liên kết SSH</div>
          <div className='text-lg font-bold font-mono mt-0.5 text-emerald-600 dark:text-emerald-400'>
            {stats.linked}
          </div>
        </div>
        <div className='rounded-lg border bg-card p-3'>
          <div className='text-muted-foreground'>Đang chờ duyệt đơn SSH</div>
          <div className='text-lg font-bold font-mono mt-0.5 text-amber-700 dark:text-amber-300'>
            {stats.pending}
          </div>
        </div>
        <div className='rounded-lg border bg-card p-3'>
          <div className='text-muted-foreground'>Chưa liên kết SSH</div>
          <div className='text-lg font-bold font-mono mt-0.5'>{stats.unlinked}</div>
        </div>
      </div>

      {reissuedResult && (
        <Card className='border-amber-500/50 bg-amber-500/10'>
          <CardHeader className='py-3'>
            <CardTitle className='text-sm text-amber-900 dark:text-amber-200'>
              Mật khẩu đăng nhập Cổng Web mới cho @{reissuedResult.username} (
              {reissuedResult.fullName})
            </CardTitle>
            <CardDescription className='text-xs text-amber-800 dark:text-amber-300'>
              Mật khẩu này chỉ hiển thị đúng 1 lần để gửi cho sinh viên (bắt buộc đổi sau khi đăng
              nhập).
            </CardDescription>
          </CardHeader>
          <CardContent className='pb-3 flex flex-wrap items-center justify-between gap-2'>
            <code className='rounded bg-background border px-3 py-1.5 font-mono text-sm font-semibold'>
              {reissuedResult.password}
            </code>
            <div className='flex gap-2'>
              <Button
                size='sm'
                variant='outline'
                onClick={() => {
                  void navigator.clipboard.writeText(reissuedResult.password);
                  toast.success('Đã sao chép mật khẩu Web mới');
                }}
              >
                Sao chép mật khẩu
              </Button>
              <Button size='sm' variant='ghost' onClick={() => setReissuedResult(null)}>
                Đóng
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className='pb-3'>
          <CardTitle className='text-base'>
            Tài khoản Đăng ký trên Cổng Web (Tách biệt với Tài khoản SSH)
          </CardTitle>
          <CardDescription className='text-xs'>
            Sinh viên/Giảng viên đăng ký tại cổng Web chỉ dùng để đăng nhập nộp đơn. Tài khoản sẽ tự
            động liên kết với Tài khoản SSH máy chủ ngay sau khi Admin phê duyệt và cấp phát thành
            công.
          </CardDescription>
        </CardHeader>
        <CardContent className='space-y-4'>
          <div className='flex flex-wrap items-center justify-between gap-2'>
            <div className='flex flex-wrap gap-1.5'>
              {(
                [
                  { id: 'ALL', label: `Tất cả (${stats.total})` },
                  { id: 'SSH_LINKED', label: `Đã liên kết SSH (${stats.linked})` },
                  { id: 'PENDING_SSH', label: `Chờ duyệt SSH (${stats.pending})` },
                  { id: 'UNLINKED', label: `Chưa liên kết SSH (${stats.unlinked})` }
                ] as const
              ).map((tab) => (
                <Button
                  key={tab.id}
                  type='button'
                  size='sm'
                  variant={filterMode === tab.id ? 'default' : 'outline'}
                  className='h-8 text-xs'
                  onClick={() => setFilterMode(tab.id)}
                >
                  {tab.label}
                </Button>
              ))}
            </div>
            <Input
              aria-label='Tìm tài khoản đăng ký Web'
              placeholder='Tìm @username, họ tên, mã SV, email hoặc GVHD'
              className='max-w-xs'
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          <div className='overflow-x-auto'>
            <table className='w-full min-w-[920px] text-left text-xs'>
              <thead>
                <tr className='border-b text-muted-foreground'>
                  <th className='p-3'>Tài khoản Web</th>
                  <th className='p-3'>Mã SV/GV &amp; Lớp</th>
                  <th className='p-3'>GVHD &amp; Đề tài</th>
                  <th className='p-3'>Đơn xin cấp SSH</th>
                  <th className='p-3'>Liên kết Tài khoản SSH (Tự động)</th>
                  <th className='p-3 text-right'>Thao tác Web</th>
                </tr>
              </thead>
              <tbody>
                {loading && accounts.length === 0 ? (
                  <tr>
                    <td colSpan={6} className='p-6 text-center text-muted-foreground'>
                      Đang tải danh sách tài khoản đăng ký Web...
                    </td>
                  </tr>
                ) : filteredAccounts.length === 0 ? (
                  <tr>
                    <td colSpan={6} className='p-6 text-center text-muted-foreground'>
                      Không có tài khoản đăng ký Web phù hợp.
                    </td>
                  </tr>
                ) : (
                  filteredAccounts.map((acc) => (
                    <tr key={acc.member_id} className='border-b last:border-0 align-top'>
                      <td className='p-3'>
                        <div className='font-semibold text-sm'>@{acc.username}</div>
                        <div className='text-foreground font-medium'>{acc.full_name}</div>
                        <div className='text-muted-foreground'>{acc.email || '—'}</div>
                        <div className='mt-1 flex flex-wrap gap-1'>
                          <Badge variant='outline' className='text-xs'>
                            {acc.web_account_status}
                          </Badge>
                          {acc.must_change_password && (
                            <Badge
                              variant='outline'
                              className='text-xs bg-amber-500/10 text-amber-700'
                            >
                              Cần đổi MK Web
                            </Badge>
                          )}
                        </div>
                      </td>
                      <td className='p-3'>
                        <Badge variant='secondary' className='text-xs'>
                          {acc.member_category === 'LECTURER' ? 'Giảng viên' : 'Sinh viên'}
                        </Badge>
                        <div className='font-mono mt-1 font-medium'>
                          {acc.member_code || 'Chưa có mã'}
                        </div>
                        <div className='text-muted-foreground'>{acc.department_class || '—'}</div>
                      </td>
                      <td className='p-3 max-w-[240px]'>
                        <div className='font-medium'>
                          {acc.supervisor_name || (
                            <span className='text-muted-foreground'>Chưa khai báo GVHD</span>
                          )}
                        </div>
                        <div className='text-muted-foreground mt-0.5 line-clamp-2'>
                          {acc.latest_request?.project_title || 'Chưa nộp đề tài'}
                        </div>
                      </td>
                      <td className='p-3 space-y-1.5'>
                        <div>{formatRequestBadge(acc.latest_request?.status)}</div>
                        {acc.latest_request && (
                          <div className='space-y-1'>
                            <div className='font-mono text-xs text-muted-foreground'>
                              {acc.latest_request.id}
                            </div>
                            <Link
                              href={`/dashboard/ssh-requests/${encodeURIComponent(acc.latest_request.id)}/review`}
                              className='inline-block text-xs text-blue-600 hover:underline font-medium'
                            >
                              {acc.latest_request.status === 'PENDING_APPROVAL'
                                ? 'Xem xét & Phê duyệt →'
                                : 'Xem chi tiết đơn →'}
                            </Link>
                          </div>
                        )}
                      </td>
                      <td className='p-3'>
                        {acc.is_ssh_linked ? (
                          <div className='space-y-1'>
                            <Badge className='bg-emerald-600 text-white text-xs'>
                              Đã liên kết SSH: @{acc.linked_ssh_username}
                            </Badge>
                            <div className='font-mono text-xs text-muted-foreground'>
                              {(acc.linked_ssh_server || 'workstation').toUpperCase()} •{' '}
                              {acc.linked_ssh_host}:{acc.linked_ssh_port}
                            </div>
                          </div>
                        ) : (
                          <div className='space-y-1'>
                            <Badge variant='outline' className='text-muted-foreground text-xs'>
                              Chưa liên kết SSH
                            </Badge>
                            <div className='text-xs text-muted-foreground'>
                              Tự động liên kết sau khi đơn được duyệt
                            </div>
                          </div>
                        )}
                      </td>
                      <td className='p-3 text-right'>
                        <div className='flex flex-wrap justify-end gap-1.5'>
                          <Button
                            size='sm'
                            variant='outline'
                            disabled={busyId === acc.member_id || demoMode}
                            onClick={() => void handleReissueWebPassword(acc)}
                          >
                            {busyId === acc.member_id ? 'Đang cấp...' : 'Cấp lại MK Web'}
                          </Button>
                          {onDeleteWebAccount && !acc.is_ssh_linked && (
                            <Button
                              size='sm'
                              variant='outline'
                              className='border-rose-500/40 text-rose-600 hover:bg-rose-500/10'
                              disabled={busyId === acc.member_id || demoMode}
                              onClick={() => onDeleteWebAccount(acc.member_id)}
                            >
                              Xóa TK
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
