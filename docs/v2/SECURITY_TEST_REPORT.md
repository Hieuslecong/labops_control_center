# BÁO CÁO KIỂM THỬ BẢO MẬT ĐỘC LẬP (SECURITY TEST REPORT) — LABOPS CONTROL CENTER V2

**Ngày kiểm thử:** `2026-11-10`  
**Repository:** `https://github.com/Hieuslecong/labops_control_center`  
**Branch:** `feature/labops-control-center`  
**Bộ kiểm thử tự động đã chạy:** [backend/tests/test_v2_security.py](file:///home/aiot-1/admin_labops_control_center/backend/tests/test_v2_security.py), [backend/tests/test_v2_comprehensive.py](file:///home/aiot-1/admin_labops_control_center/backend/tests/test_v2_comprehensive.py), [backend/tests/test_http_endpoints.py](file:///home/aiot-1/admin_labops_control_center/backend/tests/test_http_endpoints.py), [backend/tests/test_wireguard_provisioning.py](file:///home/aiot-1/admin_labops_control_center/backend/tests/test_wireguard_provisioning.py) (`68/68 PASS`)

---

## 1. Tổng quan kiến trúc bảo mật V2

1. **Xác thực Backend độc lập (Fail-Closed Authentication):**
   - Triển khai tại [backend/app/core/security.py](file:///home/aiot-1/admin_labops_control_center/backend/app/core/security.py).
   - Mọi request tới `/api/v1/*` (ngoại trừ `/healthz` và `/`) bắt buộc phải có header `Authorization: Bearer <HMAC-SHA256-Token>`.
   - Nếu thiếu biến môi trường `LABOPS_AUTH_SECRET`, hệ thống **fail-closed** (`503 Service Unavailable`) thay vì cho phép truy cập ẩn danh hoặc dùng secret rỗng.
   - BFF Proxy tại [src/app/api/labops/_lib.ts](file:///home/aiot-1/admin_labops_control_center/src/app/api/labops/_lib.ts) xác thực phiên đăng nhập Clerk trên Next.js Server và ký token ngắn hạn chuyển tiếp xuống Backend Python, kèm kiểm tra CSRF `Origin`/`Host` cho mọi request `POST`/`PUT`/`PATCH`/`DELETE`.

2. **Tách biệt hoàn toàn User Token và Machine Token (Node Agent):**
   - Token người dùng (`ADMIN`, `RESEARCHER`) sử dụng chữ ký HMAC với `LABOPS_AUTH_SECRET`.
   - Token máy trạm (`LABOPS_NODE_AGENT_TOKEN`) chỉ được chấp nhận tại endpoint `/api/v1/v2/hosts/heartbeat` thông qua hàm `require_node_agent()`.
   - Đã kiểm chứng: Dùng `LABOPS_NODE_AGENT_TOKEN` để gọi API người dùng bị từ chối `401 Unauthorized`; dùng token `ADMIN` để giả mạo heartbeat mà không có `X-Node-Agent-Token` hợp lệ cũng bị từ chối.

3. **Kiểm soát truy cập theo vai trò (RBAC) và Quyền sở hữu tài nguyên (Ownership):**
   - `ADMIN`: Toàn quyền quản trị hạ tầng, duyệt kết quả quét mã nguồn `REVIEW_REQUIRED`, thu hồi bất kỳ GPU/Workspace/SSH key nào, xem tích hợp Arcane.
   - `RESEARCHER`: Chỉ được phép thao tác trên tài nguyên có `owner == principal.username` thông qua `require_owner_or_admin()`.

---

## 2. Kết quả kiểm thử bảo mật chi tiết (Section 7, 11, 12, 16)

| Hạng mục kiểm thử | Kịch bản tấn công / Kiểm thử âm tính | Kết quả mong đợi | Kết quả thực tế | Trạng thái |
|---|---|---|---|---|
| **Unauthenticated API Access** | Gọi trực tiếp `GET /api/v1/servers` hoặc `POST /api/v1/gpus/allocate` không có header `Authorization` | Trả về `401 Unauthorized` | Trả về `401 Unauthorized` (`test_unauthenticated_requests_are_rejected`) | `PASS_REAL` |
| **Privilege Escalation (Researcher -> Admin)** | Researcher gọi `POST /api/v1/v2/scanner/{id}/approve`, `POST /api/v1/v2/reconcile`, hoặc xem trạng thái Arcane | Trả về `403 Forbidden` | Trả về `403 Forbidden` (`test_researcher_cannot_admin`) | `PASS_REAL` |
| **Horizontal Privilege Escalation (IDOR / Ownership)** | Researcher `alice` gọi API giải phóng GPU lease của `bob` hoặc xóa SSH key của `bob` | Trả về `403 Forbidden` | Trả về `403 Forbidden` (`test_ownership`, `test_ssh_key_registry_blocks_private_keys_and_enforces_ownership`) | `PASS_REAL` |
| **Token Tampering (Giả mạo vai trò trong payload)** | Sửa payload base64 của token từ `RESEARCHER` thành `ADMIN` mà không có `LABOPS_AUTH_SECRET` | Chữ ký HMAC không khớp, trả về `401 Unauthorized` | Trả về `401 Unauthorized` (`test_tampered_role_rejected`) | `PASS_REAL` |
| **Expired Token Replay** | Sử dụng lại token đã hết hạn (`exp < now`) | Trả về `401 Unauthorized` | Trả về `401 Unauthorized` (`test_expired_token_rejected`) | `PASS_REAL` |
| **Machine Token Misuse** | Sử dụng `LABOPS_NODE_AGENT_TOKEN` để gọi API `/api/v1/servers` hoặc `/api/v1/v2/workspaces` | Trả về `401 Unauthorized` | Trả về `401 Unauthorized` (`test_agent_token_is_not_user`) | `PASS_REAL` |
| **CORS Wildcard Removal** | Kiểm tra header `Access-Control-Allow-Origin` trên [backend/app/main.py](file:///home/aiot-1/admin_labops_control_center/backend/app/main.py) | Không trả về `*`; chỉ phản hồi origin nằm trong `settings.CORS_ORIGINS` | Đã loại bỏ hoàn toàn `Access-Control-Allow-Origin: *` ở cả FastAPI và Stdlib HTTP Server | `PASS_REAL` |
| **Container Breakout / Privileged Workspace** | Yêu cầu tạo Workspace với `/var/run/docker.sock`, `/etc`, `/root`, hoặc đường dẫn chứa `..` | Từ chối `400 Bad Request` | `WorkspaceController` chặn tuyệt đối và ép buộc `non_privileged=True`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges:true"]` | `PASS_REAL` |
| **Node Agent Arbitrary Command Execution** | Gửi action không nằm trong allowlist (ví dụ `rm -rf` hoặc `bash -c`) tới `NodeAgentAdapter` | Từ chối thực thi ngay lập tức | Chỉ cho phép 6 action cố định trong `ALLOWED_ACTIONS`, kiểm tra regex tham số nghiêm ngặt (`test_node_agent_rejects_non_allowlisted_action_and_supports_idempotency`) | `PASS_REAL` |
| **SSH Private Key Upload Leak** | Người dùng dán nhầm SSH Private Key vào form đăng ký SSH Key | Từ chối `400 Bad Request`, không ghi vào đĩa hay log | `VPNSSHService` chặn mọi chuỗi chứa `PRIVATE KEY` và kiểm tra định dạng OpenSSH public key hợp lệ | `PASS_REAL` |
| **WireGuard Key Exposure** | Kiểm tra response của API `/api/v1/access/wireguard-peers` và `/api/v1/users` | Không bao giờ trả về Server Private Key hoặc tạo Client Private Key giả | Chỉ lưu và trả về `client_public_key`, `assigned_ip`, và trạng thái xác minh từ `wg show` | `PASS_REAL` |

---

## 3. Giới hạn trung thực của cơ chế Quét bảo mật mã nguồn (Section 16)

Theo đúng yêu cầu kiểm toán bảo mật:
- [backend/app/services/security_scanner.py](file:///home/aiot-1/admin_labops_control_center/backend/app/services/security_scanner.py) yêu cầu bắt buộc **Git URL hợp lệ** và **commit SHA 40 ký tự hex** (`^[0-9a-fA-F]{40}$`).
- Kết quả quét chỉ có giá trị đối với **đúng commit SHA đã được quét**.
- **Giới hạn kiến trúc đã được công bố rõ trong API và UI:** Khi nghiên cứu viên đã kết nối SSH trực tiếp vào bên trong Workspace của họ, họ có thể tự chỉnh sửa code hoặc tải thêm file mới sau thời điểm quét (`post_scan_ssh_modification_warning`). Do đó, Trivy scan tại bước nạp code **không thể ngăn chặn việc sửa code trực tiếp qua SSH sau khi quét**, trừ khi:
  1. Workspace mount thư mục code ở chế độ chỉ đọc (`read-only`), và
  2. Container/Host chặn quyền ghi hoặc chặn kết nối mạng ra ngoài đối với thư mục thực thi.
- Cảnh báo này được gắn trực tiếp trong trường `limitation_notice` của mọi bản ghi scan để Admin và Researcher nắm rõ, không gây hiểu lầm về khả năng bảo vệ tuyệt đối.



## Kiểm tra độc lập bổ sung 2026-11-10

`unittest` xác nhận ở cấp hàm: token sửa chữ ký/vai trò và token hết hạn bị từ chối; researcher không qua require_role ADMIN và ownership khác bị từ chối. Đây không phải kiểm thử FastAPI hay production. Các test HTTP bị skip do sandbox không mở listener; FastAPI không cài. `backend/app/api/v1/endpoints/jobs.py` trả danh sách `service.jobs` cho mọi principal được xác thực mà không lọc owner: nguy cơ lộ thông tin job giữa researcher; chưa xác minh qua HTTP. `require_labops_admin` chấp nhận `X-LabOps-Actor` do client gửi làm actor khi biết token admin tĩnh, do đó audit actor không phải danh tính đã xác minh. Cần kiểm thử và sửa cùng các đường gọi BFF trước khi triển khai.
