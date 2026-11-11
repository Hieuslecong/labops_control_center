# TRIỂN KHAI WG-EASY V15 CHO LABOPS CONTROL CENTER

Thư mục này chứa cấu hình triển khai Docker Compose độc lập cho **wg-easy v15**, phục vụ quản trị WireGuard VPN tự động cho LabOps Control Center.

---

## 1. Thông số Kỹ thuật & Nguyên tắc An toàn
- **Phiên bản (Pinned)**: `ghcr.io/wg-easy/wg-easy:15@sha256:071f79a639e9361123a5e57733107320207b844ddbbd92dfa6962f541fe8aac8`
- **Tên container**: `labops-wg-easy`
- **Volume**: `labops_wg_easy_data` (Volume Docker riêng biệt, **tuyệt đối không mount** `/etc/wireguard` của host).
- **Cổng VPN Client**: UDP `2205` (Tách biệt hoàn toàn với `wg0` UDP `2204`).
- **Cổng Quản trị Web/API**: TCP `51821` (Chỉ bind vào `127.0.0.1`, chỉ phục vụ LabOps Backend nội bộ).
- **Subnet VPN**: `10.88.40.0/24` (Tách biệt với subnet `10.88.30.0/24` của WireGuard cũ).

---

## 2. Các Script Vận hành
- `healthcheck.sh`: Kiểm tra trạng thái container, API loopback và port lắng nghe.
- `backup.sh`: Sao lưu dữ liệu volume `labops_wg_easy_data` ra file nén `.tar.gz`.
- `restore.sh`: Phục hồi dữ liệu từ bản sao lưu.
- `rollback.sh`: Dừng container nhanh chóng khi cần rollback mà không ảnh hưởng hệ thống cũ.

---

## 3. Khởi chạy
```bash
docker compose config
docker compose up -d
bash healthcheck.sh
```

