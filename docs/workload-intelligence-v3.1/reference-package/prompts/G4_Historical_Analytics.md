# G4 — Historical Analytics V1

**Prerequisites:** G1, G2/G3 integration. **Acceptance IDs:** H01–H09 (see 08_Test_Acceptance_Matrix.md).

Read master prompt, specification, playbook, metric/data contract, protected scope, compatibility matrix and rollback plan. Keep all user constraints. Perform actual safe code and tests; do not produce another plan. Check prior gate evidence, no activation if dependency failed.

## Tasks
Build separate read-only analytics repository/service over existing telemetry_history_samples with version-aware normalized reader. Do not modify Zalo sampler, worker, outbox, retention or config. Inspect actual history fields; qualify every series, bound ranges, points, SQL query time and pagination. Use observed intervals/gaps, no interpolation or extrapolation; coverage ratio per series; distinguish device occupancy/compute-equivalent/workload/allocation hours. Legacy missing host/ID/CPU/RAM means unavailable, not guessed. No exit success/failure without trusted evidence. Group membership at observation if verified, otherwise label current group/unknown. History V2 capture only if existing non-Zalo writer, approved additive migration and compatibility evidence; otherwise DEFERRED. No new worker. Add no-data UI, capability matrix and index/query measurements. Validate old/new schema combinations.

## Exit gate
Record exact HEAD, changed files, commands, exit codes, evidence paths, PASS/BLOCKED/DEFERRED/NOT_RUN, blockers and rollback checkpoint in GATE_STATUS.md. Do not call unexecuted tests passed. Continue independent safe work only. No production mutation or protected Zalo edits.
