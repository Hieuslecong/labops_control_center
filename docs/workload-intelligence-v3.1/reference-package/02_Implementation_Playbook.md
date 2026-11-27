# Implementation Playbook — Workload Intelligence V3.1
Operational checklist for coding agent. Source of truth: `01_Specification.md`. UI reference: `04_Interactive_Prototype.html` and `visuals/`. This is an execution plan, not a production patch.

## 0. Preflight, isolation and baseline (gate G0)
1. Read AGENTS.md, README, relevant process-control/hardening docs and package/lockfiles; inspect actual HEAD, branch, git status, worktree, untracked changes, migration graph, feature flags and deployment scripts. Review pinned commit c1e3af18 only as reference. Do not reset, stash without permission, switch branch over ongoing work, rewrite history or push. Create separate worktree/branch only if cleanly permitted; prefer `feature/workload-intelligence-v3.1` as name, not mandatory. Record exact SHA and source paths. Detect overlapping Zalo implementation; make no edits in `backend/app/services/zalo_assistant/**`, associated AI files or configuration. Do not merge parallel work automatically. If repo inaccessible, stop with blocker and deliver audit/patch proposal only.
2. Identify current routes, BFF proxy/auth allowlist, admin/researcher route guards, schemas, real process ownership, process cache and protections, metrics collectors, global polling and shared data-table/chart UI. Find current source of host/group/member inventory and history. Inspect `src/app/dashboard/jobs/page.tsx`, `src/features/jobs/components/jobs-monitoring-view.tsx`, `src/features/labops/{store,queries}.ts`, `src/types/labops.ts`, `src/components/layout/global-telemetry-poller.tsx`, `backend/app/api/v1/endpoints/{jobs,processes}.py`, `backend/app/adapters/process_monitor_adapter.py`, `backend/app/services/labops_service.py`, `go/internal/process/**`, `backend/app/db/models.py`. Read telemetry collector/worker only for compatibility. Verify actual paths rather than invent modules.
3. Run existing tests in safe development/staging environment; record command, SHA, environment, exit status, failed tests and whether failures pre-existed. Candidate commands (confirm repository conventions): `bun run typecheck`, `bun run lint`, `bun test src/__tests__`, `bun run build`, `backend/.venv/bin/pytest backend/tests/ -q --tb=short`, `(cd go && go test ./...)`. Never run tests against production DB or real destructive APIs. Use fixtures and fake process backend. Do not install globally or upgrade dependencies for convenience. Capture screenshot of actual current admin page in staging, count network calls per refresh, baseline latency/CPU. No live deployment.
4. Read-only DB inspection in authorized staging only. Example SQL: `SELECT host_id, count(*), min(collected_at), max(collected_at) FROM telemetry_history_samples GROUP BY host_id;`. Inspect schema and a small sanitized sample; do not print secrets/command lines. Determine sample cadence, quality statuses, GPU UUID, data units, job IDs, missing job CPU/RAM, group relationships, source versions, retention. No assumption that production has history. Mark supported chart capabilities as a matrix. G0 output: AUDIT.md, baseline tests, metric dictionary draft, overlap map. Proceed with independent UI/read-only work if other gates blocked; disclose blockers.

## 1. Data correctness and contracts (G1)
- Implement normalized typed live DTO using real source fields, include status/coverage/timestamps independently for host/GPU/jobs. Keep old array endpoints and schemas. Add explicit metric units, CPU basis, RAM method, per-device attribution confidence, group identity, protected state. Never turn missing into zero.
- Test store partial-failure preservation; new successful empty list vs failed list. Fix global timestamp to reflect actual successful observation, not fetch completion. If shared store modification risks unrelated pages, isolate jobs-specific adapter and document residual limitation; do not create a parallel collector. Keep auth/session cache boundaries.
- Audit collector grouping: root and descendants only once; do not count raw diagnostics and root aggregate together. Classify gpustat/nvidia-smi/nvtop monitoring, preserve real compute and GPU processes, classify Python unknown/general unless training evidence. Reconcile device VRAM vs per-process VRAM separately. For multi-GPU, return explicit allocations or mark legacy representative unknown, never assign full total to one GPU. Label RSS tree sum; PSS on-demand capability only with bounded collection and no new capabilities; cgroup workspace total distinct. Check start ticks frequency and boot identity; avoid hardcoded time conversion when unsupported.
- Add versioned stable identity DTO, preserve current ref parser compatibility, ensure old ref cannot be used after reboot/stale snapshot for dangerous action unless server can verify current identity. No fabricated boot_id. Validate API role in BFF and backend, add tests. G1: data contracts, fixtures and correctness tests pass.

