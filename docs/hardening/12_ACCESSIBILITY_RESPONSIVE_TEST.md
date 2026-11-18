# 12 — BÁO CÁO KHẢ NĂNG TRUY CẬP & RESPONSIVE (ACCESSIBILITY & RESPONSIVE)

- **Branch**: `hardening/labops-v2`
- **Công cụ kiểm chứng**: `oxlint` (bao gồm luật `jsx-a11y`), `tsc --noEmit`, kiểm thử tự động trong `src/__tests__/milestone3-frontend-hardening.test.ts`.

---

## 1. Ma trận Kiểm tra Accessibility (WCAG & `jsx-a11y`)

| Tiêu chí Accessibility | Tình trạng Trước Audit | Biện pháp Sửa chữa | Kết quả Kiểm chứng |
|---|---|---|---|
| **Kích thước chữ tối thiểu (Typography)** | Tồn tại hàng trăm lớp `text-[8px]`, `text-[9px]`, `text-[10px]`, `text-[11px]` dưới ngưỡng đọc dễ (`< 12px`). | Nâng toàn bộ **28 tệp** thuộc `src/features/`, `src/components/layout/`, `src/components/org-switcher.tsx`, `src/app/` lên tối thiểu `text-xs` (`>= 12px`) mà **không sửa** thư mục upstream `src/components/ui/**`. | Test `enforces WCAG minimum text-xs sizing outside upstream ui primitives`: **0 vi phạm — PASS** |
| **Cấu trúc HTML Tương tác Lồng nhau** | `member-repo-history-panel.tsx` lồng `<Link href='/dashboard/security'><Button>` (vi phạm HTML interactive content nesting). | Chuyển sang Base UI composition chuẩn `<Button nativeButton={false} render={<Link href='/dashboard/security' aria-label='Mở Trung tâm An ninh' />}>`. | `oxlint` `jsx-a11y`: **0 errors** |
| **Gắn Nhãn Điều khiển Form** | Checkbox xóa tệp trong modal xóa thành viên chưa liên kết label chuẩn cho trình đọc màn hình; nút trigger `UserNav` thiếu `aria-label`. | Tách `<input type='checkbox' id='safe-delete-remove-files'>` đi kèm `<label htmlFor='safe-delete-remove-files'>`; bổ sung `aria-label='Menu tài khoản người dùng'` trên `UserNav`. | `oxlint` `jsx-a11y(label-has-associated-control)` & `control-has-associated-label`: **0 errors** |
| **Điều hướng Bàn phím & Skip Link** | Đã có `<a href='#main-content'>Chuyển tới nội dung chính</a>` và phím tắt Command+K (`KBar`). | Cập nhật `adminNavGroups` trong `nav-config.ts` tương thích `KBar` (`useFilteredNavGroups`) và xóa mọi link dead route. | `KBar` + Layout compile sạch trong `next build` (**29/29** routes) |

---

## 2. Ma trận Responsive Breakpoints (Code & Layout Structure)

| Breakpoint / Khung hình | Thành phần kiểm tra trong mã nguồn | Hành vi thiết kế được áp dụng | Ghi chú xác minh |
|---|---|---|---|
| Mobile (`< 640px`, `default`) | `AppSidebar`, `Header`, `MemberManagementView`, `SafeMemberDeleteDialog`, `ResearcherPortalView` | Sidebar dạng drawer (`SidebarProvider`); các grid KPI chuyển `grid-cols-1` / `grid-cols-2`; bảng dữ liệu dùng `overflow-x-auto`; modal giới hạn `max-h-[90vh] overflow-y-auto`. | Xác minh cấu trúc Tailwind responsive (`sm:`, `md:`, `lg:`) qua compile/typecheck; **chưa chụp render browser thật** |
| Tablet (`640px – 1024px`) | `KpiSummary`, `SecurityManagementView`, `ResourcesManagementView` | Grid tự động chuyển `sm:grid-cols-2` / `md:grid-cols-3` / `sm:grid-cols-5`; các nút hành động nhóm `flex-wrap`. | Đọc/rà soát class layout + isolated build PASS |
| Desktop (`>= 1024px`) | `OverviewContent`, `SidebarInset` | Sửa lỗi lồng hai lớp `<PageContainer>` ở `src/app/dashboard/overview/layout.tsx` giúp chiều rộng & padding nội dung nhất quán toàn dashboard. | `overview/layout.tsx` render `<>{children}</>` duy nhất **PASS** |
