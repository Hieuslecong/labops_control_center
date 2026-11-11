import PageContainer from '@/components/layout/page-container';
import { requireResearcherOrAdmin } from '@/lib/auth-guard';
import { ResearcherPortalView } from '@/features/researcher/components/researcher-portal-view';

export const metadata = {
  title: 'Kết nối & Bảo mật - Cổng nghiên cứu viên',
  description: 'Quản lý khóa SSH, cấu hình WireGuard VPN và luồng kết nối an toàn 5 bước vào workspace'
};

export default async function ResearcherConnectPage() {
  await requireResearcherOrAdmin();
  return (
    <PageContainer
      pageTitle='Kết nối & Bảo mật cá nhân'
      pageDescription='Thiết lập WireGuard VPN nội bộ, đăng ký khóa công khai OpenSSH và lấy lệnh kết nối vào workspace riêng'
    >
      <ResearcherPortalView initialTab='connect' />
    </PageContainer>
  );
}
