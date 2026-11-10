import { protectRoute } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { ResourcesManagementView } from '@/features/resources/components/resources-management-view';

export const metadata = {
  title: 'Tài nguyên - LabOps Control Center',
  description: 'Quản trị hạn ngạch tính toán và cấp phát 4 GPU NVIDIA A30'
};

export default async function ResourcesPage() {
  await protectRoute();
  return (
    <PageContainer
      pageTitle='Quản trị Tài nguyên & Cấp phát GPU'
      pageDescription='Giám sát độc lập 4 GPU NVIDIA A30 (96GB VRAM), thiết lập chế độ Shared/Exclusive và kiểm soát hạn ngạch VRAM'
    >
      <ResourcesManagementView />
    </PageContainer>
  );
}
