# KẾ HOẠCH KHÔI PHỤC AN TOÀN (ROLLBACK PLAN — LABOPS CONTROL CENTER V2)

## 1. Nguyên tắc Khôi phục Không Mất Dữ liệu
Bản nâng cấp LabOps Control Center V2 được thiết kế theo nguyên tắc **chỉ bổ sung (additive) và không phá hủy (non-destructive)**:
- Không xóa hoặc sửa đổi định dạng các bảng trong `001_initial_schema.sql`, `002_ssh_dual_auth_and_storage.sql`, `003_secure_access_wireguard_teleport.sql`; toàn bộ bảng V2 nằm trong không gian tên `v2_*` tại `004_labops_v2_complete.sql`.
- Không ghi đè phá hủy các tệp JSON cũ (`members.json`, `ssh_endpoints.json`, `ssh_policies.json`, `storage_allocations.json`, `wireguard_peers.json`); các kho trạng thái mới tách riêng (`gpu_reservations.json`, `security_scans.json`, `v2_hosts.json`, `v2_workspaces.json`, `v2_ssh_keys.json`).
- Không thay đổi cấu hình `/etc/ssh/sshd_config`, không đụng vào giao diện `wg0`, không dừng bất kỳ container hay tiến trình GPU nào đang chạy trên host.

---

## 2. Quy trình Rollback Từng Bước (Nếu Cần Quay Lại Bản Trước)

### Bước 1: Khôi phục Mã nguồn trên Nhánh Làm việc
```bash
git status
git diff
# Kiểm tra commit trước khi nâng cấp V2 và checkout có kiểm soát (không dùng git reset --hard mù quáng):
git log -n 5 --oneline
```

### Bước 2: Khôi phục Dữ liệu Tệp JSON (Nếu đã tạo bản sao lưu)
```bash
ls -dt backend/data.backup.* | head -n 1
# Nếu cần quay về trạng thái JSON trước di trú:
cp -a backend/data.backup.<TIMESTAMP>/*.json backend/data/
```

### Bước 3: Khởi động lại Dịch vụ Web & API (Không chạm tới Workload Nghiên cứu)
```bash
docker compose up -d --build frontend backend
```
Do các container nghiên cứu và giao diện `wg0` tách biệt hoàn toàn với container `frontend`/`backend`, việc rollback giao diện điều khiển không gây gián đoạn bất kỳ phiên SSH hay tiến trình huấn luyện AI nào của nghiên cứu viên.

