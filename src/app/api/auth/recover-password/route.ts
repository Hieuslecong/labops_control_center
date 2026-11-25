import { NextResponse } from 'next/server';
import { validateSameOriginMutation } from '@/app/api/labops/_lib';

function getBackendApiBase(): string {
  const rawBackend = (process.env.LABOPS_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
  return rawBackend.endsWith('/api/v1') ? rawBackend : `${rawBackend}/api/v1`;
}

export async function POST(request: Request) {
  if (!validateSameOriginMutation(request)) {
    return NextResponse.json({ error: 'Nguồn yêu cầu không hợp lệ' }, { status: 403 });
  }

  try {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const backendRes = await fetch(`${getBackendApiBase()}/auth/recover-web-password`, {
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
            : 'Không thể đặt lại mật khẩu tài khoản Web';
      return NextResponse.json({ error: msg }, { status: backendRes.status });
    }

    return NextResponse.json(data, { status: 200 });
  } catch {
    return NextResponse.json({ error: 'Không kết nối được máy chủ xác thực' }, { status: 502 });
  }
}
