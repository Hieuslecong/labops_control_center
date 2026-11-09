'use client';

import React from 'react';
import PageContainer from '@/components/layout/page-container';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import Link from 'next/link';

export default function TeamView() {
  const members = useLabOpsStore((state) => state.members);

  return (
    <PageContainer
      pageTitle='Đội ngũ Nghiên cứu & Dự án'
      pageDescription='Quản trị các nhóm dự án AI, phân quyền truy cập GPU và phân bổ tài nguyên'
    >
      <div className='flex flex-col gap-5'>
        <div className='grid grid-cols-1 md:grid-cols-3 gap-4'>
          <Card className='p-4 bg-card'>
            <div className='text-xs font-semibold text-muted-foreground uppercase'>
              Tổng số Nhà nghiên cứu
            </div>
            <div className='text-2xl font-bold mt-1'>{members.length}</div>
            <div className='text-xs text-emerald-600 mt-1 flex items-center gap-1'>
              <span className='size-2 rounded-full bg-emerald-500'></span>
              {members.filter((m) => m.sshStatus === 'ONLINE').length} Đang kết nối SSH
            </div>
          </Card>
          <Card className='p-4 bg-card'>
            <div className='text-xs font-semibold text-muted-foreground uppercase'>
              Dự án Active
            </div>
            <div className='text-2xl font-bold mt-1'>3 Nhóm đề tài</div>
            <div className='text-xs text-muted-foreground mt-1'>
              LLM Reasoning, Vision AI, Speech
            </div>
          </Card>
          <Card className='p-4 bg-card'>
            <div className='text-xs font-semibold text-muted-foreground uppercase'>
              Chính sách Sudo Host
            </div>
            <div className='text-2xl font-bold mt-1 text-emerald-600'>
              0 / {members.length} Sudo
            </div>
            <div className='text-xs text-muted-foreground mt-1'>
              Strict non-root unprivileged isolation
            </div>
          </Card>
        </div>

        <Card className='bg-card'>
          <CardHeader className='pb-3'>
            <div className='flex items-center justify-between'>
              <div>
                <CardTitle className='text-base font-bold'>Danh sách Thành viên Nhóm</CardTitle>
                <CardDescription className='text-xs'>
                  Chi tiết phân quyền và môi trường làm việc
                </CardDescription>
              </div>
              <Button
                size='sm'
                nativeButton={false}
                render={<Link href='/dashboard/users' aria-label='Quản trị thành viên phòng lab' />}
                className='h-8 text-xs gap-1.5'
              >
                <Icons.teams className='size-3.5' /> Quản trị thành viên
              </Button>
            </div>
          </CardHeader>
          <CardContent className='pt-0'>
            <div className='divide-y text-xs'>
              {members.map((m) => (
                <div key={m.id} className='py-3 flex items-center justify-between'>
                  <div className='flex items-center gap-3'>
                    <div className='size-8 rounded-full bg-primary/10 text-primary font-bold flex items-center justify-center text-xs'>
                      {m.fullName.slice(0, 2).toUpperCase()}
                    </div>
                    <div>
                      <div className='font-semibold'>
                        {m.fullName}{' '}
                        <span className='text-muted-foreground font-mono font-normal'>
                          @{m.username}
                        </span>
                      </div>
                      <div className='text-[11px] text-muted-foreground'>
                        {m.researchGroup} • {m.email}
                      </div>
                    </div>
                  </div>
                  <div className='flex items-center gap-3'>
                    <Badge variant='outline' className='text-[10px] font-mono uppercase'>
                      {m.assignedServer}
                    </Badge>
                    <Badge variant='secondary' className='text-[10px]'>
                      {m.role}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
