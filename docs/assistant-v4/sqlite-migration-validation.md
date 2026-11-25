# LabOps Personal AI Agent V4 — SQLite Schema & Migration Validation Report

**Audit Date:** 2026-11-25 (ICT / UTC+7)  
**Production Database:** `DATABASE_URL=sqlite:///backend/data/labops_v2.sqlite3` (verified read-only; **never modified**)  
**Target Migration:** `0005_labops_personal_ai_v4` (`down_revision = 0004_labops_mram_v1`)

---

## 1. Live Production Database Configuration & Schema Drift Audit (Read-Only)

Inspected via read-only SQLite URI (`file:backend/data/labops_v2.sqlite3?mode=ro`) and online backup snapshot:

- **Configured Backend Dialect:** SQLite (`DATABASE_URL=sqlite:///backend/data/labops_v2.sqlite3` in `.env.local`).
- **Observed File Footprint:** Main DB `44,220,416` bytes (`42.17 MiB`), plus WAL (`~4.0M`) & SHM (`32K`) at snapshot time.
- **SQLite Pragmas & Integrity:** `PRAGMA journal_mode = wal`; `PRAGMA integrity_check = ok`.
- **Table Count:** `33` tables present (`alembic_version`, core LabOps V2 tables, AI SRE v2 tables, and all 11 MRAM tables).
- **Key Production Row Counts:**
  - `telemetry_history_samples`: `8,002`
  - `audit_events`: `232`
  - `deletion_operations`: `32`
  - `members`: `28`, `member_profiles`: `23`, `member_web_credentials`: `6`
  - `server_access_requests`: `4`, `request_resource_recommendations`: `4`, `policy_acceptances`: `4`, `approval_invitations`: `8`
  - `incidents`: `4`, `incident_events`: `6`
  - `notification_outbox`: `16`, `notification_delivery_attempts`: `10`, `notification_subscriptions`: `2`, `zalo_identity_links`: `1`
  - `daily_report_snapshots`: `4`, `report_runs`: `4`, `agent_tool_audit`: `39`, `revoked_tokens`: `18`, `scheduler_leases`: `1`

> [!WARNING]
> **Critical Pre-Existing Schema Drift Found:**  
> Live `alembic_version` currently reads `0003_labops_ai_sre_agent_v2`, **even though all 11 tables introduced by `0004_labops_mram_v1` already exist in the live SQLite file!**  
> Because `backend/alembic/versions/0004_labops_mram_v1.py` defensively checks `if "<table_name>" not in tables:` before creating each table, running `alembic upgrade head` advances cleanly from `0003` → `0004` → `0005` without failing or altering existing MRAM tables/data — **as verified in the isolated rehearsal below**. Nevertheless, pre-upgrade backup and post-upgrade verification remain mandatory.

---

## 2. Isolated Migration Rehearsal (`sqlite3.backup()` Copy in `/tmp/v4_migration_rehearsal`)

All destructive/DDL checks ran exclusively against a clean WAL-consistent `sqlite3.backup()` copy of the live database (never touching `backend/data/labops_v2.sqlite3`).

### 2.1 Upgrade Correctness (`0003` → `0004` → `0005`)
- **Command:** `DATABASE_URL=sqlite:///.../labops_v2_rehearsal.sqlite3 alembic -c alembic.ini upgrade head`
- **Exit Code:** `0` (`901.2 ms`)
- **Version Transition:** `0003_labops_ai_sre_agent_v2` → `0004_labops_mram_v1` → `0005_labops_personal_ai_v4`
- **Table Count:** `33` → `50` (`+17` strictly additive `v4_*` tables)
- **File Size Delta (empty V4 tables):** `44,220,416` → `44,503,040` bytes (`+282,624` bytes / `+276 KiB`)
- **Idempotent Re-Upgrade (`alembic upgrade head` 2nd run):** Exit Code `0` (clean no-op)
- **Post-Upgrade Integrity Check:** `PRAGMA integrity_check = ok`

### 2.2 Legacy Record Preservation (Byte-for-Byte Verification)
Every one of the **32 non-`alembic_version` legacy tables** was compared before and after migration using exact row counts and SHA-256 content hashes (or `(MIN(rowid), MAX(rowid), COUNT(*))` checksum on large tables):
- **Legacy Table Diffs:** `[]` (**0 altered/missing rows across all 32 tables**, including `members`, `server_access_requests`, `incidents`, `zalo_identity_links`, `notification_outbox`, `audit_events`, and `telemetry_history_samples`).

