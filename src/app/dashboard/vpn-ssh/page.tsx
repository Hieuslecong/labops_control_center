import PageContainer from '@/components/layout/page-container';
import { protectRoute } from '@/lib/auth-guard';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

export const metadata = {
  title: 'Truy cập VPN và SSH - LabOps Control Center',
  description: 'Quản lý truy cập từ xa qua WireGuard VPN và OpenSSH với khóa công khai riêng cho từng thành viên'
};

export default async function VpnSshPage() {
  await protectRoute();
  return (
    <PageContainer
      pageTitle='Quản lý Truy cập Từ xa (WireGuard VPN & OpenSSH)'
      pageDescription='Kiến trúc V2: WireGuard VPN -> Địa chỉ nội bộ được cấp -> OpenSSH với khóa công khai riêng cho từng thành viên vào Workspace cô lập (Bảo toàn nguyên vẹn Legacy SSH cổng 22)'
    >
      <div className='grid gap-4 md:grid-cols-2'>
        <Card>
          <CardHeader>
            <div className='flex items-center justify-between'>
              <CardTitle className='text-sm font-bold'>Chính sách WireGuard VPN (wg0)</CardTitle>
              <Badge variant='outline' className='font-mono text-[10px]'>
                CLIENT PUBLIC KEY ONLY
              </Badge>
            </div>
            <CardDescription className='text-xs'>
              Không triển khai NetBird; giữ nguyên cấu hình wg0 và các peer đang hoạt động
            </CardDescription>
          </CardHeader>
          <CardContent className='text-xs space-y-2 text-muted-foreground'>
            <p>
              • Thiết bị người dùng tự sinh cặp khóa WireGuard và chỉ gửi <strong>Public Key</strong>{' '}
              lên hệ thống.
            </p>
            <p>
              • Máy chủ tuyệt đối không tự sinh hay trả về Private Key qua API hoặc giao diện web.
            </p>
            <p>
              • Peer chỉ được coi là <code>ACTIVE</code> khi có bắt tay (handshake) thực tế trên giao
              diện <code>wg0</code>.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className='flex items-center justify-between'>
              <CardTitle className='text-sm font-bold'>
                Chính sách OpenSSH Per-Member Public Key
              </CardTitle>
              <Badge variant='outline' className='font-mono text-[10px] text-emerald-600'>
                ISOLATED WORKSPACE SSH
              </Badge>
            </div>
            <CardDescription className='text-xs'>
              Mỗi thành viên sử dụng khóa công khai SSH riêng để truy cập trực tiếp vào workspace cô lập
            </CardDescription>
          </CardHeader>
          <CardContent className='text-xs space-y-2 text-muted-foreground'>
            <p>
              • Không dùng tài khoản chung, không dùng mật khẩu yếu, không cấp quyền{' '}
              <code>sudo</code> trên máy chủ vật lý.
            </p>
            <p>
              • Cổng SSH quản trị truyền thống (Legacy SSH) trên máy chủ được bảo toàn nguyên trạng
              để quản trị viên vận hành khẩn cấp.
            </p>
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}

