'use client';

import React, { useState, useMemo } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter
} from '@/components/ui/dialog';
import { Icons } from '@/components/icons';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator
} from '@/components/ui/dropdown-menu';
import { useLabOpsStore } from '@/features/labops/store';
import { MemberProvisioningWizard } from './member-provisioning-wizard';
import { MemberRole, MemberStatus, LabMember } from '@/types/labops';
import { toast } from 'sonner';

function getRoleBadge(role: MemberRole) {
  switch (role) {
    case 'SUPER_ADMIN':
      return (
        <Badge variant='default' className='bg-purple-600 text-[10px]'>
          SUPER ADMIN
        </Badge>
      );
    case 'LAB_MANAGER':
      return (
        <Badge
          variant='outline'
          className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[10px]'
        >
          LAB MANAGER
        </Badge>
      );
    case 'RESEARCHER':
      return (
        <Badge
          variant='outline'
          className='bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/30 text-[10px]'
        >
          NGHIÊN CỨU VIÊN
        </Badge>
      );
    case 'STUDENT':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px]'
        >
          SINH VIÊN
        </Badge>
      );
    case 'GUEST':
    default:
      return (
        <Badge variant='secondary' className='text-[10px]'>
          KHÁCH MỜI
        </Badge>
      );
  }
}

function getStatusBadge(status: MemberStatus) {
  switch (status) {
    case 'ACTIVE':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-medium'
        >
          HOẠT ĐỘNG
        </Badge>
      );
    case 'PENDING_KEY_ENROLLMENT':
      return (
        <Badge
          variant='outline'
          className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[10px] font-medium'
        >
          CHỜ NẠP KHÓA
        </Badge>
      );
    case 'SUSPENDED':
      return (
        <Badge
          variant='outline'
          className='bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30 text-[10px] font-medium'
        >
          TẠM KHÓA
        </Badge>
      );
    case 'ARCHIVED':
    default:
      return (
        <Badge variant='secondary' className='text-muted-foreground text-[10px] font-medium'>
          LƯU TRỮ
        </Badge>
      );
  }
}

