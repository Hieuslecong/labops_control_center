import { requireResearcherOrAdmin } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { ResearcherHandoverAccess } from '@/features/mram/components/researcher-handover-access';

export const metadata = {
  title: 'Bàn giao Quyền SSH - LabOps Control Center'
};

export default async function ResearcherAccessHandoverPage() {
  await requireResearcherOrAdmin();
  return (
    <PageContainer
      pageTitle='Trung tâm Bàn giao Quyền Truy cập SSH & VPN'
      pageDescription='Nhận mật khẩu SSH tạm thời một lần (Step-Up), tải WireGuard + SSH ZIP và cấu hình VS Code Remote-SSH'
    >
      <ResearcherHandoverAccess />
    </PageContainer>
  );
}
