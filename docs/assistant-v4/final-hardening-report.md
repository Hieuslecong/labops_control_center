# LabOps Personal AI Agent V4 — Final Hardening Report

- **Repository**: `/home/aiot-ws1/projects/labops_control_center`
- **Branch**: `feature/labops-ai-sre-agent-v2`
- **Baseline HEAD**: `c1e3af18c31e6c8fa982d1958a1c83863585bcb9`
- **Final Classification**: `CONDITIONAL` (automated hardening and isolated verification complete; external production rollout and live staging gates remain pending operator authorization)

## 1. Executive Summary

Following the independent audit, targeted hardening was implemented and verified without redesigning the V4 architecture, replacing `NativeAgentRuntime`, introducing unnecessary dependencies, or touching Workload Intelligence V3.1, live `.env.local`, live SQLite data (`backend/data/labops_v2.sqlite3`), systemd services, network/VPN/SSH/firewall configuration, GPU allocations, Docker workspaces, or existing member accounts.

All post-edit code verification suites passed cleanly (`49 passed` V4 suite, `315 passed, 2 skipped` full backend suite with the 2 pre-existing hardware skips, `13/13` Go test packages, `9/9` frontend tests plus `typecheck`, `lint`, and `bun run build`), alongside an isolated production-copy SQLite backup, explicit `0005_labops_personal_ai_v4` migration, and byte-level restore rehearsal across all `32` legacy tables (`8,508` rows intact).

## 2. Hardening Implemented by Area

### 2.1 Shadow-Mode Semantics & Notification Ownership Resolution
- **`backend/app/services/assistant_v4/notification_policy.py`**:
  - Added `resolve_notification_owner(config)` returning `"V2_LEGACY"`, `"V2_LEGACY_SHADOW"`, or `"V4_ACTIVE"` so `shadow_mode=True` strictly keeps V2 as the sole external delivery owner even when `notification_v2=True`.
  - Added `_record_shadow_decision()` to persist structured `shadow:policy:metrics` evaluations (read-only quiet hours, category, and quota checks), zero real Zalo quota consumption, `SHADOW_DRY_RUN` receipts, and `V4IncidentMetaRecord.shadow_only`.
- **`backend/app/services/assistant_v4/task_scheduler.py`**:
  - Added `shadow_mode: bool = False` to `PersistentScheduler.tick()`, suppressing outbox enqueues during shadow-mode execution.
- **`backend/app/services/zalo_assistant/worker.py`** & **`backend/app/services/mram_service.py`**:
  - Switched notification ownership gating (`_run_v4_worker_step`, `suppress_v2_enqueue`, `suppress_direct_send`, and MRAM alert routing) to authoritative `resolve_notification_owner(...) == "V4_ACTIVE"`.
- Verification: Covered by `test_shadow_mode_*` and `test_v2_v4_no_double_enqueue_in_worker_and_mram` in `backend/tests/test_assistant_v4_final_hardening.py`.

### 2.2 V2-to-V4 Notification Cutover & Rollback Protocol
- **`backend/app/services/assistant_v4/notification_policy.py`**:
  - Added `sync_notification_ownership()` managing the `notification_cutover:state` cursor, writing `"V4_NOTIFICATION_OWNERSHIP_CHANGED"` to `AuditEventRecord`, resynchronizing shadow reminders, marking pre-cutover backlog items with `SKIPPED_PRE_CUTOVER` receipts, and setting `skip_reconciled_before` anti-backlog guards.
  - Updated `DigestSchedulerV4.evaluate_due_digests()` to deduplicate legacy V2 daily (`report_runs`) and weekly (`notification_outbox`) schedules with `COVERED_BY_V2_LEGACY` receipts during handover.
- **`backend/app/services/zalo_assistant/repo_alert_monitor.py`**:
  - Added `suppress_direct_send: bool = False` to `check_and_notify()`, continuing `_record_incident_for_alert()` ingestion into `v4_event_store` while skipping direct V2 sends and recording `DELEGATED_TO_V4_POLICY` when V4 is active.
- **`backend/app/services/assistant_v4/facade.py`**:
  - Added `prepare_notification_cutover()` and wired `sync_notification_ownership()` inside `run_worker_cycle()`.
- **Delivery Guarantee Boundary**: Because the upstream Zalo Bot Platform API does not support client-side idempotency keys, `UNKNOWN_DELIVERY` outcomes represent bounded at-least-once delivery semantics rather than claimed exactly-once external delivery.
- **Runbook Reference**: `docs/assistant-v4/notification-cutover-protocol.md`.
- Verification: Covered by `test_notification_cutover_*` and `test_notification_ownership_rollback_*`.

### 2.3 SQLite WAL Safety, Schema Drift Hardening & Migration Rehearsal
- **`backend/app/db/session.py`**:
  - Added `resolve_sqlite_abs_path(database_url)`, thread-safe `_metrics_lock` + `_sqlite_lock_errors` counter catching SQLite `"locked"` / `"busy"` `OperationalError` events in `DatabaseManager.session_scope()`, and `DatabaseManager.get_db_metrics()` (`dialect`, `sqlite_path_abs`, `db_size_bytes`, `wal_size_bytes`, `shm_size_bytes`, `total_bytes`, `sqlite_lock_errors`, `journal_mode`).
