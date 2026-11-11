# BÁO CÁO HIỆN TRẠNG HẠ TẦNG VÀ KHOẢNG TRỐNG CHUYỂN ĐỔI WG-EASY
**Dự án**: LabOps Control Center V2  
**Hệ thống đích**: aiotlab-rack (Debian 13 Trixie, LAN `192.168.167.250/21`)  
**Ngày thực hiện**: 10/11/2026  
**Trạng thái**: Đã hoàn thành tích hợp wg-easy v15 làm Single VPN Source of Truth.

---

## 1. TỔNG QUAN HIỆN TRẠNG HẠ TẦNG

| Thành phần | Cấu hình thực tế trên máy chủ | Trạng thái tích hợp LabOps V2 |
| :--- | :--- | :--- |
| **Máy chủ** | Debian 13 (aiotlab-rack, Linux 6.12.63+deb13-amd64) | Production Ready |
| **Mạng LAN** | `192.168.167.250/21`, Gateway: `192.168.160.1` | Thông suốt, không xung đột IP |
| **SSH Legacy** | Port 1911 (User: `aiot-1`, Host: `118.70.169.236`) | Không thay đổi, bảo toàn quyền Admin |
| **Host WireGuard** | `wg0` trên host, subnet `10.88.30.1/24`, UDP port 2204 | Đã chuyển sang chế độ **READ-ONLY**, giữ kết nối MacBook `10.88.30.2` |
| **Container wg-easy** | Docker `labops-wg-easy`, Image `ghcr.io/wg-easy/wg-easy:15` | Đang chạy **Up (healthy)**, Subnet `10.88.40.0/24` |
| **Port Mapping** | Host `2205:2204/udp`, Web API `127.0.0.1:51821/tcp` | Không xung đột host port 2204 |
| **Public Endpoint** | `118.70.169.236:2205` (hoặc `118.70.169.236:2204` khi cutover) | Đã cấu hình và kiểm chứng trong gói ZIP |

---

## 2. CÁC ĐIỂM NGHẼN ĐÃ ĐƯỢC GIẢI QUYẾT (GAPS RESOLVED)

### 2.1. Xung đột Port UDP 2204 giữa Host Kernel WireGuard và Docker Container
- **Khoảng trống ban đầu**: Cả giao thức WireGuard trên Host (`wg-quick@wg0`) và container `wg-easy` đều muốn chiếm dụng UDP port 2204 trên Debian host, dẫn đến lỗi xung đột socket (`address already in use`) hoặc làm ngắt kết nối MacBook của Admin.
- **Giải pháp xử lý**:
  - Trong giai đoạn dual-run kiểm thử an toàn, `compose.yaml` map host UDP port `2205` vào container UDP port `2204` (`"2205:2204/udp"`).
  - Web API REST của `wg-easy` bind chặt chẽ vào loopback `127.0.0.1:51821/tcp`, hoàn toàn cách ly khỏi mạng ngoài.
  - Sau khi Admin nghiệm thu và chạy lệnh tắt host `wg0`, chỉ cần đổi mapping thành `"2204:2204/udp"` là chuyển giao trọn vẹn port 2204 mà không cần khởi tạo lại cấu hình client.

### 2.2. Xóa bỏ hoàn toàn việc LabOps can thiệp trực tiếp `/etc/wireguard/wg0.conf`
- **Khoảng trống ban đầu**: Module cũ `wireguard_adapter.py` ghi đè tệp cấu hình trên host và gọi `wg set` / `wg-quick strip`.
- **Giải pháp xử lý**:
  - `LegacyWireGuardProvider` được chuyển đổi thành **READ-ONLY**: mọi thao tác ghi (`create_device`, `disable_device`, `enable_device`, `revoke_device`) đều bị chặn và ném ngoại lệ `VPNProviderError`.
  - `VPNManager` trỏ `primary_provider` duy nhất về `WgEasyAdapter`.
  - Mọi thao tác cấp phát, tạm dừng, kích hoạt lại và thu hồi WireGuard peer đều được thực thi tự động qua REST API của container `wg-easy`.

### 2.3. Hỗ trợ đa thiết bị (Multi-Device) cho cùng một thành viên
- **Khoảng trống ban đầu**: Mỗi thành viên chỉ có 1 trường `wireguard` tĩnh, không phân tách nhiều thiết bị cá nhân.
- **Giải pháp xử lý**:
  - Quản lý danh sách `wireguard.devices[]`: Mỗi thiết bị có `device_id`, `name` (VD: "MacBook M3", "iPhone"), IP riêng trên dải `10.88.40.x`, khóa công khai riêng và trạng thái kích hoạt độc lập.
  - Admin có thể cấp thêm thiết bị, tạm dừng từng thiết bị hoặc xóa vĩnh viễn thiết bị mà không ảnh hưởng tới các thiết bị khác của thành viên.

### 2.4. Xuất trọn gói cấu hình ZIP kết nối tự động (One-Click Onboarding)
- **Khoảng trống ban đầu**: Người dùng không tải được Private Key hoặc phải cấu hình thủ công từng bước qua dòng lệnh.
- **Giải pháp xử lý**:
  - Dashboard tích hợp endpoint `/api/labops/v2/ssh-access/{id}/download-zip` xuất tệp `LabOps_Access_<username>.zip`.
  - Tệp ZIP chứa đầy đủ:
    1. `README.md` hướng dẫn chi tiết bằng tiếng Việt.
    2. `wireguard/labops.conf`: Tệp cấu hình WireGuard chính thức được tải trực tiếp từ `wg-easy`.
    3. `ssh/config`: Đoạn cấu hình Host SSH chuẩn trỏ vào IP máy chủ `192.168.167.250` và Port SSH của Workspace.
    4. `ssh/id_ed25519_<username>` & `.pub`: Cặp khóa Ed25519 (giải phóng khỏi RAM ngay sau lần tải duy nhất để bảo đảm an toàn).
    5. `scripts/`: Bộ kịch bản tự động cài đặt `setup-macos.sh`, `setup-linux.sh`, `setup-windows.ps1`.

---

## 3. TIẾN TRÌNH CHUYỂN GIAO TIẾP THEO

1. **Giai đoạn Hiện tại (Dual-Run)**:
   - Host `wg0` tiếp tục duy trì peer `10.88.30.2` (MacBook Admin).
   - `wg-easy` tiếp nhận toàn bộ thành viên mới và thiết bị nghiên cứu trên dải `10.88.40.0/24`.
2. **Kế hoạch Cutover Chính thức (Khi Admin phê duyệt)**:
   - Thêm peer Admin vào `wg-easy` (Client IP: `10.88.40.2`).
   - Tắt dịch vụ host: `sudo systemctl stop wg-quick@wg0 && sudo systemctl disable wg-quick@wg0`.
   - Đổi mapping trong `infrastructure/wg-easy/compose.yaml` thành `"2204:2204/udp"`.
   - Chạy `docker compose up -d` để `wg-easy` tiếp quản hoàn toàn UDP port 2204.

