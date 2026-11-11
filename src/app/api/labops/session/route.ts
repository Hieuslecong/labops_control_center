import { auth, currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { isDemoModeAllowed, hasRealClerkKeys } from '@/lib/auth-guard';

export const dynamic = 'force-dynamic';

export interface UserSessionData {
  authenticated: boolean;
  userId: string | null;
  username: string;
  fullName: string;
  email: string;
  imageUrl: string;
  role: 'ADMIN' | 'RESEARCHER' | null;
  permissions: string[];
  isDemo: boolean;
}

export async function GET(request: Request) {
  try {
    const isDemo = isDemoModeAllowed();

    // In demo mode without real Clerk keys
    if (isDemo && !hasRealClerkKeys()) {
      const url = new URL(request.url);
      const previewRole = url.searchParams.get('previewRole');
      const role = (previewRole === 'RESEARCHER' ? 'RESEARCHER' : 'ADMIN') as 'ADMIN' | 'RESEARCHER';
      return NextResponse.json<UserSessionData>({
        authenticated: true,
        userId: 'demo_admin_user',
        username: role === 'ADMIN' ? 'admin' : 'researcher_demo',
        fullName: role === 'ADMIN' ? 'Quản trị viên LabOps' : 'Nghiên cứu viên (Demo)',
        email: role === 'ADMIN' ? 'admin@labops.local' : 'researcher@labops.local',
        imageUrl: '',
        role,
        permissions: role === 'ADMIN' ? ['all'] : ['researcher:read', 'researcher:write'],
        isDemo: true
      });
    }

    const session = await auth();
    const { userId, orgId, has, sessionClaims } = session;

    if (!userId) {
      return NextResponse.json<UserSessionData>(
        {
          authenticated: false,
          userId: null,
          username: '',
          fullName: '',
          email: '',
          imageUrl: '',
          role: null,
          permissions: [],
          isDemo: false
        },
        { status: 401 }
      );
    }

    const metadataRole = String(
      (sessionClaims?.metadata as { role?: string } | undefined)?.role ||
        (sessionClaims?.publicMetadata as { role?: string } | undefined)?.role ||
        ''
    ).toUpperCase();

    let role: 'ADMIN' | 'RESEARCHER' = 'ADMIN';
    if (metadataRole === 'RESEARCHER') {
      role = 'RESEARCHER';
    } else if (orgId && typeof has === 'function' && !has({ role: 'org:admin' }) && metadataRole !== 'ADMIN') {
      role = 'RESEARCHER';
    }

    let fullName = '';
    let email = '';
    let imageUrl = '';
    let username = (sessionClaims?.username as string | undefined) || '';

    try {
      const user = await currentUser();
      if (user) {
        fullName = user.fullName || `${user.firstName || ''} ${user.lastName || ''}`.trim();
        email = user.primaryEmailAddress?.emailAddress || user.emailAddresses?.[0]?.emailAddress || '';
        imageUrl = user.imageUrl || '';
        if (!username) username = user.username || email.split('@')[0] || userId;
      }
    } catch {
      // currentUser may fail if Clerk service is slow, fallback to sessionClaims
    }

    if (!username) {
      username = (sessionClaims?.email as string | undefined)?.split('@')[0] || userId;
    }

    return NextResponse.json<UserSessionData>({
      authenticated: true,
      userId,
      username,
      fullName: fullName || `@${username}`,
      email,
      imageUrl,
      role,
      permissions: role === 'ADMIN' ? ['all'] : ['researcher:read', 'researcher:write'],
      isDemo: false
    });
  } catch (err: unknown) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ authenticated: false, role: null, error: errorMsg }, { status: 500 });
  }
}

