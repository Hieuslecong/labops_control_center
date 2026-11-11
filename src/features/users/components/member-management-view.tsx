'use client';

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useLabOpsStore } from '@/features/labops/store';
import { MemberProvisioningWizard } from './member-provisioning-wizard';
import { LabMember, WireGuardDevice, WireGuardVpnOverview } from '@/types/labops';
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
    value === 'ACTIVE' ||
    value === 'READY' ||
    value === 'AVAILABLE' ||
    value === 'VERIFIED' ||
    value === 'SSH_KEY_ENROLLED'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : value === 'PARTIAL_FAILURE' ||
          value === 'UNAVAILABLE' ||
          value === 'FAILED' ||
          value === 'VERIFIED_REVOKED' ||
          value === 'REVOKED'
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
  const [health, setHealth] = useState<AccessHealth | null>(null);
  const [vpnOverview, setVpnOverview] = useState<WireGuardVpnOverview | null>(null);
  const [devicesByUser, setDevicesByUser] = useState<Record<string, WireGuardDevice[]>>({});
  const [newDeviceNames, setNewDeviceNames] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState('');
  const [wizardOpen, setWizardOpen] = useState(false);
  const [managedUser, setManagedUser] = useState<string | null>(null);
  const [deviceName, setDeviceName] = useState('');
  const [publicKey, setPublicKey] = useState('');
  const [sshLabel, setSshLabel] = useState('Laptop cá nhân');
  const [sshKeyInput, setSshKeyInput] = useState('');
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

  const refresh = useCallback(async () => {
    if (DEMO_MODE) return;
    await fetchAccessMembers();
    try {
      const healthResponse = await fetch('/api/access/health', { cache: 'no-store' });
      setHealth(healthResponse.ok ? await healthResponse.json() : null);
    } catch {
      setHealth(null);
    }
    try {
      const vpnRes = await fetch('/api/labops/v2/vpn/overview', { cache: 'no-store' });
      if (vpnRes.ok) {
        setVpnOverview(await vpnRes.json());
      }
    } catch {
      // ignore
    }
    const rows = useLabOpsStore.getState().members;
    const results = await Promise.all(
      rows.map(async (member) => {
        try {
          const v2Res = await fetch(`/api/labops/v2/members/${encodeURIComponent(member.id)}/devices`, {
            cache: 'no-store'
          });
          if (v2Res.ok) {
            const rawDevices = await v2Res.json();
            if (Array.isArray(rawDevices) && rawDevices.length > 0) {
              const normalized: WireGuardDevice[] = rawDevices.map((d: any) => ({
                deviceId: String(d.device_id || d.deviceId || d.id),
                id: String(d.id || d.device_id || ''),
                name: d.name || 'Thiết bị VPN',
                clientIp: d.address || d.clientIp || d.vpn_ip || '',
                publicKey: d.public_key || d.publicKey || '',
                status: (d.status || (d.enabled !== false ? 'ACTIVE' : 'DISABLED')) as WireGuardDevice['status'],
                enabled: d.enabled !== false,
                lastHandshake: d.last_handshake || d.lastHandshake || null,
                transferRx: Number(d.transfer_rx || d.transferRx || 0),
                transferTx: Number(d.transfer_tx || d.transferTx || 0)
              }));
              return [member.id, normalized] as const;
            }
          }
        } catch {
          // fallback to legacy
        }
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

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void refresh();
  }, [refresh]);
  /* oxlint-enable react/set-state-in-effect */

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

  const downloadDeviceConfigFile = async (member: LabMember, device: WireGuardDevice) => {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/labops/v2/members/${encodeURIComponent(member.id)}/devices/${encodeURIComponent(device.deviceId)}/config`,
        { cache: 'no-store' }
      );
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(typeof err.detail === 'string' ? err.detail : `Lỗi tải config (${response.status})`);
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
    const confirmed = window.confirm(`Xác nhận thu hồi thiết bị "${device.name}" của @${member.username}?`);
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
        toast.success(`Đã tự động tải cấu hình ${member.username}-${safeName}.conf để import ngay!`);
      }
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

  const rotateMemberSshKeypair = async (member: LabMember, requirePassphrase = false) => {
    const result = await runAction(
      `/api/labops/v2/ssh-access/${encodeURIComponent(member.id)}/rotate-key`,
      'POST',
      { require_passphrase: requirePassphrase }
    );
    if (result) {
      toast.success(
        `Đã tạo cặp khóa Ed25519 mới (${requirePassphrase ? 'có passphrase' : 'kết nối thẳng 1 chạm'}) cho @${member.username}. Bạn có thể tải file config ngay bên dưới!`
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

  const downloadTextFile = (filename: string, content: string) => {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
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
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Gói khóa riêng đã được tải trước đó.');
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
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Thông tin bí mật đã được lấy trước đó.');
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
        toast.success(`Đã xác minh thu hồi SSH thành công (VERIFIED_REVOKED) cho @${member.username}.`);
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

  const deleteMemberAction = async (member: LabMember) => {
    const confirmed = window.confirm(
      `⚠️ CẢNH BÁO XÓA TÀI KHOẢN VĨNH VIỄN:\n\n` +
        `Bạn có chắc chắn muốn XÓA VĨNH VIỄN tài khoản @${member.username} (${member.fullName})?\n\n` +
        `Thao tác này sẽ thực hiện:\n` +
        `1. Dừng và xóa container workspace ws-${member.username}\n` +
        `2. Thu hồi và xóa toàn bộ WireGuard VPN peers trên wg-easy\n` +
        `3. Hủy bỏ toàn bộ SSH keys và secrets của thành viên\n` +
        `4. Dọn dẹp thư mục workspace trên máy chủ\n\n` +
        `Thao tác này KHÔNG THỂ KHÔI PHỤC!`
    );
    if (!confirmed) return;
    const result = await runAction(
      `/api/labops/v2/members/${encodeURIComponent(member.id)}`,
      'DELETE'
    );
    if (result) {
      toast.success(`Đã xóa vĩnh viễn tài khoản @${member.username} thành công.`);
      if (managedUser === member.id) setManagedUser(null);
      await refresh();
    }
  };

  const handleFirstLoginPasswordChange = async (member: LabMember) => {
    if (!tempPasswordInput.trim() || !newPasswordInput.trim()) {
      toast.error('Vui lòng nhập đầy đủ mật khẩu tạm và mật khẩu mới.');
      return;
    }
    const result = await runAction('/api/labops/v2/ssh-access/first-login-change-password', 'POST', {
      username: member.username,
      temporary_password: tempPasswordInput,
      new_password: newPasswordInput
    });
    if (result) {
      setTempPasswordInput('');
      setNewPasswordInput('');
      toast.success(`Đã đổi mật khẩu lần đầu thành công cho @${member.username}! Mật khẩu tạm đã hết hiệu lực.`);
    }
  };

  return (
    <div className='space-y-5'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <div>
          <h1 className='text-2xl font-semibold'>Quản lý Thành viên & Quyền Truy cập SSH</h1>
          <p className='text-sm text-muted-foreground'>
            Admin tạo, cấp phát khóa SSH mã hóa Passphrase, mật khẩu tạm lần đầu và thu hồi quyền truy cập từ Dashboard
          </p>
        </div>
        <div className='flex gap-2'>
          <Button variant='outline' onClick={() => void refresh()} disabled={busy || DEMO_MODE}>
            Làm mới dữ liệu
          </Button>
          <Button onClick={() => setWizardOpen(true)} disabled={DEMO_MODE}>
            + Tạo quyền truy cập SSH
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

      {/* WIREGUARD VPN SYSTEM OVERVIEW */}
      <Card className='border-primary/30 bg-card/60 backdrop-blur-sm shadow-sm'>
        <CardContent className='p-4 space-y-3'>
          <div className='flex flex-wrap items-center justify-between gap-2 border-b pb-2'>
            <div className='flex items-center gap-2'>
              <span className='text-base font-semibold'>Hệ thống WireGuard VPN Mesh</span>
              <Badge variant='outline' className='bg-emerald-500/10 text-emerald-600 border-emerald-500/30'>
                {vpnOverview?.active_provider === 'wg-easy' ? 'wg-easy v15 (Active & Healthy)' : 'Kernel wg0'}
              </Badge>
              <Badge variant='outline' className='text-xs'>
                Trạng thái: {vpnOverview?.status || 'HEALTHY'}
              </Badge>
            </div>
            <div className='text-xs text-muted-foreground flex flex-wrap items-center gap-3'>
              <span>
                Public Endpoint:{' '}
                <strong className='text-foreground font-mono'>
                  {vpnOverview?.public_endpoint || '118.70.169.236:2204'}
                </strong>
              </span>
              <span>
                Subnet:{' '}
                <strong className='text-foreground font-mono'>{vpnOverview?.cidr || '10.88.40.0/24'}</strong>
              </span>
            </div>
          </div>

          <div className='grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs'>
            <div className='rounded-md border p-2.5 bg-muted/20'>
              <div className='text-muted-foreground'>Cổng UDP & Chuyển tiếp</div>
              <div className='font-mono font-medium text-sm mt-0.5'>UDP 2204 (MikroTik NAT)</div>
              <div className='text-[11px] text-muted-foreground mt-0.5'>118.70.169.236:2204</div>
            </div>
            <div className='rounded-md border p-2.5 bg-muted/20'>
              <div className='text-muted-foreground'>Thiết bị VPN / Kết nối</div>
              <div className='font-mono font-medium text-sm mt-0.5 text-emerald-600 dark:text-emerald-400'>
                {vpnOverview?.active_clients ?? 0} / {vpnOverview?.total_clients ?? 0} online
              </div>
              <div className='text-[11px] text-muted-foreground mt-0.5'>Handshake gần nhất &lt; 3 phút</div>
            </div>
            <div className='rounded-md border p-2.5 bg-muted/20'>
              <div className='text-muted-foreground'>Lưu lượng Mạng VPN</div>
              <div className='font-mono font-medium text-sm mt-0.5'>
                ↓ {formatBytes(vpnOverview?.total_rx_bytes)} · ↑ {formatBytes(vpnOverview?.total_tx_bytes)}
              </div>
              <div className='text-[11px] text-muted-foreground mt-0.5'>Tổng lưu lượng toàn trạm</div>
            </div>
            <div className='rounded-md border p-2.5 bg-muted/20'>
              <div className='text-muted-foreground'>Độc lập & Bảo toàn</div>
              <div className='font-mono font-medium text-sm mt-0.5 text-blue-600 dark:text-blue-400'>
                wg0 (2204) & SSH (22/1911)
              </div>
              <div className='text-[11px] text-muted-foreground mt-0.5'>Bảo toàn nguyên vẹn hạ tầng host</div>
            </div>
          </div>
        </CardContent>
      </Card>

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
                              disabled={
                                busy ||
                                DEMO_MODE ||
                                member.sshKeyStatus === 'VERIFIED_REVOKED'
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
                            <div className='grid gap-5 lg:grid-cols-2'>
                              {/* CỘT 1: VÒNG ĐỜI QUYỀN SSH DO ADMIN QUẢN LÝ */}
                              <section className='space-y-3 rounded-lg border bg-background p-4'>
                                <div className='flex items-center justify-between'>
                                  <h3 className='font-semibold text-sm'>
                                    1. Quản lý Vòng đời Khóa SSH & Mật khẩu tạm (@{member.username})
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
                                        ssh -i id_ed25519_{member.username} -p {member.sshPort} {member.username}@
                                        {member.sshIp || '192.168.167.250'}
                                      </code>
                                    </p>
                                    {member.sshFingerprint && (
                                      <p className='text-[11px] text-muted-foreground'>
                                        Fingerprint: {member.sshFingerprint}
                                      </p>
                                    )}
                                    <p className='text-[11px] text-muted-foreground'>
                                      Trạng thái mật khẩu lần đầu: <strong>{member.passwordStatus || 'NOT_APPLICABLE'}</strong>
                                    </p>
                                  </div>
                                )}

                                <div className='rounded-md border p-3 space-y-2 bg-muted/30'>
                                  <p className='text-xs font-semibold'>
                                    A. Tạo mới / Cấp lại Khóa SSH (Bảo vệ bằng Passphrase) & Bàn giao 1 lần
                                  </p>
                                  <div className='flex flex-wrap gap-2'>
                                    <Button
                                      type='button'
                                      size='sm'
                                      disabled={busy || DEMO_MODE}
                                      onClick={() => void rotateMemberSshKeypair(member, false)}
                                    >
                                      ⚡ Cấp khóa kết nối thẳng 1 chạm (Không Passphrase)
                                    </Button>
                                    <Button
                                      type='button'
                                      size='sm'
                                      variant='outline'
                                      disabled={busy || DEMO_MODE}
                                      onClick={() => void rotateMemberSshKeypair(member, true)}
                                    >
                                      🔒 Cấp khóa có Passphrase
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
                                      disabled={busy || DEMO_MODE}
                                      onClick={() => void downloadMemberAccessZip(member)}
                                    >
                                      📦 Tải bộ kết nối (.zip)
                                    </Button>
                                    <Button
                                      type='button'
                                      size='sm'
                                      variant='secondary'
                                      disabled={busy || DEMO_MODE || !member.oneTimePackage?.channel_secrets_available}
                                      onClick={() => void claimMemberSecrets(member)}
                                    >
                                      {member.oneTimePackage?.channel_secrets_available
                                        ? '🔑 Passphrase & Mật khẩu tạm (Kênh riêng)'
                                        : 'Passphrase & Mật khẩu tạm đã lấy'}
                                    </Button>
                                  </div>

                                  {!member.oneTimePackage?.key_bundle_available && (
                                    <div className='rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-900 dark:text-amber-200'>
                                      Khóa riêng SSH của @{member.username} đã được giải phóng khỏi RAM theo chính sách một lần (One-Time Vault).
                                      Nếu thành viên làm mất file khóa, Admin bấm <strong>Cấp khóa 1 chạm</strong> hoặc <strong>Cấp khóa có Passphrase</strong> bên trên để tạo cặp khóa mới và tải lại file ZIP ngay!
                                    </div>
                                  )}

                                  <div className='pt-1'>
                                    <details className='text-xs text-muted-foreground cursor-pointer'>
                                      <summary className='font-medium text-primary hover:underline py-0.5'>
                                        ▶ Hoặc giải nén / lấy từng file cấu hình rời (.conf, .sh, SSH key)
                                      </summary>
                                      <div className='mt-2 space-y-2 pl-2 border-l-2 border-muted'>
                                        <Button
                                          type='button'
                                          size='sm'
                                          variant='outline'
                                          disabled={busy || DEMO_MODE || !member.oneTimePackage?.key_bundle_available}
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
                                                claimedBundleByUser[member.id]!.wireguard_conf!
                                              )
                                            }
                                          >
                                            Tải {member.username}-labops-wg0.conf (Import vào WireGuard)
                                          </Button>
                                        )}
                                        {claimedBundleByUser[member.id]?.quick_setup_script && (
                                          <Button
                                            type='button'
                                            size='sm'
                                            variant='outline'
                                            onClick={() =>
                                              downloadTextFile(
                                                `setup-ssh-${member.username}.sh`,
                                                claimedBundleByUser[member.id]!.quick_setup_script!
                                              )
                                            }
                                          >
                                            Tải setup-ssh-{member.username}.sh (Tự động cài SSH & Key)
                                          </Button>
                                        )}
                                        {claimedBundleByUser[member.id]?.ssh_config_snippet && (
                                          <Button
                                            type='button'
                                            size='sm'
                                            variant='outline'
                                            onClick={() =>
                                              downloadTextFile(
                                                `ssh_config_${member.username}`,
                                                claimedBundleByUser[member.id]!.ssh_config_snippet!
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
                                              claimedBundleByUser[member.id]!.encrypted_private_key
                                            )
                                          }
                                        >
                                          Tải riêng Key id_ed25519_{member.username}
                                        </Button>
                                      </div>
                                      <p className='font-mono text-[11px] text-muted-foreground'>
                                        Chạy <code>bash setup-ssh-{member.username}.sh</code> xong gõ luôn:{' '}
                                        <strong className='text-foreground'>
                                          {claimedBundleByUser[member.id]?.ssh_alias_command || `ssh labops-${member.username}`}
                                        </strong>
                                      </p>
                                    </div>
                                  )}

                                  {claimedSecretsByUser[member.id] && (
                                    <div className='rounded border border-emerald-500/40 bg-emerald-500/10 p-2.5 text-xs font-mono space-y-1'>
                                      <p className='font-sans font-semibold text-emerald-800 dark:text-emerald-200'>
                                        Thông tin bí mật hiển thị 1 lần (Gửi cho @{member.username} qua kênh riêng):
                                      </p>
                                      {claimedSecretsByUser[member.id]?.key_passphrase && (
                                        <p>
                                          Passphrase mở khóa Private Key:{' '}
                                          <strong className='select-all bg-background px-1.5 py-0.5 rounded border'>
                                            {claimedSecretsByUser[member.id]?.key_passphrase}
                                          </strong>
                                        </p>
                                      )}
                                      {claimedSecretsByUser[member.id]?.temporary_password && (
                                        <p>
                                          Mật khẩu tạm đăng nhập lần đầu:{' '}
                                          <strong className='select-all bg-background px-1.5 py-0.5 rounded border'>
                                            {claimedSecretsByUser[member.id]?.temporary_password}
                                          </strong>
                                        </p>
                                      )}
                                    </div>
                                  )}
                                </div>

                                <div className='rounded-md border p-3 space-y-2'>
                                  <p className='text-xs font-semibold'>
                                    B. Đổi mật khẩu lần đầu (Từ mật khẩu tạm sang mật khẩu chính thức)
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
                                    onClick={() => void handleFirstLoginPasswordChange(member)}
                                  >
                                    Xác nhận đổi mật khẩu lần đầu
                                  </Button>
                                </div>

                                <div className='flex flex-wrap items-center justify-between gap-2 pt-1 border-t'>
                                  <span className='text-xs text-muted-foreground'>
                                    Đóng các phiên SSH đang mở của workspace (tách riêng với thu hồi khóa):
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
                                    2. Quản lý Đa Thiết Bị WireGuard VPN (@{member.username})
                                  </h3>
                                  <Badge variant='outline' className='text-xs'>
                                    {devices.length} thiết bị
                                  </Badge>
                                </div>

                                {devices.length ? (
                                  <div className='space-y-2 max-h-[360px] overflow-y-auto pr-1'>
                                    {devices.map((device) => (
                                      <div
                                        key={device.deviceId}
                                        className='rounded-md border p-3 text-xs space-y-2 bg-card'
                                      >
                                        <div className='flex flex-wrap items-center justify-between gap-1'>
                                          <div className='flex items-center gap-2'>
                                            <span className='font-semibold text-sm'>{device.name}</span>
                                            <Badge
                                              variant='outline'
                                              className={
                                                device.enabled && device.status === 'ACTIVE'
                                                  ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30'
                                                  : 'bg-amber-500/10 text-amber-700 border-amber-500/30'
                                              }
                                            >
                                              {device.enabled ? 'ACTIVE' : 'PAUSED'}
                                            </Badge>
                                          </div>
                                          <div className='font-mono text-muted-foreground'>
                                            IP: <strong className='text-foreground'>{device.clientIp || '10.88.40.x'}</strong>
                                          </div>
                                        </div>

                                        <div className='grid grid-cols-2 gap-2 text-[11px] text-muted-foreground'>
                                          <div>
                                            Handshake:{' '}
                                            <span className='text-foreground'>
                                              {device.lastHandshake
                                                ? typeof device.lastHandshake === 'number'
                                                  ? new Date(device.lastHandshake * 1000).toLocaleString()
                                                  : String(device.lastHandshake)
                                                : 'Chưa có kết nối'}
                                            </span>
                                          </div>
                                          <div>
                                            Lưu lượng:{' '}
                                            <span className='text-foreground font-mono'>
                                              ↓ {formatBytes(device.transferRx)} · ↑ {formatBytes(device.transferTx)}
                                            </span>
                                          </div>
                                        </div>

                                        <div className='flex flex-wrap items-center gap-2 pt-1 border-t'>
                                          <Button
                                            type='button'
                                            size='sm'
                                            variant='outline'
                                            disabled={busy || DEMO_MODE}
                                            onClick={() => void downloadDeviceConfigFile(member, device)}
                                          >
                                            📥 Tải .conf
                                          </Button>
                                          <Button
                                            type='button'
                                            size='sm'
                                            variant='outline'
                                            disabled={busy || DEMO_MODE}
                                            onClick={() => void toggleDeviceState(member, device)}
                                          >
                                            {device.enabled ? '⏸ Tạm dừng' : '▶ Kích hoạt'}
                                          </Button>
                                          <Button
                                            type='button'
                                            size='sm'
                                            variant='destructive'
                                            disabled={busy || DEMO_MODE}
                                            onClick={() => void revokeDeviceV2Action(member, device)}
                                          >
                                            Thu hồi
                                          </Button>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <div className='rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground'>
                                    Chưa có thiết bị VPN nào. Hãy tạo thiết bị bên dưới để cấp file .conf kết nối!
                                  </div>
                                )}

                                {/* FORM CẤP THIẾT BỊ 1 CHẠM */}
                                <div className='rounded-md border p-3 space-y-2 bg-muted/20'>
                                  <p className='text-xs font-semibold'>
                                    ⚡ Cấp thiết bị WireGuard mới (1-chạm tự sinh Key & Config)
                                  </p>
                                  <div className='flex gap-2'>
                                    <Input
                                      aria-label='Tên thiết bị mới'
                                      placeholder='VD: MacBook M3, iPhone, Máy trạm cá nhân...'
                                      value={newDeviceNames[member.id] || ''}
                                      onChange={(e) =>
                                        setNewDeviceNames((prev) => ({ ...prev, [member.id]: e.target.value }))
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
                                      disabled={busy || DEMO_MODE || !(newDeviceNames[member.id] || '').trim()}
                                      onClick={() => void quickAddMemberDevice(member)}
                                    >
                                      + Cấp thiết bị
                                    </Button>
                                  </div>
                                  <p className='text-[11px] text-muted-foreground'>
                                    Hệ thống wg-easy tự động cấp IP riêng trong subnet <code>10.88.40.0/24</code> và tải ngay file cấu hình <code>.conf</code>.
                                  </p>
                                </div>

                                {/* NÂNG CAO: TỰ NHẬP PUBLIC KEY */}
                                <details className='text-xs text-muted-foreground cursor-pointer'>
                                  <summary className='font-medium text-primary hover:underline py-0.5'>
                                    ▶ Nâng cao: Đăng ký bằng WireGuard Public Key tự sinh
                                  </summary>
                                  <div className='mt-2 space-y-2 pl-2 border-l-2 border-muted'>
                                    <Input
                                      aria-label='Tên thiết bị'
                                      placeholder='Tên thiết bị'
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
                                      disabled={busy || !deviceName.trim() || !publicKey.trim()}
                                      onClick={() => void registerDevice(member)}
                                    >
                                      Đăng ký WireGuard Public Key
                                    </Button>
                                  </div>
                                </details>

                                <div className='border-t pt-2 text-xs text-muted-foreground flex justify-between'>
                                  <span>Workspace: {member.workspaceStatus || 'NOT_VERIFIED'}</span>
                                  <span>Last VPN handshake: {latestHandshake(devices)}</span>
                                </div>
                              </section>

                              {/* KHU VỰC NGUY HIỂM: XÓA VĨNH VIỄN TÀI KHOẢN */}
                              <div className='lg:col-span-2 rounded-lg border border-rose-500/40 bg-rose-500/5 p-4 flex flex-wrap items-center justify-between gap-3'>
                                <div>
                                  <h4 className='text-sm font-semibold text-rose-600 dark:text-rose-400'>
                                    Khu vực nguy hiểm: Xóa vĩnh viễn tài khoản @{member.username}
                                  </h4>
                                  <p className='text-xs text-muted-foreground mt-0.5'>
                                    Dừng và xóa container workspace ws-{member.username}, thu hồi và xóa toàn bộ VPN peers trên wg-easy, hủy bỏ các khóa SSH và dọn dẹp thư mục trên máy chủ.
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