## 2. UI foundation and active table (G2)
- Feature-local implementation, preserve page shell. Split monolithic JobsMonitoringView into header, scope controls, KPI grid, data-quality/coverage, tabs, chart wrappers and table. Reuse existing UI tokens and libraries. Add tabs exactly as spec; Raw diagnostics switch in Live. Keep original legacy page and existing dialogs intact. New G2–G4 UI is READ ONLY; use disabled controls or no controls with explanatory text. No new action binding until G5 and G6 PASS. No Zalo or AI actions/links.
- Scope selectors: node/group/member/GPU (group only if supported); URL state via existing nuqs if appropriate, cascading authorized options; clear filters; historical range separate from live data. KPI scope coherent. No stale data marked live; no flash to zero. Raw mode separate counts. Render 6 KPI cards, chart placeholders only for unavailable. Table includes identity, owner, workspace, GPU association, CPU/RSS/VRAM, runtime, state, protected/actions. Search, sorting, column visibility, grouping if feasible, pagination, compact row height, export scoped safe CSV. Sticky headers/columns desktop; overflow confined to table; responsive 1440/1280/768/390. Use tabular numbers and accessible tooltips. Side drawer and keyboard focus. No blocking on historical DB if not ready.
- Prototype visual reference is a design target, not code to copy: `visuals/01_overview.png`, `02_live.png`, `03_analytics.png`, `04_history.png`, `05_detail.png` and dark/mobile optional. Recreate via actual component system, not static HTML injection. Capture screenshots, compare visual hierarchy and contrast. G2: UI regression, keyboard, mobile, no protected action exposure.

## 3. Live intelligence (G3)
- Add C02–C04, C09 when supported, T02–T04; C01 if real historical GPU data exists; default overview keeps 4 chart slots. Device VRAM denominator only observed selected devices; member filter affects ranking not device total; note scope. Unknown group/owner explicit. Table and charts share same snapshot; consistent sort and filters. Raw diagnostics fetched only when active. Consolidate query polling using existing global owner/query key; avoid duplicate manual `fetchLiveJobs`+`fetchLiveState`; inspect concurrent requests and abort stale filters. 10s live, no needless background. UI time range never changes live counts.
- Data export safe formula escaping, header/units, selected rows scope, cap. Do not export unsanitized command line. Test snapshot with partial rack offline, all offline, online zero, no inventory, unknown ownership, duplicated rows, multi-GPU, VRAM mismatch, stale. G3: chart values exactly match normalized fixture, no mock data production.

## 4. Historical analytics (G4)
- Read `telemetry_history_samples` through separate analytics repository/service, no worker changes. Bound host/time, use index; establish metric capability matrix per source/version. Add compatible BFF/API routes for snapshot/analytics/history/events as necessary. Register static routes before parameter routes; validate auth, range and resource filters, cap bucket/series/rows, timeouts; null gap handling. Use UTC storage and Asia/Ho_Chi_Minh presentation. Read existing shared data without changing retention. Cache per authorized scope, time bucket, freshness. No raw JSON returned to browser unless sanitized fields necessary.
- C05–C08: actual GPU/host series where supported; workload CPU/RAM unavailable until captured. C10–C11 require valid occupancy/group semantics, not inferred from VRAM. C12–C14 only with stable identity/verified event evidence. Reuse shared components; no fabricated history. If no durable stable lifecycle records, implement observed interval view and explicit unknown/coverage or defer; do not infer exit success. If additional table needed, propose additive migration (existing mechanism) and capture through existing non-Zalo collection only; if no suitable writer, document and request design review rather than editing Zalo. Test mixed old/new records, JSON null, duplicate timestamps, out-of-order snapshots, gaps, node reboot, missing owner/group, range/timezone, stale values and query plan.
- G4: capability matrix, golden analytics fixture, no hidden fallback. At least chart/table shells render clear empty states when unavailable. If real data absent, report supported/unavailable accurately, not failure disguised by mock.

