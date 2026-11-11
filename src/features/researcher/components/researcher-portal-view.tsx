'use client';

import React, { useEffect, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Icons } from '@/components/icons';
import { toast } from 'sonner';
import { labopsCall } from '@/features/labops/api';

interface SshKeyRecord {
  id: string;
  username: string;
  label: string;
  key_type: string;
  fingerprint_sha256: string;
  desired_state: string;
  observed_state: string;
  created_at: string;
}

interface WorkspaceRecord {
  id: string;
  name: string;
  owner: string;
  host_id: string;
  runtime_type: string;
  image_ref: string;
  ssh_host_port?: number;
  vpn_bind_ip?: string;
  cpu_cores_limit: number;
  ram_mb_limit: number;
  pids_limit: number;
  storage_quota_gb?: number;
  lifecycle_state: string;
  observed_state: string;
}

interface ReservationRecord {
  id: string;
  gpu_uuid: string;
  gpu_id?: string;
  host_id?: string;
  job_name: string;
  mode: string;
  state: string;
  runtime_verified: boolean;
  reserved_at: string;
  allocated_hours?: number | null;
  runtime_hours?: number | null;
  idle_hold_hours?: number | null;
}

interface ScanRecord {
  id: string;
  repo_url: string;
  commit_sha: string;
  state: string;
  submitted_at: string;
  disclaimer?: string;
  error?: string | null;
}

interface ResearcherSnapshot {
  username: string;
  workspaces: WorkspaceRecord[];
  ssh_keys: SshKeyRecord[];
  reservations: ReservationRecord[];
  security_scans: ScanRecord[];
  usage: {
    allocated_gpu_hours: number;
    verified_runtime_gpu_hours: number;
    active_reservations: number;
  };
}

