'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { labopsCall } from '@/features/labops/api';
import { formatRemainingSla, ServerAccessRequestItem } from '../types';
import { toast } from 'sonner';

const STATUS_FILTERS = [
  { key: 'ALL', label: 'Tất cả' },
  { key: 'PENDING_APPROVAL', label: 'Chờ duyệt (SLA 24h)' },
  { key: 'NEED_INFO', label: 'Cần bổ sung' },
  { key: 'VERIFIED_READY', label: 'Sẵn sàng (Verified)' },
  { key: 'EXPIRED', label: 'Quá hạn 24h' },
  { key: 'REJECTED', label: 'Từ chối' }
];

function reqBadgeClass(st: string): string {
  switch (st) {
    case 'VERIFIED_READY':
      return 'bg-emerald-500/10 text-emerald-700 border-emerald-300';
    case 'PENDING_APPROVAL':
    case 'PROVISIONING':
      return 'bg-amber-500/10 text-amber-700 border-amber-300';
    case 'EXPIRED':
    case 'REJECTED':
    case 'PROVISIONING_FAILED':
      return 'bg-rose-500/10 text-rose-700 border-rose-300';
    default:
      return '';
  }
}

function getQueueActionMeta(status: string): {
  label: string;
  variant: 'default' | 'outline';
  className?: string;
} {
  switch (status) {
    case 'VERIFIED_READY':
    case 'APPROVED':
      return {
        label: 'Đã phê duyệt',
        variant: 'outline',
        className: 'border-emerald-400 text-emerald-700 dark:text-emerald-300'
      };
    case 'REJECTED':
      return {
        label: 'Đã từ chối',
        variant: 'outline',
        className: 'border-rose-400 text-rose-700 dark:text-rose-300'
      };
    case 'EXPIRED':
      return { label: 'Đã quá hạn 24h', variant: 'outline' };
    case 'PENDING_APPROVAL':
    case 'NEED_INFO':
      return { label: 'Xem xét & Phê duyệt', variant: 'default' };
    default:
      return { label: 'Xem chi tiết', variant: 'outline' };
  }
}

export function AdminSshRequestsQueue() {
  const [requests, setRequests] = useState<ServerAccessRequestItem[]>([]);
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [loading, setLoading] = useState(true);
  const [sweeping, setSweeping] = useState(false);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    const res = await labopsCall<{ requests: ServerAccessRequestItem[] }>(
      `admin/ssh-requests?status=${encodeURIComponent(statusFilter)}`
    );
    if (res.ok && res.data) {
      setRequests(res.data.requests || []);
    } else {
      toast.error('Không thể tải hàng đợi yêu cầu SSH', { description: res.error });
    }
    setLoading(false);
  }, [statusFilter]);

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void loadQueue();
  }, [loadQueue]);
  /* oxlint-enable react/set-state-in-effect */

  const handleSweep = async () => {
    setSweeping(true);
    const res = await labopsCall<{
      expired_requests: string[];
      expired_ssh_accounts: string[];
    }>('admin/mram/sweep-sla', { method: 'POST' });
    setSweeping(false);
    if (res.ok && res.data) {
      toast.success(
        `Đã rà soát SLA: ${res.data.expired_requests.length} yêu cầu quá hạn 24h, ${res.data.expired_ssh_accounts.length} SSH sinh viên hết hạn.`
      );
      void loadQueue();
    } else {
      toast.error('Lỗi rà soát SLA', { description: res.error });
    }
  };

  return (
    <div className='space-y-4'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <div className='flex flex-wrap gap-1.5'>
          {STATUS_FILTERS.map((f) => (
            <Button
              key={f.key}
              size='sm'
              variant={statusFilter === f.key ? 'default' : 'outline'}
              onClick={() => setStatusFilter(f.key)}
            >
              {f.label}
            </Button>
          ))}
        </div>
        <div className='flex gap-2'>
          <Button
            size='sm'
            variant='outline'
            onClick={() => void handleSweep()}
            disabled={sweeping}
          >
            {sweeping ? 'Đang quét SLA...' : 'Quét hạn 24h & Hết hạn SSH'}
          </Button>
          <Button size='sm' variant='outline' onClick={() => void loadQueue()}>
            Làm mới
          </Button>
        </div>
      </div>

      {loading ? (
        <Card>
          <CardContent className='p-6 text-sm text-muted-foreground'>
            Đang tải danh sách yêu cầu...
          </CardContent>
        </Card>
      ) : requests.length === 0 ? (
        <Card>
          <CardContent className='p-8 text-center text-sm text-muted-foreground'>
            Không có yêu cầu cấp SSH nào trong bộ lọc hiện tại.
          </CardContent>
        </Card>
      ) : (
        <div className='space-y-3'>
          {requests.map((r) => (
            <Card key={r.id}>
              <CardHeader className='pb-2'>
                <div className='flex flex-wrap items-start justify-between gap-2'>
                  <div>
                    <CardTitle className='text-base flex flex-wrap items-center gap-2'>
                      <span>{r.full_name}</span>
                      <Badge variant='outline' className='font-mono text-xs'>
                        @{r.username}
                      </Badge>
                      <Badge variant='secondary' className='text-xs'>
                        {r.member_category}
                      </Badge>
                      <Badge variant='outline' className='text-xs'>
                        {r.request_type}
                      </Badge>
                    </CardTitle>
                    <CardDescription className='text-xs mt-1'>
                      Mã đơn: <span className='font-mono'>{r.id}</span> • Dự án:{' '}
                      <strong>{r.project_title}</strong> • GVHD:{' '}
                      <strong>{r.supervisor_name || 'N/A'}</strong>
                    </CardDescription>
                  </div>
                  <div className='flex flex-wrap items-center gap-2'>
                    <Badge variant='outline' className={reqBadgeClass(r.status)}>
                      {r.status}
                    </Badge>
                    {r.status === 'PENDING_APPROVAL' && (
                      <Badge variant='outline' className='font-mono text-xs text-amber-700'>
                        SLA còn: {formatRemainingSla(r.remaining_review_seconds)}
                      </Badge>
                    )}
                    {(() => {
                      const actionMeta = getQueueActionMeta(r.status);
                      return (
                        <Link href={`/dashboard/ssh-requests/${encodeURIComponent(r.id)}/review`}>
                          <Button
                            size='sm'
                            variant={actionMeta.variant}
                            className={actionMeta.className}
                          >
                            {actionMeta.label}
                          </Button>
                        </Link>
                      );
                    })()}
                  </div>
                </div>
              </CardHeader>
              <CardContent className='pt-0 text-xs space-y-2'>
                <div className='rounded-md bg-muted/40 p-2.5'>
                  <strong>Lý do sử dụng máy chủ:</strong> {r.registration_reason}
                </div>
                <div className='flex flex-wrap gap-3 text-muted-foreground font-mono'>
                  <span>
                    Đề xuất:{' '}
                    {r.recommendation
                      ? `${r.recommendation.recommended_host_id || 'Chờ chọn'} | ${r.recommendation.cpu_cores}C / ${r.recommendation.ram_gb}G / ${r.recommendation.storage_quota_gb}GiB | GPU: ${r.recommendation.gpu_mode}`
                      : 'N/A'}
                  </span>
                  <span>
                    • Gửi lúc:{' '}
                    {r.submitted_at ? new Date(r.submitted_at).toLocaleString('vi-VN') : '—'}
                  </span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
