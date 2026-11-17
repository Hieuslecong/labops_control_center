'use client';

import React, { createContext, useContext } from 'react';
import { useUser } from '@clerk/nextjs';

export type ClerkUserType = ReturnType<typeof useUser>['user'];

export interface ClerkAuthContextValue {
  user: ClerkUserType | null;
  isLoaded: boolean;
  isSignedIn: boolean;
  isClerkEnabled: boolean;
}

const defaultContextValue: ClerkAuthContextValue = {
  user: null,
  isLoaded: true,
  isSignedIn: false,
  isClerkEnabled: false
};

const ClerkAuthContext = createContext<ClerkAuthContextValue>(defaultContextValue);

export function useClerkSafe(): ClerkAuthContextValue {
  return useContext(ClerkAuthContext);
}

function ClerkBridge({ children }: { children: React.ReactNode }) {
  const { user, isLoaded, isSignedIn } = useUser();
  return (
    <ClerkAuthContext.Provider
      value={{
        user,
        isLoaded,
        isSignedIn: Boolean(isSignedIn),
        isClerkEnabled: true
      }}
    >
      {children}
    </ClerkAuthContext.Provider>
  );
}

export function ClerkAuthProvider({
  enableClerk,
  children
}: {
  enableClerk: boolean;
  children: React.ReactNode;
}) {
  if (enableClerk) {
    return <ClerkBridge>{children}</ClerkBridge>;
  }
  return (
    <ClerkAuthContext.Provider value={defaultContextValue}>{children}</ClerkAuthContext.Provider>
  );
}
