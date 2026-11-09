import { protectRoute } from '@/lib/auth-guard';
import { redirect } from 'next/navigation';

export default async function Page() {
  await protectRoute();

  redirect('/dashboard/forms/basic');
}
