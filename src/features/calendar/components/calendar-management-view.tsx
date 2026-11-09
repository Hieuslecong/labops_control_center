'use client';

import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
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
import { useLabOpsStore } from '@/features/labops/store';
import { EventType, ServerId } from '@/types/labops';
import { toast } from 'sonner';

function getEventBadge(type: EventType) {
  switch (type) {
    case 'SEMINAR':
      return (
        <Badge
          variant='outline'
          className='bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30 text-[10px]'
        >
          SEMINAR
        </Badge>
      );
    case 'MAINTENANCE':
      return (
        <Badge
          variant='outline'
          className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[10px]'
        >
          BẢO TRÌ
        </Badge>
      );
    case 'LAB_MEETING':
      return (
        <Badge
          variant='outline'
          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px]'
        >
          HỌP LAB
        </Badge>
      );
    case 'WORKSHOP':
      return (
        <Badge
          variant='outline'
          className='bg-purple-500/10 text-purple-700 dark:text-purple-400 border-purple-500/30 text-[10px]'
        >
          WORKSHOP
        </Badge>
      );
    case 'PRESENTATION':
    default:
      return (
        <Badge
          variant='outline'
          className='bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/30 text-[10px]'
        >
          BÁO CÁO
        </Badge>
      );
  }
}

