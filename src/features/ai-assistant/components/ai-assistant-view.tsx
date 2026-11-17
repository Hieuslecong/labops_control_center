'use client';

import React, { useState, useEffect } from 'react';
import {
  IconSparkles,
  IconCheck,
  IconAlertTriangle,
  IconAlertCircle,
  IconFileText,
  IconSend,
  IconRefresh,
  IconSettings,
  IconShield,
  IconHistory,
  IconDownload,
  IconCpu,
  IconServer,
  IconKey,
  IconUserCheck,
  IconBan,
  IconTerminal2
} from '@tabler/icons-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface OverviewData {
  zalo_status: string;
  consumer: {
    consumer_id: string;
    is_polling: boolean;
    has_token: boolean;
    token_masked: string;
    enabled_read_tools: string[];
    disabled_write_tools: string[];
  };
  quota: {
    current_month: string;
    total_limit: number;
    standard_limit: number;
    critical_reserve: number;
    total_sent: number;
    standard_sent: number;
    critical_sent: number;
    ai_requests: number;
    standard_remaining: number;
    critical_remaining: number;
    total_remaining: number;
    percent_used: number;
  };
  ai_gateway: {
    status: string;
    base_url: string;
    model: string;
    model_available: boolean;
  };
  active_model: string;
  reports_count: number;
  latest_report?: {
    id: string;
    title: string;
    created_at: string;
    content: string;
  };
  recent_alerts: Array<{
    id: string;
    level: string;
    message: string;
    timestamp: string;
  }>;
}