### 2.3 Required V4 Indexes & Unique Constraints Verified
Inspected via `PRAGMA index_list(...)` / `PRAGMA index_info(...)` on the upgraded copy:
- `v4_event_store`: `UNIQUE(source, source_event_id)`, `ix_v4_event_type_observed(event_type, observed_at)`, `ix_v4_event_store_resource(resource_id)`, `ix_v4_event_store_corr(correlation_id)`.
- `v4_event_consumer_receipts`: `UNIQUE(consumer, event_id)`, `ix_v4_receipt_consumer_res(consumer, resource_id, observed_at)`.
- `v4_message_inbox`: `UNIQUE(channel, bot_id, external_message_id)`, `ix_v4_inbox_state_lease(state, lease_until)`.
- `v4_notification_outbox`: `UNIQUE(dedupe_key)`, `ix_v4_outbox_claim(status, priority, not_before)`, `ix_v4_outbox_lease(status, lease_until)`, `ix_v4_outbox_recipient(recipient_id)`.
- `v4_delivery_attempts`: `ix_v4_del_att_outbox(outbox_id)`.
- `v4_incident_meta`: `ix_v4_inc_meta_parent(parent_incident_id)`, `ix_v4_inc_meta_rem(next_reminder_at)`.
- `v4_assistant_tasks`: `UNIQUE(idempotency_key)`, `ix_v4_tasks_due(status, next_run_at)`, `ix_v4_tasks_owner(owner_id, status)`.
- `v4_watch_rules`: `ix_v4_watches_status_target(status, target_id)`, `ix_v4_watches_owner(owner_id)`.
- `v4_action_proposals`: `UNIQUE(idempotency_key)`, `ix_v4_proposals_actor_state(actor_id, state, expires_at)`.
- `v4_chat_sessions`: `UNIQUE(scope_key)`, `ix_v4_chat_sess_principal(principal_id, last_active_at)`.
- `v4_chat_messages`: `ix_v4_chat_msg_session_seq(session_id, seq)`.
- `v4_knowledge_documents`: `ix_v4_kb_scope_status(scope, status)`.
- `v4_investigations`: `UNIQUE(episode_key)`, `ix_v4_inv_incident(incident_id)`.
- `v4_webhook_deliveries`: `UNIQUE(connector_id, delivery_id)`, `ix_v4_wh_conn(connector_id)`.
- Plus PK autoindexes on `v4_worker_cursors`, `v4_user_preferences`, `v4_connector_states`.

---

## 3. SQLite WAL Concurrency, Legacy Compatibility & Rollback Verification

| Check | Test Method on Isolated Upgraded Copy | Observed Result |
|---|---|---|
| **SQLAlchemy WAL Pragmas** | `DatabaseManager` engine inspection | `journal_mode='wal'`, `busy_timeout=5000`, `foreign_keys=1` (10s connect timeout). |
| **Concurrent CAS Locking** | **6 concurrent worker threads** claiming **500 `v4_notification_outbox` rows** (`batch_size=15`) via optimistic CAS (`WHERE notification_id=... AND fence=...`) | **500 / 500 claimed**, `unique_claimed = 500`, **`duplicates = 0`**, **`errors = []`** (zero `SQLITE_BUSY` / lock failures). |
| **Legacy Compat (Flags OFF)** | Instantiated `UnifiedIncidentEngine`, `ZaloIdentityRBACService`, `NotificationOutboxRecord` queries, and `AssistantV4Facade` with all `ASSISTANT_V4_*` = `false` on upgraded copy | Read `4` incidents, `1` Zalo link, `2` subscriptions, `16` legacy outbox rows cleanly; `v4.run_worker_cycle()` returned `{"enabled": False}` (dormant). |
| **Backup & Restore** | Pre-migration `sqlite3.backup()` snapshot restored & verified | `integrity = ok`, `alembic_version = [('0003_labops_ai_sre_agent_v2',)]`, `diffs = []`. |
| **Schema Downgrade (`0005` → `0004`)** | `alembic downgrade 0004_labops_mram_v1` on upgraded clone | Exit Code `0`, `table_count = 33`, `integrity = ok`, `legacy_diffs = []` (only `v4_*` tables dropped). |
| **Operational Flag Rollback (`R02`)** | Tested in `test_r02_flag_rollback_preserves_pending_tasks_without_duplicates` | Setting `ASSISTANT_V4_ENABLED=false` halts V4 worker execution immediately without dropping `v4_*` state or causing duplicate sends. |

---

## 4. Storage Growth & Retention Assessment

- **Measured Growth Benchmark:** Inserting `500` synthetic `v4_event_store` rows + `500` `v4_notification_outbox` rows and running `PRAGMA wal_checkpoint(TRUNCATE)` increased the isolated DB by `487,424` bytes (`~974.8` bytes per event+outbox pair).
- **Projected Annual Growth (Low-Volume Lab Estimate):**
  - At ~200 events + 100 outbox rows + 150 chat messages per day (`~100–250 KiB/day`), unmanaged V4 tables would add roughly `35–90 MiB/year` (very small relative to disk capacity, though unbounded over multi-year runs).
- **Gap Identified (Operational Action Needed Before Long-Term Unattended Production):**
  - Spec §5 defines retention targets (`7d` raw chat unless stricter, `30d` delivery attempts, `90d` redacted technical events), **but no automated periodic retention/pruning job is currently implemented in V4** (`docs/assistant-v4/deployment-and-rollback.md` did not call this out).
  - **Recommendation:** Keep V4 retention disabled until Stage 4/5 soak confirms growth rate, then schedule an explicit batched archive/prune maintenance task (only on explicitly authorized V4 tables; **never** prune `audit_events` or `incidents` without compliance sign-off).

---

## 5. Temporary Artifacts & Live Safety Confirmation

- All rehearsal files in `/tmp/v4_migration_rehearsal` were removed after verification.
- Live `backend/data/labops_v2.sqlite3` timestamp and contents remain completely untouched (`alembic_version` remains `0003_labops_ai_sre_agent_v2` pending explicit production maintenance window authorization).
