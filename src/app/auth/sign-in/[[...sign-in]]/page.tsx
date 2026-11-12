import { Metadata } from 'next';
import SignInViewPage from '@/features/auth/components/sign-in-view';

export const metadata: Metadata = {
  title: 'Đăng nhập | LabOps',
  description: 'Đăng nhập LabOps Control Center.'
};

export default async function Page({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  return <SignInViewPage error={error} />;
}
