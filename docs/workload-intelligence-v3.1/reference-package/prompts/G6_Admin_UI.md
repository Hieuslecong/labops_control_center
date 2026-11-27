# G6 — Activate Administration UI

**Prerequisites:** G2/G3/G5 PASS. **Acceptance IDs:** W01–W03 (see 08_Test_Acceptance_Matrix.md).

Read master prompt, specification, playbook, metric/data contract, protected scope, compatibility matrix and rollback plan. Keep all user constraints. Perform actual safe code and tests; do not produce another plan. Check prior gate evidence, no activation if dependency failed.

## Tasks
Enable action controls only in isolated fake/staging context with capability enabled and G5 PASS. New view remains disabled if stale, role changed, identity unsupported, protected or operation in flight. Reuse dialogs; show exact owner/node/boot/PID/start/scope, required reason, explicit SIGTERM confirmation and typed target for SIGKILL. No automatic escalation or optimistic row deletion. Send exact server-issued identity and idempotency key; handle conflict and unknown without retry. Show remaining/skipped descendants, partial result distinctly, reconcile snapshot and audit. Keep legacy page unchanged until approved cutover. Screenshot confirmation states; test fake backend side effects only. No real signals or production flag enablement.

## Exit gate
Record exact HEAD, changed files, commands, exit codes, evidence paths, PASS/BLOCKED/DEFERRED/NOT_RUN, blockers and rollback checkpoint in GATE_STATUS.md. Do not call unexecuted tests passed. Continue independent safe work only. No production mutation or protected Zalo edits.
