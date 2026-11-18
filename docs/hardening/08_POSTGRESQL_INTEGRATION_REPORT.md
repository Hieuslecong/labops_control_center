# 08 — BÁO CÁO TÍCH HỢP CƠ SỞ DỮ LIỆU & PHỤC HỒI (POSTGRESQL & ALEMBIC)

- **Branch**: `hardening/labops-v2`
- **Thành phần liên quan**:
  - ORM Models & Session Manager: `backend/app/db/models.py`, `backend/app/db/session.py` (`DatabaseManager`, `normalize_sync_database_url`)
  - Alembic Migrations: `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/0001_labops_v2_hardened_schema.py`
  - Atomic JSON & Idempotency: `backend/app/db/atomic_json.py`, `backend/app/db/idempotency.py`
  - Backup & Restore CLI: `scripts/labops_db_backup_restore.py`
  - Tests: `backend/tests/test_milestone2_hardening.py`, `backend/tests/test_milestone4_integration_and_ops.py`

---

## 1. Lược đồ Dữ liệu & Tính Toàn vẹn Giao dịch

1. **9 Bảng Quan hệ (SQLAlchemy 2.x — Revision `0001_labops_v2_hardened`)**:
   - `hosts`, `members`, `workspaces`, `gpu_reservations`, `audit_events`, `idempotency_records` (ràng buộc `uq_idem_actor_endpoint_key`), `revoked_tokens`, `deletion_operations`, `zalo_outbox` (`ix_zalo_outbox_month_status`).
2. **Hỗ trợ Kép SQLite WAL & PostgreSQL**:
   - `DatabaseManager` gắn tự động `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000` cho SQLite, và chuẩn hóa URL `postgresql+asyncpg://` → `postgresql://` cho driver đồng bộ (`normalize_sync_database_url`).
   - Toàn bộ cấu trúc ORM dùng chuẩn chung `String`, `Integer`, `Boolean`, `Text`, `DateTime(timezone=True)`, `JSON` và `render_as_batch=url.startswith("sqlite")` trong `alembic/env.py`.
3. **JSON Fail-Closed (`atomic_json.py`)**:
   - Ghi tệp qua temp-file + `fsync` + `os.replace` kèm file lock `fcntl`; đọc tệp bị hỏng ném `PersistenceCorruptionError` thay vì tự ghi đè dữ liệu rỗng (`test_alembic_upgrade_downgrade_roundtrip_and_corrupt_json_fail_closed`).

---

## 2. Kiểm chứng Alembic Roundtrip & Công cụ Backup / Restore

Công cụ `scripts/labops_db_backup_restore.py` hỗ trợ hai lệnh chuẩn hóa:
- `backup --db-url <URL> --out-dir <DIR>`: xuất dữ liệu từng bảng theo thứ tự khóa ngoại sang JSONL (`tables/<table>.jsonl`), thực hiện online snapshot nhị phân SQLite bằng `sqlite3.Connection.backup(...)`, ghi mã phiên bản `alembic_version`, số lượng bản ghi từng bảng và băm **SHA-256** toàn bộ artifact vào `manifest.json`.
- `restore --bundle-dir <DIR> --target-db-url <URL> [--allow-overwrite]`: kiểm tra toàn bộ SHA-256 trước khi chạm vào DB đích (lỗi ngay với exit code `2` nếu bị sửa dù chỉ 1 byte), từ chối ghi đè DB khác rỗng nếu thiếu `--allow-overwrite`, và đối chiếu lại `SELECT COUNT(*)` từng bảng sau khôi phục.

| Thử nghiệm trên DB cô lập (`tmp_path`) | Kết quả thực tế | Trạng thái |
|---|---|---|
| `alembic upgrade head` → `alembic downgrade base` → `alembic upgrade head` | Đủ 9 bảng sau `upgrade`, sạch sau `downgrade`, tái tạo nguyên vẹn sau re-upgrade | **PASS** |
| Backup & Verify `manifest.json` | Đọc `alembic_version = 0001_labops_v2_hardened`, đủ checksum JSONL + `sqlite_snapshot.db` | **PASS** |
| Tamper Rejection (chỉnh 1 byte trong `tables/members.jsonl`) | Thoát mã lỗi `2` kèm thông báo `Checksum mismatch` trước khi ghi DB đích | **PASS** |
| Clean Restore & Overwrite Guard | DB mới khớp 100% dữ liệu (`minhtq`, `ws-minhtq`, `22011`); chạy lần 2 thiếu `--allow-overwrite` bị chặn (`Refusing to overwrite`) | **PASS** |

---

## 3. Hạn chế Còn lại
- Chưa chạy Alembic migration trực tiếp lên PostgreSQL server production vì tuân thủ ràng buộc không can thiệp DB production khi chưa có lịch bảo trì được duyệt; khả năng tương thích dialect đã được kiểm chứng ở tầng SQLAlchemy/Alembic schema & ETL trung lập.
