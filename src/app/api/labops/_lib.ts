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

export async function proxyLabOps(request: Request, segments: string[]): Promise<Response> {
  const backend = process.env.LABOPS_BACKEND_URL?.replace(/\/$/, '');
  const secret = process.env.LABOPS_SESSION_SECRET;
  if (!backend || !secret) {
    return NextResponse.json({ detail: 'Backend LabOps chưa được cấu hình' }, { status: 503 });
  }

  let session: Awaited<ReturnType<typeof auth>>;
  try {
    session = await auth();
  } catch {
    return NextResponse.json({ detail: 'Xác thực chưa được cấu hình' }, { status: 503 });
  }
  const { userId, orgId, has, sessionClaims } = session;
  if (!userId) return NextResponse.json({ detail: 'Yêu cầu đăng nhập' }, { status: 401 });

  const method = request.method;
  if (method !== 'GET' && request.headers.get('origin') !== new URL(request.url).origin) {
    return NextResponse.json({ detail: 'Nguồn yêu cầu không hợp lệ' }, { status: 403 });
  }

  const path = segments.join('/');
  if (!ALLOWED_PATH.test(path) || path.includes('..')) {
    return NextResponse.json({ detail: 'Đường dẫn không hợp lệ' }, { status: 400 });
  }

  const role = orgId && has({ role: 'org:admin' }) ? 'ADMIN' : 'RESEARCHER';
  const username =
    (sessionClaims?.username as string | undefined) ||
    (sessionClaims?.email as string | undefined) ||
    userId;
  const token = issueLabOpsToken(username, role, secret);

  const search = new URL(request.url).search;
  try {
    const body = method === 'GET' || method === 'DELETE' ? undefined : await request.text();
    const idem = request.headers.get('idempotency-key');
    const response = await fetch(`${backend}/api/v1/${path}${search}`, {
      method,
      cache: 'no-store',
      headers: {
        Authorization: `Bearer ${token}`,
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
    return NextResponse.json({ detail: 'Backend LabOps không phản hồi' }, { status: 502 });
  }
}

