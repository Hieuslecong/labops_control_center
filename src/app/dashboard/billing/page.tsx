import { protectRoute } from '@/lib/auth-guard';
import BillingView from './billing-view';

export default async function Page() {
  await protectRoute();
  return <BillingView />;
}
