import { clerkMiddleware } from '@clerk/nextjs/server';
import { NextResponse, type NextRequest, type NextFetchEvent } from 'next/server';
import { verifySessionCookieToken } from '@/lib/session-token';

const isDemoMode =
  process.env.NEXT_PUBLIC_DEMO_MODE === 'true' && process.env.NODE_ENV !== 'production';
const hasRealClerkKeys = Boolean(
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY &&
  !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY.includes('mock') &&
  process.env.CLERK_SECRET_KEY &&
  !process.env.CLERK_SECRET_KEY.includes('mock')
);

type MiddlewareHandler = (
  request: NextRequest,
  event: NextFetchEvent
) => NextResponse | Promise<NextResponse>;

const handler: MiddlewareHandler | null = hasRealClerkKeys
  ? (clerkMiddleware() as unknown as MiddlewareHandler)
  : null;

const PROTECTED = /^\/(dashboard|api)(\/|$)/;

const ADMIN_DASHBOARD_PREFIXES = [
  '/dashboard/overview',
  '/dashboard/servers',
  '/dashboard/users',
  '/dashboard/vpn-ssh',
  '/dashboard/resources',
  '/dashboard/jobs',
  '/dashboard/monitoring',
  '/dashboard/security',
  '/dashboard/ai-assistant',
  '/dashboard/audit-logs',
  '/dashboard/calendar',
  '/dashboard/usage',
  '/dashboard/settings'
];

export default function middleware(request: NextRequest, event: NextFetchEvent) {
  const pathname = request.nextUrl.pathname;

  // Cho phép gọi các endpoint đăng nhập/đăng xuất cục bộ
  if (pathname.startsWith('/api/auth/')) {
    return NextResponse.next();
  }

  const secret = process.env.LABOPS_SESSION_SECRET || process.env.LABOPS_AUTH_SECRET || '';
  const verifiedSession = verifySessionCookieToken(
    request.cookies.get('labops_session')?.value,
    secret
  );

  if (verifiedSession) {
    if (
      verifiedSession.role === 'RESEARCHER' &&
      ADMIN_DASHBOARD_PREFIXES.some(
        (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
      )
    ) {
      return NextResponse.redirect(new URL('/dashboard/researcher', request.url));
    }
    return NextResponse.next();
  }

  if (handler) return handler(request, event);
  if (isDemoMode) return NextResponse.next();
  // Fail-closed: chưa có phiên đăng nhập hợp lệ thì chuyển hướng hoặc chặn API
  if (PROTECTED.test(pathname)) {
    if (pathname.startsWith('/dashboard')) {
      const signInUrl = new URL('/auth/sign-in', request.url);
      return NextResponse.redirect(signInUrl);
    }
    return NextResponse.json(
      { error: 'Unauthorized', detail: 'Chưa đăng nhập hoặc phiên làm việc đã hết hạn' },
      { status: 401 }
    );
  }
  return NextResponse.next();
}

export const config = {
  matcher: [
    // Skip Next.js internals and all static files, unless found in search params
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    // Always run for API routes
    '/(api|trpc)(.*)'
  ]
};
