import PageContainer from '@/components/layout/page-container';
import { requireResearcherOrAdmin } from '@/lib/auth-guard';
import { ResearcherPortalView } from '@/features/researcher/components/researcher-portal-view';

export const metadata = {
  title: 'Cổng nghiên cứu viên - LabOps Control Center',
  description:
    'Không gian làm việc cá nhân của nghiên cứu viên: kết nối WireGuard + OpenSSH, môi trường cô lập, lịch giữ GPU và kết quả quét mã nguồn'
};

export default async function ResearcherPage() {
  await requireResearcherOrAdmin();
  return (
    <PageContainer
      pageTitle='Cổng Nghiên cứu viên (Researcher Portal)'
      pageDescription='Quản lý khóa công khai OpenSSH cá nhân, thông tin kết nối workspace qua WireGuard VPN, lượt giữ chỗ GPU độc quyền và lịch sử sử dụng của riêng bạn'
    >
      <ResearcherPortalView initialTab='overview' />
    </PageContainer>
  );
}

