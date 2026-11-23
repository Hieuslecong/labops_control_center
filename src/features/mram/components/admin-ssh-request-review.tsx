'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { labopsCall } from '@/features/labops/api';
import { formatRemainingSla, ServerAccessRequestItem } from '../types';
import { toast } from 'sonner';

interface AdminSshRequestReviewProps {
  requestId: string;
  inviteToken?: string;
}

interface HostEntry {
  id: string;
  display_name?: string;
  hostname?: string;
  status?: string;
  observed_state?: string;
  freshness?: string;
}

interface PoolEntry {
  storage_id?: string;
  id?: string;
  device?: string;
  mount_point?: string;
  allocatable_gib?: number;
  eligible?: boolean;
  ineligibility_reason?: string | null;
}

interface GpuEntry {
  id?: string;
  uuid?: string;
  name?: string;
  model?: string;
  vram_total_mb?: number;
}

export function AdminSshRequestReview({ requestId, inviteToken }: AdminSshRequestReviewProps) {
  const [reqItem, setReqItem] = useState<ServerAccessRequestItem | null>(null);
  const [hosts, setHosts] = useState<HostEntry[]>([]);
  const [poolsByHost, setPoolsByHost] = useState<Record<string, PoolEntry[]>>({});
  const [gpusByHost, setGpusByHost] = useState<Record<string, GpuEntry[]>>({});
  const [inviteStatus, setInviteStatus] = useState<{ valid: boolean; reason?: string } | null>(
    null
  );
  const [loading, setLoading] = useState(true);

  // Editable config fields (initialized from recommendation)
  const [hostId, setHostId] = useState('');
  const [poolId, setPoolId] = useState('');
  const [cpuCores, setCpuCores] = useState(4);
  const [ramGb, setRamGb] = useState(16);
  const [storageGb, setStorageGb] = useState(50);
  const [gpuMode, setGpuMode] = useState('NONE');
  const [gpuUuid, setGpuUuid] = useState('');
  const [gpuVramGb, setGpuVramGb] = useState(12);
  const [durationDays, setDurationDays] = useState<string>('30');
  const [decisionReason, setDecisionReason] = useState(
    'Đã kiểm tra hồ sơ, GVHD và tài nguyên hợp lệ.'
  );
  const [adminPassword, setAdminPassword] = useState('');
  const [validationResult, setValidationResult] = useState<{
    valid: boolean;
    errors: string[];
    warnings: string[];
  } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [completedDecision, setCompletedDecision] = useState<{
    action: 'approve' | 'reject' | 'request-info';
    status: string;
    verification?: Record<string, unknown>;
  } | null>(null);

  const loadDetail = useCallback(
    async (skipInviteVerify = false) => {
      setLoading(true);
      const res = await labopsCall<{
        request: ServerAccessRequestItem;
        hosts: HostEntry[];
        pools_by_host: Record<string, PoolEntry[]>;
        gpus_by_host: Record<string, GpuEntry[]>;
      }>(`admin/ssh-requests/${encodeURIComponent(requestId)}`);

      if (res.ok && res.data) {
        const r = res.data.request;
        setReqItem(r);
        setHosts(res.data.hosts || []);
        setPoolsByHost(res.data.pools_by_host || {});
        setGpusByHost(res.data.gpus_by_host || {});
        if (r.recommendation) {
          const recHost = r.recommendation.recommended_host_id || '';
          setHostId(recHost);
          setPoolId(r.recommendation.recommended_pool_id || '');
          setCpuCores(r.recommendation.cpu_cores || 4);
          setRamGb(r.recommendation.ram_gb || 16);
          setStorageGb(
            r.recommendation.storage_quota_gb || (r.member_category === 'LECTURER' ? 100 : 50)
          );
          const recGpuMode =
            r.recommendation.gpu_mode && r.recommendation.gpu_mode !== 'NONE'
              ? r.recommendation.gpu_mode
              : r.gpu_requested
                ? 'SHARED'
                : 'NONE';
          setGpuMode(recGpuMode);
          const hostGpuList = (res.data.gpus_by_host || {})[recHost] || [];
          const fallbackGpuUuid = String(hostGpuList[0]?.uuid || hostGpuList[0]?.id || '');
          setGpuUuid(r.recommendation.gpu_uuid || (recGpuMode !== 'NONE' ? fallbackGpuUuid : ''));
          setGpuVramGb(r.recommendation.gpu_vram_gb || 12);
          setDurationDays(
            r.recommendation.duration_days != null ? String(r.recommendation.duration_days) : ''
          );
        } else {
          setStorageGb(r.member_category === 'LECTURER' ? 100 : 50);
        }
      } else {
        toast.error('Không thể tải chi tiết yêu cầu SSH', { description: res.error });
      }

      if (inviteToken && !skipInviteVerify) {
        const vRes = await labopsCall<{ valid: boolean; reason?: string }>(
          `admin/approval-invitations/verify?request_id=${encodeURIComponent(requestId)}&token=${encodeURIComponent(inviteToken)}`
        );
        if (vRes.ok && vRes.data) {
          setInviteStatus(vRes.data);
        }
      }
      setLoading(false);
    },
    [requestId, inviteToken]
  );

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    if (typeof window !== 'undefined' && inviteToken) {
      const currentUrl = new URL(window.location.href);
      if (currentUrl.searchParams.has('verified')) {
        currentUrl.searchParams.delete('verified');
        window.history.replaceState(null, '', currentUrl.pathname + currentUrl.search);
      }
    }
    void loadDetail();
  }, [loadDetail, inviteToken]);
  /* oxlint-enable react/set-state-in-effect */

  // Host switch MUST reset poolId and gpuUuid cleanly
  const handleHostChange = (newHostId: string) => {
    setHostId(newHostId);
    const nextPools = poolsByHost[newHostId] || [];
    const preferredPool = nextPools.find((p) => p.eligible) || nextPools[0];
    setPoolId(preferredPool ? String(preferredPool.storage_id || preferredPool.id || '') : '');
    setGpuUuid('');
    setGpuMode('NONE');
    setValidationResult(null);
  };

  const buildConfigPayload = () => ({
    host_id: hostId,
    pool_id: poolId,
    cpu_cores: Number(cpuCores),
    ram_gb: Number(ramGb),
    storage_quota_gb: Number(storageGb),
    gpu_mode: gpuMode,
    gpu_uuid: gpuMode === 'NONE' ? null : gpuUuid || null,
    gpu_vram_gb: gpuMode === 'SHARED' ? Number(gpuVramGb || 12) : null,
    duration_days: durationDays.trim() ? Number(durationDays) : null
  });

  const handleValidate = async () => {
    const res = await labopsCall<{ valid: boolean; errors: string[]; warnings: string[] }>(
      `admin/ssh-requests/${encodeURIComponent(requestId)}/validate`,
      { method: 'POST', body: buildConfigPayload() }
    );
    if (res.ok && res.data) {
      setValidationResult(res.data);
      if (res.data.valid) toast.success('Kiểm tra trước cấp phát đạt (VALID)');
      else toast.error('Kiểm tra tài nguyên chưa đạt', { description: res.data.errors.join('; ') });
    } else {
      toast.error('Lỗi pre-validation', { description: res.error });
    }
  };

  const hasValidInvite = Boolean(inviteToken && inviteStatus?.valid);

  const handleDecision = async (action: 'approve' | 'reject' | 'request-info') => {
    const extraHeaders: Record<string, string> = {};
    if (!hasValidInvite) {
      if (!adminPassword.trim()) {
        toast.error('Bắt buộc nhập Mật khẩu Quản trị viên (Step-Up Auth) để xác nhận');
        return;
      }
      setSubmitting(true);
      const stepAction =
        action === 'approve'
          ? 'APPROVE_SSH_REQUEST'
          : action === 'reject'
            ? 'REJECT_SSH_REQUEST'
            : 'REQUEST_INFO_SSH_REQUEST';

      const stepRes = await labopsCall<{ step_up_token: string }>('auth/step-up', {
        method: 'POST',
        body: {
          password: adminPassword,
          action: stepAction,
          target_id: requestId
        }
      });
      if (!stepRes.ok || !stepRes.data?.step_up_token) {
        setSubmitting(false);
        toast.error('Xác thực Step-Up thất bại', { description: stepRes.error });
        return;
      }
      extraHeaders['X-Step-Up-Token'] = stepRes.data.step_up_token;
    } else {
      setSubmitting(true);
    }

    const res = await labopsCall<{
      decision?: string;
      request?: ServerAccessRequestItem;
      provisioning?: {
        status?: string;
        verification?: Record<string, unknown>;
        error?: string | null;
      };
    }>(`admin/ssh-requests/${encodeURIComponent(requestId)}/${action}`, {
      method: 'POST',
      headers: extraHeaders,
      body: {
        reason: decisionReason,
        expected_version: reqItem?.version,
        invitation_token: inviteToken || undefined,
        config: buildConfigPayload()
      }
    });
    setSubmitting(false);
    setAdminPassword('');

    if (!res.ok) {
      toast.error(`Không thể thực hiện ${action}`, { description: res.error });
      return;
    }
    setCompletedDecision({
      action,
      status: res.data?.provisioning?.status || res.data?.decision || action.toUpperCase(),
      verification: res.data?.provisioning?.verification
    });
    if (res.data?.request) {
      setReqItem(res.data.request);
    } else {
      void loadDetail(true);
    }
    toast.success(`Đã xử lý yêu cầu SSH (${action.toUpperCase()})`);
  };

  if (loading) {
    return (
      <Card>
        <CardContent className='p-6 text-sm text-muted-foreground'>
          Đang tải hồ sơ xét duyệt...
        </CardContent>
      </Card>
    );
  }

  if (!reqItem) {
    return (
      <Card>
        <CardContent className='p-6 text-sm text-rose-600'>Không tìm thấy yêu cầu SSH.</CardContent>
      </Card>
    );
  }

  const hostPools = poolsByHost[hostId] || [];
  const hostGpus = gpusByHost[hostId] || [];
  const isFinal = !['PENDING_APPROVAL', 'NEED_INFO'].includes(reqItem.status);

  const isStandaloneInvite = Boolean(inviteToken);

  if (isStandaloneInvite && completedDecision) {
    const ver = completedDecision.verification || reqItem.provisioning?.verification || {};
    const sshIp = typeof ver.ssh_ip === 'string' ? ver.ssh_ip : undefined;
    const sshPort = typeof ver.ssh_port === 'number' ? ver.ssh_port : undefined;
    const workspaceDir = typeof ver.workspace_dir === 'string' ? ver.workspace_dir : undefined;
    const isApproved = completedDecision.action === 'approve';
    return (
      <div className='fixed inset-0 z-50 overflow-y-auto bg-slate-50 dark:bg-zinc-950 p-4 sm:p-8 flex items-center justify-center'>
        <Card className='w-full max-w-xl'>
          <CardHeader>
            <div className='flex items-center justify-between gap-2'>
              <CardTitle className='text-lg'>
                {isApproved
                  ? 'Đã Phê duyệt & Cấp phát Quyền SSH'
                  : completedDecision.action === 'reject'
                    ? 'Đã Từ chối Yêu cầu SSH'
                    : 'Đã Gửi Yêu cầu Bổ sung'}
              </CardTitle>
              <Badge variant='outline'>{reqItem.status}</Badge>
            </div>
            <CardDescription className='text-xs'>
              Mã yêu cầu: <span className='font-mono'>{reqItem.id}</span> • {reqItem.full_name} (@
              {reqItem.username})
            </CardDescription>
          </CardHeader>
          <CardContent className='space-y-3 text-xs'>
            {isApproved && (
              <div className='rounded-md border border-emerald-500/30 bg-emerald-500/5 p-3 space-y-1.5'>
                <div className='font-semibold text-emerald-700 dark:text-emerald-300'>
                  Cấu hình đã cấp phát thành công
                </div>
                <div className='grid grid-cols-1 sm:grid-cols-2 gap-1.5 font-mono'>
                  <div>Máy chủ: {hostId || 'workstation'}</div>
                  <div>
                    Tài nguyên: {cpuCores} vCPU / {ramGb} GiB RAM / {storageGb} GiB
                  </div>
                  <div>
                    GPU: {gpuMode === 'SHARED' ? `SHARED (${gpuVramGb} GiB VRAM)` : gpuMode}
                  </div>
                  {sshIp && sshPort && (
                    <div>
                      SSH: {sshIp}:{sshPort}
                    </div>
                  )}
                </div>
                {workspaceDir && (
                  <div className='font-mono text-muted-foreground break-all'>
                    Workspace: {workspaceDir}
                  </div>
                )}
              </div>
            )}
            <div className='rounded-md border bg-muted/40 p-3 text-muted-foreground'>
              Liên kết phê duyệt 1 lần của yêu cầu này đã được sử dụng và tự động khóa. Tải lại
              trang hoặc mở lại liên kết sẽ không thể truy cập lại.
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (
    isStandaloneInvite &&
    !completedDecision &&
    ((inviteStatus && !inviteStatus.valid) || isFinal)
  ) {
    return (
      <div className='fixed inset-0 z-50 overflow-y-auto bg-slate-50 dark:bg-zinc-950 p-4 sm:p-8 flex items-center justify-center'>
        <Card className='w-full max-w-lg border-rose-500/30'>
          <CardHeader>
            <div className='flex items-center justify-between gap-2'>
              <CardTitle className='text-lg'>Liên kết phê duyệt 1 lần không còn hiệu lực</CardTitle>
              <Badge variant='outline'>{reqItem.status}</Badge>
            </div>
            <CardDescription className='text-xs font-mono'>{reqItem.id}</CardDescription>
          </CardHeader>
          <CardContent className='space-y-2 text-xs text-muted-foreground'>
            <p>
              Mỗi yêu cầu cấp quyền SSH chỉ có duy nhất một liên kết phê duyệt sử dụng 1 lần. Yêu
              cầu này đã được xử lý trước đó hoặc liên kết đã hết hạn.
            </p>
            <div className='rounded border bg-muted/40 p-2 font-mono'>
              Trạng thái: {inviteStatus?.reason || `REQUEST_${reqItem.status}`}
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div
      className={
        isStandaloneInvite
          ? 'fixed inset-0 z-50 overflow-y-auto bg-slate-50 dark:bg-zinc-950 p-4 sm:p-8'
          : ''
      }
    >
      <div className={isStandaloneInvite ? 'mx-auto max-w-3xl space-y-4' : 'space-y-4'}>
        <div className='flex flex-wrap items-center justify-between gap-2'>
          {isStandaloneInvite ? (
            <h1 className='text-lg font-bold'>Xác nhận Cấp Quyền Truy cập SSH</h1>
          ) : (
            <Link href='/dashboard/ssh-requests'>
              <Button variant='outline' size='sm'>
                ← Quay lại hàng đợi
              </Button>
            </Link>
          )}
          <div className='flex flex-wrap items-center gap-2'>
            <Badge variant='outline' className='font-mono'>
              {reqItem.id}
            </Badge>
            <Badge variant='outline'>{reqItem.status}</Badge>
            {reqItem.status === 'PENDING_APPROVAL' && (
              <Badge variant='outline' className='text-amber-700 font-mono'>
                SLA còn: {formatRemainingSla(reqItem.remaining_review_seconds)}
              </Badge>
            )}
          </div>
        </div>

        {inviteStatus && !inviteStatus.valid && (
          <Card className='border-rose-500/40 bg-rose-500/10'>
            <CardContent className='p-3 text-xs'>
              ⚠️ Liên kết phê duyệt Zalo không còn hiệu lực ({inviteStatus.reason}).
            </CardContent>
          </Card>
        )}

        {/* Hồ sơ thành viên & Lý do */}
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-base'>Thông tin Người đăng ký</CardTitle>
          </CardHeader>
          <CardContent className='space-y-3 text-xs'>
            <div className='grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2'>
              <div>
                <strong>Họ tên:</strong> {reqItem.full_name} (@{reqItem.username})
              </div>
              <div>
                <strong>Loại:</strong>{' '}
                {reqItem.member_category === 'LECTURER' ? 'Giảng viên' : 'Sinh viên'} (
                {reqItem.member_code || 'N/A'})
              </div>
              <div>
                <strong>Lớp/Bộ môn:</strong> {reqItem.department_class || 'N/A'}
              </div>
              <div>
                <strong>GVHD:</strong> {reqItem.supervisor_name || 'N/A'}
              </div>
              <div>
                <strong>Dự án:</strong> {reqItem.project_title}
              </div>
              <div>
                <strong>Yêu cầu GPU:</strong> {reqItem.gpu_requested ? 'CÓ' : 'Không'}
              </div>
            </div>
            <div className='rounded border p-2.5 bg-muted/30'>
              <strong>Lý do sử dụng máy chủ:</strong> {reqItem.registration_reason}
            </div>
          </CardContent>
        </Card>

        {/* Bảng chỉnh sửa cấu hình tài nguyên */}
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-base'>
              {isFinal
                ? reqItem.status === 'REJECTED'
                  ? 'Cấu hình Yêu cầu (Đã từ chối)'
                  : reqItem.status === 'EXPIRED'
                    ? 'Cấu hình Yêu cầu (Đã quá hạn 24h)'
                    : 'Cấu hình Tài nguyên Đã Phê duyệt & Cấp phát'
                : 'Cấu hình Tài nguyên Cấp phát (Admin có thể chỉnh)'}
            </CardTitle>
            {!isFinal && (
              <CardDescription className='text-xs'>
                Đổi máy chủ sẽ tự động reset Storage Pool và GPU UUID để tránh mismatch giữa các
                node.
              </CardDescription>
            )}
          </CardHeader>
          <CardContent className='space-y-4 text-sm'>
            {reqItem.recommendation?.manual_review_required && (
              <div className='rounded border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-200'>
                ⚠️ Hệ thống đánh dấu cần kiểm tra thủ công:{' '}
                {String(
                  reqItem.recommendation.rationale?.manual_reason || 'Kiểm tra host/storage pool'
                )}
              </div>
            )}

            <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
              <div className='space-y-1'>
                <Label>Máy chủ (Host)</Label>
                <select
                  disabled={isFinal}
                  value={hostId}
                  onChange={(e) => handleHostChange(e.target.value)}
                  className='w-full rounded-md border bg-background px-3 py-2 text-sm'
                >
                  <option value=''>-- Chọn máy chủ --</option>
                  {hosts.map((h) => (
                    <option key={h.id} value={h.id}>
                      {h.display_name || h.hostname || h.id} (
                      {h.observed_state || h.freshness || h.status || 'UNKNOWN'})
                    </option>
                  ))}
                </select>
              </div>

              <div className='space-y-1'>
                <Label>Storage Pool vật lý (đã xác minh mount/UUID)</Label>
                <select
                  disabled={isFinal || !hostId}
                  value={poolId}
                  onChange={(e) => {
                    setPoolId(e.target.value);
                    setValidationResult(null);
                  }}
                  className='w-full rounded-md border bg-background px-3 py-2 text-sm'
                >
                  <option value=''>-- Chọn Storage Pool --</option>
                  {hostPools.map((p) => {
                    const sid = String(p.storage_id || p.id || '');
                    const devLabel = p.device ? p.device.split('/').pop() : '';
                    return (
                      <option key={sid} value={sid} disabled={!p.eligible}>
                        {devLabel ? `[${devLabel}] ` : ''}
                        {p.mount_point} ({sid}) — Còn {p.allocatable_gib ?? 0} GiB{' '}
                        {p.eligible ? '' : `[INELIGIBLE: ${p.ineligibility_reason}]`}
                      </option>
                    );
                  })}
                </select>
              </div>

              <div className='space-y-1'>
                <Label>CPU (vCPU Shared)</Label>
                <Input
                  disabled={isFinal}
                  type='number'
                  min={1}
                  max={64}
                  value={cpuCores}
                  onChange={(e) => setCpuCores(Number(e.target.value))}
                />
              </div>

              <div className='space-y-1'>
                <Label>RAM (GiB)</Label>
                <Input
                  disabled={isFinal}
                  type='number'
                  min={1}
                  max={512}
                  value={ramGb}
                  onChange={(e) => setRamGb(Number(e.target.value))}
                />
              </div>

              <div className='space-y-1'>
                <Label>Storage Quota (GiB)</Label>
                <Input
                  disabled={isFinal}
                  type='number'
                  min={10}
                  max={5000}
                  value={storageGb}
                  onChange={(e) => setStorageGb(Number(e.target.value))}
                />
              </div>

              <div className='space-y-1'>
                <Label>
                  Thời hạn SSH (ngày
                  {reqItem.member_category === 'LECTURER' ? ' — Không bắt buộc với GV' : ''})
                </Label>
                <Input
                  disabled={isFinal}
                  type='number'
                  min={1}
                  max={365}
                  value={durationDays}
                  placeholder='Để trống = không thời hạn'
                  onChange={(e) => setDurationDays(e.target.value)}
                />
              </div>

              <div className='space-y-1'>
                <Label>Chế độ GPU</Label>
                <select
                  disabled={isFinal}
                  value={gpuMode}
                  onChange={(e) => {
                    const nextMode = e.target.value;
                    setGpuMode(nextMode);
                    if (nextMode === 'NONE') {
                      setGpuUuid('');
                    } else {
                      if (!gpuUuid && hostGpus.length > 0) {
                        setGpuUuid(String(hostGpus[0].uuid || hostGpus[0].id || ''));
                      }
                      if (nextMode === 'SHARED' && !gpuVramGb) {
                        setGpuVramGb(12);
                      }
                    }
                  }}
                  className='w-full rounded-md border bg-background px-3 py-2 text-sm'
                >
                  <option value='NONE'>NONE (Không cấp GPU)</option>
                  <option value='SHARED'>SHARED (Chia sẻ GPU — cấp VRAM theo GiB)</option>
                  <option value='EXCLUSIVE'>EXCLUSIVE (Độc quyền cả GPU)</option>
                </select>
              </div>

              {gpuMode !== 'NONE' && (
                <div className='space-y-1'>
                  <Label>Chọn GPU UUID trên {hostId}</Label>
                  <select
                    disabled={isFinal}
                    value={gpuUuid}
                    onChange={(e) => setGpuUuid(e.target.value)}
                    className='w-full rounded-md border bg-background px-3 py-2 text-sm'
                  >
                    <option value=''>-- Chọn GPU --</option>
                    {hostGpus.map((g) => {
                      const gid = String(g.uuid || g.id || '');
                      return (
                        <option key={gid} value={gid}>
                          {g.name || g.model || gid} ({gid})
                        </option>
                      );
                    })}
                  </select>
                </div>
              )}

              {gpuMode === 'SHARED' && (
                <div className='space-y-1'>
                  <Label>RAM GPU (VRAM) cấp phát (GiB — mặc định 12 GB)</Label>
                  <Input
                    disabled={isFinal}
                    type='number'
                    min={1}
                    max={80}
                    value={gpuVramGb}
                    onChange={(e) => setGpuVramGb(Number(e.target.value))}
                  />
                </div>
              )}
            </div>

            {validationResult && (
              <div
                className={`rounded border p-3 text-xs space-y-1 ${
                  validationResult.valid
                    ? 'border-emerald-500/40 bg-emerald-500/10'
                    : 'border-rose-500/40 bg-rose-500/10'
                }`}
              >
                <div className='font-semibold'>
                  {validationResult.valid ? '✅ Pre-Validation Đạt' : '❌ Pre-Validation Không Đạt'}
                </div>
                {validationResult.errors.map((err, i) => (
                  <div key={i} className='text-rose-700 dark:text-rose-300'>
                    • {err}
                  </div>
                ))}
                {validationResult.warnings.map((w, i) => (
                  <div key={i} className='text-amber-700 dark:text-amber-300'>
                    ⚠️ {w}
                  </div>
                ))}
              </div>
            )}

            {!isFinal && (
              <div className='space-y-3 border-t pt-3'>
                <div className='space-y-1'>
                  <Label>Lý do quyết định / Ghi chú</Label>
                  <Textarea
                    value={decisionReason}
                    onChange={(e) => setDecisionReason(e.target.value)}
                    rows={2}
                  />
                </div>
                {!hasValidInvite && (
                  <div className='space-y-1'>
                    <Label>Mật khẩu Admin (Step-Up Verification) *</Label>
                    <Input
                      type='password'
                      value={adminPassword}
                      onChange={(e) => setAdminPassword(e.target.value)}
                      placeholder='Nhập LABOPS_ADMIN_PASSWORD để xác thực'
                    />
                  </div>
                )}
                <div className='flex flex-wrap gap-2'>
                  <Button type='button' variant='outline' onClick={() => void handleValidate()}>
                    Kiểm tra Trước Cấp phát
                  </Button>
                  <Button
                    type='button'
                    disabled={submitting}
                    onClick={() => void handleDecision('approve')}
                  >
                    {submitting ? 'Đang xử lý...' : 'Phê duyệt & Cấp phát'}
                  </Button>
                  <Button
                    type='button'
                    variant='secondary'
                    disabled={submitting}
                    onClick={() => void handleDecision('request-info')}
                  >
                    Yêu cầu Bổ sung
                  </Button>
                  <Button
                    type='button'
                    variant='destructive'
                    disabled={submitting}
                    onClick={() => void handleDecision('reject')}
                  >
                    Từ chối
                  </Button>
                </div>
              </div>
            )}

            {isFinal && (
              <div
                className={`rounded border p-3 text-xs space-y-1 ${
                  reqItem.status === 'REJECTED' || reqItem.status === 'PROVISIONING_FAILED'
                    ? 'border-rose-500/40 bg-rose-500/10'
                    : reqItem.status === 'EXPIRED'
                      ? 'border-slate-400 bg-muted/40'
                      : 'border-emerald-500/40 bg-emerald-500/10'
                }`}
              >
                <div className='font-semibold'>
                  {reqItem.status === 'REJECTED'
                    ? 'Đã Từ chối Yêu cầu'
                    : reqItem.status === 'EXPIRED'
                      ? 'Yêu cầu Đã Quá hạn 24h (SLA)'
                      : 'Đã Phê duyệt & Cấp phát Quyền SSH'}
                </div>
                <div className='text-muted-foreground'>
                  Người xử lý: <strong>{reqItem.decided_by || 'admin'}</strong>
                  {reqItem.decided_at
                    ? ` • Lúc: ${new Date(reqItem.decided_at).toLocaleString('vi-VN')}`
                    : ''}
                </div>
                {reqItem.decision_reason && (
                  <div>
                    <strong>Ghi chú quyết định:</strong> {reqItem.decision_reason}
                  </div>
                )}
              </div>
            )}

            {reqItem.provisioning && (
              <div className='rounded border p-3 text-xs space-y-1 bg-muted/20'>
                <div className='font-semibold'>
                  Trạng thái Cấp phát: {reqItem.provisioning.status}
                </div>
                {reqItem.provisioning.last_error && (
                  <div className='text-rose-600'>Lỗi: {reqItem.provisioning.last_error}</div>
                )}
                {(() => {
                  const ver = (reqItem.provisioning.verification || {}) as Record<string, unknown>;
                  const sshIp = typeof ver.ssh_ip === 'string' ? ver.ssh_ip : undefined;
                  const sshPort = typeof ver.ssh_port === 'number' ? ver.ssh_port : undefined;
                  const wsDir =
                    typeof ver.workspace_dir === 'string' ? ver.workspace_dir : undefined;
                  return (
                    <div className='space-y-1 font-mono text-muted-foreground pt-1'>
                      {sshIp && sshPort && (
                        <div>
                          SSH Endpoint: {sshIp}:{sshPort}
                        </div>
                      )}
                      {wsDir && <div className='break-all'>Workspace: {wsDir}</div>}
                    </div>
                  );
                })()}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
