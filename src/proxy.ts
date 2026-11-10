import { clerkMiddleware } from '@clerk/nextjs/server';
import { NextResponse, type NextRequest, type NextFetchEvent } from 'next/server';

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

export default function middleware(request: NextRequest, event: NextFetchEvent) {
  if (handler) return handler(request, event);
  if (isDemoMode) return NextResponse.next();
  // Fail-closed: không có xác thực thì chặn các vùng nhạy cảm.
  if (PROTECTED.test(request.nextUrl.pathname)) {
    return NextResponse.json(
      { detail: 'Xác thực chưa được cấu hình. Truy cập bị từ chối.' },
      { status: 503 }
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
