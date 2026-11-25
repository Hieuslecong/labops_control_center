# LabOps Personal AI Agent V4 — Data Retention & Maintenance Policy

**Implementation:** [retention_service.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/assistant_v4/retention_service.py), [`RetentionV4Config`](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/assistant_v4/config.py), [assistant_v4.example.yaml](file:///home/aiot-ws1/projects/labops_control_center/config/assistant_v4.example.yaml)  
**Default Posture:** **Feature-flagged OFF (`ASSISTANT_V4_RETENTION_ENABLED=false`)** and **Dry-Run (`ASSISTANT_V4_RETENTION_DRY_RUN=true`)**.

---

## 1. Execution Safety Guarantees

1. **Opt-In + Dry-Run Default:** Automatic scheduling inside `AssistantV4Facade.run_worker_cycle()` executes only when `retention.enabled = true`, respecting `run_interval_hours = 24`. Manual admin trigger `POST /api/v1/ai-assistant/v4/retention/run` defaults to `dry_run = true` when automated retention is disabled unless explicitly overridden by an authenticated Admin.
2. **Bounded Short Transactions:** Deletes at most `batch_size = 500` IDs per transaction and at most `max_batches_per_run = 10` batches per category per run so SQLite WAL writer locks stay short (`< tens of ms`).
3. **No Routine `VACUUM`:** `RetentionMaintenanceService` **never** runs SQLite `VACUUM` or exclusive database rebuilds during normal production operation (`vacuum_executed = false`).
4. **Dedupe & Replay Safety Window:** Every idempotency/dedupe/replay table enforces `cutoff = min(now - category_days, now - min_dedupe_safety_hours)` where `min_dedupe_safety_hours = 168` (`7` days minimum retention regardless of lower settings).
5. **Foreign-Key-Aware Child-First Ordering:** Child tables are strictly pruned before parent records only after verifying parent/child safety predicates.

---

## 2. Category Retention Matrix & Preservation Rules

| Order | Table | Default Age Window | Eligible States | Strictly Preserved (Never Deleted) |
|---|---|---|---|---|
| 1 | `v4_chat_messages` | `7 days` | Messages in inactive sessions or inactive beyond `7d` | Messages in `ACTIVE` sessions active within `7d` |
| 2 | `v4_chat_sessions` | `30 days` | `status != 'ACTIVE'` AND `0` remaining `v4_chat_messages` | `ACTIVE` sessions or sessions with messages remaining |
| 3 | `v4_delivery_attempts` | `30d` (>= `168h`) | `result_class != 'IN_FLIGHT'` AND parent not active | `IN_FLIGHT` attempts; attempts tied to `PENDING`, `CLAIMED`, `FAILED_RETRYABLE`, `UNKNOWN_DELIVERY` |
| 4 | `v4_notification_outbox` | `30d` (>= `168h`) | `SENT`, `DLQ`, `SUPPRESSED`, `EXPIRED` AND `0` remaining attempts | `PENDING`, `CLAIMED`, `FAILED_RETRYABLE`, `UNKNOWN_DELIVERY`; rows inside `168h` safety window |
| 5 | `v4_message_inbox` | `30d` (>= `168h`) | `PROCESSED`, `FAILED`, `IGNORED` | `RECEIVED`, `CLAIMED`; rows inside `168h` dedupe window |
| 6 | `v4_action_proposals` | `90d` (>= `168h`) | `SUCCEEDED`, `REJECTED`, `EXPIRED`, `REVOKED`, `FAILED`, `UNKNOWN_OUTCOME` | `PROPOSED`, `PENDING_CONFIRMATION`, `CONFIRMED`, `EXECUTING`; rows inside `168h` safety window |
| 7 | `v4_watch_rules` | `90 days` | `COMPLETED`, `CANCELLED`, `EXPIRED` | `ACTIVE` watch rules |
| 8 | `v4_assistant_tasks` | `90 days` | `COMPLETED`, `CANCELLED`, `EXPIRED` AND `0` remaining watch rules | `ACTIVE`, `WAITING_CONDITION`, `RUNNING` tasks |
| 9 | `v4_webhook_deliveries` | `30d` (>= `168h`) | Past `webhook_deliveries_days` & `min_dedupe_safety_hours` | Recent deliveries inside safety dedupe window |
| 10 | `v4_event_store` | `90d` (>= `168h`) | **Only consumed events** (`EXISTS` receipt in `v4_event_consumer_receipts`) | **Unconsumed events**; recent events inside `168h` |
| 11 | `v4_event_consumer_receipts` | `90d` (>= `168h`) | **Only after parent removed** from `v4_event_store` | Any receipt whose parent event still exists in `v4_event_store` |

---

## 3. Permanently Protected Immutable Tables

`RetentionMaintenanceService.PROTECTED_IMMUTABLE_TABLES` explicitly excludes and **never deletes** rows from:

- Security & compliance: `audit_events`
- Incidents & investigations: `incidents`, `incident_events`, `v4_investigations`, `v4_incident_meta`
- Identity, MRAM & workspaces: `members`, `member_profiles`, `member_web_credentials`, `workspaces`, `server_access_requests`
- Legacy V2 telemetry & outbox: `telemetry_history_samples`, `notification_outbox`, `notification_delivery_attempts`
- Curated knowledge & configuration state: `v4_knowledge_documents`, `v4_user_preferences`, `v4_worker_cursors`, `v4_connector_states`

---

## 4. Maintenance Audit & Growth Monitoring

- Every retention run (dry-run or live) writes:
  1. `AuditEventRecord(action="V4_RETENTION_MAINTENANCE", target_type="DATABASE", target_id="v4_tables", result="DRY_RUN"|"EXECUTED", details=...)` containing deleted/candidate counts and DB footprint delta.
  2. `V4WorkerCursorRecord("retention:last_run")` with per-category counts and SQLite main/WAL/SHM byte sizes before and after execution.
- Admin observability: exposed in `GET /api/v1/ai-assistant/v4/metrics` under `retention` and `database` (`db_size_bytes`, `wal_size_bytes`, `shm_size_bytes`, `total_bytes`, `sqlite_lock_errors`).
- If SQLite growth plateaus after Stage 4–5 and disk compaction is ever desired during an approved maintenance window, schedule a manual offline backup + stopped-writer `VACUUM` rather than runtime vacuuming.
