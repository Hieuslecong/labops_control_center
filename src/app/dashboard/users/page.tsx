import { requireAdmin } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { MemberManagementView } from '@/features/users/components/member-management-view';

export const metadata = {
  title: 'Thành viên & Truy cập - LabOps Control Center',
  description: 'Quản trị danh sách thành viên nghiên cứu, tài khoản WireGuard VPN, container Incus/Docker và khóa SSH'
};

export default async function UsersPage() {
  await requireAdmin();
  return (
    <PageContainer
      pageTitle='Quản trị Thành viên & Quyền truy cập'
      pageDescription='Quản lý tài khoản nghiên cứu viên, cấp phát Workspace Linux container, quản lý WireGuard VPN và khóa SSH'
    >
      <MemberManagementView />
    </PageContainer>
  );
}
