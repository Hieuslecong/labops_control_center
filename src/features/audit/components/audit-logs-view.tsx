'use client';

import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Icons } from '@/components/icons';
import { useLabOpsStore } from '@/features/labops/store';

export function AuditLogsView() {
  const auditLogs = useLabOpsStore((state) => state.auditLogs);

  const [searchTerm, setSearchTerm] = useState('');
  const [resultFilter, setResultFilter] = useState<string>('all');
  const [typeFilter, setTypeFilter] = useState<string>('all');

  const filteredLogs = auditLogs.filter((log) => {
    const matchesSearch =
      log.actor.toLowerCase().includes(searchTerm.toLowerCase()) ||
      log.action.toLowerCase().includes(searchTerm.toLowerCase()) ||
      log.targetId.toLowerCase().includes(searchTerm.toLowerCase()) ||
      log.details.toLowerCase().includes(searchTerm.toLowerCase()) ||
      log.correlationId.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesResult = resultFilter === 'all' || log.result === resultFilter;
    const matchesType = typeFilter === 'all' || log.targetType === typeFilter;
    return matchesSearch && matchesResult && matchesType;
  });

  const handleExportCsv = () => {
    const csvContent =
      'data:text/csv;charset=utf-8,' +
      'ID,Timestamp,Actor,Action,TargetType,TargetId,Result,CorrelationID,Details\n' +
      auditLogs
        .map(
          (l) =>
            `"${l.id}","${l.timestamp}","${l.actor}","${l.action}","${l.targetType}","${l.targetId}","${l.result}","${l.correlationId}","${l.details}"`
        )
        .join('\n');

    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `labops_audit_logs_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className='flex flex-col gap-4'>
      {/* KPI Stats on Audits */}
      <div className='grid grid-cols-2 sm:grid-cols-4 gap-3'>
        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Tổng số thao tác đã ghi
          </span>
          <div className='text-xl font-bold mt-0.5 text-foreground flex items-center gap-1.5'>
            <Icons.audit className='size-5 text-primary' />
            {auditLogs.length} bản ghi
          </div>
          <span className='text-[10px] text-muted-foreground'>
            Nhật ký bất biến (Immutable Audit)
          </span>
        </Card>

        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Thao tác thành công</span>
          <div className='text-xl font-bold mt-0.5 text-emerald-600 dark:text-emerald-400'>
            {auditLogs.filter((l) => l.result === 'SUCCESS').length}
          </div>
          <span className='text-[10px] text-muted-foreground'>Thực thi chuẩn xác</span>
        </Card>

        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>
            Cảnh báo & Can thiệp
          </span>
          <div className='text-xl font-bold mt-0.5 text-amber-600 dark:text-amber-400'>
            {auditLogs.filter((l) => l.result === 'WARNING').length}
          </div>
          <span className='text-[10px] text-muted-foreground'>Cách ly Sandbox & Tạm khóa</span>
        </Card>

        <Card className='p-3.5 bg-card'>
          <span className='text-[11px] text-muted-foreground font-medium'>Thao tác thất bại</span>
          <div className='text-xl font-bold mt-0.5 text-rose-600 dark:text-rose-400'>
            {auditLogs.filter((l) => l.result === 'FAILED').length}
          </div>
          <span className='text-[10px] text-muted-foreground'>Bị từ chối bởi chính sách</span>
        </Card>
      </div>

      {/* Filter and Search Bar */}
      <div className='flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-3 rounded-lg border bg-card'>
        <div className='relative flex-1 max-w-md'>
          <Icons.search className='absolute left-2.5 top-2.5 size-4 text-muted-foreground' />
          <Input
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder='Tìm kiếm thao tác, người dùng, ID đối tượng, Correlation ID...'
            className='pl-8 h-9 text-xs'
          />
        </div>

        <div className='flex items-center gap-2 flex-wrap'>
          <select
            value={resultFilter}
            onChange={(e) => setResultFilter(e.target.value)}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả kết quả</option>
            <option value='SUCCESS'>Thành công (SUCCESS)</option>
            <option value='WARNING'>Cảnh báo (WARNING)</option>
            <option value='FAILED'>Thất bại (FAILED)</option>
          </select>

          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className='h-9 rounded-md border border-input bg-background px-2.5 text-xs font-medium'
          >
            <option value='all'>Tất cả loại đối tượng</option>
            <option value='GPU'>GPU Allocation</option>
            <option value='WORKSPACE'>Incus Workspace</option>
            <option value='MEMBER'>Tài khoản Thành viên</option>
            <option value='SECURITY_SCAN'>An ninh / Quét mã</option>
          </select>

          <Button
            size='sm'
            variant='outline'
            onClick={handleExportCsv}
            className='h-9 text-xs font-semibold gap-1.5'
          >
            <Icons.download className='size-3.5' /> Xuất CSV
          </Button>
        </div>
      </div>

      {/* Table */}
      <Card className='border bg-card'>
        <CardHeader className='pb-2 pt-4'>
          <CardTitle className='text-sm font-bold'>Nhật ký Kiểm toán Toàn hệ thống</CardTitle>
          <CardDescription className='text-xs'>
            Ghi nhận toàn bộ thao tác đặc quyền quản trị, cấp phát tài nguyên, thay đổi hạn ngạch và
            quyết định an ninh
          </CardDescription>
        </CardHeader>
        <CardContent className='p-0'>
          <div className='overflow-x-auto'>
            <table className='w-full text-left text-xs'>
              <thead>
                <tr className='border-b text-muted-foreground font-semibold bg-muted/20'>
                  <th className='py-2.5 px-3'>Thời gian (UTC+7)</th>
                  <th className='py-2.5 px-3'>Người thực hiện</th>
                  <th className='py-2.5 px-3'>Hành động</th>
                  <th className='py-2.5 px-3'>Đối tượng tác động</th>
                  <th className='py-2.5 px-3'>Mã giao dịch (Tx ID)</th>
                  <th className='py-2.5 px-3'>Chi tiết thao tác</th>
                  <th className='py-2.5 px-3 text-right'>Kết quả</th>
                </tr>
              </thead>
              <tbody className='divide-y divide-border/60'>
                {filteredLogs.map((log) => (
                  <tr key={log.id} className='hover:bg-muted/30 transition-colors'>
                    <td className='py-3 px-3 font-mono text-[11px] text-muted-foreground whitespace-nowrap'>
                      {log.timestamp}
                    </td>

                    <td className='py-3 px-3 font-semibold text-foreground whitespace-nowrap'>
                      {log.actor}
                    </td>

                    <td className='py-3 px-3'>
                      <span className='font-medium text-foreground'>{log.action}</span>
                    </td>

                    <td className='py-3 px-3'>
                      <Badge variant='outline' className='font-mono text-[10px]'>
                        {log.targetType}: {log.targetId}
                      </Badge>
                    </td>

                    <td className='py-3 px-3 font-mono text-[10px] text-muted-foreground'>
                      {log.correlationId}
                    </td>

                    <td className='py-3 px-3 max-w-sm'>
                      <p className='text-[11px] text-muted-foreground line-clamp-2'>
                        {log.details}
                      </p>
                    </td>

                    <td className='py-3 px-3 text-right'>
                      {log.result === 'SUCCESS' && (
                        <Badge
                          variant='outline'
                          className='bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px] font-mono'
                        >
                          SUCCESS
                        </Badge>
                      )}
                      {log.result === 'WARNING' && (
                        <Badge
                          variant='outline'
                          className='bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[10px] font-mono'
                        >
                          WARNING
                        </Badge>
                      )}
                      {log.result === 'FAILED' && (
                        <Badge
                          variant='outline'
                          className='bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30 text-[10px] font-mono'
                        >
                          FAILED
                        </Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
