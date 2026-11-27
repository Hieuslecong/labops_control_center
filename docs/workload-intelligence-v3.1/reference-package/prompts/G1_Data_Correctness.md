# G1 — Metric Contract & Data Correctness

**Prerequisites:** G0. **Acceptance IDs:** D01–D13 (see 08_Test_Acceptance_Matrix.md).

Read master prompt, specification, playbook, metric/data contract, protected scope, compatibility matrix and rollback plan. Keep all user constraints. Perform actual safe code and tests; do not produce another plan. Check prior gate evidence, no activation if dependency failed.

## Tasks
Implement feature-local normalized Pydantic/TypeScript DTOs and read-only snapshot mapping consistent with schemas. Verify legacy response diff, maintain field names and units for existing consumers. Read authorized inventory and roots once, deduplicate stable IDs, preserve partial and stale states. Scope KPIs and device-wide memory distinctly; verify root/child and multi-GPU accounting. Add conservative workload taxonomy and tri-state GPU association without changing legacy classification semantics. RSS explicitly labeled; PSS optional on demand only. Start ticks/boot ID and UID/container metadata additive; no guessed identity. Preserve actual source timestamps and last-good data; handle error/empty separately. Validate all fixture contracts, negative cases and role-specific filters; test mixed versions and shared Zalo consumer. Do not enable new process actions. Output METRIC_CONTRACT.md, API_CONTRACT.md, ADRs and executed tests.

## Exit gate
Record exact HEAD, changed files, commands, exit codes, evidence paths, PASS/BLOCKED/DEFERRED/NOT_RUN, blockers and rollback checkpoint in GATE_STATUS.md. Do not call unexecuted tests passed. Continue independent safe work only. No production mutation or protected Zalo edits.
