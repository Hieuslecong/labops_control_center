/* oxlint-disable react/set-state-in-effect */
'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  IconAlertCircle,
  IconBan,
  IconCheck,
  IconHistory,
  IconKey,
  IconRefresh,
  IconSend,
  IconServer,
  IconSettings,
  IconShield,
  IconSparkles,
  IconTrash
} from '@tabler/icons-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

interface V4Health {
  v4_enabled: boolean;
  flags: Record<string, boolean>;
  ingress_mode: string;
  agent_runtime: string;
  ai_circuit_breaker_open: boolean;
  outbox_summary: Record<string, number>;
  watchdog: {
    configured: boolean;
    coverage_status: string;
    healthy: boolean;
    checks?: Record<string, { ok: boolean; detail?: string; reason?: string }>;
  };
}

interface V4Metrics {
  outbox: Record<string, number>;
  quota: Record<string, unknown>;
  watchdog: { configured?: boolean; coverage_status?: string; healthy?: boolean };
  ai_circuit_breaker_open: boolean;
}

interface V4EventCoverage {
  summary: Record<string, number>;
  events: Array<{
    event_type: string;
    status: string;
    severity?: string;
    source?: string;
  }>;
}

interface V4TaskItem {
  task_id: string;
  owner_id: string;
  owner_role: string;
  task_type: string;
  title: string;
  status: string;
  next_run_at?: string | null;
  expires_at?: string | null;
  watch_rule?: {
    rule_id: string;
    target_id: string;
    metric: string;
    comparator: string;
    threshold: number;
    sustained_seconds: number;
    status: string;
    last_state: string;
  };
}

interface V4InvestigationItem {
  run_id: string;
  incident_id: string;
  episode_key: string;
  runbook_id: string;
  state: string;
  summary: string;
  verified_root_cause: string | null;
  evidence_refs: string[];
  missing_information: string[];
  reviewed_by?: string | null;
  review_feedback?: string | null;
}

interface V4ApprovalItem {
  action_id: string;
  actor_id: string;
  actor_role: string;
  action_type: string;
  risk_tier: string;
  target_type: string;
  target_id: string;
  state: string;
  reason?: string | null;
  expires_at: string;
  created_at: string;
  error_detail?: string | null;
}

interface V4Preferences {
  principal_id: string;
  timezone: string;
  opted_in_preferences: Record<string, unknown>;
  version?: number;
}

interface V4MemoryOverview {
  principal_id: string;
  active_sessions: Array<{
    session_id: string;
    channel: string;
    chat_id: string;
    sequence_no: number;
    last_active_at: string;
  }>;
  note?: string;
}

interface V4ConnectorItem {
  connector_id: string;
  enabled: boolean;
  status: string;
  owner_scope: string;
  last_success_at?: string | null;
  last_error?: string | null;
}

interface V4InboxItem {
  id: string;
  channel: string;
  external_message_id: string;
  sender_id: string;
  principal_id?: string | null;
  state: string;
  text_snippet: string;
  received_at: string;
}

