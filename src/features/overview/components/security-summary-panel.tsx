'use client';

import React, { useMemo } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { toast } from 'sonner';
import { labopsCall } from '@/features/labops/api';
import { useRepositoryRequestsQuery } from '@/features/labops/queries';
import {
  getCloneStatusMeta,
  getPostCloneReviewMeta,
  resolveCloneStatus,
  resolvePostCloneReviewStatus
} from '@/features/security/utils/repo-status';
import Link from 'next/link';

export function SecuritySummaryPanel() {
  const requestsQuery = useRepositoryRequestsQuery({ intervalMs: 15_000 });
  const requests = useMemo(() => requestsQuery.data ?? [], [requestsQuery.data]);
  const loading = requestsQuery.isPending;

  const handleDecision = async (id: string, decision: 'APPROVED' | 'REJECTED') => {
    const res = await labopsCall(`repositories/requests/${id}/decision`, {
      method: 'POST',
      body: { decision, reason: `Admin quyết định nhanh từ bảng điều khiển Tổng quan` }
    });
    if (res.ok) {
      toast.success(`Đã ghi nhận quyết định ${decision === 'APPROVED' ? 'Duyệt' : 'Từ chối'}`);
      void requestsQuery.refetch();
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
                Auto Security Bot quét cách ly bằng Trivy sau khi clone &amp; cảnh báo Zalo Admin
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
              <span>
                Chưa có yêu cầu clone nào đang chờ. Gõ <code>gitaiot clone &lt;repo&gt;</code> để
                quét mã.
              </span>
            </div>
          ) : (
            topRequests.map((item) => {
              const cloneMeta = getCloneStatusMeta(resolveCloneStatus(item));
              const reviewStatus = resolvePostCloneReviewStatus(item);
              const reviewMeta = getPostCloneReviewMeta(reviewStatus);
              return (
                <div
                  key={item.id}
                  className='p-2.5 rounded-lg border bg-muted/20 flex flex-col gap-1.5 text-xs'
                >
                  <div className='flex items-center justify-between gap-1'>
                    <div className='flex items-center gap-1.5 truncate'>
                      <Icons.github className='size-3.5 shrink-0 text-muted-foreground' />
                      <span className='font-mono font-semibold text-foreground truncate'>
                        {item.owner && item.repo_name
                          ? `${item.owner}/${item.repo_name}`
                          : item.repo_name || item.canonical_repo}
                      </span>
                      <span className='font-mono text-xs text-muted-foreground'>
                        (
                        {item.commit_sha
                          ? item.commit_sha.slice(0, 7)
                          : item.requested_ref || 'HEAD'}
                        )
                      </span>
                    </div>
                    <div className='flex items-center gap-1 shrink-0'>
                      <Badge
                        variant='outline'
                        className={`${cloneMeta.className} text-xs font-semibold`}
                      >
                        Clone: {cloneMeta.label}
                      </Badge>
                      <Badge
                        variant='outline'
                        className={`${reviewMeta.className} text-xs font-semibold`}
                      >
                        {reviewMeta.label}
                      </Badge>
                    </div>
                  </div>

                  <div className='text-xs text-muted-foreground line-clamp-1'>
                    {item.report?.policy_reasons?.length ? (
                      item.report.policy_reasons[0]
                    ) : item.error ? (
                      <span className='text-rose-600 dark:text-rose-400'>{item.error}</span>
                    ) : (
                      'Kho mã an toàn đạt chuẩn Kiểm duyệt Cách ly'
                    )}
                  </div>

                  <div className='flex items-center justify-between text-xs text-muted-foreground pt-1 border-t border-border/40'>
                    <span>
                      Bởi:{' '}
                      <span className='font-mono font-medium text-foreground'>
                        @{item.submitted_by}
                      </span>
                      {item.report?.vulnerabilities && (
                        <>
                          {' '}
                          • Lỗ hổng:{' '}
                          <strong
                            className={
                              item.report.vulnerabilities.critical > 0 ||
                              item.report.vulnerabilities.high > 0
                                ? 'text-rose-500'
                                : 'text-emerald-500'
                            }
                          >
                            {item.report.vulnerabilities.critical} Crit /{' '}
                            {item.report.vulnerabilities.high} High
                          </strong>
                        </>
                      )}
                    </span>

                    {reviewStatus === 'REVIEW_REQUIRED' && (
                      <div className='flex items-center gap-1'>
                        <Button
                          size='sm'
                          variant='outline'
                          onClick={() => void handleDecision(item.id, 'APPROVED')}
                          className='h-6 text-xs px-2 text-emerald-600 hover:text-emerald-700 hover:bg-emerald-500/10'
                        >
                          Duyệt
                        </Button>
                        <Button
                          size='sm'
                          variant='ghost'
                          onClick={() => void handleDecision(item.id, 'REJECTED')}
                          className='h-6 text-xs px-2 text-rose-600 hover:text-rose-700 hover:bg-rose-500/10'
                        >
                          Từ chối
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Trivy Scanner Status Footer */}
        <div className='p-2.5 rounded-lg border bg-background flex items-center justify-between text-xs'>
          <div className='flex items-center gap-2'>
            <div className='flex size-6 items-center justify-center rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'>
              <Icons.check className='size-3.5' />
            </div>
            <div>
              <span className='font-semibold block text-xs'>
                Repository Guard &amp; Trivy Scanner
              </span>
              <span className='text-xs text-muted-foreground'>
                Quét CVE tự động • Kiểm duyệt Cách ly (đồng bộ 15s)
              </span>
            </div>
          </div>
          <Badge
            variant='outline'
            className='text-emerald-600 border-emerald-500/30 text-xs font-mono'
          >
            {requests.length > 0 ? 'ĐANG GIÁM SÁT' : 'SẴN SÀNG'}
          </Badge>
        </div>
      </CardContent>
    </Card>
  );
}
