import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { validateSameOriginMutation } from '@/app/api/labops/_lib';
import { MAX_SESSION_TTL_SECONDS } from '@/lib/session-token';

function getBackendApiBase(): string {
  const rawBackend = (process.env.LABOPS_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
  return rawBackend.endsWith('/api/v1') ? rawBackend : `${rawBackend}/api/v1`;
}

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const username = searchParams.get('username');
    if (username !== null) {
      const res = await fetch(
        `${getBackendApiBase()}/auth/check-username?username=${encodeURIComponent(username)}`,
        { cache: 'no-store' }
      );
      const data = await res
        .json()
        .catch(() => ({ available: false, message: 'Không thể kiểm tra' }));
      return NextResponse.json(data, { status: res.status });
    }

    const res = await fetch(`${getBackendApiBase()}/auth/public-lecturers`, { cache: 'no-store' });
    const data = await res.json().catch(() => ({ lecturers: [] }));
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ lecturers: [] }, { status: 200 });
  }
}

export async function POST(request: Request) {
  if (!validateSameOriginMutation(request)) {
    return NextResponse.json({ error: 'Nguồn yêu cầu không hợp lệ' }, { status: 403 });
  }

  try {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const backendRes = await fetch(`${getBackendApiBase()}/auth/register`, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });

    const data = (await backendRes.json().catch(() => ({}))) as Record<string, unknown>;
    if (!backendRes.ok) {
      const msg =
        typeof data.detail === 'string'
          ? data.detail
          : typeof data.error === 'string'
            ? data.error
            : 'Không thể đăng ký tài khoản';
      return NextResponse.json({ error: msg }, { status: backendRes.status });
    }

    const accessToken = String(data.access_token || '');
    const maxAge = Math.max(
      60,
      Math.min(Number(data.expires_in || MAX_SESSION_TTL_SECONDS), MAX_SESSION_TTL_SECONDS)
    );

    if (accessToken) {
      const cookieStore = await cookies();
      cookieStore.set('labops_session', accessToken, {
        httpOnly: true,
        secure: process.env.NODE_ENV === 'production',
        sameSite: 'lax',
        path: '/',
        maxAge
      });
    }

    return NextResponse.json({
      success: true,
      username: data.username,
      role: 'RESEARCHER',
      member_category: data.member_category,
      ssh_access_state: data.ssh_access_state || 'NONE',
      redirect: '/dashboard/researcher/ssh-request'
    });
  } catch {
    return NextResponse.json({ error: 'Không kết nối được máy chủ đăng ký' }, { status: 502 });
  }
}
