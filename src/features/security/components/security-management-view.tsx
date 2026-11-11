'use client';

import React, { useEffect, useState, useTransition } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter
} from '@/components/ui/dialog';
import { Icons } from '@/components/icons';
import { toast } from 'sonner';
import { labopsCall } from '@/features/labops/api';
import { RepositoryGuardRequest, SecurityReviewStatus } from '@/types/labops';

function getSecurityStatusBadge(status: SecurityReviewStatus | string) {
  switch (status) {
    case 'AUTO_APPROVED':
    case 'APPROVED':
    case 'MANUALLY_APPROVED':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-mono font-semibold'
        >
          {status === 'AUTO_APPROVED' ? 'TỰ ĐỘNG PHÊ DUYỆT' : 'ADMIN ĐÃ DUYỆT'}
        </Badge>
      );
    case 'REVIEW_REQUIRED':
    case 'MANUAL_REVIEW':
      return (
        <Badge
          variant='outline'
          className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[10px] font-mono font-semibold'
        >
          CẦN ADMIN DUYỆT
        </Badge>
      );
    case 'REJECTED':
      return (
        <Badge
          variant='outline'
          className='bg-rose-600/10 text-rose-600 border-rose-600/30 text-[10px] font-mono font-semibold'
        >
          TỪ CHỐI
        </Badge>
      );
    case 'FAILED':
      return (
        <Badge
          variant='outline'
          className='bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/30 text-[10px] font-mono'
        >
          THẤT BẠI
        </Badge>
      );
    case 'DELIVERED':
      return (
        <Badge
          variant='outline'
          className='bg-teal-500/10 text-teal-700 dark:text-teal-400 border-teal-500/30 text-[10px] font-mono font-semibold'
        >
          ĐÃ PHÂN PHỐI
        </Badge>
      );
    case 'FETCHING':
    case 'SCANNING':
    case 'POLICY_EVALUATION':
    case 'RESOLVING_COMMIT':
      return (
        <Badge
          variant='outline'
          className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[10px] font-mono animate-pulse'
        >
          ĐANG QUÉT & CÁCH LY
        </Badge>
      );
    default:
      return (
        <Badge variant='outline' className='text-[10px] font-mono'>
          {status}
        </Badge>
      );
  }
}

