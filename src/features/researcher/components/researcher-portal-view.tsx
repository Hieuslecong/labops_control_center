'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import { labopsCall } from '@/features/labops/api';
import { Icons } from '@/components/icons';
import { RepositoryGuardRequest } from '@/types/labops';
import { ServerAccessRequestItem } from '@/features/mram/types';

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

interface JobRecord {
  id: string;
  name?: string;
  job_name?: string;
  username: string;
  state?: string;
  gpu_uuid?: string;
  command?: string;
  started_at?: string;
}

interface ResearcherSnapshot {
  username: string;
  member?: {
    id?: string;
    username?: string;
    fullName?: string;
    email?: string;
    vpn_ip?: string;
    assigned_port?: number;
    status?: string;
    createdAt?: string;
  } | null;
  workspaces: WorkspaceRecord[];
  ssh_keys: SshKeyRecord[];
  reservations: ReservationRecord[];
  jobs?: JobRecord[];
  security_scans: ScanRecord[];
  usage: {
    allocated_gpu_hours: number;
    verified_runtime_gpu_hours: number;
    active_reservations: number;
  };
}

interface ResearcherPortalViewProps {
  initialTab?: 'overview' | 'connect' | 'workspace' | 'jobs';
}

export function ResearcherPortalView({ initialTab = 'overview' }: ResearcherPortalViewProps) {
  const [activeTab, setActiveTab] = useState<string>(
    initialTab === 'connect' ? 'overview' : initialTab
  );
  const [snapshot, setSnapshot] = useState<ResearcherSnapshot | null>(null);
  const [repoRequests, setRepoRequests] = useState<RepositoryGuardRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [mustChangeBanner, setMustChangeBanner] = useState(false);
  const [tempPassword, setTempPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [changingPassword, setChangingPassword] = useState(false);
  const [authMe, setAuthMe] = useState<{
    full_name?: string;
    username?: string;
    member_category?: string;
    ssh_access_state?: string;
    must_change_password?: boolean;
  } | null>(null);
  const [myRequests, setMyRequests] = useState<ServerAccessRequestItem[]>([]);

  const loadSnapshot = async () => {
    setLoading(true);
    const [res, repoRes, meRes, reqRes] = await Promise.all([
      labopsCall<ResearcherSnapshot>('v2/researcher/me'),
      labopsCall<RepositoryGuardRequest[]>('repositories/requests'),
      labopsCall<{
        full_name?: string;
        username?: string;
        member_category?: string;
        ssh_access_state?: string;
        must_change_password?: boolean;
      }>('auth/me'),
      labopsCall<{ requests: ServerAccessRequestItem[] }>('me/ssh-requests')
    ]);
    if (meRes.ok && meRes.data) {
      setAuthMe(meRes.data);
      if (meRes.data.must_change_password) {
        setMustChangeBanner(true);
      }
    }
    if (reqRes.ok && Array.isArray(reqRes.data?.requests)) {
      setMyRequests(reqRes.data.requests);
    }
    if (!res.ok || !res.data) {
      setErrorMsg(res.error || 'Chưa xác thực hoặc không thể tải dữ liệu Cổng nghiên cứu viên');
      setSnapshot(null);
    } else {
      setSnapshot(res.data);
      setErrorMsg(null);
    }
    if (repoRes.ok && Array.isArray(repoRes.data)) {
      setRepoRequests(repoRes.data);
    }
    setLoading(false);
  };

  /* oxlint-disable react/set-state-in-effect, react/exhaustive-effect-dependencies */
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      if (params.get('changePassword') === '1') {
        setMustChangeBanner(true);
      }
    }
    void loadSnapshot();
  }, []);

  useEffect(() => {
    if (initialTab) {
      setActiveTab(initialTab === 'connect' ? 'overview' : initialTab);
    }
  }, [initialTab]);
  /* oxlint-enable react/set-state-in-effect, react/exhaustive-effect-dependencies */

  const handleChangePassword = async (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (!tempPassword || !newPassword || !confirmPassword) {
      toast.error('Vui lòng nhập đầy đủ mật khẩu tạm và mật khẩu mới.');
      return;
    }
    if (newPassword.length < 12) {
      toast.error('Mật khẩu mới phải có ít nhất 12 ký tự.');
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error('Xác nhận mật khẩu mới không khớp.');
      return;
    }
    setChangingPassword(true);
    const webRes = await labopsCall('auth/change-web-password', {
      method: 'POST',
      body: {
        current_password: tempPassword,
        new_password: newPassword
      }
    });
    const res = webRes.ok
      ? webRes
      : await labopsCall('v2/ssh-access/first-login-change-password', {
          method: 'POST',
          body: {
            temporary_password: tempPassword,
            new_password: newPassword
          }
        });
    setChangingPassword(false);
    if (!res.ok) {
      toast.error('Không thể đổi mật khẩu', { description: res.error });
      return;
    }
    toast.success('Đổi mật khẩu thành công', {
      description: 'Mật khẩu tạm đã bị thu hồi; hãy dùng mật khẩu mới cho lần đăng nhập tới.'
    });
    setTempPassword('');
    setNewPassword('');
    setConfirmPassword('');
    setMustChangeBanner(false);
    void loadSnapshot();
  };

  // Primary workspace
  const primaryWorkspace = snapshot?.workspaces?.[0];
  const isWorkspaceReady =
    primaryWorkspace?.observed_state === 'RUNNING' && Boolean(primaryWorkspace?.ssh_host_port);

  const username = snapshot?.username || 'researcher';

  const latestReq = myRequests[0] || null;
  const isSimplifiedOnboarding =
    !loading && !isWorkspaceReady && authMe?.ssh_access_state !== 'VERIFIED_READY';

  if (loading && !snapshot && !errorMsg) {
    return (
      <div className='max-w-3xl space-y-4'>
        <Card className='p-6 text-sm text-muted-foreground flex items-center gap-2.5'>
          <Icons.spinner className='size-4 animate-spin text-primary' />
          <span>Đang đồng bộ hồ sơ và trạng thái không gian nghiên cứu của bạn...</span>
        </Card>
      </div>
    );
  }

  if (isSimplifiedOnboarding) {
    return (
      <div className='max-w-3xl space-y-5'>
        <Card>
          <CardHeader>
            <div className='flex flex-wrap items-center justify-between gap-2'>
              <div>
                <CardTitle>
                  Xin chào, {authMe?.full_name || snapshot?.username || 'Nghiên cứu viên'}
                </CardTitle>
                <CardDescription className='text-xs mt-1'>
                  Hoàn thành 3 bước đơn giản dưới đây để bắt đầu sử dụng máy chủ Lab.
                </CardDescription>
              </div>
              <Badge variant='outline'>
                {authMe?.member_category === 'ADMIN' || authMe?.username === 'admin'
                  ? 'Quản trị viên'
                  : authMe?.member_category === 'LECTURER'
                    ? 'Giảng viên'
                    : 'Sinh viên'}
              </Badge>
            </div>
          </CardHeader>
          <CardContent className='space-y-3 text-sm'>
            <div className='rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 flex items-center justify-between'>
              <div>
                <div className='font-semibold'>Bước 1: Tài khoản cổng Web</div>
                <div className='text-xs text-muted-foreground'>
                  Đã kích hoạt (@{authMe?.username || snapshot?.username})
                </div>
              </div>
              <Badge className='bg-emerald-600 text-white'>Đã xong</Badge>
            </div>

            <div className='rounded-lg border p-3 flex flex-wrap items-center justify-between gap-3'>
              <div className='space-y-1 flex-1 min-w-[240px]'>
                <div className='font-semibold'>Bước 2: Gửi đơn xin cấp quyền SSH</div>
                {latestReq ? (
                  <div className='space-y-1.5 text-xs text-muted-foreground'>
                    <div>
                      Đơn <strong>{latestReq.project_title}</strong> ({latestReq.status})
                      {latestReq.supervisor_name ? ` • GVHD: ${latestReq.supervisor_name}` : ''}
                    </div>
                    {latestReq.decision_reason && (
                      <div
                        className={`rounded border px-2.5 py-1.5 text-xs ${
                          latestReq.status === 'REJECTED'
                            ? 'border-rose-500/40 bg-rose-500/10 text-rose-900 dark:text-rose-200'
                            : latestReq.status === 'NEED_INFO'
                              ? 'border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200'
                              : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-900 dark:text-emerald-200'
                        }`}
                      >
                        <strong>Phản hồi từ Admin:</strong> {latestReq.decision_reason}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className='text-xs text-muted-foreground'>
                    Nhập tên giảng viên hướng dẫn, tên đề tài và lý do sử dụng máy chủ.
                  </div>
                )}
              </div>
              {latestReq?.status === 'PENDING_APPROVAL' ? (
                <div className='flex flex-wrap items-center gap-2'>
                  <Badge variant='outline' className='text-amber-700 border-amber-400'>
                    Đang chờ Admin duyệt (SLA 24h)
                  </Badge>
                  <Link href='/dashboard/researcher/requests'>
                    <Button size='sm' variant='outline'>
                      Xem đơn đã nộp ({myRequests.length})
                    </Button>
                  </Link>
                </div>
              ) : (
                <div className='flex flex-wrap items-center gap-2'>
                  {myRequests.length > 0 && (
                    <Link href='/dashboard/researcher/requests'>
                      <Button size='sm' variant='outline'>
                        Xem đơn đã nộp ({myRequests.length})
                      </Button>
                    </Link>
                  )}
                  <Link href='/dashboard/researcher/ssh-request'>
                    <Button size='sm'>{latestReq ? 'Gửi đơn mới' : 'Đăng ký cấp SSH ngay'}</Button>
                  </Link>
                </div>
              )}
            </div>

            <div className='rounded-lg border p-3 flex flex-wrap items-center justify-between gap-3 opacity-80'>
              <div>
                <div className='font-semibold'>Bước 3: Nhận thông tin kết nối SSH</div>
                <div className='text-xs text-muted-foreground'>
                  Sau khi Admin phê duyệt, thông tin SSH và mật khẩu tạm 1 lần sẽ mở tại đây.
                </div>
              </div>
              <Link href='/dashboard/researcher/access'>
                <Button size='sm' variant='outline'>
                  Mở trang Kết nối SSH
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>

        {mustChangeBanner && (
          <Card className='border-amber-500/40 bg-amber-500/5'>
            <CardHeader className='pb-2'>
              <CardTitle className='text-base'>Đổi mật khẩu Web lần đầu</CardTitle>
              <CardDescription className='text-xs'>
                Tài khoản bạn đang dùng mật khẩu tạm do Admin cấp. Hãy đổi sang mật khẩu cá nhân
                (tối thiểu 12 ký tự).
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleChangePassword} className='space-y-3 text-sm'>
                <div className='grid grid-cols-1 sm:grid-cols-3 gap-2'>
                  <Input
                    type='password'
                    placeholder='Mật khẩu hiện tại'
                    value={tempPassword}
                    onChange={(e) => setTempPassword(e.target.value)}
                  />
                  <Input
                    type='password'
                    placeholder='Mật khẩu mới (>= 12 ký tự)'
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                  />
                  <Input
                    type='password'
                    placeholder='Nhập lại mật khẩu mới'
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                  />
                </div>
                <Button type='submit' size='sm' disabled={changingPassword}>
                  {changingPassword ? 'Đang cập nhật...' : 'Cập nhật mật khẩu'}
                </Button>
              </form>
            </CardContent>
          </Card>
        )}
      </div>
    );
  }

  return (
    <div className='flex flex-col gap-5'>
      {/* Cảnh báo phạm vi an toàn & Role banner */}
      <Card className='border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/30'>
        <div className='flex flex-wrap items-center justify-between gap-3'>
          <div className='space-y-1 text-xs'>
            <div className='font-bold text-blue-950 dark:text-blue-100 flex items-center gap-2'>
              <span>Không gian Làm việc Nghiên cứu viên</span>
              <Badge
                variant='outline'
                className='bg-blue-100/80 text-blue-800 border-blue-300 font-mono text-xs'
              >
                {authMe?.member_category === 'ADMIN' || authMe?.username === 'admin'
                  ? 'ROLE: ADMIN'
                  : authMe?.member_category === 'LECTURER'
                    ? 'ROLE: LECTURER'
                    : 'ROLE: RESEARCHER'}
              </Badge>
            </div>
            <p className='text-blue-900/90 dark:text-blue-200/80'>
              Phạm vi cô lập: Bạn chỉ xem và thao tác trên workspace, khóa SSH, GPU và tiến trình
              của chính mình. Quyền root máy chủ vật lý và Docker daemon bị từ chối theo chính sách
              bảo mật Zero-Trust.
            </p>
          </div>
          <Button
            size='sm'
            variant='outline'
            onClick={() => void loadSnapshot()}
            className='text-xs'
            disabled={loading}
          >
            {loading ? 'Đang cập nhật...' : 'Làm mới dữ liệu'}
          </Button>
        </div>
      </Card>

      {mustChangeBanner && (
        <Card className='border-amber-500/40 bg-amber-500/10 p-4 text-xs text-amber-900 dark:text-amber-200 space-y-3'>
          <div>
            <div className='font-semibold flex items-center gap-1.5'>
              <Icons.warning className='size-4 shrink-0 text-amber-600' />
              Yêu cầu đổi mật khẩu lần đầu
            </div>
            <p className='mt-1'>
              Tài khoản của bạn đang sử dụng mật khẩu tạm ban đầu. Vui lòng đổi sang mật khẩu cá
              nhân tối thiểu 12 ký tự ngay bên dưới.
            </p>
          </div>
          <form
            onSubmit={(e) => void handleChangePassword(e)}
            className='grid grid-cols-1 md:grid-cols-4 gap-2'
          >
            <Input
              type='password'
              autoComplete='current-password'
              value={tempPassword}
              onChange={(e) => setTempPassword(e.target.value)}
              placeholder='Mật khẩu tạm / hiện tại'
              className='h-8 text-xs bg-background'
            />
            <Input
              type='password'
              autoComplete='new-password'
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder='Mật khẩu mới (>= 12 ký tự)'
              className='h-8 text-xs bg-background'
            />
            <Input
              type='password'
              autoComplete='new-password'
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder='Nhập lại mật khẩu mới'
              className='h-8 text-xs bg-background'
            />
            <Button type='submit' size='sm' className='h-8 text-xs' disabled={changingPassword}>
              {changingPassword ? 'Đang lưu...' : 'Đổi mật khẩu'}
            </Button>
          </form>
        </Card>
      )}

      {/* MRAM v1.0: Luồng Đăng ký & Bàn giao Quyền SSH */}
      <Card className='border-emerald-500/30 bg-emerald-500/5 p-4 text-xs'>
        <div className='flex flex-wrap items-center justify-between gap-3'>
          <div className='space-y-1'>
            <div className='font-semibold flex items-center gap-2'>
              <span>Quyền Truy cập Máy chủ (MRAM v1.0)</span>
              <Badge variant='outline'>{authMe?.member_category || 'STUDENT'}</Badge>
              <Badge variant='outline'>SSH: {authMe?.ssh_access_state || 'NONE'}</Badge>
            </div>
            <p className='text-muted-foreground'>
              Thành viên đăng nhập cổng Web trước, gửi đơn xin cấp quyền SSH (SLA 24h) và nhận mật
              khẩu SSH tạm thời một lần sau khi Admin phê duyệt &amp; kiểm chứng runtime.
            </p>
          </div>
          <div className='flex flex-wrap gap-2'>
            <Link href='/dashboard/researcher/ssh-request'>
              <Button size='sm'>Xin Cấp SSH</Button>
            </Link>
            <Link href='/dashboard/researcher/requests'>
              <Button size='sm' variant='outline'>
                Đơn đã nộp ({myRequests.length})
              </Button>
            </Link>
            <Link href='/dashboard/researcher/access'>
              <Button size='sm' variant='secondary'>
                Bàn giao &amp; Bí mật SSH
              </Button>
            </Link>
          </div>
        </div>
      </Card>

      {errorMsg && (
        <Card className='border-amber-300 bg-amber-50 p-4 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-200'>
          <div className='font-semibold'>Trạng thái phiên làm việc: CẦN XÁC MINH</div>
          <div className='mt-1'>{errorMsg}</div>
        </Card>
      )}

      {/* Tabs điều hướng chính */}
      <Tabs value={activeTab} onValueChange={(val) => setActiveTab(val)}>
        <TabsList className='grid grid-cols-1 md:grid-cols-3 w-full h-auto p-1'>
          <TabsTrigger value='overview' className='py-2 text-xs font-medium'>
            1. Tổng quan của tôi
          </TabsTrigger>
          <TabsTrigger value='workspace' className='py-2 text-xs font-medium'>
            2. Workspace của tôi
          </TabsTrigger>
          <TabsTrigger value='jobs' className='py-2 text-xs font-medium'>
            3. GPU &amp; Tác vụ của tôi
          </TabsTrigger>
        </TabsList>

        {/* ============================================================== */}
        {/* TAB 1: TỔNG QUAN CỦA TÔI                                      */}
        {/* ============================================================== */}
        <TabsContent value='overview' className='mt-4 space-y-4'>
          <div className='grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4'>
            {/* Card 1: Workspace */}
            <Card className='p-4 border-l-4 border-l-primary'>
              <div className='text-xs text-muted-foreground'>Workspace cá nhân</div>
              <div className='mt-1 text-base font-bold font-mono truncate'>
                {primaryWorkspace?.name || (loading ? 'Đang tải...' : 'Chưa cấp')}
              </div>
              <div className='mt-2 flex items-center gap-1.5'>
                <Badge
                  variant='outline'
                  className={`text-xs font-mono ${
                    primaryWorkspace?.observed_state === 'RUNNING'
                      ? 'bg-emerald-500/10 text-emerald-700 border-emerald-300'
                      : 'bg-amber-500/10 text-amber-800 border-amber-300'
                  }`}
                >
                  {primaryWorkspace?.observed_state || 'NOT_PROVISIONED'}
                </Badge>
                {primaryWorkspace?.ssh_host_port && (
                  <span className='text-xs text-muted-foreground'>
                    Port: <strong>{primaryWorkspace.ssh_host_port}</strong>
                  </span>
                )}
              </div>
            </Card>

            {/* Card 2: VPN & SSH */}
            <Card className='p-4 border-l-4 border-l-blue-500'>
              <div className='text-xs text-muted-foreground'>Khóa SSH &amp; VPN</div>
              <div className='mt-1 text-base font-bold'>
                {snapshot?.ssh_keys.length ?? 0} khóa công khai
              </div>
              <div className='mt-2 text-xs text-muted-foreground flex items-center justify-between'>
                <span>VPN IP:</span>
                <code className='font-mono font-bold text-foreground'>
                  {snapshot?.member?.vpn_ip || primaryWorkspace?.vpn_bind_ip || 'Chưa gán IP'}
                </code>
              </div>
            </Card>

            {/* Card 3: GPU Allocations */}
            <Card className='p-4 border-l-4 border-l-emerald-500'>
              <div className='text-xs text-muted-foreground'>GPU-Hours tích lũy</div>
              <div className='mt-1 text-base font-bold text-emerald-600'>
                {snapshot?.usage.verified_runtime_gpu_hours ?? 0} giờ chạy thực
              </div>
              <div className='mt-2 text-xs text-muted-foreground'>
                Đã giữ chỗ: <strong>{snapshot?.usage.allocated_gpu_hours ?? 0}h</strong> (
                {snapshot?.usage.active_reservations ?? 0} GPU active)
              </div>
            </Card>

            {/* Card 4: Tác vụ & Jobs */}
            <Card className='p-4 border-l-4 border-l-purple-500'>
              <div className='text-xs text-muted-foreground'>Tác vụ &amp; Quét an ninh</div>
              <div className='mt-1 text-base font-bold'>
                {(snapshot?.jobs?.length ?? 0) + (snapshot?.reservations?.length ?? 0)} tác vụ
              </div>
              <div className='mt-2 text-xs text-muted-foreground'>
                Kiểm duyệt kho mã:{' '}
                {repoRequests.length > 0
                  ? `${repoRequests.length} yêu cầu`
                  : `${snapshot?.security_scans.length ?? 0} lượt`}
              </div>
            </Card>
          </div>

          {/* Quick Connection Action Card */}
          <Card className='p-5'>
            <div className='flex flex-wrap items-center justify-between gap-4'>
              <div className='space-y-1'>
                <h3 className='text-sm font-bold'>Sẵn sàng bắt đầu nghiên cứu?</h3>
                <p className='text-xs text-muted-foreground max-w-xl'>
                  Kết nối vào môi trường huấn luyện thông qua WireGuard VPN và khóa SSH Ed25519 cá
                  nhân. Dữ liệu được lưu trữ tự động trên máy chủ tại thư mục riêng{' '}
                  <code>/media/LabOps-Data/{username}</code>.
                </p>
              </div>
              <div className='flex gap-2'>
                <Link href='/dashboard/researcher/access'>
                  <Button size='sm' className='text-xs'>
                    Mở Trung tâm Bàn giao SSH &amp; VPN
                  </Button>
                </Link>
                <Button
                  size='sm'
                  variant='outline'
                  onClick={() => setActiveTab('workspace')}
                  className='text-xs'
                >
                  Chi tiết Workspace
                </Button>
              </div>
            </div>
          </Card>
        </TabsContent>

        {/* ============================================================== */}
        {/* TAB 2: WORKSPACE CỦA TÔI                                      */}
        {/* ============================================================== */}
        <TabsContent value='workspace' className='mt-4 space-y-4'>
          <div className='grid grid-cols-1 gap-4 lg:grid-cols-2'>
            {/* Thông số Container */}
            <Card>
              <CardHeader className='pb-3'>
                <CardTitle className='text-sm font-bold'>Thông số Môi trường Workspace</CardTitle>
                <CardDescription className='text-xs'>
                  Container Docker cô lập với cgroups v2, không quyền root máy chủ
                </CardDescription>
              </CardHeader>
              <CardContent className='space-y-3 text-xs'>
                {primaryWorkspace ? (
                  <div className='space-y-2'>
                    <div className='flex items-center justify-between border-b pb-2'>
                      <span className='text-muted-foreground'>Tên workspace:</span>
                      <span className='font-mono font-bold'>{primaryWorkspace.name}</span>
                    </div>
                    <div className='flex items-center justify-between border-b pb-2'>
                      <span className='text-muted-foreground'>Máy chủ vật lý:</span>
                      <span className='font-mono font-semibold'>{primaryWorkspace.host_id}</span>
                    </div>
                    <div className='flex items-center justify-between border-b pb-2'>
                      <span className='text-muted-foreground'>Trạng thái vận hành:</span>
                      <Badge variant='outline' className='font-mono text-xs'>
                        {primaryWorkspace.observed_state}
                      </Badge>
                    </div>
                    <div className='flex items-center justify-between border-b pb-2'>
                      <span className='text-muted-foreground'>Cổng SSH mở:</span>
                      <span className='font-mono font-bold text-foreground'>
                        {primaryWorkspace.ssh_host_port ?? 'Chưa cấp'}
                      </span>
                    </div>
                    <div className='flex items-center justify-between border-b pb-2'>
                      <span className='text-muted-foreground'>Giới hạn vCPU:</span>
                      <span className='font-semibold'>{primaryWorkspace.cpu_cores_limit} vCPU</span>
                    </div>
                    <div className='flex items-center justify-between border-b pb-2'>
                      <span className='text-muted-foreground'>Giới hạn RAM:</span>
                      <span className='font-semibold'>
                        {Math.round(primaryWorkspace.ram_mb_limit / 1024)} GiB
                      </span>
                    </div>
                    <div className='flex items-center justify-between'>
                      <span className='text-muted-foreground'>Giới hạn tiến trình (PIDs):</span>
                      <span className='font-semibold'>{primaryWorkspace.pids_limit} pids</span>
                    </div>
                  </div>
                ) : (
                  <div className='text-center py-6 text-muted-foreground'>
                    Chưa có workspace nào được liên kết với tài khoản này.
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Lưu trữ & An toàn Dữ liệu */}
            <Card>
              <CardHeader className='pb-3'>
                <CardTitle className='text-sm font-bold'>Lưu trữ &amp; Phân vùng Dữ liệu</CardTitle>
                <CardDescription className='text-xs'>
                  Dữ liệu bền vững được lưu trên ổ đĩa chuyên dụng của máy chủ
                </CardDescription>
              </CardHeader>
              <CardContent className='space-y-3 text-xs'>
                <div className='rounded-md border bg-muted/20 p-3 space-y-1.5'>
                  <div className='text-muted-foreground text-xs'>
                    Đường dẫn thư mục cá nhân trên máy chủ:
                  </div>
                  <div className='font-mono font-bold text-xs select-all text-foreground'>
                    /media/LabOps-Data/{username}
                  </div>
                  <p className='text-xs text-muted-foreground pt-1'>
                    Thư mục này được mount trực tiếp vào container của bạn. Toàn bộ code,
                    checkpoints, models và datasets đặt tại đây sẽ không bị mất khi container khởi
                    động lại.
                  </p>
                </div>

                <div className='space-y-2 pt-2'>
                  <div className='flex items-center justify-between border-b pb-2'>
                    <span className='text-muted-foreground'>Hạn mức lưu trữ (Quota):</span>
                    <span className='font-semibold'>
                      {primaryWorkspace?.storage_quota_gb
                        ? `${primaryWorkspace.storage_quota_gb} GB`
                        : 'Chưa giới hạn'}
                    </span>
                  </div>
                  <div className='flex items-center justify-between border-b pb-2'>
                    <span className='text-muted-foreground'>Quyền Docker Socket:</span>
                    <Badge
                      variant='outline'
                      className='text-rose-600 bg-rose-500/10 border-rose-300 text-xs'
                    >
                      DENIED (Không gắn docker.sock)
                    </Badge>
                  </div>
                  <div className='flex items-center justify-between'>
                    <span className='text-muted-foreground'>Quyền Sudo Máy chủ:</span>
                    <Badge
                      variant='outline'
                      className='text-rose-600 bg-rose-500/10 border-rose-300 text-xs'
                    >
                      DENIED (Không có quyền sudo root)
                    </Badge>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Kiểm duyệt và Tải Kho Mã Nguồn An Toàn (Repository Security Gateway) */}
          <Card>
            <CardHeader className='pb-3'>
              <div className='flex flex-wrap items-center justify-between gap-2'>
                <div>
                  <CardTitle className='text-sm font-bold flex items-center gap-2'>
                    <Icons.shield className='size-4 text-emerald-600' /> Tải Kho Mã An Toàn
                    (Repository Security Gateway)
                  </CardTitle>
                  <CardDescription className='text-xs'>
                    Mã nguồn từ GitHub/GitLab được tự động quét mã độc, lộ bí mật và CVE trước khi
                    tải vào workspace
                  </CardDescription>
                </div>
                <Badge
                  variant='outline'
                  className='text-xs font-mono border-emerald-300 text-emerald-700 bg-emerald-500/10'
                >
                  Kiểm duyệt Cách ly (Repo Guard)
                </Badge>
              </div>
            </CardHeader>
            <CardContent className='space-y-4 text-xs'>
              <div className='rounded-md border bg-muted/30 p-3 space-y-2'>
                <div className='font-semibold text-foreground text-xs'>
                  Cách tải kho mã vào thư mục làm việc bằng CLI:
                </div>
                <div className='rounded bg-black p-2.5 font-mono text-xs text-emerald-400 select-all'>
                  gitaiot clone https://github.com/organization/your-repo --dest
                  /workspace/work/your-repo
                </div>
                <p className='text-xs text-muted-foreground'>
                  Hệ thống sẽ tự động quét bằng Trivy scanner, xác minh SHA-256 digest và tự động
                  phê duyệt nếu an toàn.
                </p>
              </div>

              {repoRequests.length > 0 ? (
                <div className='space-y-2 pt-1'>
                  <div className='font-semibold text-xs text-foreground'>
                    Yêu cầu kiểm duyệt của bạn ({repoRequests.length}):
                  </div>
                  <div className='divide-y rounded-md border'>
                    {repoRequests.map((req) => (
                      <div
                        key={req.id}
                        className='flex flex-wrap items-center justify-between p-2.5 gap-2 hover:bg-muted/30'
                      >
                        <div className='space-y-0.5 min-w-0'>
                          <div className='font-mono font-medium text-xs truncate max-w-md'>
                            {req.repo_url}{' '}
                            <span className='text-muted-foreground'>
                              ({req.requested_ref || 'HEAD'})
                            </span>
                          </div>
                          <div className='text-xs text-muted-foreground font-mono'>
                            Commit:{' '}
                            {req.commit_sha ? req.commit_sha.substring(0, 10) : 'Đang phân giải'} •{' '}
                            {new Date(req.submitted_at).toLocaleString('vi-VN')}
                          </div>
                        </div>
                        <div className='flex items-center gap-2 shrink-0'>
                          <Badge
                            variant='outline'
                            className={`text-xs font-mono ${
                              req.state === 'AUTO_APPROVED' ||
                              req.state === 'MANUALLY_APPROVED' ||
                              req.state === 'DELIVERED'
                                ? 'bg-emerald-500/10 text-emerald-700 border-emerald-300'
                                : req.state === 'REVIEW_REQUIRED'
                                  ? 'bg-amber-500/10 text-amber-700 border-amber-300'
                                  : req.state === 'REJECTED' || req.state === 'FAILED'
                                    ? 'bg-rose-500/10 text-rose-700 border-rose-300'
                                    : 'bg-muted text-muted-foreground'
                            }`}
                          >
                            {req.state}
                          </Badge>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <p className='text-xs text-muted-foreground'>
                  Bạn chưa có yêu cầu kiểm duyệt kho mã nào. Sử dụng lệnh <code>gitaiot clone</code>{' '}
                  trong workspace để tải kho mã an toàn.
                </p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ============================================================== */}
        {/* TAB 4: GPU & TÁC VỤ CỦA TÔI                                    */}
        {/* ============================================================== */}
        <TabsContent value='jobs' className='mt-4 space-y-4'>
          {/* Lượt giữ chỗ GPU độc quyền */}
          <Card>
            <CardHeader className='pb-3'>
              <CardTitle className='text-sm font-bold'>
                Lượt Giữ chỗ GPU Độc quyền (FULL_GPU_EXCLUSIVE)
              </CardTitle>
              <CardDescription className='text-xs'>
                Gán trực tiếp theo UUID thiết bị GPU NVIDIA A30 thực tế; đảm bảo không xung đột bộ
                nhớ VRAM với thành viên khác
              </CardDescription>
            </CardHeader>
            <CardContent className='space-y-3 text-xs'>
              {(snapshot?.reservations.length ?? 0) === 0 ? (
                <div className='text-center py-6 text-muted-foreground'>
                  Bạn chưa có lượt giữ chỗ GPU nào đang kích hoạt.
                </div>
              ) : (
                snapshot?.reservations.map((r) => (
                  <div key={r.id} className='rounded-lg border p-3 space-y-2 bg-card'>
                    <div className='flex flex-wrap items-center justify-between gap-2'>
                      <div className='font-bold text-sm'>{r.job_name || r.id}</div>
                      <div className='flex items-center gap-1.5'>
                        <Badge variant='outline' className='font-mono text-xs'>
                          {r.state}
                        </Badge>
                        <Badge
                          variant='outline'
                          className={`font-mono text-xs ${
                            r.runtime_verified
                              ? 'bg-emerald-500/10 text-emerald-700 border-emerald-300'
                              : 'bg-amber-500/10 text-amber-800 border-amber-300'
                          }`}
                        >
                          {r.runtime_verified ? 'VERIFIED' : 'PENDING_VERIFICATION'}
                        </Badge>
                      </div>
                    </div>
                    <div className='grid grid-cols-1 md:grid-cols-3 gap-2 font-mono text-xs text-muted-foreground pt-1 border-t'>
                      <div>
                        GPU UUID: <span className='text-foreground'>{r.gpu_uuid}</span>
                      </div>
                      <div>
                        Giữ chỗ:{' '}
                        <strong className='text-foreground'>{r.allocated_hours ?? 0}h</strong>
                      </div>
                      <div>
                        Chạy thực:{' '}
                        <strong className='text-emerald-600'>
                          {r.runtime_hours ?? 'Chưa xác minh'}h
                        </strong>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          {/* Kế toán GPU-Hours */}
          <Card>
            <CardHeader className='pb-3'>
              <CardTitle className='text-sm font-bold'>
                Bảng Phân tích GPU-Hours Trung thực
              </CardTitle>
              <CardDescription className='text-xs'>
                Tách biệt rõ ràng giữa thời gian giữ chỗ tài nguyên và thời gian huấn luyện thực tế
              </CardDescription>
            </CardHeader>
            <CardContent className='text-xs space-y-3'>
              <div className='grid grid-cols-1 md:grid-cols-3 gap-3'>
                <div className='rounded-md border p-3 bg-muted/20'>
                  <div className='text-muted-foreground text-xs'>
                    Thời gian giữ chỗ (Allocated Hours)
                  </div>
                  <div className='mt-1 text-lg font-bold text-foreground'>
                    {snapshot?.usage.allocated_gpu_hours ?? 0} h
                  </div>
                  <div className='mt-1 text-xs text-muted-foreground'>
                    Tổng thời gian từ lúc cấp slot GPU đến lúc giải phóng
                  </div>
                </div>

                <div className='rounded-md border p-3 bg-emerald-500/10 border-emerald-500/30'>
                  <div className='text-emerald-800 dark:text-emerald-200 text-xs'>
                    Thời gian chạy thực (Runtime Hours)
                  </div>
                  <div className='mt-1 text-lg font-bold text-emerald-700 dark:text-emerald-300'>
                    {snapshot?.usage.verified_runtime_gpu_hours ?? 0} h
                  </div>
                  <div className='mt-1 text-xs text-muted-foreground'>
                    Được Node Agent và NVML xác minh có tiến trình tính toán
                  </div>
                </div>

                <div className='rounded-md border p-3 bg-muted/20'>
                  <div className='text-muted-foreground text-xs'>
                    Thời gian nhàn rỗi (Idle Hold Hours)
                  </div>
                  <div className='mt-1 text-lg font-bold text-amber-600'>
                    {Math.max(
                      0,
                      Math.round(
                        ((snapshot?.usage.allocated_gpu_hours ?? 0) -
                          (snapshot?.usage.verified_runtime_gpu_hours ?? 0)) *
                          100
                      ) / 100
                    )}{' '}
                    h
                  </div>
                  <div className='mt-1 text-xs text-muted-foreground'>
                    Giữ chỗ nhưng không phát sinh tải tính toán trên GPU
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
