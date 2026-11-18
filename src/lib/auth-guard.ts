import { auth } from '@clerk/nextjs/server';
import { redirect } from 'next/navigation';
import { cookies } from 'next/headers';
import { verifySessionCookieToken } from '@/lib/session-token';

/**
 * Bảo vệ trang nhạy cảm và phân quyền Role-Based Access Control (RBAC).
 * Hỗ trợ 2 chế độ xác thực hợp nhất:
 * 1. Session nội bộ LabOps (xác thực trực tiếp bằng cookie labops_session ký HMAC)
 * 2. Phiên Clerk SSO (nếu có cấu hình khóa Clerk)
 */
export function isDemoModeAllowed(): boolean {
  return process.env.NEXT_PUBLIC_DEMO_MODE === 'true' && process.env.NODE_ENV !== 'production';
}

export function hasRealClerkKeys(): boolean {
  const pk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  const sk = process.env.CLERK_SECRET_KEY;
  return Boolean(pk && sk && !pk.includes('mock') && !sk.includes('mock'));
}

export interface LocalSession {
  username: string;
  role: 'ADMIN' | 'RESEARCHER';
  memberId?: string | null;
  rawToken?: string;
}

export async function getLocalSession(): Promise<LocalSession | null> {
  try {
    const cookieStore = await cookies();
    const token = cookieStore.get('labops_session')?.value;
    const secret = process.env.LABOPS_SESSION_SECRET || process.env.LABOPS_AUTH_SECRET || '';
    const verified = verifySessionCookieToken(token, secret);
    if (!verified) return null;

    return {
      username: verified.username,
      role: verified.role,
      memberId: verified.memberId,
      rawToken: verified.rawToken
    };
  } catch {
    return null;
  }
}

export async function getUserRole(): Promise<'ADMIN' | 'RESEARCHER' | null> {
  // 1. Ưu tiên kiểm tra phiên đăng nhập quản trị nội bộ LabOps
  const localSession = await getLocalSession();
  if (localSession) {
    return localSession.role;
  }

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

    // Default-deny: Chỉ cấp quyền ADMIN khi được xác thực rõ ràng qua org:admin hoặc metadata role ADMIN
    if (metadataRole === 'ADMIN') return 'ADMIN';
    if (orgId && typeof has === 'function' && has({ role: 'org:admin' })) {
      return 'ADMIN';
    }
    // Mọi người dùng hợp lệ khác mặc định chỉ có quyền RESEARCHER
    return 'RESEARCHER';
  } catch {
    return null;
  }
}

export async function protectRoute(): Promise<void> {
  // 1. Đã đăng nhập bằng tài khoản LabOps (admin) -> Cho phép truy cập
  const localSession = await getLocalSession();
  if (localSession) {
    return;
  }

  if (isDemoModeAllowed() && !hasRealClerkKeys()) return;
  if (!hasRealClerkKeys()) {
    redirect('/auth/sign-in');
  }
  await auth.protect();
}

export async function requireAdmin(): Promise<void> {
  await protectRoute();
  const role = await getUserRole();
  // Fail-closed: Bất kỳ vai trò nào không phải ADMIN đều bị chuyển hướng về Researcher Portal
  if (role !== 'ADMIN') {
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
