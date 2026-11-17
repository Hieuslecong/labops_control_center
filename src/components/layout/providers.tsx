'use client';

import React from 'react';
import { ClerkProvider } from '@clerk/nextjs';
import { ActiveThemeProvider } from '../themes/active-theme';
import QueryProvider from './query-provider';
import { ClerkAuthProvider } from './clerk-auth-context';

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
      <QueryProvider>
        {enableClerk ? (
          <ClerkProvider>
            <ClerkAuthProvider enableClerk={true}>{children}</ClerkAuthProvider>
          </ClerkProvider>
        ) : (
          <ClerkAuthProvider enableClerk={false}>{children}</ClerkAuthProvider>
        )}
      </QueryProvider>
    </ActiveThemeProvider>
  );
}
