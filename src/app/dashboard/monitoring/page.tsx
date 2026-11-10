import PageContainer from '@/components/layout/page-container';
import { protectRoute } from '@/lib/auth-guard';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

export const metadata = {
  title: 'Giám sát và cảnh báo - LabOps Control Center',
  description: 'Giám sát hạ tầng tập trung qua Beszel, NVML và psutil kèm nhãn nguồn dữ liệu và độ tươi'
};

export default async function MonitoringPage() {
  await protectRoute();
  return (
    <PageContainer
      pageTitle='Giám sát Hạ tầng và Cảnh báo (Beszel & NVML)'
      pageDescription='Theo dõi số liệu CPU, RAM, đĩa, mạng và GPU theo thời gian thực với thông tin nguồn thu thập (source), thời điểm cập nhật (collected_at) và độ tươi (ONLINE / STALE / OFFLINE / UNKNOWN)'
    >
      <div className='grid gap-4 md:grid-cols-2'>
        <Card>
          <CardHeader>
            <div className='flex items-center justify-between'>
              <CardTitle className='text-sm font-bold'>Trạng thái Tích hợp Beszel Hub</CardTitle>
              <Badge variant='outline' className='font-mono text-[10px] text-amber-600'>
                NOT_CONFIGURED / LOCAL_FALLBACK
              </Badge>
            </div>
            <CardDescription className='text-xs'>
              Phiên bản mục tiêu: Beszel v0.9.1 • Giao thức REST API chính thức
            </CardDescription>
          </CardHeader>
          <CardContent className='text-xs space-y-2 text-muted-foreground'>
            <p>
              Khi biến môi trường <code>BESZEL_HUB_URL</code> chưa được cấu hình, hệ thống không bao
              giờ hiển thị biểu đồ lịch sử giả hay báo trạng thái xanh giả. Các chỉ số tức thời trên
              node cục bộ được đọc trực tiếp từ <code>psutil</code> và <code>nvidia-smi</code>.
            </p>
            <p>
              Endpoint kiểm tra trực tiếp: <code>/api/labops/v2/monitoring/beszel</code>.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className='flex items-center justify-between'>
              <CardTitle className='text-sm font-bold'>Trạng thái Tích hợp Arcane Docker</CardTitle>
              <Badge variant='outline' className='font-mono text-[10px] text-blue-600'>
                ADMIN ONLY • v1.1.0
              </Badge>
            </div>
            <CardDescription className='text-xs'>
              Quản trị container, image, volume và network dành riêng cho Quản trị viên
            </CardDescription>
          </CardHeader>
          <CardContent className='text-xs space-y-2 text-muted-foreground'>
            <p>
              Nghiên cứu viên không bao giờ được cấp quyền truy cập Arcane hoặc Docker socket. Mọi
              thao tác tạo workspace của nghiên cứu viên đều phải đi qua{' '}
              <strong>LabOps Workspace Controller</strong> với kiểm tra chính sách bảo mật.
            </p>
            <p>
              Endpoint quản trị: <code>/api/labops/v2/containers/summary</code>.
            </p>
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}

