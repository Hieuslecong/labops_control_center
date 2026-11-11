# Nhật ký Thay đổi Hoàn tất (Final Changelog)

**Dự án:** LabOps Control Center  
**Phiên bản:** 2.2.0 (UI Cleanup, Role-Based Navigation & Duplicate Removal)  
**Branch:** `feature/labops-control-center`

---

## 1. Tóm tắt Thay đổi Chính (Highlights)

### 1.1. Phân quyền Tuyệt đối & Lọc Menu Server-Side (RBAC)
* **Xây dựng endpoint session:** Thêm `src/app/api/labops/session/route.ts` xác thực quyền `ADMIN` hoặc `RESEARCHER` từ Clerk Session Claims và Organization Roles.
* **Xây dựng hook phiên:** Tạo `src/hooks/use-auth-session.ts` đồng bộ phiên làm việc, hỗ trợ Admin Role Preview Switcher.
* **Sửa lỗi lọc menu client:** Viết lại hoàn toàn `src/hooks/use-nav.ts` để lọc menu theo vai trò thực tế. Không còn trả về nguyên mảng như trước đây.
* **Bảo vệ toàn bộ trang máy chủ:** Cập nhật các Server Components bằng `requireAdmin()` và `requireResearcherOrAdmin()` trong `src/lib/auth-guard.ts`. Nghiên cứu viên gõ URL Admin sẽ bị tự động chuyển hướng về Cổng nghiên cứu viên.

### 1.2. Loại bỏ Menu Trùng lặp & Tinh giản Sidebar Admin
* **Gỡ bỏ `/dashboard/vpn-ssh`:** Chuyển hướng server 307 sang `/dashboard/users`.
* **Hợp nhất "Thành viên & Truy cập":** Tái cấu trúc component `MemberManagementView` với 5 tabs trực quan:
  1. *Tổng quan* (Thông tin định danh, hạn ngạch, thư mục `/media/LabOps-Data/<username>`, Xóa tài khoản).
  2. *Truy cập VPN & SSH* (Cấp khóa 1-chạm, Tải ZIP, Quản lý đa thiết bị WireGuard).
  3. *Workspace* (Container status, vCPU/RAM/PIDs, Cổng SSH).
  4. *GPU & Tiến trình* (Chế độ FULL_GPU_EXCLUSIVE, liên kết jobs).
  5. *Nhật ký* (Audit log, handshake VPN gần nhất).
* **Sidebar Admin chuẩn hóa 7 nhóm:** Loại bỏ toàn bộ tiền tố số thứ tự (`1.`, `2.`), sử dụng danh pháp enterprise.

### 1.3. Đơn giản hóa & Nâng cấp Cổng Nghiên cứu viên (Researcher Portal)
* **Cấu trúc 4 tabs & Sub-routes độc lập:**
  * `/dashboard/researcher` (Tab: Tổng quan của tôi)
  * `/dashboard/researcher/connect` (Tab: Kết nối & Bảo mật)
  * `/dashboard/researcher/workspace` (Tab: Workspace của tôi)
  * `/dashboard/researcher/jobs` (Tab: GPU & Tác vụ của tôi)
* **Quy trình kết nối chuẩn 5 bước:**
  * Xóa bỏ IP hardcode `10.200.0.2`.
  * Sinh lệnh SSH Terminal và cấu hình VSCode Remote SSH động theo cổng và IP thật.
  * Hiển thị cảnh báo trạng thái nếu workspace chưa chạy.

### 1.4. Khắc phục Dữ liệu Giả & Sai lệch Trạng thái
* **Sửa lỗi Header:** Loại bỏ nhãn cố định `Workstation Online • Track Mất kết nối`; tính toán chính xác trạng thái thực của từng host dựa trên telemetry từ Go Node Agent.
* **Sửa lỗi User giả:** Thay thế `admin@labops.local` trong `app-sidebar.tsx` bằng dữ liệu thực tế từ phiên đăng nhập.
* **Go Node Agent Process Control:** Bổ sung chốt chặn PID <= 1 trong `controller.go`, biên dịch lại Go binary, đảm bảo 100% test case process control vượt qua.

---

## 2. Danh sách Tệp Tin Thay đổi (Modified & Created Files)

### Tệp Tin Mới Tạo:
* `src/app/api/labops/session/route.ts`
* `src/hooks/use-auth-session.ts`
* `src/app/dashboard/researcher/connect/page.tsx`
* `src/app/dashboard/researcher/workspace/page.tsx`
* `src/app/dashboard/researcher/jobs/page.tsx`
* `docs/ui-refactor/01_CURRENT_UI_AUDIT.md`
* `docs/ui-refactor/02_DUPLICATE_FEATURE_MATRIX.md`
* `docs/ui-refactor/03_ROLE_PERMISSION_MATRIX.md`
* `docs/ui-refactor/04_NEW_NAVIGATION_STRUCTURE.md`
* `docs/ui-refactor/05_AUTHORIZATION_REVIEW.md`
* `docs/ui-refactor/06_RESEARCHER_PORTAL_REDESIGN.md`
* `docs/ui-refactor/07_REGRESSION_TEST_REPORT.md`
* `docs/ui-refactor/08_FINAL_CHANGELOG.md`

### Tệp Tin Cập nhật & Tái cấu trúc:
* `src/lib/auth-guard.ts`
* `src/types/index.ts`
* `src/config/nav-config.ts`
* `src/hooks/use-nav.ts`
* `src/components/layout/app-sidebar.tsx`
* `src/components/layout/header.tsx`
* `src/app/dashboard/page.tsx`
* `src/app/dashboard/overview/page.tsx`
* `src/app/dashboard/jobs/page.tsx`
* `src/app/dashboard/usage/page.tsx`
* `src/app/dashboard/calendar/page.tsx`
* `src/app/dashboard/users/page.tsx`
* `src/app/dashboard/servers/page.tsx`
* `src/app/dashboard/resources/page.tsx`
* `src/app/dashboard/monitoring/page.tsx`
* `src/app/dashboard/security/page.tsx`
* `src/app/dashboard/audit-logs/page.tsx`
* `src/app/dashboard/settings/page.tsx`
* `src/app/dashboard/vpn-ssh/page.tsx`
* `src/app/dashboard/researcher/page.tsx`
* `src/features/researcher/components/researcher-portal-view.tsx`
* `src/features/users/components/member-management-view.tsx`
* `go/internal/process/controller.go`
* `backend/tests/test_admin_ssh_lifecycle.py`

