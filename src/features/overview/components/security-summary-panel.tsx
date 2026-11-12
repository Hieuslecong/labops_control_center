'use client';

import React, { useEffect, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { toast } from 'sonner';
import { labopsCall } from '@/features/labops/api';
import { RepositoryGuardRequest } from '@/types/labops';
import Link from 'next/link';

function getSecurityStatusBadge(status: string) {
  switch (status) {
    case 'AUTO_APPROVED':
    case 'APPROVED':
    case 'MANUALLY_APPROVED':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-semibold'
        >
          {status === 'AUTO_APPROVED' ? 'TỰ ĐỘNG DUYỆT' : 'ADMIN ĐÃ DUYỆT'}
        </Badge>
      );
    case 'DELIVERED':
      return (
        <Badge
          variant='outline'
          className='bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[10px] font-semibold'
        >
          ĐÃ PHÂN PHỐI
        </Badge>
      );
    case 'REVIEW_REQUIRED':
    case 'MANUAL_REVIEW':
      return (
        <Badge
          variant='outline'
          className='bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[10px] font-semibold'
        >
          CẦN ADMIN DUYỆT
        </Badge>
      );
    case 'REJECTED':
      return (
        <Badge
          variant='outline'
          className='bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/30 text-[10px] font-semibold'
        >
          BỊ TỪ CHỐI
        </Badge>
      );
    case 'FAILED':
      return (
        <Badge
          variant='outline'
          className='bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30 text-[10px]'
        >
          THẤT BẠI
        </Badge>
      );
    default:
      return (
        <Badge variant='outline' className='text-[10px] font-mono'>
          {status}
        </Badge>
      );
  }
}

