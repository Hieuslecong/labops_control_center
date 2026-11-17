import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

export async function POST(request: Request) {
  if (request.headers.get('origin') !== new URL(request.url).origin) {
    return NextResponse.json({ error: 'Nguồn yêu cầu không hợp lệ hoặc bị thiếu' }, { status: 403 });
  }
  const cookieStore = await cookies();
  cookieStore.delete('labops_session');
  return NextResponse.json({
    success: true,
    redirect: '/auth/sign-in'
  });
}
