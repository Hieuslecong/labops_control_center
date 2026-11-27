# LabOps Workload Intelligence V3.1 — Verification & Test Report (G7)

All checks below reflect actual execution results observed after the final edits on `feature/labops-ai-sre-agent-v2`.

## 1. Automated Unit, Contract, Static Analysis & Build Gates

| Suite | Command | Result | Scope & Honest Disclosures |
| --- | --- | --- | --- |
| **Go Node Agent** | `cd go && go test -count=1 ./...` | **PASS** (13 packages `ok`, 5 cmd/proto packages `[no test files]`) | Covers `internal/process`, `internal/gpu`, `internal/host`, `internal/agent`, `internal/docker`, `internal/storage`, `internal/transport`, `internal/gateway`, `internal/pki`, `internal/config`, `internal/repositoryguard`, `internal/resources`, `internal/gitaiotcli` |
| **Backend V3.1 Gate Suite** | `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_workload_v31_contracts_and_gates.py -v` | **PASS** (`7 passed`) | Covers 10 synthetic JSON Schema/invariant fixtures (`D01–D13`), partial/stale/offline/empty telemetry, multi-GPU dedup (`D06`), VRAM mismatch (`D08`), CSV formula injection (`P04`), read-only analytics gap handling & keyset cursor (`G4`), pidfd/identity/idempotency/partial-failure (`G5`), HTTP RBAC & researcher descendant filtering |
| **Full Backend Suite** | `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/ -q --tb=short` | **322 passed, 2 skipped, 0 failed** (`148.51s`) | Includes all V3.1 tests plus existing process, workload, auth, security, and Zalo AI / V4 suites; 2 pre-existing runtime-skipped cases in unrelated backend suites |
| **Frontend Unit Tests** | `bun test src/__tests__` | **PASS** (`14 pass, 0 fail` across 2 files) | 9 tests in `milestone3-frontend-hardening.test.ts` + 5 V3.1 suites in `workload-v31.test.ts` (BFF admin RBAC, CSV formula neutralization, numeric `elapsed_seconds` unknown-last sorting, schema validation, zero `setInterval`/micro-font/Tabler direct icon invariants) |
| **TypeScript Check** | `bun run typecheck` (`tsc --noEmit`) | **PASS** (`0 errors`) | Whole `src/` tree type-checked cleanly |
| **Frontend Lint & Deprecated APIs** | `bun run lint` + `npx oxlint --deny-warnings <V3.1 files>` | **PASS** (`0 errors`; `0 warnings` across all 9 V3.1 files; `0 warnings, 0 errors` on `lint:deprecated`) | Whole-repo `oxlint` reports 28 pre-existing warnings in unrelated legacy UI/AI files (`src/features/ai-assistant/`, `src/components/kbar/`, `src/components/data-table/`) |
| **Isolated Next.js Build** | `NEXT_PUBLIC_SENTRY_DISABLED=1 NEXT_TELEMETRY_DISABLED=1 NEXT_DIST_DIR=.next-v31-demo NEXT_PUBLIC_LABOPS_WORKLOAD_V31=true bun run build` | **PASS** (Exit `0`, 36/36 workers, 44 app routes built) | Built with `BypassSandbox: true` solely because `next/font/google` fetches Google Fonts over HTTPS at build time; `.next`, `.next-blue`, `.next-green` untouched; `tsconfig.json` restored via `git checkout -- tsconfig.json` |
| **Protected Scope Checksum** | `sha256sum -c docs/workload-intelligence-v3.1/.protected_sha256 --quiet` | **PASS** (`PROTECTED_HASHES_OK`) | All 54 Zalo AI / V4 files unchanged |

## 2. Isolated Demo Stack End-to-End Verification (`:8010` + `:3010`)

- **Database isolation**: Created online backup copy via `sqlite3.backup()` at `/tmp/labops_v31_demo_data/labops_v2.sqlite3` (`45,662,208` bytes), leaving live `backend/data/labops_v2.sqlite3` untouched and disabling Zalo/Router tokens (`ZALO_BOT_TOKEN=""`, `ROUTER_API_KEY=""`, `LABOPS_WORKLOAD_V31_ACTIONS_ENABLED="false"`).
- **Verified HTTP & Security Responses (live uvicorn `:8010` + Next.js `.next-v31-demo` `:3010`)**:
  - `GET /api/v1/jobs/snapshot` (Admin): `200 OK`, schema-validated (`workload-snapshot`), `2` nodes (`workstation`, `track`), `1` GPU, quality `PARTIAL`.
  - `GET /api/v1/jobs/analytics` (Admin): `200 OK`, schema-validated (`workload-analytics`), `9` historical series, `host_and_gpu_history: SUPPORTED`, `per_workload_cpu_history: UNSUPPORTED`, `research_group_attribution: UNSUPPORTED`.
  - `GET /api/v1/jobs/history` (Admin): `200 OK`, schema-validated (`workload-history`), `10` observed history items.
  - `GET /api/v1/jobs/events` (Admin): `200 OK`, schema-validated (`workload-events`), `1` audit event item.
  - `GET /api/v1/jobs/snapshot/export.csv` (Admin): `200 OK`, `content-type: text/csv; charset=utf-8` (`252` bytes).
  - Legacy compatibility `GET /api/v1/jobs` & `/api/v1/jobs/active`: `200 OK` (Admin) and `200 OK` on `/api/v1/jobs` (Researcher).
  - RBAC enforcement: Researcher denied (`403`) on `GET /api/v1/jobs/snapshot` directly AND via Next.js BFF `GET /api/labops/jobs/snapshot`. Admin allowed (`200 OK`) via BFF (`73.9ms`).
  - Next.js SSR routes: `GET /dashboard/jobs` (`200 OK`, contains V3.1 root marker) and `GET /dashboard/jobs?v31=0` (`200 OK`).
- **Observed Endpoint Latencies against `45.6 MB` Backup DB**:
  - `/api/v1/jobs/snapshot`: `829.1ms` cold first collector pass; `4.8ms–9.3ms` steady-state across next 5 calls.
  - `/api/v1/jobs/analytics` (`24h` default UI window): `543.3ms` cold, `315.0ms–450.3ms` subsequent runs (`< 800ms`). Over a 5-day multi-host scan (`8,116` historical rows), `analytics` took `1004.9ms–1164.2ms` (note: reading full ORM objects including `jobs_json`; see `DEPLOY_ROLLBACK.md` for optional column-projection speedup if 7d/30d queries need sub-800ms SLA).
  - `/api/v1/jobs/history` (`24h` window): `270.4ms–395.6ms` (`< 800ms`); 5-day scan `989.2ms`.
  - `/api/v1/jobs/events`: `13.4ms`.

## 3. Honest Gaps / Non-Executed Interactive Checks

- **Playwright / Headless DOM Screenshot Run**: Playwright is not installed in `package.json` or `backend/.venv`, so automated browser DOM screenshot capture (`chromium`/`playwright`) was not run against `:3010`; SSR HTML rendering, BFF proxying, Recharts/React client bundle compilation (`36/36` Next.js build pages), and strict lint/type/unit tests were verified directly.
- **Process Detail / V3.1 Signal Execution on Host**: At the time of the backup snapshot (`workloads: 0` live student workloads running), `/api/v1/jobs/{workload_id}/detail` returned `None` (skipped in live HTTP check when `workloads` was empty), while backend unit/integration suite `test_workload_v31_contracts_and_gates.py` verified `/detail`, `pidfd` fail-closed behavior, identity tuple checks, partial tree failures, idempotency replay/conflict, and RBAC directly.
