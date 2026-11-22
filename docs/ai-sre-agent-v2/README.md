# LabOps AI SRE Agent v2 — Architecture, Operations & Deliverables

## 1. Dependency Map & Architecture Overview

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                         LabOps AI SRE Agent v2                                │
├───────────────────────┬──────────────────────────┬────────────────────────────┤
│  Ingestion Sources    │  Core SRE Engine         │  Delivery & UI             │
│                       │                          │                            │
│ • HostRegistry +      │ • UnifiedTelemetry       │ • NotificationDispatcher   │
│   HostMonitorAdapter  │   Collector (freshness)  │   (per-recipient DB outbox)│
│ • Go Node Agent :9100 │ • UnifiedIncidentEngine  │ • Zalo Bot (Admin + Owner) │
│ • Beszel / Arcane     │   (dedup, hysteresis,    │ • Daily Exec Brief (08:00) │
│ • Repository Guard    │    cooldown, ack/resolve)│ • Weekly Report (Fri 17:00)│
│ • Audit & Jobs        │ • SREToolRegistry (12    │ • Dashboard AI Assistant   │
│                       │   RBAC read-only tools)  │   (6 integrated tabs)      │
└───────────────────────┴──────────────────────────┴────────────────────────────┘
```

### Key Modules Implemented
- `backend/app/db/models.py` & `backend/alembic/versions/0003_labops_ai_sre_agent_v2.py`: 11 tables (`zalo_identity_links`, `notification_subscriptions`, `notification_outbox`, `notification_delivery_attempts`, `incidents`, `incident_events`, `report_runs`, `daily_report_snapshots`, `telemetry_history_samples`, `agent_tool_audit`, `scheduler_leases`).
- `backend/app/services/zalo_assistant/lease_manager.py`: DB row-level scheduler lease preventing split-brain duplicate workers.
- `backend/app/services/zalo_assistant/identity_rbac.py`: One-time expiring link codes (`/link LAB-XXXXXX`) binding Zalo User IDs to LabOps `ADMIN`/`RESEARCHER`/`STUDENT` accounts.
- `backend/app/services/zalo_assistant/telemetry_collector.py`: Honest `ONLINE`/`DEGRADED`/`STALE`/`OFFLINE`/`UNKNOWN` normalization + GPU (`host_id + uuid`), Docker/Workspace, Jobs, Services + historical KPIs (`N/A` on missing history).
- `backend/app/services/zalo_assistant/incident_engine.py`: Rule evaluation with hysteresis, cooldown, maintenance windows, correlation (container OOM ↔ workspace clones), `OPEN → ACKNOWLEDGED → INVESTIGATING → RESOLVED`.
- `backend/app/services/zalo_assistant/repo_alert_monitor.py`: Event-driven + 30s reconciliation Repository Guard alerts, strict Admin-only recipients, per-recipient delivery state tracking, zero secret leakage.
- `backend/app/services/zalo_assistant/report_generator.py`: Deterministic Daily Executive Briefing (`08:00 Asia/Ho_Chi_Minh`, `< 1,800` chars Zalo summary, no `localhost`, AI fallback) + live Weekly Report.
- `backend/app/services/zalo_assistant/tool_registry.py`: 12 schema-validated RBAC-enforced read-only tools, secret scrubbing, timeout & tool budget, audit trail.
- `backend/app/services/zalo_assistant/notification_dispatcher.py`: Per-recipient DB outbox, priority queue (`P0` > `P1` > `REPORT` > `P2`), quiet hours (`P0` bypass), exponential backoff + jitter, `DELIVERY_UNKNOWN` handling on timeout.

---

## 2. Audit Fix Report (Phase 0)

| Vấn đề cũ | Giải pháp đã triển khai |
| :--- | :--- |
| Hardcoded `Workstation ONLINE / Rack OFFLINE` trong `bot_core.py`, `report_generator.py`, `ai_gateway_client.py` và `ai-assistant-view.tsx` | Thay hoàn toàn bằng `UnifiedTelemetryCollector` kèm `host_id`, `status`, `collected_at`, `last_heartbeat_at`, `freshness_status`, `source`. Nếu mất dữ liệu → trả `UNKNOWN` / `OFFLINE` / `STALE`. |
| Zalo authorization chỉ dùng allowlist phẳng; `get_admin_recipients()` gửi nhầm tới toàn bộ `authorized_users` | Thêm `ZaloIdentityRBACService` liên kết Zalo ID với tài khoản LabOps qua mã một lần. `get_verified_admin_recipients()` lọc nghiêm ngặt chỉ gửi cho `ADMIN`. |
| Outbox idempotency key trùng vẫn gửi lại transport; timeout tự động hoàn quota | `QuotaLedger.is_already_dispatched()` chặn gửi trùng; timeout đánh dấu `DELIVERY_UNKNOWN` không hoàn quota mù quáng; `NotificationDispatcher` lưu state độc lập từng recipient. |
| Worker chỉ dựa vào PID file | Thêm `SchedulerLeaseManager` dùng bảng `scheduler_leases` trong PostgreSQL/SQLite; khôi phục sạch sau restart mà không tạo báo cáo trùng. |

---

## 3. Hướng dẫn Cấu hình Zalo Bot & Identity Linking

1. Thiết lập trong `.env.local` (không commit):
   - `ZALO_BOT_TOKEN=<token>`
   - `ZALO_AUTHORIZED_USERS=<admin_zalo_id_1>,<admin_zalo_id_2>` (dự phòng Admin bootstrap)
   - `LABOPS_PUBLIC_DASHBOARD_URL=https://aiotserver.fitdnu.id.vn`
