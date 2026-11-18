import { NavGroup } from '@/types';

/**
 * LabOps Control Center - Cấu hình Điều hướng Phân quyền (RBAC Navigation Config)
 * - Quản trị viên (ADMIN): 5 nhóm vận hành cốt lõi tinh giản.
 * - Nghiên cứu viên (RESEARCHER): Không gian cá nhân cô lập.
 */

export const adminNavGroups: NavGroup[] = [
  {
    label: 'Tổng quan & Điều hành',
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
    label: 'Hạ tầng & Tài nguyên',
    items: [
      {
        title: 'Máy chủ & GPU',
        url: '/dashboard/servers',
        icon: 'server',
        shortcut: ['h', 't'],
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
      }
    ]
  },
  {
    label: 'Thành viên & Bảo mật',
    items: [
      {
        title: 'Thành viên & Truy cập',
        url: '/dashboard/users',
        icon: 'teams',
        shortcut: ['t', 'v'],
        roles: ['ADMIN'],
        items: []
      },
      {
        title: 'Kiểm duyệt Kho mã',
        url: '/dashboard/security',
        icon: 'shield',
        shortcut: ['k', 'd'],
        roles: ['ADMIN'],
        items: []
      },
      {
        title: 'Giám sát An ninh',
        url: '/dashboard/monitoring',
        icon: 'gauge',
        shortcut: ['g', 's'],
        roles: ['ADMIN'],
        items: []
      }
    ]
  },
  {
    label: 'Trợ lý AI & Báo cáo',
    items: [
      {
        title: 'Trợ lý AI & Zalo',
        url: '/dashboard/ai-assistant',
        icon: 'sparkles',
        shortcut: ['a', 'i'],
        roles: ['ADMIN'],
        items: []
      },
      {
        title: 'Báo cáo Sử dụng',
        url: '/dashboard/usage',
        icon: 'activity',
        shortcut: ['b', 'c'],
        roles: ['ADMIN'],
        items: []
      }
    ]
  },
  {
    label: 'Quản trị Hệ thống',
    items: [
      {
        title: 'Nhật ký Kiểm toán',
        url: '/dashboard/audit-logs',
        icon: 'audit',
        shortcut: ['n', 'k'],
        roles: ['ADMIN'],
        items: []
      },
      {
        title: 'Lịch Lab & Bảo trì',
        url: '/dashboard/calendar',
        icon: 'calendar',
        shortcut: ['l', 'l'],
        roles: ['ADMIN'],
        items: []
      },
      {
        title: 'Cài đặt Hệ thống',
        url: '/dashboard/settings',
        icon: 'settings',
        shortcut: ['c', 'd'],
        roles: ['ADMIN'],
        items: []
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

export const navGroups: NavGroup[] = [...adminNavGroups, ...researcherNavGroups];
