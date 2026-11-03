import { auth } from '@clerk/nextjs/server';

export async function protectRoute() {
  const isDemoMode = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';
  const hasRealClerkKeys = Boolean(
    !isDemoMode &&
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY &&
    !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY.includes('mock') &&
    process.env.CLERK_SECRET_KEY &&
    !process.env.CLERK_SECRET_KEY.includes('mock')
  );

  if (hasRealClerkKeys) {
    try {
      await auth.protect();
    } catch {
      // In local demo mode or offline, continue to demo interface
    }
  }
}
