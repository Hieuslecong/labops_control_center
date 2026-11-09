import { NextResponse } from 'next/server';
import { proxyLabOpsAccess } from '../../../_lib';

type RouteContext = { params: Promise<{ id: string }> };

export async function PUT(request: Request, { params }: RouteContext) {
  const { id } = await params;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: 'Invalid JSON body' }, { status: 400 });
  }
  if (!body || typeof body !== 'object' || !['ACTIVE', 'SUSPENDED', 'ARCHIVED'].includes(String((body as { status?: unknown }).status))) {
    return NextResponse.json({ detail: 'Invalid account status' }, { status: 400 });
  }
  return proxyLabOpsAccess(
    new Request(request.url, { method: 'PUT', headers: request.headers, body: JSON.stringify(body) }),
    `users/${encodeURIComponent(id)}/status`
  );
}
