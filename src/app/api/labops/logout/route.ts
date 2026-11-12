import { NextResponse } from 'next/server';

export async function POST(request: Request): Promise<Response> {
  const origin = request.headers.get('origin');
  if (origin && origin !== new URL(request.url).origin) {
    return NextResponse.json({ detail: 'Nguồn yêu cầu không hợp lệ' }, { status: 403 });
  }
  const response = NextResponse.json({ success: true });
  response.cookies.delete('labops_session');
  return response;
}
