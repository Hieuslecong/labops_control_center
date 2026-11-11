import { redirect } from 'next/navigation';
import { requireAdmin } from '@/lib/auth-guard';

export const metadata = {
  title: 'Thành viên & Truy cập - LabOps Control Center',
  description: 'Chuyển hướng hợp nhất tới Trang Quản trị Thành viên & Quyền truy cập VPN/SSH'
};

export default async function VpnSshPage() {
  await requireAdmin();
  redirect('/dashboard/users');
}
