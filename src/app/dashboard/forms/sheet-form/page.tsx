import { protectRoute } from '@/lib/auth-guard';
import PageContainer from '@/components/layout/page-container';
import SheetFormDemo from '@/features/forms/components/sheet-form-demo';

export const metadata = {
  title: 'Dashboard: Sheet Form'
};

export default async function Page() {
  await protectRoute();

  return (
    <PageContainer
      pageTitle='Sheet & Dialog Forms'
      pageDescription='Form patterns inside sheets and dialogs with external submit buttons.'
    >
      <SheetFormDemo />
    </PageContainer>
  );
}
