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
        title: 'Duyệt Cấp phát SSH',
        url: '/dashboard/ssh-requests',
        icon: 'key',
        shortcut: ['d', 's'],
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
        title: 'Giám sát & Cảnh báo',
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
    label: 'Cổng Thành viên',
    items: [
      {
        title: 'Trang chủ của tôi',
        url: '/dashboard/researcher',
        icon: 'dashboard',
        shortcut: ['t', 'q'],
        roles: ['RESEARCHER'],
        items: []
      },
      {
        title: 'Đăng ký cấp SSH',
        url: '/dashboard/researcher/ssh-request',
        icon: 'key',
        shortcut: ['x', 's'],
        roles: ['RESEARCHER'],
        items: []
      },
      {
        title: 'Đơn đã nộp & Trạng thái',
        url: '/dashboard/researcher/requests',
        icon: 'history',
        shortcut: ['d', 'n'],
        roles: ['RESEARCHER'],
        items: []
      },
      {
        title: 'Thông tin Kết nối SSH',
        url: '/dashboard/researcher/access',
        icon: 'shield',
        shortcut: ['b', 'g'],
        roles: ['RESEARCHER'],
        items: []
      }
    ]
  }
];

export const navGroups: NavGroup[] = [...adminNavGroups, ...researcherNavGroups];
