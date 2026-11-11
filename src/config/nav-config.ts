import { NavGroup } from '@/types';

/**
 * LabOps Control Center - Cấu hình Điều hướng Phân quyền (RBAC Navigation Config)
 * - Quản trị viên (ADMIN): 7 nhóm tính năng cốt lõi tinh giản.
 * - Nghiên cứu viên (RESEARCHER): 4 nhóm tính năng cá nhân cô lập.
 * - Loại bỏ hoàn toàn đánh số thứ tự cố định ('1.', '2.', '11.') trong title.
 */

export const adminNavGroups: NavGroup[] = [
  {
    label: 'Điều hành & Hạ tầng',
    items: [
      {
        title: 'Tổng quan',
        url: '/dashboard/overview',
        icon: 'dashboard',
        shortcut: ['t', 'q'],
        roles: ['ADMIN'],
        items: []
      },
      {
        title: 'Hạ tầng & GPU',
        url: '/dashboard/servers',
        icon: 'server',
        shortcut: ['h', 't'],
        roles: ['ADMIN'],
        items: []
      },
      {
        title: 'Thành viên & Truy cập',
        url: '/dashboard/users',
        icon: 'teams',
        shortcut: ['t', 'v'],
        roles: ['ADMIN'],
        items: []
      },
      {
        title: 'Workspace & Lưu trữ',
        url: '/dashboard/resources',
        icon: 'database',
        shortcut: ['w', 's'],
        roles: ['ADMIN'],
        items: []
      },
      {
        title: 'Tác vụ & Tiến trình',
        url: '/dashboard/jobs',
        icon: 'job',
        shortcut: ['t', 'c'],
        roles: ['ADMIN'],
        items: []
      }
    ]
  },
  {
    label: 'Bảo mật & Quản trị',
    items: [
      {
        title: 'Giám sát & Bảo mật',
        url: '/dashboard/monitoring',
        icon: 'gauge',
        shortcut: ['g', 's'],
        roles: ['ADMIN'],
        items: []
      },
      {
        title: 'Quản trị hệ thống',
        url: '/dashboard/audit-logs',
        icon: 'settings',
        shortcut: ['q', 't'],
        roles: ['ADMIN'],
        items: [
          {
            title: 'Nhật ký kiểm toán',
            url: '/dashboard/audit-logs',
            icon: 'audit',
            roles: ['ADMIN']
          },
          {
            title: 'Lịch Lab & Bảo trì',
            url: '/dashboard/calendar',
            icon: 'calendar',
            roles: ['ADMIN']
          },
          {
            title: 'Báo cáo sử dụng',
            url: '/dashboard/usage',
            icon: 'activity',
            roles: ['ADMIN']
          },
          {
            title: 'Cài đặt hệ thống',
            url: '/dashboard/settings',
            icon: 'settings',
            roles: ['ADMIN']
          }
        ]
      }
    ]
  }
];

export const researcherNavGroups: NavGroup[] = [
  {
    label: 'Không gian Nghiên cứu viên',
    items: [
      {
        title: 'Tổng quan của tôi',
        url: '/dashboard/researcher',
        icon: 'dashboard',
        shortcut: ['t', 'q'],
        roles: ['RESEARCHER'],
        items: []
      },
      {
        title: 'Kết nối & Bảo mật',
        url: '/dashboard/researcher/connect',
        icon: 'key',
        shortcut: ['k', 'n'],
        roles: ['RESEARCHER'],
        items: []
      },
      {
        title: 'Workspace của tôi',
        url: '/dashboard/researcher/workspace',
        icon: 'database',
        shortcut: ['w', 's'],
        roles: ['RESEARCHER'],
        items: []
      },
      {
        title: 'GPU & Tác vụ của tôi',
        url: '/dashboard/researcher/jobs',
        icon: 'job',
        shortcut: ['g', 'p'],
        roles: ['RESEARCHER'],
        items: []
      }
    ]
  }
];

export const navGroups: NavGroup[] = [
  ...adminNavGroups,
  ...researcherNavGroups
];
