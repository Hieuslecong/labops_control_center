# LabOps Personal AI Agent V4 — Deployment & Rollback Runbook

> **Important:** All V4 code paths ship dormant (`false` by default). Do not enable flags in production without explicit administrator authorization and healthy staging verification.

## 1. Pre-Deployment Verification
1. Backup production SQLite/PostgreSQL database before running Alembic migrations.
2. Run migration check on a copy/staging DB:
   ```bash
   PYTHONPATH=backend backend/.venv/bin/alembic upgrade head
   ```
   Revision `0005_labops_personal_ai_v4` is purely additive (creates `v4_*` tables only; zero alterations to legacy tables).

## 2. Staged Rollout Sequence
Enable flags incrementally in `.env.local` or `ASSISTANT_V4_CONFIG_PATH` YAML and restart cleanly (`systemctl restart ...`):
1. **Shadow Verification:** `ASSISTANT_V4_ENABLED=true`, `ASSISTANT_V4_EVENT_ENGINE=true`, `ASSISTANT_V4_SHADOW_MODE=true`. Verify `GET /api/v1/ai-assistant/v4/health` and `v4_event_store` / `v4_incident_meta`.
2. **Notification V2:** Set `ASSISTANT_V4_SHADOW_MODE=false`, `ASSISTANT_V4_NOTIFICATION_V2=true` (replaces duplicate inline sends with unified `v4_notification_outbox` leased delivery).
3. **Tasks & Watchdog:** Set `ASSISTANT_V4_TASK_MANAGER=true`, configure `ASSISTANT_V4_WATCHDOG_PING_URL`, then set `ASSISTANT_V4_WATCHDOG_ENABLED=true`.
4. **Conversational AI & Proactive RCA:** Set `ASSISTANT_V4_NATURAL_LANGUAGE=true`, optionally `ASSISTANT_V4_PROACTIVE_INVESTIGATION=true`.
5. **Write Confirmations (Highest Privilege):** Only after validating R1–R3 Dashboard & Zalo approval audit logs, set `ASSISTANT_V4_WRITE_ACTIONS=true`.

## 3. Instant Safe Rollback (`R02`)
1. Flip V4 flags back to `false` (or set `ASSISTANT_V4_ENABLED=false`) and restart backend / `labops-ai-sre-worker`.
2. **Data Safety:** Disabling flags immediately restores V2 deterministic command and `/ask` paths while leaving existing `v4_assistant_tasks`, `v4_watch_rules`, and historical records intact in `v4_*` tables without spurious duplicate dispatches. Users/Admins can still list or cancel pending tasks via `/api/v1/ai-assistant/v4/tasks`.
3. Schema downgrade (`alembic downgrade 0004_labops_mram_v1`) drops only `v4_*` tables and is intended for staging/clean teardown rather than routine production flag rollback.
