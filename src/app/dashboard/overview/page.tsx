import { protectRoute } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { OverviewContent } from '@/features/overview/components/overview-content';

export const metadata = {
  title: 'Tổng quan - LabOps Control Center',
  description: 'Bảng điều khiển giám sát hạ tầng máy chủ và 4 GPU NVIDIA A30'
};

export default async function OverviewPage() {
  await protectRoute();
  return (
    <PageContainer>
      <OverviewContent />
    </PageContainer>
  );
}
