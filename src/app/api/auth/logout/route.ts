import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

export async function POST() {
  const cookieStore = await cookies();
  cookieStore.delete('labops_session');
  return NextResponse.json({
    success: true,
    redirect: '/auth/sign-in'
  });
}

export async function GET() {
  const cookieStore = await cookies();
  cookieStore.delete('labops_session');
  return NextResponse.redirect(new URL('/auth/sign-in', 'http://127.0.0.1:3000'));
}
