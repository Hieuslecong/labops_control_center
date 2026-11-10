import { auth } from '@clerk/nextjs/server';

/**
 * Bảo vệ trang nhạy cảm (fail-closed).
 * - Chế độ demo chỉ khi NEXT_PUBLIC_DEMO_MODE=true VÀ không phải production build.
 * - Thiếu khóa Clerk ngoài demo -> ném lỗi cấu hình (không âm thầm cho qua).
 * - Không bắt lỗi của auth.protect(): lệnh chuyển hướng đăng nhập phải được Next.js xử lý.
 */
export function isDemoModeAllowed(): boolean {
  return process.env.NEXT_PUBLIC_DEMO_MODE === 'true' && process.env.NODE_ENV !== 'production';
}

export function hasRealClerkKeys(): boolean {
  const pk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  const sk = process.env.CLERK_SECRET_KEY;
  return Boolean(pk && sk && !pk.includes('mock') && !sk.includes('mock'));
}

export async function protectRoute(): Promise<void> {
  if (isDemoModeAllowed()) return;
  if (!hasRealClerkKeys()) {
    throw new Error('Xác thực chưa được cấu hình: thiếu khóa Clerk. Truy cập bị từ chối.');
  }
  await auth.protect();
}
