# G5 — Process Security

**Prerequisites:** G0/G1. **Acceptance IDs:** S01–S09 (see 08_Test_Acceptance_Matrix.md).

Read master prompt, specification, playbook, metric/data contract, protected scope, compatibility matrix and rollback plan. Keep all user constraints. Perform actual safe code and tests; do not produce another plan. Check prior gate evidence, no activation if dependency failed.

## Tasks
Review actual ProcessRef, controller, safeguards, BFF, stop/kill and tree routes. Reuse existing control logic; verify before fetching sensitive tree and filter descendants. New action target must be authorized, fresh, current boot, node, PID, start ticks, UID and container, nonprotected. pidfd safe signal mandatory for new path; unsupported old agent/ENOSYS fail closed, legacy behavior unchanged pending separate approval. No negative PGID, bulk, escalation or replay on unknown. Bind confirmation and durable operation key to exact actor/identity/action/params; atomic concurrent lock and result retrieval, conflict if key reused with different payload. If impossible within scope leave new action disabled. Test with fake ProcessBackend only: stale identity, reboot, protected/foreign descendants, mixed versions, concurrent actions, timeout/partial/unknown, revoked role. Audit sanitized results, no production signals. Output security report and capability flags; G5 PASS only with evidence.

## Exit gate
Record exact HEAD, changed files, commands, exit codes, evidence paths, PASS/BLOCKED/DEFERRED/NOT_RUN, blockers and rollback checkpoint in GATE_STATUS.md. Do not call unexecuted tests passed. Continue independent safe work only. No production mutation or protected Zalo edits.
