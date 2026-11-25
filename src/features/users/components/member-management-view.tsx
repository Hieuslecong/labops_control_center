'use client';

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useLabOpsStore } from '@/features/labops/store';
import { MemberProvisioningWizard } from './member-provisioning-wizard';
import { SafeMemberDeleteDialog } from './safe-member-delete-dialog';
import { CreateWebAccountDialog } from '@/features/mram/components/create-web-account-dialog';
import { SupervisorGroupsPanel } from '@/features/mram/components/supervisor-groups-panel';
import { WebAccountsPanel } from '@/features/mram/components/web-accounts-panel';
import { LecturerOption, SupervisorGroup, WebPortalAccountItem } from '@/features/mram/types';
import { labopsCall } from '@/features/labops/api';
import { LabMember, WireGuardDevice, WireGuardVpnOverview } from '@/types/labops';
import { toast } from 'sonner';

const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === 'true';

type MemberFilterTab = 'ALL' | 'ACTIVE_PENDING' | 'WORKSTATION' | 'RACK' | 'INACTIVE';

const INACTIVE_STATUSES = new Set([
  'SUSPENDED',
  'ARCHIVED',
  'DELETE_ELIGIBLE',
  'DELETED',
  'VERIFIED_REVOKED'
]);

function downloadTextFile(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function isWebOnlyUnprovisioned(member: LabMember): boolean {
  return (
    (member.provisioningState === 'WEB_ONLY' || member.status === 'WEB_ACTIVE') &&
    !member.sshPort &&
    member.sshAccessState !== 'VERIFIED_READY'
  );
}

function formatMemberRoleLabel(role?: string): string {
  switch (role) {
    case 'STUDENT':
      return 'Sinh viên';
    case 'RESEARCHER':
      return 'Nghiên cứu viên';
    case 'ADMIN':
      return 'Quản trị viên';
    default:
      return role || 'RESEARCHER';
  }
}

function resolveMemberHostIp(member: LabMember): string {
  return (
    member.sshIp ||
    (member.assignedServer === 'workstation' ? '192.168.167.251' : '192.168.167.250')
  );
}

function resolveMemberSshUser(member: LabMember): string {
  return member.username;
}

function parseUdpPort(endpoint?: string): string {
  if (!endpoint) return '2204';
  const parts = endpoint.trim().split(':');
  const last = parts[parts.length - 1];
  return last && /^\d+$/.test(last) ? last : '2204';
}

function getMemberWireGuardStatus(member: LabMember, devices: WireGuardDevice[]): string {
  if (
    devices.some(
      (d) => d.status === 'ACTIVE' && d.enabled && !d.deviceId.startsWith('dev-pending-')
    )
  ) {
    return 'ACTIVE';
  }
  if (
    devices.some(
      (d) => d.status === 'PENDING_PROVISIONING' || d.deviceId.startsWith('dev-pending-')
    ) ||
    member.wireguard?.status === 'PENDING_PROVISIONING'
  ) {
    return 'PENDING_PROVISIONING';
  }
  return devices[0]?.status || member.wireguard?.status || 'NOT_CONFIGURED';
}

function statusBadge(status?: string) {
  const value = status || 'NOT_VERIFIED';
  const isSuccess =
    value === 'ACTIVE' ||
    value === 'ACTIVATED' ||
    value === 'READY' ||
    value === 'AVAILABLE' ||
    value === 'VERIFIED' ||
    value === 'SSH_RUNTIME_VERIFIED' ||
    value === 'SSH_KEY_ENROLLED';
  const isDanger =
    value === 'PARTIAL_FAILURE' ||
    value === 'UNAVAILABLE' ||
    value === 'FAILED' ||
    value === 'VERIFIED_REVOKED' ||
    value === 'REVOKED' ||
    value === 'SSH_ACCESS_DENIED' ||
    value === 'SSH_RUNTIME_NOT_ISOLATED' ||
    value === 'AGENT_UNAVAILABLE' ||
    value === 'PROVISION_FAILED' ||
    value === 'QUOTA_NOT_ENFORCEABLE' ||
    value === 'NOT_ENFORCEABLE' ||
    value === 'TEMP_PASSWORD_EXPIRED';
  const style = isSuccess
    ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
    : isDanger
      ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
      : 'bg-amber-500/10 text-amber-800 dark:text-amber-200';
  return (
    <Badge variant='outline' className={style}>
      {value}
    </Badge>
  );
}

function formatBytes(bytes?: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function latestHandshake(devices: WireGuardDevice[]) {
  const timestamps = devices.map((device) => {
    if (!device.lastHandshake) return 0;
    if (typeof device.lastHandshake === 'number') return device.lastHandshake;
    const parsed = Date.parse(device.lastHandshake);
    return isNaN(parsed) ? 0 : parsed / 1000;
  });
  const latest = Math.max(0, ...timestamps);
  return latest ? new Date(latest * 1000).toLocaleString() : 'Chưa có handshake';
}

export function MemberManagementView() {
  const members = useLabOpsStore((state) => state.members);
  const memberSyncError = useLabOpsStore((state) => state.memberSyncError);
  const fetchAccessMembers = useLabOpsStore((state) => state.fetchAccessMembers);
  const [vpnOverview, setVpnOverview] = useState<WireGuardVpnOverview | null>(null);
  const [devicesByUser, setDevicesByUser] = useState<Record<string, WireGuardDevice[]>>({});
  const [newDeviceNames, setNewDeviceNames] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<MemberFilterTab>('ALL');
  const [wizardOpen, setWizardOpen] = useState(false);
  const [managedUser, setManagedUser] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [claimedSecretsByUser, setClaimedSecretsByUser] = useState<
    Record<
      string,
      {
        key_passphrase?: string;
        temporary_password?: string;
        temp_password_expires_at?: string;
      }
    >
  >({});
  const [claimedBundleByUser, setClaimedBundleByUser] = useState<
    Record<
      string,
      {
        username: string;
        ssh_host: string;
        ssh_port: number;
        host_alias?: string;
        ssh_command: string;
        ssh_alias_command?: string;
        encrypted_private_key: string;
        ssh_config_snippet?: string;
        quick_setup_script?: string;
        wireguard_conf?: string;
        wireguard_client_ip?: string;
      }
    >
  >({});
  const [tempPasswordInput, setTempPasswordInput] = useState('');
  const [newPasswordInput, setNewPasswordInput] = useState('');
  const [deleteTargetMember, setDeleteTargetMember] = useState<LabMember | null>(null);
  const [webAccountDialogOpen, setWebAccountDialogOpen] = useState(false);
  const [viewMode, setViewMode] = useState<'ssh' | 'web' | 'supervisors'>('ssh');
  const [webAccounts, setWebAccounts] = useState<WebPortalAccountItem[]>([]);
  const [webAccountsLoading, setWebAccountsLoading] = useState(false);
  const [supervisorGroups, setSupervisorGroups] = useState<SupervisorGroup[]>([]);
  const [lecturers, setLecturers] = useState<LecturerOption[]>([]);
  const [showVpnDevices, setShowVpnDevices] = useState(false);

  const refresh = useCallback(async () => {
    if (DEMO_MODE) return;
    setWebAccountsLoading(true);
    await fetchAccessMembers();
    try {
      const [supRes, webRes] = await Promise.all([
        labopsCall<{ groups: SupervisorGroup[]; lecturers: LecturerOption[] }>(
          'admin/supervisor-groups'
        ),
        labopsCall<{ accounts: WebPortalAccountItem[] }>('admin/web-accounts')
      ]);
      if (supRes.ok && supRes.data) {
        setSupervisorGroups(supRes.data.groups || []);
        setLecturers(supRes.data.lecturers || []);
      }
      if (webRes.ok && webRes.data) {
        setWebAccounts(webRes.data.accounts || []);
      }
    } catch {
      // ignore if endpoint unavailable
    } finally {
      setWebAccountsLoading(false);
    }
    try {
      const vpnRes = await fetch('/api/labops/v2/vpn/overview', { cache: 'no-store' });
      setVpnOverview(vpnRes.ok ? await vpnRes.json() : null);
    } catch {
      setVpnOverview(null);
    }
    const rows = useLabOpsStore.getState().members;
    const results = await Promise.all(
      rows.map(async (member) => {
        try {
          const v2Res = await fetch(
            `/api/labops/v2/members/${encodeURIComponent(member.id)}/devices`,
            {
              cache: 'no-store'
            }
          );
          if (v2Res.ok) {
            const rawDevices = await v2Res.json();
            if (Array.isArray(rawDevices) && rawDevices.length > 0) {
              const normalized: WireGuardDevice[] = rawDevices.map((item: unknown) => {
                const d = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>;
                const enabled = d.enabled !== false;
                return {
                  deviceId: String(d.device_id || d.deviceId || d.id || ''),
                  id: String(d.id || d.device_id || ''),
                  name: String(d.name || 'Thiết bị VPN'),
                  clientIp: String(d.address || d.clientIp || d.vpn_ip || ''),
                  publicKey: String(d.public_key || d.publicKey || ''),
                  allowedSubnets: String(d.allowed_subnets || d.allowedSubnets || ''),
                  status: String(
                    d.status || (enabled ? 'ACTIVE' : 'DISABLED')
                  ) as WireGuardDevice['status'],
                  enabled,
                  lastHandshake: (d.last_handshake || d.lastHandshake || null) as
                    | string
                    | number
                    | null,
                  transferRx: Number(d.transfer_rx || d.transferRx || 0),
                  transferTx: Number(d.transfer_tx || d.transferTx || 0)
                };
              });
              return [member.id, normalized] as const;
            }
          }
        } catch {
          // fallback to legacy
        }
        try {
          const response = await fetch(
            `/api/access/users/${encodeURIComponent(member.id)}/wireguard/devices`,
            {
              cache: 'no-store'
            }
          );
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

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void refresh();
  }, [refresh]);
  /* oxlint-enable react/set-state-in-effect */

  const sshMembers = useMemo(() => members.filter((m) => !isWebOnlyUnprovisioned(m)), [members]);

  const linkedWebByMemberId = useMemo(() => {
    const map: Record<string, WebPortalAccountItem> = {};
    for (const acc of webAccounts) {
      if (acc.is_ssh_linked && acc.linked_ssh_member_id) {
        map[acc.linked_ssh_member_id] = acc;
      }
    }
    return map;
  }, [webAccounts]);

  const summaryStats = useMemo(() => {
    const total = sshMembers.length;
    const workstation = sshMembers.filter((m) => m.assignedServer === 'workstation').length;
    const track = sshMembers.filter((m) => m.assignedServer === 'track').length;
    const inactive = sshMembers.filter((m) => INACTIVE_STATUSES.has(m.status)).length;
    const active = total - inactive;
    return { total, workstation, track, active, inactive };
  }, [sshMembers]);

  const visibleMembers = useMemo(
    () =>
      sshMembers.filter((member) => {
        if (statusFilter === 'ACTIVE_PENDING' && INACTIVE_STATUSES.has(member.status)) return false;
        if (statusFilter === 'INACTIVE' && !INACTIVE_STATUSES.has(member.status)) return false;
        if (statusFilter === 'WORKSTATION' && member.assignedServer !== 'workstation') return false;
        if (statusFilter === 'RACK' && member.assignedServer !== 'track') return false;
        const query = filter.toLowerCase();
        return (
          !query ||
          [
            member.username,
            member.fullName,
            member.email,
            member.workspaceName,
            member.linkedWebUsername || ''
          ].some((value) => value?.toLowerCase().includes(query))
        );
      }),
    [filter, sshMembers, statusFilter]
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
      toast.error(
        error instanceof Error ? error.message : 'Không thể xác minh kết quả từ backend.'
      );
      return null;
    } finally {
      setBusy(false);
    }
  };

  const downloadDeviceConfigFile = async (member: LabMember, device: WireGuardDevice) => {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/labops/v2/members/${encodeURIComponent(member.id)}/devices/${encodeURIComponent(device.deviceId)}/config`,
        { cache: 'no-store' }
      );
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(
          typeof err.detail === 'string' ? err.detail : `Lỗi tải config (${response.status})`
        );
      }
      const confText = await response.text();
      const safeName = (device.name || 'device').toLowerCase().replace(/[^a-z0-9_-]/g, '_');
      downloadTextFile(`${member.username}-${safeName}.conf`, confText);
      toast.success(`Đã tải file cấu hình WireGuard: ${member.username}-${safeName}.conf`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Không thể tải cấu hình WireGuard.');
    } finally {
      setBusy(false);
    }
  };

  const toggleDeviceState = async (member: LabMember, device: WireGuardDevice) => {
    const nextState = !device.enabled;
    const result = await runAction(
      `/api/labops/v2/members/${encodeURIComponent(member.id)}/devices/${encodeURIComponent(device.deviceId)}/toggle`,
      'POST',
      { enable: nextState }
    );
    if (result) {
      toast.success(`Đã ${nextState ? 'kích hoạt' : 'tạm dừng'} thiết bị ${device.name}.`);
    }
  };

  const revokeDeviceV2Action = async (member: LabMember, device: WireGuardDevice) => {
    const confirmed = window.confirm(
      `Xác nhận thu hồi thiết bị "${device.name}" của @${member.username}?`
    );
    if (!confirmed) return;
    const result = await runAction(
      `/api/labops/v2/members/${encodeURIComponent(member.id)}/devices/${encodeURIComponent(device.deviceId)}`,
      'DELETE'
    );
    if (result) {
      toast.success(`Đã xóa thiết bị ${device.name}.`);
    }
  };

  const quickAddMemberDevice = async (member: LabMember) => {
    const name = (newDeviceNames[member.id] || '').trim();
    if (!name) {
      toast.error('Vui lòng nhập tên thiết bị (ví dụ: MacBook M3, iPhone, Máy bàn).');
      return;
    }
    const result = await runAction(
      `/api/labops/v2/members/${encodeURIComponent(member.id)}/devices`,
      'POST',
      { name }
    );
    if (result) {
      setNewDeviceNames((prev) => ({ ...prev, [member.id]: '' }));
      toast.success(`Đã cấp thành công thiết bị "${name}" cho @${member.username}!`);
      if (result.configuration) {
        const safeName = name.toLowerCase().replace(/[^a-z0-9_-]/g, '_');
        downloadTextFile(`${member.username}-${safeName}.conf`, result.configuration);
        toast.success(
          `Đã tự động tải cấu hình ${member.username}-${safeName}.conf để import ngay!`
        );
      }
    }
  };

  const rotateMemberSshKeypair = async (member: LabMember, requirePassphrase = true) => {
    const result = await runAction(
      `/api/labops/v2/ssh-access/${encodeURIComponent(member.id)}/rotate-key`,
      'POST',
      { require_passphrase: requirePassphrase }
    );
    if (result) {
      toast.success(
        `Đã tạo cặp khóa Ed25519 mới (${requirePassphrase ? 'bảo vệ Passphrase' : 'tùy chọn không passphrase'}) cho @${member.username}.`
      );
    }
  };

  const reverifyMemberWorkspace = async (member: LabMember) => {
    const result = await runAction(
      `/api/labops/v2/ssh-access/${encodeURIComponent(member.id)}/verify`,
      'POST'
    );
    if (result) {
      toast.info(
        `Kết quả kiểm chứng runtime @${member.username}: ${result.overall_readiness || 'NOT_VERIFIED'}`
      );
    }
  };

  const reissueMemberTempPassword = async (member: LabMember) => {
    const result = await runAction(
      `/api/labops/v2/ssh-access/${encodeURIComponent(member.id)}/reissue-temp-password`,
      'POST',
      { ttl_seconds: 86400 }
    );
    if (result) {
      toast.success(
        `Đã cấp lại mật khẩu tạm mới cho @${member.username} (mật khẩu tạm cũ đã bị hủy hiệu lực).`
      );
    }
  };

  const downloadMemberAccessZip = async (member: LabMember) => {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/labops/v2/ssh-access/${encodeURIComponent(member.id)}/download-zip`,
        { cache: 'no-store' }
      );
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(
          typeof errorData.detail === 'string'
            ? errorData.detail
            : `Không thể tải file ZIP (Mã lỗi ${response.status})`
        );
      }
      const blob = await response.blob();
      const contentDisposition = response.headers.get('Content-Disposition');
      let filename = `LabOps_Access_${member.username}.zip`;
      if (contentDisposition) {
        const match = contentDisposition.match(/filename="?([^";]+)"?/i);
        if (match && match[1]) filename = match[1];
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(`Đã tải thành công bộ kết nối: ${filename}`);
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Không thể tải gói kết nối ZIP.');
    } finally {
      setBusy(false);
    }
  };

  const claimMemberKeyBundle = async (member: LabMember) => {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/labops/v2/ssh-access/${encodeURIComponent(member.id)}/claim-key-bundle`,
        { method: 'POST', cache: 'no-store' }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof data.detail === 'string' ? data.detail : 'Gói khóa riêng đã được tải trước đó.'
        );
      }
      setClaimedBundleByUser((prev) => ({ ...prev, [member.id]: data }));
      if (data.wireguard_conf) {
        downloadTextFile(`${data.username}-labops-wg0.conf`, data.wireguard_conf);
      }
      if (data.quick_setup_script) {
        downloadTextFile(`setup-ssh-${data.username}.sh`, data.quick_setup_script);
      }
      toast.success(
        `Đã tải file cấu hình WireGuard (${data.username}-labops-wg0.conf) & Script cài SSH (setup-ssh-${data.username}.sh)!`
      );
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Không thể tải gói kết nối.');
    } finally {
      setBusy(false);
    }
  };

  const claimMemberSecrets = async (member: LabMember) => {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/labops/v2/ssh-access/${encodeURIComponent(member.id)}/claim-secrets`,
        { method: 'POST', cache: 'no-store' }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof data.detail === 'string' ? data.detail : 'Thông tin bí mật đã được lấy trước đó.'
        );
      }
      setClaimedSecretsByUser((prev) => ({ ...prev, [member.id]: data }));
      toast.success('Đã lấy Passphrase & Mật khẩu tạm (1 lần duy nhất). Hãy gửi qua kênh riêng!');
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Không thể lấy thông tin bí mật.');
    } finally {
      setBusy(false);
    }
  };

  const revokeMemberSsh = async (member: LabMember) => {
    const confirmed = window.confirm(
      `Xác nhận THU HỒI QUYỀN SSH của thành viên @${member.username} (Workspace: ${member.workspaceName} trên host ${member.assignedServer})?\n\n` +
        'Thao tác này sẽ gỡ khóa công khai khỏi cấu hình SSH của workspace, vô hiệu hóa mật khẩu tạm và chặn mọi kết nối SSH mới.'
    );
    if (!confirmed) return;
    const result = await runAction(
      `/api/labops/v2/ssh-access/${encodeURIComponent(member.id)}/revoke`,
      'POST'
    );
    if (result) {
      if (result.status === 'VERIFIED_REVOKED') {
        toast.success(
          `Đã xác minh thu hồi SSH thành công (VERIFIED_REVOKED) cho @${member.username}.`
        );
      } else {
        toast.warning(`Trạng thái thu hồi: ${result.status} — ${result.reason}`);
      }
    }
  };

  const terminateMemberSessions = async (member: LabMember) => {
    const confirmed = window.confirm(
      `Xác nhận ĐÓNG CÁC PHIÊN SSH ĐANG MỞ của workspace @${member.username}?\n\n` +
        'Chỉ đóng các phiên SSH thuộc workspace của thành viên này, không ảnh hưởng tới SSH cổng 22 hay các job nghiên cứu.'
    );
    if (!confirmed) return;
    await runAction(
      `/api/labops/v2/ssh-access/${encodeURIComponent(member.id)}/terminate-sessions`,
      'POST',
      { confirm: true }
    );
  };

  const deleteMemberAction = (member: LabMember) => {
    setDeleteTargetMember(member);
  };

  const handleFirstLoginPasswordChange = async (member: LabMember) => {
    if (!tempPasswordInput.trim() || !newPasswordInput.trim()) {
      toast.error('Vui lòng nhập đầy đủ mật khẩu tạm và mật khẩu mới.');
      return;
    }
    const result = await runAction(
      '/api/labops/v2/ssh-access/first-login-change-password',
      'POST',
      {
        username: member.username,
        temporary_password: tempPasswordInput,
        new_password: newPasswordInput
      }
    );
    if (result) {
      setTempPasswordInput('');
      setNewPasswordInput('');
      toast.success(
        `Đã đổi mật khẩu lần đầu thành công cho @${member.username}! Mật khẩu tạm đã hết hiệu lực.`
      );
    }
  };

  const vpnHealthy = vpnOverview?.status === 'HEALTHY' || vpnOverview?.status === 'VPN_API_HEALTHY';
  const vpnMisconfigured = vpnOverview?.status === 'VPN_ROUTING_MISCONFIGURED';
  const wgPort = parseUdpPort(vpnOverview?.public_endpoint);
  const verifiedIsolatedCount = sshMembers.filter(
    (m) => m.authorizationGate?.ssh_authorized === true || m.overallReadiness === 'READY'
  ).length;
  const openSshGatewayStatus =
    verifiedIsolatedCount > 0
      ? 'VERIFIED'
      : sshMembers.length > 0
        ? 'NOT_VERIFIED'
        : 'PENDING_PROVISIONING';

  return (
    <div className='space-y-4'>
      {/* COMPACT TOOLBAR: SECTION SWITCHER + ACTIONS */}
      <div className='flex flex-wrap items-center justify-between gap-2 border-b pb-3'>
        <div className='flex flex-wrap gap-2'>
          <Button
            type='button'
            size='sm'
            variant={viewMode === 'ssh' ? 'default' : 'outline'}
            onClick={() => setViewMode('ssh')}
          >
            Tài khoản SSH &amp; VPN ({sshMembers.length})
          </Button>
          <Button
            type='button'
            size='sm'
            variant={viewMode === 'web' ? 'default' : 'outline'}
            onClick={() => setViewMode('web')}
          >
            Tài khoản Đăng ký Web ({webAccounts.length})
          </Button>
          <Button
            type='button'
            size='sm'
            variant={viewMode === 'supervisors' ? 'default' : 'outline'}
            onClick={() => setViewMode('supervisors')}
          >
            Nhóm theo Giảng viên ({supervisorGroups.length})
          </Button>
        </div>
        <div className='flex flex-wrap gap-2'>
          <Button
            size='sm'
            variant='outline'
            onClick={() => void refresh()}
            disabled={busy || DEMO_MODE}
          >
            Làm mới dữ liệu
          </Button>
          <Button
            size='sm'
            variant='secondary'
            onClick={() => setWebAccountDialogOpen(true)}
            disabled={DEMO_MODE}
          >
            + Tạo Tài khoản Web (SV/GV)
          </Button>
          <Button size='sm' onClick={() => setWizardOpen(true)} disabled={DEMO_MODE}>
            + Tạo quyền truy cập SSH
          </Button>
        </div>
      </div>

      <CreateWebAccountDialog
        open={webAccountDialogOpen}
        lecturers={lecturers}
        onClose={() => setWebAccountDialogOpen(false)}
        onCreated={() => void refresh()}
      />

      {viewMode === 'web' && (
        <WebAccountsPanel
          accounts={webAccounts}
          loading={webAccountsLoading}
          demoMode={DEMO_MODE}
          onRefresh={() => void refresh()}
          onDeleteWebAccount={(memberId) => {
            const target = members.find((m) => m.id === memberId);
            if (target) setDeleteTargetMember(target);
          }}
        />
      )}

      {viewMode === 'supervisors' && (
        <SupervisorGroupsPanel
          groups={supervisorGroups}
          lecturers={lecturers}
          onRefresh={() => void refresh()}
        />
      )}

      {viewMode === 'ssh' && (
        <>
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

          {/* WIREGUARD VPN SYSTEM OVERVIEW */}
          <Card
            className={`backdrop-blur-sm shadow-sm ${
              vpnOverview && !vpnHealthy
                ? 'border-rose-500/40 bg-rose-500/5'
                : 'border-primary/30 bg-card/60'
            }`}
          >
            <CardContent className='p-4 space-y-3'>
              <div className='flex flex-wrap items-center justify-between gap-2 border-b pb-2'>
                <div className='flex items-center gap-2'>
                  <span className='text-base font-semibold'>Hệ thống WireGuard VPN Gateway</span>
                  <Badge
                    variant='outline'
                    className='bg-emerald-500/10 text-emerald-600 border-emerald-500/30'
                  >
                    Docker wg-easy v15 (Duy nhất)
                  </Badge>
                  <Badge
                    variant='outline'
                    className={
                      vpnOverview?.status === 'HEALTHY'
                        ? 'bg-emerald-500/10 text-emerald-700 border-emerald-500/30 text-xs'
                        : vpnOverview?.status === 'VPN_API_HEALTHY'
                          ? 'bg-amber-500/10 text-amber-800 dark:text-amber-200 border-amber-500/30 text-xs'
                          : vpnOverview
                            ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-500/40 text-xs'
                            : 'bg-amber-500/10 text-amber-800 border-amber-500/30 text-xs'
                    }
                  >
                    Trạng thái: {vpnOverview ? vpnOverview.status : 'ĐANG KIỂM TRA...'}
                  </Badge>
                </div>
                <div className='text-xs text-muted-foreground flex flex-wrap items-center gap-3'>
                  <span>
                    Public Endpoint:{' '}
                    <strong className='text-foreground font-mono'>
                      {vpnOverview?.public_endpoint || 'Chưa cấu hình Endpoint'}
                    </strong>
                  </span>
                  <span>
                    Subnet:{' '}
                    <strong className='text-foreground font-mono'>
                      {vpnOverview?.cidr || '10.88.40.0/24'}
                    </strong>
                  </span>
                </div>
              </div>

              {vpnOverview && vpnMisconfigured && (
                <div className='rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-800 dark:text-rose-200 space-y-1'>
                  <p className='font-semibold'>
                    ⚠️ Lỗi cấu hình định tuyến VPN (VPN_ROUTING_MISCONFIGURED)
                  </p>
                  <p>
                    {(vpnOverview.routing_issues || []).join('; ') ||
                      'Phát hiện sai lệch subnet/route/NAT giữa LabOps và wg-easy.'}{' '}
                    — Cần đồng bộ lại CIDR/hooks trước khi cấp cấu hình VPN.
                  </p>
                </div>
              )}

              {vpnOverview && vpnOverview.status === 'VPN_API_HEALTHY' && (
                <div className='rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-900 dark:text-amber-200'>
                  API wg-easy đang hoạt động (Persisted CIDR:{' '}
                  <code>{vpnOverview.persisted_cidr || vpnOverview.cidr}</code>), nhưng Backend chưa
                  thể kiểm chứng trực tiếp kernel <code>wg0</code>/NAT trong container (
                  <code>{vpnOverview.runtime_inspection_status || 'UNVERIFIED'}</code>).
                </div>
              )}

              {vpnOverview && !vpnHealthy && !vpnMisconfigured && (
                <div className='rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-800 dark:text-rose-200 space-y-1'>
                  <p className='font-semibold'>
                    ⚠️ wg-easy VPN Gateway hiện đang ngoại tuyến ({vpnOverview.status})
                  </p>
                  <p>
                    {vpnOverview.wg_easy?.error ||
                      'Không thể kết nối API quản trị wg-easy tại cổng nội bộ.'}{' '}
                    — SSH trực tiếp trong mạng LAN vẫn hoạt động bình thường; cần khởi động
                    container <code>labops-wg-easy</code> để cấp hoặc kích hoạt peer VPN từ xa qua
                    Internet.
                  </p>
                </div>
              )}

              <div className='grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs'>
                <div className='rounded-md border p-2.5 bg-muted/20'>
                  <div className='text-muted-foreground'>Cổng UDP & Chuyển tiếp</div>
                  <div className='font-mono font-medium text-sm mt-0.5'>UDP {wgPort} (wg-easy)</div>
                  <div className='text-xs text-muted-foreground mt-0.5'>
                    {vpnOverview?.public_endpoint || 'Chưa cấu hình Endpoint'}
                  </div>
                </div>
                <button
                  type='button'
                  onClick={() => setShowVpnDevices((prev) => !prev)}
                  className='rounded-md border p-2.5 bg-muted/20 text-left hover:bg-muted/40 transition cursor-pointer w-full'
                >
                  <div className='flex items-center justify-between gap-1 text-muted-foreground'>
                    <span>Thiết bị VPN / Kết nối</span>
                    <span className='text-primary font-medium'>
                      {showVpnDevices ? 'Thu gọn ▴' : 'Xem chi tiết ▾'}
                    </span>
                  </div>
                  <div
                    className={`font-mono font-medium text-sm mt-0.5 ${
                      vpnHealthy
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : 'text-muted-foreground'
                    }`}
                  >
                    {vpnOverview?.active_clients ?? 0} / {vpnOverview?.total_clients ?? 0} online
                  </div>
                  <div className='text-xs text-muted-foreground mt-0.5'>
                    Handshake ≤{' '}
                    {Math.max(1, Math.round((vpnOverview?.handshake_ttl_seconds ?? 180) / 60))} phút
                    {(vpnOverview?.ever_connected_clients ?? 0) > (vpnOverview?.active_clients ?? 0)
                      ? ` · từng nối: ${vpnOverview?.ever_connected_clients}`
                      : ''}
                  </div>
                </button>
                <div className='rounded-md border p-2.5 bg-muted/20'>
                  <div className='text-muted-foreground'>Lưu lượng Mạng VPN</div>
                  <div className='font-mono font-medium text-sm mt-0.5'>
                    ↓ {formatBytes(vpnOverview?.total_rx_bytes)} · ↑{' '}
                    {formatBytes(vpnOverview?.total_tx_bytes)}
                  </div>
                  <div className='text-xs text-muted-foreground mt-0.5'>
                    Tổng lưu lượng toàn trạm
                  </div>
                </div>
                <div className='rounded-md border p-2.5 bg-muted/20'>
                  <div className='text-muted-foreground'>Chính sách Định tuyến</div>
                  <div className='font-mono font-medium text-sm mt-0.5 text-blue-600 dark:text-blue-400'>
                    Per-Host Dynamic Route
                  </div>
                  <div className='text-xs text-muted-foreground mt-0.5'>
                    Phân giải theo Host được cấp
                  </div>
                </div>
              </div>

              {showVpnDevices && (
                <div className='rounded-md border bg-background p-3 space-y-2 text-xs'>
                  <div className='flex flex-wrap items-center justify-between gap-2'>
                    <span className='font-semibold'>
                      Danh sách Thiết bị WireGuard VPN ({vpnOverview?.devices?.length ?? 0} thiết
                      bị)
                    </span>
                    <span className='text-muted-foreground'>
                      Thiết bị có handshake trong vòng{' '}
                      {Math.max(1, Math.round((vpnOverview?.handshake_ttl_seconds ?? 180) / 60))}{' '}
                      phút được tính là ĐANG ONLINE
                    </span>
                  </div>
                  {!vpnOverview?.devices || vpnOverview.devices.length === 0 ? (
                    <div className='text-muted-foreground py-2'>
                      Chưa có danh sách thiết bị từ wg-easy.
                    </div>
                  ) : (
                    <div className='overflow-x-auto'>
                      <table className='w-full text-xs'>
                        <thead>
                          <tr className='border-b text-left text-muted-foreground'>
                            <th className='py-1.5 pr-3'>Trạng thái</th>
                            <th className='py-1.5 pr-3'>Tên thiết bị (wg-easy peer)</th>
                            <th className='py-1.5 pr-3'>VPN IP</th>
                            <th className='py-1.5 pr-3'>Handshake gần nhất</th>
                            <th className='py-1.5 text-right'>Lưu lượng (↓ RX / ↑ TX)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {vpnOverview.devices
                            .toSorted((a, b) => {
                              if (a.is_online !== b.is_online) return a.is_online ? -1 : 1;
                              const ah = a.last_handshake ? 1 : 0;
                              const bh = b.last_handshake ? 1 : 0;
                              if (ah !== bh) return bh - ah;
                              return a.name.localeCompare(b.name);
                            })
                            .map((dev) => {
                              const ageMin =
                                dev.handshake_age_seconds != null
                                  ? Math.floor(dev.handshake_age_seconds / 60)
                                  : null;
                              return (
                                <tr key={dev.id} className='border-b last:border-0'>
                                  <td className='py-1.5 pr-3'>
                                    {dev.is_online ? (
                                      <Badge className='bg-emerald-600 text-white text-xs'>
                                        ĐANG ONLINE
                                      </Badge>
                                    ) : dev.last_handshake ? (
                                      <Badge variant='outline' className='text-xs text-amber-700'>
                                        Từng kết nối (Offline)
                                      </Badge>
                                    ) : (
                                      <Badge
                                        variant='outline'
                                        className='text-xs text-muted-foreground'
                                      >
                                        Chưa kết nối
                                      </Badge>
                                    )}
                                  </td>
                                  <td className='py-1.5 pr-3 font-medium font-mono'>{dev.name}</td>
                                  <td className='py-1.5 pr-3 font-mono'>{dev.vpn_ip || '—'}</td>
                                  <td className='py-1.5 pr-3 text-muted-foreground'>
                                    {dev.last_handshake
                                      ? `${ageMin !== null ? (ageMin < 1 ? 'vừa xong' : `${ageMin} phút trước`) : ''} (${new Date(dev.last_handshake).toLocaleString('vi-VN')})`
                                      : 'Chưa từng handshake'}
                                  </td>
                                  <td className='py-1.5 text-right font-mono'>
                                    ↓ {formatBytes(dev.transfer_rx)} / ↑{' '}
                                    {formatBytes(dev.transfer_tx)}
                                  </td>
                                </tr>
                              );
                            })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          <div className='grid gap-3 lg:grid-cols-2' aria-label='Phân tách hệ thống SSH'>
            <Card>
              <CardContent className='space-y-2 p-4'>
                <h2 className='font-medium'>Legacy Host SSH (Cổng 22)</h2>
                <div className='flex flex-wrap gap-2'>
                  <Badge variant='outline'>Bảo toàn nguyên trạng trên Workstation &amp; Rack</Badge>
                  <Badge variant='outline'>Không tự động chỉnh sửa</Badge>
                </div>
                <p className='text-xs text-muted-foreground'>
                  Cổng SSH quản trị truyền thống của máy chủ vật lý được bảo vệ tuyệt đối.
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className='space-y-2 p-4'>
                <h2 className='font-medium'>Container SSH Workspace (Isolated Non-Root)</h2>
                <div className='flex flex-wrap gap-2'>
                  <Badge variant='outline'>Managed by Go Node Agent</Badge>
                  {statusBadge(openSshGatewayStatus)}
                </div>
                <p className='text-xs text-muted-foreground'>
                  Mỗi thành viên sử dụng khóa công khai OpenSSH riêng và kết nối trực tiếp vào
                  Docker container cô lập (dải <code>22001–29999 → 2222</code>), không chạy dưới
                  user quản trị host. ({verifiedIsolatedCount}/{sshMembers.length} workspace READY)
                </p>
              </CardContent>
            </Card>
          </div>

          {/* ACCOUNT SUMMARY STRIP & FILTER TABS */}
          <div className='grid grid-cols-2 sm:grid-cols-5 gap-2.5 text-xs'>
            <div className='rounded-lg border bg-card p-3'>
              <div className='text-muted-foreground'>Tổng tài khoản</div>
              <div className='text-lg font-bold font-mono mt-0.5'>{summaryStats.total}</div>
            </div>
            <div className='rounded-lg border bg-card p-3'>
              <div className='text-muted-foreground'>Workstation (aiot-ws1)</div>
              <div className='text-lg font-bold font-mono mt-0.5 text-primary'>
                {summaryStats.workstation}
              </div>
            </div>
            <div className='rounded-lg border bg-card p-3'>
              <div className='text-muted-foreground'>Rack (aiotlab-rack)</div>
              <div className='text-lg font-bold font-mono mt-0.5'>{summaryStats.track}</div>
            </div>
            <div className='rounded-lg border bg-card p-3'>
              <div className='text-muted-foreground'>Hoạt động / Chờ runtime</div>
              <div className='text-lg font-bold font-mono mt-0.5 text-emerald-600 dark:text-emerald-400'>
                {summaryStats.active}
              </div>
            </div>
            <div className='rounded-lg border bg-card p-3'>
              <div className='text-muted-foreground'>Tạm ngưng / Chờ xóa</div>
              <div className='text-lg font-bold font-mono mt-0.5 text-amber-700 dark:text-amber-300'>
                {summaryStats.inactive}
              </div>
            </div>
          </div>

          <Card>
            <CardContent className='space-y-4 p-4'>
              <div className='flex flex-wrap items-center justify-between gap-2'>
                <div className='flex flex-wrap gap-1.5'>
                  {(
                    [
                      { id: 'ALL', label: `Tất cả (${summaryStats.total})` },
                      { id: 'ACTIVE_PENDING', label: `Hoạt động / Chờ (${summaryStats.active})` },
                      { id: 'WORKSTATION', label: `Workstation (${summaryStats.workstation})` },
                      { id: 'RACK', label: `Rack (${summaryStats.track})` },
                      { id: 'INACTIVE', label: `Tạm ngưng / Chờ xóa (${summaryStats.inactive})` }
                    ] as const
                  ).map((tab) => (
                    <Button
                      key={tab.id}
                      type='button'
                      size='sm'
                      variant={statusFilter === tab.id ? 'default' : 'outline'}
                      className='h-8 text-xs'
                      onClick={() => setStatusFilter(tab.id)}
                    >
                      {tab.label}
                    </Button>
                  ))}
                </div>
                <Input
                  aria-label='Tìm tài khoản'
                  placeholder='Tìm username, tên, email hoặc workspace'
                  className='max-w-xs'
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                />
              </div>
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
                      const resolvedHostIp = resolveMemberHostIp(member);
                      const resolvedSshUser = resolveMemberSshUser(member);
                      const wgStatus = getMemberWireGuardStatus(member, devices);
                      const wgIp = member.wireguard?.clientIp || devices[0]?.clientIp;
                      const linkedWeb = linkedWebByMemberId[member.id];
                      const linkedWebUser = member.linkedWebUsername || linkedWeb?.username;
                      return (
                        <Fragment key={member.id}>
                          <tr className='border-b align-top'>
                            <td className='p-3'>
                              <div className='font-medium'>@{member.username}</div>
                              <div className='text-xs text-muted-foreground'>
                                {member.fullName} · {member.email}
                              </div>
                              {linkedWebUser && (
                                <div className='mt-1'>
                                  <Badge
                                    variant='outline'
                                    className='bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/30 text-xs'
                                  >
                                    Liên kết TK Web: @{linkedWebUser}
                                    {linkedWeb?.member_code ? ` (${linkedWeb.member_code})` : ''}
                                  </Badge>
                                </div>
                              )}
                            </td>
                            <td className='p-3'>
                              <Badge variant='outline' className='text-xs font-medium'>
                                {formatMemberRoleLabel(member.role)}
                              </Badge>
                            </td>
                            <td className='p-3'>
                              <div className='font-mono uppercase text-xs font-semibold'>
                                {member.assignedServer} · {resolvedHostIp}
                              </div>
                              <div className='text-xs text-muted-foreground'>
                                {member.workspaceName}
                              </div>
                            </td>
                            <td className='p-3'>
                              {statusBadge(member.sshKeyStatus || 'NOT_CONFIGURED')}
                              <div className='text-xs font-mono text-muted-foreground mt-1 space-y-0.5'>
                                {member.sshPort ? (
                                  <>
                                    <div className='text-foreground font-semibold'>
                                      ssh labops-{member.username}
                                    </div>
                                    <div>
                                      -p {member.sshPort} {resolvedSshUser}@{resolvedHostIp}
                                    </div>
                                  </>
                                ) : (
                                  'Chưa cấp khóa SSH'
                                )}
                              </div>
                            </td>
                            <td className='p-3'>
                              {statusBadge(wgStatus)}
                              <div className='text-xs text-muted-foreground font-mono mt-0.5'>
                                {devices.length} thiết bị{wgIp ? ` · ${wgIp}` : ''}
                              </div>
                            </td>
                            <td className='p-3'>
                              {statusBadge(member.status)}
                              <div className='text-xs text-muted-foreground'>
                                {member.provisioningState || '—'}
                              </div>
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
                                  disabled={
                                    busy || DEMO_MODE || member.sshKeyStatus === 'VERIFIED_REVOKED'
                                  }
                                  onClick={() => void revokeMemberSsh(member)}
                                >
                                  Thu hồi SSH
                                </Button>
                                <Button
                                  size='sm'
                                  variant='outline'
                                  className='border-rose-500/50 text-rose-600 hover:bg-rose-500/10 hover:text-rose-700 dark:text-rose-400'
                                  disabled={busy || DEMO_MODE}
                                  onClick={() => void deleteMemberAction(member)}
                                >
                                  Xóa tài khoản
                                </Button>
                              </div>
                            </td>
                          </tr>
                          {expanded && (
                            <tr key={`${member.id}-details`} className='border-b bg-muted/20'>
                              <td colSpan={7} className='p-4' aria-label='Chi tiết tài khoản'>
                                <Tabs defaultValue='access' className='w-full'>
                                  <TabsList className='grid grid-cols-2 sm:grid-cols-5 w-full h-auto p-1 bg-muted/60'>
                                    <TabsTrigger value='overview' className='text-xs py-1.5'>
                                      1. Tổng quan
                                    </TabsTrigger>
                                    <TabsTrigger value='access' className='text-xs py-1.5'>
                                      2. Truy cập VPN &amp; SSH
                                    </TabsTrigger>
                                    <TabsTrigger value='workspace' className='text-xs py-1.5'>
                                      3. Workspace
                                    </TabsTrigger>
                                    <TabsTrigger value='gpu_jobs' className='text-xs py-1.5'>
                                      4. GPU &amp; Tiến trình
                                    </TabsTrigger>
                                    <TabsTrigger value='logs' className='text-xs py-1.5'>
                                      5. Nhật ký
                                    </TabsTrigger>
                                  </TabsList>

                                  {/* TAB 1: TỔNG QUAN TÀI KHOẢN */}
                                  <TabsContent value='overview' className='mt-3 space-y-4'>
                                    <div className='grid gap-4 md:grid-cols-2'>
                                      <div className='rounded-lg border bg-background p-4 space-y-3'>
                                        <h4 className='text-xs font-bold uppercase text-muted-foreground'>
                                          Thông tin định danh &amp; Trạng thái
                                        </h4>
                                        <div className='space-y-2 text-xs'>
                                          <div className='flex justify-between border-b pb-1.5'>
                                            <span className='text-muted-foreground'>
                                              Họ và tên:
                                            </span>
                                            <span className='font-semibold'>
                                              {member.fullName || 'Chưa cập nhật'}
                                            </span>
                                          </div>
                                          <div className='flex justify-between border-b pb-1.5'>
                                            <span className='text-muted-foreground'>Username:</span>
                                            <span className='font-mono font-bold'>
                                              @{member.username}
                                            </span>
                                          </div>
                                          <div className='flex justify-between border-b pb-1.5'>
                                            <span className='text-muted-foreground'>Email:</span>
                                            <span>{member.email || 'N/A'}</span>
                                          </div>
                                          <div className='flex justify-between border-b pb-1.5'>
                                            <span className='text-muted-foreground'>
                                              Vai trò hệ thống:
                                            </span>
                                            <Badge variant='outline' className='text-xs font-mono'>
                                              {formatMemberRoleLabel(member.role)} (
                                              {member.role || 'RESEARCHER'})
                                            </Badge>
                                          </div>
                                          <div className='flex justify-between border-b pb-1.5'>
                                            <span className='text-muted-foreground'>
                                              Trạng thái tài khoản:
                                            </span>
                                            {statusBadge(member.status)}
                                          </div>
                                          <div className='flex justify-between'>
                                            <span className='text-muted-foreground'>
                                              Ngày kích hoạt:
                                            </span>
                                            <span>
                                              {member.createdAt
                                                ? new Date(member.createdAt).toLocaleDateString()
                                                : 'Chưa xác định'}
                                            </span>
                                          </div>
                                        </div>
                                      </div>

                                      <div className='rounded-lg border bg-background p-4 space-y-3'>
                                        <h4 className='text-xs font-bold uppercase text-muted-foreground'>
                                          Hạn ngạch &amp; Thư mục lưu trữ máy chủ
                                        </h4>
                                        <div className='space-y-2 text-xs'>
                                          <div className='rounded-md border bg-muted/30 p-2.5 space-y-1'>
                                            <div className='text-muted-foreground text-xs'>
                                              Đường dẫn thư mục riêng trên máy chủ:
                                            </div>
                                            <div className='font-mono font-bold text-xs select-all break-all text-foreground'>
                                              {member.memberStorageDirectory ||
                                                member.workspaceDir ||
                                                member.mountPath ||
                                                'Chưa cấp phát'}
                                            </div>
                                            <p className='text-xs text-muted-foreground'>
                                              Thư mục cô lập (0700), chỉ tài khoản này và admin có
                                              quyền truy xuất.
                                            </p>
                                          </div>
                                          <div className='flex justify-between border-b pb-1.5 pt-1'>
                                            <span className='text-muted-foreground'>
                                              Chính sách CPU:
                                            </span>
                                            <span className='font-mono font-semibold'>
                                              {member.cpuPolicy || 'SHARED_WEIGHTED'} (Trọng số{' '}
                                              {member.cpuWeight ?? 100})
                                            </span>
                                          </div>
                                          <div className='flex justify-between border-b pb-1.5'>
                                            <span className='text-muted-foreground'>
                                              Chính sách RAM:
                                            </span>
                                            <span className='font-mono font-semibold'>
                                              {member.memoryPolicy || 'SHARED_HOST_PROTECTED'}
                                            </span>
                                          </div>
                                          <div className='flex justify-between gap-2'>
                                            <span className='text-muted-foreground shrink-0'>
                                              Ổ đĩa &amp; Lưu trữ:
                                            </span>
                                            <span className='font-semibold text-right'>
                                              {member.storagePoolLabel ||
                                                member.hostStorageRoot ||
                                                'Mặc định'}{' '}
                                              · {member.storageQuotaGb || 0} GiB (
                                              {member.storageQuotaEnforcement || 'NOT_VERIFIED'})
                                            </span>
                                          </div>
                                        </div>
                                      </div>
                                    </div>
                                  </TabsContent>

                                  {/* TAB 2: TRUY CẬP VPN & SSH */}
                                  <TabsContent value='access' className='mt-3'>
                                    <div className='grid gap-5 lg:grid-cols-2'>
                                      {/* CỘT 1: VÒNG ĐỜI QUYỀN SSH DO ADMIN QUẢN LÝ */}
                                      <section className='space-y-3 rounded-lg border bg-background p-4'>
                                        <div className='flex items-center justify-between'>
                                          <h3 className='font-semibold text-sm'>
                                            1. Quản lý Vòng đời Khóa SSH & Mật khẩu tạm (@
                                            {member.username})
                                          </h3>
                                          {statusBadge(member.sshKeyStatus || 'NOT_CONFIGURED')}
                                        </div>
                                        {member.sshPort && (
                                          <div className='rounded-md bg-emerald-500/10 border border-emerald-500/30 p-2.5 text-xs font-mono space-y-1'>
                                            <p className='font-semibold text-emerald-700 dark:text-emerald-300'>
                                              Endpoint SSH đã cấp:
                                            </p>
                                            <p>
                                              1 chạm:{' '}
                                              <code className='font-bold select-all'>
                                                ssh labops-{member.username}
                                              </code>
                                            </p>
                                            <p>
                                              Trực tiếp:{' '}
                                              <code className='select-all'>
                                                ssh -i ~/.ssh/id_ed25519_{member.username} -p{' '}
                                                {member.sshPort} {resolvedSshUser}@{resolvedHostIp}
                                              </code>
                                            </p>
                                            {member.sshFingerprint && (
                                              <p className='text-xs text-muted-foreground'>
                                                Fingerprint: {member.sshFingerprint}
                                              </p>
                                            )}
                                            <p className='text-xs text-muted-foreground'>
                                              Kích hoạt lần đầu:{' '}
                                              <strong>
                                                {member.activationStatus || 'PENDING_ACTIVATION'}
                                              </strong>{' '}
                                              · Mật khẩu:{' '}
                                              <strong>
                                                {member.passwordStatus || 'NOT_APPLICABLE'}
                                              </strong>
                                            </p>
                                          </div>
                                        )}

                                        {/* MA TRẬN 9 TRẠNG THÁI BẢO MẬT & SẴN SÀNG WORKSPACE */}
                                        <div className='rounded-md border bg-muted/20 p-3 space-y-2 text-xs'>
                                          <div className='flex items-center justify-between gap-2'>
                                            <span className='font-semibold'>
                                              Ma trận 9 Trạng thái Cô lập &amp; Kiểm chứng Runtime
                                            </span>
                                            <Button
                                              type='button'
                                              size='sm'
                                              variant='outline'
                                              disabled={busy || DEMO_MODE}
                                              onClick={() => void reverifyMemberWorkspace(member)}
                                            >
                                              🔍 Kiểm chứng lại Runtime
                                            </Button>
                                          </div>
                                          <div className='grid grid-cols-1 sm:grid-cols-3 gap-2'>
                                            <div className='rounded border bg-background p-2 flex items-center justify-between gap-1'>
                                              <span className='text-muted-foreground'>
                                                1. Activation:
                                              </span>
                                              {statusBadge(
                                                member.activationStatus || 'PENDING_ACTIVATION'
                                              )}
                                            </div>
                                            <div className='rounded border bg-background p-2 flex items-center justify-between gap-1'>
                                              <span className='text-muted-foreground'>
                                                2. SSH Key:
                                              </span>
                                              {statusBadge(member.sshKeyStatus || 'NOT_CONFIGURED')}
                                            </div>
                                            <div className='rounded border bg-background p-2 flex items-center justify-between gap-1'>
                                              <span className='text-muted-foreground'>
                                                3. Docker:
                                              </span>
                                              {statusBadge(
                                                member.workspaceDelegationStatus ||
                                                  member.runtimeVerification?.docker_running ||
                                                  'NOT_VERIFIED'
                                              )}
                                            </div>
                                            <div className='rounded border bg-background p-2 flex items-center justify-between gap-1'>
                                              <span className='text-muted-foreground'>
                                                4. SSH Runtime:
                                              </span>
                                              {statusBadge(member.sshStatus || 'SSH_ACCESS_DENIED')}
                                            </div>
                                            <div className='rounded border bg-background p-2 flex items-center justify-between gap-1'>
                                              <span className='text-muted-foreground'>
                                                5. Privilege Iso:
                                              </span>
                                              {statusBadge(
                                                member.runtimeVerification?.privilege_isolation ||
                                                  'NOT_VERIFIED'
                                              )}
                                            </div>
                                            <div className='rounded border bg-background p-2 flex items-center justify-between gap-1'>
                                              <span className='text-muted-foreground'>
                                                6. Storage Quota:
                                              </span>
                                              {statusBadge(
                                                member.runtimeVerification?.storage_quota ||
                                                  member.storageQuotaEnforcement ||
                                                  'NOT_VERIFIED'
                                              )}
                                            </div>
                                            <div className='rounded border bg-background p-2 flex items-center justify-between gap-1'>
                                              <span className='text-muted-foreground'>
                                                7. CPU / RAM:
                                              </span>
                                              {statusBadge(
                                                member.runtimeVerification?.cpu_ram_limits ||
                                                  'NOT_VERIFIED'
                                              )}
                                            </div>
                                            <div className='rounded border bg-background p-2 flex items-center justify-between gap-1'>
                                              <span className='text-muted-foreground'>
                                                8. VPN Peer:
                                              </span>
                                              {statusBadge(wgStatus)}
                                            </div>
                                            <div className='rounded border bg-background p-2 flex items-center justify-between gap-1'>
                                              <span className='text-muted-foreground'>
                                                9. Overall:
                                              </span>
                                              {statusBadge(
                                                member.overallReadiness ||
                                                  member.provisioningState ||
                                                  'PENDING_PROVISIONING'
                                              )}
                                            </div>
                                          </div>
                                          {member.authorizationGate?.blocking_reasons &&
                                            member.authorizationGate.blocking_reasons.length >
                                              0 && (
                                              <div className='rounded border border-rose-500/30 bg-rose-500/10 p-2 text-rose-800 dark:text-rose-200 space-y-0.5'>
                                                <div className='font-semibold'>
                                                  Lý do Gate đang chặn SSH / READY:
                                                </div>
                                                {member.authorizationGate.blocking_reasons.map(
                                                  (reason, rIdx) => (
                                                    <div key={rIdx}>• {reason}</div>
                                                  )
                                                )}
                                              </div>
                                            )}
                                        </div>

                                        <div className='rounded-md border p-3 space-y-2 bg-muted/30'>
                                          <p className='text-xs font-semibold'>
                                            A. Cấp lại Khóa SSH Ed25519 (Mặc định Passphrase) &amp;
                                            Bàn giao qua Kênh Riêng
                                          </p>
                                          <div className='flex flex-wrap gap-2'>
                                            <Button
                                              type='button'
                                              size='sm'
                                              disabled={busy || DEMO_MODE}
                                              onClick={() =>
                                                void rotateMemberSshKeypair(member, true)
                                              }
                                            >
                                              🔒 Cấp khóa Ed25519 có Passphrase (Khuyến nghị)
                                            </Button>
                                            <Button
                                              type='button'
                                              size='sm'
                                              variant='outline'
                                              disabled={busy || DEMO_MODE}
                                              onClick={() =>
                                                void rotateMemberSshKeypair(member, false)
                                              }
                                            >
                                              Cấp khóa không Passphrase (Chỉ thử nghiệm nội bộ)
                                            </Button>
                                            <Button
                                              type='button'
                                              size='sm'
                                              variant='outline'
                                              disabled={busy || DEMO_MODE}
                                              onClick={() => void reissueMemberTempPassword(member)}
                                            >
                                              Cấp lại Mật khẩu tạm lần đầu
                                            </Button>
                                          </div>

                                          <div className='flex flex-wrap items-center gap-2 pt-1'>
                                            <Button
                                              type='button'
                                              size='sm'
                                              className='font-bold'
                                              disabled={
                                                busy ||
                                                DEMO_MODE ||
                                                member.status === 'SUSPENDED' ||
                                                member.status === 'ARCHIVED' ||
                                                member.status === 'DELETE_ELIGIBLE' ||
                                                member.status === 'DELETED'
                                              }
                                              title={
                                                member.status === 'SUSPENDED' ||
                                                member.status === 'ARCHIVED' ||
                                                member.status === 'DELETE_ELIGIBLE' ||
                                                member.status === 'DELETED'
                                                  ? 'Tài khoản đã bị tạm ngưng/niêm phong, không thể tải gói kết nối'
                                                  : 'Tải bộ kết nối SSH & WireGuard (.zip)'
                                              }
                                              onClick={() => void downloadMemberAccessZip(member)}
                                            >
                                              📦 Tải bộ kết nối (.zip)
                                            </Button>
                                            <Button
                                              type='button'
                                              size='sm'
                                              variant='secondary'
                                              disabled={
                                                busy ||
                                                DEMO_MODE ||
                                                !member.oneTimePackage?.channel_secrets_available
                                              }
                                              onClick={() => void claimMemberSecrets(member)}
                                            >
                                              {member.oneTimePackage?.channel_secrets_available
                                                ? '🔑 Passphrase & Mật khẩu tạm (Kênh riêng)'
                                                : 'Passphrase & Mật khẩu tạm đã lấy'}
                                            </Button>
                                          </div>

                                          {!member.oneTimePackage?.key_bundle_available && (
                                            <div className='rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-900 dark:text-amber-200'>
                                              Khóa riêng SSH của @{member.username} đã được giải
                                              phóng khỏi RAM theo chính sách một lần (One-Time
                                              Vault). Nếu thành viên làm mất file khóa, Admin bấm{' '}
                                              <strong>Cấp khóa 1 chạm</strong> hoặc{' '}
                                              <strong>Cấp khóa có Passphrase</strong> bên trên để
                                              tạo cặp khóa mới và tải lại file ZIP ngay!
                                            </div>
                                          )}

                                          <div className='pt-1'>
                                            <details className='text-xs text-muted-foreground cursor-pointer'>
                                              <summary className='font-medium text-primary hover:underline py-0.5'>
                                                ▶ Hoặc giải nén / lấy từng file cấu hình rời (.conf,
                                                .sh, SSH key)
                                              </summary>
                                              <div className='mt-2 space-y-2 pl-2 border-l-2 border-muted'>
                                                <Button
                                                  type='button'
                                                  size='sm'
                                                  variant='outline'
                                                  disabled={
                                                    busy ||
                                                    DEMO_MODE ||
                                                    !member.oneTimePackage?.key_bundle_available
                                                  }
                                                  onClick={() => void claimMemberKeyBundle(member)}
                                                >
                                                  {member.oneTimePackage?.key_bundle_available
                                                    ? 'Tải rời: WireGuard .conf & Script SSH 1 chạm'
                                                    : 'Gói Config rời đã tải / không còn trong RAM'}
                                                </Button>
                                              </div>
                                            </details>
                                          </div>

                                          {claimedBundleByUser[member.id] && (
                                            <div className='rounded border border-primary/40 bg-primary/5 p-2.5 text-xs space-y-2'>
                                              <p className='font-semibold'>
                                                Các tệp cấu hình dùng ngay cho @{member.username}:
                                              </p>
                                              <div className='flex flex-wrap gap-2'>
                                                {claimedBundleByUser[member.id]?.wireguard_conf && (
                                                  <Button
                                                    type='button'
                                                    size='sm'
                                                    variant='outline'
                                                    onClick={() =>
                                                      downloadTextFile(
                                                        `${member.username}-labops-wg0.conf`,
                                                        claimedBundleByUser[member.id]!
                                                          .wireguard_conf!
                                                      )
                                                    }
                                                  >
                                                    Tải {member.username}-labops-wg0.conf (Import
                                                    vào WireGuard)
                                                  </Button>
                                                )}
                                                {claimedBundleByUser[member.id]
                                                  ?.quick_setup_script && (
                                                  <Button
                                                    type='button'
                                                    size='sm'
                                                    variant='outline'
                                                    onClick={() =>
                                                      downloadTextFile(
                                                        `setup-ssh-${member.username}.sh`,
                                                        claimedBundleByUser[member.id]!
                                                          .quick_setup_script!
                                                      )
                                                    }
                                                  >
                                                    Tải setup-ssh-{member.username}.sh (Tự động cài
                                                    SSH & Key)
                                                  </Button>
                                                )}
                                                {claimedBundleByUser[member.id]
                                                  ?.ssh_config_snippet && (
                                                  <Button
                                                    type='button'
                                                    size='sm'
                                                    variant='outline'
                                                    onClick={() =>
                                                      downloadTextFile(
                                                        `ssh_config_${member.username}`,
                                                        claimedBundleByUser[member.id]!
                                                          .ssh_config_snippet!
                                                      )
                                                    }
                                                  >
                                                    Tải ssh_config_{member.username}
                                                  </Button>
                                                )}
                                                <Button
                                                  type='button'
                                                  size='sm'
                                                  variant='outline'
                                                  onClick={() =>
                                                    downloadTextFile(
                                                      `id_ed25519_${member.username}`,
                                                      claimedBundleByUser[member.id]!
                                                        .encrypted_private_key
                                                    )
                                                  }
                                                >
                                                  Tải riêng Key id_ed25519_{member.username}
                                                </Button>
                                              </div>
                                              <p className='font-mono text-xs text-muted-foreground'>
                                                Chạy{' '}
                                                <code>bash setup-ssh-{member.username}.sh</code>{' '}
                                                xong gõ luôn:{' '}
                                                <strong className='text-foreground'>
                                                  {claimedBundleByUser[member.id]
                                                    ?.ssh_alias_command ||
                                                    `ssh labops-${member.username}`}
                                                </strong>
                                              </p>
                                            </div>
                                          )}

                                          {claimedSecretsByUser[member.id] && (
                                            <div className='rounded border border-emerald-500/40 bg-emerald-500/10 p-2.5 text-xs font-mono space-y-1'>
                                              <p className='font-sans font-semibold text-emerald-800 dark:text-emerald-200'>
                                                Thông tin bí mật hiển thị 1 lần (Gửi cho @
                                                {member.username} qua kênh riêng):
                                              </p>
                                              {claimedSecretsByUser[member.id]?.key_passphrase && (
                                                <p>
                                                  Passphrase mở khóa Private Key:{' '}
                                                  <strong className='select-all bg-background px-1.5 py-0.5 rounded border'>
                                                    {
                                                      claimedSecretsByUser[member.id]
                                                        ?.key_passphrase
                                                    }
                                                  </strong>
                                                </p>
                                              )}
                                              {claimedSecretsByUser[member.id]
                                                ?.temporary_password && (
                                                <p>
                                                  Mật khẩu tạm đăng nhập lần đầu:{' '}
                                                  <strong className='select-all bg-background px-1.5 py-0.5 rounded border'>
                                                    {
                                                      claimedSecretsByUser[member.id]
                                                        ?.temporary_password
                                                    }
                                                  </strong>
                                                </p>
                                              )}
                                            </div>
                                          )}
                                        </div>

                                        <div className='rounded-md border p-3 space-y-2'>
                                          <p className='text-xs font-semibold'>
                                            B. Đổi mật khẩu lần đầu (Từ mật khẩu tạm sang mật khẩu
                                            chính thức)
                                          </p>
                                          <div className='grid gap-2 sm:grid-cols-2'>
                                            <Input
                                              type='password'
                                              placeholder='Mật khẩu tạm được cấp'
                                              value={tempPasswordInput}
                                              onChange={(e) => setTempPasswordInput(e.target.value)}
                                            />
                                            <Input
                                              type='password'
                                              placeholder='Mật khẩu mới (>=12 ký tự, chữ hoa/thường/số/ký tự ĐB)'
                                              value={newPasswordInput}
                                              onChange={(e) => setNewPasswordInput(e.target.value)}
                                            />
                                          </div>
                                          <Button
                                            type='button'
                                            size='sm'
                                            variant='outline'
                                            disabled={busy || DEMO_MODE}
                                            onClick={() =>
                                              void handleFirstLoginPasswordChange(member)
                                            }
                                          >
                                            Xác nhận đổi mật khẩu lần đầu
                                          </Button>
                                        </div>

                                        <div className='flex flex-wrap items-center justify-between gap-2 pt-1 border-t'>
                                          <span className='text-xs text-muted-foreground'>
                                            Đóng các phiên SSH đang mở của workspace (tách riêng với
                                            thu hồi khóa):
                                          </span>
                                          <Button
                                            type='button'
                                            size='sm'
                                            variant='destructive'
                                            disabled={busy || DEMO_MODE}
                                            onClick={() => void terminateMemberSessions(member)}
                                          >
                                            Đóng phiên SSH đang mở
                                          </Button>
                                        </div>
                                      </section>

                                      {/* CỘT 2: WIREGUARD VPN THEO THIẾT BỊ (MULTI-DEVICE WG-EASY V15) */}
                                      <section className='space-y-3 rounded-lg border bg-background p-4'>
                                        <div className='flex items-center justify-between'>
                                          <h3 className='font-semibold text-sm'>
                                            2. Quản lý Đa Thiết Bị WireGuard VPN (@{member.username}
                                            )
                                          </h3>
                                          <Badge variant='outline' className='text-xs'>
                                            {devices.length} thiết bị
                                          </Badge>
                                        </div>

                                        {devices.length ? (
                                          <div className='space-y-2 max-h-[360px] overflow-y-auto pr-1'>
                                            {devices.map((device) => {
                                              const isPendingDev =
                                                device.status === 'PENDING_PROVISIONING' ||
                                                device.deviceId.startsWith('dev-pending-');
                                              const badgeLabel = isPendingDev
                                                ? 'PENDING_PROVISIONING'
                                                : device.enabled && device.status === 'ACTIVE'
                                                  ? 'ACTIVE'
                                                  : device.status || 'PAUSED';
                                              const allowedRoutes =
                                                device.allowedSubnets ||
                                                member.wireguard?.allowedSubnets ||
                                                'Chưa phân giải';
                                              return (
                                                <div
                                                  key={device.deviceId}
                                                  className='rounded-md border p-3 text-xs space-y-2 bg-card'
                                                >
                                                  <div className='flex flex-wrap items-center justify-between gap-1'>
                                                    <div className='flex items-center gap-2'>
                                                      <span className='font-semibold text-sm'>
                                                        {device.name}
                                                      </span>
                                                      <Badge
                                                        variant='outline'
                                                        className={
                                                          device.enabled &&
                                                          device.status === 'ACTIVE' &&
                                                          !isPendingDev
                                                            ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30'
                                                            : 'bg-amber-500/10 text-amber-700 border-amber-500/30'
                                                        }
                                                      >
                                                        {badgeLabel}
                                                      </Badge>
                                                    </div>
                                                    <div className='font-mono text-muted-foreground'>
                                                      IP:{' '}
                                                      <strong className='text-foreground'>
                                                        {device.clientIp || '10.88.40.x'}
                                                      </strong>
                                                    </div>
                                                  </div>

                                                  <div className='text-xs text-muted-foreground font-mono'>
                                                    AllowedIPs:{' '}
                                                    <span className='text-foreground'>
                                                      {allowedRoutes}
                                                    </span>
                                                  </div>

                                                  <div className='grid grid-cols-2 gap-2 text-xs text-muted-foreground'>
                                                    <div>
                                                      Handshake:{' '}
                                                      <span className='text-foreground'>
                                                        {device.lastHandshake
                                                          ? typeof device.lastHandshake === 'number'
                                                            ? new Date(
                                                                device.lastHandshake * 1000
                                                              ).toLocaleString()
                                                            : String(device.lastHandshake)
                                                          : 'Chưa có kết nối'}
                                                      </span>
                                                    </div>
                                                    <div>
                                                      Lưu lượng:{' '}
                                                      <span className='text-foreground font-mono'>
                                                        ↓ {formatBytes(device.transferRx)} · ↑{' '}
                                                        {formatBytes(device.transferTx)}
                                                      </span>
                                                    </div>
                                                  </div>

                                                  <div className='flex flex-wrap items-center gap-2 pt-1 border-t'>
                                                    <Button
                                                      type='button'
                                                      size='sm'
                                                      variant='outline'
                                                      disabled={
                                                        busy ||
                                                        DEMO_MODE ||
                                                        isPendingDev ||
                                                        !vpnHealthy
                                                      }
                                                      title={
                                                        isPendingDev
                                                          ? 'Thiết bị chờ chưa đăng ký trên wg-easy Gateway'
                                                          : !vpnHealthy
                                                            ? 'wg-easy Gateway đang ngoại tuyến'
                                                            : 'Tải cấu hình .conf động theo host'
                                                      }
                                                      onClick={() =>
                                                        void downloadDeviceConfigFile(
                                                          member,
                                                          device
                                                        )
                                                      }
                                                    >
                                                      📥 Tải .conf
                                                    </Button>
                                                    <Button
                                                      type='button'
                                                      size='sm'
                                                      variant='outline'
                                                      disabled={
                                                        busy ||
                                                        DEMO_MODE ||
                                                        isPendingDev ||
                                                        !vpnHealthy
                                                      }
                                                      title={
                                                        isPendingDev
                                                          ? 'Thiết bị chờ chưa đăng ký trên wg-easy Gateway'
                                                          : !vpnHealthy
                                                            ? 'wg-easy Gateway đang ngoại tuyến'
                                                            : 'Bật/tắt peer trên wg-easy'
                                                      }
                                                      onClick={() =>
                                                        void toggleDeviceState(member, device)
                                                      }
                                                    >
                                                      {device.enabled
                                                        ? '⏸ Tạm dừng'
                                                        : '▶ Kích hoạt'}
                                                    </Button>
                                                    <Button
                                                      type='button'
                                                      size='sm'
                                                      variant='destructive'
                                                      disabled={busy || DEMO_MODE}
                                                      onClick={() =>
                                                        void revokeDeviceV2Action(member, device)
                                                      }
                                                    >
                                                      {isPendingDev ? 'Xóa bản ghi chờ' : 'Thu hồi'}
                                                    </Button>
                                                  </div>
                                                </div>
                                              );
                                            })}
                                          </div>
                                        ) : (
                                          <div className='rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground'>
                                            Chưa có thiết bị VPN nào. Hãy tạo thiết bị bên dưới để
                                            cấp file .conf kết nối!
                                          </div>
                                        )}

                                        {/* FORM CẤP THIẾT BỊ 1 CHẠM */}
                                        <div className='rounded-md border p-3 space-y-2 bg-muted/20'>
                                          <p className='text-xs font-semibold'>
                                            ⚡ Cấp thiết bị WireGuard mới (1-chạm tự sinh Key &
                                            Config)
                                          </p>
                                          {!vpnHealthy && (
                                            <p className='text-xs text-rose-700 dark:text-rose-300'>
                                              wg-easy Gateway đang OFFLINE — không thể cấp peer mới
                                              cho đến khi khởi động container{' '}
                                              <code>labops-wg-easy</code>.
                                            </p>
                                          )}
                                          <div className='flex gap-2'>
                                            <Input
                                              aria-label='Tên thiết bị mới'
                                              placeholder='VD: MacBook M3, iPhone, Máy trạm cá nhân...'
                                              disabled={!vpnHealthy || busy || DEMO_MODE}
                                              value={newDeviceNames[member.id] || ''}
                                              onChange={(e) =>
                                                setNewDeviceNames((prev) => ({
                                                  ...prev,
                                                  [member.id]: e.target.value
                                                }))
                                              }
                                              onKeyDown={(e) => {
                                                if (e.key === 'Enter') {
                                                  e.preventDefault();
                                                  void quickAddMemberDevice(member);
                                                }
                                              }}
                                            />
                                            <Button
                                              type='button'
                                              size='sm'
                                              disabled={
                                                busy ||
                                                DEMO_MODE ||
                                                !vpnHealthy ||
                                                !(newDeviceNames[member.id] || '').trim()
                                              }
                                              onClick={() => void quickAddMemberDevice(member)}
                                            >
                                              + Cấp thiết bị
                                            </Button>
                                          </div>
                                          <p className='text-xs text-muted-foreground'>
                                            Hệ thống wg-easy tự động cấp IP riêng trong subnet{' '}
                                            <code>{vpnOverview?.cidr || '10.88.40.0/24'}</code> và
                                            tải ngay file cấu hình <code>.conf</code>.
                                          </p>
                                        </div>

                                        <div className='border-t pt-2 text-xs text-muted-foreground flex justify-between'>
                                          <span>
                                            Workspace: {member.workspaceStatus || 'NOT_VERIFIED'}
                                          </span>
                                          <span>
                                            Last VPN handshake: {latestHandshake(devices)}
                                          </span>
                                        </div>
                                      </section>
                                    </div>

                                    {/* KHU VỰC NGUY HIỂM: XÓA VĨNH VIỄN TÀI KHOẢN */}
                                    <div className='mt-4 rounded-lg border border-rose-500/40 bg-rose-500/5 p-4 flex flex-wrap items-center justify-between gap-3'>
                                      <div>
                                        <h4 className='text-sm font-semibold text-rose-600 dark:text-rose-400'>
                                          Khu vực nguy hiểm: Xóa vĩnh viễn tài khoản @
                                          {member.username}
                                        </h4>
                                        <p className='text-xs text-muted-foreground mt-0.5'>
                                          Dừng và xóa container workspace ws-{member.username}, thu
                                          hồi và xóa toàn bộ VPN peers trên wg-easy, hủy bỏ các khóa
                                          SSH và dọn dẹp thư mục trên máy chủ.
                                        </p>
                                      </div>
                                      <Button
                                        type='button'
                                        size='sm'
                                        variant='destructive'
                                        disabled={busy || DEMO_MODE}
                                        onClick={() => void deleteMemberAction(member)}
                                      >
                                        Xóa vĩnh viễn tài khoản @{member.username}
                                      </Button>
                                    </div>
                                  </TabsContent>

                                  {/* TAB 3: WORKSPACE */}
                                  <TabsContent value='workspace' className='mt-3 space-y-3'>
                                    <div className='rounded-lg border bg-background p-4 space-y-3'>
                                      <div className='flex items-center justify-between'>
                                        <h4 className='text-sm font-semibold'>
                                          Thông tin Container Workspace
                                        </h4>
                                        {statusBadge(member.workspaceStatus)}
                                      </div>
                                      <div className='grid gap-4 md:grid-cols-2 text-xs'>
                                        <div className='space-y-2'>
                                          <div className='flex justify-between border-b pb-1'>
                                            <span className='text-muted-foreground'>
                                              Tên Workspace / Container:
                                            </span>
                                            <span className='font-mono font-bold'>
                                              {member.workspaceName || `ws-${member.username}`}
                                            </span>
                                          </div>
                                          <div className='flex justify-between border-b pb-1'>
                                            <span className='text-muted-foreground'>
                                              Máy chủ vật lý:
                                            </span>
                                            <span className='font-semibold'>
                                              {member.assignedServer === 'workstation'
                                                ? `Workstation aiot-ws1 (${resolvedHostIp})`
                                                : `Rack aiotlab-rack (${resolvedHostIp})`}
                                            </span>
                                          </div>
                                          <div className='flex justify-between border-b pb-1'>
                                            <span className='text-muted-foreground'>
                                              Cổng SSH ánh xạ:
                                            </span>
                                            <span className='font-mono font-bold text-foreground'>
                                              {member.sshPort || 'Chưa cấp'}
                                            </span>
                                          </div>
                                        </div>
                                        <div className='space-y-2'>
                                          <div className='flex justify-between border-b pb-1'>
                                            <span className='text-muted-foreground'>
                                              Chính sách CPU:
                                            </span>
                                            <span className='font-mono'>
                                              {member.cpuPolicy || 'SHARED_WEIGHTED'}
                                            </span>
                                          </div>
                                          <div className='flex justify-between border-b pb-1'>
                                            <span className='text-muted-foreground'>
                                              Chính sách RAM:
                                            </span>
                                            <span className='font-mono'>
                                              {member.memoryPolicy || 'SHARED_HOST_PROTECTED'}
                                            </span>
                                          </div>
                                          <div className='flex justify-between border-b pb-1 gap-2'>
                                            <span className='text-muted-foreground shrink-0'>
                                              Thư mục lưu trữ:
                                            </span>
                                            <code className='font-mono text-right break-all'>
                                              {member.memberStorageDirectory ||
                                                member.workspaceDir ||
                                                member.mountPath ||
                                                'Chưa cấp phát'}
                                            </code>
                                          </div>
                                        </div>
                                      </div>
                                    </div>
                                  </TabsContent>

                                  {/* TAB 4: GPU & TIẾN TRÌNH */}
                                  <TabsContent value='gpu_jobs' className='mt-3 space-y-3'>
                                    <div className='rounded-lg border bg-background p-4 space-y-3 text-xs'>
                                      <div className='flex items-center justify-between'>
                                        <h4 className='text-sm font-semibold'>
                                          Tài nguyên GPU &amp; Tiến trình đang chạy
                                        </h4>
                                        <Badge variant='outline' className='font-mono text-xs'>
                                          {member.gpuPermissions && member.gpuPermissions.length > 0
                                            ? member.gpuAllocationMode || 'SHARED'
                                            : 'CPU_ONLY'}
                                        </Badge>
                                      </div>
                                      {member.gpuPermissions && member.gpuPermissions.length > 0 ? (
                                        <div className='space-y-1.5 text-muted-foreground'>
                                          <p>
                                            GPU được cấp:{' '}
                                            <strong className='font-mono text-foreground'>
                                              {member.gpuPermissions.join(', ')}
                                            </strong>{' '}
                                            · Chính sách VRAM:{' '}
                                            <strong className='text-foreground'>
                                              {member.vramPolicy || 'Policy-Monitored'}
                                            </strong>
                                          </p>
                                          <p>
                                            Tiến trình chạy trên GPU được xác minh thực tế qua Go
                                            Node Agent và NVIDIA NVML.
                                          </p>
                                        </div>
                                      ) : (
                                        <p className='text-muted-foreground'>
                                          Tài khoản @{member.username} hiện chưa được cấp phát GPU
                                          riêng (chạy chế độ CPU-only).
                                        </p>
                                      )}
                                      <div className='pt-2 flex gap-2'>
                                        <Button
                                          size='sm'
                                          variant='outline'
                                          onClick={() => window.open('/dashboard/jobs', '_self')}
                                        >
                                          Xem danh sách tiến trình trên Dashboard Công việc →
                                        </Button>
                                      </div>
                                    </div>
                                  </TabsContent>

                                  {/* TAB 5: NHẬT KÝ */}
                                  <TabsContent value='logs' className='mt-3 space-y-3'>
                                    <div className='rounded-lg border bg-background p-4 space-y-3 text-xs'>
                                      <h4 className='text-sm font-semibold'>
                                        Nhật ký bảo mật &amp; Hoạt động gần đây
                                      </h4>
                                      <div className='space-y-2'>
                                        <div className='flex justify-between border-b pb-1'>
                                          <span className='text-muted-foreground'>
                                            Lần handshake VPN mới nhất:
                                          </span>
                                          <span className='font-mono'>
                                            {latestHandshake(devices)}
                                          </span>
                                        </div>
                                        <div className='flex justify-between border-b pb-1'>
                                          <span className='text-muted-foreground'>
                                            Trạng thái mật khẩu tạm:
                                          </span>
                                          <span className='font-mono'>
                                            {member.passwordStatus || 'NOT_APPLICABLE'}
                                          </span>
                                        </div>
                                        <div className='flex justify-between border-b pb-1'>
                                          <span className='text-muted-foreground'>
                                            SSH Key Fingerprint:
                                          </span>
                                          <span className='font-mono'>
                                            {member.sshFingerprint || 'Chưa ghi nhận'}
                                          </span>
                                        </div>
                                      </div>
                                    </div>
                                  </TabsContent>
                                </Tabs>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                    {!visibleMembers.length && (
                      <tr>
                        <td colSpan={7} className='p-8 text-center text-sm text-muted-foreground'>
                          {memberSyncError
                            ? 'Không thể tải danh sách từ backend.'
                            : 'Chưa có thành viên nào.'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}
      <MemberProvisioningWizard
        open={wizardOpen}
        onOpenChange={(open) => {
          setWizardOpen(open);
          if (!open) void refresh();
        }}
      />
      <SafeMemberDeleteDialog
        open={Boolean(deleteTargetMember)}
        member={deleteTargetMember}
        onOpenChange={(open) => {
          if (!open) setDeleteTargetMember(null);
        }}
        onMemberUpdated={async () => {
          await refresh();
        }}
        onDeleted={async () => {
          if (deleteTargetMember && managedUser === deleteTargetMember.id) {
            setManagedUser(null);
          }
          setDeleteTargetMember(null);
          await refresh();
        }}
      />
    </div>
  );
}
