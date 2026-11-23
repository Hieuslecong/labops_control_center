import { requireAdmin } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { AdminSshRequestsQueue } from '@/features/mram/components/admin-ssh-requests-queue';

export const metadata = {
  title: 'Duyệt Cấp phát SSH - LabOps Control Center',
  description:
    'Hàng đợi xét duyệt yêu cầu cấp phát quyền truy cập máy chủ SSH, Storage Pool và GPU (SLA 24h)'
};

export default async function AdminSshRequestsPage() {
  await requireAdmin();
  return (
    <PageContainer
      pageTitle='Duyệt Yêu cầu Cấp phát SSH (MRAM v1.0)'
      pageDescription='Xem xét lý do nghiên cứu, chỉnh sửa cấu hình tài nguyên và kiểm tra trước cấp phát trong SLA 24 giờ'
    >
      <AdminSshRequestsQueue />
    </PageContainer>
  );
}
