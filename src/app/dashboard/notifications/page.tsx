import { protectRoute } from '@/lib/auth-guard';
import NotificationsPage from '@/features/notifications/components/notifications-page';

export const metadata = {
  title: 'Dashboard: Notifications'
};

export default async function Page() {
  await protectRoute();

  return <NotificationsPage />;
}
