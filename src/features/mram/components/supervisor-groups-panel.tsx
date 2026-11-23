'use client';

import React, { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { labopsCall } from '@/features/labops/api';
import { LecturerOption, SupervisorGroup } from '../types';
import { toast } from 'sonner';

interface SupervisorGroupsPanelProps {
  groups: SupervisorGroup[];
  lecturers: LecturerOption[];
  onRefresh: () => void;
}

export function SupervisorGroupsPanel({
  groups,
  lecturers,
  onRefresh
}: SupervisorGroupsPanelProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [assigningStudent, setAssigningStudent] = useState<string | null>(null);
  const [selectedSupervisor, setSelectedSupervisor] = useState<string>('');
  const [busyId, setBusyId] = useState<string | null>(null);

  const toggleGroup = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !(prev[id] ?? true) }));
  };

  const handleAssignSupervisor = async (studentMemberId: string) => {
    if (!selectedSupervisor) {
      toast.error('Vui lòng chọn Giảng viên hướng dẫn');
      return;
    }
    setBusyId(studentMemberId);
    const res = await labopsCall(
      `admin/members/${encodeURIComponent(studentMemberId)}/supervisor`,
      {
        method: 'PUT',
        body: {
          primary_supervisor_id: selectedSupervisor,
          change_reason: 'Admin cập nhật phân công GVHD từ bảng Nhóm theo Giảng viên'
        }
      }
    );
    setBusyId(null);
    if (!res.ok) {
      toast.error('Không thể gán Giảng viên hướng dẫn', { description: res.error });
      return;
    }
    toast.success('Đã cập nhật Giảng viên hướng dẫn');
    setAssigningStudent(null);
    onRefresh();
  };

  return (
    <div className='space-y-3'>
      {groups.length === 0 ? (
        <Card>
          <CardContent className='p-6 text-sm text-muted-foreground text-center'>
            Chưa có dữ liệu nhóm Giảng viên hướng dẫn.
          </CardContent>
        </Card>
      ) : (
        groups.map((grp) => {
          const isOpen = expanded[grp.supervisor_id] ?? true;
          const isUnassigned = grp.supervisor_id === 'UNASSIGNED';
          return (
            <Card key={grp.supervisor_id} className={isUnassigned ? 'border-amber-500/40' : ''}>
              <CardHeader
                className='py-3 cursor-pointer hover:bg-muted/30 transition'
                onClick={() => toggleGroup(grp.supervisor_id)}
              >
                <div className='flex flex-wrap items-center justify-between gap-2'>
                  <div className='space-y-0.5'>
                    <CardTitle className='text-base flex items-center gap-2'>
                      <span>{isOpen ? '▼' : '▶'}</span>
                      <span>{grp.supervisor_name}</span>
                      {grp.supervisor_username && (
                        <Badge variant='outline' className='font-mono text-xs'>
                          @{grp.supervisor_username}
                        </Badge>
                      )}
                      {isUnassigned && (
                        <Badge variant='outline' className='bg-amber-500/10 text-amber-700 text-xs'>
                          Cần phân công GVHD
                        </Badge>
                      )}
                    </CardTitle>
                    <CardDescription className='text-xs'>
                      {grp.department_class || 'Phòng thí nghiệm AIoT'} • Dự án:{' '}
                      {grp.projects.length > 0 ? grp.projects.join(', ') : 'Chưa có dự án'} • Host:{' '}
                      {grp.hosts.length > 0 ? grp.hosts.join(', ') : 'Chưa cấp phát'}
                    </CardDescription>
                  </div>
                  <div className='flex flex-wrap items-center gap-1.5 text-xs'>
                    <Badge variant='secondary'>SV: {grp.total_students}</Badge>
                    {grp.pending_requests > 0 && (
                      <Badge className='bg-amber-600 text-white'>
                        Chờ duyệt: {grp.pending_requests}
                      </Badge>
                    )}
                    <Badge variant='outline' className='bg-emerald-500/10 text-emerald-700'>
                      SSH Active: {grp.active_ssh_count}
                    </Badge>
                    {grp.expiring_soon_count > 0 && (
                      <Badge variant='outline' className='bg-rose-500/10 text-rose-700'>
                        Sắp hết hạn: {grp.expiring_soon_count}
                      </Badge>
                    )}
                    <Badge variant='outline'>
                      Storage: {grp.total_storage_quota_gb} GiB | GPU: {grp.total_gpu_count}
                    </Badge>
                  </div>
                </div>
              </CardHeader>

              {isOpen && (
                <CardContent className='pt-0 border-t'>
                  {grp.students.length === 0 ? (
                    <div className='py-4 text-xs text-muted-foreground'>
                      Chưa có sinh viên nào được phân công cho giảng viên này.
                    </div>
                  ) : (
                    <div className='overflow-x-auto'>
                      <table className='w-full text-xs mt-2'>
                        <thead>
                          <tr className='border-b text-left text-muted-foreground'>
                            <th className='py-2 pr-3'>Sinh viên / Mã</th>
                            <th className='py-2 pr-3'>Dự án</th>
                            <th className='py-2 pr-3'>Web / SSH State</th>
                            <th className='py-2 pr-3'>Tài nguyên</th>
                            <th className='py-2 pr-3'>Hết hạn SSH</th>
                            <th className='py-2 text-right'>Hành động</th>
                          </tr>
                        </thead>
                        <tbody>
                          {grp.students.map((st) => (
                            <tr key={st.member_id} className='border-b last:border-0'>
                              <td className='py-2 pr-3'>
                                <div className='font-medium'>{st.full_name}</div>
                                <div className='font-mono text-muted-foreground'>
                                  @{st.username} {st.member_code ? `• ${st.member_code}` : ''}
                                </div>
                              </td>
                              <td className='py-2 pr-3'>{st.latest_project || '—'}</td>
                              <td
                                className='py-2 pr-3'
                                aria-label={`Trạng thái ${st.web_account_status} SSH ${st.ssh_access_state}`}
                              >
                                <div className='flex flex-wrap gap-1'>
                                  <Badge variant='outline'>{st.web_account_status}</Badge>
                                  <Badge
                                    variant='outline'
                                    className={
                                      st.ssh_access_state === 'VERIFIED_READY'
                                        ? 'bg-emerald-500/10 text-emerald-700'
                                        : st.ssh_access_state === 'EXPIRED'
                                          ? 'bg-rose-500/10 text-rose-700'
                                          : ''
                                    }
                                  >
                                    SSH: {st.ssh_access_state}
                                  </Badge>
                                </div>
                              </td>
                              <td className='py-2 pr-3 font-mono'>
                                {st.assigned_server
                                  ? `${st.assigned_server} (${st.cpu_limit_cores}C/${st.ram_limit_gb}G/${st.storage_quota_gb}GiB)`
                                  : 'Chưa cấp'}
                              </td>
                              <td className='py-2 pr-3 font-mono'>
                                {st.ssh_expires_at
                                  ? new Date(st.ssh_expires_at).toLocaleDateString('vi-VN')
                                  : 'Không giới hạn'}
                              </td>
                              <td className='py-2 text-right'>
                                {assigningStudent === st.member_id ? (
                                  <div className='flex items-center justify-end gap-1'>
                                    <select
                                      aria-label='Chọn Giảng viên hướng dẫn'
                                      value={selectedSupervisor}
                                      onChange={(e) => setSelectedSupervisor(e.target.value)}
                                      className='rounded border bg-background px-2 py-1 text-xs'
                                    >
                                      <option value=''>-- Chọn GVHD --</option>
                                      {lecturers.map((l) => (
                                        <option key={l.member_id} value={l.member_id}>
                                          {l.full_name}
                                        </option>
                                      ))}
                                    </select>
                                    <Button
                                      size='sm'
                                      variant='default'
                                      disabled={busyId === st.member_id}
                                      onClick={() => void handleAssignSupervisor(st.member_id)}
                                    >
                                      Lưu
                                    </Button>
                                    <Button
                                      size='sm'
                                      variant='ghost'
                                      onClick={() => setAssigningStudent(null)}
                                    >
                                      Hủy
                                    </Button>
                                  </div>
                                ) : (
                                  <Button
                                    size='sm'
                                    variant='outline'
                                    onClick={() => {
                                      setAssigningStudent(st.member_id);
                                      setSelectedSupervisor(
                                        grp.supervisor_id === 'UNASSIGNED' ? '' : grp.supervisor_id
                                      );
                                    }}
                                  >
                                    Đổi GVHD
                                  </Button>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </CardContent>
              )}
            </Card>
          );
        })
      )}
    </div>
  );
}
