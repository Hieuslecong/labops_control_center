# ĐÁNH GIÁ BẢO MẬT HỆ THỐNG LABOPS + WG-EASY
**DevSecOps & Threat Modeling Review**

---

## 1. NGUYÊN TẮC QUẢN LÝ BÍ MẬT VÀ KHÓA RIÊNG (PRIVATE KEY HYGIENE)

1. **Tuyệt đối không lưu Plaintext Private Key trên đĩa**:
   - Backend chỉ tạo cặp khóa tạm thời trong bộ nhớ RAM khi cấp phát.
   - Khóa riêng được đóng gói vào tệp ZIP kết nối và trả về cho Admin đúng **1 LẦN DUY NHẤT** (One-Time Claim).
   - Sau khi xuất ZIP hoặc tải về, khóa riêng được giải phóng hoàn toàn khỏi bộ nhớ.
2. **Không ghi Private Key vào Logs**:
   - Mọi log ghi nhận chỉ hiển thị Fingerprint SHA256 (VD: `SHA256:d9Yv...`) hoặc Public Key, không bao giờ in Private Key.

---

## 2. BẢO VỆ GIAO DIỆN QUẢN TRỊ WG-EASY

- **Không mở Web UI ra Internet**: Port 51821 được bind duy nhất vào `127.0.0.1:51821` (`127.0.0.1:51821:51821/tcp`).
- **Không ai từ Internet có thể truy cập Web UI của wg-easy**: Chỉ có tiến trình Backend API nội bộ gọi REST API qua loopback.
- **Mật khẩu bảo vệ Web API**: Mật khẩu phức tạp `WG_EASY_ADMIN_PASSWORD` được nạp từ biến môi trường, không lưu cứng trong mã nguồn mở.

---

## 3. CÁCH LY THÀNH VIÊN VÀ QUYỀN HẠN TỐI THIỂU (LEAST PRIVILEGE)

- Container của thành viên không chạy `--privileged`.
- Không mount `/var/run/docker.sock` vào workspace của thành viên.
- Chặn tuyệt đối mount chéo các thư mục `/home` hoặc `/workspace` của thành viên khác.
- Kernel RLIMIT thực thi giới hạn CPU core, RAM limit và Storage quota ngay tại mức hệ thống.

