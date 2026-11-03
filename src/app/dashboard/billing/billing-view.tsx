'use client';

import React from 'react';
import PageContainer from '@/components/layout/page-container';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

export default function BillingView() {
  return (
    <PageContainer
      pageTitle='Chi phí & Hạn ngạch Hạ tầng (Lab Allocation)'
      pageDescription='Giám sát chi phí điện toán, định mức GPU-hours và phân bổ tài nguyên nghiên cứu'
    >
      <div className='space-y-6'>
        <div className='grid grid-cols-1 md:grid-cols-3 gap-4'>
          <Card className='p-4 bg-card'>
            <div className='text-xs font-semibold text-muted-foreground uppercase'>
              Tổng GPU-hours Phân bổ
            </div>
            <div className='text-2xl font-bold mt-1'>5,000 h / tháng</div>
            <div className='text-xs text-muted-foreground mt-1'>Đã sử dụng: 384.2 h (7.7%)</div>
          </Card>
          <Card className='p-4 bg-card'>
            <div className='text-xs font-semibold text-muted-foreground uppercase'>
              Dung lượng ZFS Quota
            </div>
            <div className='text-2xl font-bold mt-1'>28.0 TB</div>
            <div className='text-xs text-muted-foreground mt-1'>
              NVMe: 8TB • Bulk HDD: 16TB • WS: 4TB
            </div>
          </Card>
          <Card className='p-4 bg-card'>
            <div className='text-xs font-semibold text-muted-foreground uppercase'>
              Chế độ Vận hành
            </div>
            <div className='text-2xl font-bold mt-1 text-emerald-600'>On-Premise Lab</div>
            <div className='text-xs text-muted-foreground mt-1'>
              Hạ tầng tự chủ, không tính phí cloud API
            </div>
          </Card>
        </div>

        <Card className='bg-card'>
          <CardHeader>
            <CardTitle className='text-base font-bold'>
              Định mức Tài nguyên theo Đề tài Nghiên cứu
            </CardTitle>
            <CardDescription className='text-xs'>
              Hạn mức phân bổ tài nguyên cho từng nhóm đề tài phòng thí nghiệm
            </CardDescription>
          </CardHeader>
          <CardContent className='pt-0'>
            <div className='divide-y text-xs'>
              <div className='py-3 flex items-center justify-between'>
                <div>
                  <div className='font-semibold'>Đề tài LLM Reasoning & Vision-Language Models</div>
                  <div className='text-muted-foreground'>Trưởng nhóm: PGS. Nguyễn Văn A</div>
                </div>
                <div className='flex items-center gap-2'>
                  <Badge variant='outline' className='font-mono'>
                    2x NVIDIA A30
                  </Badge>
                  <Badge variant='secondary'>2,000 GPU-hours</Badge>
                </div>
              </div>

              <div className='py-3 flex items-center justify-between'>
                <div>
                  <div className='font-semibold'>Đề tài Y tế Thông minh & Xử lý Ảnh Siêu âm</div>
                  <div className='text-muted-foreground'>Trưởng nhóm: TS. Trần Thị B</div>
                </div>
                <div className='flex items-center gap-2'>
                  <Badge variant='outline' className='font-mono'>
                    1x NVIDIA A30
                  </Badge>
                  <Badge variant='secondary'>1,500 GPU-hours</Badge>
                </div>
              </div>

              <div className='py-3 flex items-center justify-between'>
                <div>
                  <div className='font-semibold'>Đề tài Speech Recognition & Audio AI</div>
                  <div className='text-muted-foreground'>Trưởng nhóm: ThS. Lê C</div>
                </div>
                <div className='flex items-center gap-2'>
                  <Badge variant='outline' className='font-mono'>
                    1x NVIDIA A30
                  </Badge>
                  <Badge variant='secondary'>1,500 GPU-hours</Badge>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
