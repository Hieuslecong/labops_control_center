'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { labopsCall } from '@/features/labops/api';
import { formatRemainingSla, MemberCategory, ServerAccessRequestItem } from '../types';
import { toast } from 'sonner';

interface AuthMeInfo {
  username: string;
  full_name: string;
  member_id: string | null;
  member_category: MemberCategory | 'ADMIN';
  ssh_access_state: string;
}

const ACADEMIC_TITLES = new Set([
  'TS.',
  'PGS.',
  'PGS.TS.',
  'GS.',
  'GS.TS.',
  'THS.',
  'TS',
  'PGS',
  'PGS.TS',
  'GS',
  'GS.TS',
  'THS'
]);

const VN_TONE_MAP: Record<string, string> = {
  à: 'òa',
  á: 'óa',
  ả: 'ỏa',
  ã: 'õa',
  ạ: 'ọa',
  À: 'Òa',
  Á: 'Óa',
  Ả: 'Ỏa',
  Ã: 'Õa',
  Ạ: 'Ọa',
  è: 'òe',
  é: 'óe',
  ẻ: 'ỏe',
  ẽ: 'õe',
  ẹ: 'ọe',
  È: 'Òe',
  É: 'Óe',
  Ẻ: 'Ỏe',
  Ẽ: 'Õe',
  Ẹ: 'Ọe',
  ỳ: 'ùy',
  ý: 'úy',
  ỷ: 'ủy',
  ỹ: 'ũy',
  ỵ: 'ụy',
  Ỳ: 'Ùy',
  Ý: 'Úy',
  Ỷ: 'Ủy',
  Ỹ: 'Ũy',
  Ỵ: 'Ụy'
};

function normalizeVietnameseToneWord(word: string): string {
  const nfc = word.normalize('NFC');
  if (/^[qQ]/.test(nfc)) return nfc;
  return nfc
    .replace(/([oO])([àáảãạÀÁẢÃẠ])$/, (_, o: string, v: string) => {
      const mapped = VN_TONE_MAP[v] || o + v;
      return o === 'O' ? mapped.charAt(0).toUpperCase() + mapped.slice(1) : mapped.toLowerCase();
    })
    .replace(/([oO])([èéẻẽẹÈÉẺẼẸ])$/, (_, o: string, v: string) => {
      const mapped = VN_TONE_MAP[v] || o + v;
      return o === 'O' ? mapped.charAt(0).toUpperCase() + mapped.slice(1) : mapped.toLowerCase();
    })
    .replace(/([uU])([ỳýỷỹỵỲÝỶỸỴ])$/, (_, u: string, v: string) => {
      const mapped = VN_TONE_MAP[v] || u + v;
      return u === 'U' ? mapped.charAt(0).toUpperCase() + mapped.slice(1) : mapped.toLowerCase();
    });
}

function formatSupervisorTitleCase(raw: string): string {
  const cleaned = raw.normalize('NFC').trim().replace(/\s+/g, ' ');
  if (!cleaned) return '';
  return cleaned
    .split(' ')
    .filter((w) => !ACADEMIC_TITLES.has(w.toUpperCase()))
    .map((w) => {
      const norm = normalizeVietnameseToneWord(w);
      return norm.charAt(0).toUpperCase() + norm.slice(1).toLowerCase();
    })
    .join(' ');
}

function getSshStatusMeta(status: string): { label: string; className: string } {
  switch (status) {
    case 'PENDING_APPROVAL':
      return {
        label: 'Đang chờ Admin duyệt',
        className: 'bg-amber-500/10 text-amber-700 border-amber-400 dark:text-amber-300'
      };
    case 'VERIFIED_READY':
    case 'APPROVED':
      return {
        label: 'Đã phê duyệt & Cấp phát',
        className: 'bg-emerald-500/10 text-emerald-700 border-emerald-400 dark:text-emerald-300'
      };
    case 'REJECTED':
      return {
        label: 'Đã từ chối',
        className: 'bg-rose-500/10 text-rose-700 border-rose-400 dark:text-rose-300'
      };
    case 'NEED_INFO':
      return {
        label: 'Cần bổ sung thông tin',
        className: 'bg-orange-500/10 text-orange-700 border-orange-400 dark:text-orange-300'
      };
    case 'EXPIRED':
      return {
        label: 'Quá hạn 24h',
        className: 'bg-slate-500/10 text-slate-700 border-slate-400 dark:text-slate-300'
      };
    case 'PROVISIONING_FAILED':
      return {
        label: 'Lỗi cấp phát',
        className: 'bg-rose-500/10 text-rose-700 border-rose-400 dark:text-rose-300'
      };
    default:
      return { label: status, className: '' };
  }
}

