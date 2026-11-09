import { NavGroup } from '@/types';

/**
 * LabOps Control Center - Navigation Configuration
 * 9 primary sections in Vietnamese with RBAC and shortcut support.
 */
export const navGroups: NavGroup[] = [
  {
    label: 'Hạ tầng & Điều hành',
    items: [
      {
        title: 'Tổng quan',
        url: '/dashboard/overview',
        icon: 'dashboard',
        isActive: true,
        shortcut: ['t', 'q'],
        items: []
      },
      {
        title: 'Máy chủ',
        url: '/dashboard/servers',
        icon: 'server',
        isActive: false,
        shortcut: ['m', 'c'],
        items: []
      },
      {
        title: 'Thành viên',
        url: '/dashboard/users',
        icon: 'teams',
        isActive: false,
        shortcut: ['t', 'v'],
        items: []
      },
      {
        title: 'Tài nguyên',
        url: '/dashboard/resources',
        icon: 'gpu',
        isActive: false,
        shortcut: ['t', 'n'],
        items: []
      },
      {
        title: 'Công việc',
        url: '/dashboard/jobs',
        icon: 'job',
        isActive: false,
        shortcut: ['c', 'v'],
        items: []
      }
    ]
  },
  {
    label: 'Quản trị & An toàn',
    items: [
      {
        title: 'Bảo mật',
        url: '/dashboard/security',
        icon: 'shield',
        isActive: false,
        shortcut: ['b', 'm'],
        items: []
      },
      {
        title: 'Lịch Lab',
        url: '/dashboard/calendar',
        icon: 'calendar',
        isActive: false,
        shortcut: ['l', 'l'],
        items: []
      },
      {
        title: 'Nhật ký',
        url: '/dashboard/audit-logs',
        icon: 'audit',
        isActive: false,
        shortcut: ['n', 'k'],
        items: []
      },
      {
        title: 'Cài đặt',
        url: '/dashboard/settings',
        icon: 'settings',
        isActive: false,
        shortcut: ['c', 'd'],
        items: []
      }
    ]
  }
];
