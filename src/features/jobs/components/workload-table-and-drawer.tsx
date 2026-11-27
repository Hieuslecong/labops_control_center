'use client';

import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle
} from '@/components/ui/sheet';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from '@/components/ui/table';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { Icons } from '@/components/icons';
import { ProcessTreeDialog } from '@/features/jobs/components/process-tree-dialog';
import { useLabOpsProcessesQuery } from '@/features/labops/queries';
import type {
  ActionKind,
  ProcessActionResultEnvelope,
  ScopeBase,
  SnapshotWorkloadItem,
  WorkloadDetailResponse
} from '@/features/jobs/api/types';
import {
  buildWorkloadsCsv,
  compareNullableSeconds,
  formatActionBlockReasonLabel,
  formatBytesGiB,
  formatCpuBasisLabel,
  formatDurationSeconds,
  formatGroupAttributionMode,
  formatIctTime,
  formatKindConfidenceLabel,
  formatMemoryMethodLabel,
  formatStateBadgeLabel,
  formatVramAttributionLabel,
  formatWorkloadKindLabel
} from '@/features/jobs/api/service';
import { workloadMutations, workloadQueries } from '@/features/jobs/api/queries';
import type { ComputeJob, ServerId } from '@/types/labops';

function stateBadgeVariant(
  state: string,
  quality: string
): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (quality === 'OFFLINE' || quality === 'ERROR') return 'destructive';
  if (state === 'OBSERVED' && quality === 'FRESH') return 'secondary';
  return 'outline';
}

function toLegacyComputeJobShim(w: SnapshotWorkloadItem): ComputeJob {
  const srv: ServerId = w.node_id === 'track' ? 'track' : 'workstation';
  return {
    id: w.id,
    ref: w.ref ?? `${srv}:${w.host_pid}`,
    username: w.username || w.member_id || 'unknown',
    fullName: w.username || w.member_id || 'unknown',
    serverId: srv,
    workspace: w.workspace || '',
    pid: w.host_pid,
    script: w.display_name,
    cpuPercent: w.cpu.value_percent ?? 0,
    ramUsedMb: Math.round((w.memory.value_bytes ?? 0) / (1024 * 1024)),
    vramUsedMb: Math.round((w.vram.value_bytes ?? 0) / (1024 * 1024)),
    startTime: w.started_at ?? '',
    elapsedTime: formatDurationSeconds(w.elapsed_seconds),
    status: w.state === 'OBSERVED' ? 'RUNNING' : 'PENDING',
    sshStatus: 'UNKNOWN',
    containerState: w.container_id ? 'RUNNING' : 'HOST',
    gpuId: w.gpu_allocations?.[0]?.gpu_uuid ?? null
  };
}

// ---------- Overview Active Workloads Preview (up to 8 rows) ----------

interface WorkloadOverviewPreviewTableProps {
  workloads: SnapshotWorkloadItem[];
  onSelectWorkload: (workloadId: string) => void;
  onViewAll: () => void;
}

