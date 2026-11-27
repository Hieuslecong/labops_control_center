'use client';

import { usePathname } from 'next/navigation';
import { useMemo } from 'react';

type BreadcrumbItem = {
  title: string;
  link: string;
};

// Bản đồ tiêu đề đường dẫn cho LabOps Control Center
const segmentLabels: Record<string, string> = {
  dashboard: 'Bảng điều khiển',
  overview: 'Tổng quan',
  servers: 'Máy chủ & GPU',
  users: 'Thành viên & Truy cập',
  resources: 'Workspace & Lưu trữ',
  jobs: 'Tác vụ & Tiến trình',
  security: 'Kiểm duyệt Kho mã',
  monitoring: 'Giám sát & Cảnh báo',
  'ai-assistant': 'Trợ lý AI & Zalo',
  usage: 'Báo cáo Sử dụng',
  'audit-logs': 'Nhật ký Kiểm toán',
  calendar: 'Lịch Lab & Bảo trì',
  settings: 'Cài đặt Hệ thống',
  notifications: 'Thông báo vận hành',
  profile: 'Hồ sơ phiên đăng nhập',
  'ssh-requests': 'Duyệt Cấp phát SSH',
  'ssh-request': 'Đăng ký Quyền SSH',
  requests: 'Đơn của tôi',
  access: 'Bàn giao Quyền SSH',
  review: 'Xem xét & Phê duyệt',
  'vpn-ssh': 'VPN & SSH',
  researcher: 'Không gian cá nhân',
  connect: 'Kết nối & Bảo mật',
  workspace: 'Workspace của tôi'
};

export function useBreadcrumbs() {
  const pathname = usePathname();

  const breadcrumbs = useMemo(() => {
    const segments = pathname.split('/').filter(Boolean);
    return segments.map((segment, index): BreadcrumbItem => {
      const path = `/${segments.slice(0, index + 1).join('/')}`;
      const isIdentifier = /^(sshreq-|req-|ws-|gpu-)/i.test(segment);
      const fallbackTitle = isIdentifier
        ? segment
        : segment.charAt(0).toUpperCase() + segment.slice(1);
      return {
        title: segmentLabels[segment] || fallbackTitle,
        link: path
      };
    });
  }, [pathname]);

  return breadcrumbs;
}
