'use client';

import React from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import Link from 'next/link';

function getSecurityStatusBadge(status: string) {
  switch (status) {
    case 'APPROVED':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px]'
        >
          APPROVED
        </Badge>
      );
    case 'MANUAL_REVIEW':
      return (
        <Badge
          variant='outline'
          className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[10px]'
        >
          MANUAL REVIEW
        </Badge>
      );
    case 'QUARANTINED':
      return (
        <Badge
          variant='outline'
          className='bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30 text-[10px]'
        >
          QUARANTINED
        </Badge>
      );
    case 'SCANNING':
    default:
      return (
        <Badge
          variant='outline'
          className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[10px]'
        >
          SCANNING
        </Badge>
      );
  }
}

export function SecuritySummaryPanel() {
  const reviews = useLabOpsStore((state) => state.securityReviews);
  const updateSecurityReview = useLabOpsStore((state) => state.updateSecurityReview);

  return (
    <Card className='border bg-card h-full flex flex-col justify-between'>
      <CardHeader className='pb-3 pt-4'>
        <div className='flex items-center justify-between'>
          <div className='flex items-center gap-2'>
            <Icons.shield className='size-5 text-rose-600 dark:text-rose-400' />
            <div>
              <CardTitle className='text-base font-bold tracking-tight'>
                Kiểm duyệt Mã nguồn & An toàn Hạ tầng
              </CardTitle>
              <p className='text-xs text-muted-foreground'>
                Quy trình quét cách ly Sandbox cho các GitHub repo trước khi chạy trên GPU
              </p>
            </div>
          </div>
          <Link
            href='/dashboard/security'
            className='text-xs font-medium text-primary hover:underline flex items-center gap-1'
          >
            Quản lý an ninh <Icons.arrowRight className='size-3.5' />
          </Link>
        </div>
      </CardHeader>

      <CardContent className='pt-0 pb-4 space-y-3 flex-1 flex flex-col justify-between'>
        {/* Repo review list */}
        <div className='space-y-2'>
          {reviews.slice(0, 3).map((item) => (
            <div
              key={item.id}
              className='p-2.5 rounded-lg border bg-muted/20 flex flex-col gap-1.5 text-xs'
            >
              <div className='flex items-center justify-between gap-1'>
                <div className='flex items-center gap-1.5 truncate'>
                  <Icons.github className='size-3.5 shrink-0 text-muted-foreground' />
                  <span className='font-mono font-semibold text-foreground truncate'>
                    {item.repoName}
                  </span>
                  <span className='font-mono text-[10px] text-muted-foreground'>
                    ({item.commitSha})
                  </span>
                </div>
                {getSecurityStatusBadge(item.status)}
              </div>

              <p className='text-[11px] text-muted-foreground line-clamp-2'>{item.summary}</p>

              <div className='flex items-center justify-between text-[10px] text-muted-foreground pt-1 border-t border-border/40'>
                <span>
                  Bởi:{' '}
                  <span className='font-mono font-medium text-foreground'>@{item.submittedBy}</span>{' '}
                  • Điểm rủi ro:{' '}
                  <strong className={item.riskScore > 50 ? 'text-rose-500' : 'text-emerald-500'}>
                    {item.riskScore}/100
                  </strong>
                </span>

                {item.status === 'MANUAL_REVIEW' && (
                  <div className='flex items-center gap-1'>
                    <Button
                      size='sm'
                      variant='outline'
                      onClick={() =>
                        updateSecurityReview(item.id, 'APPROVED', 'Lê Chí Hiếu (Admin)')
                      }
                      className='h-6 text-[10px] px-2 text-emerald-600 hover:text-emerald-700 hover:bg-emerald-500/10'
                    >
                      Duyệt
                    </Button>
                    <Button
                      size='sm'
                      variant='ghost'
                      onClick={() =>
                        updateSecurityReview(item.id, 'REJECTED', 'Lê Chí Hiếu (Admin)')
                      }
                      className='h-6 text-[10px] px-2 text-rose-600 hover:text-rose-700 hover:bg-rose-500/10'
                    >
                      Từ chối
                    </Button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* ZFS Backup status */}
        <div className='p-2.5 rounded-lg border bg-background flex items-center justify-between text-xs'>
          <div className='flex items-center gap-2'>
            <div className='flex size-6 items-center justify-center rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'>
              <Icons.database className='size-3.5' />
            </div>
            <div>
              <span className='font-semibold block text-xs'>Sao lưu ZFS Snapshots</span>
              <span className='text-[10px] text-muted-foreground'>
                Track: 4.3 TB (Snapshot 04:00 hôm nay) • WS: 1.5 TB (OK)
              </span>
            </div>
          </div>
          <Badge
            variant='outline'
            className='text-emerald-600 border-emerald-500/30 text-[10px] font-mono'
          >
            ĐỒNG BỘ 100%
          </Badge>
        </div>
      </CardContent>
    </Card>
  );
}
