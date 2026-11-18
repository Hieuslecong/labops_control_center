'use client';

import { useMemo, useState } from 'react';
import { create } from 'zustand';
import type { NotificationStatus, NotificationAction } from '@/components/ui/notification-card';
import { useLabOpsStore } from '@/features/labops/store';

export type Notification = {
  id: string;
  title: string;
  body: string;
  status: NotificationStatus;
  createdAt: string;
  actions?: NotificationAction[];
};

type NotificationState = {
  notifications: Notification[];
  readIds: Record<string, boolean>;
  markAsRead: (id: string) => void;
  markAllAsRead: (ids?: string[]) => void;
  removeNotification: (id: string) => void;
  addNotification: (notification: Omit<Notification, 'status'>) => void;
  unreadCount: () => number;
};

export const useNotificationStore = create<NotificationState>()((set, get) => ({
  notifications: [],
  readIds: {},

  markAsRead: (id) =>
    set((state) => ({
      readIds: { ...state.readIds, [id]: true },
      notifications: state.notifications.map((n) =>
        n.id === id ? { ...n, status: 'read' as const } : n
      )
    })),

  markAllAsRead: (ids) =>
    set((state) => {
      const nextReadIds = { ...state.readIds };
      state.notifications.forEach((n) => {
        nextReadIds[n.id] = true;
      });
      ids?.forEach((id) => {
        nextReadIds[id] = true;
      });
      return {
        readIds: nextReadIds,
        notifications: state.notifications.map((n) => ({
          ...n,
          status: 'read' as const
        }))
      };
    }),

  removeNotification: (id) =>
    set((state) => ({
      notifications: state.notifications.filter((n) => n.id !== id)
    })),

  addNotification: (notification) =>
    set((state) => ({
      notifications: [{ ...notification, status: 'unread' as const }, ...state.notifications]
    })),

  unreadCount: () => get().notifications.filter((n) => n.status === 'unread').length
}));

export function useOperationalNotifications() {
  const servers = useLabOpsStore((state) => state.servers);
  const gpus = useLabOpsStore((state) => state.gpus);
  const syncError = useLabOpsStore((state) => state.syncError);
  const {
    notifications: customNotifications,
    readIds,
    markAsRead,
    markAllAsRead
  } = useNotificationStore();
  const [fallbackIso] = useState(() => new Date().toISOString());

  const notifications = useMemo(() => {
    const operational: Notification[] = [];

    if (syncError) {
      operational.push({
        id: 'op-sync-error',
        title: 'Mất đồng bộ dữ liệu LabOps',
        body: syncError,
        status: readIds['op-sync-error'] ? 'read' : 'unread',
        createdAt: fallbackIso,
        actions: [
          {
            id: 'overview',
            label: 'Xem tổng quan',
            type: 'redirect',
            style: 'danger'
          }
        ]
      });
    }

    servers.forEach((server) => {
      if (server.status !== 'online') {
        const id = `op-server-${server.id}`;
        operational.push({
          id,
          title: `Máy chủ ${server.name} (${server.hostname}) ${server.status}`,
          body: `Trạng thái kết nối hiện tại: ${server.status}. IP nội bộ: ${server.ipAddress}.`,
          status: readIds[id] ? 'read' : 'unread',
          createdAt: server.collectedAt || fallbackIso,
          actions: [
            {
              id: 'servers',
              label: 'Kiểm tra máy chủ',
              type: 'redirect',
              style: 'primary'
            }
          ]
        });
      }
    });

    gpus.forEach((gpu) => {
      const vramPercent =
        gpu.vramTotalMb > 0 ? Math.round((gpu.vramUsedMb / gpu.vramTotalMb) * 100) : 0;
      if (gpu.tempCelsius >= 80 || vramPercent >= 90) {
        const id = `op-gpu-${gpu.id}`;
        operational.push({
          id,
          title: `Cảnh báo tải cao GPU ${gpu.displayLabel}`,
          body: `Nhiệt độ ${gpu.tempCelsius}°C • VRAM ${vramPercent}% trên ${gpu.serverName}.`,
          status: readIds[id] ? 'read' : 'unread',
          createdAt: gpu.collectedAt || fallbackIso,
          actions: [
            {
              id: 'jobs',
              label: 'Xem tác vụ GPU',
              type: 'redirect',
              style: 'primary'
            }
          ]
        });
      }
    });

    const normalizedCustom = customNotifications.map((item) => ({
      ...item,
      status: (readIds[item.id] ? 'read' : item.status) as NotificationStatus
    }));

    return [...operational, ...normalizedCustom];
  }, [servers, gpus, syncError, customNotifications, readIds, fallbackIso]);

  const unreadCount = useMemo(
    () => notifications.filter((n) => n.status === 'unread').length,
    [notifications]
  );

  return {
    notifications,
    unreadCount,
    markAsRead,
    markAllAsRead: () => markAllAsRead(notifications.map((n) => n.id))
  };
}
