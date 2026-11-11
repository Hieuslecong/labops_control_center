# Ma trận Phân quyền & Vai trò Người dùng (Role & Permission Matrix)

**Dự án:** LabOps Control Center  
**Nguyên tắc thiết kế:** Role-Based Access Control (RBAC) xác thực từ máy chủ (Server-Verified), không tin cậy client-side storage, Fail-Closed Security.

---

## 1. Định nghĩa Vai trò Hệ thống (System Roles)

| Vai trò | Ký hiệu | Đối tượng áp dụng | Nguyên tắc an ninh cốt lõi |
| :--- | :--- | :--- | :--- |
| **Quản trị viên (Admin)** | `ADMIN` | Quản trị viên phòng Lab / Giảng viên / Kỹ sư DevSecOps | Toàn quyền kiểm soát tài nguyên máy chủ, GPU scheduler, cấp phát/thu hồi VPN, hủy job/tiến trình thành viên, xem toàn bộ audit logs. |
| **Nghiên cứu viên (Researcher)** | `RESEARCHER` | Học viên, sinh viên nghiên cứu, kỹ sư AI/ML | **Không gian cô lập hoàn toàn (Zero-Trust Sandbox):** Chỉ xem và quản lý workspace cá nhân, khóa SSH riêng, lượt giữ chỗ GPU cá nhân; không xem được tiến trình của người khác; không quyền root/sudo; không chạm Docker daemon. |

---

## 2. Ma trận Phân quyền Trang Giao diện (Frontend Route Access Matrix)

| Tuyến đường (Route) | Mục đích sử dụng | Quyền truy cập | Cơ chế bảo vệ | Xử lý khi truy cập trái phép |
| :--- | :--- | :--- | :--- | :--- |
| `/dashboard/overview` | Tổng quan toàn bộ cụm máy chủ & GPU | `ADMIN` | `requireAdmin()` | Tự động chuyển hướng về `/dashboard/researcher` |
| `/dashboard/servers` | Quản trị hạ tầng máy chủ & Telemetry | `ADMIN` | `requireAdmin()` | Tự động chuyển hướng về `/dashboard/researcher` |
| `/dashboard/users` | Quản lý thành viên, VPN & khóa SSH | `ADMIN` | `requireAdmin()` | Tự động chuyển hướng về `/dashboard/researcher` |
| `/dashboard/vpn-ssh` | Trang cũ (Đã hợp nhất vào `/users`) | `ADMIN` | Server redirect 307 | Chuyển hướng về `/dashboard/users` |
| `/dashboard/resources` | Quản lý Workspace & Lưu trữ cụm | `ADMIN` | `requireAdmin()` | Tự động chuyển hướng về `/dashboard/researcher` |
| `/dashboard/jobs` | Giám sát tác vụ AI & Kiểm soát tiến trình | `ADMIN` | `requireAdmin()` | Tự động chuyển hướng về `/dashboard/researcher` |
| `/dashboard/monitoring` | Giám sát số liệu CPU/RAM/VRAM chi tiết | `ADMIN` | `requireAdmin()` | Tự động chuyển hướng về `/dashboard/researcher` |
| `/dashboard/security` | Giám sát an ninh, quét mã & CVE | `ADMIN` | `requireAdmin()` | Tự động chuyển hướng về `/dashboard/researcher` |
| `/dashboard/audit-logs` | Nhật ký kiểm toán hệ thống không thể xóa | `ADMIN` | `requireAdmin()` | Tự động chuyển hướng về `/dashboard/researcher` |
| `/dashboard/calendar` | Lịch phòng Lab, bảo trì máy chủ | `ADMIN` | `requireAdmin()` | Tự động chuyển hướng về `/dashboard/researcher` |
| `/dashboard/settings` | Thiết lập thông số điều hành hệ thống | `ADMIN` | `requireAdmin()` | Tự động chuyển hướng về `/dashboard/researcher` |
| `/dashboard/researcher` | Cổng Nghiên cứu viên: Tổng quan của tôi | `RESEARCHER`, `ADMIN` | `requireResearcherOrAdmin()` | Chuyển hướng về trang đăng nhập `/auth/sign-in` |
| `/dashboard/researcher/connect` | Kết nối 5 bước: WireGuard & OpenSSH | `RESEARCHER`, `ADMIN` | `requireResearcherOrAdmin()` | Chuyển hướng về trang đăng nhập `/auth/sign-in` |
| `/dashboard/researcher/workspace` | Workspace cá nhân & Thư mục lưu trữ | `RESEARCHER`, `ADMIN` | `requireResearcherOrAdmin()` | Chuyển hướng về trang đăng nhập `/auth/sign-in` |
| `/dashboard/researcher/jobs` | Lượt giữ chỗ GPU & Tác vụ cá nhân | `RESEARCHER`, `ADMIN` | `requireResearcherOrAdmin()` | Chuyển hướng về trang đăng nhập `/auth/sign-in` |

---

## 3. Ma trận Quyền hạn Thao tác (Action & API Permission Matrix)

| Hành động nghiệp vụ | API Endpoint | `ADMIN` | `RESEARCHER` | Ghi chú an ninh |
| :--- | :--- | :---: | :---: | :--- |
| Xác thực phiên đăng nhập | `GET /api/labops/session` | Cho phép | Cho phép | Trả về `role` được xác minh từ server claims |
| Xem dữ liệu tổng quan cụm | `GET /api/v1/overview` | Cho phép | Chặn (403) | Bảo vệ thông tin hạ tầng toàn lab |
| Lấy dữ liệu Cổng nghiên cứu viên | `GET /api/v1/v2/researcher/me` | Cho phép | Cho phép | Chỉ trả về dữ liệu thuộc `p.username` |
| Đăng ký khóa công khai SSH | `POST /api/v1/v2/ssh-keys` | Cho phép | Cho phép | Chỉ cho đăng ký Public Key (ssh-ed25519) |
| Cấp khóa SSH 1-chạm & Passphrase | `POST /api/v1/v2/ssh-access/rotate-key` | Cho phép | Chặn (403) | Hành vi quản trị nhạy cảm |
| Tải file ZIP kết nối | `GET /api/v1/v2/ssh-access/{id}/download-zip` | Cho phép | Chỉ tải của mình | Yêu cầu khớp username |
| Lấy mật khẩu tạm / Passphrase | `POST /api/v1/v2/ssh-access/{id}/claim-secrets` | Cho phép | Chặn (403) | Kênh riêng bảo mật |
| Tạm dừng / Buộc dừng tiến trình (Kill) | `POST /api/v1/v2/processes/terminate` | Cho phép | Chặn (403) | Bắt buộc kiểm tra quyền Admin, ghi Audit log |
| Tạm dừng / Buộc dừng cây tiến trình | `POST /api/v1/v2/processes/terminate-tree` | Cho phép | Chặn (403) | Bắt buộc kiểm tra quyền Admin, ghi Audit log |
| Xóa vĩnh viễn tài khoản thành viên | `DELETE /api/v1/v2/members/{id}` | Cho phép | Chặn (403) | Xóa container, VPN peers, thư mục |
