'use client';

import React, { useState, useEffect, useMemo, useTransition } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription
} from '@/components/ui/dialog';
import { Icons } from '@/components/icons';
import { toast } from 'sonner';
import { labopsCall } from '@/features/labops/api';
import { RepositoryGuardRequest, SecurityReviewStatus } from '@/types/labops';
import Link from 'next/link';

function getApprovalBadge(status: SecurityReviewStatus | string) {
  switch (status) {
    case 'AUTO_APPROVED':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[11px] font-semibold gap-1 shrink-0'
        >
          <Icons.check className='size-3 text-emerald-600' />
          TỰ ĐỘNG DUYỆT
        </Badge>
      );
    case 'APPROVED':
    case 'MANUALLY_APPROVED':
      return (
        <Badge
          variant='outline'
          className='bg-teal-500/15 text-teal-700 dark:text-teal-400 border-teal-500/30 text-[11px] font-semibold gap-1 shrink-0'
        >
          <Icons.circleCheck className='size-3 text-teal-600' />
          ADMIN ĐÃ DUYỆT
        </Badge>
      );
    case 'DELIVERED':
      return (
        <Badge
          variant='outline'
          className='bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[11px] font-semibold gap-1 shrink-0'
        >
          <Icons.check className='size-3 text-blue-600' />
          ĐÃ PHÂN PHỐI
        </Badge>
      );
    case 'REVIEW_REQUIRED':
    case 'MANUAL_REVIEW':
      return (
        <Badge
          variant='outline'
          className='bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[11px] font-semibold gap-1 shrink-0'
        >
          <Icons.warning className='size-3 text-amber-600' />
          CHỜ ADMIN DUYỆT
        </Badge>
      );
    case 'REJECTED':
      return (
        <Badge
          variant='outline'
          className='bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/30 text-[11px] font-semibold gap-1 shrink-0'
        >
          <Icons.close className='size-3 text-rose-600' />
          BỊ TỪ CHỐI
        </Badge>
      );
    case 'FAILED':
      return (
        <Badge
          variant='outline'
          className='bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30 text-[11px] font-semibold gap-1 shrink-0'
        >
          <Icons.alertCircle className='size-3 text-red-600' />
          THẤT BẠI
        </Badge>
      );
    case 'FETCHING':
    case 'SCANNING':
    case 'POLICY_EVALUATION':
    case 'RESOLVING_COMMIT':
      return (
        <Badge
          variant='outline'
          className='bg-sky-500/15 text-sky-700 dark:text-sky-400 border-sky-500/30 text-[11px] font-mono animate-pulse shrink-0'
        >
          <Icons.spinner className='size-3 animate-spin' />
          ĐANG QUÉT
        </Badge>
      );
    default:
      return (
        <Badge variant='outline' className='text-[11px] font-mono shrink-0'>
          {status}
        </Badge>
      );
  }
}

function formatDateTime(isoString?: string): { formatted: string; relative: string } {
  if (!isoString) return { formatted: '—', relative: '—' };
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return { formatted: isoString, relative: '—' };

    const formatted = d.toLocaleString('vi-VN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      day: '2-digit',
      month: '2-digit',
      year: 'numeric'
    });

    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
    let relative = '';
    if (diffSec < 60) {
      relative = 'vừa xong';
    } else if (diffSec < 3600) {
      relative = `${Math.floor(diffSec / 60)} phút trước`;
    } else if (diffSec < 86400) {
      relative = `${Math.floor(diffSec / 3600)} giờ trước`;
    } else {
      relative = `${Math.floor(diffSec / 86400)} ngày trước`;
    }

    return { formatted, relative };
  } catch {
    return { formatted: isoString, relative: '—' };
  }
}

