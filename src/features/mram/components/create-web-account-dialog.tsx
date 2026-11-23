'use client';

import React, { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { labopsCall } from '@/features/labops/api';
import { LecturerOption, MemberCategory } from '../types';
import { toast } from 'sonner';

interface CreateWebAccountDialogProps {
  open: boolean;
  lecturers: LecturerOption[];
  onClose: () => void;
  onCreated: () => void;
}

export function CreateWebAccountDialog({
  open,
  lecturers,
  onClose,
  onCreated
}: CreateWebAccountDialogProps) {
  const [username, setUsername] = useState('');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [memberCode, setMemberCode] = useState('');
  const [departmentClass, setDepartmentClass] = useState('');
  const [category, setCategory] = useState<MemberCategory>('STUDENT');
  const [supervisorId, setSupervisorId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [createdResult, setCreatedResult] = useState<{
    username: string;
    initial_web_password: string;
    member_category: string;
  } | null>(null);

  if (!open) return null;

  const handleSubmit = async (e: React.SyntheticEvent) => {
    e.preventDefault();
    setSubmitting(true);
    const res = await labopsCall<{
      initial_web_password: string;
      profile: { username: string; member_category: string };
    }>('admin/member-web-accounts', {
      method: 'POST',
      body: {
        username: username.trim().toLowerCase(),
        full_name: fullName.trim(),
        email: email.trim(),
        member_code: memberCode.trim(),
        department_class: departmentClass.trim(),
        member_category: category,
        primary_supervisor_id: category === 'STUDENT' && supervisorId ? supervisorId : undefined
      }
    });
    setSubmitting(false);
    if (!res.ok || !res.data) {
      toast.error('Không thể tạo tài khoản web ban đầu', { description: res.error });
      return;
    }
    setCreatedResult({
      username: res.data.profile.username,
      initial_web_password: res.data.initial_web_password,
      member_category: res.data.profile.member_category
    });
    toast.success('Đã tạo tài khoản Web ban đầu (chưa cấp SSH/GPU/Storage)');
    onCreated();
  };

  return (
    <div className='fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4'>
      <Card className='w-full max-w-xl max-h-[90vh] overflow-y-auto'>
        <CardHeader>
          <div className='flex items-center justify-between'>
            <div>
              <CardTitle>Tạo Tài khoản Web Ban đầu (MRAM v1.0)</CardTitle>
              <CardDescription className='text-xs mt-1'>
                Chỉ cấp đăng nhập Web Portal; KHÔNG tự động tạo Linux user, SSH key, WireGuard hay
                GPU.
              </CardDescription>
            </div>
            <Button variant='ghost' size='sm' onClick={onClose}>
              Đóng
            </Button>
          </div>
        </CardHeader>
        <CardContent className='space-y-4'>
          {createdResult ? (
            <div className='space-y-3 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-4 text-sm'>
              <div className='font-semibold text-emerald-700 dark:text-emerald-300'>
                Tạo tài khoản Web thành công — Mật khẩu hiển thị 1 lần
              </div>
              <div className='grid grid-cols-2 gap-2 text-xs font-mono'>
                <div>Username:</div>
                <div className='font-bold'>{createdResult.username}</div>
                <div>Loại thành viên:</div>
                <div>{createdResult.member_category}</div>
                <div>Mật khẩu web ban đầu:</div>
                <div className='font-bold text-rose-600 dark:text-rose-400 select-all'>
                  {createdResult.initial_web_password}
                </div>
              </div>
              <p className='text-xs text-muted-foreground'>
                Thành viên phải đổi mật khẩu web trong lần đăng nhập đầu tiên, sau đó điền đơn xin
                cấp SSH.
              </p>
              <div className='flex justify-end gap-2'>
                <Button
                  size='sm'
                  onClick={() => {
                    void navigator.clipboard.writeText(
                      `Tài khoản LabOps Web:\nUsername: ${createdResult.username}\nMật khẩu tạm: ${createdResult.initial_web_password}`
                    );
                    toast.success('Đã sao chép thông tin đăng nhập web');
                  }}
                >
                  Sao chép
                </Button>
                <Button
                  size='sm'
                  variant='outline'
                  onClick={() => {
                    setCreatedResult(null);
                    setUsername('');
                    setFullName('');
                    setMemberCode('');
                    onClose();
                  }}
                >
                  Hoàn tất
                </Button>
              </div>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className='space-y-3 text-sm'>
              <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
                <div className='space-y-1'>
                  <Label>Loại thành viên *</Label>
                  <select
                    value={category}
                    onChange={(e) => setCategory(e.target.value as MemberCategory)}
                    className='w-full rounded-md border bg-background px-3 py-2 text-sm'
                  >
                    <option value='STUDENT'>Sinh viên (STUDENT)</option>
                    <option value='LECTURER'>Giảng viên (LECTURER)</option>
                  </select>
                </div>
                <div className='space-y-1'>
                  <Label>Username *</Label>
                  <Input
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder='sv_nguyenvana'
                  />
                </div>
                <div className='space-y-1'>
                  <Label>Họ và tên *</Label>
                  <Input
                    required
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder='Nguyễn Văn A'
                  />
                </div>
                <div className='space-y-1'>
                  <Label>{category === 'STUDENT' ? 'Mã sinh viên *' : 'Mã cán bộ / GV *'}</Label>
                  <Input
                    required
                    value={memberCode}
                    onChange={(e) => setMemberCode(e.target.value)}
                    placeholder={category === 'STUDENT' ? '1571020001' : 'GV-AIOT-01'}
                  />
                </div>
                <div className='space-y-1'>
                  <Label>Email</Label>
                  <Input
                    type='email'
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder='nguyenvana@fitdnu.id.vn'
                  />
                </div>
                <div className='space-y-1'>
                  <Label>Khoa / Lớp / Bộ môn</Label>
                  <Input
                    value={departmentClass}
                    onChange={(e) => setDepartmentClass(e.target.value)}
                    placeholder='CNTT K16 - AIoT Lab'
                  />
                </div>
              </div>

              {category === 'STUDENT' && (
                <div className='space-y-1'>
                  <Label>Giảng viên hướng dẫn (có thể gán ngay hoặc SV chọn khi gửi đơn SSH)</Label>
                  <select
                    value={supervisorId}
                    onChange={(e) => setSupervisorId(e.target.value)}
                    className='w-full rounded-md border bg-background px-3 py-2 text-sm'
                  >
                    <option value=''>-- Chưa phân công (SV chọn khi gửi đơn) --</option>
                    {lecturers.map((l) => (
                      <option key={l.member_id} value={l.member_id}>
                        {l.full_name} (@{l.username} — {l.member_code || 'GV'})
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div className='flex items-center justify-between pt-2 border-t'>
                <Badge variant='outline' className='text-xs'>
                  SSH Access State: NONE
                </Badge>
                <div className='flex gap-2'>
                  <Button type='button' variant='outline' onClick={onClose}>
                    Hủy
                  </Button>
                  <Button type='submit' disabled={submitting}>
                    {submitting ? 'Đang tạo...' : 'Tạo Tài khoản Web'}
                  </Button>
                </div>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
