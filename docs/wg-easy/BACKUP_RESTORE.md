# SAO LƯU VÀ PHỤC HỒI DỮ LIỆU WG-EASY (BACKUP & RESTORE)
**Bảo vệ dữ liệu danh bạ VPN, khóa mã hóa và cấu hình**

---

## 1. CÁC TỆP DỮ LIỆU CỐT LÕI

Toàn bộ dữ liệu của `wg-easy` được lưu trữ trong Docker Volume `labops_wg_easy_data` (tương đương `/etc/wireguard` trong container):
1. `wg-easy.db`: Cơ sở dữ liệu SQLite chứa tài khoản, thông số clients, lưu lượng truyền nhận, khóa công khai/riêng tư.
2. `wg0.conf`: Tệp cấu hình WireGuard runtime được sinh tự động bởi `wg-easy`.

---

## 2. KỊCH BẢN SAO LƯU NHANH (BACKUP SCRIPT)

Chạy lệnh sao lưu trực tiếp từ host:
```bash
#!/usr/bin/env bash
set -e
BACKUP_DIR="/home/aiot-1/backups/wg-easy"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "$BACKUP_DIR"

echo "Đang sao lưu cơ sở dữ liệu wg-easy..."
docker cp labops-wg-easy:/etc/wireguard/wg-easy.db "$BACKUP_DIR/wg-easy_${TIMESTAMP}.db"
docker cp labops-wg-easy:/etc/wireguard/wg0.conf "$BACKUP_DIR/wg0_${TIMESTAMP}.conf"

echo "✅ Đã lưu bản backup tại: $BACKUP_DIR/wg-easy_${TIMESTAMP}.db"
```

---

## 3. QUY TRÌNH PHỤC HỒI (RESTORE PROCEDURE)

Khi cần khôi phục lại dữ liệu VPN:

```bash
# 1. Dừng container wg-easy
docker compose -f infrastructure/wg-easy/compose.yaml down

# 2. Khởi tạo container tạm để copy dữ liệu vào volume
docker run --rm -v labops_wg_easy_data:/target -v /home/aiot-1/backups/wg-easy:/source alpine sh -c "
  cp /source/wg-easy_TARGET.db /target/wg-easy.db
  cp /source/wg0_TARGET.conf /target/wg0.conf
  chmod 600 /target/wg-easy.db /target/wg0.conf
"

# 3. Khởi động lại container
docker compose -f infrastructure/wg-easy/compose.yaml up -d
```

