import { requireResearcherOrAdmin } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { ResearcherSshRequestForm } from '@/features/mram/components/researcher-ssh-request-form';

export const metadata = {
  title: 'Đăng ký Quyền SSH - LabOps Control Center'
};

export default async function ResearcherSshRequestPage() {
  await requireResearcherOrAdmin();
  return (
    <PageContainer
      pageTitle='Xin Cấp Quyền Truy Cập Máy chủ (SSH)'
      pageDescription='Điền đầy đủ lý do nghiên cứu, giảng viên hướng dẫn (nếu là sinh viên) và xác nhận chính sách an toàn LabOps'
    >
      <ResearcherSshRequestForm />
    </PageContainer>
  );
}
