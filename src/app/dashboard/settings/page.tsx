import PageContainer from '@/components/layout/page-container';
import { SettingsView } from '@/features/settings/components/settings-view';

export const metadata = {
  title: 'Cài đặt - LabOps Control Center',
  description: 'Cấu hình kết nối Incus daemon, Netdata, NVIDIA NVML và chế độ vận hành'
};

export default function SettingsPage() {
  return (
    <PageContainer
      pageTitle='Cài đặt & Tích hợp Hạ tầng'
      pageDescription='Quản trị kết nối API Incus, cấu hình tác tử giám sát Netdata, tần suất NVML và chuyển đổi chế độ Demo / Production'
    >
      <SettingsView />
    </PageContainer>
  );
}