export function WorkloadOverviewPreviewTable({
  workloads,
  onSelectWorkload,
  onViewAll
}: WorkloadOverviewPreviewTableProps) {
  const previewRows = useMemo(() => {
    return workloads
      .toSorted((a, b) => {
        const av = a.vram.value_bytes ?? -1;
        const bv = b.vram.value_bytes ?? -1;
        if (av !== bv) return bv - av;
        return compareNullableSeconds(a.elapsed_seconds, b.elapsed_seconds, 'desc', a.id, b.id);
      })
      .slice(0, 8);
  }, [workloads]);

  return (
    <Card>
      <CardHeader className='pb-2 flex flex-row items-center justify-between gap-2'>
        <div>
          <CardTitle className='text-sm font-semibold'>Tác vụ Đang chạy Tiêu biểu</CardTitle>
          <CardDescription className='text-xs'>
            Tối đa 8 workload nổi bật theo mức dùng VRAM và thời lượng đã quan sát
          </CardDescription>
        </div>
        <Button type='button' size='sm' variant='outline' onClick={onViewAll}>
          Xem tất cả ({workloads.length}) <Icons.arrowRight className='size-4 ml-1' />
        </Button>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className='text-xs'>Workload</TableHead>
              <TableHead className='text-xs'>Thành viên</TableHead>
              <TableHead className='text-xs'>Node / PID</TableHead>
              <TableHead className='text-xs'>GPU / VRAM</TableHead>
              <TableHead className='text-xs text-right'>CPU %</TableHead>
              <TableHead className='text-xs text-right'>Thời lượng</TableHead>
              <TableHead className='text-xs'>Trạng thái</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {previewRows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className='py-8 text-center text-xs text-muted-foreground'>
                  Không có workload tính toán đang chạy trong phạm vi đã chọn.
                </TableCell>
              </TableRow>
            ) : (
              previewRows.map((w) => (
                <TableRow
                  key={w.id}
                  onClick={() => onSelectWorkload(w.id)}
                  className='cursor-pointer text-xs hover:bg-muted/50'
                >
                  <TableCell>
                    <div className='flex items-center gap-2'>
                      <span className='font-medium text-foreground'>{w.display_name}</span>
                      <Badge variant='outline' className='text-xs'>
                        {formatWorkloadKindLabel(w.workload_kind)}
                      </Badge>
                    </div>
                  </TableCell>
                  <TableCell>{w.username ?? w.member_id ?? 'Chưa ánh xạ'}</TableCell>
                  <TableCell className='font-mono text-muted-foreground'>
                    {w.node_id} • {w.host_pid}
                  </TableCell>
                  <TableCell className='tabular-nums'>
                    {w.gpu_associated ? formatBytesGiB(w.vram.value_bytes) : 'Không dùng GPU'}
                  </TableCell>
                  <TableCell className='text-right tabular-nums'>
                    {w.cpu.value_percent === null ? '—' : `${w.cpu.value_percent.toFixed(1)}%`}
                  </TableCell>
                  <TableCell className='text-right tabular-nums'>
                    {formatDurationSeconds(w.elapsed_seconds)}
                  </TableCell>
                  <TableCell>
                    <Badge variant={stateBadgeVariant(w.state, w.quality)} className='text-xs'>
                      {w.state}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// ---------- G6 Confirmation Dialog for V3.1 Safe Process Actions ----------

interface V31ProcessActionDialogProps {
  detail: WorkloadDetailResponse | undefined;
  action: ActionKind | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCompleted: () => void;
}

function generateOperationKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `v31op-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function V31ProcessActionDialog({
  detail,
  action,
  open,
  onOpenChange,
  onCompleted
}: V31ProcessActionDialogProps) {
  const [reason, setReason] = useState('');
  const [terminateTree, setTerminateTree] = useState(true);
  const [typedConfirm, setTypedConfirm] = useState('');
  const [operationKey, setOperationKey] = useState<string>(() => generateOperationKey());
  const [lastResult, setLastResult] = useState<ProcessActionResultEnvelope | null>(null);

  const mutation = useMutation({
    ...workloadMutations.processAction(),
    onSuccess: (res) => {
      setLastResult(res);
      if (res.status === 'TERMINATED' || res.status === 'KILLED') {
        toast.success(`Thao tác ${res.action} hoàn tất (${res.status})`, {
          description: `Correlation ID: ${res.correlation_id}`
        });
      } else {
        toast.warning(`Kết quả thao tác: ${res.status}`, {
          description: res.message
        });
      }
      onCompleted();
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Lỗi thực thi thao tác tiến trình');
    }
  });

  const w = detail?.workload;
  const expectedKillToken = w ? `KILL ${w.host_pid}` : 'KILL';
  const requiresTypedKill = action === 'SIGKILL';
  const isReasonValid = reason.trim().length >= 3;
  const isConfirmValid = !requiresTypedKill || typedConfirm.trim() === expectedKillToken;

  const handleClose = (nextOpen: boolean) => {
    if (!nextOpen) {
      setReason('');
      setTypedConfirm('');
      setLastResult(null);
      setOperationKey(generateOperationKey());
    }
    onOpenChange(nextOpen);
  };

  const handleSubmit = (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!w || !action || !w.ref || !isReasonValid || !isConfirmValid) return;
    mutation.mutate({
      ref: w.ref,
      payload: {
        action,
        reason: reason.trim(),
        operation_key: operationKey,
        terminate_tree: terminateTree,
        timeout_seconds: action === 'SIGTERM' ? 8 : 3,
        expected_boot_id: w.boot_id,
        expected_start_ticks: w.start_ticks,
        expected_uid: w.uid,
        expected_container_id: w.container_id,
        force_confirmed: requiresTypedKill,
        typed_confirmation: requiresTypedKill ? typedConfirm.trim() : null
      }
    });
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className='sm:max-w-lg'>
        <DialogHeader>
          <DialogTitle className='flex items-center gap-2 text-base'>
            <Icons.shieldAlert className='size-5 text-destructive' />
            {action === 'SIGKILL'
              ? 'Buộc dừng tiến trình (SIGKILL)'
              : 'Dừng an toàn tiến trình (SIGTERM)'}
          </DialogTitle>
          <DialogDescription className='text-xs'>
            Thực thi qua đường dẫn pidfd V3.1 với xác minh boot_id, start_ticks, uid và khóa
            idempotency.
          </DialogDescription>
        </DialogHeader>

        {w && (
          <form onSubmit={handleSubmit} className='space-y-3 text-xs'>
            <div className='rounded-md border bg-muted/30 p-3 space-y-1 font-mono'>
              <div>
                <span className='text-muted-foreground'>Workload:</span> {w.display_name} ({w.id})
              </div>
              <div>
                <span className='text-muted-foreground'>Target:</span> {w.node_id} • PID{' '}
                {w.host_pid} • boot_id {w.boot_id ?? '—'} • start_ticks {w.start_ticks ?? '—'}
              </div>
              <div>
                <span className='text-muted-foreground'>Operation Key:</span> {operationKey}
              </div>
            </div>

            <div className='space-y-1'>
              <label htmlFor='v31-action-reason' className='font-medium text-foreground block'>
                Lý do thực hiện (bắt buộc, tối thiểu 3 ký tự)
              </label>
              <Input
                id='v31-action-reason'
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder='Ví dụ: OOM stall trên GPU-WS-01 theo yêu cầu của chủ tác vụ'
                required
              />
            </div>

            <label className='flex items-center gap-2 cursor-pointer select-none'>
              <input
                type='checkbox'
                checked={terminateTree}
                onChange={(e) => setTerminateTree(e.target.checked)}
                className='rounded border-input'
              />
              <span>Gửi tín hiệu cả cây tiến trình con cùng owner (an toàn trước PID reuse)</span>
            </label>

            {requiresTypedKill && (
              <div className='space-y-1 rounded-md border border-destructive/40 bg-destructive/5 p-3'>
                <label htmlFor='v31-typed-kill' className='font-semibold text-destructive block'>
                  Nhập chính xác &quot;{expectedKillToken}&quot; để xác nhận SIGKILL
                </label>
                <Input
                  id='v31-typed-kill'
                  value={typedConfirm}
                  onChange={(e) => setTypedConfirm(e.target.value)}
                  placeholder={expectedKillToken}
                  autoComplete='off'
                />
              </div>
            )}

            {lastResult && (
              <div className='rounded-md border bg-muted/40 p-3 space-y-1'>
                <div className='flex items-center justify-between'>
                  <span className='font-semibold'>Trạng thái: {lastResult.status}</span>
                  <Badge variant='outline' className='text-xs'>
                    {lastResult.verification}
                  </Badge>
                </div>
                <p className='text-muted-foreground'>{lastResult.message}</p>
                {lastResult.pids_remaining.length > 0 && (
                  <p className='font-mono text-destructive'>
                    PID còn sót: {lastResult.pids_remaining.join(', ')}
                  </p>
                )}
                <p className='font-mono text-muted-foreground'>
                  correlation_id: {lastResult.correlation_id}
                </p>
              </div>
            )}

            <DialogFooter className='gap-2 pt-2'>
              <Button type='button' variant='outline' onClick={() => handleClose(false)}>
                Đóng
              </Button>
              <Button
                type='submit'
                variant={action === 'SIGKILL' ? 'destructive' : 'default'}
                disabled={
                  mutation.isPending ||
                  !w.ref ||
                  !isReasonValid ||
                  !isConfirmValid ||
                  !detail?.actions_enabled_by_flag
                }
              >
                {mutation.isPending ? 'Đang gửi tín hiệu...' : `Xác nhận ${action}`}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ---------- Slide-Over Detail Drawer ----------

interface WorkloadDetailDrawerProps {
  principalKey: string;
  workloadId: string | null;
  snapshotWorkloads: SnapshotWorkloadItem[];
  isAdmin: boolean;
  onClose: () => void;
  onActionCompleted: () => void;
}

export function WorkloadDetailDrawer({
  principalKey,
  workloadId,
  snapshotWorkloads,
  isAdmin,
  onClose,
  onActionCompleted
}: WorkloadDetailDrawerProps) {
  const [treeJob, setTreeJob] = useState<ComputeJob | null>(null);
  const [treeOpen, setTreeOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState<ActionKind | null>(null);

  const detailQuery = useQuery({
    ...workloadQueries.detail(principalKey, workloadId ?? ''),
    enabled: Boolean(workloadId)
  });

  const fallbackWorkload = useMemo(
    () => snapshotWorkloads.find((w) => w.id === workloadId) ?? null,
    [snapshotWorkloads, workloadId]
  );

  const detail = detailQuery.data;
  const w = detail?.workload ?? fallbackWorkload;

  return (
    <>
      <Sheet
        open={Boolean(workloadId)}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) onClose();
        }}
      >
        <SheetContent side='right' className='w-full sm:max-w-xl flex flex-col overflow-hidden p-5'>
          <SheetHeader className='p-0 pb-3 border-b shrink-0'>
            <SheetTitle className='text-base font-semibold flex items-center gap-2 pr-6'>
              <Icons.terminal className='size-4 text-primary shrink-0' />
              <span className='truncate'>{w?.display_name ?? 'Chi tiết Workload'}</span>
            </SheetTitle>
            <SheetDescription className='text-xs'>
              Thông tin xác minh nguồn lực, ngữ cảnh thực thi và hành động an toàn V3.1
            </SheetDescription>
          </SheetHeader>

          {detailQuery.isLoading && !w ? (
            <div className='py-8 flex items-center gap-2 text-xs text-muted-foreground'>
              <Icons.spinner className='size-4 animate-spin' /> Đang tải chi tiết workload...
            </div>
          ) : w ? (
            <div className='flex-1 overflow-y-auto space-y-4 pt-4 pr-1 text-xs'>
              <div className='flex flex-wrap items-center gap-2'>
                <Badge variant={stateBadgeVariant(w.state, w.quality)} className='text-xs'>
                  {formatStateBadgeLabel(w.state)} • {formatStateBadgeLabel(w.quality)}
                </Badge>
                <Badge variant='outline' className='text-xs'>
                  {formatWorkloadKindLabel(w.workload_kind)} (
                  {formatKindConfidenceLabel(w.kind_confidence)})
                </Badge>
                {w.is_protected && (
                  <Badge variant='destructive' className='text-xs'>
                    TIẾN TRÌNH HỆ THỐNG ĐƯỢC BẢO VỆ
                  </Badge>
                )}
              </div>

              {w.kind_evidence.length > 0 && (
                <div className='flex flex-wrap gap-1.5'>
                  {w.kind_evidence.map((ev) => (
                    <Badge key={ev} variant='secondary' className='font-mono text-xs'>
                      {ev}
                    </Badge>
                  ))}
                </div>
              )}

              {!w.actions_allowed && w.action_block_reason && (
                <div className='rounded-md border border-amber-500/40 bg-amber-500/10 p-2.5 text-amber-800 dark:text-amber-200'>
                  Khóa an toàn thao tác: {formatActionBlockReasonLabel(w.action_block_reason)}
                </div>
              )}

              {/* Metrics Grid */}
              <div className='grid grid-cols-2 gap-2.5'>
                <div className='rounded-md border p-2.5 min-w-0'>
                  <div className='text-muted-foreground'>CPU quan sát</div>
                  <div className='text-sm font-semibold tabular-nums mt-0.5'>
                    {w.cpu.value_percent === null ? '—' : `${w.cpu.value_percent.toFixed(1)}%`}
                  </div>
                  <div className='text-muted-foreground mt-0.5 break-words'>
                    {formatCpuBasisLabel(w.cpu.basis)}
                  </div>
                </div>

                <div className='rounded-md border p-2.5 min-w-0'>
                  <div className='text-muted-foreground'>Bộ nhớ (RAM)</div>
                  <div className='text-sm font-semibold tabular-nums mt-0.5'>
                    {formatBytesGiB(w.memory.value_bytes)}
                  </div>
                  <div className='text-muted-foreground mt-0.5 break-words'>
                    {formatMemoryMethodLabel(w.memory.method)}{' '}
                    {detail?.pss_bytes ? `• PSS ${formatBytesGiB(detail.pss_bytes)}` : ''}
                  </div>
                </div>

                <div className='rounded-md border p-2.5 min-w-0'>
                  <div className='text-muted-foreground'>VRAM Tiến trình</div>
                  <div className='text-sm font-semibold tabular-nums mt-0.5'>
                    {w.gpu_associated ? formatBytesGiB(w.vram.value_bytes) : 'Không dùng GPU'}
                  </div>
                  <div className='text-muted-foreground mt-0.5 break-words'>
                    {formatVramAttributionLabel(w.vram.attribution)}
                  </div>
                </div>

                <div className='rounded-md border p-2.5 min-w-0'>
                  <div className='text-muted-foreground'>Thời lượng chạy</div>
                  <div className='text-sm font-semibold tabular-nums mt-0.5'>
                    {formatDurationSeconds(w.elapsed_seconds)}
                  </div>
                  <div className='text-muted-foreground mt-0.5 break-words'>
                    Bắt đầu: {formatIctTime(w.started_at)}
                  </div>
                </div>
              </div>

              {/* Per-GPU Allocations */}
              {w.gpu_allocations && w.gpu_allocations.length > 0 && (
                <div className='rounded-md border p-3 space-y-1.5'>
                  <div className='font-semibold'>Phân bổ VRAM theo từng GPU</div>
                  {w.gpu_allocations.map((ga) => (
                    <div key={ga.gpu_uuid} className='flex items-center justify-between font-mono'>
                      <span>{detail?.gpu_labels?.[ga.gpu_uuid] || ga.gpu_uuid}</span>
                      <span>
                        {formatBytesGiB(ga.value_bytes)} ({formatStateBadgeLabel(ga.quality)})
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* Identity & Tuple Table */}
              <div className='rounded-md border p-3 space-y-1.5'>
                <div className='font-semibold'>Định danh & Tuple chống PID Reuse</div>
                <div className='grid grid-cols-[120px_minmax(0,1fr)] gap-x-3 gap-y-1 font-mono'>
                  <span className='text-muted-foreground'>Workload ID:</span>
                  <span className='min-w-0 break-all'>{w.id}</span>
                  <span className='text-muted-foreground'>Node:</span>
                  <span className='min-w-0 break-words'>{detail?.node_name ?? w.node_id}</span>
                  <span className='text-muted-foreground'>Thành viên:</span>
                  <span className='min-w-0 break-words'>
                    {detail?.member_full_name ?? w.username ?? w.member_id ?? 'Chưa ánh xạ'}
                  </span>
                  <span className='text-muted-foreground'>Nhóm:</span>
                  <span className='min-w-0 break-words'>
                    {w.group_id ?? '—'} (
                    {formatGroupAttributionMode(detail?.group_attribution_mode)})
                  </span>
                  <span className='text-muted-foreground'>Host PID:</span>
                  <span>{w.host_pid}</span>
                  <span className='text-muted-foreground'>Boot ID:</span>
                  <span className='min-w-0 truncate' title={w.boot_id ?? undefined}>
                    {w.boot_id ?? '—'}
                  </span>
                  <span className='text-muted-foreground'>Start Ticks:</span>
                  <span>{w.start_ticks ?? '—'}</span>
                  <span className='text-muted-foreground'>UID / Container:</span>
                  <span className='min-w-0 break-all'>
                    {w.uid ?? '—'} / {w.container_id ?? 'host'}
                  </span>
                </div>
              </div>

              {detail?.cmdline_sanitized && (
                <div className='rounded-md border bg-muted/30 p-3 space-y-1'>
                  <div className='font-semibold'>Command line (đã ẩn bí mật)</div>
                  <code className='block break-all font-mono text-foreground'>
                    {detail.cmdline_sanitized}
                  </code>
                </div>
              )}

              {detail?.metric_definitions && (
                <div className='rounded-md border p-3 space-y-1'>
                  <div className='font-semibold'>Định nghĩa chuẩn mực</div>
                  {Object.entries(detail.metric_definitions).map(([k, desc]) => (
                    <div key={k} className='text-muted-foreground'>
                      <span className='font-mono text-foreground'>{k}: </span>
                      {desc}
                    </div>
                  ))}
                </div>
              )}

              {/* Footer Actions */}
              <div className='flex flex-wrap items-center justify-between gap-2 pt-2 border-t'>
                <Button
                  type='button'
                  size='sm'
                  variant='outline'
                  onClick={() => {
                    setTreeJob(toLegacyComputeJobShim(w));
                    setTreeOpen(true);
                  }}
                >
                  <Icons.network className='size-4 mr-1.5' />
                  Xem cây tiến trình
                </Button>

                {isAdmin && (
                  <div className='flex items-center gap-2'>
                    <Button
                      type='button'
                      size='sm'
                      variant='outline'
                      disabled={!detail?.actions_enabled_by_flag || !w.actions_allowed || !w.ref}
                      onClick={() => setPendingAction('SIGTERM')}
                    >
                      Dừng (SIGTERM)
                    </Button>
                    <Button
                      type='button'
                      size='sm'
                      variant='destructive'
                      disabled={!detail?.actions_enabled_by_flag || !w.actions_allowed || !w.ref}
                      onClick={() => setPendingAction('SIGKILL')}
                    >
                      Buộc dừng (SIGKILL)
                    </Button>
                  </div>
                )}
              </div>

              {isAdmin && !detail?.actions_enabled_by_flag && (
                <p className='text-muted-foreground'>
                  Chế độ điều khiển tiến trình V3.1 đang tắt
                  (LABOPS_WORKLOAD_V31_ACTIONS_ENABLED=false).
                </p>
              )}
            </div>
          ) : null}
        </SheetContent>
      </Sheet>

      <ProcessTreeDialog job={treeJob} open={treeOpen} onOpenChange={setTreeOpen} />

      <V31ProcessActionDialog
        detail={detail}
        action={pendingAction}
        open={Boolean(pendingAction)}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) setPendingAction(null);
        }}
        onCompleted={() => {
          void detailQuery.refetch();
          onActionCompleted();
        }}
      />
    </>
  );
}

// ---------- Live Workloads Table (T01 + Raw Admin Diagnostics Switcher) ----------

type SortKey = 'vram_desc' | 'elapsed_desc' | 'elapsed_asc' | 'cpu_desc' | 'member_asc';

interface WorkloadLiveTableProps {
  workloads: SnapshotWorkloadItem[];
  scope: ScopeBase;
  isAdmin: boolean;
  autoRefresh: boolean;
  onSelectWorkload: (workloadId: string) => void;
}

export function WorkloadLiveTable({
  workloads,
  scope,
  isAdmin,
  autoRefresh,
  onSelectWorkload
}: WorkloadLiveTableProps) {
  const queryClient = useQueryClient();
  const [tableMode, setTableMode] = useState<'workloads' | 'raw'>('workloads');
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<SortKey>('vram_desc');
  const [showRss, setShowRss] = useState(true);
  const [showSystemIds, setShowSystemIds] = useState(false);
  const [pageSize, setPageSize] = useState<number>(25);
  const [pageIndex, setPageIndex] = useState<number>(0);

  // Admin-only raw diagnostics fallback: lazy-loaded strictly when tableMode === 'raw'
  const rawProcessesQuery = useLabOpsProcessesQuery({
    enabled: isAdmin && tableMode === 'raw',
    intervalMs: autoRefresh ? 10_000 : undefined
  });

  const filteredAndSorted = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = workloads.filter((w) => {
      if (!q) return true;
      return (
        w.display_name.toLowerCase().includes(q) ||
        (w.username ?? '').toLowerCase().includes(q) ||
        (w.member_id ?? '').toLowerCase().includes(q) ||
        (w.workspace ?? '').toLowerCase().includes(q) ||
        String(w.host_pid).includes(q) ||
        w.node_id.toLowerCase().includes(q) ||
        w.workload_kind.toLowerCase().includes(q)
      );
    });

    return filtered.toSorted((a, b) => {
      if (sortBy === 'elapsed_desc') {
        return compareNullableSeconds(a.elapsed_seconds, b.elapsed_seconds, 'desc', a.id, b.id);
      }
      if (sortBy === 'elapsed_asc') {
        return compareNullableSeconds(a.elapsed_seconds, b.elapsed_seconds, 'asc', a.id, b.id);
      }
      if (sortBy === 'cpu_desc') {
        return compareNullableSeconds(a.cpu.value_percent, b.cpu.value_percent, 'desc', a.id, b.id);
      }
      if (sortBy === 'member_asc') {
        const ma = a.username ?? a.member_id ?? '\uffff';
        const mb = b.username ?? b.member_id ?? '\uffff';
        return ma.localeCompare(mb) || a.id.localeCompare(b.id);
      }
      // vram_desc default
      return compareNullableSeconds(a.vram.value_bytes, b.vram.value_bytes, 'desc', a.id, b.id);
    });
  }, [workloads, search, sortBy]);

  const totalPages = Math.max(1, Math.ceil(filteredAndSorted.length / pageSize));
  const clampedPage = Math.min(pageIndex, totalPages - 1);
  const visibleRows = filteredAndSorted.slice(
    clampedPage * pageSize,
    clampedPage * pageSize + pageSize
  );

  const handleExportCsv = () => {
    const csv = buildWorkloadsCsv(filteredAndSorted, {
      node_id: scope.node_id,
      member_id: scope.member_id
    });
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `labops-workloads-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast.success(
      `Đã xuất ${Math.min(filteredAndSorted.length, 2000)} dòng CSV (đã vô hiệu hóa công thức)`
    );
  };

  return (
    <Card>
      <CardHeader className='pb-3 space-y-3'>
        <div className='flex flex-wrap items-center justify-between gap-2'>
          <div className='flex items-center gap-2'>
            <Button
              type='button'
              size='sm'
              variant={tableMode === 'workloads' ? 'default' : 'outline'}
              onClick={() => {
                if (tableMode !== 'workloads') {
                  setSearch('');
                  setPageIndex(0);
                }
                setTableMode('workloads');
              }}
            >
              Tác vụ thành viên ({workloads.length})
            </Button>
            {isAdmin && (
              <Button
                type='button'
                size='sm'
                variant={tableMode === 'raw' ? 'default' : 'outline'}
                onClick={() => {
                  if (tableMode !== 'raw') {
                    setSearch('');
                    setPageIndex(0);
                  }
                  setTableMode('raw');
                  void queryClient.invalidateQueries({ queryKey: ['labops', 'processes', 'all'] });
                }}
              >
                <Icons.terminal className='size-4 mr-1.5' />
                Chẩn đoán quản trị (Raw)
              </Button>
            )}
          </div>

          {tableMode === 'workloads' && (
            <div className='flex flex-wrap items-center gap-2'>
              <Button
                type='button'
                size='sm'
                variant={showRss ? 'secondary' : 'outline'}
                onClick={() => setShowRss((v) => !v)}
              >
                {showRss ? 'Ẩn RSS' : 'Hiện RSS'}
              </Button>
              <Button
                type='button'
                size='sm'
                variant={showSystemIds ? 'secondary' : 'outline'}
                onClick={() => setShowSystemIds((v) => !v)}
              >
                {showSystemIds ? 'Ẩn Tuple' : 'Hiện Tuple'}
              </Button>
              <Button
                type='button'
                size='sm'
                variant='outline'
                onClick={handleExportCsv}
                disabled={filteredAndSorted.length === 0}
              >
                <Icons.download className='size-4 mr-1.5' />
                Xuất CSV ({Math.min(filteredAndSorted.length, 2000)})
              </Button>
            </div>
          )}
        </div>

        {tableMode === 'workloads' && (
          <div className='flex flex-wrap items-center justify-between gap-2'>
            <div className='relative w-full sm:max-w-xs'>
              <Icons.search className='size-4 text-muted-foreground absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none' />
              <Input
                id='v31-workload-search'
                name='workloadSearch'
                aria-label='Tìm tiến trình, user, PID, GPU'
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPageIndex(0);
                }}
                placeholder='Tìm tiến trình, user, PID, GPU...'
                className='pl-8 pr-8 h-8 text-xs'
              />
              {search && (
                <button
                  type='button'
                  aria-label='Xóa bộ lọc tìm kiếm'
                  onClick={() => {
                    setSearch('');
                    setPageIndex(0);
                  }}
                  className='absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground'
                >
                  <Icons.close className='size-3.5' />
                </button>
              )}
            </div>

            <div className='flex flex-wrap items-center gap-2 text-xs'>
              <span className='text-muted-foreground'>Sắp xếp:</span>
              <NativeSelect
                size='sm'
                aria-label='Sắp xếp bảng tác vụ'
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as SortKey)}
              >
                <NativeSelectOption value='vram_desc'>VRAM giảm dần</NativeSelectOption>
                <NativeSelectOption value='elapsed_desc'>Thời lượng dài nhất</NativeSelectOption>
                <NativeSelectOption value='elapsed_asc'>Thời lượng ngắn nhất</NativeSelectOption>
                <NativeSelectOption value='cpu_desc'>CPU % cao nhất</NativeSelectOption>
                <NativeSelectOption value='member_asc'>Thành viên A–Z</NativeSelectOption>
              </NativeSelect>

              <NativeSelect
                size='sm'
                aria-label='Số dòng mỗi trang'
                value={String(pageSize)}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPageIndex(0);
                }}
              >
                <NativeSelectOption value='25'>25 / trang</NativeSelectOption>
                <NativeSelectOption value='50'>50 / trang</NativeSelectOption>
                <NativeSelectOption value='100'>100 / trang</NativeSelectOption>
              </NativeSelect>
            </div>
          </div>
        )}
      </CardHeader>

      <CardContent>
        {tableMode === 'raw' && isAdmin ? (
          <div className='space-y-3'>
            <div className='rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-muted-foreground'>
              Chế độ Raw tách biệt khỏi dữ liệu KPI/Overview — hiển thị tiến trình hệ thống thô phục
              vụ chẩn đoán Admin.
            </div>
            {rawProcessesQuery.isLoading ? (
              <div className='py-8 text-center text-xs text-muted-foreground'>
                Đang tải tiến trình thô...
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className='text-xs'>Node / PID</TableHead>
                    <TableHead className='text-xs'>User</TableHead>
                    <TableHead className='text-xs'>Tiến trình</TableHead>
                    <TableHead className='text-xs text-right'>CPU %</TableHead>
                    <TableHead className='text-xs text-right'>RAM (MB)</TableHead>
                    <TableHead className='text-xs text-right'>VRAM (MB)</TableHead>
                    <TableHead className='text-xs'>Trạng thái</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(rawProcessesQuery.data ?? []).slice(0, 100).map((proc) => (
                    <TableRow key={`${proc.serverId}:${proc.pid}`} className='text-xs'>
                      <TableCell className='font-mono'>
                        {proc.serverId} • {proc.pid}
                      </TableCell>
                      <TableCell>{proc.username}</TableCell>
                      <TableCell
                        className='max-w-[300px] truncate'
                        title={proc.cmdline || proc.script}
                      >
                        {proc.script}
                      </TableCell>
                      <TableCell className='text-right tabular-nums'>
                        {proc.cpuPercent.toFixed(1)}%
                      </TableCell>
                      <TableCell className='text-right tabular-nums'>{proc.ramUsedMb}</TableCell>
                      <TableCell className='text-right tabular-nums'>{proc.vramUsedMb}</TableCell>
                      <TableCell>
                        <Badge variant='outline' className='text-xs'>
                          {proc.status}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        ) : (
          <div className='space-y-3'>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className='text-xs'>Workload</TableHead>
                  <TableHead className='text-xs'>Thành viên / Nhóm</TableHead>
                  <TableHead className='text-xs'>Node / PID</TableHead>
                  {showSystemIds && <TableHead className='text-xs'>Boot / Ticks</TableHead>}
                  <TableHead className='text-xs'>GPU / VRAM</TableHead>
                  <TableHead className='text-xs text-right'>CPU</TableHead>
                  {showRss && <TableHead className='text-xs text-right'>RAM (RSS)</TableHead>}
                  <TableHead className='text-xs text-right'>Thời lượng</TableHead>
                  <TableHead className='text-xs'>Trạng thái</TableHead>
                  <TableHead className='text-xs text-right'>Chi tiết</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visibleRows.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={8 + (showRss ? 1 : 0) + (showSystemIds ? 1 : 0)}
                      className='py-8 text-center text-xs text-muted-foreground'
                    >
                      Không tìm thấy workload nào khớp điều kiện lọc.
                    </TableCell>
                  </TableRow>
                ) : (
                  visibleRows.map((w) => (
                    <TableRow key={w.id} className='text-xs'>
                      <TableCell>
                        <div className='space-y-0.5'>
                          <div className='font-medium text-foreground'>{w.display_name}</div>
                          <div className='flex items-center gap-1'>
                            <Badge variant='outline' className='text-xs'>
                              {formatWorkloadKindLabel(w.workload_kind)}
                            </Badge>
                            {w.workspace && (
                              <span className='text-muted-foreground truncate max-w-[160px]'>
                                {w.workspace}
                              </span>
                            )}
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <div>{w.username ?? w.member_id ?? 'Chưa ánh xạ'}</div>
                        {w.group_id && <div className='text-muted-foreground'>{w.group_id}</div>}
                      </TableCell>
                      <TableCell className='font-mono'>
                        {w.node_id} • {w.host_pid}
                      </TableCell>
                      {showSystemIds && (
                        <TableCell className='font-mono text-muted-foreground'>
                          <div>{w.boot_id ? w.boot_id.slice(0, 8) : '—'}</div>
                          <div>{w.start_ticks ?? '—'}</div>
                        </TableCell>
                      )}
                      <TableCell className='tabular-nums'>
                        {w.gpu_associated ? (
                          <div>
                            <div className='font-medium'>{formatBytesGiB(w.vram.value_bytes)}</div>
                            {w.gpu_allocations && w.gpu_allocations.length > 0 && (
                              <div className='font-mono text-muted-foreground'>
                                {w.gpu_allocations.length} GPU
                              </div>
                            )}
                          </div>
                        ) : w.gpu_associated === null ? (
                          'Chưa xác định'
                        ) : (
                          'CPU only'
                        )}
                      </TableCell>
                      <TableCell className='text-right tabular-nums'>
                        {w.cpu.value_percent === null ? '—' : `${w.cpu.value_percent.toFixed(1)}%`}
                      </TableCell>
                      {showRss && (
                        <TableCell className='text-right tabular-nums'>
                          {formatBytesGiB(w.memory.value_bytes)}
                        </TableCell>
                      )}
                      <TableCell className='text-right tabular-nums'>
                        {formatDurationSeconds(w.elapsed_seconds)}
                      </TableCell>
                      <TableCell>
                        <Badge variant={stateBadgeVariant(w.state, w.quality)} className='text-xs'>
                          {w.state}
                        </Badge>
                      </TableCell>
                      <TableCell className='text-right'>
                        <Button
                          type='button'
                          size='sm'
                          variant='ghost'
                          onClick={() => onSelectWorkload(w.id)}
                        >
                          Chi tiết
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>

            <div className='flex items-center justify-between text-xs text-muted-foreground pt-1'>
              <span>
                Hiển thị {visibleRows.length} / {filteredAndSorted.length} workload
              </span>
              <div className='flex items-center gap-1'>
                <Button
                  type='button'
                  size='sm'
                  variant='outline'
                  disabled={clampedPage === 0}
                  onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
                >
                  <Icons.chevronLeft className='size-4' />
                </Button>
                <span className='px-2 tabular-nums'>
                  Trang {clampedPage + 1} / {totalPages}
                </span>
                <Button
                  type='button'
                  size='sm'
                  variant='outline'
                  disabled={clampedPage >= totalPages - 1}
                  onClick={() => setPageIndex((p) => Math.min(totalPages - 1, p + 1))}
                >
                  <Icons.chevronRight className='size-4' />
                </Button>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
