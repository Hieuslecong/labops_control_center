import { redirect } from 'next/navigation';
import { requireResearcherOrAdmin } from '@/lib/auth-guard';

export const metadata = {
  title: 'Kết nối & Bảo mật - Cổng nghiên cứu viên',
  description: 'Chuyển hướng tới Trung tâm Bàn giao Quyền Truy cập SSH & VPN'
};

export default async function ResearcherConnectPage() {
  await requireResearcherOrAdmin();
  redirect('/dashboard/researcher/access');
}
