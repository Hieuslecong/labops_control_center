# Ma trận Đối chiếu & Phương án Hợp nhất Chức năng Trùng lặp (Duplicate Feature Matrix)

**Dự án:** LabOps Control Center  
**Mục tiêu:** Loại bỏ hoàn toàn sự trùng lặp trên thanh điều hướng, tinh gọn trải nghiệm Quản trị viên và Nghiên cứu viên.

---

## 1. Bảng Phân tích Hiện trạng Trùng lặp

| Tuyến đường (URL) | Component hiển thị | Nhóm chức năng | Tình trạng trùng lặp | Phương án giải quyết |
| :--- | :--- | :--- | :--- | :--- |
| `/dashboard/users` | `<MemberManagementView />` | Quản lý thành viên | Trùng 100% với `/dashboard/vpn-ssh` | **Giữ lại làm trung tâm duy nhất:** Đổi tên thành "Thành viên & Truy cập". |
| `/dashboard/vpn-ssh` | `<MemberManagementView />` | Truy cập VPN & Khóa SSH | Trùng 100% với `/dashboard/users` | **Chuyển hướng (Redirect):** Xóa khỏi Sidebar; đặt `redirect('/dashboard/users')` phía server với mã 307. |
| Chi tiết Member cũ | 2 cột không phân tab (Section 1 SSH, Section 2 VPN) | Chi tiết tài khoản | Giao diện kéo dài, khó tìm kiếm, lẫn lộn giữa SSH, VPN, Workspace và Xóa user | **Tái cấu trúc 5 Tabs:**<br>1. Tổng quan<br>2. Truy cập VPN & SSH<br>3. Workspace<br>4. GPU & Tiến trình<br>5. Nhật ký |
| `/dashboard/researcher` cũ | 1 màn hình dài gồm cả Key, Workspace, Lịch, Form SSH | Môi trường cá nhân | Không phân chia luồng rõ ràng, hardcode lệnh SSH với IP `10.200.0.2` | **Tái cấu trúc 4 Tabs & Sub-routes:**<br>1. `/dashboard/researcher` (Tổng quan)<br>2. `/dashboard/researcher/connect` (Kết nối 5 bước)<br>3. `/dashboard/researcher/workspace` (Container)<br>4. `/dashboard/researcher/jobs` (GPU & Jobs) |

---

## 2. Chi tiết Cải tiến Màn hình "Thành viên & Truy cập" (`/dashboard/users`)

Toàn bộ quy trình quản trị vòng đời tài khoản được hợp nhất vào một điểm duy nhất với 5 Tabs nội hàm:

### Tab 1: Tổng quan
* Họ tên, Username, Email, Quyền (Role: `RESEARCHER` / `ADMIN`).
* Ngày tạo, trạng thái tài khoản (`ACTIVE`, `PENDING_VERIFICATION`).
* Hạn ngạch vCPU, RAM, Storage Quota.
* Đường dẫn thư mục riêng máy chủ: `/media/LabOps-Data/<username>`.
* Khu vực nguy hiểm: Nút xóa vĩnh viễn tài khoản (dọn dẹp container, VPN peers, thư mục).

### Tab 2: Truy cập VPN & SSH
* **Vòng đời Khóa SSH & Mật khẩu tạm:**
  * Cấp khóa 1-chạm (Không Passphrase) để hỗ trợ thành viên mới kết nối ngay.
  * Cấp khóa có Passphrase (tăng cường bảo mật).
  * Cấp lại mật khẩu tạm lần đầu (hết hạn sau 24h).
  * Tải trọn bộ kết nối (.zip gồm SSH key, script cấu hình, wireguard config).
  * Lấy thông tin bí mật một lần (One-Time Vault giải phóng khỏi RAM).
  * Đóng các phiên SSH đang mở của workspace.
* **Quản lý Đa Thiết Bị WireGuard VPN:**
  * Danh sách thiết bị (MacBook, Linux Workstation, iPhone...).
  * Cấp thiết bị mới 1-chạm với IP nội bộ subnet `10.88.40.0/24`.
  * Tải file `.conf`, Bật/Tắt thiết bị, Thu hồi thiết bị.

### Tab 3: Workspace
* Tên container: `ws-<username>` trên máy chủ `aiotlab-rack`.
* Trạng thái container: `RUNNING`, `STOPPED`, `FAILED`.
* Cổng SSH ánh xạ (Mapped Port).
* Giới hạn cgroups: CPU cores, RAM GiB, PIDs limit.
* Thư mục mount `/media/LabOps-Data/<username>`.
* Chính sách bảo mật: Không gắn `docker.sock`, không quyền `sudo` root trên host.

### Tab 4: GPU & Tiến trình
* Chế độ GPU: `FULL_GPU_EXCLUSIVE`.
* Tiến trình AI / Python đang chạy của thành viên.
* Nút chuyển nhanh sang màn hình Giám sát tiến trình (`/dashboard/jobs`).

### Tab 5: Nhật ký
* Thời điểm handshake VPN gần nhất.
* Lịch sử cấp phát SSH, đổi mật khẩu lần đầu.
* Vân tay khóa công khai (Fingerprint SHA256).