export function ResearcherPortalView() {
  const [snapshot, setSnapshot] = useState<ResearcherSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [keyLabel, setKeyLabel] = useState('Laptop Nghiên cứu');
  const [publicKey, setPublicKey] = useState('');
  const [submittingKey, setSubmittingKey] = useState(false);

  const loadSnapshot = async () => {
    setLoading(true);
    const res = await labopsCall<ResearcherSnapshot>('v2/researcher/me');
    if (!res.ok || !res.data) {
      setErrorMsg(res.error || 'Chưa xác thực hoặc không thể tải dữ liệu Cổng nghiên cứu viên');
      setSnapshot(null);
    } else {
      setSnapshot(res.data);
      setErrorMsg(null);
    }
    setLoading(false);
  };

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void loadSnapshot();
  }, []);
  /* oxlint-enable react/set-state-in-effect */

  const handleRegisterKey = async (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (!publicKey.trim()) return;
    setSubmittingKey(true);
    const res = await labopsCall('v2/ssh-keys', {
      method: 'POST',
      body: { label: keyLabel, public_key: publicKey }
    });
    setSubmittingKey(false);
    if (!res.ok) {
      toast.error('Không thể đăng ký khóa SSH công khai', { description: res.error });
      return;
    }
    toast.info('Đã ghi nhận khóa công khai SSH (chờ đồng bộ xuống workspace)', {
      description: 'Trạng thái: PENDING_SYNC. Hệ thống không bao giờ yêu cầu hoặc lưu khóa riêng (private key).'
    });
    setPublicKey('');
    void loadSnapshot();
  };

  return (
    <div className='flex flex-col gap-5'>
      {/* Cảnh báo phạm vi quyền hạn rõ ràng */}
      <Card className='border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/30'>
        <div className='flex items-start justify-between gap-3'>
          <div className='space-y-1 text-xs'>
            <div className='font-bold text-blue-950 dark:text-blue-100'>
              Không gian Làm việc Cá nhân — Cổng Nghiên cứu viên (Phạm vi Cô lập)
            </div>
            <p className='text-blue-900/90 dark:text-blue-200/80'>
              Bạn chỉ nhìn thấy và thao tác trên môi trường làm việc, khóa công khai SSH, lượt giữ chỗ
              GPU và kết quả quét mã nguồn của chính mình. Nghiên cứu viên không có quyền truy cập
              Arcane Admin, Docker socket hay lệnh <code>sudo</code> trên máy chủ vật lý.
            </p>
          </div>
          <Button size='sm' variant='outline' onClick={() => void loadSnapshot()} className='text-xs'>
            Làm mới trạng thái
          </Button>
        </div>
      </Card>

      {errorMsg && (
        <Card className='border-amber-300 bg-amber-50 p-4 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-200'>
          <div className='font-semibold'>Trạng thái kết nối dữ liệu: PENDING / CHƯA XÁC THỰC</div>
          <div className='mt-1'>{errorMsg}</div>
        </Card>
      )}

      {/* Tóm tắt định량 sử dụng */}
      <div className='grid grid-cols-1 gap-4 md:grid-cols-3'>
        <Card className='p-4'>
          <div className='text-xs text-muted-foreground'>Tài khoản nghiên cứu viên</div>
          <div className='mt-1 text-lg font-bold font-mono'>
            {loading ? 'Đang tải...' : snapshot?.username || 'UNKNOWN'}
          </div>
          <div className='mt-1 text-[11px] text-muted-foreground'>
            Xác thực từ phiên máy chủ (không tin tham số từ trình duyệt)
          </div>
        </Card>
        <Card className='p-4'>
          <div className='text-xs text-muted-foreground'>Thời gian giữ chỗ GPU (Allocated)</div>
          <div className='mt-1 text-lg font-bold text-blue-600'>
            {snapshot?.usage.allocated_gpu_hours ?? 0} giờ GPU
          </div>
          <div className='mt-1 text-[11px] text-muted-foreground'>
            Đang giữ hoạt động: {snapshot?.usage.active_reservations ?? 0} GPU (FULL_GPU_EXCLUSIVE)
          </div>
        </Card>
        <Card className='p-4'>
          <div className='text-xs text-muted-foreground'>Thời gian chạy thực đã xác minh (Runtime)</div>
          <div className='mt-1 text-lg font-bold text-emerald-600'>
            {snapshot?.usage.verified_runtime_gpu_hours ?? 0} giờ GPU
          </div>
          <div className='mt-1 text-[11px] text-muted-foreground'>
            Chỉ tính khi Node Agent xác minh tiến trình thực tế trên GPU
          </div>
        </Card>
      </div>

      {/* Hướng dẫn kết nối WireGuard + OpenSSH */}
      <Card>
        <CardHeader className='pb-3'>
          <CardTitle className='text-sm font-bold'>
            1. Hướng dẫn Kết nối An toàn (WireGuard VPN + OpenSSH Public Key)
          </CardTitle>
          <CardDescription className='text-xs'>
            Luồng kết nối tiêu chuẩn V2: Thiết bị đã đăng ký WireGuard → Địa chỉ VPN nội bộ → Cổng
            OpenSSH của Workspace cô lập
          </CardDescription>
        </CardHeader>
        <CardContent className='space-y-3 text-xs'>
          <div className='rounded-md border bg-muted/40 p-3 font-mono text-[11px]'>
            <div># Bước 1: Tạo cặp khóa Ed25519 trên máy cá nhân (tuyệt đối không gửi khóa riêng)</div>
            <div>ssh-keygen -t ed25519 -C &quot;researcher@labops&quot;</div>
            <div className='mt-1.5'># Bước 2: Bật kết nối WireGuard VPN và SSH vào workspace của bạn</div>
            <div>ssh -i ~/.ssh/id_ed25519 -p &lt;CỔNG_WORKSPACE&gt; &lt;USERNAME&gt;@10.200.0.2</div>
          </div>

          <form onSubmit={handleRegisterKey} className='grid gap-3 pt-2 md:grid-cols-3'>
            <div>
              <Label className='text-xs'>Nhãn thiết bị</Label>
              <Input
                value={keyLabel}
                onChange={(e) => setKeyLabel(e.target.value)}
                className='mt-1 h-8 text-xs'
                required
              />
            </div>
            <div className='md:col-span-2'>
              <Label className='text-xs'>Khóa công khai OpenSSH (ssh-ed25519 / rsa-sha2-256)</Label>
              <div className='mt-1 flex gap-2'>
                <Input
                  value={publicKey}
                  onChange={(e) => setPublicKey(e.target.value)}
                  placeholder='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@laptop'
                  className='h-8 font-mono text-xs'
                  required
                />
                <Button type='submit' size='sm' disabled={submittingKey} className='h-8 text-xs'>
                  Đăng ký khóa
                </Button>
              </div>
            </div>
          </form>

          <div className='pt-2'>
            <div className='mb-1.5 font-semibold'>Danh sách khóa công khai SSH của tôi</div>
            {(snapshot?.ssh_keys.length ?? 0) === 0 ? (
              <div className='text-muted-foreground'>Chưa có khóa công khai SSH nào được đăng ký.</div>
            ) : (
              <div className='space-y-1.5'>
                {snapshot?.ssh_keys.map((k) => (
                  <div
                    key={k.id}
                    className='flex items-center justify-between rounded border px-3 py-2 text-xs'
                  >
                    <div>
                      <span className='font-semibold'>{k.label}</span>{' '}
                      <span className='font-mono text-[11px] text-muted-foreground'>
                        ({k.key_type} • {k.fingerprint_sha256})
                      </span>
                    </div>
                    <Badge variant='outline' className='font-mono text-[10px]'>
                      {k.observed_state}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Môi trường làm việc (Workspaces) & Lượt giữ chỗ GPU */}
      <div className='grid grid-cols-1 gap-4 lg:grid-cols-2'>
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-bold'>2. Môi trường Làm việc của Tôi (Workspaces)</CardTitle>
            <CardDescription className='text-xs'>
              Giới hạn CPU/RAM/PIDs rõ ràng; không gắn Docker socket; không cấp quyền root máy chủ
            </CardDescription>
          </CardHeader>
          <CardContent className='space-y-2 text-xs'>
            {(snapshot?.workspaces.length ?? 0) === 0 ? (
              <div className='text-muted-foreground'>
                Chưa có workspace nào được gán cho tài khoản của bạn.
              </div>
            ) : (
              snapshot?.workspaces.map((ws) => (
                <div key={ws.id} className='rounded-lg border p-3 space-y-1'>
                  <div className='flex items-center justify-between'>
                    <span className='font-bold font-mono'>{ws.name}</span>
                    <Badge variant='outline' className='font-mono text-[10px]'>
                      {ws.runtime_type} • {ws.observed_state}
                    </Badge>
                  </div>
                  <div className='text-muted-foreground'>
                    Máy chủ: <strong>{ws.host_id}</strong> • Cổng SSH:{' '}
                    <code>{ws.ssh_host_port ?? 'CHƯA CẤP'}</code> • Giới hạn: {ws.cpu_cores_limit}{' '}
                    vCPU / {Math.round(ws.ram_mb_limit / 1024)} GiB RAM / {ws.pids_limit} PIDs
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-bold'>
              3. Lượt Giữ chỗ GPU &amp; Kết quả Quét Mã nguồn
            </CardTitle>
            <CardDescription className='text-xs'>
              Chế độ độc quyền toàn bộ GPU (FULL_GPU_EXCLUSIVE) theo UUID thực tế
            </CardDescription>
          </CardHeader>
          <CardContent className='space-y-2 text-xs'>
            {(snapshot?.reservations.length ?? 0) === 0 ? (
              <div className='text-muted-foreground'>
                Bạn chưa có lượt giữ chỗ GPU nào đang hoạt động.
              </div>
            ) : (
              snapshot?.reservations.map((r) => (
                <div key={r.id} className='rounded-lg border p-3 space-y-1'>
                  <div className='flex items-center justify-between'>
                    <span className='font-semibold'>{r.job_name || r.id}</span>
                    <Badge variant='outline' className='font-mono text-[10px]'>
                      {r.state} ({r.runtime_verified ? 'VERIFIED' : 'PENDING_VERIFICATION'})
                    </Badge>
                  </div>
                  <div className='font-mono text-[11px] text-muted-foreground'>
                    UUID: {r.gpu_uuid} • Giữ chỗ: {r.allocated_hours ?? 0}h • Chạy thực:{' '}
                    {r.runtime_hours ?? 'Chưa xác minh'}
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

