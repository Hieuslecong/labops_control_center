import { requireAdmin } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { JobsMonitoringView } from '@/features/jobs/components/jobs-monitoring-view';

export const metadata = {
  title: 'Công việc - LabOps Control Center',
  description: 'Giám sát tiến trình PyTorch, CUDA kernels và tác vụ tính toán đang chạy'
};

export default async function JobsPage() {
  await requireAdmin();
  return (
    <PageContainer
      pageTitle='Công việc Đang chạy & Giám sát Tiến trình'
      pageDescription='Theo dõi chi tiết các tiến trình AI, tiêu thụ CPU/RAM/VRAM và trạng thái kết nối SSH của thành viên'
    >
      <JobsMonitoringView />
    </PageContainer>
  );
}
