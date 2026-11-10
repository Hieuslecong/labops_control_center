'use client';

import React from 'react';
import { ClerkProvider } from '@clerk/nextjs';
import { ActiveThemeProvider } from '../themes/active-theme';
import QueryProvider from './query-provider';

export default function Providers({
  activeThemeValue,
  enableClerk,
  children
}: {
  activeThemeValue: string;
  enableClerk: boolean;
  children: React.ReactNode;
}) {
  return (
    <ActiveThemeProvider initialTheme={activeThemeValue}>
      <QueryProvider>{enableClerk ? <ClerkProvider>{children}</ClerkProvider> : children}</QueryProvider>
    </ActiveThemeProvider>
  );
}
