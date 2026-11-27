import { redirect } from 'next/navigation';

interface PageProps {
  params: Promise<{ id: string }>;
}

export default async function AdminSshRequestIndexRedirectPage({ params }: PageProps) {
  const { id } = await params;
  redirect(`/dashboard/ssh-requests/${encodeURIComponent(id)}/review`);
}