export default function AIAssistantView() {
  const [activeTab, setActiveTab] = useState('overview');
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [reports, setReports] = useState<any[]>([]);
  const [config, setConfig] = useState<any>(null);
  const [tools, setTools] = useState<any>(null);
  const [history, setHistory] = useState<any>(null);

  // Quick test state
  const [chatInput, setChatInput] = useState('');
  const [chatLog, setChatLog] = useState<Array<{ sender: string; text: string }>>([
    {
      sender: 'assistant',
      text: 'Xin chào! Tôi là LabOps AI Assistant kết nối trực tiếp với 9Router cục bộ (model model_free). Bạn có thể thử các lệnh /status, /jobs, /report, /quota hoặc đặt câu hỏi.'
    }
  ]);
  const [sending, setSending] = useState(false);
  const [reportGenLoading, setReportGenLoading] = useState(false);
  const [saveConfigSuccess, setSaveConfigSuccess] = useState(false);

  // Form states
  const [editToken, setEditToken] = useState('');
  const [editUsers, setEditUsers] = useState('');
  const [editModel, setEditModel] = useState('model_free');

  const fetchData = async () => {
    setLoading(true);
    try {
      const [ovRes, repRes, cfgRes, toolRes, histRes] = await Promise.all([
        fetch('/api/labops/ai-assistant/overview'),
        fetch('/api/labops/ai-assistant/reports'),
        fetch('/api/labops/ai-assistant/config'),
        fetch('/api/labops/ai-assistant/tools'),
        fetch('/api/labops/ai-assistant/history')
      ]);

      if (ovRes.ok) setOverview(await ovRes.json());
      if (repRes.ok) {
        const rData = await repRes.json();
        setReports(rData.reports || []);
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
      console.error('Lỗi tải dữ liệu AI Assistant:', e);
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
          text: data.reply || (data.success ? 'Đã thực thi thành công.' : 'Lỗi thực thi lệnh.')
        }
      ]);
      fetchData();
    } catch {
      setChatLog((prev) => [
        ...prev,
        { sender: 'assistant', text: '❌ Lỗi kết nối tới dịch vụ AI Assistant backend.' }
      ]);
    } finally {
      setSending(false);
    }
  };

  const handleGenerateReport = async () => {
    setReportGenLoading(true);
    try {
      const res = await fetch('/api/labops/ai-assistant/reports/generate', { method: 'POST' });
      if (res.ok) {
        await fetchData();
      }
    } catch (e) {
      console.error('Lỗi tạo báo cáo:', e);
    } finally {
      setReportGenLoading(false);
    }
  };

  const handleSaveConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaveConfigSuccess(false);
    try {
      const payload: any = {
        ai_model: editModel,
        authorized_users: editUsers.split(',').map((u) => u.trim()).filter(Boolean)
      };
      if (editToken.trim()) {
        payload.bot_token = editToken.trim();
      }
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
    } catch (e) {
      console.error('Lỗi lưu cấu hình:', e);
    }
  };

  return (
    <div className='space-y-6'>
      {/* Header Info Banner */}
      <div className='flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 p-4 rounded-xl border bg-card'>
        <div className='flex items-center gap-3'>
          <div className='p-2.5 rounded-lg bg-primary/10 text-primary'>
            <IconSparkles className='h-6 w-6' />
          </div>
          <div>
            <h2 className='text-lg font-bold'>Trợ lý AI & Zalo Bot Control Plane</h2>
            <p className='text-xs text-muted-foreground'>
              Vận hành trên Workstation Primary (`aiot-ws1`) • 9Router AI Gateway (Cổng 20128)
            </p>
          </div>
        </div>
        <div className='flex items-center gap-2'>
          <Button variant='outline' size='sm' onClick={fetchData} disabled={loading}>
            <IconRefresh className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
            Làm mới
          </Button>
          <Badge variant='outline' className='bg-emerald-500/10 text-emerald-600 border-emerald-500/20 font-mono text-xs'>
            9Router: ONLINE
          </Badge>
          <Badge variant='outline' className='bg-primary/10 text-primary border-primary/20 font-mono text-xs'>
            Model: {overview?.active_model || 'model_free'}
          </Badge>
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className='w-full'>
        <TabsList className='grid grid-cols-2 sm:grid-cols-5 w-full'>
          <TabsTrigger value='overview' className='text-xs sm:text-sm'>
            <IconServer className='h-4 w-4 mr-1.5' />
            Tổng quan
          </TabsTrigger>
          <TabsTrigger value='config' className='text-xs sm:text-sm'>
            <IconSettings className='h-4 w-4 mr-1.5' />
            Cấu hình
          </TabsTrigger>
          <TabsTrigger value='reports' className='text-xs sm:text-sm'>
            <IconFileText className='h-4 w-4 mr-1.5' />
            Báo cáo tuần
          </TabsTrigger>
          <TabsTrigger value='tools' className='text-xs sm:text-sm'>
            <IconShield className='h-4 w-4 mr-1.5' />
            Quyền & Công cụ
          </TabsTrigger>
          <TabsTrigger value='history' className='text-xs sm:text-sm'>
            <IconHistory className='h-4 w-4 mr-1.5' />
            Lịch sử & Quota
          </TabsTrigger>
        </TabsList>

        {/* TAB 1: TỔNG QUAN */}
        <TabsContent value='overview' className='space-y-4 pt-3'>
          <div className='grid gap-4 md:grid-cols-3'>
            {/* Quota Card */}
            <Card>
              <CardHeader className='pb-2'>
                <div className='flex justify-between items-center'>
                  <CardTitle className='text-sm font-semibold'>Zalo Outbound Quota</CardTitle>
                  <Badge variant='secondary' className='text-[10px] font-mono'>
                    Tháng {overview?.quota?.current_month}
                  </Badge>
                </div>
                <CardDescription className='text-xs'>
                  Hạn mức cứng 3.000 tin nhắn/tháng
                </CardDescription>
              </CardHeader>
              <CardContent className='space-y-3 pt-1'>
                <div>
                  <div className='flex justify-between text-xs mb-1 font-medium'>
                    <span>Đã gửi: {overview?.quota?.total_sent || 0} / 3.000</span>
                    <span>{overview?.quota?.percent_used || 0}%</span>
                  </div>
                  <div className='w-full h-2 rounded-full bg-muted overflow-hidden'>
                    <div
                      className='h-full bg-primary transition-all'
                      style={{ width: `${Math.min(100, overview?.quota?.percent_used || 0)}%` }}
                    />
                  </div>
                </div>
                <div className='grid grid-cols-2 gap-2 text-xs pt-1 border-t'>
                  <div>
                    <span className='text-muted-foreground block text-[11px]'>Tin thông thường:</span>
                    <span className='font-mono font-bold'>{overview?.quota?.standard_remaining} / 2.800</span>
                  </div>
                  <div>
                    <span className='text-muted-foreground block text-[11px]'>Dự phòng khẩn cấp:</span>
                    <span className='font-mono font-bold text-amber-600'>{overview?.quota?.critical_remaining} / 200</span>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* AI Gateway Card */}
            <Card>
              <CardHeader className='pb-2'>
                <div className='flex justify-between items-center'>
                  <CardTitle className='text-sm font-semibold'>9Router AI Gateway</CardTitle>
                  <Badge variant='outline' className='bg-emerald-500/10 text-emerald-600 font-mono text-[10px]'>
                    {overview?.ai_gateway?.status || 'ONLINE'}
                  </Badge>
                </div>
                <CardDescription className='text-xs'>
                  Cổng kết nối AI cục bộ cổng 20128
                </CardDescription>
              </CardHeader>
              <CardContent className='space-y-2 text-xs pt-1'>
                <div className='flex justify-between py-1 border-b'>
                  <span className='text-muted-foreground'>Mô hình kích hoạt:</span>
                  <span className='font-mono font-bold text-primary'>{overview?.active_model}</span>
                </div>
                <div className='flex justify-between py-1 border-b'>
                  <span className='text-muted-foreground'>Đường dẫn (Endpoint):</span>
                  <span className='font-mono text-[11px]'>http://127.0.0.1:20128/v1</span>
                </div>
                <div className='flex justify-between py-1'>
                  <span className='text-muted-foreground'>Lượt gọi AI tháng này:</span>
                  <span className='font-mono font-bold'>{overview?.quota?.ai_requests || 0} lượt</span>
                </div>
              </CardContent>
            </Card>

            {/* Zalo Polling Consumer Guard */}
            <Card>
              <CardHeader className='pb-2'>
                <div className='flex justify-between items-center'>
                  <CardTitle className='text-sm font-semibold'>Zalo Polling Consumer</CardTitle>
                  <Badge
                    variant='outline'
                    className={overview?.consumer?.has_token ? 'bg-emerald-500/10 text-emerald-600 font-mono text-[10px]' : 'bg-amber-500/10 text-amber-600 font-mono text-[10px]'}
                  >
                    {overview?.consumer?.has_token ? 'ACTIVE' : 'STANDBY (Chưa token)'}
                  </Badge>
                </div>
                <CardDescription className='text-xs'>
                  Cơ chế Single-Consumer Lease bảo vệ token
                </CardDescription>
              </CardHeader>
              <CardContent className='space-y-2 text-xs pt-1'>
                <div className='flex justify-between py-1 border-b'>
                  <span className='text-muted-foreground'>Consumer Host:</span>
                  <span className='font-mono font-medium'>aiot-ws1 (Primary)</span>
                </div>
                <div className='flex justify-between py-1 border-b'>
                  <span className='text-muted-foreground'>Rack Consumer:</span>
                  <span className='font-mono text-muted-foreground'>ISOLATED / STOPPED</span>
                </div>
                <div className='flex justify-between py-1'>
                  <span className='text-muted-foreground'>Token trạng thái:</span>
                  <span className='font-mono text-[11px]'>{overview?.consumer?.token_masked}</span>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Interactive Chat & Command Test Box */}
          <Card>
            <CardHeader className='pb-2'>
              <div className='flex flex-wrap items-center justify-between gap-2'>
                <div>
                  <CardTitle className='text-sm font-semibold'>Thử nghiệm Lệnh & Hỏi đáp AI Trực tiếp</CardTitle>
                  <CardDescription className='text-xs'>
                    Tương tác kiểm thử bot theo đúng 7 lệnh chuẩn: /status, /jobs, /report, /quota, /ask
                  </CardDescription>
                </div>
                <div className='flex flex-wrap gap-1'>
                  {['/status', '/jobs', '/report', '/quota'].map((cmd) => (
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
              {/* Chat Log Window */}
              <div className='h-64 overflow-y-auto rounded-lg border bg-muted/30 p-3 space-y-3 text-xs font-mono'>
                {chatLog.map((m, idx) => (
                  <div
                    key={idx}
                    className={`flex flex-col ${m.sender === 'user' ? 'items-end' : 'items-start'}`}
                  >
                    <span className='text-[10px] text-muted-foreground mb-1'>
                      {m.sender === 'user' ? 'Bạn' : 'LabOps AI Assistant'}
                    </span>
                    <div
                      className={`max-w-[85%] rounded-lg p-2.5 whitespace-pre-wrap ${
                        m.sender === 'user'
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-card border text-card-foreground'
                      }`}
                    >
                      {m.text}
                    </div>
                  </div>
                ))}
                {sending && (
                  <div className='text-xs text-muted-foreground animate-pulse'>
                    Đang kết nối 9Router và tạo phản hồi...
                  </div>
                )}
              </div>

              {/* Chat Input Bar */}
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
                  placeholder='Nhập lệnh (/status, /jobs, /quota) hoặc câu hỏi kỹ thuật...'
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

          {/* Recent Alerts List */}
          <Card>
            <CardHeader className='pb-2'>
              <CardTitle className='text-sm font-semibold'>Thông báo & Cảnh báo gần đây</CardTitle>
            </CardHeader>
            <CardContent className='space-y-2 text-xs'>
              {overview?.recent_alerts?.map((alt) => (
                <div
                  key={alt.id}
                  className={`flex items-start gap-2.5 p-2.5 rounded-lg border ${
                    alt.level === 'CRITICAL' ? 'bg-destructive/10 border-destructive/20 text-destructive' : 'bg-muted/40 border-muted'
                  }`}
                >
                  {alt.level === 'CRITICAL' ? (
                    <IconAlertCircle className='h-4 w-4 shrink-0 mt-0.5' />
                  ) : (
                    <IconAlertTriangle className='h-4 w-4 shrink-0 mt-0.5 text-amber-500' />
                  )}
                  <div className='flex-1'>
                    <p className='font-medium'>{alt.message}</p>
                    <span className='text-[10px] text-muted-foreground'>{alt.timestamp}</span>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        {/* TAB 2: CẤU HÌNH */}
        <TabsContent value='config' className='space-y-4 pt-3'>
          <Card>
            <CardHeader>
              <CardTitle className='text-base font-semibold'>Cấu hình Bot & AI Gateway</CardTitle>
              <CardDescription className='text-xs'>
                Toàn bộ cấu hình được lưu qua API có xác thực quyền Admin. Tuyệt đối không phơi token trong trình duyệt.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSaveConfig} className='space-y-4 max-w-xl text-xs'>
                {saveConfigSuccess && (
                  <div className='p-3 rounded-lg bg-emerald-500/10 text-emerald-600 border border-emerald-500/20 flex items-center gap-2'>
                    <IconCheck className='h-4 w-4' />
                    <span>Đã cập nhật cấu hình thành công!</span>
                  </div>
                )}

                <div className='space-y-1.5'>
                  <label className='font-semibold flex items-center justify-between'>
                    <span>Zalo Bot Token (Cập nhật token mới an toàn):</span>
                    <span className='text-muted-foreground font-mono text-[11px]'>
                      Hiện tại: {config?.zalo_token_masked}
                    </span>
                  </label>
                  <Input
                    type='password'
                    value={editToken}
                    onChange={(e) => setEditToken(e.target.value)}
                    placeholder='Dán Zalo Bot Token mới nếu muốn thay đổi...'
                    className='text-xs font-mono'
                  />
                  <p className='text-[11px] text-muted-foreground'>
                    Để trống nếu giữ nguyên token hiện tại. Không bao giờ commit token vào kho mã nguồn.
                  </p>
                </div>

                <div className='space-y-1.5'>
                  <label className='font-semibold'>Người dùng được phân quyền (Authorized User IDs):</label>
                  <Input
                    value={editUsers}
                    onChange={(e) => setEditUsers(e.target.value)}
                    placeholder='admin, user1, user2...'
                    className='text-xs font-mono'
                  />
                  <p className='text-[11px] text-muted-foreground'>
                    Phân tách bằng dấu phẩy. Dùng `*` nếu cho phép toàn bộ thành viên trong Lab.
                  </p>
                </div>

                <div className='space-y-1.5'>
                  <label className='font-semibold'>Mô hình AI trên 9Router (Model Name):</label>
                  <Input
                    value={editModel}
                    onChange={(e) => setEditModel(e.target.value)}
                    placeholder='model_free'
                    className='text-xs font-mono'
                  />
                  <p className='text-[11px] text-muted-foreground'>
                    Mặc định: <code>model_free</code> (chạy trên 9Router cổng 20128).
                  </p>
                </div>

                <div className='space-y-1.5'>
                  <label className='font-semibold'>AI Gateway URL:</label>
                  <Input
                    value='http://127.0.0.1:20128/v1'
                    disabled
                    className='text-xs font-mono bg-muted'
                  />
                  <p className='text-[11px] text-muted-foreground'>
                    Cố định loopback an toàn, không mở cổng công khai ra internet.
                  </p>
                </div>

                <div className='pt-2'>
                  <Button type='submit' size='sm'>
                    Lưu thay đổi
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>
        </TabsContent>

        {/* TAB 3: BÁO CÁO TUẦN */}
        <TabsContent value='reports' className='space-y-4 pt-3'>
          <div className='flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 p-4 rounded-xl border bg-card'>
            <div>
              <h3 className='text-sm font-bold'>Lịch Báo cáo Tự động Định kỳ</h3>
              <p className='text-xs text-muted-foreground'>
                Mặc định phát hành: <strong>Thứ Sáu, 17:00 (Asia/Ho_Chi_Minh)</strong>
              </p>
            </div>
            <Button
              size='sm'
              onClick={handleGenerateReport}
              disabled={reportGenLoading}
            >
              <IconFileText className='h-4 w-4 mr-1' />
              {reportGenLoading ? 'Đang tổng hợp...' : 'Tạo báo cáo tuần ngay'}
            </Button>
          </div>

          {/* Reports List */}
          <div className='space-y-4'>
            {reports.length === 0 ? (
              <Card>
                <CardContent className='p-6 text-center text-xs text-muted-foreground'>
                  Chưa có báo cáo tuần nào được tạo. Hãy nhấn "Tạo báo cáo tuần ngay" bên trên.
                </CardContent>
              </Card>
            ) : (
              reports.map((rpt) => (
                <Card key={rpt.id}>
                  <CardHeader className='pb-2'>
                    <div className='flex items-center justify-between'>
                      <CardTitle className='text-sm font-bold'>{rpt.title}</CardTitle>
                      <div className='flex items-center gap-2'>
                        <span className='text-[11px] text-muted-foreground font-mono'>{rpt.created_at}</span>
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
                      Tác giả: {rpt.author} • Hạ tầng: {rpt.summary?.infrastructure}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className='p-3.5 rounded-lg bg-muted/40 border text-xs font-mono whitespace-pre-wrap max-h-96 overflow-y-auto'>
                      {rpt.content}
                    </div>
                  </CardContent>
                </Card>
              ))
            )}
          </div>
        </TabsContent>

        {/* TAB 4: QUYỀN & CÔNG CỤ */}
        <TabsContent value='tools' className='space-y-4 pt-3'>
          <div className='grid gap-4 md:grid-cols-2'>
            {/* Read-Only Tools */}
            <Card>
              <CardHeader className='pb-2'>
                <div className='flex items-center gap-2'>
                  <div className='p-1.5 rounded bg-emerald-500/10 text-emerald-600'>
                    <IconCheck className='h-4 w-4' />
                  </div>
                  <div>
                    <CardTitle className='text-sm font-semibold'>Công cụ Đọc được Kích hoạt (Read-Only)</CardTitle>
                    <CardDescription className='text-xs'>
                      An toàn tuyệt đối, không gây tác động hay thay đổi trạng thái máy chủ
                    </CardDescription>
                  </div>
                </div>
              </CardHeader>
              <CardContent className='pt-2'>
                <ul className='space-y-2 text-xs font-mono'>
                  {tools?.read_only_tools?.map((t: string) => (
                    <li key={t} className='flex items-center gap-2 p-2 rounded border bg-card'>
                      <IconCheck className='h-3.5 w-3.5 text-emerald-600 shrink-0' />
                      <span className='font-semibold'>{t}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>

            {/* Disabled Write Tools */}
            <Card>
              <CardHeader className='pb-2'>
                <div className='flex items-center gap-2'>
                  <div className='p-1.5 rounded bg-destructive/10 text-destructive'>
                    <IconBan className='h-4 w-4' />
                  </div>
                  <div>
                    <CardTitle className='text-sm font-semibold'>Công cụ Ghi bị Khóa Chặt (Disabled)</CardTitle>
                    <CardDescription className='text-xs'>
                      Bảo vệ môi trường theo đúng chính sách Non-Disruptive
                    </CardDescription>
                  </div>
                </div>
              </CardHeader>
              <CardContent className='pt-2'>
                <ul className='space-y-2 text-xs font-mono'>
                  {tools?.disabled_write_tools?.map((t: string) => (
                    <li key={t} className='flex items-center gap-2 p-2 rounded border bg-card text-muted-foreground'>
                      <IconBan className='h-3.5 w-3.5 text-destructive shrink-0' />
                      <span className='line-through'>{t}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* TAB 5: LỊCH SỬ & QUOTA */}
        <TabsContent value='history' className='space-y-4 pt-3'>
          <Card>
            <CardHeader className='pb-2'>
              <CardTitle className='text-sm font-semibold'>Nhật ký Hộp thư Outbox & Quota Ledger</CardTitle>
              <CardDescription className='text-xs'>
                Ghi nhận chi tiết từng thông điệp outbound, đảm bảo tính bất biến (idempotency) và không vượt 3.000 tin/tháng
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className='space-y-2 text-xs font-mono'>
                {history?.outbox?.length === 0 ? (
                  <p className='text-muted-foreground p-4 text-center'>Chưa có tin nhắn outbound trong tháng.</p>
                ) : (
                  history?.outbox?.map((m: any) => (
                    <div key={m.id} className='flex items-center justify-between p-2.5 rounded border bg-card'>
                      <div className='space-y-0.5'>
                        <div className='flex items-center gap-2'>
                          <span className='font-bold text-primary'>{m.id}</span>
                          <Badge variant='outline' className='text-[10px]'>
                            {m.is_critical ? 'CRITICAL ALERT' : 'STANDARD'}
                          </Badge>
                          <span className='text-muted-foreground text-[10px]'>Tới: {m.recipient_id}</span>
                        </div>
                        <p className='text-muted-foreground text-[11px]'>{m.message_snippet}...</p>
                      </div>
                      <span className='text-[10px] text-muted-foreground shrink-0'>{m.created_at}</span>
                    </div>
                  ))
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
