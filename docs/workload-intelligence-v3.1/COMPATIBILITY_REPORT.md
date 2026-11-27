# LabOps Workload Intelligence V3.1 — Compatibility & Protected Scope Report (G7)

- **Repository**: `Hieuslecong/labops_control_center`
- **Branch**: `feature/labops-ai-sre-agent-v2`
- **Start HEAD**: `50b7085126e47b4784c8c3a89c2a3bf0be42bc29`
- **Reviewed Baseline**: `c1e3af18c31e6c8fa982d1958a1c83863585bcb9`

## 1. Zalo AI & V4 Protected Scope Integrity

- Manifest file: [`.protected_sha256`](file:///home/aiot-ws1/projects/labops_control_center/docs/workload-intelligence-v3.1/.protected_sha256) covering `54` Zalo AI / V4 source, worker, config, migration, and documentation files.
- Verification command: `sha256sum -c docs/workload-intelligence-v3.1/.protected_sha256 --quiet` -> **`PROTECTED_HASHES_OK`** (`0` bytes changed).
- Zero edits to: `backend/app/services/zalo_assistant/**`, `backend/app/services/assistant_v4/**`, `backend/app/api/v1/endpoints/ai_assistant.py`, `src/features/ai-assistant/**`, live `.env.local`, live SQLite WAL database (`backend/data/labops_v2.sqlite3`), systemd services, network/SSH/WireGuard/firewall configs, Docker privileges, GPU allocations, or `.next-blue`/`.next-green`.

## 2. Backward Compatibility Matrix (`C01–C14` / Legacy API Consumers)

| Surface | Requirement | Verification Status | Evidence |
| --- | --- | --- | --- |
| `GET /api/v1/jobs` | Existing list contract & researcher filtering untouched | **PASS** | `200 OK` on isolated demo for both `ADMIN` and `RESEARCHER`; `test_user_workloads_filtering.py` passes |
| `GET /api/v1/jobs/active` | Legacy Zalo/overview active workloads endpoint preserved | **PASS** | `200 OK` on isolated demo; additive fields on `process_monitor_adapter` keep legacy dictionary keys intact |
| `POST /api/v1/jobs/{job_id}/stop` | Existing route priority preserved after static GET routes | **PASS** | Static V3.1 routes (`/jobs/snapshot`, `/jobs/analytics`, `/jobs/history`, `/jobs/events`, `/jobs/{id}/detail`) registered before `/{job_id}/stop` in `jobs.py` |
| `POST /api/v1/processes/{ref}/stop` & `/kill` | Legacy process endpoints preserved untouched | **PASS** | `test_process_control.py` passes alongside new V3.1 suite; V3.1 hardened actions isolated at `/processes/{ref}/v31-action` |
| `GET /api/v1/processes/{ref}/tree` | Admin sees full tree; researcher sees self/owned descendants only | **PASS** | Non-admin descendants filtered recursively by `_filter_tree_for_owner` in `processes.py`; verified in `test_http_endpoints_rbac_and_tree_descendant_filtering` |
| Go Agent JSON telemetry (`ProcessInfo` / `ProcessReport`) | Additive-only schema fields | **PASS** | `BootID`, `ElapsedSeconds`, `StartedAtUTC`, `WorkloadKind`, `KindConfidence`, `KindEvidence`, `GPUAllocations` added with `omitempty`; all existing fields preserved; `go test -count=1 ./...` green |
| `/dashboard/jobs` Legacy UI fallback | Preserve legacy UI when `NEXT_PUBLIC_LABOPS_WORKLOAD_V31` is unset/false | **PASS** | `JobsMonitoringView` in `src/features/jobs/components/jobs-monitoring-view.tsx` defaults to `LegacyJobsMonitoringView` unless `NEXT_PUBLIC_LABOPS_WORKLOAD_V31 === 'true'` or `?v31=1` (overrideable via `?v31=0`) |
| `telemetry_history_samples` SQLite WAL | Read-only reads using existing composite index | **PASS** | Zero DDL migrations or schema changes required for V3.1; reads use existing `telemetry_history_samples` via `(host_id, collected_at)` |
