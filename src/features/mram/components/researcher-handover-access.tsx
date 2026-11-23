'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { labopsCall } from '@/features/labops/api';
import { toast } from 'sonner';

interface HandoverData {
  ready: boolean;
  ssh_access_state: string;
  web_account_status: string;
  member_category: string;
  member_id?: string;
  username?: string;
  assigned_server?: string;
  ssh_host?: string;
  ssh_port?: number;
  workspace_dir?: string;
  cpu_limit_cores?: number;
  ram_limit_gb?: number;
  storage_quota_gb?: number;
  gpu_permissions?: string[];
  gpu_mode?: string;
  ssh_expires_at?: string | null;
  secret_already_claimed?: boolean;
  vault_available?: boolean;
  ssh_command?: string;
  vscode_config?: string;
  message?: string;
}

export function ResearcherHandoverAccess() {
  const [data, setData] = useState<HandoverData | null>(null);
  const [loading, setLoading] = useState(true);
  const [stepUpPassword, setStepUpPassword] = useState('');
  const [revealedSecret, setRevealedSecret] = useState<{
    temporary_password?: string;
    passphrase?: string;
    notice?: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const loadHandover = useCallback(async () => {
    setLoading(true);
    const res = await labopsCall<HandoverData>('me/ssh-access');
    if (res.ok && res.data) {
      setData(res.data);
    } else {
      toast.error('Không thể tải thông tin bàn giao SSH', { description: res.error });
    }
    setLoading(false);
  }, []);

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void loadHandover();
  }, [loadHandover]);
  /* oxlint-enable react/set-state-in-effect */

  const handleClaimOrReissue = async (mode: 'claim' | 'reissue') => {
    if (!data?.username) return;
    if (!stepUpPassword.trim()) {
      toast.error('Vui lòng nhập Mật khẩu Web của bạn để xác thực Step-Up');
      return;
    }
    setBusy(true);
    const stepRes = await labopsCall<{ step_up_token: string }>('auth/step-up', {
      method: 'POST',
      body: {
        password: stepUpPassword,
        action: mode === 'claim' ? 'CLAIM_SSH_SECRET' : 'REISSUE_SSH_SECRET',
        target_id: data.username
      }
    });
    if (!stepRes.ok || !stepRes.data?.step_up_token) {
      setBusy(false);
      toast.error('Xác thực mật khẩu thất bại', { description: stepRes.error });
      return;
    }

    const res = await labopsCall<{
      temporary_password?: string;
      passphrase?: string;
      one_time_notice?: string;
    }>(`me/ssh-access/${mode}`, {
      method: 'POST',
      headers: { 'X-Step-Up-Token': stepRes.data.step_up_token }
    });
    setBusy(false);
    setStepUpPassword('');

    if (!res.ok || !res.data) {
      toast.error(`Không thể ${mode === 'claim' ? 'nhận' : 'cấp lại'} mật khẩu tạm SSH`, {
        description: res.error
      });
      return;
    }

    setRevealedSecret({
      temporary_password: res.data.temporary_password,
      passphrase: res.data.passphrase,
      notice: res.data.one_time_notice
    });
    toast.success('Đã mở bí mật SSH một lần! Hãy lưu lại ngay.');
    void loadHandover();
  };

  if (loading) {
    return (
      <Card>
        <CardContent className='p-6 text-sm text-muted-foreground'>Đang tải...</CardContent>
      </Card>
    );
  }

  if (!data?.ready) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Trung tâm Bàn giao Quyền Truy cập SSH</CardTitle>
          <CardDescription>
            Trạng thái hiện tại: <Badge variant='outline'>{data?.ssh_access_state || 'NONE'}</Badge>
          </CardDescription>
        </CardHeader>
        <CardContent className='space-y-3 text-sm'>
          <p className='text-muted-foreground'>
            {data?.message || 'Bạn chưa có workspace SSH ở trạng thái VERIFIED_READY.'}
          </p>
          <div className='flex flex-wrap gap-2'>
            <Link href='/dashboard/researcher/ssh-request'>
              <Button>Gửi đơn xin cấp / gia hạn SSH</Button>
            </Link>
            <Link href='/dashboard/researcher/requests'>
              <Button variant='outline'>Xem trạng thái đơn của tôi</Button>
            </Link>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className='space-y-4'>
      <Card className='border-blue-500/30 bg-blue-500/5'>
        <CardHeader className='pb-2'>
          <CardTitle className='text-base'>
            Hướng dẫn Thực hiện Kết nối Máy chủ (Từ cài đặt WireGuard VPN đến VS Code)
          </CardTitle>
          <CardDescription className='text-xs'>
            Thực hiện đúng theo 4 bước dưới đây để kết nối an toàn vào Workspace của bạn trên cụm AI
            Lab.
          </CardDescription>
        </CardHeader>
        <CardContent className='space-y-2.5 text-xs'>
          <div className='grid grid-cols-1 md:grid-cols-2 gap-2.5'>
            <div className='rounded-md border bg-background p-3 space-y-1'>
              <div className='font-semibold text-blue-700 dark:text-blue-300'>
                Bước 1 — Cài đặt &amp; Bật WireGuard VPN
              </div>
              <p className='text-muted-foreground leading-relaxed'>
                1. Cài ứng dụng <strong>WireGuard</strong> trên máy cá nhân (tải từ{' '}
                <span className='font-mono'>wireguard.com/install</span>).
                <br />
                2. Bấm <strong>Tải gói WireGuard + SSH ZIP</strong> ở khung bên dưới rồi giải nén.
                <br />
                3. Mở WireGuard → chọn <strong>Import tunnel(s) from file</strong> → chọn file{' '}
                <span className='font-mono'>.conf</span> vừa giải nén → bấm{' '}
                <strong>Activate</strong>.
              </p>
            </div>

            <div className='rounded-md border bg-background p-3 space-y-1'>
              <div className='font-semibold text-blue-700 dark:text-blue-300'>
                Bước 2 — Mở khóa Mật khẩu SSH Tạm Thời 1 Lần
              </div>
              <p className='text-muted-foreground leading-relaxed'>
                1. Kéo xuống mục <strong>Bước 2</strong> bên dưới.
                <br />
                2. Nhập <strong>Mật khẩu đăng nhập Web</strong> của chính bạn rồi bấm{' '}
                <strong>Nhận Bí mật 1 lần</strong> (hoặc <strong>Cấp lại</strong> nếu cần tạo mật
                khẩu mới).
                <br />
                3. Sao chép và lưu lại <strong>Mật khẩu tạm SSH</strong> ngay (chỉ hiện đúng 1 lần).
              </p>
            </div>

            <div className='rounded-md border bg-background p-3 space-y-1'>
              <div className='font-semibold text-blue-700 dark:text-blue-300'>
                Bước 3 — Kết nối bằng Terminal hoặc VS Code Remote-SSH
              </div>
              <p className='text-muted-foreground leading-relaxed'>
                • <strong>Terminal / PowerShell:</strong> dán lệnh{' '}
                <span className='font-mono'>{data.ssh_command}</span> rồi nhập mật khẩu tạm SSH ở
                Bước 2.
                <br />• <strong>VS Code:</strong> cài extension <strong>Remote - SSH</strong>, sao
                chép cấu hình bên dưới dán vào tệp <span className='font-mono'>~/.ssh/config</span>,
                nhấn <span className='font-mono'>F1</span> →{' '}
                <strong>Remote-SSH: Connect to Host</strong> → chọn{' '}
                <span className='font-mono'>labops-{data.username}</span>.
              </p>
            </div>

            <div className='rounded-md border bg-background p-3 space-y-1'>
              <div className='font-semibold text-blue-700 dark:text-blue-300'>
                Bước 4 — Đổi mật khẩu lần đầu &amp; Lưu dữ liệu đúng chỗ
              </div>
              <p className='text-muted-foreground leading-relaxed'>
                1. Nếu hệ thống yêu cầu đổi mật khẩu khi SSH lần đầu, nhập lại mật khẩu tạm rồi đặt
                mật khẩu mới.
                <br />
                2. Lưu toàn bộ mã nguồn và dataset trong thư mục Workspace được cấp:{' '}
                <span className='font-mono break-all'>{data.workspace_dir}</span>.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className='border-emerald-500/40'>
        <CardHeader>
          <div className='flex flex-wrap items-center justify-between gap-2'>
            <div>
              <CardTitle>
                Bước 1 &amp; 3 — Thông tin Kết nối SSH &amp; Gói WireGuard ({data.assigned_server})
              </CardTitle>
              <CardDescription className='text-xs'>
                Thư mục lưu trữ Workspace: {data.workspace_dir}
              </CardDescription>
            </div>
            <Badge className='bg-emerald-600 text-white'>VERIFIED_READY</Badge>
          </div>
        </CardHeader>
        <CardContent className='space-y-3 text-xs'>
          <div className='grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 font-mono'>
            <div>SSH Host/VPN IP: {data.ssh_host}</div>
            <div>SSH Port: {data.ssh_port}</div>
            <div>
              Tài nguyên: {data.cpu_limit_cores}C / {data.ram_limit_gb}GiB / {data.storage_quota_gb}
              GiB
            </div>
            <div>
              GPU: {data.gpu_permissions?.length ? data.gpu_permissions.join(', ') : 'NONE'}
            </div>
            <div>
              Hết hạn SSH:{' '}
              {data.ssh_expires_at
                ? new Date(data.ssh_expires_at).toLocaleDateString('vi-VN')
                : 'Không giới hạn'}
            </div>
          </div>

          <div className='rounded border bg-muted/30 p-3 space-y-2'>
            <div className='font-semibold'>Lệnh kết nối SSH (Terminal / PowerShell):</div>
            <pre className='overflow-x-auto font-mono text-xs'>{data.ssh_command}</pre>
            <div className='font-semibold pt-2'>Cấu hình VS Code Remote-SSH (~/.ssh/config):</div>
            <pre className='overflow-x-auto font-mono text-xs'>{data.vscode_config}</pre>
            <div className='flex flex-wrap gap-2 pt-2'>
              {data.ssh_command && (
                <Button
                  size='sm'
                  variant='outline'
                  onClick={() => {
                    if (data.ssh_command) {
                      void navigator.clipboard.writeText(data.ssh_command);
                      toast.success('Đã sao chép Lệnh SSH');
                    }
                  }}
                >
                  Sao chép Lệnh SSH
                </Button>
              )}
              <Button
                size='sm'
                variant='outline'
                onClick={() => {
                  if (data.vscode_config) {
                    void navigator.clipboard.writeText(data.vscode_config);
                    toast.success('Đã sao chép cấu hình VS Code Remote-SSH');
                  }
                }}
              >
                Sao chép VS Code Config
              </Button>
              {data.member_id && (
                <a
                  href={`/api/labops/v2/ssh-access/${encodeURIComponent(data.member_id)}/download-zip`}
                  target='_blank'
                  rel='noreferrer'
                >
                  <Button size='sm'>Tải gói WireGuard + SSH ZIP</Button>
                </a>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className='pb-2'>
          <CardTitle className='text-base'>
            Bước 2 — Nhận Mật khẩu SSH Tạm Thời Một Lần (Step-Up Protected)
          </CardTitle>
          <CardDescription className='text-xs'>
            Yêu cầu xác thực lại mật khẩu Web của chính bạn. Bí mật chỉ hiển thị đúng 1 lần duy
            nhất.
          </CardDescription>
        </CardHeader>
        <CardContent className='space-y-3 text-sm'>
          {revealedSecret && (
            <div className='rounded border border-amber-500/40 bg-amber-500/10 p-3 text-xs space-y-1.5 font-mono'>
              <div className='font-semibold text-amber-800 dark:text-amber-200'>
                {revealedSecret.notice}
              </div>
              <div className='flex flex-wrap items-center justify-between gap-2'>
                <div>
                  Mật khẩu tạm SSH:{' '}
                  <strong className='select-all'>{revealedSecret.temporary_password}</strong>
                </div>
                {revealedSecret.temporary_password && (
                  <Button
                    size='sm'
                    variant='outline'
                    onClick={() => {
                      if (revealedSecret.temporary_password) {
                        void navigator.clipboard.writeText(revealedSecret.temporary_password);
                        toast.success('Đã sao chép Mật khẩu tạm SSH');
                      }
                    }}
                  >
                    Sao chép Mật khẩu tạm
                  </Button>
                )}
              </div>
              {revealedSecret.passphrase && (
                <div>
                  Passphrase khóa riêng:{' '}
                  <strong className='select-all'>{revealedSecret.passphrase}</strong>
                </div>
              )}
            </div>
          )}

          <div className='flex flex-wrap items-end gap-2'>
            <div className='space-y-1 flex-1 min-w-[240px]'>
              <Label>Mật khẩu Web hiện tại của bạn *</Label>
              <Input
                type='password'
                value={stepUpPassword}
                onChange={(e) => setStepUpPassword(e.target.value)}
                placeholder='Nhập mật khẩu cổng web để mở khóa bí mật 1 lần'
              />
            </div>
            {!data.secret_already_claimed && data.vault_available ? (
              <Button disabled={busy} onClick={() => void handleClaimOrReissue('claim')}>
                Nhận Bí mật 1 lần
              </Button>
            ) : (
              <Button
                disabled={busy}
                variant='secondary'
                onClick={() => void handleClaimOrReissue('reissue')}
              >
                Cấp lại Mật khẩu Tạm Mới (Reissue)
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
