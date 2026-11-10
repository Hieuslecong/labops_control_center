# TÍCH HỢP WIREGUARD VPN & OPENSSH PER-MEMBER PUBLIC KEY (WIREGUARD INTEGRATION V2)

## 1. Kiến trúc Truy cập Từ xa Chuẩn V2
```
Thiết bị Nghiên cứu viên (WireGuard Client)
  ──[Tunnel wg0 đã xác minh Handshake]──> Địa chỉ VPN Nội bộ (10.200.0.x)
  ──[OpenSSH Public Key Ed25519/RSA]────> Cổng SSH Cô lập (22010–29999) của Workspace
```
- **Không triển khai NetBird**.
- **Không bắt buộc Teleport** trong luồng kết nối SSH vào workspace (Teleport chỉ giữ vai trò tùy chọn mở rộng tương lai).
- **Bảo toàn nguyên trạng Legacy SSH (cổng 22)** trên máy chủ vật lý và giao diện `wg0` đang chạy.

---

## 2. Nguyên tắc An toàn Khóa Mật mã
1. **WireGuard**:
   - Client tự sinh cặp khóa trên thiết bị cá nhân và chỉ gửi `public_key` (chuỗi Base64 44 ký tự) lên LabOps.
   - LabOps tuyệt đối không sinh, không lưu và không trả về `private_key` hay tệp cấu hình chứa khóa riêng.
   - Trạng thái peer ban đầu là `PENDING_HANDSHAKE` / `NOT_VERIFIED`, chỉ chuyển sang `ACTIVE` khi quan sát được handshake thực tế từ `wg show wg0 dump`.
2. **OpenSSH Per-Member Public Key (`backend/app/services/vpn_ssh_service.py`)**:
   - Chỉ chấp nhận khóa công khai định dạng OpenSSH (`ssh-ed25519`, `rsa-sha2-256`, `ecdsa-sha2-nistp256/...`).
   - Tự động từ chối nếu chuỗi đầu vào chứa `PRIVATE KEY`.
   - Tính và kiểm tra trùng lặp vân tay `SHA256:<base64>` trước khi lưu.
   - Hỗ trợ thu hồi (`REVOKED`) theo đúng quyền sở hữu (nghiên cứu viên chỉ thu hồi được khóa của chính mình; Admin thu hồi được mọi khóa).

