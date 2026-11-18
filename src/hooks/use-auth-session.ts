'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import { useClerkSafe } from '@/components/layout/clerk-auth-context';
import type { UserSessionData } from '@/app/api/labops/session/route';

let globalCachedSession: UserSessionData | null = null;
let globalPreviewRole: 'ADMIN' | 'RESEARCHER' | null = null;
const listeners = new Set<() => void>();

function notifyListeners() {
  listeners.forEach((listener) => listener());
}

export function useAuthSession() {
  const { user, isLoaded: clerkLoaded, isSignedIn } = useClerkSafe();
  const [session, setSession] = useState<UserSessionData | null>(globalCachedSession);
  const [loading, setLoading] = useState<boolean>(!globalCachedSession);
  const [previewRole, setLocalPreviewRole] = useState<'ADMIN' | 'RESEARCHER' | null>(
    globalPreviewRole
  );

  const fetchSession = useCallback(async () => {
    try {
      const res = await fetch('/api/labops/session').catch(() => null);
      if (res && res.ok) {
        const data = (await res.json()) as UserSessionData;
        globalCachedSession = data;
        setSession(data);
      } else if (res && (res.status === 401 || res.status === 403)) {
        globalCachedSession = null;
        setSession(null);
      }
    } catch {
      // Ignore network errors on session check
    } finally {
      setLoading(false);
      notifyListeners();
    }
  }, []);

  useEffect(() => {
    const handleChange = () => {
      setSession(globalCachedSession);
      setLocalPreviewRole(globalPreviewRole);
    };
    listeners.add(handleChange);
    return () => {
      listeners.delete(handleChange);
    };
  }, []);

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    if (!globalCachedSession) {
      void fetchSession();
    }
  }, [fetchSession]);
  /* oxlint-enable react/set-state-in-effect */

  const setPreviewRole = useCallback((role: 'ADMIN' | 'RESEARCHER' | null) => {
    globalPreviewRole = role;
    setLocalPreviewRole(role);
    notifyListeners();
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch {
      // Ignore network errors during sign-out cleanup
    } finally {
      globalCachedSession = null;
      globalPreviewRole = null;
      setSession(null);
      setLocalPreviewRole(null);
      notifyListeners();
      window.location.assign('/auth/sign-in');
    }
  }, []);

  // Determine effective role (fail-closed: never default to ADMIN before verification)
  const rawRole: 'ADMIN' | 'RESEARCHER' = useMemo(() => {
    if (session?.role === 'ADMIN' || session?.role === 'RESEARCHER') {
      return session.role;
    }
    if (!session && isSignedIn) {
      const metadataRole = String(
        (user?.publicMetadata as { role?: string })?.role || ''
      ).toUpperCase();
      if (metadataRole === 'ADMIN') return 'ADMIN';
    }
    return 'RESEARCHER';
  }, [session, user, isSignedIn]);

  const effectiveRole: 'ADMIN' | 'RESEARCHER' = useMemo(() => {
    if (previewRole && rawRole === 'ADMIN') return previewRole;
    return rawRole;
  }, [previewRole, rawRole]);

  const displayUser = useMemo(() => {
    if (session && session.authenticated) {
      return {
        fullName: session.fullName || user?.fullName || `@${session.username || 'user'}`,
        imageUrl: session.imageUrl || user?.imageUrl || '',
        emailAddresses: session.email
          ? [{ emailAddress: session.email }]
          : user?.emailAddresses || [{ emailAddress: '' }],
        username: session.username || user?.username || ''
      };
    }
    if (user && isSignedIn) {
      return {
        fullName:
          user.fullName ||
          `${user.firstName || ''} ${user.lastName || ''}`.trim() ||
          'Thành viên LabOps',
        imageUrl: user.imageUrl || '',
        emailAddresses: user.primaryEmailAddress
          ? [{ emailAddress: user.primaryEmailAddress.emailAddress }]
          : [{ emailAddress: '' }],
        username: user.username || ''
      };
    }
    return {
      fullName: loading ? 'Đang tải phiên...' : 'Người dùng LabOps',
      imageUrl: '',
      emailAddresses: [{ emailAddress: '' }],
      username: ''
    };
  }, [session, user, isSignedIn, loading]);

  return {
    session,
    user: displayUser,
    role: effectiveRole,
    actualRole: rawRole,
    isAdmin: effectiveRole === 'ADMIN',
    isResearcher: effectiveRole === 'RESEARCHER',
    isPreviewMode: previewRole !== null && rawRole === 'ADMIN',
    previewRole,
    setPreviewRole,
    logout,
    isLoading: loading && !clerkLoaded,
    refetchSession: fetchSession
  };
}
