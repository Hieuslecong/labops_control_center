# LabOps Workload Intelligence V3.1 — Architecture & Design Decisions (G1)

1. **ADR-01: Strictly Compliant Additive Envelopes Without Mutating Legacy Array APIs**
   - `/jobs` and `/jobs/active` continue returning `List[JobResponse]` verbatim from `service.jobs` so existing Researcher views, Overview tables, and Zalo Bot `/jobs` have zero behavioral drift.
   - New V3.1 routes (`/jobs/snapshot`, `/jobs/analytics`, `/jobs/history`, `/jobs/events`, `/jobs/{id}/detail`) register before `/{job_id}/stop` and serialize strictly with `ConfigDict(extra="forbid")` matching JSON Schemas in `contracts/`.

2. **ADR-02: Zero Modifications to Zalo AI & Historical Shared Collector**
   - `backend/app/services/zalo_assistant/**` and `assistant_v4/**` are untouched. Historical charts read `telemetry_history_samples` read-only via `WorkloadIntelligenceService`. Unsupported legacy metrics (per-workload historical CPU/RAM, historical research group ID, History V2 collector) are surfaced explicitly as `UNSUPPORTED` / `DEFERRED` rather than adding a parallel collector.

3. **ADR-03: Fail-Closed Process Security Boundary on V3.1 Actions**
   - V3.1 process actions stay disabled unless `LABOPS_WORKLOAD_V31_ACTIONS_ENABLED=true`. Even when enabled, `execute_v31_process_action` checks `boot_id`, `start_ticks`, `uid`, `container_id`, protected status, durable `IdempotencyRecord`, per-target concurrency lock, and `os.pidfd_open` + `signal.pidfd_send_signal` without `syscall.Kill` numeric fallback on `ENOSYS`.

4. **ADR-04: Principal-Isolated Query Cache & Feature-Flagged UI Cutover**
   - `JobsMonitoringView` defaults to legacy behavior unless `NEXT_PUBLIC_LABOPS_WORKLOAD_V31 === 'true'` or `?v31=1` query override is present.
   - `workloadQueries` keys include `principalKey` (`username:role`) and clear/discard stale in-flight fetches on role or session changes without relying on `setInterval`.
