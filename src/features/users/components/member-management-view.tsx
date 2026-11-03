'use client';

import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
import { MemberRole, MemberStatus } from '@/types/labops';
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
  const updateMemberStatus = useLabOpsStore((state) => state.updateMemberStatus);
  const deleteMember = useLabOpsStore((state) => state.deleteMember);

  const [wizardOpen, setWizardOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [serverFilter, setServerFilter] = useState<string>('all');
  const [roleFilter, setRoleFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');

  const filteredMembers = members.filter((m) => {
    const matchesSearch =
      m.fullName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      m.username.toLowerCase().includes(searchTerm.toLowerCase()) ||
      m.email.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesServer = serverFilter === 'all' || m.assignedServer === serverFilter;
    const matchesRole = roleFilter === 'all' || m.role === roleFilter;
    const matchesStatus = statusFilter === 'all' || m.status === statusFilter;
    return matchesSearch && matchesServer && matchesRole && matchesStatus;
  });

  return (
    <div className='flex flex-col gap-4'>
      {/* KPI Stats on Users */}
      <div className='grid grid-cols-2 sm:grid-cols-4 gap-3'>
        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Tổng số thành viên</span>
          <div className='text-xl font-bold mt-0.5'>{members.length} người</div>
          <span className='text-[10px] text-muted-foreground'>Được cấp quyền tài khoản</span>
        </Card>
        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Phiên SSH trực tuyến
          </span>
          <div className='text-xl font-bold mt-0.5 text-emerald-600 dark:text-emerald-400'>
            {members.filter((m) => m.sshStatus === 'ONLINE').length} người
          </div>
          <span className='text-[10px] text-muted-foreground'>
            {members.reduce((a, b) => a + b.sshSessionsCount, 0)} kết nối WireGuard
          </span>
        </Card>
        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Server Track (Hà Nội)
          </span>
          <div className='text-xl font-bold mt-0.5 text-blue-600 dark:text-blue-400'>
            {members.filter((m) => m.assignedServer === 'track').length} workspaces
          </div>
          <span className='text-[10px] text-muted-foreground'>Container Incus hoạt động</span>
        </Card>
        <Card className='p-3 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Server Workstation (TP.HCM)
          </span>
          <div className='text-xl font-bold mt-0.5 text-purple-600 dark:text-purple-400'>
            {members.filter((m) => m.assignedServer === 'workstation').length} workspaces
          </div>
          <span className='text-[10px] text-muted-foreground'>Container Incus hoạt động</span>
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
              placeholder='Tìm theo tên, username, email...'
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
            <option value='track'>Server Track</option>
            <option value='workstation'>Server Workstation</option>
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
            <option value='SUSPENDED'>Tạm khóa</option>
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
                  <th className='py-2.5 px-3'>Kết nối SSH</th>
                  <th className='py-2.5 px-3'>Hạn ngạch Phần cứng</th>
                  <th className='py-2.5 px-3'>Quyền GPU & VRAM</th>
                  <th className='py-2.5 px-3'>Trạng thái</th>
                  <th className='py-2.5 px-3 text-right'>Thao tác</th>
                </tr>
              </thead>
              <tbody className='divide-y divide-border/60'>
                {filteredMembers.map((m) => (
                  <tr key={m.id} className='hover:bg-muted/30 transition-colors'>
                    {/* Thành viên */}
                    <td className='py-3 px-3'>
                      <div className='font-bold text-foreground'>{m.fullName}</div>
                      <div className='text-[11px] text-muted-foreground font-mono'>
                        @{m.username} • {m.email}
                      </div>
                    </td>

                    {/* Vai trò */}
                    <td className='py-3 px-3'>{getRoleBadge(m.role)}</td>

                    {/* Máy chủ & Workspace */}
                    <td className='py-3 px-3'>
                      <Badge variant='outline' className='font-mono text-[10px] uppercase'>
                        {m.assignedServer}
                      </Badge>
                      <div className='text-[11px] font-mono font-medium text-primary mt-0.5'>
                        {m.workspaceName}
                      </div>
                    </td>

                    {/* Kết nối SSH */}
                    <td className='py-3 px-3'>
                      <div className='flex items-center gap-1.5'>
                        <span
                          className={`size-2 rounded-full ${m.sshStatus === 'ONLINE' ? 'bg-emerald-500 animate-pulse' : 'bg-slate-400'}`}
                        ></span>
                        <span className='font-medium'>{m.sshStatus}</span>
                      </div>
                      <div className='text-[10px] text-muted-foreground font-mono mt-0.5'>
                        {m.sshSessionsCount > 0
                          ? `${m.sshSessionsCount} phiên SSH`
                          : 'Không có phiên'}
                      </div>
                    </td>

                    {/* Hạn ngạch */}
                    <td className='py-3 px-3 font-mono text-[11px]'>
                      <div>{m.cpuLimitCores} Cores vCPU</div>
                      <div className='text-muted-foreground'>
                        {m.ramLimitGb}GB RAM • {m.storageQuotaGb}GB ZFS
                      </div>
                    </td>

                    {/* Quyền GPU */}
                    <td className='py-3 px-3'>
                      <div className='text-foreground font-medium text-[11px]'>{m.vramPolicy}</div>
                      <div className='text-[10px] text-muted-foreground font-mono'>
                        Đã dùng: {m.totalGpuHours} GPU-hours
                      </div>
                    </td>

                    {/* Trạng thái */}
                    <td className='py-3 px-3'>{getStatusBadge(m.status)}</td>

                    {/* Thao tác */}
                    <td className='py-3 px-3 text-right'>
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          render={
                            <Button
                              size='sm'
                              variant='ghost'
                              aria-label={`Thao tác cho ${m.fullName}`}
                              className='size-8 p-0'
                            >
                              <Icons.ellipsis className='size-4' />
                              <span className='sr-only'>Thao tác</span>
                            </Button>
                          }
                        />
                        <DropdownMenuContent align='end' className='w-48 text-xs'>
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
                              <Icons.check className='size-3.5 mr-2' /> Khôi phục hoạt động
                            </DropdownMenuItem>
                          )}

                          <DropdownMenuItem
                            onClick={() => {
                              toast.info(`Khóa SSH của @${m.username}: ${m.sshPublicKeyPreview}`);
                            }}
                          >
                            <Icons.key className='size-3.5 mr-2' /> Xem khóa SSH
                          </DropdownMenuItem>

                          <DropdownMenuItem
                            onClick={() => {
                              toast.info(`Workspace ${m.workspaceName} đang lưu trên ZFS pool`);
                            }}
                          >
                            <Icons.workspace className='size-3.5 mr-2' /> Quản lý Workspace
                          </DropdownMenuItem>

                          <DropdownMenuSeparator />

                          <DropdownMenuItem
                            onClick={() => {
                              if (confirm(`Bạn có chắc chắn muốn xóa thành viên ${m.fullName}?`)) {
                                deleteMember(m.id);
                                toast.error(`Đã xóa thành viên @${m.username}`);
                              }
                            }}
                            className='text-rose-600 focus:text-rose-600'
                          >
                            <Icons.trash className='size-3.5 mr-2' /> Xóa tài khoản
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Multi-step Provisioning Wizard Dialog */}
      <MemberProvisioningWizard open={wizardOpen} onOpenChange={setWizardOpen} />
    </div>
  );
}
