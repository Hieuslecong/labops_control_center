import PageContainer from '@/components/layout/page-container';
import { requireResearcherOrAdmin } from '@/lib/auth-guard';
import { ResearcherPortalView } from '@/features/researcher/components/researcher-portal-view';

export const metadata = {
  title: 'Workspace của tôi - Cổng nghiên cứu viên',
  description: 'Môi trường container cô lập, hạn mức tài nguyên CPU/RAM và dung lượng lưu trữ cá nhân'
};

export default async function ResearcherWorkspacePage() {
  await requireResearcherOrAdmin();
  return (
    <PageContainer
      pageTitle='Môi trường làm việc cá nhân (Workspace)'
      pageDescription='Kiểm tra thông số container, giới hạn vCPU/RAM/PIDs và đường dẫn dữ liệu cô lập trên máy chủ'
    >
      <ResearcherPortalView initialTab='workspace' />
    </PageContainer>
  );
}
