# KẾ HOẠCH DI TRÚ DỮ LIỆU & HẠ TẦNG KHÔNG PHÁ HỦY (MIGRATION PLAN V2)

## 1. Nguyên tắc Di trú Không Gián đoạn (Zero-Destruction Migration)
1. **Không xóa và không ghi đè phá hủy** các tệp JSON hiện có trong `backend/data/*.json` (`members.json`, `ssh_endpoints.json`, `ssh_policies.json`, `storage_allocations.json`, `wireguard_peers.json`, `audit_logs.json`).
2. **Bảo toàn Legacy SSH (cổng 22) và các workspace Incus hiện hữu**: Mọi endpoint SSH/Incus cũ được ánh xạ sang `runtime_type = INCUS_LEGACY` với cờ `legacy_preserved = true`.
3. **Chuyển đổi mô hình truy cập từ xa sang WireGuard + OpenSSH Per-Member Public Key**:
   - Không triển khai NetBird.
   - Gỡ bỏ sự phụ thuộc bắt buộc vào Teleport trong luồng đăng ký khóa SSH của thành viên; Teleport chỉ giữ vai trò tùy chọn mở rộng tương lai.

---

## 2. Các Bước Thực hiện Di trú Cơ sở Dữ liệu (`004_labops_v2_complete.sql`)

### Bước 1: Sao lưu trạng thái hiện tại
```bash
cp -a backend/data "backend/data.backup.$(date +%Y%m%d_%H%M%S)"
```

### Bước 2: Chạy Migration SQL trên PostgreSQL 16
```bash
psql "$DATABASE_URL" -f backend/migrations/001_initial_schema.sql
psql "$DATABASE_URL" -f backend/migrations/002_ssh_dual_auth_and_storage.sql
psql "$DATABASE_URL" -f backend/migrations/003_secure_access_wireguard_teleport.sql
psql "$DATABASE_URL" -f backend/migrations/004_labops_v2_complete.sql
```
Toàn bộ câu lệnh trong `004_labops_v2_complete.sql` đều sử dụng `CREATE TABLE IF NOT EXISTS` và `CREATE INDEX IF NOT EXISTS`, đặt trong giao dịch `BEGIN ... COMMIT` để đảm bảo tính nguyên tử (idempotent & transactional).

### Bước 3: Đối soát Dữ liệu JSON Hiện hữu (Reconciliation)
Gọi endpoint quản trị `GET /api/v1/v2/reconcile` (hoặc qua `HostRegistry.reconcile_json_stores()`) để kiểm tra tính toàn vẹn của toàn bộ 8 kho dữ liệu JSON và đối chiếu số bản ghi mà không làm thay đổi dữ liệu gốc.

