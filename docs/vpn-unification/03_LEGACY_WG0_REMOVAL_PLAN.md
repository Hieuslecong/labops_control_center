# KẾ HOẠCH VÔ HIỆU HÓA VÀ THU HỒI LEGACY WG0 TRÊN HOST
## 03_LEGACY_WG0_REMOVAL_PLAN.md

### 1. Mục tiêu và điều kiện tiên quyết

Dịch vụ `legacy-wg0` trên host Rack (giao tiếp qua `/etc/wireguard/wg0.conf` trên cổng UDP 2204) từng là kênh VPN chính trước khi Docker `wg-easy` được chuẩn hóa. Để loại bỏ hoàn toàn mà không làm mất kết nối của các kỹ sư đang làm việc từ xa:

#### Điều kiện tiên quyết để Cutover:
- [x] Đã loại bỏ code gọi `LegacyWireGuardProvider` khỏi runtime backend LabOps.
- [x] Đã có cơ chế cấp phát động qua `wg-easy` v15.
- [ ] Tất cả thành viên đang hoạt động đã được cấp lại cấu hình VPN mới qua `wg-easy` (UDP 2205).
- [ ] Đã xác nhận ít nhất 1 kênh SSH quản trị dự phòng (Direct LAN, mTLS Gateway hoặc Reverse Proxy) đang sẵn sàng.
- [ ] Quản trị viên vật lý có mặt tại Lab hoặc có kết nối OOB (Out-of-band management) đề phòng sự cố mạng.

### 2. Các bước triển khai Cutover từng bước (Staged Cutover)

#### Giai đoạn 1: Cô lập mã nguồn (Code Decoupling) — ĐÃ HOÀN THÀNH
- Loại bỏ `WireGuardAdapter` và `legacy_wireguard_provider` khỏi `LabOpsDataService` và `VPNManager`.
- Mọi yêu cầu truy vấn provider `"legacy-wg0"` đều bị từ chối với mã lỗi `LEGACY_PROVIDER_REMOVED`.

#### Giai đoạn 2: Di chuyển dữ liệu Peer (Reconciliation)
- Trích xuất danh sách peer từ `/etc/wireguard/wg0.conf` cũ.
- Tự động tạo client tương ứng trên `wg-easy` với IP giữ nguyên hoặc cấp mới trong dải `10.88.40.0/24`.
- Gửi thông báo tải cấu hình mới cho các thành viên.

#### Giai đoạn 3: Dừng dịch vụ Legacy trên Host (Manual Host Action)
*Lưu ý: Chỉ thực hiện bằng tay khi có phê duyệt từ Quản trị viên:*
```bash
# 1. Sao lưu cấu hình cũ
sudo cp -r /etc/wireguard /etc/wireguard.backup.$(date +%F)

# 2. Dừng và vô hiệu hóa service legacy
sudo systemctl stop wg-quick@wg0
sudo systemctl disable wg-quick@wg0

# 3. Kiểm tra cổng UDP 2204 đã giải phóng
sudo ss -ulnp | grep 2204
```

#### Giai đoạn 4: Chuyển hướng NAT trên MikroTik Router (Nếu cần dùng lại cổng 2204)
Nếu muốn chuyển cổng chuẩn của wg-easy về UDP 2204:
1. Sửa `infrastructure/wg-easy/compose.yaml`: `WG_EASY_UDP_PORT=2204`.
2. Khởi động lại container: `docker compose up -d`.
3. Cập nhật firewall rule trên MikroTik trỏ cổng 2204 tới IP của máy chủ chạy `wg-easy`.
