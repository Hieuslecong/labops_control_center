# Thiết kế Kiến trúc Cổng Nghiên cứu viên Mới (Researcher Portal Redesign)

**Dự án:** LabOps Control Center  
**Component:** `src/features/researcher/components/researcher-portal-view.tsx`  
**Các tuyến đường hỗ trợ:**  
- `/dashboard/researcher` (Tab: Tổng quan của tôi)  
- `/dashboard/researcher/connect` (Tab: Kết nối & Bảo mật)  
- `/dashboard/researcher/workspace` (Tab: Workspace của tôi)  
- `/dashboard/researcher/jobs` (Tab: GPU & Tác vụ của tôi)

---

## 1. Mục tiêu Tái thiết kế
1. Xóa bỏ hoàn toàn lệnh SSH hardcode với địa chỉ IP tĩnh không xác thực (`10.200.0.2`).
2. Tách biệt giao diện thành 4 mục rõ ràng, mạch lạc, dễ sử dụng cho học viên/nghiên cứu viên.
3. Thiết lập quy trình 5 bước kết nối chuẩn mực Zero-Trust từ laptop cá nhân đến container workspace.
4. Cung cấp nút 1-click sao chép lệnh Terminal và cấu hình VSCode Remote SSH.
5. Hiển thị thông số hạn mức tài nguyên (vCPU/RAM/PIDs), thư mục dữ liệu cá nhân bền vững `/media/LabOps-Data/<username>`.
6. Phân tích GPU-Hours minh bạch giữa thời gian giữ chỗ (Allocated), thời gian chạy thực tế (Verified Runtime) và thời gian giữ nhàn rỗi (Idle Hold).

---

## 2. Quy trình 5 Bước Kết nối An toàn (5-Step Connection Flow)

| Bước | Tên bước | Nội dung chi tiết | Giao diện hỗ trợ |
| :---: | :--- | :--- | :--- |
| **1** | **Cấu hình WireGuard VPN (.conf)** | Cài đặt ứng dụng WireGuard client trên máy cá nhân và import cấu hình mạng do Admin cấp. | Hướng dẫn tải client cho Windows/macOS/Linux. |
| **2** | **Kích hoạt kết nối VPN** | Bật công tắc kết nối trên WireGuard client. Kết nối trực tiếp vào subnet nội bộ `10.88.40.0/24` mà không mở cổng ra Internet công cộng. | Hiển thị địa chỉ VPN IP nội bộ của thành viên. |
| **3** | **Đăng ký Khóa công khai OpenSSH** | Tạo cặp khóa Ed25519 (`ssh-keygen -t ed25519`) và đăng ký Public Key (`id_ed25519.pub`). Hệ thống **tuyệt đối không nhận Private Key**. | Form đăng ký có nhãn thiết bị + Danh sách khóa đã đăng ký kèm vân tay SHA-256. |
| **4** | **Kiểm tra trạng thái Workspace** | Kiểm tra container workspace `ws-<username>` có đang ở trạng thái `RUNNING` và đã được mở cổng SSH riêng hay chưa. | Huy hiệu trạng thái: `RUNNING` (Xanh lá) hoặc `PENDING/STOPPED` (Hổ phách). |
| **5** | **Lệnh kết nối SSH cá nhân hóa** | Lệnh kết nối được sinh tự động theo dữ liệu thực tế:<br>`ssh -i ~/.ssh/id_ed25519 -p <PORT_THỰC> <USERNAME>@<HOST_VPN_IP>`<br>Nếu workspace chưa chạy, hiển thị cảnh báo hướng dẫn liên hệ Admin. | Nút 1-click **Sao chép lệnh SSH** & nút **Sao chép cấu hình VSCode Remote SSH**. |

---

## 3. Chi tiết 4 Tabs Chức năng

### Tab 1: Tổng quan của tôi
* 4 Thẻ KPI cô đọng:
  1. **Workspace cá nhân:** Tên container, trạng thái hoạt động, cổng SSH.
  2. **Khóa SSH & VPN:** Số lượng khóa công khai đã lưu, IP WireGuard được gán.
  3. **GPU-Hours tích lũy:** Giờ chạy thực tế đã xác minh, số GPU đang giữ chỗ.
  4. **Tác vụ & An ninh:** Số lượng jobs AI và lượt quét mã nguồn bảo mật.
* Khối điều hướng nhanh (Quick Action Shortcuts) để chuyển đổi sang các tab chi tiết.

### Tab 2: Kết nối & Bảo mật
* Quy trình 5 bước kết nối chi tiết.
* Khu vực sao chép snippet VSCode:
  ```ssh-config
  Host labops-<username>
      HostName <VPN_IP_HOẶC_RACK_IP>
      Port <PORT_THỰC>
      User <username>
      IdentityFile ~/.ssh/id_ed25519
      ServerAliveInterval 60
  ```

### Tab 3: Workspace của tôi
* **Thông số Container:**
  * Host vật lý: `aiotlab-rack` (192.168.167.250).
  * Giới hạn cgroups: Hạn mức vCPU, RAM (GiB), PIDs limit.
  * Cổng SSH ánh xạ vào container.
* **Lưu trữ Bền vững:**
  * Đường dẫn cá nhân: `/media/LabOps-Data/<username>`.
  * Hạn mức dung lượng đĩa (Storage Quota).
  * Chính sách an ninh: Không Docker socket (`DENIED`), Không quyền root máy chủ (`DENIED`).

### Tab 4: GPU & Tác vụ của tôi
* Danh sách lượt giữ chỗ GPU độc quyền (`FULL_GPU_EXCLUSIVE`) theo UUID thực tế.
* Bảng thống kê GPU-Hours:
  * **Allocated Hours:** Tính từ lúc đăng ký đến lúc giải phóng.
  * **Verified Runtime Hours:** Được Go Agent và NVIDIA NVML xác thực có tiến trình tính toán.
  * **Idle Hold Hours:** Chênh lệch thời gian để phát hiện lãng phí GPU.

