import { auth } from '@clerk/nextjs/server';
import { redirect } from 'next/navigation';

/**
 * Bảo vệ trang nhạy cảm và phân quyền Role-Based Access Control (RBAC).
 * - Chế độ demo chỉ khi NEXT_PUBLIC_DEMO_MODE=true VÀ không phải production build.
 * - Thiếu khóa Clerk ngoài demo -> ném lỗi cấu hình (fail-closed).
 * - requireAdmin(): Chỉ cho phép ADMIN; RESEARCHER tự động chuyển hướng sang Cổng nghiên cứu viên.
 * - requireResearcherOrAdmin(): Cho phép cả ADMIN và RESEARCHER.
 */
export function isDemoModeAllowed(): boolean {
  return process.env.NEXT_PUBLIC_DEMO_MODE === 'true' && process.env.NODE_ENV !== 'production';
}

export function hasRealClerkKeys(): boolean {
  const pk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  const sk = process.env.CLERK_SECRET_KEY;
  return Boolean(pk && sk && !pk.includes('mock') && !sk.includes('mock'));
}

export async function getUserRole(): Promise<'ADMIN' | 'RESEARCHER' | null> {
  if (isDemoModeAllowed() && !hasRealClerkKeys()) {
    return 'ADMIN';
  }
  if (!hasRealClerkKeys()) return null;

  try {
    const session = await auth();
    if (!session?.userId) return null;

    const { orgId, has, sessionClaims } = session;
    const metadataRole = String(
      (sessionClaims?.metadata as { role?: string } | undefined)?.role ||
        (sessionClaims?.publicMetadata as { role?: string } | undefined)?.role ||
        ''
    ).toUpperCase();

    if (metadataRole === 'RESEARCHER') return 'RESEARCHER';
    if (orgId && typeof has === 'function' && !has({ role: 'org:admin' }) && metadataRole !== 'ADMIN') {
      return 'RESEARCHER';
    }
    return 'ADMIN';
  } catch {
    return null;
  }
}

export async function protectRoute(): Promise<void> {
  if (isDemoModeAllowed() && !hasRealClerkKeys()) return;
  if (!hasRealClerkKeys()) {
    throw new Error('Xác thực chưa được cấu hình: thiếu khóa Clerk. Truy cập bị từ chối.');
  }
  await auth.protect();
}

export async function requireAdmin(): Promise<void> {
  await protectRoute();
  const role = await getUserRole();
  if (role === 'RESEARCHER') {
    redirect('/dashboard/researcher');
  }
}

export async function requireResearcherOrAdmin(): Promise<void> {
  await protectRoute();
  const role = await getUserRole();
  if (!role) {
    redirect('/auth/sign-in');
  }
}
