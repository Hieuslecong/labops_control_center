# KẾ HOẠCH CHUYỂN ĐỔI (CUTOVER) VÀ KỊCH BẢN ROLLBACK KHẨN CẤP
## 08_MIGRATION_ROLLBACK_PLAN.md

### 1. Kế hoạch Cutover có kiểm soát (Controlled Cutover)

Để đảm bảo không làm gián đoạn công việc của các nhà nghiên cứu và các tiến trình huấn luyện mô hình AI:

```text
BƯỚC 1: XÁC MINH TEST MÔ PHỎNG (ĐÃ HOÀN THÀNH: 16/16 TESTS PASS)
         │
         ▼
BƯỚC 2: SAO LƯU DỮ LIỆU TOÀN DIỆN (Trạng thái Host, DB, WireGuard Keys)
         │
         ▼
BƯỚC 3: DEPLOY CODE BACKEND V2 (feature/wireguard-unification)
         │
         ▼
BƯỚC 4: TÁI TẠO PEER TRÊN DOCKER WG-EASY V15 (Cổng 2205)
         │
         ▼
BƯỚC 5: PHÁT HÀNH CẤU HÌNH MỚI CHO CÁC THÀNH VIÊN
         │
         ▼
BƯỚC 6: XÁC MINH KẾT NỐI SONG SONG (Dual Connectivity Verification)
         │
         ▼
BƯỚC 7: TẮT DỊCH VỤ LEGACY WG0 TRÊN HOST (Chỉ khi 100% thành viên đã chuyển sang wg-easy)
```

### 2. Kịch bản Rollback khẩn cấp (Instant Rollback Procedures)

Nếu xảy ra sự cố không lường trước sau khi triển khai:

#### Tình huống A: Backend gặp lỗi khi sinh cấu hình
**Hành động**:
1. Khôi phục backend commit trước đó (`git checkout hardening/labops-v2`).
2. Khởi động lại service FastAPI backend.
3. Các peer đã cấp trước đó trên wg-easy vẫn hoạt động bình thường ở tầng mạng.

#### Tình huống B: Container `wg-easy` gặp sự cố Docker engine
**Hành động**:
1. Giữ nguyên dịch vụ `wg-quick@wg0` trên host (chưa bị xóa).
2. Quản trị viên truy cập vào host qua cổng 1911 (Rack) hoặc 1912 (Workstation) qua mạng LAN vật lý hoặc IP nội bộ.
3. Kích hoạt lại `systemctl start wg-quick@wg0` (nếu đã tạm dừng).
4. Khôi phục port forwarding trên MikroTik về cổng UDP 2204.

#### Lệnh Rollback một chạm:
```bash
#!/bin/bash
# scripts/rollback_legacy_vpn.sh
echo "=== KÍCH HOẠT QUY TRÌNH ROLLBACK KHẨN CẤP ==="
sudo systemctl start wg-quick@wg0
sudo systemctl enable wg-quick@wg0
sudo wg show
echo "Dịch vụ legacy wg0 đã hoạt động trở lại."
```
