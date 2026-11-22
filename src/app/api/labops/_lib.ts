import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { getLocalSession } from '@/lib/auth-guard';
import { signSessionCookieToken } from '@/lib/session-token';

/**
 * BFF LabOps V2: mọi lời gọi từ trình duyệt tới backend đi qua đây.
 * - Danh tính và vai trò lấy từ phiên HMAC labops_session hoặc Clerk phía server, KHÔNG từ client header.
 * - Gắn Bearer token đã ký (ưu tiên rawToken để hỗ trợ thu hồi JTI khi logout/suspend).
 * - Kiểm tra Same-Origin CSRF & RBAC theo từng tuyến đường nhạy cảm.
 */

export function issueLabOpsToken(
  username: string,
  role: 'ADMIN' | 'RESEARCHER',
  secret: string
): string {
  return signSessionCookieToken({ username, role, ttlSeconds: 300, secret });
}

const ALLOWED_PATH = /^[a-zA-Z0-9_\-/.:]+$/;

export function validateSameOriginMutation(request: Request): boolean {
  const method = request.method.toUpperCase();
  if (method === 'GET' || method === 'HEAD' || method === 'OPTIONS') {
    return true;
  }

  const allowedOrigins = new Set<string>(['https://aiotserver.fitdnu.id.vn']);
  try {
    allowedOrigins.add(new URL(request.url).origin);
  } catch {
    return false;
  }

  const configuredCors = process.env.LABOPS_CORS_ORIGINS || '';
  for (const entry of configuredCors.split(',')) {
    const trimmed = entry.trim();
    if (!trimmed) continue;
    try {
      allowedOrigins.add(new URL(trimmed).origin);
    } catch {
      // ignore invalid origin entries
    }
  }

  for (const headerName of ['x-forwarded-host', 'host']) {
    const rawHeader = request.headers.get(headerName);
    if (!rawHeader) continue;
    const firstHost = rawHeader.split(',')[0]?.trim();
    if (!firstHost || /\s|[/\\]/.test(firstHost)) continue;
    allowedOrigins.add(`https://${firstHost}`);
    allowedOrigins.add(`http://${firstHost}`);
    const colonIdx = firstHost.lastIndexOf(':');
    if (colonIdx > 0 && !firstHost.endsWith(']')) {
      const bareHost = firstHost.slice(0, colonIdx).trim();
      if (bareHost) {
        allowedOrigins.add(`https://${bareHost}`);
        allowedOrigins.add(`http://${bareHost}`);
      }
    }
  }

  const originHeader = request.headers.get('origin');
  if (originHeader) {
    return allowedOrigins.has(originHeader);
  }
  const refererHeader = request.headers.get('referer');
  if (refererHeader) {
    try {
      return allowedOrigins.has(new URL(refererHeader).origin);
    } catch {
      return false;
    }
  }
  return false;
}

export function isAdminRequiredLabOpsPath(method: string, segments: string[]): boolean {
  const m = method.toUpperCase();
  const p = segments.join('/').replace(/^\/+|\/+$/g, '');

  if (p === 'auth/step-up') return true;
  if (p === 'users' || p.startsWith('users/')) return true;
  if (p.startsWith('admin/') || p.startsWith('ssh/') || p.startsWith('access/')) return true;
  if (
    p === 'audit-logs' ||
    p.startsWith('audit-logs/') ||
    p === 'events' ||
    p.startsWith('events/')
  ) {
    return true;
  }
  if (
    p === 'storage-pools' ||
    p.startsWith('storage-pools/') ||
    p === 'workspaces' ||
    p.startsWith('workspaces/')
  ) {
    return true;
  }
  if (
    p === 'ai-assistant/config' ||
    p === 'ai-assistant/tools' ||
    p === 'ai-assistant/history' ||
    p === 'ai-assistant/reports/generate'
  ) {
    return true;
  }
  if (
    p === 'repositories/audit' ||
    (m !== 'GET' && /^repositories\/requests\/[^/]+\/decision$/.test(p))
  ) {
    return true;
  }
  if (m !== 'GET' && /^processes\/.+\/(stop|kill)$/.test(p)) {
    return true;
  }
  if (
    p === 'v2/containers/summary' ||
    p === 'v2/reconcile' ||
    (m !== 'GET' && (p === 'v2/hosts' || p === 'v2/workspaces'))
  ) {
    return true;
  }
  if (p === 'v2/vpn/devices') return true;
  if (/^v2\/members\/[^/]+\/(suspend|archive|delete-preview)$/.test(p)) return true;
  if (m === 'DELETE' && /^v2\/members\/[^/]+$/.test(p)) return true;
  if (m !== 'GET' && /^v2\/members\/[^/]+\/devices(\/.*)?$/.test(p)) return true;
  if (
    p === 'v2/ssh-access/provision' ||
    /^v2\/ssh-access\/[^/]+\/(claim-key-bundle|claim-secrets|rotate-key|reissue-temp-password|revoke|terminate-sessions)$/.test(
      p
    )
  ) {
    return true;
  }
  return false;
}

