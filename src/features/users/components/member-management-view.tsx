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

function generateSampleOpenSshPublicKey(username: string): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  const header = [0, 0, 0, 11, 115, 115, 104, 45, 101, 100, 50, 53, 53, 49, 57, 0, 0, 0, 32];
  const combined = new Uint8Array(header.length + bytes.length);
  combined.set(header, 0);
  combined.set(bytes, header.length);
  let binary = '';
  for (let i = 0; i < combined.length; i++) {
    binary += String.fromCharCode(combined[i]);
  }
  return `ssh-ed25519 ${btoa(binary)} ${username || 'researcher'}@labops-client`;
}

function statusBadge(status?: string) {
  const value = status || 'NOT_VERIFIED';
  const style =
    value === 'ACTIVE' || value === 'AVAILABLE' || value === 'VERIFIED' || value === 'SSH_KEY_ENROLLED'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : value === 'PARTIAL_FAILURE' || value === 'UNAVAILABLE'
        ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
        : 'bg-amber-500/10 text-amber-800 dark:text-amber-200';
  return (
    <Badge variant='outline' className={style}>
      {value}
    </Badge>
  );
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
  const [sshLabel, setSshLabel] = useState('Laptop cá nhân');
  const [sshKeyInput, setSshKeyInput] = useState('');
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
    const results = await Promise.all(
      rows.map(async (member) => {
        try {
          const response = await fetch(`/api/access/users/${encodeURIComponent(member.id)}/wireguard/devices`, {
            cache: 'no-store'
          });
          if (!response.ok) return [member.id, []] as const;
          const devices = await response.json();
          return [member.id, Array.isArray(devices) ? devices : []] as const;
        } catch {
          return [member.id, []] as const;
        }
      })
    );
    setDevicesByUser(Object.fromEntries(results));
  }, [fetchAccessMembers]);

  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const visibleMembers = useMemo(
    () =>
      members.filter((member) => {
        const query = filter.toLowerCase();
        return (
          !query ||
          [member.username, member.fullName, member.email, member.workspaceName].some((value) =>
            value?.toLowerCase().includes(query)
          )
        );
      }),
    [filter, members]
  );

  const runAction = async (path: string, method: string, body?: unknown) => {
    setBusy(true);
    try {
      const response = await fetch(path, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const msg =
          typeof result.detail === 'string'
            ? result.detail
            : result.detail?.message || `Yêu cầu thất bại (${response.status})`;
        throw new Error(msg);
      }
      if (result.status === 'PARTIAL_FAILURE')
        toast.error('Thu hồi chưa hoàn tất; cần kiểm tra VPN và phiên SSH.');
      else toast.success('Backend đã xử lý yêu cầu thành công.');
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
    const result = await runAction(
      `/api/access/users/${encodeURIComponent(member.id)}/wireguard/devices`,
      'POST',
      {
        name: deviceName.trim(),
        publicKey: publicKey.trim()
      }
    );
    if (result) {
      setPublicKey('');
      setDeviceName('');
    }
  };

  const enrollMemberSshKey = async (member: LabMember) => {
    if (!sshKeyInput.trim()) {
      toast.error('Vui lòng dán khóa công khai OpenSSH (ssh-ed25519 / ssh-rsa) trước khi cấp.');
      return;
    }
    const result = await runAction('/api/labops/v2/ssh-keys', 'POST', {
      username: member.username,
      label: sshLabel.trim() || 'Khóa SSH cá nhân',
      public_key: sshKeyInput.trim()
    });
    if (result) {
      setSshKeyInput('');
      toast.success(
        result.ssh_endpoint
          ? `Đã cấp khóa SSH! Lệnh kết nối: ${result.ssh_endpoint}`
          : `Đã đăng ký khóa SSH (${result.fingerprint}) cho @${member.username}`
      );
    }
  };

  return (
    <div className='space-y-5'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <div>
          <h1 className='text-2xl font-semibold'>Quản lý Thành viên & Truy cập SSH/VPN</h1>
          <p className='text-sm text-muted-foreground'>
            Cấp tài khoản thành viên · Đăng ký khóa công khai OpenSSH · Quản lý cổng SSH Workspace & WireGuard
          </p>
        </div>
        <div className='flex gap-2'>
          <Button variant='outline' onClick={() => void refresh()} disabled={busy || DEMO_MODE}>
            Làm mới dữ liệu
          </Button>
          <Button onClick={() => setWizardOpen(true)} disabled={DEMO_MODE}>
            + Tạo Thành viên & Cấp SSH
          </Button>
        </div>
      </div>

      {DEMO_MODE && (
        <Card>
          <CardContent className='p-4 text-sm text-amber-800'>
            DEMO: dữ liệu minh họa, mọi thao tác cấp quyền đã tắt.
          </CardContent>
        </Card>
      )}
      {memberSyncError && (
        <Card>
          <CardContent className='p-4 text-sm text-amber-800'>{memberSyncError}</CardContent>
        </Card>
      )}

      <div className='grid gap-3 lg:grid-cols-2' aria-label='Phân tách hệ thống SSH'>
        <Card>
          <CardContent className='space-y-2 p-4'>
            <h2 className='font-medium'>Legacy Host SSH (Cổng 22)</h2>
            <div className='flex flex-wrap gap-2'>
              <Badge variant='outline'>Bảo toàn nguyên trạng trên aiotlab-rack</Badge>
              <Badge variant='outline'>Không tự động chỉnh sửa</Badge>
            </div>
            <p className='text-xs text-muted-foreground'>
              Cổng SSH quản trị truyền thống của máy chủ vật lý được bảo vệ tuyệt đối.
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className='space-y-2 p-4'>
            <h2 className='font-medium'>OpenSSH Workspace (Per-Member Public Key)</h2>
            <div className='flex flex-wrap gap-2'>
              <Badge variant='outline'>Managed by LabOps V2</Badge>
              {statusBadge('ACTIVE')}
            </div>
            <p className='text-xs text-muted-foreground'>
              Mỗi thành viên sử dụng khóa công khai OpenSSH riêng và được cấp cổng SSH riêng biệt (dải{' '}
              <code>22001–29999</code>) vào Workspace cô lập.
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className='space-y-4 p-4'>
          <Input
            aria-label='Tìm tài khoản'
            placeholder='Tìm username, tên, email hoặc workspace'
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
          <div className='overflow-x-auto'>
            <table className='w-full min-w-[900px] text-left text-sm'>
              <thead>
                <tr className='border-b text-xs text-muted-foreground'>
                  <th className='p-3'>Tài khoản</th>
                  <th className='p-3'>Role</th>
                  <th className='p-3'>Server / Workspace</th>
                  <th className='p-3'>OpenSSH & Cổng</th>
                  <th className='p-3'>WireGuard</th>
                  <th className='p-3'>Trạng thái</th>
                  <th className='p-3'>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {visibleMembers.map((member) => {
                  const devices = devicesByUser[member.id] || member.wireguard?.devices || [];
                  const expanded = managedUser === member.id;
                  const canEnroll =
                    !DEMO_MODE && member.workspaceStatus === 'VERIFIED' && health?.network_policy === 'VERIFIED';
                  return (
                    <Fragment key={member.id}>
                      <tr className='border-b align-top'>
                        <td className='p-3'>
                          <div className='font-medium'>@{member.username}</div>
                          <div className='text-xs text-muted-foreground'>
                            {member.fullName} · {member.email}
                          </div>
                        </td>
                        <td className='p-3'>{member.role}</td>
                        <td className='p-3'>
                          <span className='font-mono uppercase'>{member.assignedServer}</span>
                          <div className='text-xs text-muted-foreground'>{member.workspaceName}</div>
                        </td>
                        <td className='p-3'>
                          {statusBadge(member.sshKeyStatus || 'NOT_CONFIGURED')}
                          <div className='text-xs font-mono text-muted-foreground mt-1'>
                            {member.sshPort
                              ? `ssh -p ${member.sshPort} ${member.username}@${member.sshIp || '192.168.167.250'}`
                              : 'Chưa cấp khóa SSH'}
                          </div>
                        </td>
                        <td className='p-3'>
                          {statusBadge(
                            devices.some((device) => device.status === 'ACTIVE')
                              ? 'ACTIVE'
                              : devices[0]?.status || 'NOT_CONFIGURED'
                          )}
                          <div className='text-xs text-muted-foreground'>{devices.length} thiết bị</div>
                        </td>
                        <td className='p-3'>
                          {statusBadge(member.status)}
                          <div className='text-xs text-muted-foreground'>{member.provisioningState || '—'}</div>
                        </td>
                        <td className='p-3' aria-label='Thao tác'>
                          <div className='flex flex-wrap gap-2'>
                            <Button
                              size='sm'
                              variant={expanded ? 'default' : 'outline'}
                              onClick={() => setManagedUser(expanded ? null : member.id)}
                            >
                              {expanded ? 'Đóng' : 'Cấp / Quản lý SSH'}
                            </Button>
                            <Button
                              size='sm'
                              variant='destructive'
                              disabled={busy || DEMO_MODE || member.status === 'SUSPENDED'}
                              onClick={() =>
                                void runAction(`/api/access/users/${encodeURIComponent(member.id)}/revoke`, 'POST')
                              }
                            >
                              Thu hồi
                            </Button>
                          </div>
                        </td>
                      </tr>
                      {expanded && (
                        <tr key={`${member.id}-details`} className='border-b bg-muted/20'>
                          <td colSpan={7} className='p-4' aria-label='Chi tiết tài khoản'>
                            <div className='grid gap-5 lg:grid-cols-2'>
                              {/* CỘT 1: CẤP KHÓA OPENSSH & CỔNG SSH CHO THÀNH VIÊN */}
                              <section className='space-y-3 rounded-lg border bg-background p-4'>
                                <div className='flex items-center justify-between'>
                                  <h3 className='font-semibold text-sm'>
                                    1. Cấp / Cập nhật Khóa OpenSSH cho @{member.username}
                                  </h3>
                                  {statusBadge(member.sshKeyStatus || 'NOT_CONFIGURED')}
                                </div>
                                {member.sshPort && (
                                  <div className='rounded-md bg-emerald-500/10 border border-emerald-500/30 p-2.5 text-xs font-mono space-y-1'>
                                    <p className='font-semibold text-emerald-700 dark:text-emerald-300'>
                                      Endpoint SSH đã cấp:
                                    </p>
                                    <p>
                                      <code>
                                        ssh -p {member.sshPort} {member.username}@
                                        {member.sshIp || '192.168.167.250'}
                                      </code>
                                    </p>
                                    {member.sshFingerprint && (
                                      <p className='text-[11px] text-muted-foreground'>
                                        Fingerprint: {member.sshFingerprint}
                                      </p>
                                    )}
                                  </div>
                                )}
                                <Input
                                  aria-label='Nhãn khóa SSH'
                                  placeholder='Nhãn thiết bị (VD: Laptop cá nhân)'
                                  value={sshLabel}
                                  onChange={(e) => setSshLabel(e.target.value)}
                                />
                                <div className='space-y-1.5'>
                                  <div className='flex items-center justify-between'>
                                    <span className='text-xs text-muted-foreground'>
                                      OpenSSH Public Key (ssh-ed25519 / ssh-rsa)
                                    </span>
                                    <Button
                                      type='button'
                                      size='sm'
                                      variant='outline'
                                      className='h-6 text-[11px]'
                                      onClick={() => {
                                        setSshKeyInput(generateSampleOpenSshPublicKey(member.username));
                                        toast.info('Đã điền khóa công khai OpenSSH Ed25519 hợp lệ.');
                                      }}
                                    >
                                      Điền khóa Ed25519 mẫu
                                    </Button>
                                  </div>
                                  <textarea
                                    rows={3}
                                    className='w-full rounded-md border bg-background p-2 font-mono text-xs'
                                    placeholder='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@laptop'
                                    value={sshKeyInput}
                                    onChange={(e) => setSshKeyInput(e.target.value)}
                                  />
                                </div>
                                <Button
                                  size='sm'
                                  disabled={busy || DEMO_MODE || !sshKeyInput.trim()}
                                  onClick={() => void enrollMemberSshKey(member)}
                                >
                                  {busy ? 'Đang xử lý…' : 'Lưu Khóa OpenSSH & Cấp Cổng SSH'}
                                </Button>
                              </section>

                              {/* CỘT 2: WIREGUARD VPN THEO THIẾT BỊ */}
                              <section className='space-y-3 rounded-lg border bg-background p-4'>
                                <h3 className='font-semibold text-sm'>2. Thiết bị WireGuard VPN</h3>
                                {devices.length ? (
                                  devices.map((device) => (
                                    <div
                                      key={device.deviceId}
                                      className='flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-xs'
                                    >
                                      <div>
                                        <strong>{device.name}</strong> · {device.clientIp} · key …
                                        {device.publicKey.slice(-8)}
                                        <div className='text-muted-foreground'>
                                          Handshake:{' '}
                                          {device.lastHandshake
                                            ? new Date(device.lastHandshake * 1000).toLocaleString()
                                            : 'chưa có'}{' '}
                                          · {device.status}
                                        </div>
                                      </div>
                                      <Button
                                        size='sm'
                                        variant='destructive'
                                        disabled={busy || DEMO_MODE || device.status === 'REVOKED'}
                                        onClick={() =>
                                          void runAction(
                                            `/api/access/users/${encodeURIComponent(member.id)}/wireguard/devices/${encodeURIComponent(device.deviceId)}`,
                                            'DELETE'
                                          )
                                        }
                                      >
                                        Thu hồi thiết bị
                                      </Button>
                                    </div>
                                  ))
                                ) : (
                                  <p className='text-xs text-muted-foreground'>Chưa có thiết bị VPN đăng ký.</p>
                                )}
                                <Input
                                  aria-label='Tên thiết bị'
                                  placeholder='Tên thiết bị VPN'
                                  value={deviceName}
                                  onChange={(event) => setDeviceName(event.target.value)}
                                />
                                <Input
                                  aria-label='WireGuard public key'
                                  placeholder='WireGuard public key (Base64 44 ký tự)'
                                  value={publicKey}
                                  onChange={(event) => setPublicKey(event.target.value)}
                                />
                                <Button
                                  size='sm'
                                  variant='outline'
                                  disabled={!canEnroll || busy || !deviceName.trim() || !publicKey.trim()}
                                  onClick={() => void registerDevice(member)}
                                >
                                  Đăng ký WireGuard Public Key
                                </Button>
                                {!canEnroll && (
                                  <p className='text-xs text-amber-700'>
                                    Lưu ý: Giao diện <code>wg0</code> trên máy chủ hiện chưa khởi tạo nên tính năng
                                    WireGuard đang ở chế độ chờ xác minh. Bạn vẫn có thể cấp và sử dụng{' '}
                                    <strong>OpenSSH ở cột bên trái</strong> qua mạng LAN (<code>192.168.167.250</code>
                                    ).
                                  </p>
                                )}
                                <div className='border-t pt-2 text-xs text-muted-foreground'>
                                  <p>Workspace: {member.workspaceStatus || 'NOT_VERIFIED'}</p>
                                  <p>Last VPN handshake: {latestHandshake(devices)}</p>
                                </div>
                              </section>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
                {!visibleMembers.length && (
                  <tr>
                    <td colSpan={7} className='p-8 text-center text-sm text-muted-foreground'>
                      {memberSyncError ? 'Không thể tải danh sách từ backend.' : 'Chưa có thành viên nào.'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
      <MemberProvisioningWizard
        open={wizardOpen}
        onOpenChange={(open) => {
          setWizardOpen(open);
          if (!open) void refresh();
        }}
      />
    </div>
  );
}