interface SshRequestHistoryCardProps {
  requests: ServerAccessRequestItem[];
  onReload?: () => void;
}

export function SshRequestHistoryCard({ requests, onReload }: SshRequestHistoryCardProps) {
  const [busyId, setBusyId] = useState<string | null>(null);

  const handleReapply = async (requestId: string) => {
    setBusyId(requestId);
    const res = await labopsCall(`me/ssh-requests/${encodeURIComponent(requestId)}/reapply`, {
      method: 'POST',
      body: {}
    });
    setBusyId(null);
    if (!res.ok) {
      toast.error('Không thể gửi lại đơn', { description: res.error });
      return;
    }
    toast.success('Đã gửi lại đơn xin cấp quyền SSH (SLA 24 giờ)');
    onReload?.();
  };

  return (
    <Card>
      <CardHeader className='pb-3'>
        <div className='flex flex-wrap items-center justify-between gap-2'>
          <div>
            <CardTitle className='text-base'>Lịch sử &amp; Trạng thái Đơn SSH đã nộp</CardTitle>
            <CardDescription className='text-xs'>
              Kiểm tra các đơn bạn đã gửi trước đó và tình trạng phê duyệt từ Quản trị viên.
            </CardDescription>
          </div>
          <Badge variant='outline'>{requests.length} đơn</Badge>
        </div>
      </CardHeader>
      <CardContent className='space-y-2.5 text-xs'>
        {requests.length === 0 ? (
          <div className='rounded-md border border-dashed p-3 text-muted-foreground'>
            Bạn chưa nộp đơn xin cấp quyền SSH nào trước đây.
          </div>
        ) : (
          requests.map((r) => {
            const meta = getSshStatusMeta(r.status);
            return (
              <div key={r.id} className='rounded-md border p-3 space-y-1.5 bg-muted/20'>
                <div className='flex flex-wrap items-center justify-between gap-2'>
                  <div className='font-semibold text-sm'>
                    {r.project_title}{' '}
                    <span className='font-mono text-xs font-normal text-muted-foreground'>
                      ({r.id})
                    </span>
                  </div>
                  <div className='flex flex-wrap items-center gap-1.5'>
                    <Badge variant='outline' className={meta.className}>
                      {meta.label}
                    </Badge>
                    {r.status === 'PENDING_APPROVAL' && (
                      <Badge variant='outline' className='font-mono text-amber-700'>
                        Còn: {formatRemainingSla(r.remaining_review_seconds)}
                      </Badge>
                    )}
                  </div>
                </div>
                <div className='flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground'>
                  <span>
                    Gửi lúc:{' '}
                    {r.submitted_at ? new Date(r.submitted_at).toLocaleString('vi-VN') : '—'}
                  </span>
                  {r.supervisor_name && <span>GVHD: {r.supervisor_name}</span>}
                  <span>GPU: {r.gpu_requested ? 'Có yêu cầu' : 'Không'}</span>
                  {r.requested_duration_days != null && (
                    <span>Thời hạn đề xuất: {r.requested_duration_days} ngày</span>
                  )}
                </div>
                {r.decision_reason && (
                  <div className='rounded border bg-background px-2.5 py-1.5'>
                    <strong>Phản hồi Admin:</strong> {r.decision_reason}
                  </div>
                )}
                {(r.status === 'VERIFIED_READY' ||
                  r.status === 'EXPIRED' ||
                  r.status === 'REJECTED') && (
                  <div className='flex flex-wrap justify-end gap-2 pt-1'>
                    {r.status === 'VERIFIED_READY' && (
                      <Link href='/dashboard/researcher/access'>
                        <Button size='sm' variant='outline'>
                          Xem thông tin kết nối SSH
                        </Button>
                      </Link>
                    )}
                    {(r.status === 'EXPIRED' || r.status === 'REJECTED') && (
                      <Button
                        size='sm'
                        variant='outline'
                        disabled={busyId === r.id}
                        onClick={() => void handleReapply(r.id)}
                      >
                        {busyId === r.id ? 'Đang gửi...' : 'Gửi lại đơn này'}
                      </Button>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}

export function ResearcherSshRequestForm() {
  const router = useRouter();
  const [me, setMe] = useState<AuthMeInfo | null>(null);
  const [myRequests, setMyRequests] = useState<ServerAccessRequestItem[]>([]);
  const [loading, setLoading] = useState(true);

  const [supervisorName, setSupervisorName] = useState('');
  const [projectTitle, setProjectTitle] = useState('');
  const [registrationReason, setRegistrationReason] = useState('');
  const [durationDays, setDurationDays] = useState('30');
  const [gpuRequested, setGpuRequested] = useState(false);
  const [policyAccepted, setPolicyAccepted] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const loadInit = useCallback(async () => {
    setLoading(true);
    const [meRes, reqRes] = await Promise.all([
      labopsCall<AuthMeInfo>('auth/me'),
      labopsCall<{ requests: ServerAccessRequestItem[] }>('me/ssh-requests')
    ]);
    if (meRes.ok && meRes.data) {
      setMe(meRes.data);
    }
    if (reqRes.ok && Array.isArray(reqRes.data?.requests)) {
      setMyRequests(reqRes.data.requests);
    }
    setLoading(false);
  }, []);

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void loadInit();
  }, [loadInit]);
  /* oxlint-enable react/set-state-in-effect */

  const isStudent = me?.member_category !== 'LECTURER';
  const pendingReq = myRequests.find((r) => r.status === 'PENDING_APPROVAL');

  const handleSubmit = async (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (isStudent && supervisorName.trim().length < 2) {
      toast.error('Vui lòng nhập họ tên Giảng viên hướng dẫn.');
      return;
    }
    if (projectTitle.trim().length < 3) {
      toast.error('Vui lòng nhập Tên đề tài / dự án.');
      return;
    }
    if (registrationReason.trim().length < 10) {
      toast.error('Vui lòng nhập lý do cần sử dụng máy chủ (tối thiểu 10 ký tự).');
      return;
    }
    if (!policyAccepted) {
      toast.error('Vui lòng xác nhận cam kết tuân thủ quy định sử dụng tài nguyên Lab.');
      return;
    }

    const formattedSupervisor = isStudent ? formatSupervisorTitleCase(supervisorName) : undefined;
    if (formattedSupervisor) {
      setSupervisorName(formattedSupervisor);
    }
    setSubmitting(true);
    const res = await labopsCall<{ request: { id: string } }>('me/ssh-requests', {
      method: 'POST',
      body: {
        supervisor_name: formattedSupervisor,
        project_title: projectTitle.trim(),
        registration_reason: registrationReason.trim(),
        requested_duration_days: isStudent ? Number(durationDays || 30) : undefined,
        gpu_requested: gpuRequested,
        policy_accepted: policyAccepted
      }
    });
    setSubmitting(false);

    if (!res.ok) {
      toast.error('Không thể gửi yêu cầu cấp SSH', { description: res.error });
      return;
    }
    toast.success('Đã gửi đơn xin cấp quyền SSH thành công!');
    router.push('/dashboard/researcher');
  };

  if (loading) {
    return (
      <Card>
        <CardContent className='p-6 text-sm text-muted-foreground'>Đang tải...</CardContent>
      </Card>
    );
  }

  return (
    <Card className='max-w-2xl'>
      <CardHeader>
        <div className='flex flex-wrap items-center justify-between gap-2'>
          <div>
            <CardTitle>Đăng ký Quyền Sử dụng Máy chủ (SSH)</CardTitle>
            <CardDescription className='text-xs mt-1'>
              Điền thông tin ngắn gọn bên dưới để Admin duyệt trong vòng 24 giờ.
            </CardDescription>
          </div>
          <div className='flex flex-wrap items-center gap-2'>
            <Link href='/dashboard/researcher/requests'>
              <Button type='button' variant='outline' size='sm'>
                Xem đơn đã nộp ({myRequests.length})
              </Button>
            </Link>
            <Badge variant='outline'>{isStudent ? 'Sinh viên' : 'Giảng viên'}</Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className='space-y-4 text-sm'>
        {pendingReq && (
          <div className='rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-200 flex flex-wrap items-center justify-between gap-2'>
            <span>
              Bạn đang có đơn <strong className='font-mono'>{pendingReq.id}</strong> (
              {pendingReq.project_title}) ở trạng thái <strong>Đang chờ Admin duyệt</strong>.
            </span>
            <Link href='/dashboard/researcher/requests'>
              <Button type='button' variant='outline' size='sm'>
                Xem trạng thái đơn
              </Button>
            </Link>
          </div>
        )}
        <div className='rounded-md border bg-muted/30 p-3 flex flex-wrap justify-between gap-2 text-xs'>
          <span>
            Người gửi: <strong>{me?.full_name || me?.username}</strong> (@{me?.username})
          </span>
          <Badge variant='outline'>SSH: {me?.ssh_access_state || 'NONE'}</Badge>
        </div>

        <form onSubmit={handleSubmit} className='space-y-4'>
          {isStudent && (
            <div className='space-y-1.5'>
              <Label htmlFor='ssh-sup-name'>Giảng viên hướng dẫn *</Label>
              <Input
                id='ssh-sup-name'
                required
                value={supervisorName}
                onChange={(e) => setSupervisorName(e.target.value)}
                onBlur={() => setSupervisorName((prev) => formatSupervisorTitleCase(prev))}
                placeholder='Nhập họ tên giảng viên hướng dẫn (VD: Lê Công Hiếu)'
              />
            </div>
          )}

          <div className='space-y-1.5'>
            <Label htmlFor='ssh-project'>Tên đề tài / Dự án *</Label>
            <Input
              id='ssh-project'
              required
              value={projectTitle}
              onChange={(e) => setProjectTitle(e.target.value)}
              placeholder='VD: Hệ thống nhận diện đa phương thức Edge-AI'
            />
          </div>

          <div className='space-y-1.5'>
            <Label htmlFor='ssh-reason'>Lý do &amp; Mục đích sử dụng máy chủ *</Label>
            <Textarea
              id='ssh-reason'
              required
              rows={3}
              value={registrationReason}
              onChange={(e) => setRegistrationReason(e.target.value)}
              placeholder='VD: Chạy huấn luyện mô hình YOLOv8 bằng Python qua VS Code Remote-SSH...'
            />
          </div>

          {isStudent && (
            <div className='space-y-1.5'>
              <Label htmlFor='ssh-days'>Thời hạn sử dụng đề xuất (ngày)</Label>
              <Input
                id='ssh-days'
                type='number'
                min={7}
                max={180}
                value={durationDays}
                onChange={(e) => setDurationDays(e.target.value)}
              />
            </div>
          )}

          <div className='space-y-2.5 rounded-md border p-3 text-xs'>
            <label className='flex items-center gap-2 cursor-pointer text-sm'>
              <input
                type='checkbox'
                checked={gpuRequested}
                onChange={(e) => setGpuRequested(e.target.checked)}
              />
              <span>Tôi có nhu cầu sử dụng GPU để huấn luyện mô hình</span>
            </label>

            <label className='flex items-start gap-2 cursor-pointer text-muted-foreground'>
              <input
                type='checkbox'
                checked={policyAccepted}
                onChange={(e) => setPolicyAccepted(e.target.checked)}
                className='mt-0.5'
              />
              <span>
                Tôi cam kết tuân thủ quy định sử dụng tài nguyên chung và bảo mật thông tin tài
                khoản.
              </span>
            </label>
          </div>

          <div className='flex justify-end'>
            <Button type='submit' disabled={submitting || Boolean(pendingReq)}>
              {submitting ? 'Đang gửi đơn...' : 'Gửi đơn xin cấp SSH'}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