export function CalendarManagementView() {
  const events = useLabOpsStore((state) => state.events);
  const addEvent = useLabOpsStore((state) => state.addEvent);
  const updateEventStatus = useLabOpsStore((state) => state.updateEventStatus);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [filterType, setFilterType] = useState<string>('all');

  // Form state
  const [title, setTitle] = useState('');
  const [type, setType] = useState<EventType>('SEMINAR');
  const [date, setDate] = useState('2026-11-15');
  const [startTime, setStartTime] = useState('14:00');
  const [endTime, setEndTime] = useState('16:00');
  const [location, setLocation] = useState('Phòng Hội thảo A2');
  const [organizer, setOrganizer] = useState('Lê Chí Hiếu');
  const [relatedServer, setRelatedServer] = useState<ServerId | 'all'>('track');
  const [description, setDescription] = useState('');

  const filteredEvents = events.filter((e) => filterType === 'all' || e.type === filterType);

  const handleCreateEvent = (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (!title) return;

    addEvent({
      title,
      type,
      date,
      startTime,
      endTime,
      location,
      organizer,
      relatedServer,
      status: 'SCHEDULED',
      description
    });

    setDialogOpen(false);
    toast.success(`Đã thêm sự kiện: ${title}`, {
      description: `Lịch diễn ra vào ${date} (${startTime} - ${endTime})`
    });

    setTitle('');
    setDescription('');
  };

  return (
    <div className='flex flex-col gap-5'>
      {/* KPI Stats */}
      <div className='grid grid-cols-2 sm:grid-cols-4 gap-3'>
        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Tổng số sự kiện</span>
          <div className='text-xl font-bold mt-0.5 text-foreground flex items-center gap-1.5'>
            <Icons.calendar className='size-5 text-primary' />
            {events.length} sự kiện
          </div>
          <span className='text-[10px] text-muted-foreground'>Lên lịch trong tháng 11</span>
        </Card>

        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Bảo trì hệ thống</span>
          <div className='text-xl font-bold mt-0.5 text-amber-600 dark:text-amber-400'>
            {events.filter((e) => e.type === 'MAINTENANCE').length} đợt
          </div>
          <span className='text-[10px] text-muted-foreground'>Server Track & Workstation</span>
        </Card>

        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Seminars & Workshops
          </span>
          <div className='text-xl font-bold mt-0.5 text-blue-600 dark:text-blue-400'>
            {events.filter((e) => e.type === 'SEMINAR' || e.type === 'WORKSHOP').length} buổi
          </div>
          <span className='text-[10px] text-muted-foreground'>Hội thảo chia sẻ kỹ thuật</span>
        </Card>

        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Họp Lab định kỳ</span>
          <div className='text-xl font-bold mt-0.5 text-emerald-600 dark:text-emerald-400'>
            {events.filter((e) => e.type === 'LAB_MEETING').length} phiên
          </div>
          <span className='text-[10px] text-muted-foreground'>Đánh giá tiến độ nghiên cứu</span>
        </Card>
      </div>

      {/* Filter and Add Event Bar */}
      <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-3 rounded-lg border bg-card'>
        <div className='flex items-center gap-2 flex-wrap'>
          <Button
            size='sm'
            variant={filterType === 'all' ? 'default' : 'outline'}
            onClick={() => setFilterType('all')}
            className='h-8 text-xs'
          >
            Tất cả ({events.length})
          </Button>
          <Button
            size='sm'
            variant={filterType === 'SEMINAR' ? 'default' : 'outline'}
            onClick={() => setFilterType('SEMINAR')}
            className='h-8 text-xs'
          >
            Seminars
          </Button>
          <Button
            size='sm'
            variant={filterType === 'MAINTENANCE' ? 'default' : 'outline'}
            onClick={() => setFilterType('MAINTENANCE')}
            className='h-8 text-xs'
          >
            Bảo trì máy chủ
          </Button>
          <Button
            size='sm'
            variant={filterType === 'LAB_MEETING' ? 'default' : 'outline'}
            onClick={() => setFilterType('LAB_MEETING')}
            className='h-8 text-xs'
          >
            Họp Lab
          </Button>
          <Button
            size='sm'
            variant={filterType === 'WORKSHOP' ? 'default' : 'outline'}
            onClick={() => setFilterType('WORKSHOP')}
            className='h-8 text-xs'
          >
            Workshops
          </Button>
        </div>

        <Button
          size='sm'
          onClick={() => setDialogOpen(true)}
          className='h-8 text-xs font-semibold gap-1.5 bg-primary ml-auto'
        >
          <Icons.add className='size-3.5' /> Thêm Sự kiện Mới
        </Button>
      </div>

      {/* Events Grid */}
      <div className='grid grid-cols-1 md:grid-cols-2 gap-4'>
        {filteredEvents.map((evt) => (
          <Card key={evt.id} className='border bg-card flex flex-col justify-between'>
            <CardHeader className='pb-2 pt-4'>
              <div className='flex items-start justify-between gap-2'>
                <div className='space-y-1'>
                  <div className='flex items-center gap-2 flex-wrap'>
                    {getEventBadge(evt.type)}
                    {evt.relatedServer && (
                      <Badge variant='secondary' className='text-[10px] font-mono uppercase'>
                        Máy chủ: {evt.relatedServer}
                      </Badge>
                    )}
                    {evt.status === 'CANCELLED' && (
                      <Badge
                        variant='outline'
                        className='text-rose-600 border-rose-500/30 text-[10px]'
                      >
                        ĐÃ HỦY
                      </Badge>
                    )}
                  </div>
                  <CardTitle className='text-base font-bold text-foreground mt-1'>
                    {evt.title}
                  </CardTitle>
                </div>

                <div className='text-right shrink-0 p-2 rounded-lg bg-muted/40 border border-muted/60'>
                  <span className='block text-xs font-bold font-mono text-primary'>{evt.date}</span>
                  <span className='block text-[10px] text-muted-foreground'>
                    {evt.startTime} - {evt.endTime}
                  </span>
                </div>
              </div>
            </CardHeader>

            <CardContent className='pt-0 pb-4 space-y-3 text-xs flex-1 flex flex-col justify-between'>
              <CardDescription className='text-xs text-muted-foreground leading-relaxed'>
                {evt.description}
              </CardDescription>

              <div className='pt-2 border-t flex items-center justify-between text-[11px] text-muted-foreground'>
                <span>
                  Địa điểm: <strong className='text-foreground'>{evt.location}</strong> • Chủ trì:{' '}
                  <strong className='text-foreground'>{evt.organizer}</strong>
                </span>

                {evt.status !== 'CANCELLED' && (
                  <Button
                    size='sm'
                    variant='ghost'
                    onClick={() => {
                      updateEventStatus(evt.id, 'CANCELLED');
                      toast.warning(`Đã hủy sự kiện: ${evt.title}`);
                    }}
                    className='h-6 text-[10px] text-rose-600 hover:text-rose-700 hover:bg-rose-500/10'
                  >
                    Hủy lịch
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Add Event Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className='sm:max-w-[480px]'>
          <form onSubmit={handleCreateEvent}>
            <DialogHeader>
              <div className='flex items-center gap-2'>
                <div className='flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary'>
                  <Icons.calendar className='size-5' />
                </div>
                <div>
                  <DialogTitle className='text-base font-bold'>Thêm Sự kiện Phòng Lab</DialogTitle>
                  <DialogDescription className='text-xs'>
                    Tạo lịch họp, lịch báo cáo tiến độ hoặc lên kế hoạch bảo trì cụm máy chủ
                  </DialogDescription>
                </div>
              </div>
            </DialogHeader>

            <div className='grid gap-3.5 py-4 text-xs'>
              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>Tiêu đề sự kiện</Label>
                <Input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder='ví dụ: Seminar Tối ưu hóa suy luận LLM'
                  className='h-8 text-xs'
                  required
                />
              </div>

              <div className='grid grid-cols-2 gap-3'>
                <div className='space-y-1.5'>
                  <Label className='text-xs font-semibold'>Loại sự kiện</Label>
                  <select
                    value={type}
                    onChange={(e) => setType(e.target.value as EventType)}
                    className='w-full rounded-md border border-input bg-background px-3 py-1.5 text-xs font-medium'
                  >
                    <option value='SEMINAR'>Seminar (Hội thảo)</option>
                    <option value='MAINTENANCE'>Bảo trì máy chủ</option>
                    <option value='LAB_MEETING'>Họp Lab định kỳ</option>
                    <option value='WORKSHOP'>Workshop thực hành</option>
                    <option value='PRESENTATION'>Báo cáo đề tài</option>
                  </select>
                </div>

                <div className='space-y-1.5'>
                  <Label className='text-xs font-semibold'>Máy chủ liên quan</Label>
                  <select
                    value={relatedServer}
                    onChange={(e) => setRelatedServer(e.target.value as ServerId | 'all')}
                    className='w-full rounded-md border border-input bg-background px-3 py-1.5 text-xs font-medium'
                  >
                    <option value='track'>Server Track (Hà Nội)</option>
                    <option value='workstation'>Server Workstation (TP.HCM)</option>
                    <option value='all'>Toàn bộ phòng Lab</option>
                  </select>
                </div>
              </div>

              <div className='grid grid-cols-3 gap-3'>
                <div className='space-y-1.5'>
                  <Label className='text-xs font-semibold'>Ngày diễn ra</Label>
                  <Input
                    type='date'
                    value={date}
                    onChange={(e) => setDate(e.target.value)}
                    className='h-8 text-xs'
                    required
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label className='text-xs font-semibold'>Bắt đầu</Label>
                  <Input
                    type='time'
                    value={startTime}
                    onChange={(e) => setStartTime(e.target.value)}
                    className='h-8 text-xs'
                    required
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label className='text-xs font-semibold'>Kết thúc</Label>
                  <Input
                    type='time'
                    value={endTime}
                    onChange={(e) => setEndTime(e.target.value)}
                    className='h-8 text-xs'
                    required
                  />
                </div>
              </div>

              <div className='grid grid-cols-2 gap-3'>
                <div className='space-y-1.5'>
                  <Label className='text-xs font-semibold'>Địa điểm</Label>
                  <Input
                    value={location}
                    onChange={(e) => setLocation(e.target.value)}
                    placeholder='Phòng Hội thảo A2 / Google Meet'
                    className='h-8 text-xs'
                    required
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label className='text-xs font-semibold'>Người chủ trì</Label>
                  <Input
                    value={organizer}
                    onChange={(e) => setOrganizer(e.target.value)}
                    placeholder='Lê Chí Hiếu'
                    className='h-8 text-xs'
                    required
                  />
                </div>
              </div>

              <div className='space-y-1.5'>
                <Label className='text-xs font-semibold'>Mô tả chi tiết</Label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder='Nội dung trao đổi, chuẩn bị dữ liệu, lưu ý checkpoint...'
                  rows={3}
                  className='w-full rounded-md border border-input bg-background p-2.5 text-xs'
                />
              </div>
            </div>

            <DialogFooter className='gap-2 sm:gap-0'>
              <Button
                type='button'
                variant='outline'
                size='sm'
                onClick={() => setDialogOpen(false)}
                className='text-xs'
              >
                Hủy
              </Button>
              <Button type='submit' size='sm' className='text-xs font-semibold bg-primary'>
                Lưu sự kiện
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