export function SecuritySummaryPanel() {
  const [requests, setRequests] = useState<RepositoryGuardRequest[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchLiveRequests = async () => {
    try {
      const res = await labopsCall<RepositoryGuardRequest[]>('repositories/requests');
      if (res.ok && Array.isArray(res.data)) {
        setRequests(res.data);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchLiveRequests();
    const interval = setInterval(() => {
      void fetchLiveRequests();
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleDecision = async (id: string, decision: 'APPROVED' | 'REJECTED') => {
    const res = await labopsCall(`repositories/requests/${id}/decision`, {
      method: 'POST',
      body: { decision, reason: `Admin quyết định nhanh từ bảng điều khiển Tổng quan` }
    });
    if (res.ok) {
      toast.success(`Đã ghi nhận quyết định ${decision === 'APPROVED' ? 'Duyệt' : 'Từ chối'}`);
      void fetchLiveRequests();
    } else {
      toast.error('Không thể cập nhật quyết định', { description: res.error });
    }
  };

  const topRequests = requests.slice(0, 3);

  return (
    <Card className='border bg-card h-full flex flex-col justify-between min-w-0'>
      <CardHeader className='pb-3 pt-4'>
        <div className='flex items-center justify-between'>
          <div className='flex items-center gap-2'>
            <Icons.shield className='size-5 text-emerald-600 dark:text-emerald-400' />
            <div>
              <CardTitle className='text-base font-bold tracking-tight'>
                Kiểm duyệt Mã nguồn & An toàn Hạ tầng
              </CardTitle>
              <p className='text-xs text-muted-foreground'>
                Auto Security Bot quét cách ly bằng Trivy cho các repo trước khi chạy trên GPU
              </p>
            </div>
          </div>
          <Link
            href='/dashboard/security'
            className='text-xs font-medium text-primary hover:underline flex items-center gap-1 shrink-0'
          >
            Quản lý an ninh <Icons.arrowRight className='size-3.5' />
          </Link>
        </div>
      </CardHeader>

      <CardContent className='pt-0 pb-4 space-y-3 flex-1 flex flex-col justify-between'>
        {/* Repo review list */}
        <div className='space-y-2'>
          {loading && requests.length === 0 ? (
            <p className='rounded-lg border border-dashed p-4 text-xs text-muted-foreground text-center'>
              Đang đồng bộ trạng thái an ninh từ Auto Security Bot...
            </p>
          ) : topRequests.length === 0 ? (
            <div className='rounded-lg border border-dashed p-4 text-xs text-muted-foreground flex flex-col items-center gap-1 text-center'>
              <Icons.shield className='size-5 text-emerald-500' />
              <span className='font-medium text-foreground'>Auto Security Bot đang hoạt động</span>
              <span>Chưa có yêu cầu clone nào đang chờ. Gõ <code>gitlabops clone &lt;repo&gt;</code> để quét mã.</span>
            </div>
          ) : (
            topRequests.map((item) => (
              <div
                key={item.id}
                className='p-2.5 rounded-lg border bg-muted/20 flex flex-col gap-1.5 text-xs'
              >
                <div className='flex items-center justify-between gap-1'>
                  <div className='flex items-center gap-1.5 truncate'>
                    <Icons.github className='size-3.5 shrink-0 text-muted-foreground' />
                    <span className='font-mono font-semibold text-foreground truncate'>
                      {item.owner && item.repo_name ? `${item.owner}/${item.repo_name}` : item.repo_name || item.canonical_repo}
                    </span>
                    <span className='font-mono text-[10px] text-muted-foreground'>
                      ({item.commit_sha ? item.commit_sha.slice(0, 7) : item.requested_ref || 'HEAD'})
                    </span>
                  </div>
                  {getSecurityStatusBadge(item.state)}
                </div>

                <div className='text-[11px] text-muted-foreground line-clamp-1'>
                  {item.report?.policy_reasons?.length ? (
                    item.report.policy_reasons[0]
                  ) : item.error ? (
                    <span className='text-rose-600 dark:text-rose-400'>{item.error}</span>
                  ) : (
                    'Kho mã an toàn đạt chuẩn Zero-Trust'
                  )}
                </div>

                <div className='flex items-center justify-between text-[10px] text-muted-foreground pt-1 border-t border-border/40'>
                  <span>
                    Bởi: <span className='font-mono font-medium text-foreground'>@{item.submitted_by}</span>
                    {item.report?.vulnerabilities && (
                      <>
                        {' '}• Lỗ hổng:{' '}
                        <strong className={item.report.vulnerabilities.critical > 0 || item.report.vulnerabilities.high > 0 ? 'text-rose-500' : 'text-emerald-500'}>
                          {item.report.vulnerabilities.critical} Crit / {item.report.vulnerabilities.high} High
                        </strong>
                      </>
                    )}
                  </span>

                  {item.state === 'REVIEW_REQUIRED' && (
                    <div className='flex items-center gap-1'>
                      <Button
                        size='sm'
                        variant='outline'
                        onClick={() => void handleDecision(item.id, 'APPROVED')}
                        className='h-6 text-[10px] px-2 text-emerald-600 hover:text-emerald-700 hover:bg-emerald-500/10'
                      >
                        Duyệt
                      </Button>
                      <Button
                        size='sm'
                        variant='ghost'
                        onClick={() => void handleDecision(item.id, 'REJECTED')}
                        className='h-6 text-[10px] px-2 text-rose-600 hover:text-rose-700 hover:bg-rose-500/10'
                      >
                        Từ chối
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Trivy Scanner Status Footer */}
        <div className='p-2.5 rounded-lg border bg-background flex items-center justify-between text-xs'>
          <div className='flex items-center gap-2'>
            <div className='flex size-6 items-center justify-center rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'>
              <Icons.check className='size-3.5' />
            </div>
            <div>
              <span className='font-semibold block text-xs'>Trivy Security Scanner 0.76.2</span>
              <span className='text-[10px] text-muted-foreground'>
                Cơ sở dữ liệu CVE tự động cập nhật • Zero-Trust Quarantine
              </span>
            </div>
          </div>
          <Badge
            variant='outline'
            className='text-emerald-600 border-emerald-500/30 text-[10px] font-mono'
          >
            ACTIVE_RUNNING
          </Badge>
        </div>
      </CardContent>
    </Card>
  );
}