## 5. Secure process operations (G5)
- Inspect current controller, protections and tests. Preserve admin-only `/processes/{ref}/stop|kill`, existing owner-specific job rules, researcher route, raw diagnostics restrictions. Validate object scope before sensitive fetch when possible and filter descendants. Add bounded reason/timeout, exact target preview and explicit confirmation. No bulk stop/kill, no autonomous actions. Disabled stale, protected, unknown, unverified identity. Check current node/boot/PID/start ticks/UID/container and authorization immediately before signal. Keep pidfd, fail closed for unsupported safe signal; if existing compatibility requires fallback, flag blocker, don't misrepresent. Use fake ProcessBackend tests for PID reuse, boot change, unauthorized owner, different container/UID, protected descendants, ENOSYS, timeout, partial verification, retry/concurrent action. No success toast on partial; refresh canonical data. Preserve audit trail and correlation ID. Do not modify Go deployment or host privileges.
- G5: backend and Go safety tests pass, mock UI confirms actions no real kill. Test old agent versions and feature fallback. If G5 incomplete, keep legacy action paths unchanged and disable new actions; never weaken.

## 6. Activate administration UI (G6)
- Dependency: G1 identity contracts, G2–G3 read-only UI, G5 security and compatibility PASS. G4 history may be DEFERRED without blocking safe live actions.
- Bind only existing authorized controller endpoints or reviewed additive paths; wire exact identity from a current fresh snapshot, server revalidation, protected/unsupported state, explicit reason/confirmation, separate SIGTERM/SIGKILL. No automatic escalation, bulk stop, retry of ambiguous outcome.
- Use operation idempotency/lock as specified in 05_Data_Contract.md; preserve partial/timeout/unknown results and remaining descendants, request reconciliation, no optimistic removal. Fake backend integration tests, no production signals. Keep feature off if any safety or compatibility test fails. Legacy route remains available unchanged.
- G6 output: action state screenshots, fake-backend integration evidence, capability flags, compatibility test results.

