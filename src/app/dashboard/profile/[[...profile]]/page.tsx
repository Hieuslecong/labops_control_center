import { protectRoute } from '@/lib/auth-guard';
import ProfileViewPage from '@/features/profile/components/profile-view-page';

export const metadata = {
  title: 'Dashboard : Profile'
};

export default async function Page() {
  await protectRoute();

  return <ProfileViewPage />;
}
