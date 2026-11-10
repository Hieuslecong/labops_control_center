import { NavGroup } from '@/types';

/**
 * LabOps Control Center V2 - Cấu hình Điều hướng 11 Mục Tiếng Việt (Mục 20 prompt.md)
 * Phân tách rõ khu vực Quản trị Hạ tầng (Admin) và Cổng Nghiên cứu viên (Researcher Portal).
 */
export const navGroups: NavGroup[] = [
  {
    label: 'Điều hành & Hạ tầng LabOps',
    items: [
      {
        title: '1. Tổng quan',
        url: '/dashboard/overview',
        icon: 'dashboard',
        isActive: true,
        shortcut: ['t', 'q'],
        items: []
      },
      {
        title: '2. Hạ tầng và GPU',
        url: '/dashboard/servers',
        icon: 'server',
        isActive: false,
        shortcut: ['h', 't'],
        items: []
      },
      {
        title: '3. Thành viên và môi trường',
        url: '/dashboard/users',
        icon: 'teams',
        isActive: false,
        shortcut: ['t', 'v'],
        items: []
      },
      {
        title: '4. Tác vụ và lịch GPU',
        url: '/dashboard/jobs',
        icon: 'job',
        isActive: false,
        shortcut: ['t', 'c'],
        items: []
      },
      {
        title: '5. Giám sát và cảnh báo',
        url: '/dashboard/monitoring',
        icon: 'gauge',
        isActive: false,
        shortcut: ['g', 's'],
        items: []
      }
    ]
  },
  {
    label: 'Truy cập, Lưu trữ & Bảo mật',
    items: [
      {
        title: '6. Truy cập VPN và SSH',
        url: '/dashboard/vpn-ssh',
        icon: 'key',
        isActive: false,
        shortcut: ['v', 's'],
        items: []
      },
      {
        title: '7. Container và lưu trữ',
        url: '/dashboard/resources',
        icon: 'database',
        isActive: false,
        shortcut: ['c', 'l'],
        items: []
      },
      {
        title: '8. Bảo mật mã nguồn',
        url: '/dashboard/security',
        icon: 'shield',
        isActive: false,
        shortcut: ['b', 'm'],
        items: []
      },
      {
        title: '9. Báo cáo sử dụng',
        url: '/dashboard/usage',
        icon: 'activity',
        isActive: false,
        shortcut: ['b', 'c'],
        items: []
      },
      {
        title: '10. Nhật ký và cài đặt',
        url: '/dashboard/audit-logs',
        icon: 'audit',
        isActive: false,
        shortcut: ['n', 'k'],
        items: [
          {
            title: 'Nhật ký kiểm toán',
            url: '/dashboard/audit-logs',
            icon: 'audit'
          },
          {
            title: 'Lịch Lab & Bảo trì',
            url: '/dashboard/calendar',
            icon: 'calendar'
          },
          {
            title: 'Cài đặt hệ thống',
            url: '/dashboard/settings',
            icon: 'settings'
          }
        ]
      }
    ]
  },
  {
    label: 'Không gian Nghiên cứu viên',
    items: [
      {
        title: '11. Cổng nghiên cứu viên',
        url: '/dashboard/researcher',
        icon: 'terminal',
        isActive: false,
        shortcut: ['n', 'v'],
        items: []
      }
    ]
  }
];
