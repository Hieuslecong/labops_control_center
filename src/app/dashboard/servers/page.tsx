import { requireAdmin } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { ServerDetailView } from '@/features/servers/components/server-detail-view';

export const metadata = {
  title: 'Hạ tầng & GPU - LabOps Control Center',
  description: 'Giám sát chi tiết phần cứng, container và GPU của Server Rack và Workstation'
};

export default async function ServersPage() {
  await requireAdmin();
  return (
    <PageContainer
      pageTitle='Quản trị Hạ tầng & GPU'
      pageDescription='Giám sát trạng thái cụm máy chủ và cụm GPU NVIDIA A30 phân tán (Server Rack Hà Nội & Workstation)'
    >
      <ServerDetailView />
    </PageContainer>
  );
}
