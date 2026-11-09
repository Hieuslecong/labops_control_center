import PageContainer from '@/components/layout/page-container';
import { CalendarManagementView } from '@/features/calendar/components/calendar-management-view';

export const metadata = {
  title: 'Lịch Lab - LabOps Control Center',
  description: 'Quản lý lịch seminar, bảo trì máy chủ và các sự kiện khoa học'
};

export default function CalendarPage() {
  return (
    <PageContainer
      pageTitle='Lịch Hoạt động & Kế hoạch Lab'
      pageDescription='Quản trị lịch trình bảo trì máy chủ GPU, seminar nghiên cứu khoa học và báo cáo tiến độ định kỳ'
    >
      <CalendarManagementView />
    </PageContainer>
  );
}
