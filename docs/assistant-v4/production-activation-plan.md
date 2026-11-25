# LabOps Personal AI Agent V4 — Staged Production Activation & Executable Runbooks

**Target System:** `/home/aiot-ws1/projects/labops_control_center`  
**Production Database (SQLite WAL):** `/home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3`  
**Service Topology:** Blue/Green Frontend (`:3001`/`:3002` behind `:3000`), Blue/Green Backend (`:8001`/`:8002` behind `:8000`), SRE Worker (`labops-ai-sre-worker` / DB lease `zalo_sre_worker`).  

> [!CAUTION]
> **Feature-Flag Reload Reality:** Changes written to `.env.local` do **not** hot-reload into running processes. Every `.env.local` flag change requires restarting Backend (`./scripts/deploy-zero-downtime.sh --backend-only`) and `labops-ai-sre-worker`. Only explicit `ASSISTANT_V4_CONFIG_PATH` YAML reloads support `POST /api/v1/ai-assistant/v4/config/reload` (`reload_config()`) for in-process updates, and only in that process.

> [!NOTE]
> **Protected Infrastructure:** None of these stages alter server IPs (`192.168.167.250`/`.251`), NetworkManager, SSH ports/configuration, WireGuard/VPN, firewall/MikroTik, GPU allocations, Docker workspace isolation, storage mounts/quotas, or existing member accounts/permissions. Never reboot or shut down servers.

---

## Runbook 1 — Production Preflight (Read-Only)

- **Effective Feature Flags:** Unchanged (`ASSISTANT_V4_*` omitted or `false` -> `V2_LEGACY`).
- **Preconditions:** Operator read-only access on `aiot-ws1`; zero code edits in live workspace; 9Router local gateway running on `127.0.0.1:20128`.
- **Exact Commands (Read-Only):**
  ```bash
  cd /home/aiot-ws1/projects/labops_control_center
  git rev-parse HEAD && git status --short
  cat .runtime/active-slots.json
  cat backend/data/zalo_worker_status.json
  lsof backend/data/labops_v2.sqlite3* || true
  backend/.venv/bin/python -c "
  import sqlite3
  c = sqlite3.connect('file:/home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3?mode=ro', uri=True)
  print('alembic:', c.execute('SELECT version_num FROM alembic_version').fetchall())
  print('journal_mode:', c.execute('PRAGMA journal_mode').fetchall())
  print('integrity:', c.execute('PRAGMA integrity_check').fetchall())
  "
  curl -sf http://127.0.0.1:20128/v1/models | head -c 300 && echo ""
  ```
- **Verification Steps:** Confirm `journal_mode=wal`, `integrity=ok`, `alembic=0003_labops_ai_sre_agent_v2` (pre-migration), active backend slot & worker PID healthy, and `>= 10 GiB` free disk (`df -h backend/data`).
- **Stop Conditions:** `PRAGMA integrity_check != ok`, corrupted WAL, `< 2 GiB` disk free, or unexpected unapproved DB writers.
- **Rollback Steps:** None required (strictly read-only).
- **Expected Evidence:** Saved terminal transcript showing `integrity=ok`, active slot JSON, worker heartbeat, and `alembic_version`.
- **Operator Approval Requirement:** Standard read-only pre-change check.

---

## Runbook 2 — Backup & Restore Verification (With Writer Quiesce Rules)

