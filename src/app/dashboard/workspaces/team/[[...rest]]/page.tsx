import { protectRoute } from '@/lib/auth-guard';
import TeamView from './team-view';

export default async function Page() {
  await protectRoute();
  return <TeamView />;
}
