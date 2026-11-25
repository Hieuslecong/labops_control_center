import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  MAX_SESSION_TTL_SECONDS,
  signSessionCookieToken,
  verifySessionCookieToken
} from '../lib/session-token';
import { isAdminRequiredLabOpsPath, validateSameOriginMutation } from '../app/api/labops/_lib';
import { adminNavGroups } from '../config/nav-config';

const THIS_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = path.resolve(THIS_DIR, '../..');
const SRC_DIR = path.join(ROOT_DIR, 'src');

function walkTsFiles(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  const results: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...walkTsFiles(full));
    } else if (/\.(ts|tsx|css|mjs)$/.test(entry.name)) {
      results.push(full);
    }
  }
  return results;
}

describe('Milestone 3 Session Token Hardening', () => {
  const secret = 'test-labops-session-secret-0123456789abcdef';

  it('enforces an 8-hour maximum session TTL', () => {
    assert.equal(MAX_SESSION_TTL_SECONDS, 28800);
    const token = signSessionCookieToken({
      username: 'minhtq',
      role: 'RESEARCHER',
      memberId: 'mem-123',
      ttlSeconds: 999999,
      secret
    });
    const verified = verifySessionCookieToken(token, secret);
    assert.ok(verified);
    assert.equal(verified.username, 'minhtq');
    assert.equal(verified.role, 'RESEARCHER');
    assert.equal(verified.memberId, 'mem-123');
    assert.ok(verified.exp - (verified.iat ?? 0) <= MAX_SESSION_TTL_SECONDS);
  });

  it('rejects tampered tokens and wrong secrets', () => {
    const token = signSessionCookieToken({ username: 'admin', role: 'ADMIN', secret });
    const [payloadB64, sigB64] = token.split('.');
    const tamperedSigTail = `${sigB64.slice(0, -1)}${sigB64.endsWith('A') ? 'B' : 'A'}`;
    const tamperedSigHead = `${sigB64.startsWith('A') ? 'B' : 'A'}${sigB64.slice(1)}`;
    assert.equal(verifySessionCookieToken(`${payloadB64}.${tamperedSigTail}`, secret), null);
    assert.equal(verifySessionCookieToken(`${payloadB64}.${tamperedSigHead}`, secret), null);
    assert.equal(verifySessionCookieToken(token, 'wrong-secret'), null);
    assert.equal(verifySessionCookieToken('malformed', secret), null);
  });

  it('rejects expired tokens and non-session token scopes (e.g. step_up)', () => {
    const now = Math.floor(Date.now() / 1000);
    const makeSigned = (payload: Record<string, unknown>) => {
      const buf = Buffer.from(JSON.stringify(payload), 'utf-8');
      const sig = createHmac('sha256', secret).update(buf).digest('base64url');
      return `${buf.toString('base64url')}.${sig}`;
    };

    const expiredToken = makeSigned({
      sub: 'admin',
      role: 'ADMIN',
      exp: now - 10,
      scope: 'session'
    });
    assert.equal(verifySessionCookieToken(expiredToken, secret), null);

    const stepUpToken = makeSigned({
      sub: 'admin',
      role: 'ADMIN',
      exp: now + 300,
      scope: 'step_up'
    });
    assert.equal(verifySessionCookieToken(stepUpToken, secret), null);
  });
});

