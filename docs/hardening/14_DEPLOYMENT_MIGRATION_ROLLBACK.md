# 14 — KẾ HOẠCH TRIỂN KHAI, MIGRATION & ROLLBACK (DEPLOYMENT & ROLLBACK)

- **Branch**: `hardening/labops-v2`
- **Nguyên tắc an toàn**: Tuyệt đối giữ nguyên IP (`192.168.167.251` / `192.168.167.250`), NIC, WireGuard peers, SSH production, các container Docker và tác vụ GPU của thành viên trong suốt quá trình chuẩn bị và chuyển đổi có kiểm soát (Controlled Canary).

---

## 1. Quy trình Sao lưu & Chuyển đổi Dữ liệu (Pre-Cutover Backup & Migration)

1. **Sao lưu dữ liệu hiện hữu trước khi chạm production**:
   - Sao lưu bản copy chỉ đọc `backend/data/*.json` và SQLite DB hiện có (nếu đã khởi tạo).
   - Sử dụng công cụ đã kiểm thử `scripts/labops_db_backup_restore.py`:
     ```bash
     backend/.venv/bin/python scripts/labops_db_backup_restore.py backup \
       --db-url "sqlite:///backend/data/labops_v2.sqlite3" \
       --out-dir "/var/backups/labops/pre-canary-$(date +%Y%m%d-%H%M%S)"
     ```
2. **Áp dụng Alembic Migration lên PostgreSQL mục tiêu (chỉ khi có phê duyệt bảo trì)**:
   - Thiết lập `DATABASE_URL="postgresql://<user>:<pass>@127.0.0.1:5432/labops_v2"`.
   - Kiểm tra kết nối và áp dụng schema `0001_labops_v2_hardened`:
     ```bash
     cd backend && .venv/bin/alembic upgrade head
     ```
3. **Biến Môi trường Bắt buộc (Fail-Closed)**:
   - `LABOPS_SESSION_SECRET` (ngẫu nhiên >= 32 bytes, dùng chung cho FastAPI & Next.js BFF để verify HMAC + JTI).
   - `LABOPS_ADMIN_USERNAME`, `LABOPS_ADMIN_PASSWORD` (mật khẩu quản trị mạnh).
   - `LABOPS_AGENT_SECRET` & `LABOPS_BROKER_VERIFY_SECRET` (bảo vệ kết nối Backend → Go Node Agent / Repository Guard).
   - `LABOPS_GATEWAY_CONTROL_SECRET` & chứng chỉ CA/Gateway mTLS trên đường dẫn PKI bảo vệ `0600`/`0700`.
   - `NEXT_PUBLIC_DEMO_MODE="false"`.

---

## 2. Triển khai Canary Có Kiểm soát (Staged Canary Rollout)

| Giai đoạn | Phạm vi | Điều kiện kiểm tra trước khi đi tiếp (Gate) |
|---|---|---|
| **Giai đoạn 1 — Gateway & Workstation Agent** | Khởi chạy `labops-gateway` + `labops-agent` trên `aiot-ws1` với mTLS PKI cục bộ (chỉ đọc telemetry `WS-GPU-01` & quy trình thử nghiệm non-destructive). | Agent kết nối TLS 1.3 thành công với SPIFFE ID `spiffe://labops/node/workstation`; `/api/v1/gateway/nodes/workstation/telemetry` trả dữ liệu tươi (`ONLINE`). |
| **Giai đoạn 2 — Backend & Frontend Staging Port** | Chạy FastAPI + Next.js build mới ở cổng staging nội bộ (không đè `.next/` hoặc cổng đang phục vụ người dùng trước giờ bảo trì). | Login Admin + Researcher (`must_change_password`), tạo cảnh báo `NotificationCenter`, kiểm tra p95 API < 50ms, chạy smoke browser trên các breakpoint Mobile/Tablet/Desktop. |
| **Giai đoạn 3 — Cutover Chính thức & Multi-Node** | Chuyển traffic sang bản build mới tại cửa sổ bảo trì; khi `aiotlab-rack` online trở lại, chạy enrollment mTLS cho node `track` qua `deploy/enroll-node.sh`. | Cả 2 node `workstation` + `track` hiển thị đúng GPU (`1× A30` và `3× A30`), 3 test Rack trong `pytest` hết `SKIPPED` và chuyển `PASS`. |

---

## 3. Kế hoạch Rollback (< 5 phút)

Nếu phát hiện lỗi nghiêm trọng sau cutover:
1. **Rollback Ứng dụng & Binary**: Trỏ symlink / checkout lại commit `1b8d426` (hoặc artifact build trước cutover), giữ nguyên container Docker & workspace thành viên đang chạy.
2. **Rollback Schema & Dữ liệu (nếu cần)**:
   - Dùng `scripts/labops_db_backup_restore.py restore --bundle-dir <BACKUP_DIR> --target-db-url <URL> --allow-overwrite` để khôi phục chính xác dữ liệu kèm xác minh SHA-256 `manifest.json`.
   - Hoặc hạ schema nếu vừa mới migrate và chưa ghi dữ liệu mới bằng `alembic downgrade base`.
3. **Thu thập Nhật ký Sự cố**: Trích xuất bảng `audit_events` và log systemd trước khi khởi động lại dịch vụ nền.
