import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { createHmac, timingSafeEqual } from 'node:crypto';

export async function POST(request: Request) {
  try {
    if (request.headers.get('origin') !== new URL(request.url).origin) {
      return NextResponse.json({ error: 'Nguồn yêu cầu không hợp lệ hoặc bị thiếu' }, { status: 403 });
    }
    const body = await request.json().catch(() => ({}));
    const username = String(body?.username || '').trim();
    const password = String(body?.password || '');

    const expectedUser = (process.env.LABOPS_ADMIN_USERNAME || 'admin').trim();
    const expectedPass = process.env.LABOPS_ADMIN_PASSWORD;
    if (!expectedPass) {
      return NextResponse.json({ error: 'Hệ thống chưa cấu hình mật khẩu quản trị' }, { status: 503 });
    }

    const userMatch = username.toLowerCase() === expectedUser.toLowerCase();

    // Constant-time password check
    const userPassBuf = Buffer.from(password);
    const expectedPassBuf = Buffer.from(expectedPass);
    const passMatch =
      userPassBuf.length === expectedPassBuf.length &&
      timingSafeEqual(userPassBuf, expectedPassBuf);

    if (!userMatch || !passMatch) {
      return NextResponse.json(
        { error: 'Tài khoản hoặc mật khẩu không chính xác' },
        { status: 401 }
      );
    }

    const secret =
      process.env.LABOPS_SESSION_SECRET ||
      process.env.LABOPS_AUTH_SECRET ||
      '';
    if (!secret) {
      return NextResponse.json(
        { error: 'Hệ thống chưa cấu hình LABOPS_SESSION_SECRET' },
        { status: 503 }
      );
    }

    // Local login sessions are short lived and are not API bearer tokens.
    const ttlSeconds = 60 * 60;
    const issuedAt = Math.floor(Date.now() / 1000);
    const exp = issuedAt + ttlSeconds;
    const payloadObj = {
      sub: expectedUser,
      role: 'ADMIN',
      member_id: null,
      iat: issuedAt,
      exp,
      iss: 'labops-local',
      aud: 'labops-web'
    };

    const payloadBuf = Buffer.from(JSON.stringify(payloadObj), 'utf-8');
    const payloadB64 = payloadBuf.toString('base64url');
    const sig = createHmac('sha256', secret).update(payloadBuf).digest('base64url');
    const sessionToken = `${payloadB64}.${sig}`;

    const cookieStore = await cookies();
    cookieStore.set('labops_session', sessionToken, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      path: '/',
      maxAge: ttlSeconds
    });

    return NextResponse.json({
      success: true,
      message: 'Đăng nhập Quản trị viên thành công',
      redirect: '/dashboard/overview'
    });
  } catch (err) {
    return NextResponse.json(
      { error: 'Lỗi máy chủ trong quá trình xử lý đăng nhập' },
      { status: 500 }
    );
  }
}
