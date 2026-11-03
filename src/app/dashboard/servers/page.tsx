import PageContainer from '@/components/layout/page-container';
import { ServerDetailView } from '@/features/servers/components/server-detail-view';

export const metadata = {
  title: 'Máy chủ - LabOps Control Center',
  description: 'Giám sát chi tiết phần cứng, container và GPU của Server Track và Workstation'
};

export default function ServersPage() {
  return (
    <PageContainer
      pageTitle='Quản trị Máy chủ Vật lý'
      pageDescription='Giám sát trạng thái 2 máy chủ phân tán địa lý (Server Track Hà Nội & Server Workstation TP.HCM)'
    >
      <ServerDetailView />
    </PageContainer>
  );
}
