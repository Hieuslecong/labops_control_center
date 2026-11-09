import { redirect } from 'next/navigation';

export default function Page() {
  // Always direct users to the LabOps overview dashboard
  redirect('/dashboard/overview');
}
