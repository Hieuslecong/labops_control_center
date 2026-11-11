# QUY TRÌNH SAO LƯU VÀ PHỤC HỒI — WG-EASY & LABOPS CONTROL CENTER

## 1. Dữ liệu cần Sao lưu

Dữ liệu của dịch vụ `wg-easy v15` nằm trong Docker named volume: `labops_wg_easy_data`.
Bên trong container tại đường dẫn `/etc/wireguard/`:
- `wg0.conf`: File cấu hình WireGuard engine (chứa server private key, listen port, và danh sách peers).
- `db.json` / cơ sở dữ liệu nội bộ: Danh sách clients, ngày tạo, tên thiết bị, trạng thái kích hoạt, thống kê traffic.

---

## 2. Kịch bản Sao lưu Tự động (`backup.sh`)

Script được lưu tại: `infrastructure/wg-easy/backup.sh`

### Cách thực hiện thủ công:
```bash
cd /home/aiot-1/admin_labops_control_center/infrastructure/wg-easy
bash backup.sh
```

### Thiết lập Cronjob sao lưu định kỳ (Mỗi ngày lúc 02:00 sáng):
```bash
0 2 * * * cd /home/aiot-1/admin_labops_control_center/infrastructure/wg-easy && bash backup.sh >> /var/log/labops_wg_easy_backup.log 2>&1
```

Script sẽ tạo một file nén dạng `tar.gz` lưu trữ tại:
`/home/aiot-1/admin_labops_control_center/infrastructure/wg-easy/backups/wg_easy_backup_YYYYMMDD_HHMMSS.tar.gz`

---

## 3. Quy trình Phục hồi Dữ liệu (`restore.sh`)

Trong trường hợp máy chủ gặp sự cố hoặc container bị xóa nhầm dữ liệu:

### Bước 1: Liệt kê các bản sao lưu có sẵn
```bash
ls -lh /home/aiot-1/admin_labops_control_center/infrastructure/wg-easy/backups/
```

### Bước 2: Chạy script khôi phục
```bash
cd /home/aiot-1/admin_labops_control_center/infrastructure/wg-easy
bash restore.sh backups/wg_easy_backup_YYYYMMDD_HHMMSS.tar.gz
```

### Bước 3: Xác thực lại sau khi khôi phục
```bash
bash healthcheck.sh
docker exec labops-wg-easy wg show
```

---

## 4. Kịch bản Rollback Nhanh (`rollback.sh`)

Nếu sau khi cập nhật cấu hình hoặc nâng cấp container mà phát sinh lỗi:
```bash
cd /home/aiot-1/admin_labops_control_center/infrastructure/wg-easy
bash rollback.sh
```
Script sẽ tự động dừng container hiện tại, khôi phục bản sao lưu gần nhất trước khi nâng cấp và khởi động lại container về trạng thái ổn định đã biết.

