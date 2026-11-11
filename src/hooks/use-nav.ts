'use client';

/**
 * Client-side hook for filtering navigation items based on verified server RBAC
 */

import { useMemo } from 'react';
import type { NavItem, NavGroup } from '@/types';
import { adminNavGroups, researcherNavGroups } from '@/config/nav-config';
import { useAuthSession } from './use-auth-session';

/**
 * Hook to filter navigation groups based on verified server RBAC role
 */
export function useFilteredNavGroups(_groups?: NavGroup[]): NavGroup[] {
  const { role, isLoading } = useAuthSession();

  return useMemo(() => {
    // While loading role, default to safe empty state to avoid leaking or flashing admin items
    if (isLoading && !role) {
      return [];
    }

    if (role === 'RESEARCHER') {
      return researcherNavGroups;
    }

    // Default to adminNavGroups for ADMIN
    return adminNavGroups;
  }, [role, isLoading]);
}

/**
 * Hook to filter navigation items based on verified server RBAC role
 */
export function useFilteredNavItems(items: NavItem[]): NavItem[] {
  const { role, isLoading } = useAuthSession();

  return useMemo(() => {
    if (isLoading && !role) return [];
    return items.filter((item) => {
      if (!item.roles || item.roles.length === 0) return true;
      return role ? item.roles.includes(role) : false;
    });
  }, [items, role, isLoading]);
}
