import { protectRoute } from '@/lib/auth-guard';
import KanbanViewPage from '@/features/kanban/components/kanban-view-page';

export const metadata = {
  title: 'Dashboard : Kanban view'
};

export default async function page() {
  await protectRoute();

  return <KanbanViewPage />;
}
