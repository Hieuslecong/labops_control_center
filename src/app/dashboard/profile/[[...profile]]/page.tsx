import { protectRoute } from '@/lib/auth-guard';
import ProfileViewPage from '@/features/profile/components/profile-view-page';

export const metadata = {
  title: 'Hồ sơ phiên đăng nhập - LabOps Control Center'
};

export default async function Page() {
  await protectRoute();

  return <ProfileViewPage />;
}
