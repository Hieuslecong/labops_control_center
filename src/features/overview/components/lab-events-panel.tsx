'use client';

import React from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';
import Link from 'next/link';

function getEventBadge(type: string) {
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
    default:
      return (
        <Badge variant='secondary' className='text-[10px]'>
          SỰ KIỆN
        </Badge>
      );
  }
}

export function LabEventsPanel() {
  const events = useLabOpsStore((state) => state.events);

  return (
    <Card className='border bg-card h-full flex flex-col justify-between'>
      <CardHeader className='pb-3 pt-4'>
        <div className='flex items-center justify-between'>
          <div className='flex items-center gap-2'>
            <Icons.calendar className='size-5 text-primary' />
            <div>
              <CardTitle className='text-base font-bold tracking-tight'>
                Lịch Lab & Sự kiện Sắp tới
              </CardTitle>
              <p className='text-xs text-muted-foreground'>
                Lịch báo cáo khoa học, bảo trì cụm máy chủ và hội thảo nghiên cứu
              </p>
            </div>
          </div>
          <Link
            href='/dashboard/calendar'
            className='text-xs font-medium text-primary hover:underline flex items-center gap-1'
          >
            Mở lịch đầy đủ <Icons.arrowRight className='size-3.5' />
          </Link>
        </div>
      </CardHeader>

      <CardContent className='pt-0 pb-4 space-y-2.5 flex-1'>
        {events.slice(0, 4).map((evt) => (
          <div
            key={evt.id}
            className='p-2.5 rounded-lg border bg-muted/20 hover:bg-muted/40 transition-colors flex flex-col gap-1.5'
          >
            <div className='flex items-start justify-between gap-2'>
              <div className='flex items-center gap-2 flex-wrap'>
                {getEventBadge(evt.type)}
                <span className='font-semibold text-xs text-foreground leading-snug'>
                  {evt.title}
                </span>
              </div>
              <span className='text-[11px] font-mono text-muted-foreground shrink-0'>
                {evt.date}
              </span>
            </div>

            <p className='text-[11px] text-muted-foreground line-clamp-2'>{evt.description}</p>

            <div className='flex items-center justify-between text-[10px] text-muted-foreground border-t border-border/40 pt-1.5 mt-0.5'>
              <span className='flex items-center gap-1'>
                <Icons.clock className='size-3' />
                {evt.startTime} - {evt.endTime}
              </span>
              <span>
                {evt.location} •{' '}
                <span className='text-foreground font-medium'>{evt.organizer}</span>
              </span>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
