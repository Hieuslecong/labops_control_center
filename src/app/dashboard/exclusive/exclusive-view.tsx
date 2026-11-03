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
            ENTERPRISE PRO
          </Badge>
        </div>

        <div className='grid grid-cols-1 md:grid-cols-2 gap-4'>
          <Card className='bg-card'>
            <CardHeader>
              <CardTitle className='text-sm font-bold flex items-center gap-2'>
                <Icons.server className='size-4 text-primary' /> Cấu hình Cụm Vật lý Độc lập
              </CardTitle>
              <CardDescription className='text-xs'>
                Kiểm soát cấp độ Kernel, IOMMU Groups và PCIe Passthrough cho NVIDIA A30
              </CardDescription>
            </CardHeader>
            <CardContent className='text-xs text-muted-foreground space-y-2'>
              <p>• Phân chia VRAM phần cứng MIG (Multi-Instance GPU) hoặc Driver-enforced MPS.</p>
              <p>• Giao thức WireGuard Site-to-Site giữa Hà Nội và TP.HCM với MTU tối ưu hóa.</p>
              <p>• ZFS Storage Pool với nén zstd và kiểm tra toàn vẹn Scrub định kỳ.</p>
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
              <p>• 100% Containers chạy ở chế độ Unprivileged (security.idmap.isolated=true).</p>
              <p>• Cổng SSH trung gian chuyên biệt theo dải 22000-29999 cho từng thành viên.</p>
              <p>• Khóa riêng tư Ed25519 được sinh phía Client, không lưu trữ trên máy chủ.</p>
            </CardContent>
          </Card>
        </div>
      </div>
    </PageContainer>
  );
}
