import PageContainer from '@/components/layout/page-container';
import { MemberManagementView } from '@/features/users/components/member-management-view';

export const metadata = {
  title: 'Thành viên - LabOps Control Center',
  description: 'Quản trị danh sách thành viên nghiên cứu, cấp phát container Incus và khóa SSH'
};

export default function UsersPage() {
  return (
    <PageContainer
      pageTitle='Quản trị Thành viên & Quyền truy cập'
      pageDescription='Quản lý tài khoản nghiên cứu viên, cấp phát Workspace Linux container và phân quyền sử dụng GPU NVIDIA A30'
    >
      <MemberManagementView />
    </PageContainer>
  );
}
