import { requireAdmin } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { SecurityManagementView } from '@/features/security/components/security-management-view';

export const metadata = {
  title: 'Bảo mật - LabOps Control Center',
  description: 'Kiểm duyệt an toàn mã nguồn, quy trình sandbox cách ly và giám sát sao lưu dữ liệu'
};

export default async function SecurityPage() {
  await requireAdmin();
  return (
    <PageContainer
      pageTitle='Quản trị An ninh & Kiểm duyệt Mã nguồn'
      pageDescription='Kiểm soát an toàn mã nguồn GitHub, cách ly sandbox tự động, phát hiện rủi ro và giám sát trạng thái sao lưu ZFS'
    >
      <SecurityManagementView />
    </PageContainer>
  );
}
