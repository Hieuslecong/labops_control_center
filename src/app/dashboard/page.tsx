import { redirect } from 'next/navigation';
import { getUserRole } from '@/lib/auth-guard';

export default async function Dashboard() {
  const role = await getUserRole();
  if (role === 'RESEARCHER') {
    redirect('/dashboard/researcher');
  }
  redirect('/dashboard/overview');
}

