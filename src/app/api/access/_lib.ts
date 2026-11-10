import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';

export async function proxyLabOpsAccess(request: Request, path: string, method = request.method) {
  let session: Awaited<ReturnType<typeof auth>>;
  try {
    session = await auth();
  } catch {
    return NextResponse.json({ detail: 'LabOps authentication is not configured' }, { status: 503 });
  }
  const { userId, orgId, has } = session;
  if (!userId) return NextResponse.json({ detail: 'Unauthorized' }, { status: 401 });
  if (!orgId || !has({ role: 'org:admin' })) {
    return NextResponse.json({ detail: 'LabOps organization administrator required' }, { status: 403 });
  }
  if (method !== 'GET' && request.headers.get('origin') !== new URL(request.url).origin) {
    return NextResponse.json({ detail: 'Invalid request origin' }, { status: 403 });
  }

  const backend = process.env.LABOPS_BACKEND_URL?.replace(/\/$/, '');
  const token = process.env.LABOPS_ADMIN_API_TOKEN;
  if (!backend || !token) {
    return NextResponse.json({ detail: 'LabOps access backend is not configured' }, { status: 503 });
  }
  if (path.startsWith('/') || path.includes('..')) {
    return NextResponse.json({ detail: 'Invalid access API path' }, { status: 400 });
  }

  try {
    const body = method === 'GET' || method === 'DELETE' ? undefined : await request.text();
    const response = await fetch(`${backend}/${path}`, {
      method,
      cache: 'no-store',
      headers: {
        Authorization: `Bearer ${token}`,
        'X-LabOps-Actor': userId,
        ...(body ? { 'Content-Type': 'application/json' } : {})
      },
      ...(body ? { body } : {})
    });
    return new Response(response.body, {
      status: response.status,
      headers: { 'Content-Type': response.headers.get('Content-Type') || 'application/json' }
    });
  } catch {
    return NextResponse.json({ detail: 'LabOps access backend is unavailable' }, { status: 502 });
  }
}
