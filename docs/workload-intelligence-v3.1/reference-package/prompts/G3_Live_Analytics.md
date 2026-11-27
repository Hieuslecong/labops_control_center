# G3 — Live Analytics & Performance

**Prerequisites:** G1/G2. **Acceptance IDs:** P01–P05 (see 08_Test_Acceptance_Matrix.md).

Read master prompt, specification, playbook, metric/data contract, protected scope, compatibility matrix and rollback plan. Keep all user constraints. Perform actual safe code and tests; do not produce another plan. Check prior gate evidence, no activation if dependency failed.

## Tasks
Implement one logical read snapshot per scope and reuse cache; measure global poller and adapter before changes. Eliminate redundant manual jobs fetch and per-chart polling; cancel stale requests, discard late responses from old principal/scope, preserve previous good values. Backend authorization before aggregation; no public shared cache. Render live VRAM capacity, ranking, composition and supported allocation/member/group data; reconcile differences without forcing sums. Use pagination and accessible table, safe authorized CSV with formula protection and no secret commands. Cap payload and request concurrency. Measure before/after network/collector counts, p95 and memory on same fixtures. Pause hidden/inactive query work as allowed by shell. Run test matrix; report unsupported capabilities and residual shared-store constraints.

## Exit gate
Record exact HEAD, changed files, commands, exit codes, evidence paths, PASS/BLOCKED/DEFERRED/NOT_RUN, blockers and rollback checkpoint in GATE_STATUS.md. Do not call unexecuted tests passed. Continue independent safe work only. No production mutation or protected Zalo edits.
