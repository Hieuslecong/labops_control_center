# G2 — Read-only Dashboard UI

**Prerequisites:** G1. **Acceptance IDs:** U01–U10 (see 08_Test_Acceptance_Matrix.md).

Read master prompt, specification, playbook, metric/data contract, protected scope, compatibility matrix and rollback plan. Keep all user constraints. Perform actual safe code and tests; do not produce another plan. Check prior gate evidence, no activation if dependency failed.

## Tasks
Implement four tabs and six KPIs using current Next.js, shadcn/ui and theme. Inspect screenshots and prototype at full size; reuse existing sidebar/header/guard and table/chart components. Overview: four panels, scoped attention, active preview. Live: complete table with search/sort/pagination/columns and separate Raw; drawer/tree read-only, no new action binding. Analytics/history render capability-gated slots, no synthetic production data. Entity filters cascade and scope chips explain device metrics vs workload rows. Search is table-only; history range never changes live counts; sort runtime by numeric seconds unknown last; colors stable by ID. Data unavailable/stale/partial states clear, no flicker. Keep legacy page/feature flag off for production. Test keyboard, focus, mobile/table overflow, light/dark, query state persistence. Output screenshots of all tabs and documented design deviations.

## Exit gate
Record exact HEAD, changed files, commands, exit codes, evidence paths, PASS/BLOCKED/DEFERRED/NOT_RUN, blockers and rollback checkpoint in GATE_STATUS.md. Do not call unexecuted tests passed. Continue independent safe work only. No production mutation or protected Zalo edits.
