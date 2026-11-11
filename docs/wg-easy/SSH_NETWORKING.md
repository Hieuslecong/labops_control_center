# KIẾN TRÚC MẠNG VÀ KẾT NỐI SSH CONTAINER QUA VPN
**Định tuyến, Tường lửa và Cách ly Container**

---

## 1. BẢN ĐỒ ĐỊNH TUYẾN MẠNG (NETWORK TOPOLOGY)

- **Mạng VPN WireGuard Client**: `10.88.40.0/24`
- **Mạng Môi trường Container/Host LAN**: `192.168.167.250/21`
- **Cổng SSH Container cá nhân**: `22001 - 22050`
- **Cổng SSH Host Quản trị (Admin)**: `1911`

---

## 2. NGUYÊN LÝ KẾT NỐI QUA ĐƯỜNG HẦM (TUNNEL INGRESS)

Tệp WireGuard Client `.conf` được cấp phát có trường `AllowedIPs`:
```ini
AllowedIPs = 10.88.40.0/24, 192.168.167.250/32
```
Điều này mang lại 2 lợi ích cốt lõi:
1. **Split-Tunneling**: Lưu lượng Internet thông thường của thành viên (xem video, duyệt web cá nhân) vẫn đi qua mạng gia đình/công ty, không bị đẩy qua máy chủ Lab, tiết kiệm băng thông.
2. **Truy cập nội bộ an toàn**: Chỉ các gói tin nhắm tới dải VPN `10.88.40.x` và IP LAN máy chủ `192.168.167.250` mới được chuyển qua đường hầm mã hóa WireGuard.

---

## 3. CƠ CHẾ BẢO VỆ VÀ CÁCH LY (CONTAINER ISOLATION)

1. **Phân bổ cổng SSH riêng biệt**: Mỗi thành viên được cấp 1 cổng SSH container riêng biệt (ví dụ: `testacc233` dùng cổng `22011`).
2. **Quyền thư mục nghiêm ngặt (0700)**: Thư mục workspace cá nhân được thiết lập quyền `drwx------` (0700), ngăn chặn hoàn toàn việc đọc trộm hoặc ghi đè giữa các thành viên.
3. **Chặn kết nối trực tiếp cổng 22**: Cổng SSH mặc định 22 của hệ thống bị vô hiệu hóa hoặc chỉ cho phép root nội bộ; cổng 1911 chỉ dành cho Admin có khóa SSH vật lý.
4. **Ngăn chặn rò rỉ Docker Socket**: Workspace container của thành viên không được mount socket Docker `/var/run/docker.sock` và không chạy với cờ `--privileged`.