- **`backend/alembic/env.py`**:
  - Hardened `get_database_url()` so relative `sqlite:///backend/...` paths resolve relative to repository root if absent under CWD (`backend/`).
- **`backend/alembic/versions/0005_labops_personal_ai_v4.py`**:
  - Added `_ensure_index(insp, index_name, table_name, columns)` outside `if "<tbl>" not in tables:` blocks across all `v4_*` tables so pre-existing ORM-created `v4_*` drift tables receive every required Alembic index cleanly during explicit `alembic upgrade 0005_labops_personal_ai_v4`.
- **Isolated Production-Copy Rehearsal**:
  - Read-only online `sqlite3.backup()` snapshot (`0o600`, `44,965,888` bytes, `PRAGMA integrity_check = [('ok',)]`, SHA-256 `d6d8a88122096b2e47aefd167c373afe213fa9d4da847e75341a7b5df7f74e8b`).
  - Explicit `alembic -c backend/alembic.ini upgrade 0005_labops_personal_ai_v4` exited `0` (`alembic_version = [('0005_labops_personal_ai_v4',)]`, `PRAGMA integrity_check = ok`, `FK_ERRORS = 0`, `V4_INDEX_COUNT = 77`).
  - Row-by-row SHA-256 comparison (`ORDER BY rowid ASC` across all `32` legacy tables, `8,508` rows total): `CONTENT_DIFFS: {}` and `RESTORE_DIFFS: {}`.
- **Test Isolation Restoration**: Verified `HEAD` `backend/tests/conftest.py` already isolates `LABOPS_DATA_DIR`, `LABOPS_USER_DATA_DIR`, and `LABOPS_SESSION_SECRET`, restored exact `HEAD`, and re-ran all V4 and full backend test suites afterward.
- **Runbook Reference**: `docs/assistant-v4/production-preflight.md`.

### 2.4 Data Retention & Database Growth Control
- **`backend/app/services/assistant_v4/config.py`** & **`config/assistant_v4.example.yaml`**:
  - Added `RetentionV4Config` (`enabled=False`, `dry_run=True`, `run_interval_hours=24`, `batch_size=500`, `max_batches_per_run=10`, `min_dedupe_safety_hours=168`, per-table retention days) plus env overrides `ASSISTANT_V4_RETENTION_ENABLED` and `ASSISTANT_V4_RETENTION_DRY_RUN`.
- **`backend/app/services/assistant_v4/retention_service.py`**:
  - Implemented `RetentionMaintenanceService` with batch-bounded short transactions, FK child-first deletion order, zero `VACUUM`, `min_dedupe_safety_hours` enforcement, preservation of pending/retryable/claimed/unknown/in-flight/active/unconsumed records and `PROTECTED_IMMUTABLE_TABLES`, `V4_RETENTION_MAINTENANCE` audit events, `retention:last_run` cursor tracking, and `get_status()`.
- **`backend/app/api/v1/endpoints/ai_assistant.py`**, **`src/app/api/labops/_lib.ts`**, **`src/__tests__/milestone3-frontend-hardening.test.ts`**:
  - Exposed Admin-only `POST /api/v1/ai-assistant/v4/retention/run` (defaults `dry_run=True`) with matching Next.js BFF admin RBAC enforcement and unit coverage.
- **Documentation**: `docs/assistant-v4/retention-policy.md`.

### 2.5 SSRF Defense, Principal Revocation Cascading & Crash Recovery
- **`backend/app/services/assistant_v4/skills_and_connectors.py`**:
  - Hardened `SafeOutboundGuard._is_forbidden_ip()` to reject zone IDs (`%`), unwrap IPv4-mapped `::ffff:...`, `sixtofour`, and `teredo` addresses, and block CGNAT (`100.64.0.0/10`), benchmarking (`198.18.0.0/15`), NAT64 (`64:ff9b::/96`, `64:ff9b:1::/48`), ULA (`fc00::/7`), link-local (`fe80::/10`), and any `not ip.is_global` IP.
  - Restricted `validate_url()` strictly to HTTPS port `443`, enforced bounded timeouts `[0.5, 30.0]` seconds in `fetch_safe_url()` (`follow_redirects=False`), and exposed `request_timeout_s: float = 10.0` via `ExternalConnectorsConfig` and `ConnectorsService`.
