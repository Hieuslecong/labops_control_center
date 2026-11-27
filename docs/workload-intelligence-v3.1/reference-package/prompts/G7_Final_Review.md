# G7 — Final Integration & Release Readiness

**Prerequisites:** All mandatory gates; optional V2 may defer. **Acceptance IDs:** R01–R07 (see 08_Test_Acceptance_Matrix.md).

Read master prompt, specification, playbook, metric/data contract, protected scope, compatibility matrix and rollback plan. Keep all user constraints. Perform actual safe code and tests; do not produce another plan. Check prior gate evidence, no activation if dependency failed.

## Tasks
Run repository regression, Go/Python/frontend tests, typecheck/lint/build and isolated browser QA; actual commands and exit codes, pre-existing failures, skipped reasons. Recheck all protected hashes and staged/unstaged diffs, consumer differential tests, compatibility matrix, logout/cache and mixed-version rollout. Compare before/after measured performance and visual screenshots. Run feature-off and rollback drill in staging, check old artifact/new additive schema compatibility, document fallback. No production deploy/restart/migration, no auto merge/push. Generate GATE_STATUS, TEST_REPORT, COMPATIBILITY_REPORT, CAPABILITY_MATRIX, DEPLOY_ROLLBACK, final file map, exact HEAD/status and known blockers. State whether read-only candidate or full candidate; do not claim production-ready until staging/mandatory gates and user approval. Keep Zalo work intact.

## Exit gate
Record exact HEAD, changed files, commands, exit codes, evidence paths, PASS/BLOCKED/DEFERRED/NOT_RUN, blockers and rollback checkpoint in GATE_STATUS.md. Do not call unexecuted tests passed. Continue independent safe work only. No production mutation or protected Zalo edits.