async function fetchLabOpsJson<T>(
  path: string,
  init?: RequestInit
): Promise<{ ok: boolean; status: number; data?: T; error?: string }> {
  try {
    const res = await fetch(`/api/labops/${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...init?.headers
      }
    });
    const text = await res.text();
    let parsed: Record<string, unknown> = {};
    try {
      parsed = text ? (JSON.parse(text) as Record<string, unknown>) : {};
    } catch {
      parsed = { detail: text };
    }
    if (!res.ok) {
      return {
        ok: false,
        status: res.status,
        error: String(parsed.detail || parsed.error || `HTTP ${res.status}`)
      };
    }
    return { ok: true, status: res.status, data: parsed as unknown as T };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      error: err instanceof Error ? err.message : 'Network error'
    };
  }
}

export function AssistantV4Panel() {
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);
  const [health, setHealth] = useState<V4Health | null>(null);
  const [metrics, setMetrics] = useState<V4Metrics | null>(null);
  const [coverage, setCoverage] = useState<V4EventCoverage | null>(null);
  const [tasks, setTasks] = useState<V4TaskItem[]>([]);
  const [investigations, setInvestigations] = useState<V4InvestigationItem[]>([]);
  const [approvals, setApprovals] = useState<V4ApprovalItem[]>([]);
  const [prefs, setPrefs] = useState<V4Preferences | null>(null);
  const [memory, setMemory] = useState<V4MemoryOverview | null>(null);
  const [connectors, setConnectors] = useState<V4ConnectorItem[]>([]);
  const [inbox, setInbox] = useState<V4InboxItem[]>([]);

  // Interactive form states
  const [watchTarget, setWatchTarget] = useState('workstation');
  const [watchMetric, setWatchMetric] = useState('gpu_temperature_c');
  const [watchComp, setWatchComp] = useState('lt');
  const [watchThreshold, setWatchThreshold] = useState('75');
  const [watchHold, setWatchHold] = useState('300');

  const [prefKey, setPrefKey] = useState('digest_style');
  const [prefVal, setPrefVal] = useState('compact');
  const [feedbackByRun, setFeedbackByRun] = useState<Record<string, string>>({});
  const [stepUpPasswordByAction, setStepUpPasswordByAction] = useState<Record<string, string>>({});
  const [confirmReasonByAction, setConfirmReasonByAction] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    const [hRes, mRes, covRes, tRes, invRes, appRes, prefRes, memRes, conRes, inbRes] =
      await Promise.all([
        fetchLabOpsJson<V4Health>('ai-assistant/v4/health'),
        fetchLabOpsJson<V4Metrics>('ai-assistant/v4/metrics'),
        fetchLabOpsJson<V4EventCoverage>('ai-assistant/v4/events/coverage'),
        fetchLabOpsJson<{ tasks: V4TaskItem[] }>('ai-assistant/v4/tasks'),
        fetchLabOpsJson<{ investigations: V4InvestigationItem[] }>(
          'ai-assistant/v4/investigations'
        ),
        fetchLabOpsJson<{ approvals: V4ApprovalItem[] }>('ai-assistant/v4/approvals'),
        fetchLabOpsJson<V4Preferences>('ai-assistant/v4/preferences'),
        fetchLabOpsJson<V4MemoryOverview>('ai-assistant/v4/memory?channel=zalo'),
        fetchLabOpsJson<{ connectors: V4ConnectorItem[] }>('ai-assistant/v4/connectors'),
        fetchLabOpsJson<{ items: V4InboxItem[] }>('ai-assistant/v4/inbox?limit=20')
      ]);

    if (hRes.ok && hRes.data) setHealth(hRes.data);
    if (mRes.ok && mRes.data) setMetrics(mRes.data);
    if (covRes.ok && covRes.data) setCoverage(covRes.data);
    if (tRes.ok && tRes.data) setTasks(tRes.data.tasks || []);
    if (invRes.ok && invRes.data) setInvestigations(invRes.data.investigations || []);
    if (appRes.ok && appRes.data) setApprovals(appRes.data.approvals || []);
    if (prefRes.ok && prefRes.data) setPrefs(prefRes.data);
    if (memRes.ok && memRes.data) setMemory(memRes.data);
    if (conRes.ok && conRes.data) setConnectors(conRes.data.connectors || []);
    if (inbRes.ok && inbRes.data) setInbox(inbRes.data.items || []);

    setLoading(false);
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  async function handleCreateWatch(e: React.SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusyId('create-watch');
    setBanner(null);
    const res = await fetchLabOpsJson<{ watch: V4TaskItem }>('ai-assistant/v4/watches', {
      method: 'POST',
      body: JSON.stringify({
        target_id: watchTarget.trim(),
        metric: watchMetric.trim(),
        comparator: watchComp.trim(),
        threshold: Number(watchThreshold),
        sustained_seconds: Number(watchHold) || 300,
        once: true
      })
    });
    setBusyId(null);
    if (!res.ok) {
      setBanner(`Không thể tạo Watch: ${res.error}`);
      return;
    }
    setBanner(`Đã tạo Watch ${res.data?.watch?.task_id || ''}.`);
    await loadAll();
  }

  async function handleCancelTask(taskId: string) {
    setBusyId(taskId);
    setBanner(null);
    const res = await fetchLabOpsJson<{ task: V4TaskItem }>(
      `ai-assistant/v4/tasks/${taskId}/cancel`,
      {
        method: 'POST',
        body: JSON.stringify({ reason: 'Cancelled from Dashboard V4' })
      }
    );
    setBusyId(null);
    if (!res.ok) {
      setBanner(`Hủy tác vụ thất bại: ${res.error}`);
      return;
    }
    setBanner(`Đã hủy tác vụ ${taskId}.`);
    await loadAll();
  }

  async function handleSubmitInvestigationFeedback(runId: string) {
    const fb = (feedbackByRun[runId] || '').trim();
    if (!fb) return;
    setBusyId(runId);
    setBanner(null);
    const res = await fetchLabOpsJson<{ investigation: V4InvestigationItem }>(
      `ai-assistant/v4/investigations/${runId}/feedback`,
      {
        method: 'POST',
        body: JSON.stringify({ feedback: fb })
      }
    );
    setBusyId(null);
    if (!res.ok) {
      setBanner(`Lưu feedback thất bại: ${res.error}`);
      return;
    }
    setBanner(`Đã ghi nhận đánh giá cho RCA ${runId}.`);
    await loadAll();
  }

  async function handleConfirmApproval(item: V4ApprovalItem) {
    setBusyId(item.action_id);
    setBanner(null);
    const headers: Record<string, string> = {};

    if (item.risk_tier === 'R3') {
      const pw = (stepUpPasswordByAction[item.action_id] || '').trim();
      if (!pw) {
        setBusyId(null);
        setBanner(
          `Thao tác R3 (${item.action_id}) yêu cầu nhập mật khẩu Admin để lấy Step-Up token.`
        );
        return;
      }
      const stepRes = await fetchLabOpsJson<{ step_up_token: string }>('auth/step-up', {
        method: 'POST',
        body: JSON.stringify({
          password: pw,
          action: 'v4_confirm_action',
          target_id: item.action_id
        })
      });
      if (!stepRes.ok || !stepRes.data?.step_up_token) {
        setBusyId(null);
        setBanner(`Xác thực Step-Up thất bại: ${stepRes.error || 'Thiếu token'}`);
        return;
      }
      headers['X-LabOps-Step-Up-Token'] = stepRes.data.step_up_token;
    }

    const res = await fetchLabOpsJson<{ action_id: string; state: string }>(
      `ai-assistant/v4/approvals/${item.action_id}/confirm`,
      {
        method: 'POST',
        headers,
        body: JSON.stringify({
          reason: confirmReasonByAction[item.action_id] || 'Confirmed from Dashboard V4'
        })
      }
    );
    setBusyId(null);
    if (!res.ok) {
      setBanner(`Xác nhận ${item.action_id} thất bại: ${res.error}`);
      return;
    }
    setBanner(`Đã thực thi đề xuất ${item.action_id} -> ${res.data?.state}.`);
    await loadAll();
  }

  async function handleSavePreference(e: React.SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!prefKey.trim()) return;
    setBusyId('save-pref');
    setBanner(null);
    const res = await fetchLabOpsJson<V4Preferences>('ai-assistant/v4/preferences', {
      method: 'POST',
      body: JSON.stringify({ key: prefKey.trim(), value: prefVal.trim() })
    });
    setBusyId(null);
    if (!res.ok) {
      setBanner(`Lưu tùy chọn thất bại: ${res.error}`);
      return;
    }
    setBanner(`Đã lưu tùy chọn ${prefKey.trim()}.`);
    await loadAll();
  }

  async function handleClearMemory() {
    setBusyId('clear-mem');
    setBanner(null);
    const res = await fetchLabOpsJson<{ cleared_messages: number }>(
      'ai-assistant/v4/memory/clear',
      {
        method: 'POST',
        body: JSON.stringify({ channel: 'zalo' })
      }
    );
    setBusyId(null);
    if (!res.ok) {
      setBanner(`Xóa ngữ cảnh thất bại: ${res.error}`);
      return;
    }
    setBanner(
      `Đã xóa ${res.data?.cleared_messages ?? 0} tin nhắn ngữ cảnh chat (audit log được giữ nguyên).`
    );
    await loadAll();
  }

  const flagEntries = Object.entries(health?.flags || {});

  return (
    <div className='space-y-4 pt-3'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <div>
          <h3 className='text-sm font-semibold flex items-center gap-1.5'>
            <IconSparkles className='h-4 w-4 text-primary' />
            LabOps Personal AI Agent V4 Control Plane
          </h3>
          <p className='text-xs text-muted-foreground'>
            Durable Event Engine • Scoped Memory • Persistent Tasks &amp; Watches • Evidence RCA •
            R1–R3 Approvals
          </p>
        </div>
        <div className='flex items-center gap-2'>
          <Badge
            variant={health?.v4_enabled ? 'default' : 'secondary'}
            className='text-xs font-mono'
          >
            V4 Flag: {health?.v4_enabled ? 'ON' : 'OFF (Safe Rollback)'}
          </Badge>
          <Badge variant='outline' className='text-xs font-mono'>
            Watchdog: {health?.watchdog?.coverage_status || 'NOT_CONFIGURED'} (
            {health?.watchdog?.healthy ? 'OK' : 'CHECK'})
          </Badge>
          <Button variant='outline' size='sm' onClick={() => void loadAll()} disabled={loading}>
            <IconRefresh className={`h-3.5 w-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} />
            Làm mới V4
          </Button>
        </div>
      </div>

      {banner && (
        <div className='rounded-md border bg-muted/40 px-3 py-2 text-xs flex items-center justify-between gap-2'>
          <span>{banner}</span>
          <Button
            variant='ghost'
            size='sm'
            className='h-6 px-2 text-xs'
            onClick={() => setBanner(null)}
          >
            Đóng
          </Button>
        </div>
      )}

      {/* 1. Flags, Watchdog, Outbox & Coverage */}
      <div className='grid gap-4 md:grid-cols-3'>
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold flex items-center gap-1.5'>
              <IconSettings className='h-4 w-4' />
              Feature Flags &amp; Runtime
            </CardTitle>
            <CardDescription className='text-xs'>
              Ingress: {health?.ingress_mode || 'polling'} • Runtime:{' '}
              {health?.agent_runtime || 'native'}
            </CardDescription>
          </CardHeader>
          <CardContent className='flex flex-wrap gap-1.5'>
            {flagEntries.map(([k, v]) => (
              <Badge key={k} variant={v ? 'default' : 'outline'} className='text-xs font-mono'>
                {k}: {v ? 'on' : 'off'}
              </Badge>
            ))}
            <Badge
              variant={health?.ai_circuit_breaker_open ? 'destructive' : 'outline'}
              className='text-xs font-mono'
            >
              ai_breaker: {health?.ai_circuit_breaker_open ? 'OPEN' : 'CLOSED'}
            </Badge>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold flex items-center gap-1.5'>
              <IconServer className='h-4 w-4' />
              Outbox &amp; Quota Governor V4
            </CardTitle>
            <CardDescription className='text-xs'>
              Fenced claim states &amp; monthly bucket
            </CardDescription>
          </CardHeader>
          <CardContent className='space-y-1.5 text-xs font-mono'>
            <div className='flex flex-wrap gap-1.5'>
              {Object.entries(metrics?.outbox || health?.outbox_summary || {}).map(([st, cnt]) => (
                <Badge key={st} variant='outline' className='text-xs'>
                  {st}: {cnt}
                </Badge>
              ))}
            </div>
            {metrics?.quota && (
              <div className='text-muted-foreground pt-1'>
                Quota: {JSON.stringify(metrics.quota)}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold flex items-center gap-1.5'>
              <IconAlertCircle className='h-4 w-4' />
              Event Coverage Matrix
            </CardTitle>
            <CardDescription className='text-xs'>Producer activation vs catalog</CardDescription>
          </CardHeader>
          <CardContent className='flex flex-wrap gap-1.5 text-xs font-mono'>
            {coverage ? (
              Object.entries(coverage.summary || {}).map(([st, cnt]) => (
                <Badge key={st} variant='secondary' className='text-xs'>
                  {st}: {cnt}
                </Badge>
              ))
            ) : (
              <span className='text-muted-foreground'>Chỉ Admin xem ma trận chi tiết</span>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 2. Persistent Tasks & Watch Rules */}
      <Card>
        <CardHeader className='pb-2'>
          <CardTitle className='text-sm font-semibold'>Tasks &amp; Hardware Watch Rules</CardTitle>
          <CardDescription className='text-xs'>
            Lịch nhắc, báo cáo định kỳ và rule theo dõi ngưỡng bền qua restart
          </CardDescription>
        </CardHeader>
        <CardContent className='space-y-3'>
          <form onSubmit={(e) => void handleCreateWatch(e)} className='grid gap-2 md:grid-cols-6'>
            <Input
              value={watchTarget}
              onChange={(e) => setWatchTarget(e.target.value)}
              placeholder='target (workstation)'
              className='text-xs h-8'
            />
            <Input
              value={watchMetric}
              onChange={(e) => setWatchMetric(e.target.value)}
              placeholder='metric'
              className='text-xs h-8'
            />
            <Input
              value={watchComp}
              onChange={(e) => setWatchComp(e.target.value)}
              placeholder='lt|lte|gt|gte'
              className='text-xs h-8'
            />
            <Input
              value={watchThreshold}
              onChange={(e) => setWatchThreshold(e.target.value)}
              placeholder='threshold'
              className='text-xs h-8'
            />
            <Input
              value={watchHold}
              onChange={(e) => setWatchHold(e.target.value)}
              placeholder='hold (sec)'
              className='text-xs h-8'
            />
            <Button
              type='submit'
              size='sm'
              className='h-8 text-xs'
              disabled={busyId === 'create-watch'}
            >
              <IconSend className='h-3.5 w-3.5 mr-1' />
              Tạo Watch
            </Button>
          </form>

          <div className='space-y-2 max-h-60 overflow-y-auto'>
            {tasks.length === 0 ? (
              <div className='text-xs text-muted-foreground'>
                Chưa có tác vụ hoặc watch rule nào.
              </div>
            ) : (
              tasks.map((t) => (
                <div
                  key={t.task_id}
                  className='p-2 rounded border flex flex-wrap items-center justify-between gap-2 text-xs'
                >
                  <div className='space-y-0.5'>
                    <div className='font-medium flex items-center gap-1.5'>
                      <span className='font-mono'>{t.task_id}</span>
                      <Badge variant='outline' className='text-xs'>
                        {t.status}
                      </Badge>
                      <span>{t.title}</span>
                    </div>
                    <div className='text-muted-foreground font-mono'>
                      owner={t.owner_id} • type={t.task_type}
                      {t.watch_rule
                        ? ` • ${t.watch_rule.target_id} ${t.watch_rule.metric} ${t.watch_rule.comparator} ${t.watch_rule.threshold} (${t.watch_rule.sustained_seconds}s)`
                        : ''}
                    </div>
                  </div>
                  {(t.status === 'ACTIVE' || t.status === 'WAITING_CONDITION') && (
                    <Button
                      variant='outline'
                      size='sm'
                      className='h-7 text-xs'
                      disabled={busyId === t.task_id}
                      onClick={() => void handleCancelTask(t.task_id)}
                    >
                      <IconBan className='h-3.5 w-3.5 mr-1' />
                      Hủy
                    </Button>
                  )}
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>

      {/* 3. Pending Approvals & R3 Step-Up */}
      <Card>
        <CardHeader className='pb-2'>
          <CardTitle className='text-sm font-semibold flex items-center gap-1.5'>
            <IconKey className='h-4 w-4' />
            Action Proposals &amp; R1–R3 Confirmation Gate
          </CardTitle>
          <CardDescription className='text-xs'>
            R1/R2 xác nhận POST hoặc Zalo DM 1-1 mã một lần; R3 bắt buộc Dashboard POST + Step-Up
            Token
          </CardDescription>
        </CardHeader>
        <CardContent className='space-y-2 max-h-72 overflow-y-auto'>
          {approvals.length === 0 ? (
            <div className='text-xs text-muted-foreground'>Không có đề xuất hành động nào.</div>
          ) : (
            approvals.map((a) => (
              <div key={a.action_id} className='p-2.5 rounded border space-y-2 text-xs'>
                <div className='flex flex-wrap items-center justify-between gap-2'>
                  <div className='flex flex-wrap items-center gap-1.5 font-mono'>
                    <span className='font-bold'>{a.action_id}</span>
                    <Badge
                      variant={a.risk_tier === 'R3' ? 'destructive' : 'secondary'}
                      className='text-xs'
                    >
                      {a.risk_tier}
                    </Badge>
                    <Badge variant='outline' className='text-xs'>
                      {a.state}
                    </Badge>
                    <span>
                      {a.action_type} ({a.target_type}:{a.target_id})
                    </span>
                  </div>
                  <span className='text-muted-foreground font-mono'>by @{a.actor_id}</span>
                </div>
                {a.reason && <div className='text-muted-foreground'>Lý do: {a.reason}</div>}
                {a.state === 'PENDING_CONFIRMATION' && (
                  <div className='flex flex-wrap items-center gap-2'>
                    <Input
                      value={confirmReasonByAction[a.action_id] || ''}
                      onChange={(e) =>
                        setConfirmReasonByAction((prev) => ({
                          ...prev,
                          [a.action_id]: e.target.value
                        }))
                      }
                      placeholder='Ghi chú xác nhận...'
                      className='text-xs h-8 max-w-xs'
                    />
                    {a.risk_tier === 'R3' && (
                      <Input
                        type='password'
                        value={stepUpPasswordByAction[a.action_id] || ''}
                        onChange={(e) =>
                          setStepUpPasswordByAction((prev) => ({
                            ...prev,
                            [a.action_id]: e.target.value
                          }))
                        }
                        placeholder='Mật khẩu Admin Step-Up (R3)'
                        className='text-xs h-8 max-w-xs'
                      />
                    )}
                    <Button
                      size='sm'
                      className='h-8 text-xs'
                      disabled={busyId === a.action_id}
                      onClick={() => void handleConfirmApproval(a)}
                    >
                      <IconCheck className='h-3.5 w-3.5 mr-1' />
                      Xác nhận POST
                    </Button>
                  </div>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {/* 4. Evidence-Grounded RCA Investigations */}
      <Card>
        <CardHeader className='pb-2'>
          <CardTitle className='text-sm font-semibold flex items-center gap-1.5'>
            <IconShield className='h-4 w-4' />
            Evidence-Grounded Investigations (RCA)
          </CardTitle>
          <CardDescription className='text-xs'>
            Tách biệt nguyên nhân đã xác minh và khoảng trống bằng chứng; phản hồi Admin chỉ lưu
            đánh giá
          </CardDescription>
        </CardHeader>
        <CardContent className='space-y-2 max-h-72 overflow-y-auto'>
          {investigations.length === 0 ? (
            <div className='text-xs text-muted-foreground'>
              Chưa có bản ghi điều tra RCA nào (hoặc chỉ Admin xem).
            </div>
          ) : (
            investigations.map((inv) => (
              <div key={inv.run_id} className='p-2.5 rounded border space-y-1.5 text-xs'>
                <div className='flex flex-wrap items-center justify-between gap-2'>
                  <span className='font-mono font-bold'>
                    {inv.run_id} • inc={inv.incident_id} • {inv.runbook_id}
                  </span>
                  <Badge
                    variant={inv.verified_root_cause ? 'default' : 'outline'}
                    className='text-xs'
                  >
                    {inv.verified_root_cause ? 'VERIFIED RCA' : 'INSUFFICIENT_EVIDENCE'}
                  </Badge>
                </div>
                <div>{inv.summary}</div>
                <div className='text-muted-foreground font-mono'>
                  evidence={inv.evidence_refs.join(', ') || 'none'}
                  {inv.missing_information?.length
                    ? ` | missing=${inv.missing_information.join('; ')}`
                    : ''}
                </div>
                {inv.review_feedback && (
                  <div className='text-muted-foreground'>
                    Review ({inv.reviewed_by}): {inv.review_feedback}
                  </div>
                )}
                <div className='flex items-center gap-2 pt-1'>
                  <Input
                    value={feedbackByRun[inv.run_id] || ''}
                    onChange={(e) =>
                      setFeedbackByRun((prev) => ({ ...prev, [inv.run_id]: e.target.value }))
                    }
                    placeholder='Nhận xét review của Admin...'
                    className='text-xs h-7'
                  />
                  <Button
                    variant='outline'
                    size='sm'
                    className='h-7 text-xs'
                    disabled={busyId === inv.run_id}
                    onClick={() => void handleSubmitInvestigationFeedback(inv.run_id)}
                  >
                    Lưu Review
                  </Button>
                </div>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {/* 5 & 6. Preferences, Memory, Connectors & Durable Inbox */}
      <div className='grid gap-4 md:grid-cols-2'>
        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold'>
              Scoped Memory &amp; Opt-In Preferences
            </CardTitle>
            <CardDescription className='text-xs'>
              Timezone: {prefs?.timezone || 'Asia/Ho_Chi_Minh'} • Active Zalo sessions:{' '}
              {memory?.active_sessions?.length || 0}
            </CardDescription>
          </CardHeader>
          <CardContent className='space-y-3 text-xs'>
            <form onSubmit={(e) => void handleSavePreference(e)} className='flex flex-wrap gap-2'>
              <Input
                value={prefKey}
                onChange={(e) => setPrefKey(e.target.value)}
                placeholder='key'
                className='text-xs h-8 flex-1 min-w-[120px]'
              />
              <Input
                value={prefVal}
                onChange={(e) => setPrefVal(e.target.value)}
                placeholder='value'
                className='text-xs h-8 flex-1 min-w-[120px]'
              />
              <Button
                type='submit'
                size='sm'
                className='h-8 text-xs'
                disabled={busyId === 'save-pref'}
              >
                Lưu tùy chọn
              </Button>
              <Button
                type='button'
                variant='outline'
                size='sm'
                className='h-8 text-xs'
                disabled={busyId === 'clear-mem'}
                onClick={() => void handleClearMemory()}
              >
                <IconTrash className='h-3.5 w-3.5 mr-1' />
                Xóa ngữ cảnh chat
              </Button>
            </form>
            <div className='font-mono text-muted-foreground'>
              Preferences: {JSON.stringify(prefs?.opted_in_preferences || {})}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className='pb-2'>
            <CardTitle className='text-sm font-semibold flex items-center gap-1.5'>
              <IconHistory className='h-4 w-4' />
              External Connectors &amp; Durable Inbox
            </CardTitle>
            <CardDescription className='text-xs'>
              Connector opt-in (cô lập quyền Admin) &amp; lịch sử inbox chống trùng
            </CardDescription>
          </CardHeader>
          <CardContent className='space-y-2 text-xs'>
            <div className='flex flex-wrap gap-1.5'>
              {connectors.length === 0 ? (
                <span className='text-muted-foreground'>Chỉ Admin xem connector status</span>
              ) : (
                connectors.map((c) => (
                  <Badge key={c.connector_id} variant='outline' className='text-xs font-mono'>
                    {c.connector_id}: {c.status}
                  </Badge>
                ))
              )}
            </div>
            <div className='max-h-36 overflow-y-auto space-y-1 font-mono'>
              {inbox.map((m) => (
                <div key={m.id} className='p-1.5 rounded border flex justify-between gap-2'>
                  <span className='truncate'>
                    [{m.channel}] {m.sender_id}: {m.text_snippet}
                  </span>
                  <Badge variant='outline' className='text-xs shrink-0'>
                    {m.state}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
