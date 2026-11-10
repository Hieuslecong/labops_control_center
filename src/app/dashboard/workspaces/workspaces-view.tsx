'use client';

import React from 'react';
import PageContainer from '@/components/layout/page-container';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import Link from 'next/link';

export default function WorkspacesView() {
  const members = useLabOpsStore((state) => state.members);
  const memberSyncError = useLabOpsStore((state) => state.memberSyncError);
  const fetchAccessMembers = useLabOpsStore((state) => state.fetchAccessMembers);

  React.useEffect(() => { void fetchAccessMembers(); }, [fetchAccessMembers]);

  return (
    <PageContainer
      pageTitle='Workspace được cấp'
      pageDescription='Mapping workspace từ backend; trạng thái container chỉ hiển thị khi agent xác minh.'
    >
      <div className='flex flex-col gap-4'>
        <div className='flex items-center justify-between'>
          <p className='text-xs text-muted-foreground'>
            Bản ghi cấp phát không đồng nghĩa container đã được tạo hoặc cô lập.
          </p>
          <Button
            size='sm'
            nativeButton={false}
            render={<Link href='/dashboard/users' aria-label='Cấp phát workspace mới' />}
            className='h-8 text-xs gap-1.5'
          >
            <Icons.add className='size-3.5' /> Cấp phát mới
          </Button>
        </div>

        <div className='grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4'>
          {members.map((m) => (
            <Card key={m.id} className='bg-card hover:shadow-md transition-shadow'>
              <CardHeader className='pb-3'>
                <div className='flex items-start justify-between'>
                  <div className='flex items-center gap-2'>
                    <div className='size-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center font-bold text-xs'>
                      <Icons.workspace className='size-4' />
                    </div>
                    <div>
                      <CardTitle className='text-sm font-bold font-mono'>
                        {m.workspaceName}
                      </CardTitle>
                      <CardDescription className='text-xs'>
                        @{m.username} ({m.fullName})
                      </CardDescription>
                    </div>
                  </div>
                  <Badge
                    variant='outline'
                    className={
                      m.status === 'ACTIVE'
                        ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30 text-[10px]'
                        : 'bg-amber-500/10 text-amber-600 border-amber-500/30 text-[10px]'
                    }
                  >
                    {m.status}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className='pt-0 text-xs space-y-2'>
                <div className='p-2 rounded-md bg-muted/30 border space-y-1 font-mono text-[11px]'>
                  <div className='flex justify-between'>
                    <span className='text-muted-foreground'>Máy chủ:</span>
                    <span className='font-semibold uppercase'>{m.assignedServer}</span>
                  </div>
                  <div className='flex justify-between'>
                    <span className='text-muted-foreground'>Mount point yêu cầu:</span>
                    <span>{m.mountPath}</span>
                  </div>
                  <div className='flex justify-between'>
                    <span className='text-muted-foreground'>Quota yêu cầu:</span>
                    <span>{m.storageQuotaGb} GB</span>
                  </div>
                  <div className='flex justify-between'>
                    <span className='text-muted-foreground'>Workspace:</span>
                    <span>{m.workspaceStatus || 'NOT_VERIFIED'}</span>
                  </div>
                  <div className='flex justify-between'>
                    <span className='text-muted-foreground'>Teleport:</span>
                    <span>{m.teleportStatus || 'NOT_CONFIGURED'}</span>
                  </div>
                </div>

                <div className='flex items-center justify-between pt-1'>
                  <Badge variant='secondary' className='text-[10px] font-mono'>
                    {m.vramPolicy}
                  </Badge>
                  <Link
                    href={`/dashboard/users?search=${m.username}`}
                    className='text-xs text-primary font-medium hover:underline flex items-center gap-1'
                  >
                    Quản trị <Icons.arrowRight className='size-3' />
                  </Link>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
        {memberSyncError && <p className='text-sm text-amber-700'>{memberSyncError}</p>}
        {!members.length && <p className='rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground'>Chưa có workspace record đã tải từ backend.</p>}
      </div>
    </PageContainer>
  );
}