- **Effective Feature Flags:** Unchanged (`ASSISTANT_V4_ENABLED=false`).
- **Preconditions:** Runbook 1 passed; brief maintenance window approved if pausing writers during quiesced backup.
- **Exact Commands:**
  ```bash
  cd /home/aiot-ws1/projects/labops_control_center
  mkdir -p backend/data/backups && chmod 700 backend/data/backups
  TS=$(date -u +%Y%m%dT%H%M%SZ)
  BK="backend/data/backups/pre_v4_0005_${TS}.sqlite3"

  # 1. Stop DB writers cleanly before taking authoritative pre-migration snapshot
  systemctl stop labops-ai-sre-worker || pkill -TERM -f "zalo_assistant(\.worker|/worker\.py)" || true
  for f in .runtime/backend-blue.pid .runtime/backend-green.pid; do
    [ -f "$f" ] && kill -TERM "$(cat "$f")" 2>/dev/null || true
  done
  sleep 2
  lsof /home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3* || true

  # 2. WAL-consistent SQLite backup API + 0600 permissions + SHA-256 + integrity check
  backend/.venv/bin/python -c "
  import sqlite3, os
  src = sqlite3.connect('/home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3')
  dst = sqlite3.connect('${BK}')
  src.backup(dst)
  dst.close(); src.close()
  os.chmod('${BK}', 0o600)
  "
  sha256sum "${BK}" | tee "${BK}.sha256"
  backend/.venv/bin/python -c "import sqlite3; c=sqlite3.connect('${BK}'); print(c.execute('PRAGMA integrity_check').fetchall(), 'fk_errs:', len(c.execute('PRAGMA foreign_key_check').fetchall()))"

  # 3. Restore dry-test into isolated temp path (never overwrite live DB during test)
  RESTORE_TEST=$(mktemp -d)
  cp "${BK}" "${RESTORE_TEST}/test_restore.sqlite3"
  backend/.venv/bin/python -c "import sqlite3; c=sqlite3.connect('${RESTORE_TEST}/test_restore.sqlite3'); print('rows telemetry:', c.execute('SELECT COUNT(*) FROM telemetry_history_samples').fetchone()[0])"
  rm -rf "${RESTORE_TEST}"
  ```
- **Verification Steps:** `ls -la "${BK}"` shows `-rw-------` (`0600`), `PRAGMA integrity_check` returns `[('ok',)]`, `fk_errs: 0`, and `.sha256` matches.
- **Stop Conditions:** Active writer remains attached in `lsof`, backup integrity fails, or permission is broader than `0600`.
- **Rollback / Emergency Restore Rule:** Never overwrite `labops_v2.sqlite3` while writers are active. Never discard post-backup production writes without explicit operator approval. To restore from `${BK}` cleanly: (1) confirm zero writers in `lsof`, (2) verify checksum `sha256sum -c "${BK}.sha256"`, (3) remove stale `labops_v2.sqlite3-wal` and `labops_v2.sqlite3-shm`, (4) `install -m 0600 "${BK}" backend/data/labops_v2.sqlite3`, (5) restart backend + worker.
- **Expected Evidence:** Backup path, `0600` permissions, exact SHA-256 checksum, `integrity=ok`, `fk_errs=0`, and restore dry-test row count.
- **Operator Approval Requirement:** **Explicit maintenance window approval required** before stopping backend/worker writers.

---

## Runbook 3 — Explicit Migration to `0005_labops_personal_ai_v4`

- **Effective Feature Flags:** `ASSISTANT_V4_ENABLED=false` *(all V4 flags OFF)*.
- **Preconditions:** Runbook 2 quiesced backup verified on disk (`${BK}` with `.sha256`), writers still stopped (`lsof` empty).
- **Exact Commands:**
  ```bash
  cd /home/aiot-ws1/projects/labops_control_center
  ABS_DB="sqlite:////home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3"
  DATABASE_URL="${ABS_DB}" PYTHONPATH=backend backend/.venv/bin/alembic \
    -c backend/alembic.ini upgrade 0005_labops_personal_ai_v4
  DATABASE_URL="${ABS_DB}" PYTHONPATH=backend backend/.venv/bin/alembic \
    -c backend/alembic.ini current
  backend/.venv/bin/python -c "
  import sqlite3
  c = sqlite3.connect('/home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3')
  print('integrity:', c.execute('PRAGMA integrity_check').fetchall())
  print('fk_errs:', len(c.execute('PRAGMA foreign_key_check').fetchall()))
  print('v4_tables:', len(c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'v4_%'\").fetchall()))
  print('v4_indexes:', len(c.execute(\"SELECT name FROM sqlite_master WHERE type='index' AND tbl_name LIKE 'v4_%'\").fetchall()))
  "
  ```
- **Verification Steps:** `alembic current` prints `0005_labops_personal_ai_v4`; `integrity = ok`; `fk_errs = 0`; `v4_tables = 17`.
- **Stop Conditions:** Non-zero Alembic exit code, missing V4 table/index, `integrity != ok`, or unexpected legacy row count change.
- **Rollback Steps:** With writers stopped, verify `${BK}.sha256`, remove `-wal`/`-shm`, restore `${BK}` with `install -m 0600`, re-check integrity, and restart previous backend/worker build. *(Do not run `alembic downgrade` against live DB without staging rehearsal.)*
- **Expected Evidence:** Alembic upgrade log (`0003 -> 0004 -> 0005`), `current` output, `integrity=ok`, and `v4_tables=17`.
- **Operator Approval Requirement:** **Explicit Administrator sign-off required.**

