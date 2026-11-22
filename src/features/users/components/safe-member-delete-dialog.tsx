'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Icons } from '@/components/icons';
import { toast } from 'sonner';
import { labopsCall } from '@/features/labops/api';
import type { LabMember } from '@/types/labops';

interface DeletePreview {
  dry_run?: boolean;
  member_id: string;
  username: string;
  current_status: string;
  eligible_for_deletion: boolean;
  blocking_reasons: string[];
  active_workloads?: {
    has_active_workloads?: boolean;
    active_processes?: unknown[];
    active_reservations?: unknown[];
  };
  resources?: {
    wireguard_devices_count?: number;
    ssh_keys_count?: number;
    workspaces_count?: number;
    workspace_files_count?: number;
    workspace_total_bytes?: number;
  };
  operation_id?: string;
}

export interface SafeMemberDeleteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  member: LabMember | null;
  onDeleted: () => Promise<void> | void;
  onMemberUpdated?: () => Promise<void> | void;
}

function formatBytes(bytes: number | undefined): string {
  const value = Number(bytes || 0);
  if (value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const idx = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / Math.pow(1024, idx)).toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

export function SafeMemberDeleteDialog({
  open,
  onOpenChange,
  member,
  onDeleted,
  onMemberUpdated
}: SafeMemberDeleteDialogProps) {
  const [preview, setPreview] = useState<DeletePreview | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [suspendReason, setSuspendReason] = useState('Admin chuẩn bị thu hồi tài khoản');
  const [confirmUsername, setConfirmUsername] = useState('');
  const [adminPassword, setAdminPassword] = useState('');
  const [removeFiles, setRemoveFiles] = useState(false);
  const [actionBusy, setActionBusy] = useState<'suspend' | 'archive' | 'delete' | null>(null);

  const loadPreview = useCallback(async (memberId: string) => {
    setLoadingPreview(true);
    setPreviewError(null);
    const res = await labopsCall<DeletePreview>(
      `v2/members/${encodeURIComponent(memberId)}/delete-preview`
    );
    if (res.ok && res.data) {
      setPreview(res.data);
      setPreviewError(null);
    } else {
      setPreviewError(res.error || 'Không thể tải thông tin kiểm tra trước khi xóa.');
    }
    setLoadingPreview(false);
  }, []);

  useEffect(() => {
    if (!open || !member?.id) {
      return;
    }
    let cancelled = false;
    void (async () => {
      const res = await labopsCall<DeletePreview>(
        `v2/members/${encodeURIComponent(member.id)}/delete-preview`
      );
      if (cancelled) return;
      if (res.ok && res.data) {
        setPreview(res.data);
        setPreviewError(null);
      } else {
        setPreview(null);
        setPreviewError(res.error || 'Không thể tải thông tin kiểm tra trước khi xóa.');
      }
      setLoadingPreview(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [open, member?.id]);

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      setConfirmUsername('');
      setAdminPassword('');
      setRemoveFiles(false);
      setPreviewError(null);
    } else if (member?.id) {
      setLoadingPreview(true);
      setPreview(null);
    }
    onOpenChange(nextOpen);
  };

  if (!member) return null;

  const effectivePreview = preview?.member_id === member.id ? preview : null;
  const isPreviewLoading = loadingPreview || (open && !effectivePreview && previewError === null);
  const currentStatus = effectivePreview?.current_status || member.status || 'UNKNOWN';
  const isArchived =
    currentStatus === 'ARCHIVED' ||
    currentStatus === 'DELETE_ELIGIBLE' ||
    Boolean(member.archived_at);
  const isSuspendedOrArchived = currentStatus === 'SUSPENDED' || isArchived;
  const activeProcessesCount = effectivePreview?.active_workloads?.active_processes?.length ?? 0;
  const activeReservationsCount =
    effectivePreview?.active_workloads?.active_reservations?.length ?? 0;
  const hasActiveWorkloads = Boolean(effectivePreview?.active_workloads?.has_active_workloads);
  const canDelete = Boolean(effectivePreview?.eligible_for_deletion);
  const usernameMatched = confirmUsername.trim() === member.username;

  const handleSuspend = async () => {
    setActionBusy('suspend');
    const res = await labopsCall(`v2/members/${encodeURIComponent(member.id)}/suspend`, {
      method: 'POST',
      idempotent: true,
      body: { reason: suspendReason.trim() || 'Admin chuẩn bị thu hồi tài khoản' }
    });
    setActionBusy(null);
    if (!res.ok) {
      toast.error('Không thể tạm ngưng tài khoản', { description: res.error });
      return;
    }
    toast.success(`Đã tạm ngưng và thu hồi phiên của @${member.username}`);
    await loadPreview(member.id);
    await onMemberUpdated?.();
  };

  const handleArchive = async () => {
    setActionBusy('archive');
    const res = await labopsCall(`v2/members/${encodeURIComponent(member.id)}/archive`, {
      method: 'POST',
      idempotent: true,
      body: { retain_backup: true }
    });
    setActionBusy(null);
    if (!res.ok) {
      toast.error('Không thể niêm phong & archive workspace', { description: res.error });
      return;
    }
    toast.success(`Đã niêm phong & lưu trữ dữ liệu workspace @${member.username}`);
    await loadPreview(member.id);
    await onMemberUpdated?.();
  };

  const handleConfirmDelete = async () => {
    if (!canDelete) {
      toast.error('Thành viên chưa đủ điều kiện xóa (cần Suspend và Archive trước).');
      return;
    }
    if (!usernameMatched) {
      toast.error(`Vui lòng nhập chính xác username '${member.username}'.`);
      return;
    }
    if (!adminPassword) {
      toast.error('Vui lòng nhập mật khẩu Quản trị viên để xác thực Step-Up.');
      return;
    }

    setActionBusy('delete');
    const stepRes = await labopsCall<{ step_up_token?: string }>('auth/step-up', {
      method: 'POST',
      body: {
        password: adminPassword,
        action: 'DELETE_MEMBER',
        target_id: member.id
      }
    });

    if (!stepRes.ok || !stepRes.data?.step_up_token) {
      setActionBusy(null);
      toast.error('Xác thực Step-Up Admin thất bại', { description: stepRes.error });
      return;
    }

    const query = `confirm_username=${encodeURIComponent(confirmUsername.trim())}&remove_files=${
      removeFiles ? 'true' : 'false'
    }`;
    const delRes = await labopsCall(`v2/members/${encodeURIComponent(member.id)}?${query}`, {
      method: 'DELETE',
      idempotent: true,
      headers: {
        'X-LabOps-Step-Up-Token': stepRes.data.step_up_token
      }
    });
    setActionBusy(null);

    if (!delRes.ok) {
      toast.error('Không thể xóa vĩnh viễn tài khoản', { description: delRes.error });
      await loadPreview(member.id);
      return;
    }

    toast.success(`Đã xóa vĩnh viễn tài khoản @${member.username}`);
    setConfirmUsername('');
    setAdminPassword('');
    setRemoveFiles(false);
    handleOpenChange(false);
    await onDeleted();
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className='sm:max-w-xl bg-background border text-foreground max-h-[90vh] overflow-y-auto'>
        <DialogHeader>
          <div className='flex items-center gap-2.5'>
            <div className='p-2 rounded-full bg-rose-500/10 text-rose-600 dark:text-rose-400'>
              <Icons.warning className='size-5' />
            </div>
            <div className='min-w-0'>
              <DialogTitle className='text-base font-bold text-rose-600 dark:text-rose-400'>
                Quy trình Thu hồi &amp; Xóa An toàn @{member.username}
              </DialogTitle>
              <DialogDescription className='text-xs text-muted-foreground'>
                Bắt buộc thực hiện tuần tự 3 bước: Tạm ngưng (Suspend) → Niêm phong (Archive) → Xác
                thực Step-Up để Xóa vĩnh viễn.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className='space-y-4 text-xs'>
          {/* Trạng thái & Kiểm kê tài nguyên */}
          <div className='rounded-lg border bg-muted/20 p-3 space-y-2.5'>
            <div className='flex flex-wrap items-center justify-between gap-2'>
              <div className='font-semibold text-foreground'>
                Thành viên: {member.fullName}{' '}
                <span className='font-mono text-muted-foreground'>(@{member.username})</span>
              </div>
              <div className='flex items-center gap-2'>
                <Badge variant='outline' className='font-mono text-xs'>
                  {currentStatus}
                </Badge>
                <Button
                  type='button'
                  size='sm'
                  variant='ghost'
                  className='h-7 text-xs px-2'
                  onClick={() => void loadPreview(member.id)}
                  disabled={isPreviewLoading}
                >
                  <Icons.refresh
                    className={`size-3.5 mr-1 ${isPreviewLoading ? 'animate-spin' : ''}`}
                  />
                  Kiểm tra lại
                </Button>
              </div>
            </div>

            {previewError && (
              <div className='rounded border border-rose-500/30 bg-rose-500/10 p-2 text-rose-700 dark:text-rose-300'>
                {previewError}
              </div>
            )}

            <div className='grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1'>
              <div className='rounded border bg-background p-2'>
                <div className='text-muted-foreground'>WireGuard</div>
                <div className='font-mono font-semibold text-foreground'>
                  {effectivePreview?.resources?.wireguard_devices_count ?? '—'} thiết bị
                </div>
              </div>
              <div className='rounded border bg-background p-2'>
                <div className='text-muted-foreground'>Khóa SSH</div>
                <div className='font-mono font-semibold text-foreground'>
                  {effectivePreview?.resources?.ssh_keys_count ?? '—'} khóa
                </div>
              </div>
              <div className='rounded border bg-background p-2'>
                <div className='text-muted-foreground'>Workspace</div>
                <div className='font-mono font-semibold text-foreground'>
                  {effectivePreview?.resources?.workspaces_count ?? '—'} container
                </div>
              </div>
              <div className='rounded border bg-background p-2'>
                <div className='text-muted-foreground'>Dữ liệu đĩa</div>
                <div className='font-mono font-semibold text-foreground'>
                  {effectivePreview?.resources
                    ? `${effectivePreview.resources.workspace_files_count ?? 0} tệp (${formatBytes(effectivePreview.resources.workspace_total_bytes)})`
                    : '—'}
                </div>
              </div>
            </div>

            {hasActiveWorkloads && (
              <div className='rounded border border-amber-500/40 bg-amber-500/10 p-2.5 text-amber-800 dark:text-amber-300'>
                Đang phát hiện <strong>{activeProcessesCount}</strong> tiến trình và{' '}
                <strong>{activeReservationsCount}</strong> lượt giữ GPU đang chạy. Hãy dừng các tác
                vụ này trước khi Archive.
              </div>
            )}

            {effectivePreview?.blocking_reasons && effectivePreview.blocking_reasons.length > 0 && (
              <ul className='list-disc pl-4 space-y-0.5 text-amber-700 dark:text-amber-300'>
                {effectivePreview.blocking_reasons.map((reason, idx) => (
                  <li key={idx}>{reason}</li>
                ))}
              </ul>
            )}
          </div>

          {/* Bước 1: SUSPEND */}
          <div className='rounded-lg border p-3 space-y-2.5'>
            <div className='flex items-center justify-between gap-2'>
              <div className='font-semibold text-foreground'>
                Bước 1 — Tạm ngưng &amp; Thu hồi phiên (SUSPEND)
              </div>
              <Badge
                variant='outline'
                className={
                  isSuspendedOrArchived
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 text-xs'
                    : 'text-xs'
                }
              >
                {isSuspendedOrArchived ? 'HOÀN TẤT' : 'CHƯA THỰC HIỆN'}
              </Badge>
            </div>
            <p className='text-muted-foreground'>
              Thu hồi toàn bộ token đăng nhập, khóa SSH và kết nối WireGuard VPN trước khi đóng gói
              workspace.
            </p>
            {!isSuspendedOrArchived && (
              <div className='flex flex-col sm:flex-row gap-2 pt-1'>
                <Input
                  value={suspendReason}
                  onChange={(e) => setSuspendReason(e.target.value)}
                  placeholder='Lý do tạm ngưng tài khoản...'
                  className='text-xs h-8'
                />
                <Button
                  type='button'
                  size='sm'
                  variant='outline'
                  className='h-8 text-xs shrink-0'
                  disabled={actionBusy !== null}
                  onClick={() => void handleSuspend()}
                >
                  {actionBusy === 'suspend' ? 'Đang tạm ngưng...' : 'Thực hiện Suspend'}
                </Button>
              </div>
            )}
          </div>

          {/* Bước 2: ARCHIVE */}
          <div className='rounded-lg border p-3 space-y-2.5'>
            <div className='flex items-center justify-between gap-2'>
              <div className='font-semibold text-foreground'>
                Bước 2 — Niêm phong &amp; Lưu trữ dữ liệu (ARCHIVE)
              </div>
              <Badge
                variant='outline'
                className={
                  isArchived
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 text-xs'
                    : 'text-xs'
                }
              >
                {isArchived ? 'HOÀN TẤT' : 'CHƯA THỰC HIỆN'}
              </Badge>
            </div>
            <p className='text-muted-foreground'>
              Kiểm kê tệp tin workspace, dừng container cô lập và tạo bản ghi lưu trữ an toàn trước
              khi xóa tài khoản.
            </p>
            {!isArchived && (
              <div className='flex justify-end pt-1'>
                <Button
                  type='button'
                  size='sm'
                  variant='outline'
                  className='h-8 text-xs'
                  disabled={!isSuspendedOrArchived || hasActiveWorkloads || actionBusy !== null}
                  onClick={() => void handleArchive()}
                >
                  {actionBusy === 'archive' ? 'Đang niêm phong...' : 'Thực hiện Archive'}
                </Button>
              </div>
            )}
          </div>

          {/* Bước 3: STEP-UP & DELETE */}
          <div className='rounded-lg border border-rose-500/40 bg-rose-500/5 p-3 space-y-3'>
            <div className='flex items-center justify-between gap-2'>
              <div className='font-semibold text-rose-700 dark:text-rose-300'>
                Bước 3 — Xác thực Step-Up Admin &amp; Xóa vĩnh viễn
              </div>
              <Badge
                variant='outline'
                className={
                  canDelete
                    ? 'border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300 text-xs'
                    : 'text-xs'
                }
              >
                {canDelete ? 'SẴN SÀNG XÓA' : 'CHƯA ĐỦ ĐIỀU KIỆN'}
              </Badge>
            </div>

            <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
              <div className='space-y-1.5'>
                <div className='flex items-center justify-between'>
                  <Label htmlFor='confirm-delete-username' className='text-xs'>
                    Nhập lại username{' '}
                    <span className='font-mono font-bold text-foreground'>@{member.username}</span>
                  </Label>
                  <Button
                    type='button'
                    variant='ghost'
                    size='sm'
                    className='h-5 px-1.5 text-xs text-primary hover:underline'
                    disabled={!canDelete || actionBusy !== null}
                    onClick={() => setConfirmUsername(member.username)}
                  >
                    Điền nhanh
                  </Button>
                </div>
                <Input
                  id='confirm-delete-username'
                  value={confirmUsername}
                  onChange={(e) => setConfirmUsername(e.target.value)}
                  placeholder='Gõ lại chính xác username...'
                  className='h-8 text-xs font-mono'
                  disabled={!canDelete || actionBusy !== null}
                />
                {confirmUsername.trim() === '' ? (
                  <p className='text-xs text-muted-foreground'>
                    Bắt buộc nhập chính xác{' '}
                    <code className='font-mono font-semibold'>@{member.username}</code>
                  </p>
                ) : usernameMatched ? (
                  <p className='text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1 font-medium'>
                    <Icons.check className='size-3' /> Đã khớp chính xác @{member.username}
                  </p>
                ) : (
                  <p className='text-xs text-rose-600 dark:text-rose-400 flex items-center gap-1'>
                    <Icons.warning className='size-3' /> Chưa khớp với @{member.username}
                  </p>
                )}
              </div>
              <div className='space-y-1.5'>
                <Label htmlFor='step-up-admin-password' className='text-xs'>
                  Mật khẩu Quản trị viên (Step-Up)
                </Label>
                <Input
                  id='step-up-admin-password'
                  type='password'
                  autoComplete='current-password'
                  value={adminPassword}
                  onChange={(e) => setAdminPassword(e.target.value)}
                  placeholder='Nhập mật khẩu Admin...'
                  className='h-8 text-xs'
                  disabled={!canDelete || actionBusy !== null}
                />
                <p className='text-xs text-muted-foreground'>
                  Mật khẩu quản trị LabOps (LABOPS_ADMIN_PASSWORD)
                </p>
              </div>
            </div>

            <div className='flex items-start gap-2 rounded border border-border/70 bg-background/80 p-2.5'>
              <input
                id='safe-delete-remove-files'
                type='checkbox'
                checked={removeFiles}
                onChange={(e) => setRemoveFiles(e.target.checked)}
                disabled={!canDelete || actionBusy !== null}
                className='mt-0.5 size-4 rounded border-input accent-rose-600'
              />
              <label htmlFor='safe-delete-remove-files' className='space-y-0.5 cursor-pointer'>
                Xóa luôn tệp dữ liệu workspace trên đĩa (không thể khôi phục)
                <span className='block text-muted-foreground font-normal'>
                  {removeFiles
                    ? 'CẢNH BÁO: Toàn bộ tệp đã archive trong workspace trên máy chủ sẽ bị xóa vĩnh viễn.'
                    : 'Mặc định tắt: Giữ lại bản lưu trữ dữ liệu trên máy chủ sau khi xóa tài khoản.'}
                </span>
              </label>
            </div>
          </div>
        </div>

        <DialogFooter className='gap-2 sm:gap-2 flex-col sm:flex-row items-stretch sm:items-center justify-between'>
          <div className='text-xs text-muted-foreground flex items-center gap-1.5 order-2 sm:order-1'>
            {!canDelete ? (
              <span className='text-amber-600 dark:text-amber-400 flex items-center gap-1'>
                <Icons.warning className='size-3 shrink-0' /> Cần hoàn tất Suspend &amp; Archive
                trước khi xóa.
              </span>
            ) : !usernameMatched ? (
              <span className='text-amber-600 dark:text-amber-400 flex items-center gap-1'>
                <Icons.warning className='size-3 shrink-0' /> Nhập lại @{member.username} để mở khóa
                nút xóa.
              </span>
            ) : !adminPassword ? (
              <span className='text-amber-600 dark:text-amber-400 flex items-center gap-1'>
                <Icons.warning className='size-3 shrink-0' /> Nhập mật khẩu Admin để xác thực
                Step-Up.
              </span>
            ) : (
              <span className='text-emerald-600 dark:text-emerald-400 flex items-center gap-1 font-medium'>
                <Icons.check className='size-3 shrink-0' /> Đã đủ điều kiện xác thực Step-Up &amp;
                xóa an toàn.
              </span>
            )}
          </div>
          <div className='flex items-center justify-end gap-2 order-1 sm:order-2'>
            <Button
              type='button'
              variant='outline'
              size='sm'
              className='text-xs'
              disabled={actionBusy !== null}
              onClick={() => handleOpenChange(false)}
            >
              Đóng
            </Button>
            <Button
              type='button'
              variant='destructive'
              size='sm'
              className='text-xs'
              disabled={!canDelete || !usernameMatched || !adminPassword || actionBusy !== null}
              onClick={() => void handleConfirmDelete()}
            >
              {actionBusy === 'delete'
                ? 'Đang xóa vĩnh viễn...'
                : `Xác nhận Xóa vĩnh viễn @${member.username}`}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
