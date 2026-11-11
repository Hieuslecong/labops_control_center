# Báo cáo Rà soát An ninh & Phân quyền Server-Side (Authorization Review)

**Dự án:** LabOps Control Center  
**Mục tiêu:** Rà soát kiến trúc bảo mật nhiều lớp (Defense-in-Depth), kiểm chứng các chốt chặn xác thực và phân quyền trên frontend Next.js App Router, BFF Route Handlers và FastAPI backend.

---

## 1. Cơ chế Xác thực Phiên & Danh tính Cryptographic (`/api/labops/session`)

### 1.1. Luồng Xác thực Tập trung
Trước đây, danh tính admin bị hardcode trong `app-sidebar.tsx` và client tự nhận mình là Admin nếu có cookie.
Nay toàn bộ danh tính được cấp phát và kiểm chứng độc quyền bởi endpoint:
`GET /api/labops/session` (Server BFF Route Handler)

1. **Trích xuất Clerk Session Claims từ Server:**
   ```typescript
   const session = await auth();
   if (!session?.userId) {
     return NextResponse.json({ authenticated: false, role: null }, { status: 401 });
   }
   ```
2. **Xác minh Quyền hạn theo Tổ chức (Clerk Organization RBAC):**
   * Nếu user sở hữu `org:admin` hoặc metadata role là `ADMIN` → Role = `ADMIN`.
   * Mọi tài khoản khác mặc định bị giới hạn nghiêm ngặt ở quyền `RESEARCHER`.
3. **Chống giả mạo phía Client:**
   Không tin cậy bất kỳ tham số URL (`?role=admin`) hay `localStorage`. Quyền hiển thị UI và quyền gọi API hoàn toàn do JWT/HMAC token phía server quyết định.

---

## 2. Bảo vệ Tuyến đường Phía Máy chủ (Server Component Route Guards)

### 2.1. File Chốt chặn `src/lib/auth-guard.ts`
* `requireAdmin()`:
  Bảo vệ mọi trang quản trị (`/dashboard/overview`, `/dashboard/servers`, `/dashboard/users`, `/dashboard/resources`, `/dashboard/jobs`, `/dashboard/monitoring`, `/dashboard/security`, `/dashboard/audit-logs`, `/dashboard/calendar`, `/dashboard/settings`).
  * Nếu phiên là `RESEARCHER`: Tự động chuyển hướng (Redirect 307) về `/dashboard/researcher`.
  * Nếu chưa đăng nhập: Chuyển hướng về `/auth/sign-in`.
* `requireResearcherOrAdmin()`:
  Bảo vệ toàn bộ không gian Cổng nghiên cứu viên (`/dashboard/researcher/**`).
* `redirect('/dashboard/users')`:
  Tuyến đường trùng lặp `/dashboard/vpn-ssh` được chuyển hướng cứng sang `/dashboard/users` ngay tại cấp độ server.

---

## 3. Chốt chặn Phân quyền tại FastAPI Backend & Go Node Agent

### 3.1. Phân quyền Endpoint Backend
* Toàn bộ các API nhạy cảm (`/api/v1/processes/*/stop`, `/api/v1/processes/*/kill`, `/api/v1/v2/ssh-access/*`, `/api/v1/v2/members/*`) đều được bảo vệ bởi dependency `p: Principal = Depends(require_admin)`.
* Khi tài khoản có role `RESEARCHER` cố tình gọi các API trên, hệ thống ngay lập tức trả về mã lỗi HTTP `403 Forbidden`.

### 3.2. Cơ chế Bảo vệ Tiến trình Cốt lõi & Chống PID Reuse
* **Cơ chế Chống PID Reuse (Start Time Ticks Check):**
  Mỗi Process Reference được gắn chặt với thời điểm khởi tạo `/proc/{pid}/stat` (ticks). Nếu PID bị OS tái cấp phát cho tiến trình khác, thao tác dừng/kill bị từ chối với lỗi `409 Conflict`.
* **Cơ chế Bảo vệ Tiến trình Hệ thống (CheckSafeGuard):**
  * PID <= 1 (systemd/init) và Kernel threads (PPID == 2) tuyệt đối không thể bị dừng hoặc buộc dừng (từ chối ngay với mã `403`).
  * Các dịch vụ lõi như `labops-agent`, `uvicorn`, `next-server`, `dockerd`, `sshd` (port 1911) nằm trong danh sách bất khả xâm phạm.
* **Audit Logging Bắt buộc:**
  Mọi thao tác can thiệp tiến trình, cấp phát khóa SSH hay thay đổi VPN đều tự động ghi nhật ký vào `audit_logs.json` kèm actor, target ref, kết quả và lý do.

