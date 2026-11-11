import { requireAdmin } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { ResourcesManagementView } from '@/features/resources/components/resources-management-view';

export const metadata = {
  title: 'Workspace & Lưu trữ - LabOps Control Center',
  description: 'Quản trị vòng đời container Docker/Incus, hạn mức vCPU/RAM và hạn ngạch dung lượng đĩa'
};

export default async function ResourcesPage() {
  await requireAdmin();
  return (
    <PageContainer
      pageTitle='Quản trị Workspace & Lưu trữ'
      pageDescription='Quản trị vòng đời container Docker, phân bổ tài nguyên vCPU/RAM, giới hạn PIDs và hạn ngạch lưu trữ dữ liệu'
    >
      <ResourcesManagementView />
    </PageContainer>
  );
}
