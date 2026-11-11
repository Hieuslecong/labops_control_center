# TÍCH HỢP WG-EASY V15 VÀO LABOPS CONTROL CENTER — TECHNICAL SPECIFICATION

## 1. Kiến trúc Tầng Dịch vụ (Layered Architecture)

Kiến trúc tích hợp VPN trong LabOps Control Center tuân theo mẫu thiết kế **Provider Pattern (Lớp trừu tượng hóa)**, cho phép hệ thống chuyển đổi linh hoạt hoặc chạy song song giữa `wg-easy v15` và `Kernel wg0` mà không ảnh hưởng tới code nghiệp vụ của frontend hoặc controller.

```
┌────────────────────────────────────────────────────────┐
│             Next.js 16 Frontend UI                     │
│  (MemberManagementView: VPN Overview + Multi-Device)   │
└───────────────────────────┬────────────────────────────┘
                            │ /api/labops/v2/...
┌───────────────────────────▼────────────────────────────┐
│         Next.js BFF Proxy (proxyLabOps)                │
│       (Xác thực Clerk RBAC + HMAC Bearer Token)        │
└───────────────────────────┬────────────────────────────┘
                            │ /api/v1/v2/...
┌───────────────────────────▼────────────────────────────┐
│             FastAPI Backend (Port 8000)                │
│           (LabOpsService & V2 Platform API)            │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                      VPNManager                        │
│    (Điều phối Provider hoạt động & Cơ chế Fallback)    │
└─────────────┬────────────────────────────┬─────────────┘
              │ (Ưu tiên 1)                │ (Dự phòng / Coexistence)
┌─────────────▼───────────────┐ ┌──────────▼───────────────┐
│        WgEasyAdapter        │ │  LegacyWireGuardProvider │
│ (REST API 127.0.0.1:51821)  │ │ (Host kernel interface   │
│  - /api/client              │ │  wg0 via wg-quick)       │
│  - /api/admin/interface     │ └──────────────────────────┘
└─────────────────────────────┘
```

---

## 2. Chi tiết các Module Backend

### 2.1. Lớp Trừu tượng hóa `VPNProvider`
Định nghĩa tại `backend/app/adapters/vpn_provider.py`. Cung cấp hợp đồng giao tiếp chuẩn:
- `is_healthy() -> bool`: Kiểm tra tính khả dụng của provider.
- `get_system_overview() -> Dict[str, Any]`: Lấy thông số hệ thống, số client, handshake, lưu lượng Rx/Tx.
- `list_clients() -> List[Dict[str, Any]]`: Liệt kê tất cả client VPN.
- `create_client(name: str, ...) -> Dict[str, Any]`: Sinh client mới và gán IP.
- `get_client_configuration(client_id: str) -> str`: Lấy nội dung file `.conf`.
- `set_client_enabled(client_id: str, enabled: bool) -> bool`: Bật/Tắt client.
- `delete_client(client_id: str) -> bool`: Xóa client khỏi WireGuard mesh.

### 2.2. Bộ chuyển đổi `WgEasyAdapter`
Định nghĩa tại `backend/app/adapters/wg_easy_adapter.py`.
- Tương tác với REST API của `wg-easy v15` (chạy trên Nuxt/Nitro engine).
- Xác thực qua HTTP Basic Auth (`INIT_USERNAME` và `INIT_PASSWORD`).
- Thiết lập CIDR động qua `POST /api/admin/interface/cidr` với payload `{"ipv4Cidr": "10.88.40.0/24", "ipv6Cidr": ""}`.
- Payload tạo client: `{"name": client_name, "expiresAt": None}`.
- Tự động mapping dữ liệu client của wg-easy thành chuẩn của LabOps:
  - `id`: Client UUID của wg-easy.
  - `address`: IP client trong dải `10.88.40.0/24`.
  - `latestHandshakeAt`: Thời điểm handshake gần nhất.
  - `transferRx` / `transferTx`: Lưu lượng nhận / gửi tính bằng bytes.

### 2.3. Bộ điều phối `VPNManager`
Định nghĩa tại `backend/app/services/vpn_manager.py`.
- Khởi tạo mặc định `WgEasyAdapter`. Nếu container wg-easy chưa sẵn sàng, tự động chuyển đổi an toàn sang `LegacyWireGuardProvider`.
- Ánh xạ giữa `member_id` của LabOps và các thiết bị `client_id` của wg-easy.
- Hỗ trợ đa thiết bị (Multi-Device) cho cùng một thành viên: một thành viên có thể đăng ký nhiều thiết bị (Laptop, Desktop, Tablet) với IP riêng biệt trong subnet `10.88.40.0/24`.

---

## 3. Danh sách REST API Endpoints V2

| Endpoint | Method | Phân quyền | Mô tả chi tiết |
| :--- | :--- | :--- | :--- |
| `/api/v1/v2/vpn/overview` | `GET` | User / Admin | Trả về tổng quan hệ thống VPN (wg-easy health, endpoint public, CIDR, số client online, tổng Rx/Tx). |
| `/api/v1/v2/vpn/devices` | `GET` | Admin | Liệt kê tất cả thiết bị VPN đang tồn tại trên hệ thống. |
| `/api/v1/v2/members/{id}/devices` | `GET` | User / Admin | Lấy danh sách thiết bị VPN của một thành viên cụ thể (Researcher chỉ xem được của chính mình). |
| `/api/v1/v2/members/{id}/devices` | `POST` | Admin | Cấp mới 1 thiết bị VPN 1-chạm (tự sinh key và trả về cấu hình `.conf` đầy đủ). |
| `/api/v1/v2/members/{id}/devices/{device_id}/config` | `GET` | User / Admin | Tải file `.conf` của một thiết bị cụ thể. |
| `/api/v1/v2/members/{id}/devices/{device_id}/toggle` | `POST` | Admin | Tạm dừng (disable) hoặc kích hoạt lại (enable) một thiết bị VPN. |
| `/api/v1/v2/members/{id}/devices/{device_id}` | `DELETE` | Admin | Thu hồi và xóa hoàn toàn thiết bị khỏi hệ thống WireGuard. |
| `/api/v1/v2/ssh-access/{id}/download-zip` | `GET` | User / Admin | Tải gói ZIP hoàn chỉnh gồm: file `.conf` WireGuard, SSH Private Key, file `config`, và script `setup-ssh.sh`. |

---

## 4. Tích hợp Giao diện Người dùng (Frontend Integration)

File giao diện chính: `src/features/users/components/member-management-view.tsx`
1. **Top VPN System Overview Banner:**
   - Hiển thị tình trạng thời gian thực của gateway wg-easy v15.
   - Thống kê thiết bị kết nối và tổng lưu lượng truy cập.
   - Hiển thị thông số WAN Endpoint (`118.70.169.236:2204`) và Subnet (`10.88.40.0/24`).
2. **Multi-Device Section (Cột 2 trong chi tiết thành viên):**
   - Liệt kê trực quan các thiết bị đã đăng ký của thành viên.
   - Nút **"Tải .conf"** tải trực tiếp file cấu hình để import vào app WireGuard.
   - Nút **"Tạm dừng / Kích hoạt"** và **"Thu hồi"** hoạt động tức thì.
   - Form cấp thiết bị 1-chạm: Admin chỉ cần nhập tên thiết bị (ví dụ `Laptop Dell`) và nhấn enter, hệ thống tự động sinh cấu hình và kích hoạt tải về.

