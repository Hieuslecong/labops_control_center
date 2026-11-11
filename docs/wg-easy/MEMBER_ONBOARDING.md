# QUY TRÌNH TIẾP NHẬN THÀNH VIÊN VÀ HƯỚNG DẪN KẾT NỐI (MEMBER ONBOARDING)
**Trải nghiệm 1 chạm dành cho Nghiên cứu viên**

---

## 1. THAO TÁC CỦA ADMIN TRÊN DASHBOARD

1. Truy cập **LabOps Control Center Dashboard** -> **Quản lý Thành viên**.
2. Nhấn nút **"+ Tạo quyền truy cập SSH"** (Wizard 4 bước):
   - **Bước 1**: Nhập tên đăng nhập (VD: `nguyenvana`), họ tên, email.
   - **Bước 2**: Chọn máy chủ phân bổ (`aiotlab-rack`) và hạn mức tài nguyên (CPU, RAM, GPU, Storage).
   - **Bước 3**: Cấu hình xác thực:
     - Chọn *"Tự động sinh cặp khóa Ed25519"*.
     - Chọn *"Kết nối thẳng 1 chạm (Không cần passphrase)"* hoặc nhập Passphrase bảo mật.
   - **Bước 4**: Xác nhận tạo tài khoản.
3. Hệ thống tự động:
   - Cấp phát peer WireGuard trên `wg-easy`.
   - Cấp phát container/workspace và cổng SSH riêng.
   - Đăng ký Public Key vào `authorized_keys`.
   - Sinh mật khẩu tạm ngẫu nhiên có thời hạn.
4. Admin nhấn nút **"Tải tệp ZIP trọn gói"** và gửi tệp `LabOps_Access_nguyenvana.zip` cho thành viên qua kênh an toàn.

---

## 2. THAO TÁC CỦA NGHIÊN CỨU VIÊN (CLIENT SIDE)

### Bước 1: Kết nối WireGuard VPN
1. Tải ứng dụng WireGuard Client cho macOS / Windows / Linux / iOS / Android.
2. Mở tệp ZIP `LabOps_Access_<username>.zip` vừa nhận.
3. Import tệp `wireguard/labops.conf` vào ứng dụng WireGuard và bấm **Activate**.

### Bước 2: Thiết lập kết nối SSH tự động
- **Trên macOS / Linux**:
  Mở Terminal trong thư mục giải nén và chạy:
  ```bash
  bash scripts/setup-macos.sh   # hoặc bash scripts/setup-linux.sh
  ```
  *(Script tự động sao chép Private Key vào `~/.ssh/` với quyền 0600 và thêm cấu hình vào `~/.ssh/config`).*

- **Trên Windows**:
  Mở PowerShell trong thư mục giải nén và chạy:
  ```powershell
  .\scripts\setup-windows.ps1
  ```

### Bước 3: Đăng nhập vào Workspace
Gõ trực tiếp lệnh:
```bash
ssh labops-<username>
```
Kết nối thành công ngay lập tức!

