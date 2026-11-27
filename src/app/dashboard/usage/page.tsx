import PageContainer from '@/components/layout/page-container';
import { requireAdmin } from '@/lib/auth-guard';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { UsageAnalyticsView } from '@/features/overview/components/usage-analytics-view';

export const metadata = {
  title: 'Báo cáo sử dụng GPU - LabOps Control Center',
  description:
    'Kế toán chính xác thời gian giữ chỗ GPU, biểu đồ tải lịch sử và định mức tài nguyên theo thành viên'
};

export default async function UsageReportPage() {
  await requireAdmin();
  return (
    <PageContainer
      pageTitle='Báo cáo Sử dụng Tài nguyên & Kế toán GPU-Hours'
      pageDescription='Biểu đồ tiêu thụ GPU/CPU/VRAM thời gian thực, cơ cấu workload và định mức cấp phát theo thành viên'
    >
      <div className='space-y-5 w-full min-w-0'>
        <UsageAnalyticsView />

        <Card>
          <CardHeader>
            <div className='flex items-center justify-between'>
              <CardTitle className='text-sm font-bold'>
                Phương pháp Tính toán GPU-Hours Trung thực (V2)
              </CardTitle>
              <Badge variant='outline' className='font-mono text-xs text-blue-600'>
                FULL_GPU_EXCLUSIVE
              </Badge>
            </div>
            <CardDescription className='text-xs'>
              Mọi số liệu định lượng đều gắn với UUID thiết bị GPU thực và trạng thái xác minh tiến
              trình
            </CardDescription>
          </CardHeader>
          <CardContent className='text-xs space-y-2 text-muted-foreground'>
            <p>
              1. <strong>Thời gian giữ chỗ (Allocated Hours)</strong>: tính từ thời điểm{' '}
              <code>reserved_at</code> đến khi giải phóng <code>ended_at</code>.
            </p>
            <p>
              2. <strong>Thời gian chạy thực (Verified Runtime Hours)</strong>: chỉ được ghi nhận
              khi <code>runtime_verified = true</code> từ Node Agent và NVML.
            </p>
            <p>
              3. <strong>Thời gian giữ nhàn rỗi (Idle Hold Hours)</strong>: chênh lệch giữa thời
              gian giữ chỗ và thời gian thực thi nhằm phát hiện việc giữ GPU nhưng không chạy tải.
            </p>
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
