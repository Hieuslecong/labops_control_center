'use client';

import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter
} from '@/components/ui/dialog';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import { SecurityReviewStatus } from '@/types/labops';
import { toast } from 'sonner';
import { labopsCall } from '@/features/labops/api';

function getSecurityStatusBadge(status: SecurityReviewStatus) {
  switch (status) {
    case 'APPROVED':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-mono'
        >
          APPROVED
        </Badge>
      );
    case 'MANUAL_REVIEW':
      return (
        <Badge
          variant='outline'
          className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[10px] font-mono'
        >
          MANUAL REVIEW
        </Badge>
      );
    case 'QUARANTINED':
      return (
        <Badge
          variant='outline'
          className='bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30 text-[10px] font-mono'
        >
          QUARANTINED
        </Badge>
      );
    case 'REJECTED':
      return (
        <Badge
          variant='outline'
          className='bg-rose-600/10 text-rose-600 border-rose-600/30 text-[10px] font-mono'
        >
          REJECTED
        </Badge>
      );
    case 'SCANNING':
    default:
      return (
        <Badge
          variant='outline'
          className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[10px] font-mono'
        >
          SCANNING
        </Badge>
      );
  }
}

export function SecurityManagementView() {
  const reviews = useLabOpsStore((state) => state.securityReviews);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [repoUrl, setRepoUrl] = useState('');
  const [submitter, setSubmitter] = useState(''); // commit SHA (40 ký tự)

  const approvedCount = reviews.filter((r) => r.status === 'APPROVED').length;
  const manualCount = reviews.filter((r) => r.status === 'MANUAL_REVIEW').length;
  const quarantinedCount = reviews.filter((r) => r.status === 'QUARANTINED').length;

  const handleAddRepo = (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (!repoUrl) return;

    void (async () => {
      const r = await labopsCall<{ state?: string; error?: string }>('security/scans', {
        method: 'POST',
        body: { repo_url: repoUrl, commit_sha: submitter }
      });
      if (!r.ok) {
        toast.error('Không thể gửi yêu cầu quét', { description: r.error });
        return;
      }
      setDialogOpen(false);
      toast.info(`Đã tiếp nhận yêu cầu quét — trạng thái ${r.data?.state ?? 'SUBMITTED'}`, {
        description: r.data?.error ?? 'Kết quả sẽ cập nhật khi Trivy hoàn tất; không có kết quả nào được giả lập.'
      });
      setRepoUrl('');
    })();
  };

  return (
    <div className='flex flex-col gap-5'>
      {/* KPI Stats on Security */}
      <div className='grid grid-cols-2 sm:grid-cols-4 gap-3'>
        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Đã phê duyệt (Approved)
          </span>
          <div className='text-xl font-bold mt-0.5 text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5'>
            <Icons.check className='size-5' />
            {approvedCount} repo
          </div>
          <span className='text-[10px] text-muted-foreground'>Được phép chạy trên GPU</span>
        </Card>

        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Chờ Admin duyệt</span>
          <div className='text-xl font-bold mt-0.5 text-amber-600 dark:text-amber-400'>
            {manualCount} repo
          </div>
          <span className='text-[10px] text-muted-foreground'>Phát hiện tệp nhị phân lạ</span>
        </Card>

        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Đang cách ly (Quarantined)
          </span>
          <div className='text-xl font-bold mt-0.5 text-rose-600 dark:text-rose-400'>
            {quarantinedCount} repo
          </div>
          <span className='text-[10px] text-muted-foreground'>Chặn tải xuống host</span>
        </Card>

        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Sao lưu ZFS Snapshots
          </span>
          <div className='text-xl font-bold mt-0.5 text-blue-600 dark:text-blue-400 flex items-center gap-1.5'>
            <Icons.database className='size-5' />
            CHƯA CẤU HÌNH
          </div>
          <span className='text-[10px] text-muted-foreground'>Chưa có nguồn xác minh sao lưu (NOT_CONFIGURED)</span>
        </Card>
      </div>

      {/* Security Pipeline Workflow visual */}
      <Card className='border bg-card p-4'>
        <div className='flex flex-col gap-2'>
          <div className='flex items-center justify-between'>
            <h4 className='text-sm font-bold tracking-tight'>
              Quy trình Kiểm duyệt & Cách ly Sandbox 8 Bước
            </h4>
            <Badge variant='outline' className='text-[10px] font-mono'>
              ZERO-TRUST CODE EXECUTION
            </Badge>
          </div>
          <div className='grid grid-cols-2 sm:grid-cols-4 md:grid-cols-8 gap-1.5 pt-2 text-center text-xs'>
            {[
              { step: '1. Gửi Repo', desc: 'URL GitHub' },
              { step: '2. Commit SHA', desc: 'Khóa mã hash' },
              { step: '3. Cách ly', desc: 'Sandbox cách ly' },
              { step: '4. SAST Scan', desc: 'Quét lỗ hổng' },
              { step: '5. AI Review', desc: 'Phân tích code' },
              { step: '6. Policy', desc: 'Đánh giá luật' },
              { step: '7. Admin Duyệt', desc: 'Quyết định' },
              { step: '8. Sẵn sàng', desc: 'Nạp vào GPU' }
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

      {/* Repositories Review List */}
      <Card className='border bg-card'>
        <CardHeader className='pb-3 pt-4'>
          <div className='flex items-center justify-between'>
            <div>
              <CardTitle className='text-base font-bold'>
                Danh sách Repository Đang Kiểm duyệt
              </CardTitle>
              <CardDescription className='text-xs'>
                Kiểm tra tĩnh, phát hiện mã độc và kiểm soát an ninh nguồn mã nguồn AI
              </CardDescription>
            </div>
            <Button
              size='sm'
              onClick={() => setDialogOpen(true)}
              className='h-8 text-xs font-semibold gap-1.5 bg-primary'
            >
              <Icons.add className='size-3.5' /> Gửi Repo Kiểm duyệt
            </Button>
          </div>
        </CardHeader>

        <CardContent className='p-0'>
          <div className='overflow-x-auto'>
            <table className='w-full text-left text-xs'>
              <thead>
                <tr className='border-b text-muted-foreground font-semibold bg-muted/20'>
                  <th className='py-2.5 px-3'>Repository & Commit</th>
                  <th className='py-2.5 px-3'>Người gửi</th>
                  <th className='py-2.5 px-3'>Rủi ro (Risk Score)</th>
                  <th className='py-2.5 px-3'>Phát hiện (Findings)</th>
                  <th className='py-2.5 px-3'>Tóm tắt đánh giá an ninh</th>
                  <th className='py-2.5 px-3'>Trạng thái</th>
                  <th className='py-2.5 px-3 text-right'>Hành động Admin</th>
                </tr>
              </thead>
              <tbody className='divide-y divide-border/60'>
                {reviews.map((r) => (
                  <tr key={r.id} className='hover:bg-muted/30 transition-colors'>
                    <td className='py-3 px-3'>
                      <div className='font-bold font-mono text-foreground flex items-center gap-1.5'>
                        <Icons.github className='size-4 text-muted-foreground shrink-0' />
                        {r.repoName}
                      </div>
                      <div className='text-[11px] text-muted-foreground font-mono mt-0.5'>
                        Commit: <span className='text-foreground'>{r.commitSha}</span> •{' '}
                        {r.submittedAt}
                      </div>
                    </td>

                    <td className='py-3 px-3 font-mono text-[11px]'>@{r.submittedBy}</td>

                    <td className='py-3 px-3' aria-label='Điểm rủi ro'>
                      <div className='flex items-center gap-2'>
                        <span
                          className={`font-mono font-bold text-sm ${r.riskScore > 50 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'}`}
                        >
                          {r.riskScore}/100
                        </span>
                      </div>
                    </td>

                    <td className='py-3 px-3'>
                      <div className='flex items-center gap-1 text-[10px] font-mono'>
                        {r.findings.high > 0 && (
                          <Badge
                            variant='outline'
                            className='bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30 px-1 py-0'
                          >
                            {r.findings.high} High
                          </Badge>
                        )}
                        {r.findings.medium > 0 && (
                          <Badge
                            variant='outline'
                            className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 px-1 py-0'
                          >
                            {r.findings.medium} Med
                          </Badge>
                        )}
                        {r.findings.low > 0 && (
                          <Badge
                            variant='outline'
                            className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 px-1 py-0'
                          >
                            {r.findings.low} Low
                          </Badge>
                        )}
                      </div>
                    </td>

                    <td className='py-3 px-3 max-w-xs'>
                      <p className='text-[11px] text-muted-foreground line-clamp-2'>{r.summary}</p>
                      {r.reviewedBy && (
                        <span className='text-[10px] text-foreground font-medium block mt-0.5'>
                          Đã duyệt bởi: {r.reviewedBy}
                        </span>
                      )}
                    </td>

                    <td className='py-3 px-3'>{getSecurityStatusBadge(r.status)}</td>

                    <td className='py-3 px-3 text-right'>
                      <div className='flex items-center justify-end gap-1'>
                        {r.status !== 'APPROVED' && (
                          <Button
                            size='sm'
                            variant='outline'
                            onClick={() => {
                              void labopsCall(`security/scans/${encodeURIComponent(r.id)}/decision`, { method: 'POST', body: { decision: 'APPROVED' } }).then((res) => res.ok ? toast.success('Đã ghi nhận quyết định APPROVED') : toast.error('Không thể ghi nhận quyết định', { description: res.error }));
                            }}
                            className='h-7 text-xs text-emerald-600 hover:text-emerald-700 hover:bg-emerald-500/10'
                          >
                            Duyệt
                          </Button>
                        )}
                        {r.status !== 'REJECTED' && (
                          <Button
                            size='sm'
                            variant='ghost'
                            onClick={() => {
                              void labopsCall(`security/scans/${encodeURIComponent(r.id)}/decision`, { method: 'POST', body: { decision: 'REJECTED' } }).then((res) => res.ok ? toast.success('Đã ghi nhận quyết định REJECTED') : toast.error('Không thể ghi nhận quyết định', { description: res.error }));
                            }}
                            className='h-7 text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-500/10'
                          >
                            Từ chối
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Submit Repo Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className='sm:max-w-[460px]'>
          <form onSubmit={handleAddRepo}>
            <DialogHeader>
              <div className='flex items-center gap-2'>
                <div className='flex size-8 items-center justify-center rounded-lg bg-rose-500/10 text-rose-600 dark:text-rose-400'>
                  <Icons.shield className='size-5' />
                </div>
                <div>
                  <DialogTitle className='text-base font-bold'>
                    Gửi Repository vào Sandbox Quét
                  </DialogTitle>
                  <DialogDescription className='text-xs'>
                    Tự động cách ly, kiểm tra tệp thực thi độc hại và phát hiện socket ngoại vi
                  </DialogDescription>
                </div>
              </div>
            </DialogHeader>

            <div className='grid gap-3.5 py-4 text-xs'>
              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>URL GitHub Repository</Label>
                <Input
                  value={repoUrl}
                  onChange={(e) => setRepoUrl(e.target.value)}
                  placeholder='https://github.com/organization/repository'
                  className='h-8 text-xs font-mono'
                  required
                />
              </div>

              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>Commit SHA (40 ký tự hex)</Label>
                <Input
                  value={submitter}
                  onChange={(e) => setSubmitter(e.target.value)}
                  placeholder='ví dụ: 3f2a…(đủ 40 ký tự)'
                  pattern='[0-9a-fA-F]{40}'
                  className='h-8 text-xs font-mono'
                  required
                />
              </div>

              <div className='p-2.5 rounded-lg border bg-muted/20 text-[11px] text-muted-foreground'>
                Kho mã được quét tĩnh bằng Trivy (lỗ hổng, secret, cấu hình sai) tại đúng commit đã
                nêu; không chạy bất kỳ script nào của repo. Lưu ý: kết quả chỉ áp dụng cho commit đã
                quét — mã bị sửa sau đó qua SSH không được bao phủ.
              </div>
            </div>

            <DialogFooter className='gap-2 sm:gap-0'>
              <Button
                type='button'
                variant='outline'
                size='sm'
                onClick={() => setDialogOpen(false)}
                className='text-xs'
              >
                Hủy
              </Button>
              <Button type='submit' size='sm' className='text-xs font-semibold bg-primary'>
                Bắt đầu quét Sandbox
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