- **`backend/app/services/assistant_v4/memory_service.py`**, **`inbox_outbox.py`**, **`approval_service.py`**, **`zalo_assistant/identity_rbac.py`**, **`facade.py`**:
  - Enforced `s_rec.status == "ACTIVE"` in `get_bounded_context()` (`PermissionError("SESSION_NOT_ACTIVE:...")`) and added `revoke_principal_sessions()`.
  - Added `OutboxV4Service.suppress_recipient_pending(recipient_ids, reason="PRINCIPAL_REVOKED")`.
  - Added `revoke_actor_proposals()` and `reconcile_stuck_executing()` crash reconciliation in `approval_service.py` (inspecting core `IncidentRecord` / `ServerAccessRequestRecord` state without blind re-execution, transitioning to `SUCCEEDED` or `UNKNOWN_OUTCOME` with `V4_ACTION_CRASH_RECONCILED` audit).
  - Updated `revoke_link()` in `identity_rbac.py` to cascade inside a `SAVEPOINT` across `V4ChatSessionRecord`, `V4WatchRuleRecord`, `V4ActionProposalRecord`, `V4NotificationOutboxRecord`, and `V4_PRINCIPAL_REVOKED` audit (preserving `PersistentScheduler.tick()` handling of `V4AssistantTaskRecord`).

### 2.6 Observability, Watchdog Fallback, Config Reload & Activation Documentation
- **`backend/app/services/assistant_v4/agent_runtime.py`**: Added `CircuitBreaker.get_state()`, thread-safe AI latency deque (`avg_latency_ms`, `p95_latency_ms`), `total_calls`, `error_calls`, `timeout_calls`, `timeout_rate`, and `NativeAgentRuntime.get_runtime_metrics()`.
- **`backend/app/services/assistant_v4/event_store.py`**: Added `EventStoreService.get_consumer_lag()` returning `total_events`, `total_receipts`, `unprocessed_event_count`, and `event_processing_lag_seconds`.
- **`backend/app/services/assistant_v4/task_scheduler.py`**: Added `require_scheduler_tick: bool = True` to `WatchdogAdapter` so `scheduler_completion` can fall back to `worker:v4:last_cycle` when `task_manager=False` (Stages 5–6).
- **`backend/app/services/assistant_v4/facade.py`** & **`backend/app/api/v1/endpoints/ai_assistant.py`**:
  - Implemented 5-state health classification (`HEALTHY`, `DEGRADED`, `OFFLINE`, `NOT_CONFIGURED`, `DISABLED`) in `get_health(now_override=...)` and exposed all `14` metric groups in `get_metrics(now_override=...)`.
  - Added Admin-only `POST /api/v1/ai-assistant/v4/config/reload` (`reload_config()`) returning accurate `env_changes_require_restart=True` metadata (changes to `.env.local` flags require restarting backend and worker processes).
- **Documentation**: Created `docs/assistant-v4/notification-cutover-protocol.md`, `docs/assistant-v4/retention-policy.md`, `docs/assistant-v4/production-preflight.md`, and rewrote `docs/assistant-v4/production-activation-plan.md` with 7 separate executable stage runbooks and secret-free external Healthchecks provisioning steps.

## 3. Post-Hardening Verification Summary

| Suite / Check | Result |
| --- | --- |
| V4 Automated Suite (`pytest backend/tests/test_assistant_v4_*.py -v -s --tb=short`) | `49 passed, 1 warning in 12.89s` (`0 failed, 0 skipped`; includes `BENCHMARK_100 total=100 passed=100 p95_ms=15.27`, 8 hardening tests, 7 audit regression tests) |
| Full Backend Suite (`pytest backend/tests/ -q --tb=short`) | `315 passed, 2 skipped, 1 warning in 145.84s (0:02:25)` (the `2` skipped tests require `LABOPS_TEST_REAL_HARDWARE=1` in `test_adapters.py:47` & `test_v2_comprehensive.py:178`) |
| Go Node Agent (`cd go && go test -count=1 ./...`) | Exit `0`; all `13` packages with tests passed (`0.005s–1.182s`); `6` cmd/proto packages `[no test files]` |
| Frontend Unit Tests (`bun test src/__tests__`) | `9 pass, 0 fail` in `421.00ms` |
| Typecheck (`bun run typecheck`) | `0` errors (`tsc --noEmit`) |
| Lint (`bun run lint` & strict `oxlint --deny-warnings` on touched TS files) | `0` errors (`oxlint`: `28` pre-existing warnings across 270 files; `oxlint --type-aware`: `0 warnings, 0 errors` across 187 files; `oxlint --deny-warnings` on all 4 touched TS files: `0 warnings, 0 errors`) |
| Frontend Build (`bun run build`) | Exit `0` (Next.js `16.4.6` Turbopack into `.next/`, `36/36` static pages generated; `.next-blue` & `.next-green` untouched) |
| Isolated SQLite Backup & `0005` Migration Rehearsal | Backup `0o600`, `44,965,888` bytes, SHA-256 `d6d8a88122096b2e47aefd167c373afe213fa9d4da847e75341a7b5df7f74e8b`; explicit `0005` upgrade exit `0`, `FK_ERRORS = 0`, `V4_INDEX_COUNT = 77`; `8,508` rows across `32` legacy tables verified with `CONTENT_DIFFS: {}` and `RESTORE_DIFFS: {}` |
