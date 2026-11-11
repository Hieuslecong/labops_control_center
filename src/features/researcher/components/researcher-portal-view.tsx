'use client';

import React, { useEffect, useState } from 'react';
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
  const [activeTab, setActiveTab] = useState<string>(initialTab);
  const [snapshot, setSnapshot] = useState<ResearcherSnapshot | null>(null);
  const [repoRequests, setRepoRequests] = useState<RepositoryGuardRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [keyLabel, setKeyLabel] = useState('Laptop Nghiên cứu');
  const [publicKey, setPublicKey] = useState('');
  const [submittingKey, setSubmittingKey] = useState(false);

  const loadSnapshot = async () => {
    setLoading(true);
    const [res, repoRes] = await Promise.all([
      labopsCall<ResearcherSnapshot>('v2/researcher/me'),
      labopsCall<RepositoryGuardRequest[]>('repositories/requests')
    ]);
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
    void loadSnapshot();
  }, []);

  useEffect(() => {
    if (initialTab) {
      setActiveTab(initialTab);
    }
  }, [initialTab]);
  /* oxlint-enable react/set-state-in-effect, react/exhaustive-effect-dependencies */

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
    toast.success('Đã ghi nhận khóa công khai SSH thành công', {
      description: 'Trạng thái: PENDING_SYNC. Hệ thống không bao giờ lưu trữ Private Key.'
    });
    setPublicKey('');
    void loadSnapshot();
  };

  // Primary workspace
  const primaryWorkspace = snapshot?.workspaces?.[0];
  const isWorkspaceReady =
    primaryWorkspace?.observed_state === 'RUNNING' && Boolean(primaryWorkspace?.ssh_host_port);

  // Dynamic SSH command
  const sshHost = primaryWorkspace?.vpn_bind_ip || snapshot?.member?.vpn_ip || '192.168.167.250';
  const sshPort = primaryWorkspace?.ssh_host_port || snapshot?.member?.assigned_port;
  const username = snapshot?.username || 'researcher';

  const dynamicSshCommand = isWorkspaceReady && sshPort
    ? `ssh -i ~/.ssh/id_ed25519 -p ${sshPort} ${username}@${sshHost}`
    : '';

  const vscodeRemoteSnippet = isWorkspaceReady && sshPort
    ? `Host labops-${username}
    HostName ${sshHost}
    Port ${sshPort}
    User ${username}
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60`
    : '';

  const copyToClipboard = (text: string, label: string) => {
    void navigator.clipboard.writeText(text);
    toast.success(`Đã sao chép ${label} vào bộ nhớ tạm!`);
  };

  return (
    <div className='flex flex-col gap-5'>
      {/* Cảnh báo phạm vi an toàn & Role banner */}
      <Card className='border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/30'>
        <div className='flex flex-wrap items-center justify-between gap-3'>
          <div className='space-y-1 text-xs'>
            <div className='font-bold text-blue-950 dark:text-blue-100 flex items-center gap-2'>
              <span>Không gian Làm việc Nghiên cứu viên</span>
              <Badge variant='outline' className='bg-blue-100/80 text-blue-800 border-blue-300 font-mono text-[10px]'>
                ROLE: RESEARCHER
              </Badge>
            </div>
            <p className='text-blue-900/90 dark:text-blue-200/80'>
              Phạm vi cô lập: Bạn chỉ xem và thao tác trên workspace, khóa SSH, GPU và tiến trình của chính mình.
              Quyền root máy chủ vật lý và Docker daemon bị từ chối theo chính sách bảo mật Zero-Trust.
            </p>
          </div>
          <Button size='sm' variant='outline' onClick={() => void loadSnapshot()} className='text-xs' disabled={loading}>
            {loading ? 'Đang cập nhật...' : 'Làm mới dữ liệu'}
          </Button>
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
        <TabsList className='grid grid-cols-2 md:grid-cols-4 w-full h-auto p-1'>
          <TabsTrigger value='overview' className='py-2 text-xs font-medium'>
            1. Tổng quan của tôi
          </TabsTrigger>
          <TabsTrigger value='connect' className='py-2 text-xs font-medium'>
            2. Kết nối &amp; Bảo mật
          </TabsTrigger>
          <TabsTrigger value='workspace' className='py-2 text-xs font-medium'>
            3. Workspace của tôi
          </TabsTrigger>
          <TabsTrigger value='jobs' className='py-2 text-xs font-medium'>
            4. GPU &amp; Tác vụ của tôi
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
                  className={`text-[10px] font-mono ${
                    primaryWorkspace?.observed_state === 'RUNNING'
                      ? 'bg-emerald-500/10 text-emerald-700 border-emerald-300'
                      : 'bg-amber-500/10 text-amber-800 border-amber-300'
                  }`}
                >
                  {primaryWorkspace?.observed_state || 'NOT_PROVISIONED'}
                </Badge>
                {primaryWorkspace?.ssh_host_port && (
                  <span className='text-[11px] text-muted-foreground'>
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
              <div className='mt-2 text-[11px] text-muted-foreground flex items-center justify-between'>
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
              <div className='mt-2 text-[11px] text-muted-foreground'>
                Đã giữ chỗ: <strong>{snapshot?.usage.allocated_gpu_hours ?? 0}h</strong> ({snapshot?.usage.active_reservations ?? 0} GPU active)
              </div>
            </Card>

            {/* Card 4: Tác vụ & Jobs */}
            <Card className='p-4 border-l-4 border-l-purple-500'>
              <div className='text-xs text-muted-foreground'>Tác vụ &amp; Quét an ninh</div>
              <div className='mt-1 text-base font-bold'>
                {(snapshot?.jobs?.length ?? 0) + (snapshot?.reservations?.length ?? 0)} tác vụ
              </div>
              <div className='mt-2 text-[11px] text-muted-foreground'>
                Kiểm duyệt kho mã: {repoRequests.length > 0 ? `${repoRequests.length} yêu cầu` : `${snapshot?.security_scans.length ?? 0} lượt`}
              </div>
            </Card>
          </div>

          {/* Quick Connection Action Card */}
          <Card className='p-5'>
            <div className='flex flex-wrap items-center justify-between gap-4'>
              <div className='space-y-1'>
                <h3 className='text-sm font-bold'>Sẵn sàng bắt đầu nghiên cứu?</h3>
                <p className='text-xs text-muted-foreground max-w-xl'>
                  Kết nối vào môi trường huấn luyện thông qua WireGuard VPN và khóa SSH Ed25519 cá nhân.
                  Dữ liệu được lưu trữ tự động trên máy chủ tại thư mục riêng <code>/media/LabOps-Data/{username}</code>.
                </p>
              </div>
              <div className='flex gap-2'>
                <Button size='sm' onClick={() => setActiveTab('connect')} className='text-xs'>
                  Xem lệnh kết nối SSH
                </Button>
                <Button size='sm' variant='outline' onClick={() => setActiveTab('workspace')} className='text-xs'>
                  Chi tiết Workspace
                </Button>
              </div>
            </div>
          </Card>
        </TabsContent>

        {/* ============================================================== */}
        {/* TAB 2: KẾT NỐI & BẢO MẬT (5-STEP CONNECTION FLOW)             */}
        {/* ============================================================== */}
        <TabsContent value='connect' className='mt-4 space-y-4'>
          <Card>
            <CardHeader className='pb-3'>
              <CardTitle className='text-sm font-bold'>
                Quy trình 5 Bước Kết nối An toàn vào Workspace
              </CardTitle>
              <CardDescription className='text-xs'>
                Tuân thủ mô hình bảo mật Zero-Trust: Thiết bị cá nhân → WireGuard VPN nội bộ → Cổng SSH cô lập → Container riêng
              </CardDescription>
            </CardHeader>
            <CardContent className='space-y-5 text-xs'>
              {/* BƯỚC 1: WIREGUARD VPN CONFIG */}
              <div className='rounded-lg border p-4 space-y-2 bg-muted/20'>
                <div className='flex items-center gap-2'>
                  <span className='flex h-6 w-6 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground'>
                    1
                  </span>
                  <span className='font-bold text-xs'>Cấu hình WireGuard VPN (.conf)</span>
                </div>
                <p className='text-muted-foreground pl-8 text-[11px]'>
                  Cài đặt WireGuard client trên laptop (macOS/Windows/Linux) và import file cấu hình do Quản trị viên cấp.
                  VPN kết nối thẳng vào mạng nội bộ của LabOps mà không mở cổng ra Internet công cộng.
                </p>
              </div>

              {/* BƯỚC 2: BẬT KẾT NỐI VPN */}
              <div className='rounded-lg border p-4 space-y-2 bg-muted/20'>
                <div className='flex items-center gap-2'>
                  <span className='flex h-6 w-6 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground'>
                    2
                  </span>
                  <span className='font-bold text-xs'>Kích hoạt WireGuard VPN trên máy cá nhân</span>
                </div>
                <div className='pl-8 space-y-1 text-[11px] text-muted-foreground'>
                  <p>
                    Bật công tắc WireGuard client. Địa chỉ IP nội bộ của bạn trong mạng LabOps:{' '}
                    <strong className='font-mono text-foreground'>{snapshot?.member?.vpn_ip || '10.88.40.x'}</strong>.
                  </p>
                </div>
              </div>

              {/* BƯỚC 3: ĐĂNG KÝ SSH PUBLIC KEY */}
              <div className='rounded-lg border p-4 space-y-3 bg-muted/20'>
                <div className='flex items-center gap-2'>
                  <span className='flex h-6 w-6 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground'>
                    3
                  </span>
                  <span className='font-bold text-xs'>Đăng ký Khóa công khai OpenSSH (Ed25519)</span>
                </div>
                <p className='text-muted-foreground pl-8 text-[11px]'>
                  Tạo cặp khóa trên máy cá nhân: <code>ssh-keygen -t ed25519 -C &quot;{username}@labops&quot;</code>.
                  Tuyệt đối <strong>không gửi Private Key</strong>. Chỉ đăng ký Public Key <code>id_ed25519.pub</code> bên dưới:
                </p>

                <form onSubmit={handleRegisterKey} className='pl-8 grid gap-3 md:grid-cols-3 pt-1'>
                  <div>
                    <Label className='text-xs'>Nhãn thiết bị</Label>
                    <Input
                      value={keyLabel}
                      onChange={(e) => setKeyLabel(e.target.value)}
                      placeholder='VD: MacBook Pro M3'
                      className='mt-1 h-8 text-xs'
                      required
                    />
                  </div>
                  <div className='md:col-span-2'>
                    <Label className='text-xs'>Khóa công khai OpenSSH (ssh-ed25519 AAAAC3N...)</Label>
                    <div className='mt-1 flex gap-2'>
                      <Input
                        value={publicKey}
                        onChange={(e) => setPublicKey(e.target.value)}
                        placeholder='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@machine'
                        className='h-8 font-mono text-xs'
                        required
                      />
                      <Button type='submit' size='sm' disabled={submittingKey} className='h-8 text-xs'>
                        Đăng ký
                      </Button>
                    </div>
                  </div>
                </form>

                {/* Danh sách khóa hiện có */}
                <div className='pl-8 pt-2'>
                  <div className='text-[11px] font-semibold mb-1'>Khóa công khai đã đăng ký:</div>
                  {(snapshot?.ssh_keys.length ?? 0) === 0 ? (
                    <div className='text-[11px] text-muted-foreground'>Chưa có khóa nào được đăng ký.</div>
                  ) : (
                    <div className='space-y-1.5'>
                      {snapshot?.ssh_keys.map((k) => (
                        <div key={k.id} className='flex items-center justify-between rounded border bg-card px-3 py-1.5 text-xs'>
                          <div>
                            <span className='font-semibold'>{k.label}</span>{' '}
                            <span className='font-mono text-[10px] text-muted-foreground'>({k.fingerprint_sha256})</span>
                          </div>
                          <Badge variant='outline' className='font-mono text-[10px]'>
                            {k.observed_state}
                          </Badge>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* BƯỚC 4: KIỂM TRA TRẠNG THÁI WORKSPACE */}
              <div className='rounded-lg border p-4 space-y-2 bg-muted/20'>
                <div className='flex items-center gap-2'>
                  <span className='flex h-6 w-6 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground'>
                    4
                  </span>
                  <span className='font-bold text-xs'>Kiểm tra trạng thái Workspace cá nhân</span>
                </div>
                <div className='pl-8 flex items-center gap-3 text-[11px]'>
                  <span>Trạng thái:</span>
                  <Badge
                    variant='outline'
                    className={`font-mono text-[10px] ${
                      isWorkspaceReady
                        ? 'bg-emerald-500/10 text-emerald-700 border-emerald-300'
                        : 'bg-amber-500/10 text-amber-800 border-amber-300'
                    }`}
                  >
                    {primaryWorkspace?.observed_state || 'NOT_PROVISIONED'}
                  </Badge>
                  {isWorkspaceReady ? (
                    <span className='text-emerald-700 dark:text-emerald-300 font-semibold'>
                      ✓ Sẵn sàng nhận kết nối trên cổng {sshPort}
                    </span>
                  ) : (
                    <span className='text-amber-700 dark:text-amber-300'>
                      Workspace chưa hoạt động. Hãy yêu cầu Quản trị viên kích hoạt.
                    </span>
                  )}
                </div>
              </div>

              {/* BƯỚC 5: LỆNH SSH & VSCODE REMOTE */}
              <div className='rounded-lg border p-4 space-y-3 bg-muted/20'>
                <div className='flex items-center gap-2'>
                  <span className='flex h-6 w-6 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground'>
                    5
                  </span>
                  <span className='font-bold text-xs'>Lệnh kết nối SSH cá nhân hóa (Tạo động)</span>
                </div>

                <div className='pl-8 space-y-3'>
                  {isWorkspaceReady ? (
                    <>
                      {/* Lệnh Terminal */}
                      <div>
                        <div className='flex items-center justify-between mb-1'>
                          <span className='text-[11px] font-semibold text-muted-foreground'>Lệnh SSH Terminal:</span>
                          <Button
                            size='sm'
                            variant='outline'
                            className='h-7 text-[11px]'
                            onClick={() => copyToClipboard(dynamicSshCommand, 'Lệnh SSH')}
                          >
                            Sao chép lệnh
                          </Button>
                        </div>
                        <div className='rounded border bg-background p-2.5 font-mono text-xs text-foreground select-all'>
                          {dynamicSshCommand}
                        </div>
                      </div>

                      {/* Cấu hình VSCode */}
                      <div>
                        <div className='flex items-center justify-between mb-1'>
                          <span className='text-[11px] font-semibold text-muted-foreground'>
                            Cấu hình VSCode Remote SSH (thêm vào <code>~/.ssh/config</code>):
                          </span>
                          <Button
                            size='sm'
                            variant='outline'
                            className='h-7 text-[11px]'
                            onClick={() => copyToClipboard(vscodeRemoteSnippet, 'Cấu hình VSCode SSH')}
                          >
                            Sao chép config VSCode
                          </Button>
                        </div>
                        <pre className='rounded border bg-background p-2.5 font-mono text-xs text-foreground overflow-x-auto select-all'>
                          {vscodeRemoteSnippet}
                        </pre>
                      </div>
                    </>
                  ) : (
                    <div className='rounded-md border border-amber-300 bg-amber-500/10 p-3 text-xs text-amber-900 dark:text-amber-200'>
                      <div className='font-semibold'>Workspace chưa sẵn sàng kết nối</div>
                      <p className='mt-1 text-[11px]'>
                        Container workspace của bạn đang ở trạng thái{' '}
                        <strong>{primaryWorkspace?.observed_state || 'PENDING'}</strong> hoặc chưa được phân bổ cổng SSH.
                        Vui lòng nhấn <strong>&quot;Làm mới dữ liệu&quot;</strong> hoặc liên hệ Quản trị viên LabOps để khởi chạy.
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ============================================================== */}
        {/* TAB 3: WORKSPACE CỦA TÔI                                      */}
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
                      <Badge variant='outline' className='font-mono text-[10px]'>
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
                      <span className='font-semibold'>{Math.round(primaryWorkspace.ram_mb_limit / 1024)} GiB</span>
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
                  <div className='text-muted-foreground text-[11px]'>Đường dẫn thư mục cá nhân trên máy chủ:</div>
                  <div className='font-mono font-bold text-xs select-all text-foreground'>
                    /media/LabOps-Data/{username}
                  </div>
                  <p className='text-[11px] text-muted-foreground pt-1'>
                    Thư mục này được mount trực tiếp vào container của bạn. Toàn bộ code, checkpoints, models và datasets đặt tại đây sẽ không bị mất khi container khởi động lại.
                  </p>
                </div>

                <div className='space-y-2 pt-2'>
                  <div className='flex items-center justify-between border-b pb-2'>
                    <span className='text-muted-foreground'>Hạn mức lưu trữ (Quota):</span>
                    <span className='font-semibold'>
                      {primaryWorkspace?.storage_quota_gb ? `${primaryWorkspace.storage_quota_gb} GB` : 'Chưa giới hạn'}
                    </span>
                  </div>
                  <div className='flex items-center justify-between border-b pb-2'>
                    <span className='text-muted-foreground'>Quyền Docker Socket:</span>
                    <Badge variant='outline' className='text-rose-600 bg-rose-500/10 border-rose-300 text-[10px]'>
                      DENIED (Không gắn docker.sock)
                    </Badge>
                  </div>
                  <div className='flex items-center justify-between'>
                    <span className='text-muted-foreground'>Quyền Sudo Máy chủ:</span>
                    <Badge variant='outline' className='text-rose-600 bg-rose-500/10 border-rose-300 text-[10px]'>
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
                    <Icons.shield className='size-4 text-emerald-600' /> Tải Kho Mã An Toàn (Repository Security Gateway)
                  </CardTitle>
                  <CardDescription className='text-xs'>
                    Mã nguồn từ GitHub/GitLab được tự động quét mã độc, lộ bí mật và CVE trước khi tải vào workspace
                  </CardDescription>
                </div>
                <Badge variant='outline' className='text-[10px] font-mono border-emerald-300 text-emerald-700 bg-emerald-500/10'>
                  Zero-Trust Supply Chain Gate
                </Badge>
              </div>
            </CardHeader>
            <CardContent className='space-y-4 text-xs'>
              <div className='rounded-md border bg-muted/30 p-3 space-y-2'>
                <div className='font-semibold text-foreground text-xs'>
                  Cách tải kho mã vào thư mục làm việc bằng CLI:
                </div>
                <div className='rounded bg-black p-2.5 font-mono text-[11px] text-emerald-400 select-all'>
                  labops clone https://github.com/organization/your-repo --dest /workspace/work/your-repo
                </div>
                <p className='text-[11px] text-muted-foreground'>
                  Hệ thống sẽ tự động quét bằng Trivy scanner, xác minh SHA-256 digest và tự động phê duyệt nếu an toàn.
                </p>
              </div>

              {repoRequests.length > 0 ? (
                <div className='space-y-2 pt-1'>
                  <div className='font-semibold text-xs text-foreground'>
                    Yêu cầu kiểm duyệt của bạn ({repoRequests.length}):
                  </div>
                  <div className='divide-y rounded-md border'>
                    {repoRequests.map((req) => (
                      <div key={req.id} className='flex flex-wrap items-center justify-between p-2.5 gap-2 hover:bg-muted/30'>
                        <div className='space-y-0.5 min-w-0'>
                          <div className='font-mono font-medium text-xs truncate max-w-md'>
                            {req.repo_url} <span className='text-muted-foreground'>({req.requested_ref || 'HEAD'})</span>
                          </div>
                          <div className='text-[10px] text-muted-foreground font-mono'>
                            Commit: {req.commit_sha ? req.commit_sha.substring(0, 10) : 'Đang phân giải'} • {new Date(req.submitted_at).toLocaleString('vi-VN')}
                          </div>
                        </div>
                        <div className='flex items-center gap-2 shrink-0'>
                          <Badge
                            variant='outline'
                            className={`text-[10px] font-mono ${
                              req.state === 'AUTO_APPROVED' || req.state === 'MANUALLY_APPROVED' || req.state === 'DELIVERED'
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
                <p className='text-[11px] text-muted-foreground'>
                  Bạn chưa có yêu cầu kiểm duyệt kho mã nào. Sử dụng lệnh <code>labops clone</code> trong workspace để tải kho mã an toàn.
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
              <CardTitle className='text-sm font-bold'>Lượt Giữ chỗ GPU Độc quyền (FULL_GPU_EXCLUSIVE)</CardTitle>
              <CardDescription className='text-xs'>
                Gán trực tiếp theo UUID thiết bị GPU NVIDIA A30 thực tế; đảm bảo không xung đột bộ nhớ VRAM với thành viên khác
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
                        <Badge variant='outline' className='font-mono text-[10px]'>
                          {r.state}
                        </Badge>
                        <Badge
                          variant='outline'
                          className={`font-mono text-[10px] ${
                            r.runtime_verified
                              ? 'bg-emerald-500/10 text-emerald-700 border-emerald-300'
                              : 'bg-amber-500/10 text-amber-800 border-amber-300'
                          }`}
                        >
                          {r.runtime_verified ? 'VERIFIED' : 'PENDING_VERIFICATION'}
                        </Badge>
                      </div>
                    </div>
                    <div className='grid grid-cols-1 md:grid-cols-3 gap-2 font-mono text-[11px] text-muted-foreground pt-1 border-t'>
                      <div>GPU UUID: <span className='text-foreground'>{r.gpu_uuid}</span></div>
                      <div>Giữ chỗ: <strong className='text-foreground'>{r.allocated_hours ?? 0}h</strong></div>
                      <div>Chạy thực: <strong className='text-emerald-600'>{r.runtime_hours ?? 'Chưa xác minh'}h</strong></div>
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          {/* Kế toán GPU-Hours */}
          <Card>
            <CardHeader className='pb-3'>
              <CardTitle className='text-sm font-bold'>Bảng Phân tích GPU-Hours Trung thực</CardTitle>
              <CardDescription className='text-xs'>
                Tách biệt rõ ràng giữa thời gian giữ chỗ tài nguyên và thời gian huấn luyện thực tế
              </CardDescription>
            </CardHeader>
            <CardContent className='text-xs space-y-3'>
              <div className='grid grid-cols-1 md:grid-cols-3 gap-3'>
                <div className='rounded-md border p-3 bg-muted/20'>
                  <div className='text-muted-foreground text-[11px]'>Thời gian giữ chỗ (Allocated Hours)</div>
                  <div className='mt-1 text-lg font-bold text-foreground'>
                    {snapshot?.usage.allocated_gpu_hours ?? 0} h
                  </div>
                  <div className='mt-1 text-[10px] text-muted-foreground'>
                    Tổng thời gian từ lúc cấp slot GPU đến lúc giải phóng
                  </div>
                </div>

                <div className='rounded-md border p-3 bg-emerald-500/10 border-emerald-500/30'>
                  <div className='text-emerald-800 dark:text-emerald-200 text-[11px]'>Thời gian chạy thực (Runtime Hours)</div>
                  <div className='mt-1 text-lg font-bold text-emerald-700 dark:text-emerald-300'>
                    {snapshot?.usage.verified_runtime_gpu_hours ?? 0} h
                  </div>
                  <div className='mt-1 text-[10px] text-muted-foreground'>
                    Được Node Agent và NVML xác minh có tiến trình tính toán
                  </div>
                </div>

                <div className='rounded-md border p-3 bg-muted/20'>
                  <div className='text-muted-foreground text-[11px]'>Thời gian nhàn rỗi (Idle Hold Hours)</div>
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
                  <div className='mt-1 text-[10px] text-muted-foreground'>
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
