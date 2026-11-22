/* oxlint-disable react/set-state-in-effect */
'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import {
  IconSparkles,
  IconCheck,
  IconAlertCircle,
  IconFileText,
  IconSend,
  IconRefresh,
  IconSettings,
  IconShield,
  IconHistory,
  IconDownload,
  IconServer,
  IconKey,
  IconUserCheck,
  IconTrash
} from '@tabler/icons-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface ChatEntry {
  sender: string;
  text: string;
  tool_traces?: Array<{ tool: string; status: string }>;
  evidence_timestamps?: string[];
  context_scope?: string;
}

export default function AIAssistantView() {
  const [activeTab, setActiveTab] = useState('overview');
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState<any>(null);
  const [reports, setReports] = useState<any[]>([]);
  const [incidents, setIncidents] = useState<any[]>([]);
  const [identityLinks, setIdentityLinks] = useState<any[]>([]);
  const [subscriptions, setSubscriptions] = useState<any[]>([]);
  const [config, setConfig] = useState<any>(null);
  const [tools, setTools] = useState<any>(null);
  const [history, setHistory] = useState<any>(null);

  const [chatInput, setChatInput] = useState('');
  const [chatLog, setChatLog] = useState<ChatEntry[]>([
    {
      sender: 'assistant',
      text: 'Xin chào! Tôi là LabOps AI SRE Agent v2. Bạn có thể chạy /status, /incidents, /report today, /security, /quota hoặc đặt câu hỏi điều tra hạ tầng bằng công cụ thực.'
    }
  ]);
  const [sending, setSending] = useState(false);
  const [reportGenLoading, setReportGenLoading] = useState(false);
  const [dailyGenLoading, setDailyGenLoading] = useState(false);
  const [evalLoading, setEvalLoading] = useState(false);
  const [saveConfigSuccess, setSaveConfigSuccess] = useState(false);

  // Config & Identity forms
  const [editToken, setEditToken] = useState('');
  const [editUsers, setEditUsers] = useState('');
  const [editModel, setEditModel] = useState('model_free');

  const [linkUsername, setLinkUsername] = useState('');
  const [linkRole, setLinkRole] = useState('RESEARCHER');
  const [generatedCodeInfo, setGeneratedCodeInfo] = useState<any>(null);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [ovRes, repRes, incRes, linkRes, subRes, cfgRes, toolRes, histRes] = await Promise.all([
        fetch('/api/labops/ai-assistant/overview'),
        fetch('/api/labops/ai-assistant/reports'),
        fetch('/api/labops/ai-assistant/incidents?limit=50'),
        fetch('/api/labops/ai-assistant/identity-links'),
        fetch('/api/labops/ai-assistant/subscriptions'),
        fetch('/api/labops/ai-assistant/config'),
        fetch('/api/labops/ai-assistant/tools'),
        fetch('/api/labops/ai-assistant/history')
      ]);

      if (ovRes.ok) setOverview(await ovRes.json());
      if (repRes.ok) {
        const rData = await repRes.json();
        setReports(rData.reports || []);
      }
      if (incRes.ok) {
        const iData = await incRes.json();
        setIncidents(iData.incidents || []);
      }
      if (linkRes.ok) {
        const lData = await linkRes.json();
        setIdentityLinks(lData.links || []);
      }
      if (subRes.ok) {
        const sData = await subRes.json();
        setSubscriptions(sData.subscriptions || []);
      }
      if (cfgRes.ok) {
        const cData = await cfgRes.json();
        setConfig(cData);
        setEditUsers((cData.authorized_users || []).join(', '));
        setEditModel(cData.ai_model || 'model_free');
      }
      if (toolRes.ok) setTools(await toolRes.json());
      if (histRes.ok) setHistory(await histRes.json());
    } catch (e) {
      console.error('Lỗi tải dữ liệu AI SRE v2:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleSendCommand = async (cmdText?: string) => {
    const text = (cmdText || chatInput).trim();
    if (!text || sending) return;

    setChatLog((prev) => [...prev, { sender: 'user', text }]);
    if (!cmdText) setChatInput('');
    setSending(true);

    try {
      const res = await fetch('/api/labops/ai-assistant/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: text })
      });
      const data = await res.json();
      setChatLog((prev) => [
        ...prev,
        {
          sender: 'assistant',
          text: data.reply || (data.success ? 'Đã thực thi thành công.' : 'Lỗi thực thi lệnh.'),
          tool_traces: data.tool_traces,
          evidence_timestamps: data.evidence_timestamps,
          context_scope: data.context_scope
        }
      ]);
      fetchData();
    } catch {
      setChatLog((prev) => [
        ...prev,
        { sender: 'assistant', text: '❌ Lỗi kết nối tới dịch vụ AI SRE Assistant backend.' }
      ]);
    } finally {
      setSending(false);
    }
  };

  const handleGenerateReport = async (daily = false) => {
    if (daily) setDailyGenLoading(true);
    else setReportGenLoading(true);
    try {
      const endpoint = daily
        ? '/api/labops/ai-assistant/reports/generate-daily?force=true'
        : '/api/labops/ai-assistant/reports/generate';
      const res = await fetch(endpoint, { method: 'POST' });
      if (res.ok) await fetchData();
    } finally {
      setDailyGenLoading(false);
      setReportGenLoading(false);
    }
  };

  const handleIncidentAction = async (incidentId: string, action: 'ack' | 'resolve') => {
    const res = await fetch(`/api/labops/ai-assistant/incidents/${incidentId}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note: `${action.toUpperCase()} via SRE Dashboard` })
    });
    if (res.ok) await fetchData();
  };

  const handleEvaluateNow = async () => {
    setEvalLoading(true);
    try {
      await fetch('/api/labops/ai-assistant/incidents/evaluate', { method: 'POST' });
      await fetchData();
    } finally {
      setEvalLoading(false);
    }
  };

  const handleGenerateLinkCode = async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!linkUsername.trim()) return;
    const res = await fetch('/api/labops/ai-assistant/identity-links/code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: linkUsername.trim(), role: linkRole, ttl_minutes: 15 })
    });
    if (res.ok) {
      setGeneratedCodeInfo(await res.json());
      setLinkUsername('');
      await fetchData();
    }
  };

  const handleRevokeLink = async (linkId: string) => {
    const res = await fetch(`/api/labops/ai-assistant/identity-links/${linkId}`, {
      method: 'DELETE'
    });
    if (res.ok) await fetchData();
  };

  const handleToggleSub = async (recipientZaloId: string, field: string, value: boolean) => {
    const res = await fetch('/api/labops/ai-assistant/subscriptions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recipient_zalo_id: recipientZaloId, [field]: value })
    });
    if (res.ok) await fetchData();
  };

  const handleSaveConfig = async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSaveConfigSuccess(false);
    const payload: any = {
      ai_model: editModel,
      authorized_users: editUsers
        .split(',')
        .map((u) => u.trim())
        .filter(Boolean)
    };
    if (editToken.trim()) payload.bot_token = editToken.trim();
    const res = await fetch('/api/labops/ai-assistant/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      setSaveConfigSuccess(true);
      setEditToken('');
      fetchData();
      setTimeout(() => setSaveConfigSuccess(false), 3000);
    }
  };

  const repoIncidents = incidents.filter((i) => i.source === 'repository_guard');
  const openIncidentsCount = overview?.open_incidents_count ?? 0;

  return (
    <div className='space-y-6'>
      {/* Header Info Banner */}
      <div className='flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 p-4 rounded-xl border bg-card'>
        <div className='flex items-center gap-3'>
          <div className='p-2.5 rounded-lg bg-primary/10 text-primary'>
            <IconSparkles className='h-6 w-6' />
          </div>
          <div>
            <h2 className='text-lg font-bold'>LabOps AI SRE Agent v2 &amp; Zalo Operations</h2>
            <p className='text-xs text-muted-foreground'>
              Giám sát thời gian thực • Incident Engine • Daily Brief 08:00 ICT • RBAC Danh tính
              Zalo
            </p>
          </div>
        </div>
        <div className='flex flex-wrap items-center gap-2'>
          <Button variant='outline' size='sm' onClick={fetchData} disabled={loading}>
            <IconRefresh className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
            Làm mới
          </Button>
          <Badge variant='outline' className='font-mono text-xs'>
            Health: {overview?.system_health || 'UNKNOWN'}
          </Badge>
          <Badge variant='outline' className='font-mono text-xs'>
            AI Gateway: {overview?.ai_gateway?.status || 'UNKNOWN'}
          </Badge>
          <Badge variant='outline' className='font-mono text-xs'>
            Zalo: {overview?.zalo_status || 'CHECKING'}
          </Badge>
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className='w-full'>
        <TabsList className='grid grid-cols-2 sm:grid-cols-6 w-full h-auto'>
          <TabsTrigger value='overview' className='text-xs py-2'>
            <IconServer className='h-3.5 w-3.5 mr-1' />
            Tổng quan &amp; AI
          </TabsTrigger>
          <TabsTrigger value='incidents' className='text-xs py-2'>
            <IconAlertCircle className='h-3.5 w-3.5 mr-1' />
            Sự cố ({openIncidentsCount})
          </TabsTrigger>
          <TabsTrigger value='repo-security' className='text-xs py-2'>
            <IconShield className='h-3.5 w-3.5 mr-1' />
            An ninh Repo ({repoIncidents.length})
          </TabsTrigger>
          <TabsTrigger value='reports' className='text-xs py-2'>
            <IconFileText className='h-3.5 w-3.5 mr-1' />
            Báo cáo
          </TabsTrigger>
          <TabsTrigger value='config' className='text-xs py-2'>
            <IconUserCheck className='h-3.5 w-3.5 mr-1' />
            Định danh &amp; Cấu hình
          </TabsTrigger>
          <TabsTrigger value='history' className='text-xs py-2'>
            <IconHistory className='h-3.5 w-3.5 mr-1' />
            Outbox &amp; Tools
          </TabsTrigger>
        </TabsList>

        {/* TAB 1: TỔNG QUAN & AI AGENT CHAT */}
        <TabsContent value='overview' className='space-y-4 pt-3'>
          <div className='grid gap-4 md:grid-cols-4'>
            <Card>
              <CardHeader className='pb-2'>
                <CardTitle className='text-sm font-semibold'>Zalo Outbound Quota</CardTitle>
                <CardDescription className='text-xs'>
                  Cấu hình nội bộ ({overview?.quota?.total_limit || 3000}/tháng)
                </CardDescription>
              </CardHeader>
              <CardContent className='space-y-2 text-xs'>
                <div className='flex justify-between font-medium'>
                  <span>Đã gửi: {overview?.quota?.total_sent || 0}</span>
                  <span>{overview?.quota?.percent_used || 0}%</span>
                </div>
                <div className='w-full h-2 rounded-full bg-muted overflow-hidden'>
                  <div
                    className='h-full bg-primary transition-all'
                    style={{ width: `${Math.min(100, overview?.quota?.percent_used || 0)}%` }}
                  />
                </div>
                <div className='flex justify-between text-muted-foreground pt-1'>
                  <span>Thường: {overview?.quota?.standard_remaining ?? 'N/A'}</span>
                  <span className='text-amber-600'>
                    Khẩn: {overview?.quota?.critical_remaining ?? 'N/A'}
                  </span>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className='pb-2'>
                <CardTitle className='text-sm font-semibold'>Incident Center</CardTitle>
                <CardDescription className='text-xs'>Trạng thái sự cố đang mở</CardDescription>
              </CardHeader>
              <CardContent className='space-y-1.5 text-xs'>
                <div className='text-2xl font-bold'>{openIncidentsCount}</div>
                <div className='text-muted-foreground'>
                  P0 Nghiêm trọng:{' '}
                  <strong className='text-destructive'>{overview?.p0_incidents_count ?? 0}</strong>
                </div>
                <Button
                  size='sm'
                  variant='outline'
                  className='h-7 text-xs w-full mt-1'
                  onClick={() => setActiveTab('incidents')}
                >
                  Mở Incident Center
                </Button>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className='pb-2'>
                <CardTitle className='text-sm font-semibold'>AI Gateway Engine</CardTitle>
                <CardDescription className='text-xs'>
                  Model: {overview?.active_model || 'N/A'}
                </CardDescription>
              </CardHeader>
              <CardContent className='space-y-1 text-xs'>
                <div className='flex justify-between'>
                  <span>Trạng thái:</span>
                  <Badge variant='outline' className='font-mono text-xs'>
                    {overview?.ai_gateway?.status || 'UNKNOWN'}
                  </Badge>
                </div>
                <div className='flex justify-between'>
                  <span>Lượt AI:</span>
                  <span className='font-mono font-bold'>{overview?.quota?.ai_requests ?? 0}</span>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className='pb-2'>
                <CardTitle className='text-sm font-semibold'>SRE Worker &amp; Lease</CardTitle>
                <CardDescription className='text-xs'>DB Lease chống chạy trùng</CardDescription>
              </CardHeader>
              <CardContent className='space-y-1 text-xs'>
                <div className='flex justify-between'>
                  <span>Polling:</span>
                  <Badge variant='outline' className='font-mono text-xs'>
                    {overview?.consumer?.is_polling ? 'ACTIVE' : 'OFFLINE'}
                  </Badge>
                </div>
                <div className='flex justify-between'>
                  <span>Identity Links:</span>
                  <span className='font-mono font-bold'>
                    {overview?.linked_identities_count ?? 0}
                  </span>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Live Nodes Telemetry (Non-Hardcoded) */}
          <Card>
            <CardHeader className='pb-2'>
              <CardTitle className='text-sm font-semibold'>
                Trạng thái Node Thời gian thực (Telemetry Freshness)
              </CardTitle>
            </CardHeader>
            <CardContent className='grid gap-2 md:grid-cols-2 text-xs'>
              {(overview?.hosts || []).length === 0 ? (
                <p className='text-muted-foreground'>
                  Chưa nhận được telemetry từ host nào (UNKNOWN).
                </p>
              ) : (
                (overview?.hosts || []).map((h: any) => (
                  <div
                    key={h.host_id}
                    className='p-2.5 rounded-lg border flex items-center justify-between'
                  >
                    <div>
                      <div className='font-semibold'>{h.display_name || h.hostname}</div>
                      <div className='text-muted-foreground font-mono text-xs'>
                        id={h.host_id} • src={h.source} • hb={h.last_heartbeat_at || 'N/A'}
                      </div>
                    </div>
                    <Badge variant='outline' className='font-mono'>
                      {h.status} ({h.freshness_status})
                    </Badge>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          {/* AI SRE Chat & Investigation Traces */}
          <Card>
            <CardHeader className='pb-2'>
              <div className='flex flex-wrap items-center justify-between gap-2'>
                <div>
                  <CardTitle className='text-sm font-semibold'>
                    AI SRE Agent Investigation Chat
                  </CardTitle>
                  <CardDescription className='text-xs'>
                    Gọi công cụ đọc schema-validated có RBAC • Hiển thị tool traces &amp; evidence
                    timestamps
                  </CardDescription>
                </div>
                <div className='flex flex-wrap gap-1'>
                  {['/status', '/incidents', '/jobs', '/report today', '/quota'].map((cmd) => (
                    <Button
                      key={cmd}
                      variant='outline'
                      size='sm'
                      className='h-7 text-xs font-mono'
                      onClick={() => handleSendCommand(cmd)}
                      disabled={sending}
                    >
                      {cmd}
                    </Button>
                  ))}
                </div>
              </div>
            </CardHeader>
            <CardContent className='space-y-3'>
              <div className='h-64 overflow-y-auto rounded-lg border bg-muted/30 p-3 space-y-3 text-xs font-mono'>
                {chatLog.map((m, idx) => (
                  <div
                    key={idx}
                    className={`flex flex-col ${m.sender === 'user' ? 'items-end' : 'items-start'}`}
                  >
                    <span className='text-xs text-muted-foreground mb-1'>
                      {m.sender === 'user' ? 'Bạn' : 'LabOps AI SRE v2'}
                      {m.context_scope ? ` (${m.context_scope})` : ''}
                    </span>
                    <div
                      className={`max-w-[88%] rounded-lg p-2.5 whitespace-pre-wrap ${
                        m.sender === 'user'
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-card border text-card-foreground'
                      }`}
                    >
                      {m.text}
                      {m.tool_traces && m.tool_traces.length > 0 && (
                        <div className='mt-2 pt-2 border-t text-xs text-muted-foreground'>
                          <div>
                            Tools: {m.tool_traces.map((t) => `${t.tool}[${t.status}]`).join(', ')}
                          </div>
                          {m.evidence_timestamps && m.evidence_timestamps.length > 0 && (
                            <div>Evidence: {m.evidence_timestamps[0]}</div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
                {sending && (
                  <div className='text-xs text-muted-foreground animate-pulse'>
                    Đang điều tra qua Read-Only Tools...
                  </div>
                )}
              </div>

              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleSendCommand();
                }}
                className='flex gap-2'
              >
                <Input
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  placeholder='Ví dụ: Tại sao Workstation hôm nay chậm? hoặc /incidents'
                  className='text-xs font-mono'
                  disabled={sending}
                />
                <Button type='submit' size='sm' disabled={sending || !chatInput.trim()}>
                  <IconSend className='h-4 w-4 mr-1' />
                  Gửi
                </Button>
              </form>
            </CardContent>
          </Card>
        </TabsContent>

        {/* TAB 2: TRUNG TÂM SỰ CỐ */}
        <TabsContent value='incidents' className='space-y-4 pt-3'>
          <div className='flex flex-wrap items-center justify-between gap-2 p-4 rounded-xl border bg-card'>
            <div>
              <h3 className='text-sm font-bold'>Unified Incident Center</h3>
              <p className='text-xs text-muted-foreground'>
                Deduplicate theo fingerprint • Lifecycle: OPEN → ACKNOWLEDGED → INVESTIGATING →
                RESOLVED
              </p>
            </div>
            <Button size='sm' variant='outline' onClick={handleEvaluateNow} disabled={evalLoading}>
              <IconRefresh className={`h-3.5 w-3.5 mr-1 ${evalLoading ? 'animate-spin' : ''}`} />
              Đánh giá Telemetry ngay
            </Button>
          </div>

          <div className='space-y-2.5'>
            {incidents.length === 0 ? (
              <Card>
                <CardContent className='p-6 text-center text-xs text-muted-foreground'>
                  Hiện chưa có sự cố nào được ghi nhận.
                </CardContent>
              </Card>
            ) : (
              incidents.map((inc) => (
                <Card key={inc.incident_id}>
                  <CardContent className='p-3.5 flex flex-col md:flex-row md:items-center justify-between gap-3 text-xs'>
                    <div className='space-y-1'>
                      <div className='flex flex-wrap items-center gap-1.5'>
                        <Badge
                          variant={inc.severity === 'P0' ? 'destructive' : 'outline'}
                          className='font-mono'
                        >
                          {inc.severity}
                        </Badge>
                        <Badge variant='outline' className='font-mono'>
                          {inc.confidence}
                        </Badge>
                        <Badge variant='secondary' className='font-mono'>
                          {inc.status}
                        </Badge>
                        <span className='font-mono font-bold text-primary'>{inc.incident_id}</span>
                        <span className='font-semibold'>{inc.title}</span>
                      </div>
                      <div className='text-muted-foreground font-mono'>
                        source={inc.source} • resource={inc.resource_id} • owner=
                        {inc.owner_id || 'system'} • count={inc.occurrence_count}
                      </div>
                      {inc.recommended_action && (
                        <div className='text-muted-foreground'>
                          Khuyến nghị: {inc.recommended_action}
                        </div>
                      )}
                    </div>
                    <div className='flex items-center gap-1.5 shrink-0'>
                      {inc.status === 'OPEN' && (
                        <Button
                          size='sm'
                          variant='outline'
                          className='h-7 text-xs'
                          onClick={() => handleIncidentAction(inc.incident_id, 'ack')}
                        >
                          Acknowledge
                        </Button>
                      )}
                      {inc.status !== 'RESOLVED' && (
                        <Button
                          size='sm'
                          className='h-7 text-xs'
                          onClick={() => handleIncidentAction(inc.incident_id, 'resolve')}
                        >
                          Resolve
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              ))
            )}
          </div>
        </TabsContent>

        {/* TAB 3: REPOSITORY SECURITY MAPPING */}
        <TabsContent value='repo-security' className='space-y-4 pt-3'>
          <Card>
            <CardHeader className='pb-2 flex flex-row items-center justify-between'>
              <div>
                <CardTitle className='text-sm font-bold'>
                  Sự cố An ninh Kho mã (Repository Guard Incidents)
                </CardTitle>
                <CardDescription className='text-xs'>
                  Mapping tự động giữa post-clone scan findings / scan failed với Incident Engine
                  &amp; Zalo Admin Alerts
                </CardDescription>
              </div>
              <Button
                nativeButton={false}
                render={
                  <Link href='/dashboard/security' aria-label='Mở Security Center'>
                    Mở Security Center
                  </Link>
                }
                size='sm'
                variant='outline'
              />
            </CardHeader>
            <CardContent className='space-y-2 text-xs'>
              {repoIncidents.length === 0 ? (
                <p className='text-muted-foreground py-4 text-center'>
                  Không có sự cố Repository Guard nào đang ghi nhận.
                </p>
              ) : (
                repoIncidents.map((inc) => (
                  <div
                    key={inc.incident_id}
                    className='p-2.5 rounded border flex flex-col sm:flex-row sm:items-center justify-between gap-2'
                  >
                    <div>
                      <div className='font-semibold'>{inc.title}</div>
                      <div className='text-muted-foreground font-mono'>
                        {inc.incident_id} • {inc.severity} • {inc.status} • owner=@{inc.owner_id}
                      </div>
                    </div>
                    {inc.status !== 'RESOLVED' && (
                      <Button
                        size='sm'
                        variant='outline'
                        className='h-7 text-xs'
                        onClick={() => handleIncidentAction(inc.incident_id, 'ack')}
                      >
                        Xác nhận
                      </Button>
                    )}
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* TAB 4: BÁO CÁO NGÀY & TUẦN */}
        <TabsContent value='reports' className='space-y-4 pt-3'>
          <div className='flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 p-4 rounded-xl border bg-card'>
            <div>
              <h3 className='text-sm font-bold'>
                Báo cáo Điều hành Ngày (08:00 ICT) &amp; Báo cáo Tuần (Thứ 6 17:00)
              </h3>
              <p className='text-xs text-muted-foreground'>
                KPI tính toán deterministic trước khi tổng hợp • Không trùng lặp sau restart
              </p>
            </div>
            <div className='flex flex-wrap gap-2'>
              <Button
                size='sm'
                onClick={() => handleGenerateReport(true)}
                disabled={dailyGenLoading}
              >
                <IconFileText className='h-4 w-4 mr-1' />
                {dailyGenLoading ? 'Đang tạo...' : 'Tạo Daily Brief (08:00)'}
              </Button>
              <Button
                size='sm'
                variant='outline'
                onClick={() => handleGenerateReport(false)}
                disabled={reportGenLoading}
              >
                <IconFileText className='h-4 w-4 mr-1' />
                {reportGenLoading ? 'Đang tổng hợp...' : 'Tạo Báo cáo Tuần'}
              </Button>
            </div>
          </div>

          <div className='space-y-4'>
            {reports.length === 0 ? (
              <Card>
                <CardContent className='p-6 text-center text-xs text-muted-foreground'>
                  Chưa có báo cáo nào được tạo.
                </CardContent>
              </Card>
            ) : (
              reports.map((rpt) => (
                <Card key={rpt.id}>
                  <CardHeader className='pb-2'>
                    <div className='flex items-center justify-between'>
                      <CardTitle className='text-sm font-bold'>{rpt.title}</CardTitle>
                      <div className='flex items-center gap-2'>
                        <Badge variant='outline' className='text-xs font-mono'>
                          {rpt.report_type || 'WEEKLY'}
                        </Badge>
                        <a
                          href={`/api/labops/ai-assistant/reports/${rpt.id}/download`}
                          target='_blank'
                          rel='noopener noreferrer'
                        >
                          <Button variant='outline' size='sm' className='h-7 text-xs'>
                            <IconDownload className='h-3.5 w-3.5 mr-1' />
                            Tải Markdown
                          </Button>
                        </a>
                      </div>
                    </div>
                    <CardDescription className='text-xs'>
                      {rpt.author} • {rpt.created_at} • {rpt.summary?.infrastructure}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className='p-3.5 rounded-lg bg-muted/40 border text-xs font-mono whitespace-pre-wrap max-h-80 overflow-y-auto'>
                      {rpt.content}
                    </div>
                  </CardContent>
                </Card>
              ))
            )}
          </div>
        </TabsContent>

        {/* TAB 5: ĐỊNH DANH ZALO, SUBSCRIPTIONS & CẤU HÌNH */}
        <TabsContent value='config' className='space-y-4 pt-3'>
          <div className='grid gap-4 md:grid-cols-2'>
            {/* Identity Link Generator */}
            <Card>
              <CardHeader>
                <CardTitle className='text-sm font-semibold flex items-center gap-1.5'>
                  <IconKey className='h-4 w-4' /> Liên kết Danh tính Zalo (Mã một lần)
                </CardTitle>
                <CardDescription className='text-xs'>
                  Chống giả mạo: Người dùng phải gửi `/link MÃ` từ tài khoản Zalo thực của mình.
                </CardDescription>
              </CardHeader>
              <CardContent className='space-y-3 text-xs'>
                <form onSubmit={handleGenerateLinkCode} className='flex flex-wrap gap-2'>
                  <Input
                    value={linkUsername}
                    onChange={(e) => setLinkUsername(e.target.value)}
                    placeholder='Username LabOps (vd: tranglt, admin)'
                    className='text-xs font-mono flex-1'
                    required
                  />
                  <select
                    aria-label='Vai trò tài khoản LabOps'
                    value={linkRole}
                    onChange={(e) => setLinkRole(e.target.value)}
                    className='border rounded px-2 py-1 text-xs bg-background'
                  >
                    <option value='RESEARCHER'>RESEARCHER</option>
                    <option value='STUDENT'>STUDENT</option>
                    <option value='ADMIN'>ADMIN</option>
                  </select>
                  <Button type='submit' size='sm'>
                    Tạo mã
                  </Button>
                </form>

                {generatedCodeInfo && (
                  <div className='p-2.5 rounded border bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 font-mono'>
                    <div>
                      Mã liên kết cho @{generatedCodeInfo.username} ({generatedCodeInfo.role}):{' '}
                      <strong>{generatedCodeInfo.code}</strong>
                    </div>
                    <div className='text-xs mt-1'>{generatedCodeInfo.instruction}</div>
                  </div>
                )}

                <div className='space-y-1.5 pt-2'>
                  <div className='font-semibold'>Danh sách liên kết:</div>
                  {identityLinks.map((lk) => (
                    <div
                      key={lk.id}
                      className='p-2 rounded border flex items-center justify-between'
                    >
                      <div>
                        <span className='font-mono font-bold'>@{lk.username}</span> ({lk.role}) •
                        Zalo: <span className='font-mono'>{lk.zalo_user_id || 'Chờ xác nhận'}</span>
                        <Badge variant='outline' className='ml-1.5 text-xs'>
                          {lk.status}
                        </Badge>
                      </div>
                      {lk.status !== 'REVOKED' && (
                        <Button
                          size='sm'
                          variant='ghost'
                          className='h-6 px-2 text-destructive'
                          onClick={() => handleRevokeLink(lk.id)}
                        >
                          <IconTrash className='h-3.5 w-3.5' />
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Bot Config */}
            <Card>
              <CardHeader>
                <CardTitle className='text-sm font-semibold flex items-center gap-1.5'>
                  <IconSettings className='h-4 w-4' /> Cấu hình Bot &amp; AI Gateway
                </CardTitle>
              </CardHeader>
              <CardContent>
                <form onSubmit={handleSaveConfig} className='space-y-3 text-xs'>
                  {saveConfigSuccess && (
                    <div className='p-2 rounded bg-emerald-500/10 text-emerald-600 border border-emerald-500/20 flex items-center gap-1.5'>
                      <IconCheck className='h-4 w-4' /> Đã cập nhật cấu hình!
                    </div>
                  )}
                  <div className='space-y-1'>
                    <label htmlFor='zalo-bot-token' className='font-semibold'>
                      Zalo Bot Token (Hiện tại: {config?.zalo_token_masked})
                    </label>
                    <Input
                      id='zalo-bot-token'
                      type='password'
                      value={editToken}
                      onChange={(e) => setEditToken(e.target.value)}
                      placeholder='Để trống nếu giữ nguyên...'
                      className='text-xs font-mono'
                    />
                  </div>
                  <div className='space-y-1'>
                    <label htmlFor='zalo-allowlist' className='font-semibold'>
                      Allowlist dự phòng (Zalo User IDs)
                    </label>
                    <Input
                      id='zalo-allowlist'
                      value={editUsers}
                      onChange={(e) => setEditUsers(e.target.value)}
                      className='text-xs font-mono'
                    />
                  </div>
                  <div className='space-y-1'>
                    <label htmlFor='zalo-ai-model' className='font-semibold'>
                      AI Model (9Router)
                    </label>
                    <Input
                      id='zalo-ai-model'
                      value={editModel}
                      onChange={(e) => setEditModel(e.target.value)}
                      className='text-xs font-mono'
                    />
                  </div>
                  <Button type='submit' size='sm'>
                    Lưu cấu hình
                  </Button>
                </form>
              </CardContent>
            </Card>
          </div>

          {/* Notification Subscriptions */}
          <Card>
            <CardHeader className='pb-2'>
              <CardTitle className='text-sm font-semibold'>
                Cài đặt Nhận Thông báo Theo Tài khoản
              </CardTitle>
            </CardHeader>
            <CardContent className='space-y-2 text-xs'>
              {subscriptions.length === 0 ? (
                <p className='text-muted-foreground'>Chưa có người nhận nào được liên kết.</p>
              ) : (
                subscriptions.map((sub) => (
                  <div
                    key={sub.id}
                    className='p-2.5 rounded border flex flex-wrap items-center justify-between gap-2'
                  >
                    <div className='font-mono'>
                      <strong>@{sub.username}</strong> ({sub.role}) • Zalo: {sub.recipient_zalo_id}
                    </div>
                    <div className='flex flex-wrap gap-1.5'>
                      <Button
                        size='sm'
                        variant={sub.enabled ? 'default' : 'outline'}
                        className='h-6 text-xs'
                        onClick={() =>
                          handleToggleSub(sub.recipient_zalo_id, 'enabled', !sub.enabled)
                        }
                      >
                        {sub.enabled ? 'Enabled' : 'Disabled'}
                      </Button>
                      <Button
                        size='sm'
                        variant={sub.receive_p0 ? 'default' : 'outline'}
                        className='h-6 text-xs'
                        onClick={() =>
                          handleToggleSub(sub.recipient_zalo_id, 'receive_p0', !sub.receive_p0)
                        }
                      >
                        P0: {sub.receive_p0 ? 'ON' : 'OFF'}
                      </Button>
                      <Button
                        size='sm'
                        variant={sub.receive_daily_report ? 'default' : 'outline'}
                        className='h-6 text-xs'
                        onClick={() =>
                          handleToggleSub(
                            sub.recipient_zalo_id,
                            'receive_daily_report',
                            !sub.receive_daily_report
                          )
                        }
                      >
                        Daily: {sub.receive_daily_report ? 'ON' : 'OFF'}
                      </Button>
                      {sub.role === 'ADMIN' && (
                        <Button
                          size='sm'
                          variant={sub.receive_repo_alerts ? 'default' : 'outline'}
                          className='h-6 text-xs'
                          onClick={() =>
                            handleToggleSub(
                              sub.recipient_zalo_id,
                              'receive_repo_alerts',
                              !sub.receive_repo_alerts
                            )
                          }
                        >
                          Repo Alerts: {sub.receive_repo_alerts ? 'ON' : 'OFF'}
                        </Button>
                      )}
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* TAB 6: OUTBOX & TOOLS */}
        <TabsContent value='history' className='space-y-4 pt-3'>
          <div className='grid gap-4 md:grid-cols-2'>
            <Card>
              <CardHeader className='pb-2'>
                <CardTitle className='text-sm font-semibold'>
                  Read-Only Tools &amp; Tool Audit
                </CardTitle>
              </CardHeader>
              <CardContent className='space-y-2 text-xs font-mono'>
                <div className='flex flex-wrap gap-1'>
                  {(tools?.read_only_tools || []).map((t: string) => (
                    <Badge key={t} variant='outline' className='text-xs'>
                      {t}
                    </Badge>
                  ))}
                </div>
                <div className='pt-2 space-y-1 max-h-56 overflow-y-auto'>
                  {(tools?.tool_audit || []).map((a: any) => (
                    <div key={a.id} className='p-1.5 rounded border flex justify-between'>
                      <span>
                        @{a.actor_username} → {a.tool_name}
                      </span>
                      <Badge variant='outline' className='text-xs'>
                        {a.status}
                      </Badge>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className='pb-2'>
                <CardTitle className='text-sm font-semibold'>
                  Notification Outbox &amp; Delivery Attempts
                </CardTitle>
              </CardHeader>
              <CardContent className='space-y-2 text-xs font-mono max-h-72 overflow-y-auto'>
                {(history?.notification_outbox || history?.outbox || []).map((m: any) => (
                  <div key={m.id} className='p-2 rounded border space-y-0.5'>
                    <div className='flex justify-between'>
                      <span className='font-bold'>{m.event_type || m.id}</span>
                      <Badge variant='outline' className='text-xs'>
                        {m.status}
                      </Badge>
                    </div>
                    <div className='text-muted-foreground'>
                      to={m.recipient_id} • {m.message_snippet}
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
