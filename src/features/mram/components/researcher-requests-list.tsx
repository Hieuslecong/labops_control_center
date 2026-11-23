'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { labopsCall } from '@/features/labops/api';
import { ServerAccessRequestItem } from '../types';
import { SshRequestHistoryCard } from './researcher-ssh-request-form';

export function ResearcherRequestsList() {
  const [requests, setRequests] = useState<ServerAccessRequestItem[]>([]);
  const [loading, setLoading] = useState(true);

  const loadList = useCallback(async () => {
    setLoading(true);
    const res = await labopsCall<{ requests: ServerAccessRequestItem[] }>('me/ssh-requests');
    if (res.ok && res.data) {
      setRequests(res.data.requests || []);
    }
    setLoading(false);
  }, []);

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void loadList();
  }, [loadList]);
  /* oxlint-enable react/set-state-in-effect */

  return (
    <div className='max-w-3xl space-y-4'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <div className='text-sm text-muted-foreground'>
          Theo dõi toàn bộ các đơn xin cấp quyền SSH đã nộp và trạng thái phê duyệt.
        </div>
        <div className='flex gap-2'>
          <Button variant='outline' size='sm' onClick={() => void loadList()}>
            Làm mới
          </Button>
          <Link href='/dashboard/researcher/ssh-request'>
            <Button size='sm'>+ Đăng ký cấp SSH mới</Button>
          </Link>
        </div>
      </div>

      {loading ? (
        <Card>
          <CardContent className='p-6 text-sm text-muted-foreground'>Đang tải...</CardContent>
        </Card>
      ) : (
        <SshRequestHistoryCard requests={requests} onReload={() => void loadList()} />
      )}
    </div>
  );
}
