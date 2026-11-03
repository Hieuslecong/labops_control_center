import PageContainer from '@/components/layout/page-container';
import { AuditLogsView } from '@/features/audit/components/audit-logs-view';

export const metadata = {
  title: 'Nhật ký - LabOps Control Center',
  description: 'Lịch sử kiểm toán và nhật ký thao tác đặc quyền quản trị hệ thống'
};

export default function AuditLogsPage() {
  return (
    <PageContainer
      pageTitle='Nhật ký Thao tác & Kiểm toán Hệ thống'
      pageDescription='Ghi nhận toàn bộ thao tác đặc quyền, cấp phát tài nguyên, thay đổi hạn ngạch và quyết định an ninh trên hạ tầng LabOps'
    >
      <AuditLogsView />
    </PageContainer>
  );
}