2. Trên Dashboard → **AI Assistant → Định danh & Cấu hình**:
   - Nhập `username` LabOps (ví dụ `tranglt`), chọn role (`RESEARCHER`/`STUDENT`/`ADMIN`) → bấm **Tạo mã** (`LAB-XXXXXX`, hiệu lực 15 phút).
   - Thành viên gửi `/link LAB-XXXXXX` tới Zalo Bot → hệ thống tự gán đúng quyền RBAC và subscription.

---

## 4. Hướng dẫn Monitoring & Incident Rules

Các ngưỡng cấu hình qua biến môi trường:
- `SRE_GPU_TEMP_WARN_C=85` / `SRE_GPU_TEMP_CRIT_C=92`
- `SRE_DISK_WARN_PCT=85` / `SRE_DISK_CRIT_PCT=95`
- `SRE_HYSTERESIS_SAMPLES=2` (số mẫu liên tiếp trước khi phát P0/nhiệt độ/storage)
- `SRE_COOLDOWN_MINUTES=30`
- `SRE_SSH_AUTH_FAIL_THRESHOLD=10`
- Lưu ý: GPU utilization hoặc VRAM cao trong quá trình training bình thường **không** bao giờ bị đánh dấu là P0.

---

## 5. Hướng dẫn Chạy Production & External Heartbeat

### Systemd (Khuyến nghị trên Workstation `aiot-ws1`)
```bash
sudo cp infrastructure/systemd/labops-ai-sre-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now labops-ai-sre-worker.service
sudo systemctl status labops-ai-sre-worker.service
```

### Docker Compose
```bash
docker compose up -d --build sre-worker
```

### External Heartbeat (Chạy trên VPS/Monitor bên ngoài)
```bash
LABOPS_HEARTBEAT_URL="https://aiotserver.fitdnu.id.vn/api/labops/ai-assistant/heartbeat" \
EXTERNAL_ALERT_WEBHOOK_URL="https://..." \
bash scripts/ops/check-sre-external-heartbeat.sh
```
> **Lưu ý kiến trúc**: Không chạy watchdog phát hiện mất điện trên chính máy `aiot-ws1`; script này phải chạy từ một host/VPS độc lập.

---

## 6. Hướng dẫn Backup & Rollback

