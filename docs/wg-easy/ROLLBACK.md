# KẾ HOẠCH DỰ PHÒNG VÀ ROLLBACK VỀ HOST WIREGUARD
**Quy trình khôi phục trạng thái nếu xảy ra sự cố khẩn cấp**

---

## 1. NGUYÊN TẮC AN TOÀN TUYỆT ĐỐI

Trong suốt giai đoạn dual-run kiểm thử:
- Cấu hình WireGuard trên Host (`/etc/wireguard/wg0.conf`) **hoàn toàn nguyên vẹn**, không bị ghi đè.
- Dịch vụ `wg-quick@wg0` trên Host vẫn đang chạy trên UDP port 2204.
- Kết nối quản trị của Admin qua SSH port 1911 và VPN `10.88.30.2` luôn được bảo vệ.

---

## 2. QUY TRÌNH ROLLBACK NẾU WG-EASY GẶP SỰ CỐ

Trong trường hợp container `labops-wg-easy` gặp sự cố không thể khắc phục:

### Bước 1: Dừng container `wg-easy`
```bash
docker compose -f infrastructure/wg-easy/compose.yaml down
```

### Bước 2: Kích hoạt lại Host WireGuard (nếu trước đó đã tắt)
```bash
sudo systemctl restart wg-quick@wg0
sudo systemctl enable wg-quick@wg0
```

### Bước 3: Đổi cấu hình Provider trong LabOps sang Chế độ Dự phòng
Trong tệp `backend/app/core/config.py`:
```python
WG_EASY_ENABLED: bool = False
```
Khởi động lại Backend API:
```bash
./start.sh
```

Hệ thống sẽ chuyển sang chế độ dự phòng an toàn, bảo đảm máy chủ không bị gián đoạn hoạt động.