export function MemberRepoHistoryPanel() {
  const [requests, setRequests] = useState<RepositoryGuardRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [, startTransition] = useTransition();

  // Filters
  const [selectedMember, setSelectedMember] = useState<string>('all');
  const [selectedStatusGroup, setSelectedStatusGroup] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Selected for detail modal
  const [selectedReq, setSelectedReq] = useState<RepositoryGuardRequest | null>(null);

  const fetchHistory = async (showLoading = false) => {
    if (showLoading) setLoading(true);
    setIsRefreshing(true);
    try {
      const res = await labopsCall<RepositoryGuardRequest[]>('repositories/requests');
      if (res.ok && Array.isArray(res.data)) {
        setRequests(res.data);
      }
    } catch {
      // silent fail on poll
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    void fetchHistory(true);
    const interval = setInterval(() => {
      void fetchHistory(false);
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Distinct member list for filter
  const memberList = useMemo(() => {
    const map = new Map<string, number>();
    requests.forEach((r) => {
      const user = r.submitted_by || 'unknown';
      map.set(user, (map.get(user) || 0) + 1);
    });
    return Array.from(map.entries()).map(([username, count]) => ({ username, count }));
  }, [requests]);

  // Overall Statistics
  const stats = useMemo(() => {
    const total = requests.length;
    const approved = requests.filter((r) =>
      ['AUTO_APPROVED', 'APPROVED', 'MANUALLY_APPROVED', 'DELIVERED'].includes(r.state)
    ).length;
    const rejected = requests.filter((r) => r.state === 'REJECTED').length;
    const reviewReq = requests.filter((r) => r.state === 'REVIEW_REQUIRED').length;
    const failed = requests.filter((r) => r.state === 'FAILED').length;
    const approvalRate = total > 0 ? Math.round((approved / total) * 100) : 0;
    return { total, approved, rejected, reviewReq, failed, approvalRate };
  }, [requests]);

  // Filtered requests
  const filteredRequests = useMemo(() => {
    return requests.filter((r) => {
      // Member filter
      if (selectedMember !== 'all' && (r.submitted_by || 'unknown') !== selectedMember) {
        return false;
      }

      // Status filter
      if (selectedStatusGroup === 'approved') {
        if (!['AUTO_APPROVED', 'APPROVED', 'MANUALLY_APPROVED', 'DELIVERED'].includes(r.state)) {
          return false;
        }
      } else if (selectedStatusGroup === 'not_approved') {
        if (!['REJECTED', 'FAILED'].includes(r.state)) {
          return false;
        }
      } else if (selectedStatusGroup === 'review_required') {
        if (r.state !== 'REVIEW_REQUIRED') {
          return false;
        }
      }

      // Search filter
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const repo = (r.repo_url || r.canonical_repo || '').toLowerCase();
        const user = (r.submitted_by || '').toLowerCase();
        const refName = (r.requested_ref || '').toLowerCase();
        const sha = (r.commit_sha || '').toLowerCase();
        const id = (r.id || '').toLowerCase();
        const reasons = (r.report?.policy_reasons || []).join(' ').toLowerCase();
        return (
          repo.includes(query) ||
          user.includes(query) ||
          refName.includes(query) ||
          sha.includes(query) ||
          id.includes(query) ||
          reasons.includes(query)
        );
      }

      return true;
    });
  }, [requests, selectedMember, selectedStatusGroup, searchQuery]);

  return (
    <Card className='border bg-card shadow-xs w-full min-w-0'>
      <CardHeader className='pb-3 pt-4 border-b border-border/50'>
        <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3'>
          <div className='flex items-start sm:items-center gap-2.5 min-w-0'>
            <div className='p-2 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 shrink-0'>
              <Icons.shield className='size-5' />
            </div>
            <div className='min-w-0'>
              <div className='flex items-center gap-2'>
                <CardTitle className='text-base font-bold tracking-tight truncate'>
                  Lịch sử Clone & Kiểm duyệt Kho mã theo Thành viên
                </CardTitle>
                <Badge
                  variant='outline'
                  className='text-[10px] bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 shrink-0 hidden sm:inline-flex'
                >
                  ZERO-TRUST EGRESS
                </Badge>
              </div>
              <CardDescription className='text-xs text-muted-foreground truncate'>
                Ghi nhận lịch sử kèm thời gian thực tế toàn bộ yêu cầu tải mã nguồn qua{' '}
                <code className='bg-muted px-1 py-0.5 rounded text-[11px] font-mono text-foreground'>
                  gitlabops clone
                </code>{' '}
                — phân loại chi tiết theo từng thành viên (được duyệt & bị từ chối/chặn).
              </CardDescription>
            </div>
          </div>

          <div className='flex items-center gap-2 shrink-0 self-end sm:self-auto'>
            <Button
              variant='outline'
              size='sm'
              onClick={() => void fetchHistory(true)}
              disabled={isRefreshing}
              className='h-8 text-xs gap-1.5'
            >
              <Icons.refresh className={`size-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
              <span className='hidden sm:inline'>Làm mới</span>
            </Button>
            <Link href='/dashboard/security'>
              <Button size='sm' className='h-8 text-xs gap-1 bg-primary text-primary-foreground'>
                Trung tâm An ninh
                <Icons.arrowRight className='size-3.5' />
              </Button>
            </Link>
          </div>
        </div>

        {/* Mini KPI Bar */}
        <div className='grid grid-cols-2 sm:grid-cols-5 gap-2.5 pt-3'>
          <div className='p-2.5 rounded-lg border bg-muted/20'>
            <span className='text-[10px] text-muted-foreground font-medium block'>
              Tổng lượt clone
            </span>
            <div className='text-lg font-bold mt-0.5 text-foreground flex items-center gap-1.5'>
              <Icons.github className='size-4 text-muted-foreground' />
              {stats.total}
            </div>
          </div>

          <div className='p-2.5 rounded-lg border bg-emerald-500/5 border-emerald-500/20'>
            <span className='text-[10px] text-emerald-700 dark:text-emerald-400 font-medium block'>
              Đã được phê duyệt
            </span>
            <div className='text-lg font-bold mt-0.5 text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5'>
              <Icons.check className='size-4' />
              {stats.approved}
            </div>
          </div>

          <div className='p-2.5 rounded-lg border bg-rose-500/5 border-rose-500/20'>
            <span className='text-[10px] text-rose-700 dark:text-rose-400 font-medium block'>
              Không được duyệt / Bị chặn
            </span>
            <div className='text-lg font-bold mt-0.5 text-rose-600 dark:text-rose-400 flex items-center gap-1.5'>
              <Icons.close className='size-4' />
              {stats.rejected + stats.failed}
            </div>
          </div>

          <div className='p-2.5 rounded-lg border bg-amber-500/5 border-amber-500/20'>
            <span className='text-[10px] text-amber-700 dark:text-amber-400 font-medium block'>
              Cần Admin duyệt ngoại lệ
            </span>
            <div className='text-lg font-bold mt-0.5 text-amber-600 dark:text-amber-400 flex items-center gap-1.5'>
              <Icons.warning className='size-4' />
              {stats.reviewReq}
            </div>
          </div>

          <div className='p-2.5 rounded-lg border bg-muted/20'>
            <span className='text-[10px] text-muted-foreground font-medium block'>
              Tỷ lệ duyệt an toàn
            </span>
            <div className='text-lg font-bold mt-0.5 text-blue-600 dark:text-blue-400 flex items-center gap-1.5'>
              <Icons.shield className='size-4' />
              {stats.approvalRate}%
            </div>
          </div>
        </div>
      </CardHeader>

      <CardContent className='pt-3 pb-4 space-y-3.5'>
        {/* Filter Controls */}
        <div className='flex flex-col md:flex-row gap-2.5 items-stretch md:items-center justify-between'>
          {/* Member Filter Pills / Select */}
          <div className='flex flex-wrap items-center gap-1.5'>
            <span className='text-xs font-semibold text-muted-foreground mr-1 flex items-center gap-1'>
              <Icons.user className='size-3.5' /> Thành viên:
            </span>
            <Button
              variant={selectedMember === 'all' ? 'default' : 'outline'}
              size='sm'
              onClick={() => setSelectedMember('all')}
              className='h-7 text-xs px-2.5 rounded-full'
            >
              Tất cả ({requests.length})
            </Button>
            {memberList.map((m) => (
              <Button
                key={m.username}
                variant={selectedMember === m.username ? 'default' : 'outline'}
                size='sm'
                onClick={() => setSelectedMember(m.username)}
                className='h-7 text-xs px-2.5 rounded-full font-mono'
              >
                @{m.username} ({m.count})
              </Button>
            ))}
          </div>

          {/* Status Group Filters & Search */}
          <div className='flex flex-wrap items-center gap-2'>
            <div className='flex items-center gap-1 bg-muted/30 p-0.5 rounded-lg border text-xs'>
              <button
                type='button'
                onClick={() => setSelectedStatusGroup('all')}
                className={`px-2 py-1 rounded text-xs transition-colors ${
                  selectedStatusGroup === 'all'
                    ? 'bg-background font-semibold shadow-xs text-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                Tất cả
              </button>
              <button
                type='button'
                onClick={() => setSelectedStatusGroup('approved')}
                className={`px-2 py-1 rounded text-xs transition-colors ${
                  selectedStatusGroup === 'approved'
                    ? 'bg-emerald-500/15 font-semibold text-emerald-700 dark:text-emerald-400'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                ✅ Được duyệt ({stats.approved})
              </button>
              <button
                type='button'
                onClick={() => setSelectedStatusGroup('not_approved')}
                className={`px-2 py-1 rounded text-xs transition-colors ${
                  selectedStatusGroup === 'not_approved'
                    ? 'bg-rose-500/15 font-semibold text-rose-700 dark:text-rose-400'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                ❌ Không duyệt ({stats.rejected + stats.failed})
              </button>
              <button
                type='button'
                onClick={() => setSelectedStatusGroup('review_required')}
                className={`px-2 py-1 rounded text-xs transition-colors ${
                  selectedStatusGroup === 'review_required'
                    ? 'bg-amber-500/15 font-semibold text-amber-700 dark:text-amber-400'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                ⚠️ Cần duyệt ({stats.reviewReq})
              </button>
            </div>

            <div className='relative w-full sm:w-48'>
              <Icons.search className='absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground' />
              <Input
                placeholder='Tìm repo, commit, mã...'
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className='h-8 text-xs pl-8 pr-2.5'
              />
            </div>
          </div>
        </div>

        {/* History Table */}
        <div className='rounded-lg border bg-card overflow-hidden'>
          <div className='overflow-x-auto max-w-full'>
            <table className='w-full text-left border-collapse text-xs'>
              <thead>
                <tr className='border-b bg-muted/40 text-muted-foreground text-[11px] font-semibold'>
                  <th className='py-2.5 px-3 whitespace-nowrap'>Thời gian</th>
                  <th className='py-2.5 px-3 whitespace-nowrap'>Thành viên</th>
                  <th className='py-2.5 px-3 min-w-[200px]'>Kho lưu trữ & Nhánh</th>
                  <th className='py-2.5 px-3 whitespace-nowrap'>Kết quả Kiểm duyệt</th>
                  <th className='py-2.5 px-3 min-w-[180px]'>Phát hiện An ninh & Lý do</th>
                  <th className='py-2.5 px-3 text-right whitespace-nowrap'>Hành động</th>
                </tr>
              </thead>
              <tbody className='divide-y divide-border/40'>
                {loading ? (
                  <tr>
                    <td colSpan={6} className='py-8 text-center text-muted-foreground'>
                      <div className='flex items-center justify-center gap-2'>
                        <Icons.spinner className='size-4 animate-spin text-primary' />
                        <span>Đang nạp lịch sử kiểm duyệt từ Auto Security Bot...</span>
                      </div>
                    </td>
                  </tr>
                ) : filteredRequests.length === 0 ? (
                  <tr>
                    <td colSpan={6} className='py-8 text-center text-muted-foreground'>
                      <div className='flex flex-col items-center justify-center gap-1.5'>
                        <Icons.shield className='size-6 text-muted-foreground/50' />
                        <span className='font-medium'>Không tìm thấy yêu cầu clone nào phù hợp bộ lọc</span>
                        <span className='text-[11px] text-muted-foreground/80'>
                          Các lệnh thực thi qua{' '}
                          <code className='bg-muted px-1 rounded text-foreground font-mono'>
                            gitlabops clone
                          </code>{' '}
                          sẽ xuất hiện tự động tại đây.
                        </span>
                      </div>
                    </td>
                  </tr>
                ) : (
                  filteredRequests.map((r) => {
                    const timeInfo = formatDateTime(r.submitted_at);
                    const isApproved = [
                      'AUTO_APPROVED',
                      'APPROVED',
                      'MANUALLY_APPROVED',
                      'DELIVERED'
                    ].includes(r.state);

                    return (
                      <tr
                        key={r.id}
                        className={`hover:bg-muted/30 transition-colors ${
                          !isApproved ? 'bg-rose-500/[0.02]' : ''
                        }`}
                      >
                        {/* 1. Thời gian */}
                        <td className='py-2.5 px-3 whitespace-nowrap'>
                          <div className='flex flex-col'>
                            <span className='font-mono font-medium text-foreground text-[11px] flex items-center gap-1'>
                              <Icons.clock className='size-3 text-muted-foreground shrink-0' />
                              {timeInfo.formatted}
                            </span>
                            <span className='text-[10px] text-muted-foreground pl-4'>
                              {timeInfo.relative}
                            </span>
                          </div>
                        </td>

                        {/* 2. Thành viên */}
                        <td className='py-2.5 px-3 whitespace-nowrap'>
                          <div className='flex items-center gap-1.5'>
                            <div className='size-6 rounded-full bg-primary/10 text-primary flex items-center justify-center font-bold text-[10px] uppercase shrink-0'>
                              {(r.submitted_by || 'U')[0]}
                            </div>
                            <div className='flex flex-col'>
                              <span className='font-mono font-semibold text-foreground text-[11px]'>
                                @{r.submitted_by || 'unknown'}
                              </span>
                              {r.target_workspace && (
                                <span className='text-[9px] text-muted-foreground font-mono truncate max-w-[90px]'>
                                  ws: {r.target_workspace}
                                </span>
                              )}
                            </div>
                          </div>
                        </td>

                        {/* 3. Kho lưu trữ & Nhánh */}
                        <td className='py-2.5 px-3'>
                          <div className='flex flex-col gap-0.5 min-w-0'>
                            <div className='flex items-center gap-1.5 min-w-0'>
                              <Icons.github className='size-3.5 text-muted-foreground shrink-0' />
                              <a
                                href={r.canonical_repo || r.repo_url}
                                target='_blank'
                                rel='noreferrer'
                                className='font-semibold text-foreground hover:text-primary hover:underline truncate max-w-[200px] text-[11px]'
                                title={r.canonical_repo || r.repo_url}
                              >
                                {r.owner && r.repo_name
                                  ? `${r.owner}/${r.repo_name}`
                                  : r.canonical_repo || r.repo_url}
                              </a>
                            </div>

                            <div className='flex items-center gap-1.5 text-[10px] text-muted-foreground'>
                              <Badge
                                variant='secondary'
                                className='px-1 py-0 text-[9px] font-mono shrink-0'
                              >
                                {r.requested_ref || 'HEAD'}
                              </Badge>
                              {r.commit_sha ? (
                                <span
                                  className='font-mono text-[9px] truncate max-w-[80px]'
                                  title={r.commit_sha}
                                >
                                  #{r.commit_sha.slice(0, 7)}
                                </span>
                              ) : (
                                <span className='text-[9px] italic'>chưa phân giải</span>
                              )}
                              {r.cache_hit && (
                                <Badge
                                  variant='outline'
                                  className='px-1 py-0 text-[8px] bg-blue-500/10 text-blue-600 border-blue-500/30'
                                >
                                  Cache Hit
                                </Badge>
                              )}
                            </div>
                          </div>
                        </td>

                        {/* 4. Kết quả duyệt */}
                        <td className='py-2.5 px-3 whitespace-nowrap'>
                          <div className='flex flex-col gap-1 items-start'>
                            {getApprovalBadge(r.state)}
                            {r.decision && (
                              <span className='text-[9px] text-muted-foreground font-mono truncate max-w-[120px]'>
                                bởi @{r.decision.reviewer}
                              </span>
                            )}
                          </div>
                        </td>

                        {/* 5. Phát hiện An ninh & Lý do */}
                        <td className='py-2.5 px-3'>
                          <div className='flex flex-col gap-1 min-w-0 max-w-[320px]'>
                            {/* Tags kết quả quét */}
                            {r.report ? (
                              <div className='flex flex-wrap items-center gap-1'>
                                {r.report.secrets_count > 0 && (
                                  <Badge
                                    variant='outline'
                                    className='bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/40 text-[9px] px-1 py-0'
                                  >
                                    {r.report.secrets_count} Secret
                                  </Badge>
                                )}
                                {r.report.vulnerabilities?.critical > 0 && (
                                  <Badge
                                    variant='outline'
                                    className='bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30 text-[9px] px-1 py-0'
                                  >
                                    {r.report.vulnerabilities.critical} Critical
                                  </Badge>
                                )}
                                {r.report.vulnerabilities?.high > 0 && (
                                  <Badge
                                    variant='outline'
                                    className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[9px] px-1 py-0'
                                  >
                                    {r.report.vulnerabilities.high} High
                                  </Badge>
                                )}
                                {r.report.secrets_count === 0 &&
                                  r.report.vulnerabilities?.critical === 0 &&
                                  r.report.vulnerabilities?.high === 0 && (
                                    <Badge
                                      variant='outline'
                                      className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[9px] px-1 py-0'
                                    >
                                      Sạch (0 CVE, 0 Secret)
                                    </Badge>
                                  )}
                              </div>
                            ) : null}

                            {/* Lý do phê duyệt / từ chối */}
                            <div className='text-[10px] text-muted-foreground truncate'>
                              {r.decision?.reason ? (
                                <span className='text-foreground font-medium'>
                                  Ghi chú Admin: {r.decision.reason}
                                </span>
                              ) : r.error ? (
                                <span className='text-rose-600 dark:text-rose-400 font-medium'>
                                  Lỗi: {r.error}
                                </span>
                              ) : r.report?.policy_reasons?.length ? (
                                <span
                                  className={
                                    isApproved
                                      ? 'text-emerald-600 dark:text-emerald-400'
                                      : 'text-amber-600 dark:text-amber-400'
                                  }
                                >
                                  {r.report.policy_reasons[0]}
                                </span>
                              ) : (
                                <span>Tiêu chuẩn LabOps Zero-Trust</span>
                              )}
                            </div>
                          </div>
                        </td>

                        {/* 6. Hành động */}
                        <td className='py-2.5 px-3 text-right whitespace-nowrap'>
                          <Button
                            size='sm'
                            variant='outline'
                            onClick={() => setSelectedReq(r)}
                            className='h-7 text-xs px-2 gap-1'
                          >
                            <Icons.info className='size-3' />
                            Chi tiết
                          </Button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </CardContent>

      {/* Detail Dialog */}
      {selectedReq && (
        <Dialog open={Boolean(selectedReq)} onOpenChange={() => setSelectedReq(null)}>
          <DialogContent className='sm:max-w-[620px] max-h-[85vh] overflow-y-auto'>
            <DialogHeader>
              <DialogTitle className='text-base font-bold flex items-center gap-2'>
                <Icons.shield className='size-5 text-primary' /> Chi tiết Kiểm duyệt Repository
              </DialogTitle>
              <DialogDescription className='text-xs'>
                Mã yêu cầu: <span className='font-mono text-foreground font-semibold'>{selectedReq.id}</span> •
                Gửi bởi: <span className='font-mono font-semibold text-foreground'>@{selectedReq.submitted_by}</span>
              </DialogDescription>
            </DialogHeader>

            <div className='flex flex-col gap-3 py-2 text-xs'>
              {/* Summary metadata box */}
              <div className='grid grid-cols-2 gap-2 p-2.5 rounded-lg border bg-muted/20'>
                <div>
                  <span className='text-[10px] text-muted-foreground block'>Kho lưu trữ GitHub</span>
                  <a
                    href={selectedReq.canonical_repo || selectedReq.repo_url}
                    target='_blank'
                    rel='noreferrer'
                    className='font-mono font-semibold text-primary hover:underline break-all'
                  >
                    {selectedReq.canonical_repo || selectedReq.repo_url}
                  </a>
                </div>
                <div>
                  <span className='text-[10px] text-muted-foreground block'>Trạng thái duyệt</span>
                  <div className='mt-0.5'>{getApprovalBadge(selectedReq.state)}</div>
                </div>
                <div>
                  <span className='text-[10px] text-muted-foreground block'>Nhánh / Tag</span>
                  <span className='font-mono font-semibold'>{selectedReq.requested_ref || 'HEAD'}</span>
                </div>
                <div>
                  <span className='text-[10px] text-muted-foreground block'>Commit SHA (40-hex)</span>
                  <span className='font-mono font-semibold truncate block' title={selectedReq.commit_sha}>
                    {selectedReq.commit_sha || 'Chưa phân giải'}
                  </span>
                </div>
                <div>
                  <span className='text-[10px] text-muted-foreground block'>Thời gian gửi</span>
                  <span className='text-foreground'>{formatDateTime(selectedReq.submitted_at).formatted}</span>
                </div>
                <div>
                  <span className='text-[10px] text-muted-foreground block'>Cập nhật lần cuối</span>
                  <span className='text-foreground'>{formatDateTime(selectedReq.updated_at).formatted}</span>
                </div>
              </div>

              {/* SHA-256 Digest if Approved */}
              {selectedReq.artifact_digest && (
                <div className='p-2.5 rounded-lg border bg-emerald-500/5 border-emerald-500/30'>
                  <span className='text-[10px] text-emerald-700 dark:text-emerald-400 font-bold block flex items-center gap-1'>
                    <Icons.check className='size-3.5' /> Chữ ký số SHA-256 Digest (Approved Store)
                  </span>
                  <code className='text-[11px] font-mono break-all text-foreground mt-1 block select-all'>
                    {selectedReq.artifact_digest}
                  </code>
                  {selectedReq.artifact_size_bytes ? (
                    <span className='text-[10px] text-muted-foreground mt-1 block'>
                      Kích thước gói nén an toàn:{' '}
                      {(selectedReq.artifact_size_bytes / 1024).toFixed(1)} KB
                    </span>
                  ) : null}
                </div>
              )}

              {/* Error box if Failed */}
              {selectedReq.error && (
                <div className='p-2.5 rounded-lg border bg-rose-500/10 border-rose-500/30 text-rose-700 dark:text-rose-400'>
                  <span className='font-bold block text-[11px] flex items-center gap-1'>
                    <Icons.alertCircle className='size-3.5' /> Lý do thất bại / Bị chặn
                  </span>
                  <p className='text-xs mt-1 font-mono break-all'>{selectedReq.error}</p>
                </div>
              )}

              {/* Scan Report Breakdown */}
              {selectedReq.report && (
                <div className='flex flex-col gap-2 p-2.5 rounded-lg border bg-card'>
                  <span className='font-semibold text-foreground text-[11px] flex items-center justify-between'>
                    <span>Kết quả quét tĩnh bằng Trivy ({selectedReq.report.scanner_version})</span>
                    <Badge variant='outline' className='text-[9px] font-mono'>
                      Thời gian quét: {selectedReq.report.scan_duration_ms}ms
                    </Badge>
                  </span>

                  <div className='grid grid-cols-4 gap-1.5 text-center'>
                    <div className='p-1.5 rounded bg-muted/30 border'>
                      <span className='text-[9px] text-muted-foreground block'>Critical</span>
                      <span className='font-bold text-rose-600 text-sm'>
                        {selectedReq.report.vulnerabilities.critical}
                      </span>
                    </div>
                    <div className='p-1.5 rounded bg-muted/30 border'>
                      <span className='text-[9px] text-muted-foreground block'>High</span>
                      <span className='font-bold text-amber-600 text-sm'>
                        {selectedReq.report.vulnerabilities.high}
                      </span>
                    </div>
                    <div className='p-1.5 rounded bg-muted/30 border'>
                      <span className='text-[9px] text-muted-foreground block'>Secrets</span>
                      <span className='font-bold text-rose-600 text-sm'>
                        {selectedReq.report.secrets_count}
                      </span>
                    </div>
                    <div className='p-1.5 rounded bg-muted/30 border'>
                      <span className='text-[9px] text-muted-foreground block'>Scorecard</span>
                      <span className='font-bold text-blue-600 text-sm'>
                        {selectedReq.report.scorecard?.score ?? 'N/A'}
                      </span>
                    </div>
                  </div>

                  {selectedReq.report.policy_reasons?.length ? (
                    <div className='mt-1.5'>
                      <span className='text-[10px] text-muted-foreground font-semibold block'>
                        Đánh giá chính sách:
                      </span>
                      <ul className='list-disc list-inside text-[11px] space-y-0.5 text-foreground mt-0.5'>
                        {selectedReq.report.policy_reasons.map((reason, idx) => (
                          <li key={idx}>{reason}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              )}

              {/* Admin Decision Note if present */}
              {selectedReq.decision && (
                <div className='p-2.5 rounded-lg border bg-muted/40'>
                  <span className='font-semibold text-foreground text-[11px] block'>
                    Biên bản quyết định của Quản trị viên
                  </span>
                  <div className='text-[11px] text-muted-foreground mt-1'>
                    <div>Người duyệt: <span className='font-mono text-foreground font-semibold'>@{selectedReq.decision.reviewer}</span></div>
                    <div>Thời điểm: {formatDateTime(selectedReq.decision.reviewed_at).formatted}</div>
                    <div className='mt-1 text-foreground italic'>&ldquo;{selectedReq.decision.reason}&rdquo;</div>
                  </div>
                </div>
              )}
            </div>
          </DialogContent>
        </Dialog>
      )}
    </Card>
  );
}
