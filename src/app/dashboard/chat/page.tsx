import { protectRoute } from '@/lib/auth-guard';
import ChatViewPage from '@/features/chat/components/chat-view-page';

export const metadata = {
  title: 'Dashboard: Chat'
};

export default async function Page() {
  await protectRoute();

  return <ChatViewPage />;
}
