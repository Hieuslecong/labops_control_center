# G0 — Repository Audit & Isolation

**Prerequisites:** None. **Acceptance IDs:** A01–A03 (see 08_Test_Acceptance_Matrix.md).

Read master prompt, specification, playbook, metric/data contract, protected scope, compatibility matrix and rollback plan. Keep all user constraints. Perform actual safe code and tests; do not produce another plan. Check prior gate evidence, no activation if dependency failed.

## Tasks
Read repository AGENTS.md and actual process-control/deployment docs, compare current HEAD to baseline. Inventory route guards, BFF mappings, models, migration graph, store and queries, Go collector and controller, tests, shared chart/table components, historical fields, deployment flags. Audit E01–E13 against current code. Capture protected-path initial hashes and staged/unstaged changes; list Zalo shared API consumers read-only. Do not reset, stash, checkout or merge. Inspect actual environment and execute safe baseline tests; record failures and versions. For authorized staging DB only, check aggregate history row count/range, sample field presence, freshness, units, boot identity, group membership. No production query without authorization, no full logs or secrets. Capture baseline network trace and latency if possible. Output AUDIT.md, capability matrix, protected dependency map, baseline test report and gate status. If source unavailable, report blocker rather than invent code.

## Exit gate
Record exact HEAD, changed files, commands, exit codes, evidence paths, PASS/BLOCKED/DEFERRED/NOT_RUN, blockers and rollback checkpoint in GATE_STATUS.md. Do not call unexecuted tests passed. Continue independent safe work only. No production mutation or protected Zalo edits.
