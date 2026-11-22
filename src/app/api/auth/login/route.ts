import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { timingSafeEqual } from 'node:crypto';
import { validateSameOriginMutation } from '@/app/api/labops/_lib';
import { MAX_SESSION_TTL_SECONDS, signSessionCookieToken } from '@/lib/session-token';

export async function POST(request: Request) {
  const contentType = (request.headers.get('content-type') || '').toLowerCase();
  const isFormPost =
    contentType.includes('application/x-www-form-urlencoded') ||
    contentType.includes('multipart/form-data');

  const respondError = (msg: string, status: number) => {
    if (isFormPost) {
      return new Response(null, {
        status: 303,
        headers: { Location: '/auth/sign-in?error=' + encodeURIComponent(msg) }
      });
    }
    return NextResponse.json({ error: msg }, { status });
  };

  const respondSuccess = (payload: Record<string, unknown>, redirect: string) => {
    if (isFormPost) {
      return new Response(null, {
        status: 303,
        headers: { Location: redirect }
      });
    }
    return NextResponse.json(payload);
  };

  if (!validateSameOriginMutation(request)) {
    return respondError('Nguồn yêu cầu không hợp lệ', 403);
  }

  try {
    let username = '';
    let password = '';
    if (isFormPost) {
      const formData = await request.formData().catch(() => null);
      username = String(formData?.get('username') || '').trim();
      password = String(formData?.get('password') || '');
    } else {
      const body = await request.json().catch(() => ({}));
      username = String(body?.username || '').trim();
      password = String(body?.password || '');
    }

    if (!username || !password) {
      return respondError('Thiếu tên đăng nhập hoặc mật khẩu', 400);
    }

    const rawBackend = (process.env.LABOPS_BACKEND_URL || 'http://127.0.0.1:8000').replace(
      /\/$/,
      ''
    );
    const apiBase = rawBackend.endsWith('/api/v1') ? rawBackend : `${rawBackend}/api/v1`;

    let backendResponse: Response | null = null;
    try {
      backendResponse = await fetch(`${apiBase}/auth/login`, {
        method: 'POST',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
    } catch {
      backendResponse = null;
    }

    if (backendResponse) {
      const data = (await backendResponse.json().catch(() => ({}))) as Record<string, unknown>;
      if (!backendResponse.ok) {
        const msg =
          typeof data.detail === 'string'
            ? data.detail
            : typeof data.error === 'string'
              ? data.error
              : 'Tài khoản hoặc mật khẩu không chính xác';
        return respondError(msg, backendResponse.status);
      }

      const accessToken = String(data.access_token || '');
      const role = data.role === 'ADMIN' ? 'ADMIN' : 'RESEARCHER';
      const mustChange = Boolean(data.must_change_password);
      const maxAge = Math.max(
        60,
        Math.min(Number(data.expires_in || MAX_SESSION_TTL_SECONDS), MAX_SESSION_TTL_SECONDS)
      );

      if (!accessToken) {
        return respondError('Backend không trả về token hợp lệ', 502);
      }

      const cookieStore = await cookies();
      cookieStore.set('labops_session', accessToken, {
        httpOnly: true,
        secure: process.env.NODE_ENV === 'production',
        sameSite: 'lax',
        path: '/',
        maxAge
      });

      const redirect = mustChange
        ? '/dashboard/researcher?changePassword=1'
        : role === 'ADMIN'
          ? '/dashboard/overview'
          : '/dashboard/researcher';

      return respondSuccess(
        {
          success: true,
          role,
          username: String(data.username || username),
          must_change_password: mustChange,
          message: 'Đăng nhập thành công',
          redirect
        },
        redirect
      );
    }

    // Fallback when FastAPI backend is unreachable during standalone Next.js dev: Admin local env check
    const expectedUser = (process.env.LABOPS_ADMIN_USERNAME || 'admin').trim();
    const expectedPass = process.env.LABOPS_ADMIN_PASSWORD;
    if (!expectedPass) {
      return respondError('Backend không phản hồi và chưa cấu hình LABOPS_ADMIN_PASSWORD', 503);
    }

    const userMatch = username.toLowerCase() === expectedUser.toLowerCase();
    const userPassBuf = Buffer.from(password);
    const expectedPassBuf = Buffer.from(expectedPass);
    const passMatch =
      userPassBuf.length === expectedPassBuf.length &&
      timingSafeEqual(userPassBuf, expectedPassBuf);

    if (!userMatch || !passMatch) {
      return respondError('Tài khoản hoặc mật khẩu không chính xác', 401);
    }

    const secret = process.env.LABOPS_SESSION_SECRET || process.env.LABOPS_AUTH_SECRET || '';
    if (!secret) {
      return respondError('Hệ thống chưa cấu hình LABOPS_SESSION_SECRET', 503);
    }

    const sessionToken = signSessionCookieToken({
      username: expectedUser,
      role: 'ADMIN',
      ttlSeconds: MAX_SESSION_TTL_SECONDS,
      secret
    });

    const cookieStore = await cookies();
    cookieStore.set('labops_session', sessionToken, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      path: '/',
      maxAge: MAX_SESSION_TTL_SECONDS
    });

    return respondSuccess(
      {
        success: true,
        role: 'ADMIN',
        username: expectedUser,
        must_change_password: false,
        message: 'Đăng nhập Quản trị viên thành công',
        redirect: '/dashboard/overview'
      },
      '/dashboard/overview'
    );
  } catch {
    return respondError('Lỗi máy chủ trong quá trình xử lý đăng nhập', 500);
  }
}
