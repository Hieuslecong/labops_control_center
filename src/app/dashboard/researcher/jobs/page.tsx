import PageContainer from '@/components/layout/page-container';
import { requireResearcherOrAdmin } from '@/lib/auth-guard';
import { ResearcherPortalView } from '@/features/researcher/components/researcher-portal-view';

export const metadata = {
  title: 'GPU & Tác vụ của tôi - Cổng nghiên cứu viên',
  description: 'Lượt giữ chỗ GPU độc quyền, tính toán GPU-hours và giám sát tiến trình đang chạy'
};

export default async function ResearcherJobsPage() {
  await requireResearcherOrAdmin();
  return (
    <PageContainer
      pageTitle='GPU & Tác vụ cá nhân'
      pageDescription='Quản lý phiên sử dụng GPU độc quyền theo UUID thực tế, kế toán GPU-hours và tiến trình tính toán'
    >
      <ResearcherPortalView initialTab='jobs' />
    </PageContainer>
  );
}