---

## Runbook 4 — Dark Deployment (All V4 Flags OFF)

- **Effective Feature Flags (`.env.local`):**
  ```env
  ASSISTANT_V4_ENABLED=false
  ASSISTANT_V4_EVENT_ENGINE=false
  ASSISTANT_V4_NOTIFICATION_V2=false
  ASSISTANT_V4_SHADOW_MODE=false
  ASSISTANT_V4_TASK_MANAGER=false
  ASSISTANT_V4_NATURAL_LANGUAGE=false
  ASSISTANT_V4_PROACTIVE_INVESTIGATION=false
  ASSISTANT_V4_WRITE_ACTIONS=false
  ASSISTANT_V4_WATCHDOG_ENABLED=false
  ASSISTANT_V4_RETENTION_ENABLED=false
  ASSISTANT_V4_PAID_FALLBACK=false
  ```
- **Preconditions:** Runbook 3 succeeded (`0005_labops_personal_ai_v4`).
- **Exact Commands:**
  ```bash
  cd /home/aiot-ws1/projects/labops_control_center
  ./scripts/deploy-zero-downtime.sh
  systemctl is-active labops-ai-sre-worker || cat backend/data/zalo_worker_status.json
  curl -sf http://127.0.0.1:8000/healthz
  ```
- **Verification Steps:**
  - Admin checks `GET /api/v1/ai-assistant/v4/health` -> `"status": "DISABLED"`, `"v4_enabled": false`, `"effective_owner": "V2_LEGACY"`.
  - Verify existing V2 web login, Dashboard pages, MRAM SSH request view, and Zalo `/status` respond identically to baseline `c1e3af1`.
- **Stop Conditions:** Any `/healthz` failure, worker crash, SQLite lock error in logs, or V2 command regression.
- **Rollback Steps:** Checkout previous commit `c1e3af18c31e6c8fa982d1958a1c83863585bcb9` in deployment clone (schema `0005` is strictly additive and V2-compatible) and re-run `./scripts/deploy-zero-downtime.sh`.
- **Expected Evidence:** HTTP `200` on `/healthz`, V4 health `status=DISABLED`, active V2 polling heartbeat.
- **Operator Approval Requirement:** Standard release approval.

---

## Runbook 5 — Shadow Mode (`V2_LEGACY_SHADOW`, Zero V4 External Sends)

- **Effective Feature Flags (`.env.local`):**
  ```env
  ASSISTANT_V4_ENABLED=true
  ASSISTANT_V4_EVENT_ENGINE=true
  ASSISTANT_V4_SHADOW_MODE=true
  ASSISTANT_V4_NOTIFICATION_V2=false
  ASSISTANT_V4_TASK_MANAGER=false
  ASSISTANT_V4_NATURAL_LANGUAGE=false
  ASSISTANT_V4_PROACTIVE_INVESTIGATION=false
  ASSISTANT_V4_WRITE_ACTIONS=false
  ASSISTANT_V4_RETENTION_ENABLED=false
  ASSISTANT_V4_PAID_FALLBACK=false
  ```
  *(Note: See Watchdog Provisioning section below before setting `ASSISTANT_V4_WATCHDOG_ENABLED=true`.)*
- **Preconditions:** Runbook 4 stable; `.env.local` updated.
- **Exact Commands:**
  ```bash
  cd /home/aiot-ws1/projects/labops_control_center
  ./scripts/deploy-zero-downtime.sh --backend-only
  ```
- **Verification Steps (24h Soak):**
  - Admin checks `GET /api/v1/ai-assistant/v4/health` (`flags.shadow_mode == true`, `effective_owner == "V2_LEGACY_SHADOW"`) and `GET /api/v1/ai-assistant/v4/metrics` (`shadow.decisions_total`, `events.unprocessed_event_count == 0`, `outbox.total == 0`, `quota.total_sent == 0`, `database.sqlite_lock_errors == 0`).
  - Read-only SQLite check confirms `v4_event_consumer_receipts` records `SHADOW_DRY_RUN`, `v4_notification_outbox` has **0** external Zalo rows, and legacy V2 `notification_outbox` continues delivering normally.
