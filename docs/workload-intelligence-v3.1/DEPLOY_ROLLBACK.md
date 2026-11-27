# LabOps Workload Intelligence V3.1 — Deployment & Rollback Runbook (G7)

> [!IMPORTANT]
> Changes remain uncommitted in the working tree on `feature/labops-ai-sre-agent-v2` and have **not** been pushed or deployed to production (`:3000`/`:3002`/`:8000`/`:8002`).

## 1. Environment Feature Flags

| Variable | Default | Effect |
| --- | --- | --- |
| `NEXT_PUBLIC_LABOPS_WORKLOAD_V31` | `false` / unset | Controls `/dashboard/jobs` default UI (`false` = preserves legacy `LegacyJobsMonitoringView`; `?v31=1` previews V3.1, `?v31=0` forces legacy). |
| `LABOPS_WORKLOAD_V31_ACTIONS_ENABLED` | `false` | Controls `POST /api/v1/processes/{ref}/v31-action` and the detail drawer action buttons (fail-closed `403 ACTIONS_DISABLED_BY_FLAG` when `false`). |

## 2. Launching the Isolated Local Demo Stack (`:8010` + `:3010`)

The isolated `.next-v31-demo` build and `/tmp/labops_v31_demo_data/labops_v2.sqlite3` online SQLite backup do not touch production ports, live `.next-blue`/`.next-green` bundles, live DB, or Zalo AI workers.

```bash
# 1. Refresh isolated read-only DB copy (online backup, safe against WAL)
/home/aiot-ws1/projects/labops_control_center/backend/.venv/bin/python -c '
import os, sqlite3
os.makedirs("/tmp/labops_v31_demo_data", exist_ok=True)
src = sqlite3.connect("file:/home/aiot-ws1/projects/labops_control_center/backend/data/labops_v2.sqlite3?mode=ro", uri=True)
dst = sqlite3.connect("/tmp/labops_v31_demo_data/labops_v2.sqlite3")
src.backup(dst); dst.close(); src.close()
'

# 2. Start isolated backend on 127.0.0.1:8010 (with safe V4 shadow/task flags enabled)
cd /home/aiot-ws1/projects/labops_control_center/backend
DATABASE_URL="sqlite:////tmp/labops_v31_demo_data/labops_v2.sqlite3" \
LABOPS_DATA_DIR="/tmp/labops_v31_demo_data" \
ZALO_BOT_TOKEN="" ROUTER_API_KEY="" \
LABOPS_WORKLOAD_V31_ACTIONS_ENABLED="false" \
ASSISTANT_V4_ENABLED="true" \
ASSISTANT_V4_EVENT_ENGINE="true" \
ASSISTANT_V4_SHADOW_MODE="true" \
ASSISTANT_V4_TASK_MANAGER="true" \
ASSISTANT_V4_NATURAL_LANGUAGE="true" \
ASSISTANT_V4_PROACTIVE_INVESTIGATION="true" \
ASSISTANT_V4_WATCHDOG_ENABLED="true" \
ASSISTANT_V4_RETENTION_ENABLED="true" \
ASSISTANT_V4_RETENTION_DRY_RUN="true" \
ASSISTANT_V4_NOTIFICATION_V2="false" \
LABOPS_CORS_ORIGINS="http://127.0.0.1:3010,http://localhost:3010,http://192.168.167.251:3010" \
PYTHONPATH=/home/aiot-ws1/projects/labops_control_center/backend \
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 &

# 3. Start isolated frontend on 0.0.0.0:3010 (uses prebuilt .next-v31-demo)
cd /home/aiot-ws1/projects/labops_control_center
PORT=3010 HOSTNAME=0.0.0.0 NEXT_DIST_DIR=.next-v31-demo \
LABOPS_BACKEND_URL="http://127.0.0.1:8010" \
LABOPS_CORS_ORIGINS="http://127.0.0.1:3010,http://localhost:3010,http://192.168.167.251:3010" \
NEXT_PUBLIC_LABOPS_WORKLOAD_V31=true \
NEXT_TELEMETRY_DISABLED=1 \
./node_modules/.bin/next start -p 3010 -H 0.0.0.0
```

Open `http://127.0.0.1:3010/dashboard/jobs` in Chrome/Firefox (and compare legacy view at `http://127.0.0.1:3010/dashboard/jobs?v31=0`).

## 3. Staged Production Rollout Guidance (When Authorized)

1. **Stage A (Read-Only UI)**: Rebuild production dist with `NEXT_PUBLIC_LABOPS_WORKLOAD_V31=true` while keeping `LABOPS_WORKLOAD_V31_ACTIONS_ENABLED=false`. Existing Zalo AI, worker, legacy `/api/v1/jobs`, and legacy `/api/v1/processes/{ref}/{stop,kill}` remain untouched.
2. **Stage B (Verify Go Agent Additive Fields)**: Roll `labops-agent` binary update on `workstation` and `track` so `boot_id`, `elapsed_seconds`, `started_at_utc`, conservative `workload_kind`, and `gpu_allocations[]` populate live.
3. **Stage C (Controlled Action Enablement)**: Set `LABOPS_WORKLOAD_V31_ACTIONS_ENABLED=true` on the backend only after verifying kernel `pidfd_open` / `pidfd_send_signal` support (`OSPidfdProcessBackend` automatically verifies `pidfd` capability and fails closed if unsupported).
4. **Optional 7d/30d Analytics Query Optimization**: If multi-week queries exceed 800ms on SQLite, project only needed columns (`host_id, collected_at, freshness_status, cpu_percent, ram_used_gb, gpus_json`) in `WorkloadIntelligenceService.get_analytics` to avoid loading `jobs_json`.

## 4. Instant Zero-Downtime Rollback Procedure

- **UI Rollback (0 code changes)**: Set `NEXT_PUBLIC_LABOPS_WORKLOAD_V31=false` (or append `?v31=0` on `/dashboard/jobs` immediately) to revert `/dashboard/jobs` to `LegacyJobsMonitoringView`.
- **Backend Actions Rollback (0 restarts if env reloaded)**: Set `LABOPS_WORKLOAD_V31_ACTIONS_ENABLED=false` to immediately block all `/api/v1/processes/{ref}/v31-action` calls (`403`) while legacy process endpoints continue running.
- **Database Rollback**: Zero migrations were applied by Workload Intelligence V3.1, so no schema downgrade is required.
- **Demo Artifact Cleanup**: Stop `:8010` / `:3010` processes and remove `/tmp/labops_v31_demo_data` and `.next-v31-demo`.
