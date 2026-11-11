# HƯỚNG DẪN ONBOARDING THÀNH VIÊN — LABOPS CONTROL CENTER

Tài liệu này hướng dẫn chi tiết quy trình Admin cấp quyền và hướng dẫn thành viên mới kết nối vào trạm nghiên cứu LabOps qua WireGuard VPN và SSH.

---

## PHẦN 1: DÀNH CHO QUẢN TRỊ VIÊN (ADMIN WORKFLOW)

### Bước 1: Tạo Thành viên & Cấp Quyền Truy cập
1. Đăng nhập vào **LabOps Control Center** Dashboard.
2. Điều hướng tới menu **Quản lý Thành viên & Quyền Truy cập SSH** (`/dashboard/users`).
3. Bấm nút **"+ Tạo quyền truy cập SSH"** để mở Wizard cấp phát.
4. Nhập thông tin thành viên:
   - Họ và tên, Email, Username (ví dụ: `nguyenvana`).
   - Phân bổ tài nguyên nghiên cứu: CPU Cores (ví dụ: 8), RAM (ví dụ: 32 GB), Storage Quota (ví dụ: 100 GB), GPU (nếu có).
5. Nhấn **Xác nhận Cấp phát**. Hệ thống sẽ tự động:
   - Tạo container Workspace cô lập cho thành viên.
   - Cấp một cổng SSH riêng (ví dụ `22008`).
   - Tự động sinh cặp khóa SSH Ed25519 (`id_ed25519_nguyenvana`).
   - Gọi wg-easy v15 sinh cấu hình WireGuard VPN Mesh cho thiết bị đầu tiên.

### Bước 2: Bàn giao Bộ Kết nối ZIP (1-Chạm)
1. Tại dòng của thành viên `@nguyenvana`, bấm **"Cấp / Quản lý SSH"** để mở rộng chi tiết.
2. Bấm nút **"📦 Tải bộ kết nối (.zip)"**.
3. File ZIP tải về có tên `LabOps_Access_nguyenvana.zip`, bên trong bao gồm:
   - `nguyenvana-labops-wg0.conf`: File cấu hình WireGuard VPN đầy đủ (Private Key, Endpoint `118.70.169.236:2204`, IP `10.88.40.x`).
   - `id_ed25519_nguyenvana`: Khóa SSH riêng Ed25519 (đã set quyền an toàn).
   - `ssh_config_nguyenvana`: File cấu hình OpenSSH Client chuẩn.
   - `setup-ssh-nguyenvana.sh`: Script 1-chạm tự động cài đặt vào máy của thành viên.
4. Bấm nút **"🔑 Passphrase & Mật khẩu tạm"** để copy Passphrase mở khóa SSH key và Mật khẩu tạm đăng nhập lần đầu, sau đó gửi cho thành viên qua kênh liên lạc bảo mật riêng (Signal, Telegram hoặc trực tiếp).

> [!NOTE]
> Khóa riêng SSH chỉ lưu tạm trong RAM tối đa 15 phút và tự hủy ngay sau khi Admin tải bộ kết nối. Nếu thành viên làm mất file khóa, Admin chỉ cần bấm **"Cấp khóa kết nối thẳng 1 chạm"** hoặc **"Cấp khóa có Passphrase"** để tạo bộ khóa mới và tải lại file ZIP ngay lập tức.

---

## PHẦN 2: DÀNH CHO THÀNH VIÊN NGHIÊN CỨU (RESEARCHER WORKFLOW)

### Bước 1: Kết nối WireGuard VPN
1. Tải và cài đặt ứng dụng **WireGuard** chính thức từ [wireguard.com](https://www.wireguard.com/install/) (hỗ trợ Windows, macOS, Linux, iOS, Android).
2. Mở ứng dụng WireGuard, bấm **"Add Tunnel"** -> **"Import tunnel(s) from file"** -> Chọn file `nguyenvana-labops-wg0.conf`.
3. Bấm nút **"Activate"** để bật VPN.
4. Khi thấy trạng thái chuyển sang xanh và có thông số Transfer (Data sent / received) tức là bạn đã kết nối thành công vào mạng nghiên cứu LabOps!

### Bước 2: Cài đặt SSH 1-Chạm (Linux / macOS)
1. Giải nén file `LabOps_Access_nguyenvana.zip`.
2. Mở Terminal tại thư mục vừa giải nén và chạy lệnh:
   ```bash
   bash setup-ssh-nguyenvana.sh
   ```
3. Script sẽ tự động:
   - Di chuyển khóa `id_ed25519_nguyenvana` vào thư mục `~/.ssh/` và thiết lập quyền `chmod 600`.
   - Cập nhật cấu hình vào file `~/.ssh/config`.
4. Kể từ lúc này, để kết nối vào máy chủ làm việc, bạn chỉ cần gõ một lệnh duy nhất:
   ```bash
   ssh labops-nguyenvana
   ```

### Bước 3: Đổi Mật khẩu Lần đầu
Trong lần đăng nhập SSH đầu tiên bằng mật khẩu tạm, hệ thống sẽ yêu cầu bạn đổi sang mật khẩu chính thức. Mật khẩu mới cần tối thiểu 12 ký tự gồm chữ hoa, chữ thường, chữ số và ký tự đặc biệt.

---

## PHẦN 3: ĐĂNG KÝ THIẾT BỊ THỨ HAI (MULTI-DEVICE)

Nếu thành viên muốn kết nối VPN từ thêm một thiết bị khác (ví dụ iPad hoặc điện thoại):
1. Admin mở dòng của thành viên trên Dashboard.
2. Tại mục **"2. Quản lý Đa Thiết Bị WireGuard VPN"**, nhập tên thiết bị (ví dụ: `iPad Pro`) vào ô và bấm **"+ Cấp thiết bị"**.
3. File cấu hình `.conf` cho iPad sẽ được tải về ngay lập tức.
4. Gửi file này sang iPad hoặc quét mã QR trong app WireGuard để sử dụng đồng thời với máy tính xách tay.

