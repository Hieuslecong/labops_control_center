import { requireAdmin } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import { AdminSshRequestReview } from '@/features/mram/components/admin-ssh-request-review';

export const metadata = {
  title: 'Chi tiết Phê duyệt SSH - LabOps Control Center'
};

interface PageProps {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ invite?: string }>;
}

export default async function AdminSshRequestReviewPage({ params, searchParams }: PageProps) {
  await requireAdmin();
  const { id } = await params;
  const { invite } = await searchParams;
  return (
    <PageContainer
      pageTitle={`Xem xét Yêu cầu SSH (${id})`}
      pageDescription='Điều chỉnh máy chủ, Storage Pool, CPU/RAM/GPU và xác thực Step-Up để phê duyệt cấp phát'
    >
      <AdminSshRequestReview requestId={id} inviteToken={invite} />
    </PageContainer>
  );
}
