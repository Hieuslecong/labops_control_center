import PageContainer from '@/components/layout/page-container';
import React from 'react';

export default function OverViewLayout({ children }: { children: React.ReactNode }) {
  return <PageContainer>{children}</PageContainer>;
}
