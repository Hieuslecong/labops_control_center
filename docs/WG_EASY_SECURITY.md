# CHÍNH SÁCH BẢO MẬT & DEVSECOPS — WG-EASY & LABOPS CONTROL CENTER

## 1. Mô hình phòng thủ theo chiều sâu (Defense-in-Depth)

Hệ thống quản lý truy cập LabOps áp dụng kiến trúc an ninh nhiều lớp:

```
[ Lớp 1: Router WAN Firewall (MikroTik) ]
               │
               ▼  Chỉ forward UDP 2204 (WireGuard NAT)
[ Lớp 2: Linux Host Kernel Network Namespace ]
               │
               ▼  Docker Container cô lập (không --privileged)
[ Lớp 3: Loopback-Only Binding (127.0.0.1:51821) ]
               │
               ▼  Xác thực Clerk RBAC + HMAC Session Token
[ Lớp 4: BFF Gateway & FastAPI RBAC Dependencies ]
               │
               ▼  Mã hóa One-Time Vault & Ed25519 Keys
[ Lớp 5: Per-Member Isolated Workspaces & Storage ]
```

---

## 2. Nguyên tắc đặc quyền tối thiểu (Least Privilege)

### 2.1. Cấu hình Container An toàn (Không dùng `--privileged`)
Container `labops-wg-easy` không sử dụng cờ `--privileged`. Thay vào đó, container chỉ được cấp đúng 2 Linux capabilities cần thiết để quản lý giao diện WireGuard:
- `cap_add: [NET_ADMIN, SYS_MODULE]`: Cho phép tạo và cấu hình virtual network interface `wg0` bên trong container.
- Volume `/lib/modules:/lib/modules:ro` được gắn ở chế độ **chỉ đọc (Read-Only)** để nạp module kernel nếu cần, ngăn chặn việc ghi đè module hệ thống.

### 2.2. Đóng cổng Quản trị Web API khỏi Internet
- **Rủi ro:** Giao diện web của wg-easy nếu bị mở ra Internet có thể trở thành mục tiêu của tấn công brute-force mật khẩu hoặc khai thác lỗ hổng web.
- **Biện pháp:** Port `51821` **bắt buộc chỉ bind vào địa chỉ loopback** `127.0.0.1:51821:51821/tcp`.
- **Hiệu quả:** Người dùng ngoài Internet không thể truy cập trực tiếp port 51821. Mọi tương tác với API của wg-easy phải thông qua backend FastAPI (`http://127.0.0.1:8000`) sau khi đã vượt qua xác thực phân quyền nghiêm ngặt của Clerk.

---

## 3. Chính sách Quản lý Khóa & One-Time Vault

### 3.1. Thuật toán Mật mã Hiện đại
- **WireGuard VPN:** Khóa trao đổi Curve25519 (ECDH), xác thực ChaCha20-Poly1305, băm BLAKE2s.
- **SSH Workspaces:** Khóa công khai `ssh-ed25519` (chuẩn Ed25519 độ dài 256-bit), nhanh và an toàn vượt trội so với RSA truyền thống.

### 3.2. Cơ chế One-Time Vault (Bàn giao 1 lần và Tự hủy trong RAM)
Nhằm tuân thủ nguyên tắc Zero-Knowledge đối với khóa riêng của người dùng:
1. Khi tạo thành viên hoặc cấp lại khóa, backend sinh cặp khóa SSH Ed25519 trong bộ nhớ RAM tạm thời (`OneTimeSecretVault`).
2. Khóa công khai được tự động ghi vào cấu hình authorized_keys của workspace container.
3. Khóa riêng (kèm passphrase ngẫu nhiên nếu kích hoạt) được lưu trong RAM với thời gian sống tối đa (TTL = 15 phút).
4. Khi Admin bấm **"Tải bộ kết nối (.zip)"** hoặc gọi `/claim-key-bundle`, backend giải phóng (wipe) hoàn toàn khóa riêng khỏi RAM.
5. Sau khi đã tải, không ai (kể cả Admin hay kẻ tấn công chiếm quyền database) có thể khôi phục lại khóa riêng đó trên máy chủ. Nếu thành viên làm mất file khóa, Admin chỉ cần bấm **"Cấp khóa mới"** để xoay vòng (rotate) khóa tức thì.

---

## 4. Kiểm soát Truy cập Dựa trên Vai trò (RBAC)

1. **Super Admin / Lab Manager:**
   - Tạo, chỉnh sửa, khóa và xóa thành viên.
   - Cấp phát, tạm dừng hoặc thu hồi thiết bị WireGuard VPN.
   - Cấp phát và thu hồi khóa SSH vào container.
   - Giám sát tình trạng toàn trạm và phân bổ tài nguyên CPU/RAM/GPU.
2. **Researcher / Member:**
   - Chỉ được tải file cấu hình VPN và SSH thuộc về chính tài khoản của mình.
   - Bắt buộc đổi mật khẩu tạm lần đầu (yêu cầu tối thiểu 12 ký tự gồm chữ hoa, chữ thường, chữ số và ký tự đặc biệt).
   - Không được xem hoặc can thiệp vào thiết bị, khóa SSH hoặc container của thành viên khác.

---

## 5. Phòng chống Tấn công Từ chối Dịch vụ (DoS) & Circuit Breaker

Lớp `WgEasyAdapter` trong backend tích hợp sẵn:
- **HTTP Timeout:** Mọi lời gọi API nội bộ tới wg-easy có timeout tối đa 5 giây.
- **Circuit Breaker:** Khi wg-easy gặp sự cố hoặc restart, adapter tự động chuyển sang chế độ fail-safe và ghi log chi tiết, không làm tê liệt toàn bộ ứng dụng LabOps Control Center.

