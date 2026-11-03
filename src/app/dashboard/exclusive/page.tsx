import { protectRoute } from '@/lib/auth-guard';
import ExclusiveView from './exclusive-view';

export default async function Page() {
  await protectRoute();
  return <ExclusiveView />;
}
