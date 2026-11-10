import Link from 'next/link';

export default function SignUpViewPage() {
  return (
    <main className='flex min-h-screen items-center justify-center bg-background p-6'>
      <section className='max-w-lg space-y-4 rounded-xl border bg-card p-8'>
        <h1 className='text-2xl font-semibold'>Tài khoản nghiên cứu được cấp bởi quản trị viên</h1>
        <p className='text-sm text-muted-foreground'>Không có đăng ký tự do. Admin tạo yêu cầu truy cập, xác minh workspace và gửi lời mời Teleport; researcher tự đăng ký thiết bị WireGuard và thiết lập WebAuthn MFA.</p>
        <p className='text-sm text-muted-foreground'>LabOps không cấp mật khẩu Linux, SSH key hay cổng SSH riêng. Trạng thái truy cập chỉ chuyển sang hoạt động sau khi các dịch vụ thật được kiểm chứng.</p>
        <Link className='text-sm text-primary underline' href='/auth/sign-in'>Quay lại đăng nhập quản trị</Link>
      </section>
    </main>
  );
}
