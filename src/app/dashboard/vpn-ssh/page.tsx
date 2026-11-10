import PageContainer from '@/components/layout/page-container';
import { protectRoute } from '@/lib/auth-guard';
import { MemberManagementView } from '@/features/users/components/member-management-view';

export const metadata = {
  title: 'Truy cập VPN và SSH - LabOps Control Center',
  description: 'Quản lý truy cập từ xa qua WireGuard VPN và OpenSSH với khóa công khai riêng cho từng thành viên'
};

export default async function VpnSshPage() {
  await protectRoute();
  return (
    <PageContainer
      pageTitle='Quản lý Truy cập Từ xa (WireGuard VPN & OpenSSH)'
      pageDescription='Cấp khóa công khai OpenSSH và cổng SSH riêng cho từng thành viên vào Workspace cô lập (Bảo toàn nguyên vẹn Legacy SSH cổng 22)'
    >
      <MemberManagementView />
    </PageContainer>
  );
}