## 7. Verification and rollout (G7)
### Baseline regression matrix (all tests isolated)
The expanded canonical matrix in `08_Test_Acceptance_Matrix.md` adds V3.1 gate-specific tests and is mandatory. Both matrices apply.
| ID | Scenario | Expected |
|---|---|---|
| D01 | rack offline, workstation fresh | 1/4 GPU coverage; no zeros for rack; counts partial |
| D02 | endpoint error after previous success | stale last-good, actual last_success, no false empty |
| D03 | fresh empty jobs | 0 valid, explicit no workloads |
| D04 | per-member filter, search | scoped owner counts; device VRAM scope separate; search doesn't silently alter KPIs |
| D05 | two roots share RAM mappings | RSS sum label, PSS unavailable or measured; no unique RAM assertion |
| D06 | parent+child + raw duplicate | no double-count across table/chart |
| D07 | one process on 2 GPUs | two allocation rows only if verified; aggregate not assigned to one GPU |
| D08 | device vs process total mismatch | reconciliation warning, authoritative capacity, no negative free |
| D09 | gpustat / python / notebook / protected helper | correct conservative taxonomy |
| D10 | offline gap and missing history | null gap, no interpolation or false zero |
| D11 | PID reuse/boot change | separate IDs, cannot action stale identity |
| D12 | workload missing from fresh snapshot | not observed/unconfirmed, not completed |
| D13 | actual verified OOM/exit evidence | event accurate, no propagation to unrelated process |
| D14 | shared GPU two users | device-hours <= available observed duration; workload-hours distinct |
| D15 | group mapping absent | unknown group, no made-up label |
| D16 | invalid range/huge limit/sort | 400/422 or bounded, no excessive query |
| S01 | researcher direct admin route/API/export | 403, no data |
| S02 | researcher tree root/descendant mismatch | denied/redacted; no cross-user leak |
| S03 | protected process tampered UI payload | backend and agent deny |
| S04 | SIGTERM timeout / SIGKILL partial | exact result, no optimistic success/removal |
| S05 | session revoked / role change | reject new read/write, clear cache |
| S06 | CSV formula/command injection | sanitized, no secret export |
| S07 | pidfd ENOSYS / PID changed | fail closed or explicit safe compatibility restriction |
| U01 | refresh while sorting/typing/open drawer | no flicker, filter/focus stable |
| U02 | tab change, history range | inactive charts no polling; correct range only history |
| U03 | raw diagnostics | admin raw mode separate, existing filters/dialogs work |
| U04 | desktop/tablet/mobile/light/dark | no overflow beyond table, contrast, keyboard navigation |
| U05 | export/table/empty/error/loading | accessible, clear states; no fake chart |
| P01 | refresh network count | no redundant jobs fetch; measured before/after |
| P02 | 1/5/10 admins, 5/100/1000 rows | p95, CPU/RAM/query counts, bounded memory |
| P03 | 24h/30d historical ranges | bounded series and query time; query plan uses index when appropriate |
| R01 | existing dashboard/researcher jobs/overview/servers | regressions pass, no widened scope |
| R02 | Zalo module diff | zero changes to protected paths, configuration and behavior |
| R03 | migration on staging copy / old schema | backward compatible, no data deletion |
| R04 | UI feature off / rollback | old view works; no destructive rollback migration |

Run unit/integration tests, existing suite, typecheck, lint, build, browser visual tests in staging; capture actual screenshots 1440×1000, 1280, 768 and 390, light/dark. Use Playwright only if existing or isolated dev tool approved, otherwise browser manual QA. Collect real performance measurements and test evidence. Do not modify unrelated failures. Record skipped/blocked tests and reasons. For deployment, prepare existing blue-green script plan, health checks, feature flag, rollback to prior artifact, schema compatibility both versions; no production deployment/restart without explicit authorization. No destructive migrations; if additive schema remains after rollback document it. Confirm existing Zalo work not overwritten; `git diff --name-only` and scoped diff checks. Provide final report with exact commands and results, not claims.

## 8. Deliverables to produce inside repository (adapt conventions)
- `docs/workload-intelligence-v3.1/AUDIT.md`, `METRIC_CONTRACT.md`, `API_CONTRACT.md`, `DESIGN_DECISIONS.md`, `TEST_REPORT.md`, `DEPLOY_ROLLBACK.md` and `screenshots/`.
- Actual application code and tests, additive migration if justified, fixture data restricted to tests. No demo fixture imported by production route. Preserve route, auth and protected features.
- Final diff map (file, rationale, gate), compatibility matrix, data capability matrix, performance measurements, known limitations, exact HEAD, clean/dirty status, no Zalo modifications. Review every gate; if blocked, document and implement independent safe tasks. Do not claim production-ready unless all mandatory gates passed and runtime staging verified.

## 9. Gate protocol (normative)
Record each gate in `GATE_STATUS.md`: gate ID, exact HEAD, dirty diff summary, prerequisites, files changed, commands/exit codes/log references, acceptance IDs, PASS/BLOCKED/DEFERRED/NOT_RUN, blockers and rollback checkpoint. PASS requires executed mandatory tests; pre-existing failures require evidence and cannot be counted as pass. A gate with missing runtime evidence stays BLOCKED or NOT_RUN. DEFERRED only for explicitly optional capabilities, never auth/security/legacy compatibility. Independent safe work may continue, but no dependent action activation or release flag enablement. No automatic commit, merge, push, deployment or restart. Read `prompts/G*.md` at each stage.
