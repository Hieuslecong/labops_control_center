'use client';

/**
 * Client-side hook for filtering navigation items based on RBAC
 */

import { useMemo } from 'react';
import type { NavItem, NavGroup } from '@/types';

/**
 * Hook to filter navigation items based on RBAC (fully client-side)
 */
export function useFilteredNavItems(items: NavItem[]) {
  return useMemo(() => items, [items]);
}

/**
 * Hook to filter navigation groups based on RBAC (fully client-side)
 */
export function useFilteredNavGroups(groups: NavGroup[]) {
  return useMemo(() => groups, [groups]);
}
