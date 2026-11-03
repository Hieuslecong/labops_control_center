import { clerkMiddleware } from '@clerk/nextjs/server';
import { NextResponse, type NextRequest, type NextFetchEvent } from 'next/server';

const isDemoMode = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';
const hasRealClerkKeys = Boolean(
  !isDemoMode &&
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY &&
  !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY.includes('mock') &&
  process.env.CLERK_SECRET_KEY &&
  !process.env.CLERK_SECRET_KEY.includes('mock')
);

type MiddlewareHandler = (
  request: NextRequest,
  event: NextFetchEvent
) => NextResponse | Promise<NextResponse>;

let handler: MiddlewareHandler | null = null;
if (hasRealClerkKeys) {
  try {
    handler = clerkMiddleware() as unknown as MiddlewareHandler;
  } catch {
    handler = null;
  }
}

export default function middleware(request: NextRequest, event: NextFetchEvent) {
  if (handler) {
    return handler(request, event);
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
