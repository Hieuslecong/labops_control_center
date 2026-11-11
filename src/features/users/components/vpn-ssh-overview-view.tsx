'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useLabOpsStore } from '@/features/labops/store';
import type { LabMember, WireGuardDevice } from '@/types/labops';

const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';
const RECENT_HANDSHAKE_SECONDS = 180;

type HealthResponse = {
  wireguard?: {
    status?: string;
    live?: boolean;
    provider?: string;
    peers_count?: number;
    interface?: string;
    endpoint?: string;
  };
  vpn_provider?: string;
  provider?: string;
};

type DeviceResult = WireGuardDevice[] | null;

function StatusLabel({ value }: { value?: string | null }) {
  const label = value || 'CHƯA XÁC MINH';
  const good = ['VERIFIED', 'ACTIVE', 'ONLINE', 'HEALTHY', 'READY'].includes(label);
  const error = ['PARTIAL_FAILURE', 'FAILED', 'OFFLINE', 'REVOKED', 'UNAVAILABLE'].includes(label);
  return (
    <Badge
      variant='outline'
      className={
        good
          ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
          : error
            ? 'border-rose-200 bg-rose-50 text-rose-800'
            : 'border-amber-200 bg-amber-50 text-amber-900'
      }
    >
      {label}
    </Badge>
  );
}

function recentlyHandshaken(device: WireGuardDevice, nowSeconds: number) {
  const timestamp = device.lastHandshake;
  return (
    typeof timestamp === 'number' &&
    Number.isFinite(timestamp) &&
    timestamp > 0 &&
    timestamp <= nowSeconds &&
    nowSeconds - timestamp <= RECENT_HANDSHAKE_SECONDS
  );
}

function needsAttention(member: LabMember, devices: DeviceResult | undefined) {
  return (
    member.status !== 'ACTIVE' ||
    member.workspaceStatus !== 'VERIFIED' ||
    (member.provisioningState != null &&
      !['ACTIVE', 'READY', 'VERIFIED', 'COMPLETED'].includes(member.provisioningState)) ||
    devices === null
  );
}

