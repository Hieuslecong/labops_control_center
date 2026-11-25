import { createHmac, randomUUID, timingSafeEqual } from 'node:crypto';

export const MAX_SESSION_TTL_SECONDS = 8 * 3600;

export interface VerifiedSessionToken {
  username: string;
  role: 'ADMIN' | 'RESEARCHER';
  memberId: string | null;
  jti: string | null;
  iat: number | null;
  exp: number;
  rawToken: string;
}

export interface SignSessionTokenOptions {
  username: string;
  role: 'ADMIN' | 'RESEARCHER';
  memberId?: string | null;
  ttlSeconds?: number;
  secret: string;
  jti?: string;
}

export function signSessionCookieToken(opts: SignSessionTokenOptions): string {
  const secret = (opts.secret || '').trim();
  if (!secret) {
    throw new Error('LABOPS_SESSION_SECRET is required to sign session token');
  }
  const now = Math.floor(Date.now() / 1000);
  const rawTtl =
    typeof opts.ttlSeconds === 'number' && Number.isFinite(opts.ttlSeconds)
      ? Math.floor(opts.ttlSeconds)
      : MAX_SESSION_TTL_SECONDS;
  const clampedTtl = Math.max(1, Math.min(rawTtl, MAX_SESSION_TTL_SECONDS));
  const payloadObj = {
    sub: opts.username.trim(),
    role: opts.role,
    member_id: opts.memberId ?? null,
    jti: opts.jti || randomUUID().replace(/-/g, ''),
    iat: now,
    exp: now + clampedTtl,
    scope: 'session'
  };
  const payloadBuf = Buffer.from(JSON.stringify(payloadObj), 'utf-8');
  const payloadB64 = payloadBuf.toString('base64url');
  const sigB64 = createHmac('sha256', secret).update(payloadBuf).digest('base64url');
  return `${payloadB64}.${sigB64}`;
}

export function verifySessionCookieToken(
  token: string | undefined | null,
  secret: string | undefined | null
): VerifiedSessionToken | null {
  try {
    if (!token || !secret) return null;
    const raw = token.trim();
    const parts = raw.split('.');
    if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
    const [payloadB64, sigB64] = parts;

    const payloadBuf = Buffer.from(payloadB64, 'base64url');
    if (payloadB64 !== payloadBuf.toString('base64url')) {
      return null;
    }
    const expectedSigB64 = createHmac('sha256', secret).update(payloadBuf).digest('base64url');
    const providedBuf = Buffer.from(sigB64, 'utf8');
    const expectedBuf = Buffer.from(expectedSigB64, 'utf8');

    if (expectedBuf.length !== providedBuf.length || !timingSafeEqual(expectedBuf, providedBuf)) {
      return null;
    }

    const data = JSON.parse(payloadBuf.toString('utf-8')) as Record<string, unknown>;
    if (!data || typeof data !== 'object') return null;

    const now = Math.floor(Date.now() / 1000);
    if (typeof data.exp !== 'number' || !Number.isInteger(data.exp) || data.exp <= now) {
      return null;
    }
    if (typeof data.sub !== 'string' || !data.sub.trim()) {
      return null;
    }
    if (data.role !== 'ADMIN' && data.role !== 'RESEARCHER') {
      return null;
    }
    if (data.scope !== undefined && data.scope !== 'session') {
      return null;
    }

    return {
      username: data.sub.trim(),
      role: data.role,
      memberId: typeof data.member_id === 'string' ? data.member_id : null,
      jti: typeof data.jti === 'string' ? data.jti : null,
      iat: typeof data.iat === 'number' && Number.isInteger(data.iat) ? data.iat : null,
      exp: data.exp,
      rawToken: raw
    };
  } catch {
    return null;
  }
}
