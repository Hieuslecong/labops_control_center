# LabOps Personal AI Agent V4 — Production Preflight & Database Safety Report

**Audit Timestamp:** `2026-11-25T23:17Z` (ICT / `UTC+7`)  
**Repository & Branch:** `/home/aiot-ws1/projects/labops_control_center` on branch `feature/labops-ai-sre-agent-v2` (base `HEAD`: `c1e3af18c31e6c8fa982d1958a1c83863585bcb9`)  
**Live Production Status:** Untouched (`alembic_version = 0003_labops_ai_sre_agent_v2`; live DB file `size = 44,752,896 bytes`, `mtime = 2026-11-25 22:58:06 ICT`).

---

## 1. Effective Runtime `DATABASE_URL` & Current Working Directory Hazard

Read-only inspection of `.env.local`, [session.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/db/session.py), [env.py](file:///home/aiot-ws1/projects/labops_control_center/backend/alembic/env.py), and [deploy-zero-downtime.sh](file:///home/aiot-ws1/projects/labops_control_center/scripts/deploy-zero-downtime.sh):

| Check | Verified Observation | Risk & Mitigation |
|---|---|---|
| Configured `.env.local` key | `DATABASE_URL=sqlite:///backend/data/labops_v2.sqlite3` *(relative path; `SQLITE_URL_IS_ABSOLUTE=False`; credentials redacted)* | **CRITICAL CWD HAZARD:** Running `alembic` or Python from inside `backend/` with raw relative `.env.local` resolves to `backend/backend/data/labops_v2.sqlite3` (`EXISTS=False`)! |
| Authoritative Absolute Path | `/home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3` (permissions `0600`, WAL + SHM sidecars present) | Always pass explicit `DATABASE_URL=sqlite:////home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3` in operational commands. Additionally hardened `backend/alembic/env.py::get_database_url()` and added `resolve_sqlite_abs_path()`. |
| Running Backend Slot | `.runtime/active-slots.json`: `frontendSlot=green` (`:3002`), `backendSlot=green` (`:8002`) proxied behind `:3000` / `:8000` | Launched from repo root (`deploy-zero-downtime.sh` `cd "$PROJECT_DIR"`). Active writer on live SQLite. |
| Running SRE Worker | `backend/data/zalo_worker_status.json`: PID `3568760` (`lease_owner=worker-3568760-e5619f`, `is_polling=true`, `Bot Aiot Server AI Assistant`) | Active polling & outbox writer holding DB lease `zalo_sre_worker`. Must be quiesced during offline migration/restore! |
| SQLite Mode & Pragmas | `PRAGMA journal_mode = wal`, `PRAGMA foreign_keys = ON`, `PRAGMA busy_timeout = 5000` | WAL requires copying `-wal` / `-shm` or using `sqlite3.Connection.backup()` API. Never copy main `.sqlite3` with raw `cp` while active. |

---

## 2. Live Database Schema Drift Inventory (Read-Only Inspection)

Inspected via read-only URI `file:/home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3?mode=ro` and compared against clean Alembic `0004_labops_mram_v1` / `0005_labops_personal_ai_v4`:

1. **`alembic_version`:** `0003_labops_ai_sre_agent_v2` (stale relative to tables present).
2. **Legacy Table Inventory (`32` non-alembic tables, `8,508` total rows):** All `0001`–`0003` tables PLUS all `11` MRAM `0004` tables (`member_web_credentials`, `member_profiles`, `supervisor_assignments`, `server_access_requests`, `request_resource_recommendations`, `approval_records`, `approval_invitations`, `capacity_reservations`, `provisioning_operations`, `credential_claims`, `policy_acceptances`) already exist in the live DB from earlier `Base.metadata.create_all()` startup execution.
   - Known `0004` drift: ORM `create_all()` columns omit SQLite `server_default` literals (`None` vs `'scrypt'`/`'0'`/`'mram-v1.0'`) and use ORM auto-generated index names (`ix_policy_acceptances_member_id` vs Alembic `ix_policy_acc_member_id`). Both `0004_labops_mram_v1.py` and `0005_labops_personal_ai_v4.py` safely guard `if "tbl" not in tables:`.
3. **V4 Pre-Creation Drift Observation:**
   - Earlier in the audit session (at `22:58`, before live-DB read-only isolation was strictly enforced for ad-hoc commands), importing `app.main` / `ZaloAssistantService()` outside pytest's isolated `conftest.py` environment ran `DatabaseManager.init_schema()` (`Base.metadata.create_all()`) against the default SQLite path.
   - As a result, the `17` `v4_*` tables already exist in `backend/data/labops_v2.sqlite3` (`16` tables have `0` rows; `v4_knowledge_documents` holds the `3` default baseline docs `kb-ssh-docker-guide-v1`, `kb-cuda-oom-runbook-v1`, `kb-admin-incident-runbook-v1` inserted by `KnowledgeBaseService.bootstrap_defaults()`).
   - Comparing live `v4_*` schema against clean Alembic `0005` revealed missing Alembic index names (`ix_v4_chat_sess_principal`, `ix_v4_del_att_outbox`, `ix_v4_event_store_corr`, `ix_v4_event_store_resource`, `ix_v4_inc_meta_parent`, `ix_v4_inc_meta_rem`, `ix_v4_inv_incident`, `ix_v4_outbox_recipient`, `ix_v4_watches_owner`, `ix_v4_wh_conn`) and ORM-vs-Alembic `server_default` differences (`None` vs Alembic literals).
   - **Remediation Applied:** Updated [`0005_labops_personal_ai_v4.py`](file:///home/aiot-ws1/projects/labops_control_center/backend/alembic/versions/0005_labops_personal_ai_v4.py) with `_ensure_index()` outside table-existence checks so explicit migration to `0005_labops_personal_ai_v4` idempotently creates every missing V4 index without failing, while existing [`backend/tests/conftest.py`](file:///home/aiot-ws1/projects/labops_control_center/backend/tests/conftest.py) keeps pytest isolated in session temp directories (`LABOPS_DATA_DIR`, `LABOPS_USER_DATA_DIR`, `LABOPS_SESSION_SECRET`).

---

## 3. Isolated Production-Copy WAL Backup & Full-Content SHA-256 Rehearsal

Executed on an isolated disposable snapshot (`/tmp/v4_rehearsal_*`, mode `0700`, removed after verification; **live DB never written**):

1. **Online WAL-Consistent Backup via Python `sqlite3.backup()` API:**
   - Source opened strictly read-only (`mode=ro`); backup file permission restricted to `0600`.
   - Observed backup size: `44,965,888` bytes.
   - `PRAGMA integrity_check`: `ok`.
   - Backup SHA-256 checksum: `d6d8a88122096b2e47aefd167c373afe213fa9d4da847e75341a7b5df7f74e8b`.
2. **Explicit Target Migration (`0005_labops_personal_ai_v4`, NOT arbitrary `head`):**
   - Command: `alembic -c backend/alembic.ini upgrade 0005_labops_personal_ai_v4` with absolute `DATABASE_URL=sqlite:////...` -> Exit code **`0`**.
   - Revision transition logged: `0003_labops_ai_sre_agent_v2 -> 0004_labops_mram_v1 -> 0005_labops_personal_ai_v4`.
   - Post-upgrade `alembic_version`: `[('0005_labops_personal_ai_v4',)]`, `PRAGMA integrity_check = ok`, `PRAGMA foreign_key_check = 0 errors`, `77` V4 indexes present.
3. **Full Deterministic Row-by-Row Content SHA-256 Verification (All `32` Legacy Tables):**
   - Computed SHA-256 over column headers + **every single row (`ORDER BY rowid ASC`) across all 32 legacy tables (`8,508` rows total**, including every `telemetry_history_samples` row — no row-count or `MIN/MAX(rowid)` shortcut).
   - Post-upgrade comparison against pre-upgrade SHA-256 digests: **`CONTENT_DIFFS: {}`** (`0` differing tables, `8,508 / 8,508` rows byte-identical).
4. **Restore Compatibility Check:**
   - Restoring backup over the upgraded copy and re-hashing all `32` legacy tables (`8,508` rows): **`RESTORE_DIFFS: {}`**, `0` differences.

---

## 4. Safe Writer Handling Rules for Production Migration & Restore

1. **Quiesce All Writers Before Live DDL or Restore:**
   - Stop `labops-ai-sre-worker` (`systemctl stop labops-ai-sre-worker` or terminate worker PID) and stop active backend slots (`:8001`/`:8002`) during Stage 2/3 maintenance before replacing or migrating the production SQLite file.
   - Confirm zero open writers using `lsof /home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3*` (or `fuser`).
2. **Never Overwrite Live DB With Active Writers or Stale Sidecars:**
   - Before replacing `labops_v2.sqlite3` during a restore, checkpoint/quiesce writers and remove any stale `-wal` and `-shm` files from the failed database before moving the verified `0600` backup into place (otherwise SQLite replays old WAL pages on top of the restored base file and corrupts it!).
3. **Never Silently Discard Post-Backup Writes:**
   - Because backup is taken **after** stopping writers (or verified zero delta since pre-quiesce backup), no concurrent writes occur during migration. If any emergency write were ever to occur between backup and a later rollback restore, an operator **must explicitly authorize** either exporting/replaying those delta rows or accepting loss of the post-backup window before restoring.