describe('Milestone 3 BFF CSRF & RBAC Policy', () => {
  it('validates same-origin headers for state-changing requests and allows safe methods', () => {
    const getReq = new Request('https://labops.local/api/labops/v2/overview');
    assert.equal(validateSameOriginMutation(getReq), true);

    const validOriginPost = new Request('https://labops.local/api/labops/v2/ssh-keys', {
      method: 'POST',
      headers: { Origin: 'https://labops.local' }
    });
    assert.equal(validateSameOriginMutation(validOriginPost), true);

    const validRefererDelete = new Request('https://labops.local/api/labops/v2/members/u1', {
      method: 'DELETE',
      headers: { Referer: 'https://labops.local/dashboard/users' }
    });
    assert.equal(validateSameOriginMutation(validRefererDelete), true);

    const evilOriginPost = new Request('https://labops.local/api/labops/v2/ssh-keys', {
      method: 'POST',
      headers: { Origin: 'https://evil.attacker' }
    });
    assert.equal(validateSameOriginMutation(evilOriginPost), false);

    const proxiedPost = new Request('http://127.0.0.1:3000/api/auth/login', {
      method: 'POST',
      headers: {
        Origin: 'https://aiotserver.fitdnu.id.vn',
        'X-Forwarded-Host': 'aiotserver.fitdnu.id.vn'
      }
    });
    assert.equal(validateSameOriginMutation(proxiedPost), true);

    const proxiedEvilPost = new Request('http://127.0.0.1:3000/api/auth/login', {
      method: 'POST',
      headers: {
        Origin: 'https://evil.attacker',
        'X-Forwarded-Host': 'aiotserver.fitdnu.id.vn'
      }
    });
    assert.equal(validateSameOriginMutation(proxiedEvilPost), false);

    const missingHeadersPost = new Request('https://labops.local/api/labops/v2/ssh-keys', {
      method: 'POST'
    });
    assert.equal(validateSameOriginMutation(missingHeadersPost), false);
  });

  it('enforces admin RBAC for privileged paths while allowing self-service researcher endpoints', () => {
    assert.equal(isAdminRequiredLabOpsPath('POST', ['auth', 'step-up']), false);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['audit-logs']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['v2', 'vpn', 'overview']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'overview']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'reports']), true);
    assert.equal(
      isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'reports', 'rpt-1', 'download']),
      true
    );
    assert.equal(isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'daily-brief', 'status']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'identity-links']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'subscriptions']), true);
    assert.equal(
      isAdminRequiredLabOpsPath('POST', ['ai-assistant', 'incidents', 'inc-1', 'resolve']),
      true
    );
    assert.equal(isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'v4', 'metrics']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'v4', 'connectors']), true);
    assert.equal(
      isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'v4', 'events', 'coverage']),
      true
    );
    assert.equal(isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'v4', 'investigations']), true);
    assert.equal(
      isAdminRequiredLabOpsPath('POST', [
        'ai-assistant',
        'v4',
        'investigations',
        'inv4-1',
        'feedback'
      ]),
      true
    );
    assert.equal(
      isAdminRequiredLabOpsPath('POST', ['ai-assistant', 'v4', 'approvals', 'act4-1', 'confirm']),
      true
    );
    assert.equal(
      isAdminRequiredLabOpsPath('POST', ['ai-assistant', 'v4', 'retention', 'run']),
      true
    );
    assert.equal(
      isAdminRequiredLabOpsPath('POST', ['ai-assistant', 'v4', 'config', 'reload']),
      true
    );
    assert.equal(isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'v4', 'tasks']), false);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['ai-assistant', 'v4', 'health']), false);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['repositories', 'audit']), true);
    assert.equal(
      isAdminRequiredLabOpsPath('POST', ['repositories', 'requests', 'req-1', 'decision']),
      true
    );
    assert.equal(
      isAdminRequiredLabOpsPath('POST', ['repositories', 'requests', 'req-1', 'rescan']),
      true
    );
    assert.equal(
      isAdminRequiredLabOpsPath('POST', ['processes', 'workstation:host:123:0', 'stop']),
      true
    );
    assert.equal(isAdminRequiredLabOpsPath('GET', ['v2', 'members', 'u1', 'delete-preview']), true);
    assert.equal(isAdminRequiredLabOpsPath('POST', ['v2', 'members', 'u1', 'suspend']), true);
    assert.equal(isAdminRequiredLabOpsPath('POST', ['v2', 'members', 'u1', 'archive']), true);
    assert.equal(isAdminRequiredLabOpsPath('DELETE', ['v2', 'members', 'u1']), true);

    assert.equal(isAdminRequiredLabOpsPath('GET', ['v2', 'researcher', 'me']), false);
    assert.equal(isAdminRequiredLabOpsPath('POST', ['v2', 'ssh-keys']), false);
    assert.equal(
      isAdminRequiredLabOpsPath('POST', ['v2', 'ssh-access', 'first-login-change-password']),
      false
    );
    assert.equal(isAdminRequiredLabOpsPath('GET', ['repositories', 'requests']), false);
    assert.equal(isAdminRequiredLabOpsPath('POST', ['repositories', 'requests']), false);
  });
});

describe('Milestone 3 Codebase Hygiene & UI Invariants', () => {
  const allSrcFiles = walkTsFiles(SRC_DIR);

  it('removes all setInterval loops from src/', () => {
    const matches = allSrcFiles.filter((f) => {
      if (f.endsWith('milestone3-frontend-hardening.test.ts')) return false;
      return fs.readFileSync(f, 'utf-8').includes('setInterval(');
    });
    assert.deepEqual(matches, []);
  });

  it('standardizes CLI instructions on gitaiot and removes legacy CLI/template artifacts', () => {
    const nextConfigText = fs.readFileSync(path.join(ROOT_DIR, 'next.config.ts'), 'utf-8');
    assert.equal(nextConfigText.includes('api.slingacademy.com'), false);

    const badRefs = allSrcFiles.filter((f) => {
      if (f.endsWith('milestone3-frontend-hardening.test.ts')) return false;
      const content = fs.readFileSync(f, 'utf-8');
      return (
        content.includes('gitlabops') ||
        content.includes('labops clone') ||
        content.includes('Sarah Connor') ||
        content.includes('api.slingacademy.com')
      );
    });
    assert.deepEqual(badRefs, []);
  });

  it('configures 5 clean admin navigation groups including /dashboard/security', () => {
    assert.equal(adminNavGroups.length, 5);
    const urls = adminNavGroups.flatMap((g) => g.items.map((item) => item.url));
    assert.equal(urls.includes('/dashboard/security'), true);
  });

  it('enforces WCAG minimum text-xs sizing outside upstream ui primitives', () => {
    const targetDirs = [
      path.join(SRC_DIR, 'features'),
      path.join(SRC_DIR, 'components', 'layout'),
      path.join(SRC_DIR, 'app')
    ];
    const targetFiles = [
      ...targetDirs.flatMap(walkTsFiles),
      path.join(SRC_DIR, 'components', 'org-switcher.tsx')
    ];
    const microFontRegex = /text-\[(?:8|9|10|11)px\]/;
    const offenders = targetFiles.filter(
      (f) => fs.existsSync(f) && microFontRegex.test(fs.readFileSync(f, 'utf-8'))
    );
    assert.deepEqual(offenders, []);
  });
});
