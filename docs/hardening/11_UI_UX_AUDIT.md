# 11 — BÁO CÁO ĐÁNH GIÁ UI/UX (UI/UX AUDIT)

- **Branch**: `hardening/labops-v2` (`c8b360d` + cập nhật `SafeMemberDeleteDialog` tại Milestone 4)
- **Mục tiêu**: Chuẩn hóa trải nghiệm điều hành phòng Lab AI tiếng Việt, loại bỏ hoàn toàn template demo, đồng bộ hợp đồng BFF và phản ánh trạng thái trung thực.

---

## 1. So sánh Trước / Sau Cải tiến UI/UX

| Hạng mục | Trạng thái Trước Audit | Trạng thái Sau Remediation | Tệp thay đổi tiêu biểu |
|---|---|---|---|
| **Điều hướng & Cấu trúc IA** | Lộn xộn, chứa nhiều mục template thừa (`Kanban`, `Chat`, `Product`, `Billing`, `Exclusive`, `Workspaces`). | Tinh giản còn đúng **5 nhóm Admin** rõ ràng (gồm `/dashboard/security`) + Portal **Nghiên cứu viên** cô lập; cập nhật breadcrumbs tiếng Việt. | `src/config/nav-config.ts`, `src/hooks/use-breadcrumbs.tsx` |
| **Dữ liệu giả / Demo Leakage** | Thông báo chứa `Sarah Connor`, `Dashboard Pro`, `Alex`; avatar tới `api.slingacademy.com`; 12 route demo còn hoạt động. | Xóa 100% route/mock template (giảm ~8,595 dòng dư thừa); `useOperationalNotifications` tạo cảnh báo thật từ `servers`, `gpus`, `syncError`. | `src/features/notifications/{utils/store,components/notification-center,components/notifications-page}.tsx` |
| **Xóa Thành viên An toàn** | Chỉ dùng `window.confirm` và gọi `DELETE` trực tiếp 1 bước. | Modal tuần tự **3 giai đoạn** (`SafeMemberDeleteDialog`): Kiểm kê trước xóa → `SUSPEND` → `ARCHIVE` (`DELETE_ELIGIBLE`) → Step-Up password + `confirm_username` + opt-out `removeFiles` mặc định `false`. | `src/features/users/components/safe-member-delete-dialog.tsx`, `member-management-view.tsx` |
| **Luồng Nghiên cứu viên** | Thiếu giao diện đổi mật khẩu tạm lần đầu; `sshHost` fallback cứng Rack `.250`; hướng dẫn CLI dùng `labops clone`. | Bổ sung card **Đổi mật khẩu Cổng Web & Workspace** (tối thiểu 12 ký tự, nhận biết `?changePassword=1`); fallback theo `host_id` (`track` `.250` / `workstation` `.251`); chuẩn hóa `gitaiot clone`. | `src/features/researcher/components/researcher-portal-view.tsx` |
| **Danh tính & Trạng thái Hệ thống** | `useAuthSession` mặc định `ADMIN` trước xác thực; `OrgSwitcher` hiện badge `LIVE` giả cố định; trang Profile ghi chưa nối Clerk (`NOT VERIFIED`); `SignInView` điền sẵn `'admin'`. | `useAuthSession` fail-closed về `RESEARCHER`; `OrgSwitcher` đọc `servers`/`syncError` thật (`ONLINE`, `OFFLINE`, `MẤT ĐỒNG BỘ`); `ProfileViewPage` hiển thị phiên HMAC đã xác thực; `Header` gắn `<UserNav />`. | `src/hooks/use-auth-session.ts`, `src/components/org-switcher.tsx`, `src/features/profile/components/profile-view-page.tsx`, `src/components/layout/{header,user-nav,app-sidebar}.tsx` |
| **Chuẩn lệnh CLI** | Hiển thị lẫn lộn `gitlabops clone` và `labops clone`. | Thống nhất `gitaiot clone` và nhãn `KIỂM DUYỆT CÁCH LY (REPO GUARD)` trên mọi panel. | `security-management-view.tsx`, `security-summary-panel.tsx`, `member-repo-history-panel.tsx` |

---

## 2. Luồng Người dùng Chính (User Flows)

### 2.1 Admin Flow
1. Đăng nhập tại `/auth/sign-in` → `POST /api/auth/login` gắn HttpOnly HMAC cookie `labops_session` (`ADMIN`, max 8h).
2. Vào `/dashboard/overview` kiểm tra tổng quan Workstation (`aiot-ws1`) & Rack (`aiotlab-rack`), GPU `WS-GPU-01`, cảnh báo vận hành tại `NotificationCenter`, và hàng đợi `SecuritySummaryPanel`.
3. Vào `/dashboard/users` → mở `SafeMemberDeleteDialog` để `SUSPEND` → `ARCHIVE` (`DELETE_ELIGIBLE`) → xác thực Step-Up + nhập lại `@username` để xóa an toàn.
4. Vào `/dashboard/security` phê duyệt/từ chối yêu cầu `gitaiot clone`; vào `/dashboard/jobs` để `SIGTERM`/`SIGKILL` đúng `${job.serverId}` (`workstation` hoặc `track`).

### 2.2 Researcher Flow
1. Đăng nhập với mật khẩu tạm → tự động chuyển hướng sang `/dashboard/researcher?changePassword=1`, tự mở tab **Kết nối & Bảo mật** kèm banner cảnh báo.
2. Đổi mật khẩu lần đầu qua `POST /api/labops/v2/ssh-access/first-login-change-password`.
3. Đăng ký OpenSSH public key, sao chép lệnh SSH đúng host IP, theo dõi trạng thái `gitaiot clone` và GPU cá nhân; mọi truy cập vào prefix `/dashboard/{overview,users,security,...}` đều bị `src/proxy.ts` điều hướng về `/dashboard/researcher`.

---

## 3. Hạn chế Kiểm chứng Trực quan (Visual Scope Limitation)
- Tuân thủ chính sách **không làm gián đoạn dịch vụ `:3000` đang chạy** và không khởi chạy browser automation ghi/chụp trực tiếp lên production instance: giao diện đã được kiểm chứng bằng **`NEXT_DIST_DIR=.next-verify bun run build` (29/29 routes)**, `tsc --noEmit`, `oxlint` (`jsx-a11y`), và `bun test` (`milestone3-frontend-hardening.test.ts`), nhưng **chưa thực hiện chụp ảnh/đo visual screenshot qua trình duyệt Chrome ở các breakpoint**. Việc đối chiếu visual screenshot cần thực hiện trên canary instance trước khi cấp phép production.