export async function proxyLabOps(
  request: Request,
  segments: string[],
  options: { requireAdmin?: boolean } = {}
): Promise<Response> {
  const rawBackend = (process.env.LABOPS_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
  const apiBase = rawBackend.endsWith('/api/v1') ? rawBackend : `${rawBackend}/api/v1`;
  const secret = process.env.LABOPS_SESSION_SECRET || process.env.LABOPS_AUTH_SECRET;
  if (!secret) {
    return NextResponse.json(
      { detail: 'Backend LabOps chưa cấu hình LABOPS_SESSION_SECRET' },
      { status: 503 }
    );
  }

  if (!validateSameOriginMutation(request)) {
    return NextResponse.json(
      { detail: 'Nguồn yêu cầu (Origin/Referer) không hợp lệ' },
      { status: 403 }
    );
  }

  let normalizedSegments: string[];
  try {
    normalizedSegments = segments.map((s) => decodeURIComponent(s));
  } catch {
    return NextResponse.json({ detail: 'Đường dẫn API không hợp lệ' }, { status: 400 });
  }
  const path = normalizedSegments.join('/').replace(/^\/+/, '');
  if (
    !path ||
    !ALLOWED_PATH.test(path) ||
    normalizedSegments.some((s) => s === '..' || s.includes('..'))
  ) {
    return NextResponse.json({ detail: 'Đường dẫn API không hợp lệ' }, { status: 400 });
  }

  const localSession = await getLocalSession();
  let username: string;
  let role: 'ADMIN' | 'RESEARCHER';
  let bearerToken: string;

  if (localSession) {
    ({ username, role } = localSession);
    bearerToken = localSession.rawToken || issueLabOpsToken(username, role, secret);
  } else {
    let session: Awaited<ReturnType<typeof auth>>;
    try {
      session = await auth();
    } catch {
      return NextResponse.json({ detail: 'Xác thực Clerk chưa được cấu hình' }, { status: 503 });
    }
    const { userId, orgId, has, sessionClaims } = session;
    if (!userId) return NextResponse.json({ detail: 'Yêu cầu đăng nhập' }, { status: 401 });
    const metadataRole = String(
      (sessionClaims?.metadata as { role?: string } | undefined)?.role ||
        (sessionClaims?.publicMetadata as { role?: string } | undefined)?.role ||
        ''
    ).toUpperCase();
    role =
      metadataRole === 'ADMIN' || (orgId && typeof has === 'function' && has({ role: 'org:admin' }))
        ? 'ADMIN'
        : 'RESEARCHER';
    username =
      (sessionClaims?.username as string | undefined) ||
      (sessionClaims?.email as string | undefined)?.split('@')[0] ||
      userId;
    bearerToken = issueLabOpsToken(username, role, secret);
  }

  const method = request.method.toUpperCase();
  if (
    (options.requireAdmin || isAdminRequiredLabOpsPath(method, normalizedSegments)) &&
    role !== 'ADMIN'
  ) {
    return NextResponse.json(
      { detail: 'Chỉ quản trị viên LabOps được phép thực hiện thao tác này' },
      { status: 403 }
    );
  }

  const search = new URL(request.url).search;
  try {
    const body = method === 'GET' || method === 'HEAD' ? undefined : await request.text();
    const idem = request.headers.get('idempotency-key');
    const stepUpToken = request.headers.get('x-labops-step-up-token');
    const response = await fetch(`${apiBase}/${path}${search}`, {
      method,
      cache: 'no-store',
      headers: {
        Authorization: `Bearer ${bearerToken}`,
        ...(idem ? { 'Idempotency-Key': idem } : {}),
        ...(stepUpToken ? { 'X-LabOps-Step-Up-Token': stepUpToken } : {}),
        ...(body ? { 'Content-Type': 'application/json' } : {})
      },
      ...(body ? { body } : {})
    });
    const forwardHeaders: Record<string, string> = {
      'Content-Type': response.headers.get('Content-Type') || 'application/json',
      'Cache-Control': 'no-store, no-cache, must-revalidate'
    };
    const contentDisp = response.headers.get('Content-Disposition');
    if (contentDisp) {
      forwardHeaders['Content-Disposition'] = contentDisp;
    }
    return new Response(response.body, {
      status: response.status,
      headers: forwardHeaders
    });
  } catch {
    return NextResponse.json(
      { detail: 'Backend LabOps (cổng 8000) hiện không phản hồi' },
      { status: 502 }
    );
  }
}
