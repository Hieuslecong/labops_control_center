# V3.1 acceptance matrix

All application tests below are NOT_RUN until the coding agent executes them against the actual repository. The package-only contract and prototype tests are separate evidence, not product acceptance. Each test has an expected result, gate, and mandatory/conditional designation. Mark PASS only with commands, logs, exit codes, tested SHA and environment. Never turn a missing capability into a PASS by using fabricated data. Tests must use isolated DB and fake process controller. No production mutation.

| ID | Gate | Scenario | Required result |
|---|---|---|---|
| A01 | G0 | Dirty tree or concurrent Zalo edits | Protected initial diff preserved; overlap map; no reset/merge |
| A02 | G0 | Read current source, migration heads, actual history fields | Evidence with file/line and HEAD; unknown capability documented |
| A03 | G0 | Existing test baseline | Existing failures recorded; no false pass; production isolated |
| D01 | G1 | Partial rack unavailable / workstation fresh | Exact fixture count, 1/4 coverage, partial label, no zero rack |
| D02 | G1 | All nodes unavailable | Null observed counts, no false zero; stale cache distinctly shown |
| D03 | G1 | Fresh complete zero workloads | Valid zero, no error or stale confusion |
| D04 | G1 | Failed refresh after fresh snapshot | Retain last-good as stale, preserve observed timestamp, no fake live |
| D05 | G1 | Unknown GPU association and unknown member | Tri-state, unmapped counts and composition conservation |
| D06 | G1 | Multi-GPU PID + parent/child duplicates | Deduplicate root and PID-device pairs, no representative full allocation |
| D07 | G1 | Shared pages RSS tree sum | Explicit method label; no unique-memory claim; PSS null unless measured |
| D08 | G1 | Device vs process VRAM discrepancy | Authoritative capacity, mismatch warning, no negative/free inference |
| D09 | G1 | gpustat, Python, Jupyter, torchrun, protected helper | Conservative independent taxonomy; preserve legacy semantics |
| D10 | G1 | Missing nullable fields, NaN, invalid version | Reject invalid DTO; never coerce missing to zero or trust unknown version |
| D11 | G1 | Legacy units/cpu basis and time ticks | Source-verified conversion and numeric seconds; no guessed denominator |
| D12 | G1 | Username changes, group changes | Stable member ID; historically verified group or labeled current group |
| D13 | G1 | Schema and complete fixture tests | All schemas and invariants pass; invalid fixture rejected |
| U01 | G2 | 4 tabs, 6 KPI cards, overview preview, 6 table slots | Reference layout, only valid supported charts; no new actions enabled |
| U02 | G2 | Runtime sorting both directions | Numeric seconds; unknown last; stable ID tie-break |
| U03 | G2 | Filter changes and color stability | Same entity stable color; device VRAM unaffected by member filter |
| U04 | G2 | Node/GPU/member combination invalid | Clear or reject with explicit scope feedback; no phantom data |
| U05 | G2 | Historical range change | Correct chart data range only; live KPI unchanged; fixture clearly marked |
| U06 | G2 | Search and pagination | Table-only filter; KPI and composition unaffected; no page-count misuse |
| U07 | G2 | Refresh while typing/sorting/drawer open | No flicker/lost state/focus; stale indication; actions disabled |
| U08 | G2 | Desktop 1440/1280, tablet 768, mobile 390 | No page-level overflow; table scroll confined; light/dark contrast |
| U09 | G2 | Keyboard, screen reader labels, focus trap/restore | Accessible tabs/dialogs/table headings; reduced motion |
| U10 | G2 | Raw diagnostics | Admin only, separate list/query and classification; no workload KPI mixing |
| P01 | G3 | Manual refresh and global poller | One logical refresh; no redundant jobs fetch; network trace and collector counters |
| P02 | G3 | Slow old response arrives after newer request | Discard stale response by scope/generation/session |
| P03 | G3 | Multiple tabs, concurrent 1/5/10 admins | No query stampede per panel, bounded load and cache separation |
| P04 | G3 | Export filtered authorized rows | Correct order, columns, units, scope; safe spreadsheet escaping; no raw secret |
| P05 | G3 | Hidden page / inactive tabs / fixed history range | Pause unnecessary polling; shell behavior preserved; no duplicated collectors |
| H01 | G4 | No historical records with fresh live data | Honest no-data capability; live table unaffected |
| H02 | G4 | Legacy JSON missing host/job identity or per-job CPU/RAM | Unsupported/partial, no fake mapping or fallback zeros |
| H03 | G4 | Duplicate/out-of-order samples, offline gap, reboot | Deduplicate, null gaps; no false closure; new stable ID after reboot |
| H04 | G4 | Shared GPU simultaneous jobs | Device occupancy <= observed device duration; workload-hours separate; no member device-hours without evidence |
| H05 | G4 | Verified exit/OOM vs disappeared process | Only trusted evidence establishes event; container OOM not assigned arbitrarily |
| H06 | G4 | Range, timezone, huge query, pagination | Validate bounds, stable cursor, series cap, UTC storage/ICT display |
| H07 | G4 | Group relationship absent or changed | Unknown group; observation-time attribution or label current-group view |
| H08 | G4 | 24h/30d history query | Measure query plan, p95, bounded result; no N+1; retention unchanged |
| H09 | G4 | No safe non-Zalo writer | History V2 DEFERRED; no new collector/worker; history V1 remains functional |
| S01 | G5 | Researcher direct access admin analytics/raw/export | Denied consistently at BFF and backend |
| S02 | G5 | Tree root authorized but foreign descendant | No unauthorized descendant data or side effect |
| S03 | G5 | Protected target and modified UI payload | Denied by backend and agent; no signal |
| S04 | G5 | PID reused, boot changed, UID/container mismatch | New action rejected; fake backend signals count zero |
| S05 | G5 | pidfd unsupported/ENOSYS | New dangerous action fail closed; old compatibility tested |
| S06 | G5 | Two concurrent actions, duplicate request, key/payload mismatch | Durable dedup/lock; no duplicate side effect; conflict on changed payload |
| S07 | G5 | Signal timeout, remaining/skipped descendants, ambiguous outcome | Partial/unknown result, no false success or automatic retry/escalation |
| S08 | G5 | Role revoked during action approval; stale snapshot | Reauthorize/revalidate before signal, deny; audit sanitized result |
| S09 | G5 | Old Go Agent connected | Read remains usable; new action capability disabled if identity unavailable |
| W01 | G6 | G5 blocked / feature flags off | New controls disabled, legacy UI unchanged |
| W02 | G6 | Valid explicit confirmation in fake backend | Exact target/params/reason; typed force-kill confirmation; no bulk |
| W03 | G6 | Action result and refresh | No optimistic deletion; canonical reconciliation; no partial success |
| R01 | G7 | Admin logout/researcher session change/in-flight old response | Sensitive cache cleared, no cross-user exposure |
| R02 | G7 | Existing dashboard, overview, server, researcher jobs | Regression passes; same public contracts |
| R03 | G7 | Existing Zalo consumer with changed shared services | Differential fixture tests pass; protected hashes unchanged |
| R04 | G7 | Mixed backend/frontend/agent/schema versions | All mandatory rows in compatibility matrix pass |
| R05 | G7 | Migration collision or failed deploy dry run | No destructive migration; feature stays off; rollback drill verified |
| R06 | G7 | Performance baseline vs candidate | Same environment, workload and counts; p95/CPU/RAM/request metrics reported |
| R07 | G7 | Build/lint/typecheck/automated tests | Real command output, nonzero exit disclosed, no unrelated fixes to fake pass |

## Gate policy
- Every gate's mandatory cases PASS before its dependents are enabled. H01–H09 require graceful unavailable behavior even when historical data is absent. Optional V2 history/capture remains DEFERRED only with explicit capability and rationale, not false PASS. G5/G6 cannot be skipped if new action controls are enabled; if blocked, read-only release candidate only, with action flag off, not a full V3.1 release.
- Targets (staging proposals): p95 warm snapshot <=500ms, common 24h history <=1s, 30d <=2s, initial useful UI <=2s; baseline and environment recorded. Test 5/100/1000 fixture rows and 1/5/10 concurrent admins. If target unmet, quantify reason and require release review; never falsify. Zero unauthorized action/leak, zero duplicate signal, zero stale identity execution in executed suite.
- Use existing test scripts, e.g. `bun run typecheck`, `bun run lint`, `bun run build`, `bun test src/__tests__`, isolated backend pytest, `(cd go && go test ./...)` only after verifying actual repo tooling. Package tests: `python tests/validate_contracts.py`, `python tests/test_prototype.py` (Playwright/Chromium). These do not substitute for application tests.