export function SecurityManagementView() {
  const [requests, setRequests] = useState<RepositoryGuardRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [isPending, startTransition] = useTransition();

  // Modals
  const [submitDialogOpen, setSubmitDialogOpen] = useState(false);
  const [selectedReq, setSelectedReq] = useState<RepositoryGuardRequest | null>(null);
  const [decisionModalOpen, setDecisionModalOpen] = useState(false);
  const [decisionType, setDecisionType] = useState<'APPROVED' | 'REJECTED'>('APPROVED');
  const [decisionReason, setDecisionReason] = useState('');

  // Form submit state
  const [repoUrl, setRepoUrl] = useState('');
  const [ref, setRef] = useState('HEAD');

  const fetchRequests = async () => {
    setLoading(true);
    const res = await labopsCall<RepositoryGuardRequest[]>('repositories/requests');
    if (res.ok && Array.isArray(res.data)) {
      setRequests(res.data);
    }
    setLoading(false);
  };

  useEffect(() => {
    void fetchRequests();
    const timer = setInterval(() => {
      void fetchRequests();
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  // KPIs
  const autoApprovedCount = requests.filter(
    (r) => r.state === 'AUTO_APPROVED' || r.state === 'DELIVERED'
  ).length;
  const reviewRequiredCount = requests.filter((r) => r.state === 'REVIEW_REQUIRED').length;
  const rejectedCount = requests.filter((r) => r.state === 'REJECTED').length;
  const failedCount = requests.filter((r) => r.state === 'FAILED').length;
  const cacheHitCount = requests.filter((r) => r.cache_hit).length;

  const handleSubmit = (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (!repoUrl) return;

    startTransition(async () => {
      const res = await labopsCall<RepositoryGuardRequest>('repositories/requests', {
        method: 'POST',
        body: { repo_url: repoUrl, ref: ref || 'HEAD' }
      });

      if (!res.ok) {
        toast.error('Không thể gửi yêu cầu', { description: res.error });
        return;
      }

      toast.success('Đã tiếp nhận yêu cầu kiểm duyệt an toàn', {
        description: `Mã yêu cầu: ${res.data?.id}. Hệ thống đang tải về quarantine và quét tĩnh.`
      });
      setSubmitDialogOpen(false);
      setRepoUrl('');
      setRef('HEAD');
      void fetchRequests();
    });
  };

  const handleDecisionSubmit = () => {
    if (!selectedReq) return;
    if (!decisionReason.trim()) {
      toast.error('Vui lòng nhập lý do phê duyệt hoặc từ chối');
      return;
    }

    startTransition(async () => {
      const res = await labopsCall(`repositories/requests/${selectedReq.id}/decision`, {
        method: 'POST',
        body: { decision: decisionType, reason: decisionReason }
      });

      if (!res.ok) {
        toast.error('Không thể lưu quyết định quản trị', { description: res.error });
        return;
      }

      toast.success(
        `Đã ghi nhận quyết định ${decisionType === 'APPROVED' ? 'PHÊ DUYỆT' : 'TỪ CHỐI'} cho ${selectedReq.repo_name}`
      );
      setDecisionModalOpen(false);
      setDecisionReason('');
      void fetchRequests();
    });
  };

  return (
    <div className='flex flex-col gap-5'>
      {/* KPI Stats */}
      <div className='grid grid-cols-2 sm:grid-cols-5 gap-3'>
        <Card className='p-3.5 bg-card border shadow-xs'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Tự động duyệt (Auto-Approved)
          </span>
          <div className='text-xl font-bold mt-0.5 text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5'>
            <Icons.check className='size-5' />
            {autoApprovedCount}
          </div>
          <span className='text-[10px] text-muted-foreground'>Đạt 100% tiêu chuẩn chính sách</span>
        </Card>

        <Card className='p-3.5 bg-card border shadow-xs'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Chờ Admin duyệt (Review)
          </span>
          <div className='text-xl font-bold mt-0.5 text-amber-600 dark:text-amber-400 flex items-center gap-1.5'>
            <Icons.warning className='size-5' />
            {reviewRequiredCount}
          </div>
          <span className='text-[10px] text-muted-foreground'>Phát hiện rủi ro mức High</span>
        </Card>

        <Card className='p-3.5 bg-card border shadow-xs'>
          <span className='text-[11px] text-muted-foreground font-medium'>Từ chối (Rejected)</span>
          <div className='text-xl font-bold mt-0.5 text-rose-600 dark:text-rose-400 flex items-center gap-1.5'>
            <Icons.close className='size-5' />
            {rejectedCount}
          </div>
          <span className='text-[10px] text-muted-foreground'>Phát hiện Secrets hoặc Critical</span>
        </Card>

        <Card className='p-3.5 bg-card border shadow-xs'>
          <span className='text-[11px] text-muted-foreground font-medium'>Lỗi / Thất bại</span>
          <div className='text-xl font-bold mt-0.5 text-red-600 dark:text-red-400 flex items-center gap-1.5'>
            <Icons.alertCircle className='size-5' />
            {failedCount}
          </div>
          <span className='text-[10px] text-muted-foreground'>Fail-closed an toàn</span>
        </Card>

        <Card className='p-3.5 bg-card border shadow-xs'>
          <span className='text-[11px] text-muted-foreground font-medium'>Cache Hit</span>
          <div className='text-xl font-bold mt-0.5 text-blue-600 dark:text-blue-400 flex items-center gap-1.5'>
            <Icons.database className='size-5' />
            {cacheHitCount} lượt
          </div>
          <span className='text-[10px] text-muted-foreground'>Tái sử dụng kết quả xác minh</span>
        </Card>
      </div>

      {/* Visual Workflow Steps */}
      <Card className='border bg-card p-4 shadow-xs'>
        <div className='flex flex-col gap-2'>
          <div className='flex items-center justify-between'>
            <h4 className='text-sm font-bold tracking-tight'>
              Cổng Kiểm duyệt Mã nguồn Tự động (Repository Security Gateway)
            </h4>
            <Badge variant='outline' className='text-[10px] font-mono'>
              ZERO-TRUST REPOSITORY EGRESS
            </Badge>
          </div>
          <div className='grid grid-cols-2 sm:grid-cols-4 md:grid-cols-6 gap-2 pt-1 text-center text-xs'>
            {[
              { step: '1. Gửi Repo & Ref', desc: 'Xác minh HTTPS URL' },
              { step: '2. Pin Commit SHA', desc: 'Chống TOCTOU Attack' },
              { step: '3. Quarantine Download', desc: 'Cô lập No-Exec, Quota' },
              { step: '4. Trivy & Scorecard', desc: 'Quét CVE, Secrets, SAST' },
              { step: '5. Auto-Policy Engine', desc: 'Tự duyệt hoặc báo Admin' },
              { step: '6. Verified Delivery', desc: 'Phân phối vào Workspace' }
            ].map((s) => (
              <div
                key={s.step}
                className='p-2 rounded-lg border bg-muted/20 flex flex-col items-center justify-center'
              >
                <span className='font-bold text-[11px] text-foreground'>{s.step}</span>
                <span className='text-[10px] text-muted-foreground'>{s.desc}</span>
              </div>
            ))}
          </div>
        </div>
      </Card>

      {/* Requests Table */}
      <Card className='border bg-card shadow-xs'>
        <CardHeader className='pb-3 pt-4 border-b bg-muted/10'>
          <div className='flex items-center justify-between'>
            <div>
              <CardTitle className='text-base font-bold'>
                Yêu cầu Kiểm duyệt & Phân phối Kho mã
              </CardTitle>
              <CardDescription className='text-xs'>
                Kiểm soát luồng nạp mã nguồn vào Docker Workspace, phát hiện rủi ro cung ứng và ngăn
                chặn clone trực tiếp
              </CardDescription>
            </div>
            <div className='flex items-center gap-2'>
              <Button
                variant='outline'
                size='sm'
                onClick={() => void fetchRequests()}
                disabled={loading}
                className='h-8 text-xs gap-1.5'
              >
                <Icons.refresh className={`size-3.5 ${loading ? 'animate-spin' : ''}`} /> Làm mới
              </Button>
              <Button
                size='sm'
                onClick={() => setSubmitDialogOpen(true)}
                className='h-8 text-xs font-semibold gap-1.5 bg-primary'
              >
                <Icons.add className='size-3.5' /> Gửi Repo Kiểm duyệt
              </Button>
            </div>
          </div>
        </CardHeader>

        <CardContent className='p-0'>
          <div className='overflow-x-auto'>
            <table className='w-full text-left text-xs'>
              <thead>
                <tr className='border-b text-muted-foreground font-semibold bg-muted/20'>
                  <th className='py-2.5 px-3'>Mã yêu cầu & Repository</th>
                  <th className='py-2.5 px-3'>Commit SHA</th>
                  <th className='py-2.5 px-3'>Người gửi</th>
                  <th className='py-2.5 px-3'>Phát hiện rủi ro</th>
                  <th className='py-2.5 px-3'>Trạng thái</th>
                  <th className='py-2.5 px-3'>Thời gian</th>
                  <th className='py-2.5 px-3 text-right'>Hành động</th>
                </tr>
              </thead>
              <tbody className='divide-y divide-border/60'>
                {requests.length === 0 ? (
                  <tr>
                    <td colSpan={7} className='py-8 text-center text-muted-foreground'>
                      Chưa có yêu cầu kiểm duyệt kho mã nào. Bấm &quot;Gửi Repo Kiểm duyệt&quot; hoặc
                      sử dụng CLI `labops clone` trong terminal.
                    </td>
                  </tr>
                ) : (
                  requests.map((r) => (
                    <tr key={r.id} className='hover:bg-muted/30 transition-colors'>
                      <td className='py-3 px-3'>
                        <div className='font-bold font-mono text-foreground flex items-center gap-1.5'>
                          <Icons.github className='size-4 text-muted-foreground shrink-0' />
                          {r.repo_name || r.canonical_repo}
                        </div>
                        <div className='text-[10px] text-muted-foreground font-mono mt-0.5 truncate max-w-xs'>
                          {r.canonical_repo}
                        </div>
                      </td>

                      <td className='py-3 px-3 font-mono text-[11px]'>
                        {r.commit_sha ? (
                          <span className='bg-muted px-1.5 py-0.5 rounded-sm'>
                            {r.commit_sha.slice(0, 8)}
                          </span>
                        ) : (
                          <span className='text-muted-foreground italic'>Đang phân giải...</span>
                        )}
                      </td>

                      <td className='py-3 px-3 font-mono text-[11px]'>@{r.submitted_by}</td>

                      <td className='py-3 px-3'>
                        {r.report ? (
                          <div className='flex items-center gap-1 text-[10px] font-mono'>
                            {r.report.secrets_count > 0 && (
                              <Badge
                                variant='outline'
                                className='bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/40 px-1 py-0'
                              >
                                {r.report.secrets_count} Secret
                              </Badge>
                            )}
                            {r.report.vulnerabilities.critical > 0 && (
                              <Badge
                                variant='outline'
                                className='bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30 px-1 py-0'
                              >
                                {r.report.vulnerabilities.critical} Crit
                              </Badge>
                            )}
                            {r.report.vulnerabilities.high > 0 && (
                              <Badge
                                variant='outline'
                                className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 px-1 py-0'
                              >
                                {r.report.vulnerabilities.high} High
                              </Badge>
                            )}
                            {r.report.secrets_count === 0 &&
                              r.report.vulnerabilities.critical === 0 &&
                              r.report.vulnerabilities.high === 0 && (
                                <Badge
                                  variant='outline'
                                  className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 px-1 py-0'
                                >
                                  Sạch (Clean)
                                </Badge>
                              )}
                          </div>
                        ) : (
                          <span className='text-muted-foreground text-[10px]'>Chưa có dữ liệu</span>
                        )}
                      </td>

                      <td className='py-3 px-3'>{getSecurityStatusBadge(r.state)}</td>

                      <td className='py-3 px-3 text-[11px] text-muted-foreground'>
                        {r.submitted_at ? new Date(r.submitted_at).toLocaleTimeString() : '—'}
                      </td>

                      <td className='py-3 px-3 text-right'>
                        <div className='flex items-center justify-end gap-1.5'>
                          <Button
                            size='sm'
                            variant='outline'
                            onClick={() => setSelectedReq(r)}
                            className='h-7 text-xs px-2'
                          >
                            Chi tiết
                          </Button>
                          {r.state === 'REVIEW_REQUIRED' && (
                            <Button
                              size='sm'
                              onClick={() => {
                                setSelectedReq(r);
                                setDecisionType('APPROVED');
                                setDecisionModalOpen(true);
                              }}
                              className='h-7 text-xs px-2 bg-emerald-600 hover:bg-emerald-700 text-white'
                            >
                              Duyệt
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Detail Dialog */}
      {selectedReq && (
        <Dialog open={Boolean(selectedReq)} onOpenChange={() => setSelectedReq(null)}>
          <DialogContent className='sm:max-w-[620px] max-h-[85vh] overflow-y-auto'>
            <DialogHeader>
              <DialogTitle className='text-base font-bold flex items-center gap-2'>
                <Icons.shield className='size-5 text-primary' /> Chi tiết Kiểm duyệt Repository
              </DialogTitle>
              <DialogDescription className='text-xs'>
                Mã yêu cầu: <span className='font-mono text-foreground'>{selectedReq.id}</span>
              </DialogDescription>
            </DialogHeader>

            <div className='flex flex-col gap-3 py-2 text-xs'>
              <div className='grid grid-cols-2 gap-2 p-2.5 rounded-lg border bg-muted/20'>
                <div>
                  <span className='text-[10px] text-muted-foreground block'>Kho lưu trữ</span>
                  <span className='font-mono font-semibold'>{selectedReq.canonical_repo}</span>
                </div>
                <div>
                  <span className='text-[10px] text-muted-foreground block'>Commit SHA</span>
                  <span className='font-mono font-semibold truncate block'>
                    {selectedReq.commit_sha || 'N/A'}
                  </span>
                </div>
                <div>
                  <span className='text-[10px] text-muted-foreground block'>Người gửi</span>
                  <span className='font-mono'>@{selectedReq.submitted_by}</span>
                </div>
                <div>
                  <span className='text-[10px] text-muted-foreground block'>Trạng thái</span>
                  <div className='mt-0.5'>{getSecurityStatusBadge(selectedReq.state)}</div>
                </div>
              </div>

              {selectedReq.artifact_digest && (
                <div className='p-2 rounded-md bg-muted/30 border font-mono text-[11px]'>
                  <span className='text-muted-foreground block text-[10px] font-sans'>
                    Chữ ký SHA-256 Artifact Digest:
                  </span>
                  <span className='text-emerald-700 dark:text-emerald-400 break-all'>
                    {selectedReq.artifact_digest}
                  </span>
                </div>
              )}

              {selectedReq.report && (
                <div className='flex flex-col gap-2'>
                  <h5 className='font-semibold text-xs'>Lý do đánh giá chính sách bảo mật:</h5>
                  <ul className='list-disc pl-4 space-y-1 text-muted-foreground'>
                    {selectedReq.report.policy_reasons.map((reason, idx) => (
                      <li key={idx} className='text-[11px]'>
                        {reason}
                      </li>
                    ))}
                  </ul>

                  {selectedReq.report.findings && selectedReq.report.findings.length > 0 && (
                    <div className='mt-2'>
                      <h5 className='font-semibold text-xs mb-1.5'>
                        Phát hiện bảo mật chi tiết ({selectedReq.report.findings.length}):
                      </h5>
                      <div className='max-h-40 overflow-y-auto divide-y border rounded-md text-[11px]'>
                        {selectedReq.report.findings.map((f, i) => (
                          <div key={i} className='p-2 flex flex-col gap-0.5 bg-card'>
                            <div className='flex items-center justify-between'>
                              <span className='font-mono font-semibold text-foreground'>
                                {f.id}
                              </span>
                              <Badge
                                variant='outline'
                                className={
                                  f.severity === 'CRITICAL'
                                    ? 'bg-rose-500/10 text-rose-600 border-rose-500/30'
                                    : 'bg-amber-500/10 text-amber-600 border-amber-500/30'
                                }
                              >
                                {f.severity}
                              </Badge>
                            </div>
                            <span className='text-muted-foreground'>{f.title}</span>
                            <span className='text-[10px] font-mono text-muted-foreground truncate'>
                              Tệp: {f.target_file}
                            </span>
                            {f.masked_value && (
                              <span className='text-[10px] font-mono text-rose-600 dark:text-rose-400'>
                                Giá trị: {f.masked_value}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {selectedReq.error && (
                <div className='p-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400 text-xs'>
                  <span className='font-semibold block'>Lỗi:</span>
                  {selectedReq.error}
                </div>
              )}
            </div>

            <DialogFooter className='gap-2 sm:gap-0'>
              {selectedReq.state === 'REVIEW_REQUIRED' && (
                <div className='flex items-center gap-2 mr-auto'>
                  <Button
                    size='sm'
                    onClick={() => {
                      setDecisionType('APPROVED');
                      setDecisionModalOpen(true);
                    }}
                    className='bg-emerald-600 hover:bg-emerald-700 text-white text-xs'
                  >
                    Duyệt ngoại lệ
                  </Button>
                  <Button
                    size='sm'
                    variant='destructive'
                    onClick={() => {
                      setDecisionType('REJECTED');
                      setDecisionModalOpen(true);
                    }}
                    className='text-xs'
                  >
                    Từ chối
                  </Button>
                </div>
              )}
              <Button
                variant='outline'
                size='sm'
                onClick={() => setSelectedReq(null)}
                className='text-xs'
              >
                Đóng
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {/* Admin Decision Reason Modal */}
      <Dialog open={decisionModalOpen} onOpenChange={setDecisionModalOpen}>
        <DialogContent className='sm:max-w-[420px]'>
          <DialogHeader>
            <DialogTitle className='text-base font-bold'>
              {decisionType === 'APPROVED' ? 'Phê duyệt Ngoại lệ' : 'Từ chối Kho mã'}
            </DialogTitle>
            <DialogDescription className='text-xs'>
              Quyết định này sẽ được ghi nhận vào nhật ký kiểm toán bất biến (Audit Log).
            </DialogDescription>
          </DialogHeader>

          <div className='space-y-2 py-3'>
            <Label className='text-xs font-semibold'>Lý do quyết định của Quản trị viên</Label>
            <Textarea
              value={decisionReason}
              onChange={(e) => setDecisionReason(e.target.value)}
              placeholder='Ví dụ: Đã kiểm tra phát hiện CVE và xác nhận không ảnh hưởng môi trường container LabOps...'
              className='text-xs min-h-[90px]'
              required
            />
          </div>

          <DialogFooter className='gap-2 sm:gap-0'>
            <Button
              variant='outline'
              size='sm'
              onClick={() => setDecisionModalOpen(false)}
              className='text-xs'
            >
              Hủy
            </Button>
            <Button
              size='sm'
              onClick={handleDecisionSubmit}
              disabled={isPending}
              className={`text-xs ${decisionType === 'APPROVED' ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-destructive'}`}
            >
              {isPending ? 'Đang lưu...' : 'Xác nhận Quyết định'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Submit Repo Dialog */}
      <Dialog open={submitDialogOpen} onOpenChange={setSubmitDialogOpen}>
        <DialogContent className='sm:max-w-[460px]'>
          <form onSubmit={handleSubmit}>
            <DialogHeader>
              <div className='flex items-center gap-2'>
                <div className='flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary'>
                  <Icons.shield className='size-5' />
                </div>
                <div>
                  <DialogTitle className='text-base font-bold'>
                    Gửi Repository vào Security Gateway
                  </DialogTitle>
                  <DialogDescription className='text-xs'>
                    Tự động cách ly, quét tĩnh Trivy và áp dụng chính sách kiểm duyệt
                  </DialogDescription>
                </div>
              </div>
            </DialogHeader>

            <div className='grid gap-3.5 py-4 text-xs'>
              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>URL GitHub Repository (HTTPS)</Label>
                <Input
                  value={repoUrl}
                  onChange={(e) => setRepoUrl(e.target.value)}
                  placeholder='https://github.com/organization/repository'
                  className='h-8 text-xs font-mono'
                  required
                />
              </div>

              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>Nhánh, Tag hoặc Commit SHA</Label>
                <Input
                  value={ref}
                  onChange={(e) => setRef(e.target.value)}
                  placeholder='main, v1.0.0 hoặc commit SHA'
                  className='h-8 text-xs font-mono'
                />
              </div>

              <div className='p-2.5 rounded-lg border bg-muted/20 text-[11px] text-muted-foreground'>
                Hệ thống sẽ tải snapshot về vùng Quarantine cô lập (No-Exec, giới hạn 500MB), phân
                tích tĩnh các lỗ hổng CVE và phát hiện rò rỉ khóa bí mật. Nếu đạt chính sách an ninh,
                mã nguồn sẽ được tự động phân phối vào workspace.
              </div>
            </div>

            <DialogFooter className='gap-2 sm:gap-0'>
              <Button
                type='button'
                variant='outline'
                size='sm'
                onClick={() => setSubmitDialogOpen(false)}
                className='text-xs'
              >
                Hủy
              </Button>
              <Button
                type='submit'
                size='sm'
                disabled={isPending}
                className='text-xs font-semibold bg-primary'
              >
                {isPending ? 'Đang gửi...' : 'Bắt đầu kiểm duyệt'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