### Upgrade & Rollback Database Migration
```bash
# Chạy migration lên head
cd backend && .venv/bin/alembic upgrade head

# Rollback riêng phiên bản AI SRE v2 về 0002 nếu cần
cd backend && .venv/bin/alembic downgrade 0002_member_username_reuse
```

### Backup SQLite / PostgreSQL trước khi nâng cấp
```bash
sqlite3 backend/data/labops_v2.sqlite3 ".backup backend/data/labops_v2.backup.$(date +%Y%m%d%H%M).sqlite3"
```

---

## 7. Mẫu Daily Executive Briefing & Repository Guard Alert (Từ dữ liệu test)

### Mẫu Daily Executive Briefing (08:00 ICT)
```text
📊 DAILY BRIEF (2026-11-23 • 08:00 ICT)
• Health: DEGRADED | Node Online: 1/2 | P0: 0 | Action: 1
• Infra 24h: Avail 100.0% | CPU Peak 42.3% | RAM Peak 18.4 GiB
• GPU & Jobs: 1/1 busy (68.0%) | Run/Ok/Fail: 1/2/0
• Storage & WS: Growth +1.20 GB | WS Active 2/2 (Quota warn: 0)
• Security 24h: Clones 2 (Review: 1, ScanFail: 0) | SecInc: 1
💡 Insights:
  - Node mất kết nối/thiếu telemetry: aiotlab-rack (OFFLINE).
  - Trong 24h có 2 lượt clone kho mã: 1 cần kiểm duyệt, 0 lỗi quét.
🎯 Priorities:
  1. [P1] Repo TrangLe1912/risk-constrained-endpointing (@tranglt) cần kiểm duyệt sau clone
  2. Kiểm tra kết nối các node ngoại tuyến: aiotlab-rack (OFFLINE).
👉 https://aiotserver.fitdnu.id.vn/dashboard/ai-assistant
```

### Mẫu Repository Guard Security Alert (Gửi riêng Admin)
```text
⚠️ [CẢNH BÁO KIỂM DUYỆT REPO SAU CLONE] (inc-a1b2c3d4e5)
• Thành viên: @tranglt (ws-tranglt)
• Kho mã: TrangLe1912/risk-constrained-endpointing (401ad754)
• Trạng thái Clone: Thành công
• Kiểm duyệt sau Clone: Cần Admin kiểm duyệt
• Tổng hợp: 1 Secret | 0 Critical CVE | 2 High CVE | 0 Misconfig
  - [SECRET/HIGH] generic-api-key (configs/secret.env:3)
👉 Security Center: https://aiotserver.fitdnu.id.vn/dashboard/security
```

---

## 8. Ma trận Kiểm chứng & Hạn chế còn tồn tại

| Hạng mục | Trạng thái kiểm chứng | Ghi chú |
| :--- | :--- | :--- |
| DB Migration (`0003_labops_ai_sre_agent_v2`) | Tested (SQLite upgrade/downgrade) | PostgreSQL production DDL tương thích SQLAlchemy 2.x nhưng chưa chạy trực tiếp trên cụm PostgreSQL thật. |
| Identity RBAC, Outbox Idempotency, Lease | Unit & Integration Tested | Đã kiểm chứng spoof rejection, timeout `DELIVERY_UNKNOWN`, duplicate prevention, lease failover. |
| Telemetry, Incident Engine & Daily Brief | Unit & Integration Tested | Đã test hysteresis, OOM routing, no false GPU alert, 08:00 ICT window, AI offline fallback. |
| Repository Guard Alerting | Unit Tested + Live Zalo verified (trên bản v1 trước nâng cấp) | Retry per-recipient và Admin-only filter đã verify qua pytest. |
| Server Rack (`aiotlab-rack`) Live Telemetry | **Not Verified (Node Offline)** | Node Rack hiện ngoại tuyến vật lý tại thời điểm triển khai. |
| Zalo Provider Quota Ceiling (`3,000/mo`) | Internal Tracking Only | Provider Zalo API không trả về remaining monthly quota; con số 3,000 là cấu hình nội bộ LabOps. |
