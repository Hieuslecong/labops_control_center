# Protected scope — mandatory boundary

This document is normative and takes precedence over implementation convenience. Scope: `/dashboard/jobs` only and the minimum shared read/data/process-control seams needed to support it. Preserve existing `/dashboard/researcher/jobs`, other dashboard views, authentication and authorization. The user has explicitly excluded Zalo AI, which is being developed separately.

## Isolation protocol
1. Record `git rev-parse HEAD`, `git branch --show-current`, `git status --porcelain=v1 -uall`, `git worktree list`, `git diff --stat`, staged and unstaged diffs, migration graph, and protected-file hashes before edits. Do not output secret values. Reviewed commit `c1e3af18c31e6c8fa982d1958a1c83863585bcb9` is evidence, not a checkout target.
2. Work in a separate worktree/feature branch if authorized and safe. Never reset, stash, clean, force-checkout, overwrite uncommitted changes, auto-merge, rebase, commit/push or apply production migrations without permission. If dirty/shared changes overlap, isolate feature-local modules or report BLOCKED; do not resolve someone else's changes.
3. G0 produces protected-path list based on actual tree and dependency map (imports, shared models, migrations, legacy response consumers, BFF routes). Existing Zalo development must remain byte-for-byte unchanged relative to the initial working-tree state; compare against the initial snapshot rather than assuming HEAD is clean. Record hashes/diffs before and after; avoid copying secrets into reports.

## Forbidden direct changes
- `backend/app/services/zalo_assistant/**`; Zalo AI endpoints, bot/worker, notification/incident/outbox/ledger, schedules, prompts, AI gateway/model configuration, and related frontend/config/deployment/tests owned by concurrent task. Inspect read-only if required. No AI integration, no new Zalo events, no watch rules, no new notification APIs, no duplicate collectors.
- Network interfaces, IPs (rack 192.168.167.250, workstation 192.168.167.251), routes, DNS, gateway, NetworkManager, SSH/sshd, VPN/WireGuard, firewall, MikroTik, Pangolin, Docker privileges, users/credentials, existing GPU/storage allocations, quota and workspaces. No reboot/shutdown/restart, production deploy or real process signals. Do not alter deployment scripts for this project unless separately approved.
- Existing shared migration revisions and retention/deletion of history; new privileges or capabilities; unrelated package updates or broad formatting.

## Shared dependency guard
| Shared surface | Rule | Evidence required |
|---|---|---|
| `labops_service.py`, `process_monitor_adapter.py` | Keep legacy public signatures/shape/meaning; create feature-specific DTO/mapper; no hidden new collection in legacy path | Before/after differential tests on same fixtures; query/call counts; representative existing Zalo consumer contract check without editing Zalo |
| `db/models.py`, migrations | Prefer read-only existing telemetry model. Only additive migration if essential and collision reviewed. No column rename/drop/type narrowing or shared retention changes | Old app/new schema and new app/old schema feature-off test; migration heads reviewed; restore plan |
| `go/internal/process/**` | Preserve legacy ProcessRef parser/API; additive versioned identity/metadata; no new host permissions; do not force new agent rollout | Mixed old/new version tests; controller fake backend and compatibility tests |
| `src/features/labops/{store,queries}.ts`, shared layout/types/BFF | Prefer feature-local normalization; if edited, test all existing consumers; no cross-role cache data | Old/new page/researcher/header tests; auth/cache isolation; requests baseline |
| Historical storage | read-only SQLAlchemy repository; no worker import, no Zalo writes | source version and capability matrix; bounded query; no data modification |

Read-only inspection of Zalo consumer calls/tests is allowed to establish compatibility; running test fixtures is allowed only in isolated environment. Do not change existing Zalo tests just to pass. Never access live tokens, send Zalo messages or start a real worker. If concurrent work makes exact compatibility untestable, mark dependency gate BLOCKED, keep feature off and continue only independent safe tasks.
