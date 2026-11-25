# LabOps Personal AI Agent V4 — Event Catalog & Producers

Authoritative runtime source: `backend/app/services/assistant_v4/event_store.py` (`EVENT_CATALOG`).

## 1. Transactional Core Producers (Same DB `session_scope`)
- **MRAM SSH Requests (`backend/app/services/mram_service.py`):**
  - `request.created` (submitted SSH request)
  - `request.updated`, `request.approved`, `request.rejected`, `request.overdue`
  - `provisioning.succeeded`, `provisioning.failed`
- **Unified Incident Engine (`backend/app/services/zalo_assistant/incident_engine.py`):**
  - `incident.created`, `incident.reopened`, `incident.escalated`, `incident.acknowledged`, `incident.resolved`
  - Underlying taxonomy mappings: `node.unreachable`, `node.recovered`, `telemetry.stale`, `gpu.temperature.high`, `gpu.error`, `storage.threshold`, `service.down`, `service.recovered`, `workspace.failed`, `job.failed`, `job.stalled`, `training.oom`, `ssh.failure`.
- **Repository Guard (`backend/app/api/v1/endpoints/repositories.py`):**
  - Emits `repository.approved` / `repository.risk_found` on admin review decisions via `zalo_assistant_service.v4.record_repo_decision_event(...)`.
- **V4 Subsystems:**
  - `task.due`, `task.condition_met`, `task.failed` (`task_scheduler.py`)
  - `ai.unavailable`, `service.recovered` (`agent_runtime.py` `CircuitBreaker`)
  - `worker.stalled`, `notification.failed`, `github.action_failed`, `github.review_requested`, `github.issue_assigned` (when enabled)

## 2. Unsupported / Unconfigured Sources (Marked Honestly — No Fake Polling)
| Event Type | Catalog Status | Reason |
|---|---|---|
| `gpu.throttle` | `NOT_SUPPORTED` | `nvidia-smi` clock throttle reasons not parsed yet |
| `network.degraded` | `NOT_CONFIGURED` | No active read-only network latency probe configured |
| `backup.failed` | `NOT_CONFIGURED` | No backup job scheduler daemon configured |
| `certificate.expiring` | `NOT_CONFIGURED` | External TLS managed by Cloudflare/reverse proxy |
| `request.withdrawn` | `NOT_SUPPORTED` | No self-withdraw endpoint in core MRAM |
| `job.started` | `PLANNED` | Inferred via `psutil` PID polling only |
| `training.nan`, `training.checkpoint` | `NOT_CONFIGURED` | Requires opt-in structured training SDK adapter |
| `github.*`, `mail.important`, `calendar.upcoming`, `research.update` | `NOT_CONFIGURED` | External connectors disabled by default (`external_connectors.* = false`) |

## 3. Idempotency & Reconciliation
- Events deduplicate by unique `(source, source_event_id)` in `v4_event_store`.
- Consumer processing records `(consumer, event_id)` in `v4_event_consumer_receipts` and flags late/out-of-order updates (`STALE_OUT_OF_ORDER`) instead of overwriting newer state.
- `EventReconciler` scans open `ServerAccessRequestRecord` and open `IncidentRecord` rows and backfills missing events without replaying closed historical items.