- **Stop Conditions:** Any `sqlite_lock_errors > 0`, any row inserted into `v4_notification_outbox` with `channel='zalo'`, worker lag > `180s`, or legacy latency/error spike.
- **Rollback Steps:** Set `ASSISTANT_V4_ENABLED=false` and `ASSISTANT_V4_SHADOW_MODE=false` in `.env.local`, then restart backend + worker (`./scripts/deploy-zero-downtime.sh --backend-only`).
- **Expected Evidence:** 24h metrics JSON snapshot with `outbox.total == 0`, `shadow.would_send_count > 0` (when events occur), zero unhandled errors in worker logs.
- **Operator Approval Requirement:** **Admin approval required** to transition out of dark deploy.

---

## Runbook 6 — Admin-Only Notification Cutover (`V4_ACTIVE`)

- **Effective Feature Flags (`.env.local`):**
  ```env
  ASSISTANT_V4_ENABLED=true
  ASSISTANT_V4_EVENT_ENGINE=true
  ASSISTANT_V4_SHADOW_MODE=false
  ASSISTANT_V4_NOTIFICATION_V2=true
  ASSISTANT_V4_TASK_MANAGER=false
  ASSISTANT_V4_NATURAL_LANGUAGE=false
  ASSISTANT_V4_PROACTIVE_INVESTIGATION=false
  ASSISTANT_V4_WRITE_ACTIONS=false
  ASSISTANT_V4_PAID_FALLBACK=false
  ```
- **Preconditions:** Runbook 5 `>= 24h` shadow soak clean; Admin `ZaloIdentityLinkRecord` verified `ACTIVE` + `ADMIN` with observed 1-1 DM `chat_id`; non-admin `NotificationSubscriptionRecord` disabled or explicitly audited.
- **Exact Commands:**
  ```bash
  cd /home/aiot-ws1/projects/labops_control_center
  ./scripts/deploy-zero-downtime.sh --backend-only
  ```
- **Verification Steps:**
  - Verify `audit_events` contains `V4_NOTIFICATION_OWNERSHIP_CHANGED` with `target_id='V4_ACTIVE'`.
  - Verify pre-cutover `v4_event_store` backlog did **not** trigger a Zalo notification burst (`result_status='SKIPPED_PRE_CUTOVER'` for any unconsumed pre-cutover events).
  - Verify any pre-existing V2 `PENDING`/`FAILED_RETRYABLE` rows in `notification_outbox` drain cleanly once; `DELIVERY_UNKNOWN` stays frozen.
  - Trigger one controlled test event (or wait for a verified event) -> verify `v4_notification_outbox` transitions `PENDING -> CLAIMED -> SENT` with valid `provider_message_id` and legacy `notification_outbox` receives **0** new infra/MRAM/repo enqueues.
- **Stop Conditions:** Any V2+V4 double send, unexpected `DLQ` or `UNKNOWN_DELIVERY` > 1%, wrong recipient, or missed P0 alert.
- **Rollback Steps:** Set `ASSISTANT_V4_NOTIFICATION_V2=false` in `.env.local` and run `./scripts/deploy-zero-downtime.sh --backend-only` (restarts backend + worker). Verify `V4_NOTIFICATION_OWNERSHIP_CHANGED` to `V2_LEGACY` in `audit_events`; V4 data stays preserved.
- **Expected Evidence:** Clean `metrics` JSON (`delivery_outcomes`, `quota.percent_used`), Admin confirmation of 1 single clean Zalo alert, zero duplicate rows across V2/V4 outboxes.
- **Operator Approval Requirement:** **Explicit Admin cutover sign-off required.**

---

## Runbook 7 — Progressive Activation (Tasks/Watchdog → Conversational AI → Proactive RCA)

Activate **one sub-stage at a time** (`>= 24h` observation between increments); keep `ASSISTANT_V4_WRITE_ACTIONS=false` and `ASSISTANT_V4_RETENTION_ENABLED=false` (or `ASSISTANT_V4_RETENTION_DRY_RUN=true`) unless separately authorized.

