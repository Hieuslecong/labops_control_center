# MÔ HÌNH AN NINH, XÁC THỰC & PHÂN QUYỀN (SECURITY MODEL V2)

## 1. Xác thực Đa lớp & Fail-Closed
1. **Lớp Trình duyệt → Next.js (`src/proxy.ts` & `src/lib/auth-guard.ts`)**:
   - Mọi đường dẫn `/dashboard/*` và `/api/*` đều bắt buộc đi qua Clerk authentication.
   - Đã loại bỏ hoàn toàn hành vi bắt lỗi `try/catch` âm thầm cho qua khi thiếu khóa Clerk; nếu thiếu khóa ngoài chế độ demo thì trả `503` hoặc ném lỗi cấu hình (fail-closed).
2. **Lớp Next.js BFF → FastAPI (`src/app/api/labops/_lib.ts` & `backend/app/core/security.py`)**:
   - BFF kiểm tra `Origin` header đối với mọi phương thức đột biến (`POST`, `PUT`, `PATCH`, `DELETE`) để chống CSRF.
   - BFF lấy `userId`, `username`, và vai trò tổ chức (`org:admin`) từ phiên Clerk phía server, sau đó ký **HMAC-SHA256 Session Token** ngắn hạn (TTL 300 giây) bằng `LABOPS_SESSION_SECRET`.
   - Backend tự kiểm tra chữ ký HMAC và hạn dùng; **không bao giờ tin `username`, `role` hay `X-LabOps-Actor` do client tự truyền**.

---

## 2. Ma trận Phân quyền RBAC & Quyền Sở hữu (Ownership)

| Nhóm chức năng / Endpoint | `ADMIN` | `RESEARCHER` | `NODE_AGENT` |
|---|---|---|---|
| Xem tổng quan, máy chủ, danh sách GPU (`/api/v1/servers`, `/api/v1/gpus`) | Toàn quyền | Được phép (chỉ đọc) | Bị chặn (`403`) |
| Giữ chỗ GPU độc quyền (`POST /api/v1/gpus/allocate`) | Giữ cho bất kỳ thành viên | Chỉ giữ cho chính mình (`p.username`) | Bị chặn (`403`) |
| Dừng tác vụ / giải phóng reservation (`POST /api/v1/jobs/{id}/stop`) | Dừng mọi tác vụ | Chỉ dừng tác vụ do chính mình sở hữu (`403` nếu của người khác) | Bị chặn (`403`) |
| Quản lý thành viên, tạo workspace, đối soát dữ liệu (`/api/v1/v2/workspaces`, `/api/v1/v2/reconcile`) | Toàn quyền | Bị chặn (`403`) | Bị chặn (`403`) |
| Quản trị Docker qua Arcane (`GET /api/v1/v2/containers/summary`) | Toàn quyền | Bị chặn cứng (`403`) | Bị chặn (`403`) |
| Quản lý khóa công khai SSH cá nhân (`/api/v1/v2/ssh-keys`) | Quản lý toàn bộ | Chỉ xem/thêm/thu hồi khóa của chính mình | Bị chặn (`403`) |
| Cổng nghiên cứu viên (`GET /api/v1/v2/researcher/me`) | Xem theo phiên | Chỉ trả dữ liệu của chính mình | Bị chặn (`403`) |
| Gửi heartbeat Node Agent (`POST /api/v1/v2/hosts/{id}/agent/heartbeat`) | Bị chặn (`403`) | Bị chặn (`403`) | Được phép (Machine Token) |

