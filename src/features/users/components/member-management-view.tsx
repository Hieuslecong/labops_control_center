'use client';

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useLabOpsStore } from '@/features/labops/store';
import { MemberProvisioningWizard } from './member-provisioning-wizard';
import { LabMember, WireGuardDevice } from '@/types/labops';
import { toast } from 'sonner';

const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';

type AccessHealth = {
  wireguard?: { status?: string; live?: boolean; peers_count?: number };
  teleport?: { status?: string; version?: string | null };
  workspace_agents?: string;
  network_policy?: string;
};

function statusBadge(status?: string) {
  const value = status || 'NOT_VERIFIED';
  const style = value === 'ACTIVE' || value === 'AVAILABLE' || value === 'VERIFIED'
    ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
    : value === 'PARTIAL_FAILURE' || value === 'UNAVAILABLE'
      ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
      : 'bg-amber-500/10 text-amber-800 dark:text-amber-200';
  return <Badge variant='outline' className={style}>{value}</Badge>;
}

function latestHandshake(devices: WireGuardDevice[]) {
  const latest = Math.max(0, ...devices.map((device) => device.lastHandshake || 0));
  return latest ? new Date(latest * 1000).toLocaleString() : 'Chưa có handshake';
}

export function MemberManagementView() {
  const members = useLabOpsStore((state) => state.members);
  const memberSyncError = useLabOpsStore((state) => state.memberSyncError);
  const fetchAccessMembers = useLabOpsStore((state) => state.fetchAccessMembers);
  const [health, setHealth] = useState<AccessHealth | null>(null);
  const [devicesByUser, setDevicesByUser] = useState<Record<string, WireGuardDevice[]>>({});
  const [filter, setFilter] = useState('');
  const [wizardOpen, setWizardOpen] = useState(false);
  const [managedUser, setManagedUser] = useState<string | null>(null);
  const [deviceName, setDeviceName] = useState('');
  const [publicKey, setPublicKey] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (DEMO_MODE) return;
    await fetchAccessMembers();
    try {
      const healthResponse = await fetch('/api/access/health', { cache: 'no-store' });
      setHealth(healthResponse.ok ? await healthResponse.json() : null);
    } catch {
      setHealth(null);
    }
    const rows = useLabOpsStore.getState().members;
    const results = await Promise.all(rows.map(async (member) => {
      try {
        const response = await fetch(`/api/access/users/${encodeURIComponent(member.id)}/wireguard/devices`, { cache: 'no-store' });
        if (!response.ok) return [member.id, []] as const;
        const devices = await response.json();
        return [member.id, Array.isArray(devices) ? devices : []] as const;
      } catch {
        return [member.id, []] as const;
      }
    }));
    setDevicesByUser(Object.fromEntries(results));
  }, [fetchAccessMembers]);

  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { void refresh(); }, [refresh]);

  const visibleMembers = useMemo(() => members.filter((member) => {
    const query = filter.toLowerCase();
    return !query || [member.username, member.fullName, member.email, member.workspaceName]
      .some((value) => value?.toLowerCase().includes(query));
  }), [filter, members]);

  const runAction = async (path: string, method: string, body?: unknown) => {
    setBusy(true);
    try {
      const response = await fetch(path, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || `Yêu cầu thất bại (${response.status})`);
      if (result.status === 'PARTIAL_FAILURE') toast.error('Thu hồi chưa hoàn tất; cần kiểm tra VPN, Teleport và phiên SSH.');
      else toast.success('Backend đã xử lý yêu cầu.');
      await refresh();
      return result;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Không thể xác minh kết quả từ backend.');
      return null;
    } finally {
      setBusy(false);
    }
  };

  const registerDevice = async (member: LabMember) => {
    if (!publicKey.trim() || !deviceName.trim()) return;
    const result = await runAction(`/api/access/users/${encodeURIComponent(member.id)}/wireguard/devices`, 'POST', {
      name: deviceName.trim(),
      publicKey: publicKey.trim()
    });
    if (result) {
      setPublicKey('');
      setDeviceName('');
    }
  };

  return (
    <div className='space-y-5'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <div>
          <h1 className='text-2xl font-semibold'>Quản lý truy cập</h1>
          <p className='text-sm text-muted-foreground'>WireGuard theo thiết bị · Teleport MFA · SSH certificate</p>
        </div>
        <div className='flex gap-2'>
          <Button variant='outline' onClick={() => void refresh()} disabled={busy || DEMO_MODE}>Làm mới dữ liệu</Button>
          <Button onClick={() => setWizardOpen(true)} disabled={DEMO_MODE}>Tạo yêu cầu</Button>
        </div>
      </div>

      {DEMO_MODE && <Card><CardContent className='p-4 text-sm text-amber-800'>DEMO: dữ liệu minh họa, mọi thao tác cấp quyền đã tắt.</CardContent></Card>}
      {memberSyncError && <Card><CardContent className='p-4 text-sm text-amber-800'>{memberSyncError}</CardContent></Card>}

      <div className='grid gap-3 lg:grid-cols-2' aria-label='Phân tách hệ thống SSH'>
        <Card><CardContent className='space-y-2 p-4'>
          <h2 className='font-medium'>Legacy SSH</h2>
          <div className='flex flex-wrap gap-2'>
            <Badge variant='outline'>Existing · trạng thái live chưa xác minh</Badge>
            <Badge variant='outline'>Unmanaged by Secure Access v2</Badge>
          </div>
          <p className='text-xs text-muted-foreground'>Hệ thống SSH cũ được giữ độc lập. Quy trình cấp tài khoản mới của LabOps không tạo hoặc sửa tài khoản, khóa, cổng hay cấu hình SSH cũ.</p>
        </CardContent></Card>
        <Card><CardContent className='space-y-2 p-4'>
          <h2 className='font-medium'>Teleport SSH</h2>
          <div className='flex flex-wrap gap-2'>
            <Badge variant='outline'>Managed by LabOps</Badge>
            {statusBadge('NOT_VERIFIED')}
          </div>
          <p className='text-xs text-muted-foreground'>Chỉ dùng cho yêu cầu truy cập mới sau khi Gateway, Teleport và workspace riêng được xác minh.</p>
        </CardContent></Card>
      </div>

      <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-4'>
        <Card><CardContent className='space-y-2 p-4'><p className='text-xs text-muted-foreground'>WireGuard interface</p>{statusBadge(health?.wireguard?.status || 'NOT_VERIFIED')}<p className='text-xs'>Peer đang thấy: {health?.wireguard?.peers_count ?? '—'}</p></CardContent></Card>
        <Card><CardContent className='space-y-2 p-4'><p className='text-xs text-muted-foreground'>Teleport</p>{statusBadge(health?.teleport?.status || 'NOT_VERIFIED')}<p className='text-xs'>Version: {health?.teleport?.version || 'Chưa xác minh'}</p></CardContent></Card>
        <Card><CardContent className='space-y-2 p-4'><p className='text-xs text-muted-foreground'>Workspace agents</p>{statusBadge(health?.workspace_agents)}</CardContent></Card>
        <Card><CardContent className='space-y-2 p-4'><p className='text-xs text-muted-foreground'>VPN network policy</p>{statusBadge(health?.network_policy)}</CardContent></Card>
      </div>

      <Card>
        <CardContent className='space-y-4 p-4'>
          <Input aria-label='Tìm tài khoản' placeholder='Tìm username, tên, email hoặc workspace' value={filter} onChange={(event) => setFilter(event.target.value)} />
          <div className='overflow-x-auto'>
            <table className='w-full min-w-[900px] text-left text-sm'>
              <thead><tr className='border-b text-xs text-muted-foreground'><th className='p-3'>Tài khoản</th><th className='p-3'>Role</th><th className='p-3'>Server / Workspace</th><th className='p-3'>WireGuard</th><th className='p-3'>Teleport</th><th className='p-3'>TTL tối đa</th><th className='p-3'>Trạng thái</th><th className='p-3'>Thao tác</th></tr></thead>
              <tbody>
                {visibleMembers.map((member) => {
                  const devices = devicesByUser[member.id] || member.wireguard?.devices || [];
                  const expanded = managedUser === member.id;
                  const canEnroll = !DEMO_MODE && member.workspaceStatus === 'VERIFIED' && health?.network_policy === 'VERIFIED';
                  return <Fragment key={member.id}>
                    <tr className='border-b align-top'>
                      <td className='p-3'><div className='font-medium'>@{member.username}</div><div className='text-xs text-muted-foreground'>{member.fullName} · {member.email}</div></td>
                      <td className='p-3'>{member.role}</td>
                      <td className='p-3'>{member.assignedServer}<div className='text-xs text-muted-foreground'>{member.workspaceName}</div></td>
                      <td className='p-3'>{statusBadge(devices.some((device) => device.status === 'ACTIVE') ? 'ACTIVE' : devices[0]?.status || 'NOT_CONFIGURED')}<div className='text-xs text-muted-foreground'>{devices.length} thiết bị</div></td>
                      <td className='p-3'>{statusBadge(member.teleportStatus)}</td>
                      <td className='p-3'>{member.role === 'ADMIN' ? '2 giờ' : member.role === 'RESEARCHER' ? '12 giờ' : 'Chưa áp dụng'}</td>
                      <td className='p-3'>{statusBadge(member.status)}<div className='text-xs text-muted-foreground'>{member.provisioningState || '—'}</div></td>
                      <td className='p-3' aria-label='Thao tác'><div className='flex flex-wrap gap-2'>
                        <Button size='sm' variant='outline' onClick={() => setManagedUser(expanded ? null : member.id)}>Manage</Button>
                        <Button size='sm' variant='destructive' disabled={busy || DEMO_MODE || member.status === 'SUSPENDED'} onClick={() => void runAction(`/api/access/users/${encodeURIComponent(member.id)}/revoke`, 'POST')}>Thu hồi</Button>
                      </div></td>
                    </tr>
                    {expanded && <tr key={`${member.id}-details`} className='border-b bg-muted/20'><td colSpan={8} className='p-4' aria-label='Chi tiết tài khoản'>
                      <div className='grid gap-5 lg:grid-cols-2'>
                        <section className='space-y-3'>
                          <h3 className='font-medium'>Thiết bị VPN</h3>
                          {devices.length ? devices.map((device) => <div key={device.deviceId} className='flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-xs'>
                            <div><strong>{device.name}</strong> · {device.clientIp} · key …{device.publicKey.slice(-8)}<div className='text-muted-foreground'>Handshake: {device.lastHandshake ? new Date(device.lastHandshake * 1000).toLocaleString() : 'chưa có'} · {device.status}</div></div>
                            <Button size='sm' variant='destructive' disabled={busy || DEMO_MODE || device.status === 'REVOKED'} onClick={() => void runAction(`/api/access/users/${encodeURIComponent(member.id)}/wireguard/devices/${encodeURIComponent(device.deviceId)}`, 'DELETE')}>Thu hồi thiết bị</Button>
                          </div>) : <p className='text-sm text-muted-foreground'>Chưa có thiết bị đăng ký.</p>}
                          <p className='text-xs text-muted-foreground'>Last handshake và peer được đọc từ WireGuard; không hiển thị private key.</p>
                        </section>
                        <section className='space-y-3'>
                          <h3 className='font-medium'>Đăng ký thiết bị</h3>
                          <Input aria-label='Tên thiết bị' placeholder='Tên thiết bị' value={deviceName} onChange={(event) => setDeviceName(event.target.value)} />
                          <Input aria-label='WireGuard public key' placeholder='WireGuard public key từ thiết bị người dùng' value={publicKey} onChange={(event) => setPublicKey(event.target.value)} />
                          <Button size='sm' disabled={!canEnroll || busy || !deviceName.trim() || !publicKey.trim()} onClick={() => void registerDevice(member)}>Đăng ký public key</Button>
                          {!canEnroll && <p className='text-xs text-amber-700'>Cần xác minh workspace và network policy trước khi thêm peer.</p>}
                          <div className='border-t pt-3 text-xs text-muted-foreground'>
                            <p>Workspace: {member.workspaceStatus || 'NOT_VERIFIED'}</p>
                            <p>Last VPN handshake: {latestHandshake(devices)}</p>
                            <p>SSH session / lịch sử Teleport: chưa có collector đã xác minh.</p>
                          </div>
                          <Button size='sm' variant='outline' disabled={busy || DEMO_MODE || member.workspaceStatus !== 'VERIFIED' || health?.network_policy !== 'VERIFIED'} onClick={() => void runAction(`/api/access/users/${encodeURIComponent(member.id)}/activation`, 'POST')}>Gửi lời mời Teleport</Button>
                        </section>
                      </div>
                    </td></tr>}
                  </Fragment>;
                })}
                {!visibleMembers.length && <tr><td colSpan={8} className='p-8 text-center text-sm text-muted-foreground'>{memberSyncError ? 'Không thể tải danh sách từ backend.' : 'Chưa có yêu cầu truy cập.'}</td></tr>}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
      <MemberProvisioningWizard open={wizardOpen} onOpenChange={(open) => { setWizardOpen(open); if (!open) void refresh(); }} />
    </div>
  );
}