export function MemberManagementView() {
  const members = useLabOpsStore((state) => state.members);
  const jobs = useLabOpsStore((state) => state.jobs);
  const auditLogs = useLabOpsStore((state) => state.auditLogs);

  const updateMemberStatus = useLabOpsStore((state) => state.updateMemberStatus);
  const rotateSshKey = useLabOpsStore((state) => state.rotateSshKey);
  const revokeSshKey = useLabOpsStore((state) => state.revokeSshKey);
  const changeStorageQuota = useLabOpsStore((state) => state.changeStorageQuota);
  const changeSshPort = useLabOpsStore((state) => state.changeSshPort);
  const archiveWorkspace = useLabOpsStore((state) => state.archiveWorkspace);
  const deleteMemberPermanently = useLabOpsStore((state) => state.deleteMemberPermanently);

  const [wizardOpen, setWizardOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [serverFilter, setServerFilter] = useState<string>('all');
  const [roleFilter, setRoleFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');

  // Detail Modal State (8 Tabs)
  const [detailMember, setDetailMember] = useState<LabMember | null>(null);
  const [activeTab, setActiveTab] = useState<number>(1);

  // Action Modals State
  const [quotaModalMember, setQuotaModalMember] = useState<LabMember | null>(null);
  const [newQuotaValue, setNewQuotaValue] = useState<number>(250);

  const [portModalMember, setPortModalMember] = useState<LabMember | null>(null);
  const [newPortValue, setNewPortValue] = useState<number>(22001);

  const [rotateKeyModalMember, setRotateKeyModalMember] = useState<LabMember | null>(null);
  const [newPublicKeyInput, setNewPublicKeyInput] = useState<string>('');

  const [deleteModalMember, setDeleteModalMember] = useState<LabMember | null>(null);
  const [deleteConfirmText, setDeleteConfirmText] = useState<string>('');

  const filteredMembers = useMemo(() => {
    return members.filter((m) => {
      const matchesSearch =
        m.fullName.toLowerCase().includes(searchTerm.toLowerCase()) ||
        m.username.toLowerCase().includes(searchTerm.toLowerCase()) ||
        m.email.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (m.researchGroup && m.researchGroup.toLowerCase().includes(searchTerm.toLowerCase()));
      const matchesServer = serverFilter === 'all' || m.assignedServer === serverFilter;
      const matchesRole = roleFilter === 'all' || m.role === roleFilter;
      const matchesStatus = statusFilter === 'all' || m.status === statusFilter;
      return matchesSearch && matchesServer && matchesRole && matchesStatus;
    });
  }, [members, searchTerm, serverFilter, roleFilter, statusFilter]);

  const totalAllocatedZfsGb = useMemo(
    () => members.reduce((acc, m) => acc + (m.storageQuotaGb || 0), 0),
    [members]
  );

  return (
    <div className='flex flex-col gap-4'>
      {/* KPI Stats on Users */}
      <div className='grid grid-cols-2 sm:grid-cols-4 gap-3'>
        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Tổng số thành viên</span>
          <div className='text-xl font-bold mt-0.5'>{members.length} người</div>
          <span className='text-[10px] text-muted-foreground'>Standard Users (Không sudo)</span>
        </Card>
        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Phiên SSH trực tuyến
          </span>
          <div className='text-xl font-bold mt-0.5 text-emerald-600 dark:text-emerald-400'>
            {members.filter((m) => m.sshStatus === 'ONLINE').length} người
          </div>
          <span className='text-[10px] text-muted-foreground'>
            {members.reduce((a, b) => a + (b.sshSessionsCount || 0), 0)} kết nối WireGuard
          </span>
        </Card>
        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Tổng Quota ZFS Cấp phát
          </span>
          <div className='text-xl font-bold mt-0.5 text-blue-600 dark:text-blue-400'>
            {totalAllocatedZfsGb} GB
          </div>
          <span className='text-[10px] text-muted-foreground'>Đã mount tại /workspace</span>
        </Card>
        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Workspaces Phân bổ Cụm
          </span>
          <div className='text-xl font-bold mt-0.5 text-purple-600 dark:text-purple-400'>
            {members.filter((m) => m.assignedServer === 'track').length} Trk /{' '}
            {members.filter((m) => m.assignedServer === 'workstation').length} WS
          </div>
          <span className='text-[10px] text-muted-foreground'>Containers Incus unprivileged</span>
        </Card>
      </div>

      {/* Action & Filter Bar */}
      <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-3 rounded-lg border bg-card'>
        <div className='flex items-center gap-2 flex-1 max-w-md'>
          <div className='relative w-full'>
            <Icons.search className='absolute left-2.5 top-2.5 size-4 text-muted-foreground' />
            <Input
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder='Tìm theo tên, @username, email, nhóm nghiên cứu...'
              className='pl-8 h-9 text-xs'
            />
          </div>
        </div>

        <div className='flex items-center gap-2 flex-wrap'>
          {/* Server filter */}
          <select
            value={serverFilter}
            onChange={(e) => setServerFilter(e.target.value)}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả máy chủ</option>
            <option value='track'>Server Track (Hà Nội)</option>
            <option value='workstation'>Server Workstation (TP.HCM)</option>
          </select>

          {/* Role filter */}
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value)}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả vai trò</option>
            <option value='SUPER_ADMIN'>Super Admin</option>
            <option value='LAB_MANAGER'>Lab Manager</option>
            <option value='RESEARCHER'>Nghiên cứu viên</option>
            <option value='STUDENT'>Sinh viên</option>
            <option value='GUEST'>Khách mời</option>
          </select>

          {/* Status filter */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả trạng thái</option>
            <option value='ACTIVE'>Hoạt động</option>
            <option value='PENDING_KEY_ENROLLMENT'>Chờ nạp khóa</option>
            <option value='SUSPENDED'>Tạm khóa</option>
            <option value='ARCHIVED'>Lưu trữ</option>
          </select>

          <Button
            size='sm'
            onClick={() => setWizardOpen(true)}
            className='h-9 text-xs font-semibold gap-1.5 bg-primary ml-auto'
          >
            <Icons.add className='size-4' /> Thêm Thành viên
          </Button>
        </div>
      </div>

      {/* Member Data Table */}
      <Card className='border bg-card'>
        <CardContent className='p-0'>
          <div className='overflow-x-auto'>
            <table className='w-full text-left text-xs'>
              <thead>
                <tr className='border-b text-muted-foreground font-semibold bg-muted/20'>
                  <th className='py-2.5 px-3'>Thành viên</th>
                  <th className='py-2.5 px-3'>Vai trò</th>
                  <th className='py-2.5 px-3'>Máy chủ & Workspace</th>
                  <th className='py-2.5 px-3'>Ổ đĩa & Quota ZFS</th>
                  <th className='py-2.5 px-3'>Điểm cuối SSH (VPN)</th>
                  <th className='py-2.5 px-3'>Hạn ngạch vCPU / RAM</th>
                  <th className='py-2.5 px-3'>Quyền GPU & VRAM</th>
                  <th className='py-2.5 px-3'>Trạng thái</th>
                  <th className='py-2.5 px-3 text-right'>Thao tác</th>
                </tr>
              </thead>
              <tbody className='divide-y divide-border/60'>
                {filteredMembers.map((m) => {
                  const usedGb = m.storageUsedGb || 0;
                  const quotaGb = m.storageQuotaGb || 250;
                  const pct = Math.min(100, Math.round((usedGb / quotaGb) * 100));

                  return (
                    <tr key={m.id} className='hover:bg-muted/30 transition-colors'>
                      {/* Thành viên */}
                      <td className='py-3 px-3'>
                        <button
                          type='button'
                          aria-label={`Xem chi tiết của ${m.fullName}`}
                          className='font-bold text-foreground hover:underline cursor-pointer flex items-center gap-1.5 p-0 bg-transparent border-none text-left'
                          onClick={() => {
                            setDetailMember(m);
                            setActiveTab(1);
                          }}
                        >
                          {m.fullName}
                        </button>
                        <div className='text-[11px] text-muted-foreground font-mono'>
                          @{m.username} • {m.email}
                        </div>
                        {m.researchGroup && (
                          <div className='text-[10px] text-primary/80 truncate max-w-[180px]'>
                            {m.researchGroup}
                          </div>
                        )}
                      </td>

                      {/* Vai trò */}
                      <td className='py-3 px-3'>{getRoleBadge(m.role)}</td>

                      {/* Máy chủ & Workspace */}
                      <td className='py-3 px-3'>
                        <Badge variant='outline' className='font-mono text-[10px] uppercase'>
                          {m.assignedServer}
                        </Badge>
                        <div className='text-[11px] font-mono font-medium text-foreground mt-0.5'>
                          {m.workspaceName}
                        </div>
                        <span className='text-[9px] text-muted-foreground block'>
                          isolated idmap
                        </span>
                      </td>

                      {/* Ổ đĩa & Quota ZFS */}
                      <td className='py-3 px-3'>
                        <div className='font-medium text-foreground text-[11px] truncate max-w-[150px]'>
                          {m.storagePoolLabel || 'ZFS Pool'}
                        </div>
                        <div className='text-[10px] text-muted-foreground font-mono mt-0.5 flex items-center gap-1'>
                          <span>
                            {usedGb}GB / {quotaGb}GB ({pct}%)
                          </span>
                        </div>
                        <div className='w-28 bg-muted rounded-full h-1.5 mt-1 overflow-hidden'>
                          <div
                            className={`h-1.5 rounded-full ${
                              pct > 85 ? 'bg-rose-500' : pct > 60 ? 'bg-amber-500' : 'bg-primary'
                            }`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className='text-[9px] text-muted-foreground font-mono'>
                          Mount: {m.mountPath || '/workspace'}
                        </span>
                      </td>

                      {/* Điểm cuối SSH */}
                      <td className='py-3 px-3'>
                        <div className='flex items-center gap-1.5'>
                          <span
                            className={`size-2 rounded-full ${
                              m.sshStatus === 'ONLINE'
                                ? 'bg-emerald-500 animate-pulse'
                                : 'bg-slate-400'
                            }`}
                          />
                          <code className='font-mono text-[11px] font-bold'>
                            {m.sshIp}:{m.sshPort}
                          </code>
                        </div>
                        <div className='text-[10px] text-muted-foreground font-mono mt-0.5'>
                          {m.sshKeyStatus === 'PENDING_KEY_ENROLLMENT' ? (
                            <span className='text-amber-600 dark:text-amber-400 font-medium'>
                              Chờ nạp SSH Key
                            </span>
                          ) : m.sshKeyStatus === 'REVOKED' ? (
                            <span className='text-rose-600 dark:text-rose-400 font-medium'>
                              Khóa đã bị thu hồi
                            </span>
                          ) : (
                            <span>Khóa Ed25519 kích hoạt</span>
                          )}
                        </div>
                      </td>

                      {/* Hạn ngạch */}
                      <td className='py-3 px-3 font-mono text-[11px]'>
                        <div>{m.cpuLimitCores} Cores vCPU</div>
                        <div className='text-muted-foreground'>{m.ramLimitGb}GB RAM</div>
                      </td>

                      {/* Quyền GPU */}
                      <td className='py-3 px-3'>
                        <div className='text-foreground font-medium text-[11px]'>
                          {m.vramPolicy || 'Chỉ CPU'}
                        </div>
                        <div className='text-[10px] text-muted-foreground font-mono'>
                          Đã dùng: {m.totalGpuHours || 0} GPU-h
                        </div>
                      </td>

                      {/* Trạng thái */}
                      <td className='py-3 px-3'>{getStatusBadge(m.status)}</td>

                      {/* Thao tác */}
                      <td className='py-3 px-3 text-right'>
                        <div className='flex items-center justify-end gap-1'>
                          <Button
                            size='sm'
                            variant='outline'
                            aria-label={`Xem chi tiết cho ${m.fullName}`}
                            className='h-7 px-2 text-[10px] font-semibold'
                            onClick={() => {
                              setDetailMember(m);
                              setActiveTab(1);
                            }}
                          >
                            Chi tiết
                          </Button>

                          <DropdownMenu modal={false}>
                            <DropdownMenuTrigger
                              aria-label={`Mở menu thao tác cho ${m.fullName}`}
                              render={
                                <Button
                                  size='sm'
                                  variant='ghost'
                                  aria-label={`Thao tác cho ${m.fullName}`}
                                  className='size-7 p-0'
                                />
                              }
                            >
                              <span className='sr-only'>Thao tác cho {m.fullName}</span>
                              <Icons.ellipsis className='size-3.5' />
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align='end' className='w-56 text-xs'>
                              <DropdownMenuItem
                                onClick={() => {
                                  setDetailMember(m);
                                  setActiveTab(4); // SSH Tab
                                }}
                              >
                                <Icons.network className='size-3.5 mr-2' /> Xem thông số SSH
                                Endpoint
                              </DropdownMenuItem>

                              <DropdownMenuItem
                                onClick={() => {
                                  setQuotaModalMember(m);
                                  setNewQuotaValue(m.storageQuotaGb || 250);
                                }}
                              >
                                <Icons.database className='size-3.5 mr-2' /> Điều chỉnh Hạn ngạch
                                ZFS
                              </DropdownMenuItem>

                              <DropdownMenuItem
                                onClick={() => {
                                  setPortModalMember(m);
                                  setNewPortValue(m.sshPort);
                                }}
                              >
                                <Icons.terminal className='size-3.5 mr-2' /> Đổi Cổng SSH Incus
                                Proxy
                              </DropdownMenuItem>

                              <DropdownMenuItem
                                onClick={() => {
                                  setRotateKeyModalMember(m);
                                  setNewPublicKeyInput('');
                                }}
                              >
                                <Icons.key className='size-3.5 mr-2' /> Cập nhật / Đổi Khóa SSH
                              </DropdownMenuItem>

                              <DropdownMenuSeparator />

                              {m.sshKeyStatus !== 'REVOKED' && (
                                <DropdownMenuItem
                                  onClick={() => {
                                    if (
                                      confirm(
                                        `Bạn có chắc muốn thu hồi khóa SSH của @${m.username}? Cổng SSH sẽ bị khóa ngay lập tức.`
                                      )
                                    ) {
                                      revokeSshKey(m.id);
                                      toast.warning(`Đã thu hồi khóa SSH của @${m.username}`);
                                    }
                                  }}
                                  className='text-amber-600 focus:text-amber-600'
                                >
                                  <Icons.lock className='size-3.5 mr-2' /> Thu hồi Khóa SSH (Revoke)
                                </DropdownMenuItem>
                              )}

                              {m.status === 'ACTIVE' ? (
                                <DropdownMenuItem
                                  onClick={() => {
                                    updateMemberStatus(m.id, 'SUSPENDED');
                                    toast.warning(`Đã tạm khóa tài khoản @${m.username}`);
                                  }}
                                  className='text-amber-600 focus:text-amber-600'
                                >
                                  <Icons.lock className='size-3.5 mr-2' /> Tạm khóa tài khoản
                                </DropdownMenuItem>
                              ) : (
                                <DropdownMenuItem
                                  onClick={() => {
                                    updateMemberStatus(m.id, 'ACTIVE');
                                    toast.success(`Đã mở khóa tài khoản @${m.username}`);
                                  }}
                                  className='text-emerald-600 focus:text-emerald-600'
                                >
                                  <Icons.check className='size-3.5 mr-2' /> Mở khóa tài khoản
                                </DropdownMenuItem>
                              )}

                              <DropdownMenuItem
                                onClick={() => {
                                  if (
                                    confirm(
                                      `Bạn có chắc muốn lưu trữ workspace của @${m.username}? Container sẽ dừng và lưu trữ snapshot ZFS.`
                                    )
                                  ) {
                                    archiveWorkspace(m.id);
                                    toast.info(`Đã lưu trữ workspace @${m.username}`);
                                  }
                                }}
                              >
                                <Icons.workspace className='size-3.5 mr-2' /> Lưu trữ Workspace
                                (Archive)
                              </DropdownMenuItem>

                              <DropdownMenuSeparator />

                              <DropdownMenuItem
                                onClick={() => {
                                  setDeleteModalMember(m);
                                  setDeleteConfirmText('');
                                }}
                                className='text-rose-600 focus:text-rose-600'
                              >
                                <Icons.trash className='size-3.5 mr-2' /> Xóa vĩnh viễn tài khoản
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* 8-TAB MEMBER DETAIL MODAL */}
      <Dialog open={!!detailMember} onOpenChange={(val) => !val && setDetailMember(null)}>
        <DialogContent className='sm:max-w-[760px] max-h-[92vh] overflow-y-auto'>
          {detailMember && (
            <>
              <DialogHeader>
                <div className='flex items-center justify-between'>
                  <div className='flex items-center gap-2'>
                    <div className='size-9 rounded-full bg-primary/10 flex items-center justify-center font-bold text-primary'>
                      {detailMember.fullName.charAt(0)}
                    </div>
                    <div>
                      <DialogTitle className='text-base font-bold flex items-center gap-2'>
                        {detailMember.fullName}
                        {getRoleBadge(detailMember.role)}
                        {getStatusBadge(detailMember.status)}
                      </DialogTitle>
                      <DialogDescription className='text-xs font-mono'>
                        @{detailMember.username} • {detailMember.email} • Server{' '}
                        {detailMember.assignedServer.toUpperCase()}
                      </DialogDescription>
                    </div>
                  </div>
                </div>

                {/* 8 Tab Navigation */}
                <div className='flex items-center gap-1 border-b pt-3 overflow-x-auto text-xs font-medium'>
                  {[
                    { id: 1, name: 'Tổng quan' },
                    { id: 2, name: 'Workspace Incus' },
                    { id: 3, name: 'Lưu trữ ZFS' },
                    { id: 4, name: 'Truy cập SSH' },
                    { id: 5, name: 'CPU/RAM/GPU' },
                    { id: 6, name: 'Phiên SSH' },
                    { id: 7, name: 'Tác vụ Job' },
                    { id: 8, name: 'Kiểm toán' }
                  ].map((tab) => (
                    <button
                      key={tab.id}
                      type='button'
                      onClick={() => setActiveTab(tab.id)}
                      className={`px-3 py-1.5 border-b-2 whitespace-nowrap transition-colors ${
                        activeTab === tab.id
                          ? 'border-primary text-primary font-bold'
                          : 'border-transparent text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {tab.name}
                    </button>
                  ))}
                </div>
              </DialogHeader>

              {/* TAB 1: TỔNG QUAN */}
              {activeTab === 1 && (
                <div className='grid grid-cols-2 gap-3 py-2 text-xs'>
                  <Card className='p-3 space-y-1.5'>
                    <span className='font-bold text-foreground'>Thông tin Hồ sơ</span>
                    <div className='text-muted-foreground'>
                      Họ và tên:{' '}
                      <strong className='text-foreground'>{detailMember.fullName}</strong>
                    </div>
                    <div className='text-muted-foreground'>
                      Tên đăng nhập:{' '}
                      <strong className='text-foreground font-mono'>
                        @{detailMember.username}
                      </strong>
                    </div>
                    <div className='text-muted-foreground'>
                      Email: <strong className='text-foreground'>{detailMember.email}</strong>
                    </div>
                    <div className='text-muted-foreground'>
                      Nhóm nghiên cứu:{' '}
                      <strong className='text-foreground'>
                        {detailMember.researchGroup || 'AI Lab'}
                      </strong>
                    </div>
                    <div className='text-muted-foreground'>
                      Ngày tạo tài khoản:{' '}
                      <strong className='text-foreground'>{detailMember.createdAt}</strong>
                    </div>
                    <div className='text-muted-foreground'>
                      Hạn truy cập:{' '}
                      <strong className='text-foreground'>
                        {detailMember.expiresAt || 'Vô thời hạn'}
                      </strong>
                    </div>
                  </Card>

                  <Card className='p-3 space-y-1.5 border-amber-500/30 bg-amber-500/5'>
                    <span className='font-bold text-amber-900 dark:text-amber-200 flex items-center gap-1.5'>
                      <Icons.shield className='size-4' /> Quyền hạn & Cách ly An ninh
                    </span>
                    <div className='text-muted-foreground'>
                      Quyền Root/Sudo:{' '}
                      <strong className='text-rose-600 font-bold'>
                        BỊ TỪ CHỐI (Standard User)
                      </strong>
                    </div>
                    <div className='text-muted-foreground'>
                      Incus idmap:{' '}
                      <strong className='text-emerald-600'>security.idmap.isolated=true</strong>
                    </div>
                    <div className='text-muted-foreground'>
                      Socket Docker/Incus:{' '}
                      <strong className='text-rose-600'>Không được phép truy cập</strong>
                    </div>
                    <div className='text-muted-foreground'>
                      Cài đặt gói (apt):{' '}
                      <strong className='text-muted-foreground'>
                        Bị từ chối (Dùng pip user space)
                      </strong>
                    </div>
                    <div className='text-muted-foreground'>
                      Thư mục cá nhân:{' '}
                      <strong className='text-emerald-600'>
                        Đọc/Ghi toàn quyền trong /workspace
                      </strong>
                    </div>
                  </Card>
                </div>
              )}

              {/* TAB 2: WORKSPACE INCUS */}
              {activeTab === 2 && (
                <div className='grid gap-3 py-2 text-xs'>
                  <Card className='p-3.5 space-y-2'>
                    <div className='font-bold text-sm'>
                      Container Incus: {detailMember.workspaceName}
                    </div>
                    <div className='grid grid-cols-2 gap-2 text-[11px] text-muted-foreground'>
                      <div>
                        Máy chủ lưu trữ:{' '}
                        <strong className='text-foreground'>
                          {detailMember.assignedServer === 'track'
                            ? 'Server 01 — Track (Hà Nội)'
                            : 'Server 02 — Workstation (TP.HCM)'}
                        </strong>
                      </div>
                      <div>
                        Loại Instance:{' '}
                        <strong className='text-foreground'>Linux Container (LXC/Incus)</strong>
                      </div>
                      <div>
                        Cgroups CPU:{' '}
                        <strong className='text-foreground font-mono'>
                          {detailMember.cpuLimitCores} Cores
                        </strong>
                      </div>
                      <div>
                        Cgroups RAM:{' '}
                        <strong className='text-foreground font-mono'>
                          {detailMember.ramLimitGb} GB
                        </strong>
                      </div>
                      <div>
                        Namespace Isolation:{' '}
                        <strong className='text-emerald-600'>User, PID, Mount, Net isolated</strong>
                      </div>
                      <div>
                        Thời gian hoạt động (Uptime):{' '}
                        <strong className='text-foreground'>Liên tục (Daemon mode)</strong>
                      </div>
                    </div>
                  </Card>
                </div>
              )}

              {/* TAB 3: LƯU TRỮ ZFS */}
              {activeTab === 3 && (
                <div className='grid gap-3 py-2 text-xs'>
                  <Card className='p-3.5 space-y-2.5'>
                    <div className='font-bold text-sm'>Phân vùng ZFS Custom Volume</div>
                    <div className='grid grid-cols-2 gap-2 text-[11px] text-muted-foreground'>
                      <div>
                        Storage Pool:{' '}
                        <strong className='text-foreground'>{detailMember.storagePoolLabel}</strong>
                      </div>
                      <div>
                        Tên Volume:{' '}
                        <strong className='text-foreground font-mono'>
                          data-{detailMember.username}
                        </strong>
                      </div>
                      <div>
                        Thư mục gắn Container:{' '}
                        <strong className='text-primary font-mono'>
                          {detailMember.mountPath || '/workspace'}
                        </strong>
                      </div>
                      <div>
                        Quyền truy cập:{' '}
                        <strong className='text-emerald-600'>Read / Write (Cá nhân)</strong>
                      </div>
                      <div>
                        Hạn ngạch Quota:{' '}
                        <strong className='text-foreground font-mono'>
                          {detailMember.storageQuotaGb} GB ZFS
                        </strong>
                      </div>
                      <div>
                        Dung lượng đã dùng:{' '}
                        <strong className='text-foreground font-mono'>
                          {detailMember.storageUsedGb || 0} GB
                        </strong>
                      </div>
                      <div>
                        Chính sách Snapshot:{' '}
                        <strong className='text-foreground'>
                          {detailMember.backupPolicy || 'DAILY_SNAPSHOT'}
                        </strong>
                      </div>
                      <div>
                        Datasets dùng chung:{' '}
                        <strong className='text-foreground'>
                          {(detailMember.sharedDatasets || []).join(', ') || 'Không'}
                        </strong>
                      </div>
                    </div>

                    <div className='pt-2 border-t'>
                      <div className='flex items-center justify-between text-[11px] font-medium'>
                        <span>Tiến độ sử dụng dung lượng</span>
                        <span>
                          {detailMember.storageUsedGb || 0} GB / {detailMember.storageQuotaGb} GB
                        </span>
                      </div>
                      <div className='w-full bg-muted rounded-full h-2 mt-1 overflow-hidden'>
                        <div
                          className='bg-primary h-2 rounded-full'
                          style={{
                            width: `${Math.min(100, Math.round(((detailMember.storageUsedGb || 0) / (detailMember.storageQuotaGb || 250)) * 100))}%`
                          }}
                        />
                      </div>
                    </div>
                  </Card>
                </div>
              )}

              {/* TAB 4: TRUY CẬP SSH */}
              {activeTab === 4 && (
                <div className='grid gap-3 py-2 text-xs'>
                  <Card className='p-3.5 space-y-2.5'>
                    <div className='font-bold text-sm flex items-center justify-between'>
                      <span>Thông số Kết nối Dedicated SSH</span>
                      <Badge variant='outline' className='font-mono text-[10px]'>
                        Port {detailMember.sshPort}
                      </Badge>
                    </div>

                    <div className='grid grid-cols-2 gap-2 text-[11px] text-muted-foreground'>
                      <div>
                        VPN IP:{' '}
                        <strong className='text-foreground font-mono'>{detailMember.sshIp}</strong>
                      </div>
                      <div>
                        Listen Port (Host):{' '}
                        <strong className='text-foreground font-mono'>
                          {detailMember.sshPort}
                        </strong>
                      </div>
                      <div>
                        Container SSH Port:{' '}
                        <strong className='text-foreground font-mono'>22</strong>
                      </div>
                      <div>
                        Trạng thái nạp khóa:{' '}
                        <strong className='text-foreground'>{detailMember.sshKeyStatus}</strong>
                      </div>
                      <div className='col-span-2'>
                        Fingerprint:{' '}
                        <code className='text-[10px] text-foreground font-mono'>
                          {detailMember.sshFingerprint || 'Chưa đăng ký'}
                        </code>
                      </div>
                    </div>

                    <div className='space-y-1.5 pt-2 border-t'>
                      <Label className='text-xs font-semibold'>Lệnh kết nối SSH trực tiếp:</Label>
                      <div className='flex items-center justify-between p-2 rounded bg-muted/40 border font-mono text-[11px]'>
                        <code>
                          ssh -i ~/.ssh/id_ed25519 -p {detailMember.sshPort} {detailMember.username}
                          @{detailMember.sshIp}
                        </code>
                        <Button
                          size='sm'
                          variant='ghost'
                          className='h-6 px-2 text-[10px]'
                          onClick={() => {
                            navigator.clipboard.writeText(
                              `ssh -i ~/.ssh/id_ed25519 -p ${detailMember.sshPort} ${detailMember.username}@{detailMember.sshIp}`
                            );
                            toast.success('Đã sao chép lệnh SSH');
                          }}
                        >
                          <Icons.forms className='size-3 mr-1' /> Copy
                        </Button>
                      </div>
                    </div>

                    <div className='space-y-1.5'>
                      <Label className='text-xs font-semibold'>
                        Cấu hình ~/.ssh/config mẫu (VS Code Remote SSH):
                      </Label>
                      <pre className='p-2.5 rounded bg-muted/50 border font-mono text-[10px] overflow-x-auto text-foreground'>
                        {`Host labops-${detailMember.username}
  HostName ${detailMember.sshIp}
  Port ${detailMember.sshPort}
  User ${detailMember.username}
  IdentityFile ~/.ssh/id_ed25519`}
                      </pre>
                    </div>
                  </Card>
                </div>
              )}

              {/* TAB 5: CPU/RAM/GPU */}
              {activeTab === 5 && (
                <div className='grid gap-3 py-2 text-xs'>
                  <Card className='p-3.5 space-y-2'>
                    <div className='font-bold text-sm'>Phân bổ GPU & VRAM</div>
                    <div className='grid grid-cols-2 gap-2 text-[11px] text-muted-foreground'>
                      <div>
                        GPU được cấp quyền:{' '}
                        <strong className='text-foreground'>
                          {(detailMember.gpuPermissions || []).join(', ') || 'Chỉ CPU'}
                        </strong>
                      </div>
                      <div>
                        Chính sách VRAM:{' '}
                        <strong className='text-foreground'>{detailMember.vramPolicy}</strong>
                      </div>
                      <div>
                        Hạn ngạch VRAM:{' '}
                        <strong className='text-foreground font-mono'>
                          {detailMember.vramQuotaGb ? `${detailMember.vramQuotaGb} GB` : 'Chưa cấp'}
                        </strong>
                      </div>
                      <div>
                        Chế độ cấp phát:{' '}
                        <strong className='text-foreground font-mono'>
                          {detailMember.gpuAllocationMode || 'SHARED'}
                        </strong>
                      </div>
                      <div>
                        Thời gian chạy tối đa:{' '}
                        <strong className='text-foreground'>
                          {detailMember.maxRuntimeHours} giờ
                        </strong>
                      </div>
                      <div>
                        GPU Hours đã tiêu thụ:{' '}
                        <strong className='text-foreground font-mono'>
                          {detailMember.totalGpuHours || 0} giờ
                        </strong>
                      </div>
                    </div>
                  </Card>
                </div>
              )}

              {/* TAB 6: PHIÊN SSH */}
              {activeTab === 6 && (
                <div className='grid gap-3 py-2 text-xs'>
                  <Card className='p-3.5 space-y-2'>
                    <div className='font-bold text-sm'>Phiên kết nối SSH Trực tuyến</div>
                    {detailMember.sshSessionsCount > 0 ? (
                      <div className='space-y-2 text-[11px]'>
                        <div className='flex items-center justify-between p-2 rounded bg-muted/30 border'>
                          <div>
                            <div className='font-bold text-foreground'>Phiên SSH #1 (pts/0)</div>
                            <div className='text-muted-foreground font-mono text-[10px]'>
                              Client VPN: 10.200.0.104 • Đã kết nối 2 giờ 15 phút
                            </div>
                          </div>
                          <Badge
                            variant='outline'
                            className='bg-emerald-500/10 text-emerald-600 text-[10px]'
                          >
                            Active
                          </Badge>
                        </div>
                      </div>
                    ) : (
                      <div className='text-muted-foreground text-center py-4'>
                        Hiện không có phiên SSH nào đang hoạt động
                      </div>
                    )}
                  </Card>
                </div>
              )}

              {/* TAB 7: LỊCH SỬ TÁC VỤ */}
              {activeTab === 7 && (
                <div className='grid gap-3 py-2 text-xs'>
                  <Card className='p-3.5 space-y-2'>
                    <div className='font-bold text-sm'>Tác vụ Tính toán (Jobs)</div>
                    {jobs.filter((j) => j.username === detailMember.username).length > 0 ? (
                      <div className='space-y-2'>
                        {jobs
                          .filter((j) => j.username === detailMember.username)
                          .map((job) => (
                            <div
                              key={job.id}
                              className='p-2.5 rounded border bg-card text-[11px] space-y-1'
                            >
                              <div className='flex items-center justify-between'>
                                <span className='font-bold text-primary font-mono'>{job.id}</span>
                                <Badge variant='outline' className='text-[10px]'>
                                  {job.status}
                                </Badge>
                              </div>
                              <div className='font-mono text-[10px] text-muted-foreground'>
                                {job.script}
                              </div>
                              <div className='text-[10px] text-muted-foreground'>
                                GPU: {job.gpuId} • VRAM: {Math.round(job.vramUsedMb / 1024)}GB •
                                CPU: {job.cpuPercent}%
                              </div>
                            </div>
                          ))}
                      </div>
                    ) : (
                      <div className='text-muted-foreground text-center py-4'>
                        Chưa có tác vụ nào được khởi chạy
                      </div>
                    )}
                  </Card>
                </div>
              )}

              {/* TAB 8: NHẬT KÝ KIỂM TOÁN */}
              {activeTab === 8 && (
                <div className='grid gap-3 py-2 text-xs'>
                  <Card className='p-3.5 space-y-2'>
                    <div className='font-bold text-sm'>Nhật ký Kiểm toán (Audit Logs)</div>
                    <div className='space-y-2 max-h-60 overflow-y-auto'>
                      {auditLogs
                        .filter(
                          (l) =>
                            l.targetId === detailMember.username ||
                            l.details.includes(detailMember.username)
                        )
                        .map((log) => (
                          <div
                            key={log.id}
                            className='p-2 rounded border bg-muted/20 text-[11px] space-y-0.5'
                          >
                            <div className='flex items-center justify-between'>
                              <span className='font-semibold text-foreground'>{log.action}</span>
                              <span className='text-[10px] font-mono text-muted-foreground'>
                                {log.timestamp}
                              </span>
                            </div>
                            <div className='text-muted-foreground'>{log.details}</div>
                            <div className='text-[9px] font-mono text-muted-foreground'>
                              Correlation: {log.correlationId}
                            </div>
                          </div>
                        ))}
                    </div>
                  </Card>
                </div>
              )}

              <DialogFooter className='border-t pt-2'>
                <Button size='sm' onClick={() => setDetailMember(null)} className='text-xs'>
                  Đóng
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* MODAL 1: CHANGE STORAGE QUOTA */}
      <Dialog open={!!quotaModalMember} onOpenChange={(val) => !val && setQuotaModalMember(null)}>
        <DialogContent className='sm:max-w-[420px] text-xs'>
          <DialogHeader>
            <DialogTitle className='text-sm font-bold'>
              Điều chỉnh Hạn ngạch ZFS Storage
            </DialogTitle>
            <DialogDescription className='text-xs'>
              Thay đổi dung lượng quota cho @{quotaModalMember?.username} trên{' '}
              {quotaModalMember?.storagePoolLabel}
            </DialogDescription>
          </DialogHeader>

          <div className='space-y-3 py-2'>
            <div className='space-y-1'>
              <Label className='text-xs font-semibold'>Hạn ngạch mới (GB ZFS)</Label>
              <Input
                type='number'
                min={50}
                max={2000}
                value={newQuotaValue}
                onChange={(e) => setNewQuotaValue(Number(e.target.value))}
                className='h-8 text-xs font-mono'
              />
            </div>

            <div className='flex items-center gap-1.5 flex-wrap'>
              {[50, 100, 250, 500, 1000].map((q) => (
                <Button
                  key={q}
                  type='button'
                  size='sm'
                  variant={newQuotaValue === q ? 'default' : 'outline'}
                  onClick={() => setNewQuotaValue(q)}
                  className='h-7 text-xs px-2.5 font-mono'
                >
                  {q} GB
                </Button>
              ))}
            </div>
          </div>

          <DialogFooter className='border-t pt-2'>
            <Button size='sm' variant='outline' onClick={() => setQuotaModalMember(null)}>
              Hủy
            </Button>
            <Button
              size='sm'
              onClick={() => {
                if (quotaModalMember) {
                  const res = changeStorageQuota(quotaModalMember.id, newQuotaValue);
                  if (res.success) {
                    toast.success(res.message);
                    setQuotaModalMember(null);
                  } else {
                    toast.error(res.message);
                  }
                }
              }}
              className='bg-primary font-semibold'
            >
              Lưu thay đổi
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* MODAL 2: CHANGE SSH PORT */}
      <Dialog open={!!portModalMember} onOpenChange={(val) => !val && setPortModalMember(null)}>
        <DialogContent className='sm:max-w-[420px] text-xs'>
          <DialogHeader>
            <DialogTitle className='text-sm font-bold'>Đổi Cổng SSH Incus Proxy</DialogTitle>
            <DialogDescription className='text-xs'>
              Chỉ định cổng lắng nghe mới (22000 - 29999) cho @{portModalMember?.username}
            </DialogDescription>
          </DialogHeader>

          <div className='space-y-3 py-2'>
            <div className='space-y-1'>
              <Label className='text-xs font-semibold'>Cổng SSH Mới</Label>
              <Input
                type='number'
                min={22000}
                max={29999}
                value={newPortValue}
                onChange={(e) => setNewPortValue(Number(e.target.value))}
                className='h-8 text-xs font-mono'
              />
            </div>
            <p className='text-[11px] text-muted-foreground'>
              Hệ thống sẽ cập nhật Incus proxy device trên host và kiểm tra tính duy nhất.
            </p>
          </div>

          <DialogFooter className='border-t pt-2'>
            <Button size='sm' variant='outline' onClick={() => setPortModalMember(null)}>
              Hủy
            </Button>
            <Button
              size='sm'
              onClick={() => {
                if (portModalMember) {
                  const res = changeSshPort(portModalMember.id, newPortValue);
                  if (res.success) {
                    toast.success(res.message);
                    setPortModalMember(null);
                  } else {
                    toast.error(res.message);
                  }
                }
              }}
              className='bg-primary font-semibold'
            >
              Cập nhật cổng
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* MODAL 3: ROTATE SSH KEY */}
      <Dialog
        open={!!rotateKeyModalMember}
        onOpenChange={(val) => !val && setRotateKeyModalMember(null)}
      >
        <DialogContent className='sm:max-w-[500px] text-xs'>
          <DialogHeader>
            <DialogTitle className='text-sm font-bold'>Cập nhật / Đổi Khóa SSH Key</DialogTitle>
            <DialogDescription className='text-xs'>
              Nạp public key mới cho @{rotateKeyModalMember?.username} vào authorized_keys
            </DialogDescription>
          </DialogHeader>

          <div className='space-y-3 py-2'>
            <div className='space-y-1'>
              <Label className='text-xs font-semibold'>Dán Public Key Mới (Ed25519 hoặc RSA)</Label>
              <textarea
                rows={3}
                value={newPublicKeyInput}
                onChange={(e) => setNewPublicKeyInput(e.target.value)}
                placeholder='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@device'
                className='w-full rounded-md border border-input bg-background p-2 text-xs font-mono'
              />
            </div>
          </div>

          <DialogFooter className='border-t pt-2'>
            <Button size='sm' variant='outline' onClick={() => setRotateKeyModalMember(null)}>
              Hủy
            </Button>
            <Button
              size='sm'
              disabled={!newPublicKeyInput}
              onClick={() => {
                if (rotateKeyModalMember && newPublicKeyInput) {
                  const fp = `SHA256:${btoa(newPublicKeyInput).substring(0, 32)}`;
                  rotateSshKey(rotateKeyModalMember.id, newPublicKeyInput, fp);
                  toast.success(`Đã cập nhật khóa SSH cho @${rotateKeyModalMember.username}`);
                  setRotateKeyModalMember(null);
                }
              }}
              className='bg-primary font-semibold'
            >
              Cập nhật khóa
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* MODAL 4: PERMANENT DELETE CONFIRMATION */}
      <Dialog open={!!deleteModalMember} onOpenChange={(val) => !val && setDeleteModalMember(null)}>
        <DialogContent className='sm:max-w-[450px] text-xs'>
          <DialogHeader>
            <DialogTitle className='text-sm font-bold text-destructive flex items-center gap-1.5'>
              <Icons.warning className='size-4' /> Xác nhận Xóa Vĩnh viễn Thành viên
            </DialogTitle>
            <DialogDescription className='text-xs'>
              Hành động này sẽ xóa hoàn toàn container Incus, giải phóng cổng SSH{' '}
              {deleteModalMember?.sshPort} và hủy tài khoản.
            </DialogDescription>
          </DialogHeader>

          <div className='space-y-3 py-2'>
            <p className='text-muted-foreground text-[11px]'>
              Để xác nhận, vui lòng nhập chính xác tên người dùng:{' '}
              <strong>{deleteModalMember?.username}</strong>
            </p>
            <Input
              value={deleteConfirmText}
              onChange={(e) => setDeleteConfirmText(e.target.value)}
              placeholder={`Nhập ${deleteModalMember?.username} để xác nhận`}
              className='h-8 text-xs font-mono'
            />
          </div>

          <DialogFooter className='border-t pt-2'>
            <Button size='sm' variant='outline' onClick={() => setDeleteModalMember(null)}>
              Hủy bỏ
            </Button>
            <Button
              size='sm'
              variant='destructive'
              disabled={deleteConfirmText !== deleteModalMember?.username}
              onClick={() => {
                if (deleteModalMember && deleteConfirmText === deleteModalMember.username) {
                  deleteMemberPermanently(deleteModalMember.id);
                  toast.error(`Đã xóa vĩnh viễn thành viên @${deleteModalMember.username}`);
                  setDeleteModalMember(null);
                }
              }}
              className='font-semibold'
            >
              Xác nhận Xóa Vĩnh viễn
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Multi-step Provisioning Wizard Dialog */}
      <MemberProvisioningWizard open={wizardOpen} onOpenChange={setWizardOpen} />
    </div>
  );
}
