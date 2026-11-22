import PageContainer from '@/components/layout/page-container';
import { requireAdmin } from '@/lib/auth-guard';
import AISREControlView from '@/features/ai-assistant/components/ai-sre-control-view';

export const metadata = {
  title: 'Trợ lý AI & Zalo Assistant - LabOps Control Center',
  description:
    'Quản trị tập trung Zalo Bot, 9Router AI Gateway, Báo cáo tuần và Quota 3.000 tin/tháng trên Workstation Primary'
};

export default async function AIAssistantPage() {
  await requireAdmin();

  return (
    <PageContainer
      pageTitle='Trợ lý AI & Zalo Bot Control Plane'
      pageDescription='Hệ thống trợ lý vận hành AI đa phương thức và Zalo Bot với cơ chế kiểm soát hạn mức, báo cáo tuần tự động và bảo vệ hạ tầng Workstation Primary'
    >
      <AISREControlView />
    </PageContainer>
  );
}