### Stage 7A: Task Manager & Independent Watchdog
- **Effective Flags (append to Stage 6):** `ASSISTANT_V4_TASK_MANAGER=true`, `ASSISTANT_V4_WATCHDOG_ENABLED=true` (after Watchdog provisioning below).
- **Commands:** Update `.env.local` and run `./scripts/deploy-zero-downtime.sh --backend-only`.
- **Verify:** Create a test reminder via `/v4/tasks` and a watch rule via `/v4/watches`, restart `labops-ai-sre-worker`, confirm persistence + single fire, verify `health.status == "HEALTHY"` and external watchdog receives pings.
- **Stop / Rollback:** Set `ASSISTANT_V4_TASK_MANAGER=false` (and `ASSISTANT_V4_WATCHDOG_ENABLED=false` if needed) + restart backend/worker.

### Stage 7B: Read-Only Conversational AI & Scoped Memory
- **Effective Flags (append to 7A):** `ASSISTANT_V4_NATURAL_LANGUAGE=true`, `ASSISTANT_V4_AGENT_RUNTIME=native`, `ASSISTANT_V4_PAID_FALLBACK=false`.
- **Preconditions & Commands:** Run live read-only 9Router capability check first (`plain_chat=True`, `tool_calling_supported=True`, `model_free`, zero secrets sent); enable flags in `.env.local` and run `./scripts/deploy-zero-downtime.sh --backend-only`.
- **Verify:** Test one `GENERAL_CHAT` query and one `LABOPS_AGENT` tool-backed query (`/v4/metrics` -> `ai.timeout_rate == 0`, `circuit_breaker.state == "CLOSED"`), plus `/clear` and non-admin cross-container isolation.
- **Stop / Rollback:** Unbounded timeouts, hallucinated metric claims, or `circuit_breaker` flapping -> set `ASSISTANT_V4_NATURAL_LANGUAGE=false` + restart backend/worker (`/ask` reverts to V2 path).

### Stage 7C: Proactive Evidence-Based RCA (`write_actions` still OFF)
- **Effective Flags (append to 7B):** `ASSISTANT_V4_PROACTIVE_INVESTIGATION=true`, keep `ASSISTANT_V4_WRITE_ACTIONS=false`.
- **Verify:** Inspect `GET /api/v1/ai-assistant/v4/investigations` (`max_per_cycle = 2`, `verified_root_cause = null` when evidence incomplete), submit feedback via `POST /v4/investigations/{run_id}/feedback`.
- **Stop / Rollback:** Worker cycle lag > `180s` or ungrounded RCA hypotheses -> set `ASSISTANT_V4_PROACTIVE_INVESTIGATION=false` + restart backend/worker.
- **Operator Approval Requirement:** Separate Admin approval per sub-stage (`7A`, `7B`, `7C`). *(Enabling `ASSISTANT_V4_WRITE_ACTIONS=true` or live `ASSISTANT_V4_RETENTION_DRY_RUN=false` requires dedicated post-soak security sign-off outside this rollout.)*

---

## Watchdog Provisioning Instructions (Secret-Free Guide)

1. Provision an external dead-man check (Healthchecks.io or self-hosted Healthchecks instance reachable from `aiot-ws1`) on a monitor outside the LabOps process with:
   - Expected period: `60` seconds (`ASSISTANT_V4_WATCHDOG_PERIOD_SECONDS` / `watchdog.period_seconds: 60`)
   - Grace time: `180` seconds (`watchdog.grace_seconds: 180`)
2. Copy the check's private ping URL from the external monitoring dashboard **only** into `.env.local` (file permissions `0600`, excluded from git by `.gitignore`):
   ```env
   ASSISTANT_V4_WATCHDOG_ENABLED=true
   ASSISTANT_V4_WATCHDOG_PING_URL=<PROVISIONED_PING_URL_FROM_MONITORING_DASHBOARD>
   ```
3. Restart backend & worker via `./scripts/deploy-zero-downtime.sh --backend-only`.
4. Verify:
   - `GET /api/v1/ai-assistant/v4/health` returns `"watchdog": {"configured": true, "coverage_status": "ACTIVE", "healthy": true, ...}` with zero ping URL leakage.
   - `WatchdogAdapter` skips pinging whenever DB reachability, event lag, outbox backlog age, or scheduler/worker heartbeat exceeds `grace_seconds=180`.
