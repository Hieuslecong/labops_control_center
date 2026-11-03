import { protectRoute } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import AdvancedFormPatterns from '@/features/forms/components/advanced-form-patterns';

export const metadata = {
  title: 'Dashboard: Advanced Form Patterns'
};

export default async function Page() {
  await protectRoute();

  return (
    <PageContainer
      pageTitle='Advanced Form Patterns'
      pageDescription='Linked fields, async validation, dynamic rows, nested objects, cross-field validation, and form-level errors.'
    >
      <AdvancedFormPatterns />
    </PageContainer>
  );
}