export function VpnSshOverviewView() {
  const members = useLabOpsStore((state) => state.members);
  const fetchAccessMembers = useLabOpsStore((state) => state.fetchAccessMembers);
  const memberSyncError = useLabOpsStore((state) => state.memberSyncError);

  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [deviceMap, setDeviceMap] = useState<Record<string, DeviceResult>>({});
  const [filter, setFilter] = useState('');
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (DEMO_MODE) return;
    setLoading(true);
    try {
      await fetchAccessMembers();
      const response = await fetch('/api/access/health', { cache: 'no-store' }).catch(() => null);
      if (response?.ok) {
        setHealth((await response.json()) as HealthResponse);
        setHealthError(false);
      } else {
        setHealth(null);
        setHealthError(true);
      }

      const rows = useLabOpsStore.getState().members;
      const next: Record<string, DeviceResult> = {};
      // Bound request fan-out; a failed lookup must NEVER appear as zero devices.
      for (let start = 0; start < rows.length; start += 5) {
        const batch = rows.slice(start, start + 5);
        const results = await Promise.all(
          batch.map(async (member) => {
            try {
              const res = await fetch(
                `/api/access/users/${encodeURIComponent(member.id)}/wireguard/devices`,
                { cache: 'no-store' }
              );
              if (!res.ok) return [member.id, null] as const;
              const data: unknown = await res.json();
              return [member.id, Array.isArray(data) ? (data as WireGuardDevice[]) : null] as const;
            } catch {
              return [member.id, null] as const;
            }
          })
        );
        for (const [id, value] of results) next[id] = value;
      }
      setDeviceMap(next);
      setLastUpdated(new Date().toLocaleTimeString('vi-VN'));
    } finally {
      setLoading(false);
    }
  }, [fetchAccessMembers]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const nowSeconds = Math.floor(Date.now() / 1000);
  const allDeviceResponsesLoaded =
    !DEMO_MODE && !loading && members.every((member) => Array.isArray(deviceMap[member.id]));
  const registeredDevices = Object.values(deviceMap).reduce(
    (total, devices) => total + (devices?.length || 0),
    0
  );
  const recentDevices = Object.values(deviceMap).reduce(
    (total, devices) =>
      total + (devices?.filter((device) => recentlyHandshaken(device, nowSeconds)).length || 0),
    0
  );
  const sshConfigured = members.filter((member) => !!member.sshIp && !!member.sshPort).length;
  const attentionCount = members.filter((member) =>
    needsAttention(member, deviceMap[member.id])
  ).length;
  const provider = health?.vpn_provider || health?.wireguard?.provider || health?.provider || null;
  const isWgEasyProvider = typeof provider === 'string' && /wg-easy/i.test(provider);

  const visibleMembers = useMemo(
    () =>
      members.filter((member) => {
        const query = filter.trim().toLowerCase();
        if (
          query &&
          ![member.username, member.fullName, member.email, member.workspaceName]
            .filter(Boolean)
            .some((value) => value?.toLowerCase().includes(query))
        ) {
          return false;
        }
        return !attentionOnly || needsAttention(member, deviceMap[member.id]);
      }),
    [members, filter, attentionOnly, deviceMap]
  );

  return (
    <div className='space-y-5'>
      <div className='flex flex-wrap items-start justify-between gap-3'>
        <div className='space-y-1'>
          <h2 className='text-xl font-semibold tracking-tight'>Tình trạng truy cập</h2>
          <p className='text-sm text-muted-foreground'>
            Theo dõi VPN, thiết bị và SSH Workspace. Cấp tài khoản được quản lý tập trung tại trang Thành viên.
          </p>
          <p className='text-xs text-muted-foreground'>
            {lastUpdated ? `Lần tải dữ liệu: ${lastUpdated}` : 'Chưa tải được dữ liệu'}
          </p>
        </div>
        <div className='flex flex-wrap gap-2'>
          <Button variant='outline' onClick={() => void refresh()} disabled={loading || DEMO_MODE}>
            {loading ? 'Đang cập nhật…' : 'Làm mới'}
          </Button>
          <Link
            href='/dashboard/users'
            className='inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:opacity-90'
          >
            Quản lý thành viên
          </Link>
        </div>
      </div>

      {(DEMO_MODE || memberSyncError || healthError || !isWgEasyProvider) && (
        <Card className='border-amber-200 bg-amber-50/60'>
          <CardContent className='space-y-1 p-4 text-sm text-amber-950'>
            {DEMO_MODE && <p>Dữ liệu minh họa; chưa kết nối hệ thống production.</p>}
            {memberSyncError && <p>Không thể đồng bộ thành viên: {memberSyncError}</p>}
            {healthError && <p>Không đọc được trạng thái dịch vụ VPN từ backend.</p>}
            {!DEMO_MODE && !healthError && !isWgEasyProvider && (
              <p>
                Backend chưa trả thông tin xác nhận provider là wg-easy. Không coi trạng thái
                WireGuard host hoặc nhãn HEALTHY là bằng chứng cho wg-easy.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      <div className='grid gap-3 sm:grid-cols-2 xl:grid-cols-4'>
        <Card>
          <CardContent className='space-y-2 p-4'>
            <p className='text-xs font-medium text-muted-foreground'>Nguồn quản lý VPN</p>
            <p className='text-lg font-semibold'>{provider || 'Chưa xác minh'}</p>
            <StatusLabel value={isWgEasyProvider ? health?.wireguard?.status : 'NOT_VERIFIED'} />
          </CardContent>
        </Card>
        <Card>
          <CardContent className='space-y-2 p-4'>
            <p className='text-xs font-medium text-muted-foreground'>Thiết bị VPN đã đăng ký</p>
            <p className='text-2xl font-semibold'>
              {allDeviceResponsesLoaded ? registeredDevices : '—'}
            </p>
            <p className='text-xs text-muted-foreground'>Theo kết quả API thiết bị; lỗi tải không được tính là 0</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className='space-y-2 p-4'>
            <p className='text-xs font-medium text-muted-foreground'>Handshake trong 3 phút</p>
            <p className='text-2xl font-semibold'>
              {allDeviceResponsesLoaded ? recentDevices : '—'}
            </p>
            <p className='text-xs text-muted-foreground'>Không đồng nghĩa với SSH đăng nhập thành công</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className='space-y-2 p-4'>
            <p className='text-xs font-medium text-muted-foreground'>Endpoint SSH khai báo</p>
            <p className='text-2xl font-semibold'>{sshConfigured}</p>
            <p className='text-xs text-muted-foreground'>Chưa đồng nghĩa với TCP hoặc SSH đã kiểm chứng</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className='space-y-4 p-4'>
          <div className='flex flex-wrap items-center justify-between gap-3'>
            <div>
              <h3 className='font-semibold'>Danh sách quyền truy cập</h3>
              <p className='text-xs text-muted-foreground'>
                {members.length} thành viên · {attentionCount} cần xác minh hoặc xử lý
              </p>
            </div>
            <div className='flex flex-wrap items-center gap-2'>
              <Input
                aria-label='Tìm thành viên'
                placeholder='Tìm thành viên, email, workspace…'
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                className='w-64 max-w-full'
              />
              <Button
                variant={attentionOnly ? 'default' : 'outline'}
                onClick={() => setAttentionOnly((previous) => !previous)}
                aria-pressed={attentionOnly}
              >
                Cần xử lý
              </Button>
            </div>
          </div>

          <div className='overflow-x-auto'>
            <table className='w-full min-w-[850px] text-left text-sm'>
              <thead>
                <tr className='border-b text-xs text-muted-foreground'>
                  <th className='px-3 py-3 font-medium'>Thành viên</th>
                  <th className='px-3 py-3 font-medium'>Máy chủ / Workspace</th>
                  <th className='px-3 py-3 font-medium'>Thiết bị VPN</th>
                  <th className='px-3 py-3 font-medium'>SSH đã khai báo</th>
                  <th className='px-3 py-3 font-medium'>Cấp phát</th>
                  <th className='px-3 py-3 text-right font-medium'>Chi tiết</th>
                </tr>
              </thead>
              <tbody>
                {visibleMembers.map((member) => {
                  const devices = deviceMap[member.id];
                  const recent = devices?.filter((device) => recentlyHandshaken(device, nowSeconds)).length || 0;
                  return (
                    <tr key={member.id} className='border-b align-top last:border-0 hover:bg-muted/30'>
                      <td className='px-3 py-4'>
                        <div className='font-medium'>@{member.username}</div>
                        <div className='text-xs text-muted-foreground'>{member.fullName}</div>
                      </td>
                      <td className='px-3 py-4'>
                        <div className='font-medium'>{member.assignedServer}</div>
                        <div className='text-xs text-muted-foreground'>{member.workspaceName || 'Chưa có workspace'}</div>
                      </td>
                      <td className='px-3 py-4'>
                        <div className='font-medium'>
                          {devices === undefined || devices === null ? 'Không thể xác minh' : `${devices.length} thiết bị`}
                        </div>
                        <div className='text-xs text-muted-foreground'>
                          {devices ? `${recent} handshake gần đây` : 'Chưa có dữ liệu xác thực'}
                        </div>
                      </td>
                      <td className='px-3 py-4'>
                        <StatusLabel value={member.sshKeyStatus} />
                        <div className='mt-1 break-all text-xs text-muted-foreground'>
                          {member.sshIp && member.sshPort
                            ? `${member.sshIp}:${member.sshPort} · chưa thử kết nối`
                            : 'Chưa có endpoint được xác nhận'}
                        </div>
                      </td>
                      <td className='px-3 py-4'>
                        <StatusLabel value={member.provisioningState || member.status} />
                        <div className='mt-1 text-xs text-muted-foreground'>
                          Workspace: {member.workspaceStatus || 'NOT_VERIFIED'}
                        </div>
                      </td>
                      <td className='px-3 py-4 text-right'>
                        <Link
                          href='/dashboard/users'
                          className='inline-flex h-9 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted'
                          aria-label={`Quản lý thành viên ${member.username}`}
                        >
                          Quản lý
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!visibleMembers.length && (
              <p className='p-8 text-center text-sm text-muted-foreground'>
                {memberSyncError ? 'Không tải được thành viên.' : 'Không tìm thấy dữ liệu phù hợp.'}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      <details className='rounded-xl border p-4'>
        <summary className='cursor-pointer text-sm font-medium'>
          Cấu hình & chính sách truy cập
        </summary>
        <div className='mt-3 space-y-2 text-sm text-muted-foreground'>
          <p>VPN: wg-easy là nguồn quản lý chính; chỉ xác nhận khi backend trả đúng provider và trạng thái thực tế.</p>
          <p>SSH quản trị host và SSH workspace là hai dịch vụ độc lập, phải được kiểm tra riêng.</p>
          <p>Quản trị tài khoản, cấp khóa, thu hồi và tải cấu hình được thực hiện tại trang Thành viên.</p>
          <p>Trang này chỉ đọc trạng thái; không trực tiếp sửa firewall, WireGuard hoặc Docker runtime.</p>
        </div>
      </details>
    </div>
  );
}
