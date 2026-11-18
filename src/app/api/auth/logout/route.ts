import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { validateSameOriginMutation } from '@/app/api/labops/_lib';

export async function POST(request: Request) {
  if (!validateSameOriginMutation(request)) {
    return NextResponse.json({ detail: 'Nguồn yêu cầu không hợp lệ' }, { status: 403 });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get('labops_session')?.value;
  if (token) {
    const rawBackend = (process.env.LABOPS_BACKEND_URL || 'http://127.0.0.1:8000').replace(
      /\/$/,
      ''
    );
    const apiBase = rawBackend.endsWith('/api/v1') ? rawBackend : `${rawBackend}/api/v1`;
    await fetch(`${apiBase}/auth/logout`, {
      method: 'POST',
      cache: 'no-store',
      headers: { Authorization: `Bearer ${token}` }
    }).catch(() => null);
  }

  cookieStore.delete('labops_session');
  return NextResponse.json({
    success: true,
    redirect: '/auth/sign-in'
  });
}
