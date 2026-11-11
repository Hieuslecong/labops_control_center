# QUY TRÌNH CHUYỂN ĐỔI VPN TỪ HOST WG0 SANG WG-EASY
**Chiến lược Zero-Downtime Migration và Cutover an toàn**

---

## 1. GIAI ĐOẠN 1: DUAL-RUN (HIỆN TẠI)

- **Mục tiêu**: Đảm bảo admin không bị ngắt kết nối SSH/VPN trên dải `10.88.30.0/24` (MacBook IP `10.88.30.2`) trong quá trình thử nghiệm.
- **Trạng thái**:
  - Host `wg-quick@wg0` vẫn hoạt động bình thường trên UDP port 2204.
  - `labops-wg-easy` hoạt động độc lập trên dải `10.88.40.0/24`, tiếp nhận kết nối qua host port 2205 (`0.0.0.0:2205->2204/udp`).
  - Toàn bộ thành viên mới được cấp trên dải `10.88.40.0/24`.
  - Mọi thao tác qua Dashboard chỉ tương tác với `wg-easy`.

---

## 2. GIAI ĐOẠN 2: CHUYỂN GIAO THIẾT BỊ ADMIN SANG WG-EASY

1. Trên LabOps Dashboard, thêm thiết bị "Admin MacBook" cho tài khoản quản trị.
2. Tải tệp cấu hình `admin-macbook.conf` từ Dashboard (hoặc trong gói ZIP).
3. Import tệp vào ứng dụng WireGuard trên MacBook và kiểm tra kết nối tới `118.70.169.236:2205`.
4. Xác minh SSH vào host `192.168.167.250:1911` thành công qua đường hầm mới (`10.88.40.x`).

---

## 3. GIAI ĐOẠN 3: CUTOVER CHÍNH THỨC SANG UDP 2204

Khi Admin xác nhận toàn bộ kết nối trên `wg-easy` đã hoạt động hoàn hảo:

### Bước 1: Dừng dịch vụ WireGuard trên host
```bash
sudo systemctl stop wg-quick@wg0
sudo systemctl disable wg-quick@wg0
```

### Bước 2: Chuyển cổng `compose.yaml` về UDP 2204
Mở tệp `infrastructure/wg-easy/compose.yaml` và sửa dòng port mapping:
```yaml
    ports:
      - "2204:2204/udp"
      - "127.0.0.1:51821:51821/tcp"
```

### Bước 3: Cập nhật biến môi trường `backend/app/core/config.py` hoặc `.env.local`
```bash
WG_EASY_PUBLIC_ENDPOINT="118.70.169.236:2204"
```

### Bước 4: Khởi động lại container
```bash
docker compose -f infrastructure/wg-easy/compose.yaml up -d
```

### Bước 5: Kiểm tra
`ss -ulnp | grep 2204` -> Tiến trình `docker-proxy` sở hữu cổng 2204.
Lúc này toàn bộ lưu lượng UDP 2204 từ MikroTik Router sẽ đi thẳng vào `labops-wg-easy`.

