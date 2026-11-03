import { protectRoute } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { AiChatDemo } from '@/features/ai-chat/components/ai-chat-demo';

export const metadata = {
  title: 'Dashboard: AI Chat'
};

export default async function Page() {
  await protectRoute();

  return (
    <PageContainer>
      <AiChatDemo />
    </PageContainer>
  );
}
