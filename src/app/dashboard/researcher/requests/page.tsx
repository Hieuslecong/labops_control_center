import { requireResearcherOrAdmin } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { ResearcherRequestsList } from '@/features/mram/components/researcher-requests-list';

export const metadata = {
  title: 'Yêu cầu SSH của tôi - LabOps Control Center'
};

export default async function ResearcherRequestsPage() {
  await requireResearcherOrAdmin();
  return (
    <PageContainer
      pageTitle='Lịch sử & Trạng thái Yêu cầu SSH'
      pageDescription='Theo dõi hạn duyệt 24 giờ và gửi lại đơn khi quá hạn hoặc cần chỉnh sửa'
    >
      <ResearcherRequestsList />
    </PageContainer>
  );
}
