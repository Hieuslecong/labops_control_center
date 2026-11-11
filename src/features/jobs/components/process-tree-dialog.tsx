/* oxlint-disable react/set-state-in-effect */
'use client';

import React, { useEffect, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Icons } from '@/components/icons';
import { ComputeJob } from '@/types/labops';

interface ProcessTreeDialogProps {
  job: ComputeJob | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface ProcessTreeNode {
  ref: string;
  pid: number;
  ppid: number;
  pgid: number;
  user: string;
  name: string;
  command_line: string;
  classification: string;
  cpu_percent: number;
  memory_rss_mb: number;
  vram_mb?: number;
  is_gpu?: boolean;
  state: string;
  children?: ProcessTreeNode[];
}

function RenderTreeNode({ node, level = 0 }: { node: ProcessTreeNode; level?: number }) {
  const isGpu = Boolean(node.is_gpu || (node.vram_mb && node.vram_mb > 0));

  return (
    <div className='flex flex-col gap-1.5'>
      <div
        className={`flex items-center justify-between p-2 rounded-lg border text-xs font-mono transition-colors ${
          level === 0
            ? 'bg-primary/5 border-primary/30 font-bold'
            : 'bg-zinc-50 dark:bg-zinc-900/60 border-zinc-200 dark:border-zinc-800'
        }`}
        style={{ marginLeft: `${level * 20}px` }}
      >
        <div className='flex items-center gap-2 truncate max-w-sm'>
          {level > 0 && <span className='text-muted-foreground'>└─</span>}
          <Badge variant='outline' className='text-[10px] py-0 px-1.5'>
            PID: {node.pid}
          </Badge>
          <span className='font-bold text-foreground'>{node.name}</span>
          <span className='text-[10px] text-muted-foreground truncate' title={node.command_line}>
            {node.command_line}
          </span>
        </div>

        <div className='flex items-center gap-2 shrink-0 text-[10px]'>
          <span className='text-muted-foreground'>@{node.user}</span>
          <span className='text-blue-600 font-semibold'>{node.cpu_percent}% CPU</span>
          <span className='text-zinc-600 dark:text-zinc-400'>{node.memory_rss_mb} MB RAM</span>
          {isGpu && (
            <Badge variant='default' className='bg-indigo-600 text-white text-[9px] py-0 px-1'>
              GPU: {node.vram_mb} MB
            </Badge>
          )}
        </div>
      </div>

      {node.children && node.children.length > 0 && (
        <div className='flex flex-col gap-1.5'>
          {node.children.map((child) => (
            <RenderTreeNode key={child.pid} node={child} level={level + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

export function ProcessTreeDialog({ job, open, onOpenChange }: ProcessTreeDialogProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [treeData, setTreeData] = useState<ProcessTreeNode | null>(null);

  const processRef = job?.ref || `track:${job?.workspace || 'host'}:${job?.pid}:0`;

  useEffect(() => {
    if (!open || !job) {
      setTreeData(null);
      setError(null);
      return;
    }

    setLoading(true);
    setError(null);

    fetch(`/api/labops/processes/${encodeURIComponent(processRef)}/tree`)
      .then(async (res) => {
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || `Mã lỗi HTTP ${res.status}`);
        }
        return res.json();
      })
      .then((data) => {
        setTreeData(data);
      })
      .catch((err: unknown) => {
        setError((err as Error)?.message || 'Không thể tải cây tiến trình');
      })
      .finally(() => {
        setLoading(false);
      });
  }, [open, job, processRef]);

  if (!job) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-2xl bg-white dark:bg-zinc-950 border text-foreground'>
        <DialogHeader>
          <div className='flex items-center gap-2 mb-1'>
            <div className='p-2 rounded-full bg-primary/10 text-primary'>
              <Icons.terminal className='size-5' />
            </div>
            <div>
              <DialogTitle className='text-base font-bold'>
                Cây Tiến trình Phân cấp (Process Tree)
              </DialogTitle>
              <DialogDescription className='text-xs text-muted-foreground'>
                Quan hệ cha-con (PPID/PID) và các tiến trình worker con của tác vụ
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className='py-3 max-h-[420px] overflow-y-auto space-y-2 text-xs'>
          {loading && (
            <div className='flex flex-col items-center justify-center py-12 gap-2 text-muted-foreground'>
              <Icons.spinner className='size-6 animate-spin text-primary' />
              <span>Đang quét cây quan hệ tiến trình từ Go Agent...</span>
            </div>
          )}

          {error && (
            <div className='p-4 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-800 dark:text-rose-200 text-xs'>
              <strong>Không thể truy xuất:</strong> {error}
            </div>
          )}

          {!loading && !error && treeData && (
            <div className='space-y-2'>
              <RenderTreeNode node={treeData} />
            </div>
          )}
        </div>

        <DialogFooter className='pt-2'>
          <Button
            type='button'
            variant='outline'
            size='sm'
            onClick={() => onOpenChange(false)}
            className='h-8 text-xs'
          >
            Đóng
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
