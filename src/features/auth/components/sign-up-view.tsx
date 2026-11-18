import Link from 'next/link';

export default function SignUpViewPage() {
  return (
    <main className='flex min-h-screen items-center justify-center bg-background p-6'>
      <section className='max-w-lg space-y-4 rounded-xl border bg-card p-8'>
        <h1 className='text-2xl font-semibold'>Tài khoản nghiên cứu được cấp bởi quản trị viên</h1>
        <p className='text-sm text-muted-foreground'>
          Không có đăng ký tự do. Admin cấp tài khoản LabOps, cấu hình WireGuard VPN và khởi tạo
          Docker Workspace cô lập; Nghiên cứu viên tự đăng ký khóa công khai OpenSSH và đổi mật khẩu
          lần đầu.
        </p>
        <p className='text-sm text-muted-foreground'>
          Mọi thông số danh tính, container workspace và quyền truy cập chỉ chuyển sang trạng thái
          hoạt động sau khi đồng bộ và kiểm chứng thành công trên máy chủ.
        </p>
        <Link className='text-sm text-primary underline' href='/auth/sign-in'>
          Quay lại trang đăng nhập LabOps
        </Link>
      </section>
    </main>
  );
}
