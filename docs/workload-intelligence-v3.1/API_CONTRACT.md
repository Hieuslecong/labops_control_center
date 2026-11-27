# LabOps Workload Intelligence V3.1 — API Contract (G1–G5)

All V3.1 endpoints are additive under `/api/v1` and protected by both Next.js BFF `isAdminRequiredLabOpsPath()` and FastAPI `Depends(require_admin)`. Legacy `/api/v1/jobs`, `/api/v1/jobs/active`, `/api/v1/jobs/{job_id}/stop`, `/api/v1/processes`, and `/api/v1/processes/{ref}/stop|kill` contracts are preserved unchanged.

## 1. Endpoints

| Method & Path | Authorization | Response Schema (`contracts/*.schema.json`) | Notes |
| --- | --- | --- | --- |
| `GET /api/v1/jobs/snapshot` | Admin (`401`/`403`) | `WorkloadSnapshotEnvelope` (`schema_version=1`) | Query: `node_id`, `gpu_uuid`, `member_id`, `group_id`. Validates scope (`422` on contradiction). Cache `no-store`. |
| `GET /api/v1/jobs/snapshot/export.csv` | Admin (`401`/`403`) | `text/csv` | Scoped filtered export with formula injection neutralization (`'` prefix on `=+-@\t`), capped `2000` rows, zero raw cmdlines. |
| `GET /api/v1/jobs/analytics` | Admin (`401`/`403`) | `WorkloadAnalyticsEnvelope` (`schema_version=1`) | Bounded `[from, to)` (`<= 90d`), max `20` series, max `600` points/series, `NO_DATA` null gaps, capability flags. |
| `GET /api/v1/jobs/history` | Admin (`401`/`403`) | `WorkloadHistoryEnvelope` (`schema_version=1`) | Keyset cursor `(last_seen_at, id)`, `limit <= 100`, `actionable=false`, explicit `MISSING_UNCONFIRMED` (no fabricated exit codes). |
| `GET /api/v1/jobs/events` | Admin (`401`/`403`) | `WorkloadEventsEnvelope` (`schema_version=1`) | Sanitized `AuditEventRecord` items, keyset cursor `(occurred_at, id)`. |
| `GET /api/v1/jobs/{workload_id}/detail` | Admin (`401`/`403`) | `WorkloadDetailResponse` (`schema_version=1`) | Drawer metadata (`node_name`, `member_full_name`, `gpu_labels`, sanitized cmdline, metric definitions, action flag status). |
| `POST /api/v1/processes/{ref:path}/v31-action` | Admin (`401`/`403`) | `ProcessActionResultEnvelope` (`schema_version=1`) | Gated by `LABOPS_WORKLOAD_V31_ACTIONS_ENABLED`. Enforces `operation_key` idempotency (`409`), in-flight target lock (`409`), `boot_id` + `start_ticks` + `uid` + `container_id` live verification, typed `KILL <pid>` confirmation, and fail-closed `pidfd` (`503` on `ENOSYS`). |

## 2. Hardened Existing Route

- `GET /api/v1/processes/{ref:path}/tree`: Researcher access checks root ownership AND recursively filters out any foreign-owned or `SYSTEM_PROCESS`/`is_protected` descendant nodes (`S02`).
