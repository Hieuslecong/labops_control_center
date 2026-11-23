import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';
import { MAX_SESSION_TTL_SECONDS } from '@/lib/session-token';

function buildExternalUrl(request: NextRequest, pathWithQuery: string): URL {
  const proto =
    request.headers.get('x-forwarded-proto')?.split(',')[0]?.trim() ||
    request.nextUrl.protocol.replace(/:$/, '') ||
    'http';
  const host =
    request.headers.get('x-forwarded-host')?.split(',')[0]?.trim() ||
    request.headers.get('host')?.trim() ||
    request.nextUrl.host;
  return new URL(pathWithQuery, `${proto}://${host}`);
}

function escapeHtml(raw: string): string {
  return raw
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function buildInvalidInviteHtml(reasonMsg: string, requestId: string): string {
  const safeReason = escapeHtml(reasonMsg);
  const safeReqId = escapeHtml(requestId || 'N/A');
  return `<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Liên kết phê duyệt 1 lần đã hết hiệu lực - LabOps</title>
  <style>
    body { margin: 0; font-family: system-ui, -apple-system, sans-serif; background: #f8fafc; color: #0f172a; display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 16px; }
    .card { max-width: 520px; width: 100%; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 28px; box-shadow: 0 4px 20px rgba(15, 23, 42, 0.06); }
    .badge { display: inline-block; font-size: 12px; font-weight: 600; color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; padding: 4px 10px; border-radius: 999px; margin-bottom: 12px; }
    h1 { font-size: 20px; margin: 0 0 10px; }
    p { font-size: 14px; line-height: 1.6; color: #475569; margin: 0 0 14px; }
    .meta { font-size: 12px; background: #f1f5f9; border-radius: 8px; padding: 12px; color: #334155; font-family: ui-monospace, monospace; }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">Liên kết 1 lần đã đóng</div>
    <h1>Liên kết phê duyệt không còn hiệu lực</h1>
    <p>Mỗi yêu cầu cấp quyền SSH chỉ có duy nhất một liên kết phê duyệt sử dụng 1 lần. Yêu cầu này đã được phê duyệt hoặc từ chối (hoặc liên kết đã hết hạn) nên không thể truy cập lại.</p>
    <div class="meta">Mã yêu cầu: ${safeReqId}<br/>Chi tiết: ${safeReason}</div>
  </div>
</body>
</html>`;
}

export async function GET(request: NextRequest) {
  const requestId = (request.nextUrl.searchParams.get('request_id') || '').trim();
  const invite = (request.nextUrl.searchParams.get('invite') || '').trim();

  if (!requestId || !invite) {
    return new NextResponse(
      buildInvalidInviteHtml('Thiếu mã yêu cầu hoặc mã xác thực Zalo.', requestId),
      {
        status: 410,
        headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' }
      }
    );
  }

  const rawBackend = (process.env.LABOPS_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
  const apiBase = rawBackend.endsWith('/api/v1') ? rawBackend : `${rawBackend}/api/v1`;

  try {
    const res = await fetch(`${apiBase}/auth/zalo-invite-session`, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: requestId, token: invite })
    });

    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok || !data.access_token) {
      const msg =
        typeof data.detail === 'string'
          ? data.detail
          : 'Liên kết phê duyệt Zalo không hợp lệ, đã được sử dụng hoặc đã hết hạn';
      return new NextResponse(buildInvalidInviteHtml(msg, requestId), {
        status: 410,
        headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' }
      });
    }

    const accessToken = String(data.access_token);
    const maxAge = Math.max(60, Math.min(Number(data.expires_in || 3600), MAX_SESSION_TTL_SECONDS));
    const cookieStore = await cookies();
    cookieStore.set('labops_session', accessToken, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      path: '/',
      maxAge
    });

    return NextResponse.redirect(
      buildExternalUrl(
        request,
        `/dashboard/ssh-requests/${encodeURIComponent(requestId)}/review?invite=${encodeURIComponent(invite)}&verified=1`
      ),
      { status: 303 }
    );
  } catch {
    return NextResponse.redirect(
      buildExternalUrl(request, '/auth/sign-in?error=Không+kết+nối+được+máy+chủ+xác+thực'),
      { status: 303 }
    );
  }
}
