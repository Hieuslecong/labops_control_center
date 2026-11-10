import { auth } from '@clerk/nextjs/server';
import { createHmac } from 'node:crypto';
import { NextResponse } from 'next/server';

/**
 * BFF LabOps V2: mọi lời gọi từ trình duyệt tới backend đi qua đây.
 * - Danh tính và vai trò lấy từ phiên Clerk phía server, KHÔNG từ dữ liệu client gửi.
 * - Phát token phiên ngắn hạn ký HMAC (LABOPS_SESSION_SECRET) để backend tự kiểm chữ ký.
 * - Fail-closed: thiếu cấu hình -> 503; chưa đăng nhập -> 401.
 */

function b64url(buf: Buffer): string {
  return buf.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function issueLabOpsToken(username: string, role: 'ADMIN' | 'RESEARCHER', secret: string): string {
  const payload = Buffer.from(
    JSON.stringify({ sub: username, role, member_id: null, exp: Math.floor(Date.now() / 1000) + 300 })
  );
  const sig = createHmac('sha256', secret).update(payload).digest();
  return `${b64url(payload)}.${b64url(sig)}`;
}

const ALLOWED_PATH = /^[a-zA-Z0-9_\-/.]+$/;

export async function proxyLabOps(
  request: Request,
  segments: string[],
  options: { requireAdmin?: boolean } = {}
): Promise<Response> {
  const rawBackend = (process.env.LABOPS_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
  const apiBase = rawBackend.endsWith('/api/v1') ? rawBackend : `${rawBackend}/api/v1`;
  const secret = process.env.LABOPS_SESSION_SECRET || process.env.LABOPS_AUTH_SECRET;
  if (!secret) {
    return NextResponse.json({ detail: 'Backend LabOps chưa cấu hình LABOPS_SESSION_SECRET' }, { status: 503 });
  }

  let session: Awaited<ReturnType<typeof auth>>;
  try {
    session = await auth();
  } catch {
    return NextResponse.json({ detail: 'Xác thực Clerk chưa được cấu hình' }, { status: 503 });
  }
  const { userId, orgId, has, sessionClaims } = session;
  if (!userId) return NextResponse.json({ detail: 'Yêu cầu đăng nhập' }, { status: 401 });

  const method = request.method;
  const originHeader = request.headers.get('origin');
  if (method !== 'GET' && originHeader && originHeader !== new URL(request.url).origin) {
    return NextResponse.json({ detail: 'Nguồn yêu cầu (Origin) không hợp lệ' }, { status: 403 });
  }

  const path = segments.join('/').replace(/^\/+/, '');
  if (!ALLOWED_PATH.test(path) || path.includes('..')) {
    return NextResponse.json({ detail: 'Đường dẫn API không hợp lệ' }, { status: 400 });
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

  if (options.requireAdmin && role !== 'ADMIN') {
    return NextResponse.json({ detail: 'Chỉ quản trị viên LabOps được phép thực hiện thao tác này' }, { status: 403 });
  }

  const username =
    (sessionClaims?.username as string | undefined) ||
    (sessionClaims?.email as string | undefined)?.split('@')[0] ||
    userId;
  const token = issueLabOpsToken(username, role, secret);

  const search = new URL(request.url).search;
  try {
    const body = method === 'GET' || method === 'DELETE' ? undefined : await request.text();
    const idem = request.headers.get('idempotency-key');
    const response = await fetch(`${apiBase}/${path}${search}`, {
      method,
      cache: 'no-store',
      headers: {
        Authorization: `Bearer ${token}`,
        'X-LabOps-Actor': username,
        ...(idem ? { 'Idempotency-Key': idem } : {}),
        ...(body ? { 'Content-Type': 'application/json' } : {})
      },
      ...(body ? { body } : {})
    });
    return new Response(response.body, {
      status: response.status,
      headers: { 'Content-Type': response.headers.get('Content-Type') || 'application/json' }
    });
  } catch {
    return NextResponse.json({ detail: 'Backend LabOps (cổng 8000) hiện không phản hồi' }, { status: 502 });
  }
}
