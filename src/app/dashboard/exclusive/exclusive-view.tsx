'use client';

import React from 'react';
import PageContainer from '@/components/layout/page-container';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';

export default function ExclusiveView() {
  return (
    <PageContainer
      pageTitle='Khu vực Quản trị Nâng cao (Exclusive Access)'
      pageDescription='Tính năng bảo mật và quản lý đặc quyền cho Quản trị viên Hạ tầng LabOps'
    >
      <div className='space-y-6'>
        <div className='flex items-center gap-2'>
          <Icons.badgeCheck className='h-6 w-6 text-emerald-600' />
          <h2 className='text-lg font-bold'>Đặc quyền Hạ tầng Máy chủ AI</h2>
          <Badge variant='outline' className='bg-primary/10 text-primary text-[10px]'>
            NOT VERIFIED
          </Badge>
        </div>

        <div className='grid grid-cols-1 md:grid-cols-2 gap-4'>
          <Card className='bg-card'>
            <CardHeader>
              <CardTitle className='text-sm font-bold flex items-center gap-2'>
                <Icons.server className='size-4 text-primary' /> Cấu hình Cụm Vật lý Độc lập
              </CardTitle>
              <CardDescription className='text-xs'>
                Các khả năng quản trị phần cứng chưa được nối với host controller
              </CardDescription>
            </CardHeader>
            <CardContent className='text-xs text-muted-foreground space-y-2'>
              <p>• MIG, MPS, IOMMU và PCIe passthrough chưa được xác minh.</p>
              <p>• WireGuard Site-to-Site và Gateway chưa được cấu hình qua LabOps.</p>
              <p>• Storage pool, nén và scrub chưa có controller đã xác minh.</p>
            </CardContent>
          </Card>

          <Card className='bg-card'>
            <CardHeader>
              <CardTitle className='text-sm font-bold flex items-center gap-2'>
                <Icons.shield className='size-4 text-rose-600' /> Chính sách Cô lập An ninh Tuyệt
                đối
              </CardTitle>
              <CardDescription className='text-xs'>
                Không cấp quyền Sudo Host cho thành viên nghiên cứu
              </CardDescription>
            </CardHeader>
            <CardContent className='text-xs text-muted-foreground space-y-2'>
              <p>• Workspace isolation và quyền Linux/container chưa được xác minh.</p>
              <p>• SSH qua Teleport chưa được triển khai; không cấp cổng SSH riêng cho user mới.</p>
              <p>• LabOps chỉ nhận WireGuard public key; private key phải ở thiết bị người dùng.</p>
            </CardContent>
          </Card>
        </div>
      </div>
    </PageContainer>
  );
}
