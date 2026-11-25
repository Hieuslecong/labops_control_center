# LabOps Personal AI Agent V4 — Phase 0 Repository & System Audit

**Generated:** 2026-11-25 (ICT / UTC+7)
**Repository:** `/home/aiot-ws1/projects/labops_control_center`
**Branch:** `feature/labops-ai-sre-agent-v2`
**Commit Baseline:** `c1e3af1` (`feat(repo-guard,mram,sre,ops): add repo approval suppression, web account recovery, zero-downtime deploy, and RBAC hardening`)
**Working Tree Status at Audit Start:** Clean (`git status --short` empty; `.env.local` & `backend/data/` untouched)

---

## 1. Environment, Dependencies & Migration Baseline

### 1.1 Runtime & Package Inventory (Verified)
- **Backend Python:** `Python 3.13.5` (`/home/aiot-ws1/projects/labops_control_center/backend/.venv/bin/python`)
- **Pinned/Installed Backend Libraries (`pip list`):**
  - `fastapi==0.163.1`, `starlette==1.10.0`, `uvicorn==0.54.0`
  - `SQLAlchemy==2.1.9`, `alembic==1.20.1`, `asyncpg==0.32.0`
  - `pydantic==2.14.3`, `pydantic-settings==2.16.3`, `pydantic_core==2.50.4`
  - `python-zalo-bot==0.1.9`, `httpx==0.28.1`, `PyYAML==6.0.3`
  - `opentelemetry-api==1.45.2`, `cryptography==50.0.2`, `python-jose==3.5.0`
  - `pytest==8.4.2`, `pytest-asyncio==0.26.0`, `psutil==7.2.2`
  - **`pydantic-ai`:** **NOT INSTALLED.** Per Spec ADR-04 & Prompt §2.5, keep `native` runner as default behind a shared typed `AgentRuntime` protocol and provide an optional lazy-import `PydanticAIAgentRuntime` adapter gated by dependency presence and capability verification.
- **Database State:**
  - `.env.local` configures `DATABASE_URL=sqlite:///backend/data/labops_v2.sqlite3` (SQLite WAL mode via `_attach_sqlite_pragmas` in `backend/app/db/session.py:24-36`, `busy_timeout=5000`, `foreign_keys=ON`).
  - `backend/app/db/session.py:17-22` normalizes `postgresql+asyncpg://` to sync `postgresql://` for SQLAlchemy 2.x synchronous sessions.
  - **Dual-dialect requirement:** Both SQLite WAL (current production default) and PostgreSQL MUST work seamlessly in all V4 tables, queries, FTS fallbacks, and queue claims (`FOR UPDATE SKIP LOCKED` on PostgreSQL; atomic optimistic CAS `UPDATE ... WHERE id=:id AND fence=:fence` with `rowcount == 1` on SQLite).
  - **Alembic Head:** `0004_labops_mram_v1` (verified via `alembic heads`). Existing 33 tables live in `backend/app/db/models.py` (lines 1–650).
- **Frontend Stack (`package.json`):**
  - `next ^16.4.6`, `react ^19.3.1`, `typescript 7.0.2`, `oxlint ^1.88.0`, `oxfmt ^0.73.0`, `@tabler/icons-react ^3.49.0`, `zod ^4.6.7`, `@tanstack/react-query ^5.104.2`.
  - Protected UI invariants in `src/__tests__/milestone3-frontend-hardening.test.ts`:
    - Zero `setInterval(` calls anywhere in `src/`.
    - Exactly 5 `adminNavGroups` (do NOT add a 6th group).
    - Zero micro-fonts (`text-[8px]`–`text-[11px]`) outside `src/components/ui/**` (always use `text-xs`+).

### 1.2 Baseline Test Results (Observed Before V4 Edits)
- **Zalo / SRE Baseline:** `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_zalo_ai_assistant.py backend/tests/test_ai_sre_agent_v2.py backend/tests/test_repo_post_clone_and_zalo_alerts.py -q --tb=short` → **19 passed, 0 failed** (note: `PYTHONPATH=backend` is required when running individual files that don't adjust `sys.path`).
- **Prior Full Suite Check (on commit baseline):** Backend 266 passed / 2 skipped / 0 failed; Go Agent 12/12 PASS; Frontend `milestone3-frontend-hardening` 9/9 PASS; TypeScript `tsc --noEmit` 0 errors; Oxlint 0 errors (27 pre-existing `no-explicit-any` warnings in `ai-sre-control-view.tsx`).

---

## 2. Existing Implementation Inventory (`file:line` Ground Truth)

### 2.1 Zalo & AI SRE Agent V2 Modules (`backend/app/services/zalo_assistant/`)
| Component | File & Key Lines | Current Responsibilities & Gaps |
|---|---|---|
| Service Facade | `service.py:28-447` | Wires `DatabaseManager`, `QuotaLedger`, `AIGatewayClient`, `SchedulerLeaseManager`, `ZaloIdentityRBACService`, `UnifiedTelemetryCollector`, `UnifiedIncidentEngine`, `ReportGenerator`, `NotificationDispatcher`, `SREToolRegistry`, `ZaloBotEngine`, `RepoSecurityAlertMonitor`. |
| Bot Core | `bot_core.py:53-848` | Defines 17 commands in `COMMANDS` (`:54-72`), rate-limit on `/ask` (`:247-257`), `/link` claim before allowlist (`:304-329`), `handle_command` dispatch (`:283-513`), deterministic handlers (`:517-817`), stateless `/ask` (`:819-836`). **Gap:** No conversation memory, no session lifecycle (`/new`, `/clear`, `/memory`), no tasks/watches, no confirmation state machine. |
| AI Tool Registry & Runner | `tool_registry.py:77-836` | Secret scrubber (`:40-57`), 13 read-only tool specs (`TOOL_SPECS` `:81-179`), RBAC/arg/timeout/audit wrapper (`execute_tool` `:256-355`), `SREAgentRunner._select_deterministic_tools` keyword routing (`:565-650`), `run_investigation` single-turn tool loop (`:652-759`), `AgentToolAuditRecord` DB logging (`:219-255`). **Gap:** Single-pass keyword fallback; limited tools (no member info, VPN status, audit trail, container stats, logs, runbooks, task proposals); no tool-call/result history preservation across turns; container stats lacks DB ownership exact match. |
| 9Router Client | `ai_gateway_client.py:45-212` | OpenAI-compatible HTTP client targeting `ROUTER_BASE_URL` (`http://127.0.0.1:20128/v1`), `ROUTER_MODEL` (`model_free`), `timeout=25`. Strips `<think>` tags (`:39-42`). Methods: `check_health` (`:64-90`), `generate_chat_response` (`:108-167`), `generate_with_tools` (`:169-212`). **Gap:** Single round `generate_with_tools`, no circuit breaker or request budget tracking, no streaming/structured-output probe telemetry. |
| Worker & Scheduler | `worker.py:50-700` | Single-consumer DB lease `zalo_sre_worker` (`:192-263`), polling loop (`bot.get_update(offset=offset, timeout=15, limit=20)` `:308`), `process_incoming_message` (`:77-190`), 30s repo alert check (`:344`), 60s infra telemetry & incident evaluation (`:380-427`), MRAM SLA expiry sweep (`:670-677`), outbox flush (`:367-378`), 08:00 ICT daily brief (`:559-620`), Friday 17:00 ICT weekly report (`:622-660`). **Gap:** `offset` held in memory only (`:298`, lost on restart); no durable inbox table; no request-created/overdue reminder cadence (12h/22h/24h); no evening 17:00 delta brief; weekly report on Friday 17:00 vs Spec proposal Monday 08:00 (must preserve/coordinate safely). |
| Incident Engine | `incident_engine.py:104-747` | Rules for node heartbeat/storage/filesystem (`:308-381`), GPU fault/temp (`:384-431`), Docker OOM/restart/isolation (`:434-491`), training jobs (`:494-527`), PostgreSQL down (`:530-544`), SSH failure spike (`:547-567`). Lifecycle `OPEN -> ACKNOWLEDGED -> RESOLVED (+ REOPENED)` with fingerprint dedup, hysteresis (`2`), cooldown (`30m`), maintenance window. **Gap:** Emits no durable `EventEnvelope` in DB tx; lacks parent-child dependency correlation table (`node.unreachable` -> child stale GPUs/telemetry), auto-recovery notifications, and scheduled P0/P1 repeat reminders. |
| Identity & RBAC | `identity_rbac.py:60-798` | `ZaloIdentityLinkRecord` binding via one-time hashed code (`LAB-XXXXXX`) or Admin direct bind, `NotificationSubscriptionRecord` (`receive_p0`, `p1`, `daily_report`, `weekly_report`, `repo_alerts`, `quiet_hours`), 1-1 DM verification (`record_observed_private_chat` `:121-137`, `verify_daily_brief_recipient` `:139-199`). |
| Quota & Dispatcher | `quota_ledger.py:42-422`, `notification_dispatcher.py:67-414` | `QuotaLedger` (`zalo_quota_ledger.json`, flock + idempotency + two-phase `RESERVED -> SENT / DELIVERY_UNKNOWN / FAILED_REFUNDED`, V2 limits `2800` std + `200` crit = `3000`). `NotificationDispatcher` DB `notification_outbox` (`PENDING -> SENT / FAILED_RETRYABLE / DELIVERY_UNKNOWN / FAILED_PERMANENT / SUPPRESSED`) + `notification_delivery_attempts`. **Gap:** Dispatcher does in-memory sort/loop without row claim/lease/fence columns (`FOR UPDATE SKIP LOCKED` / CAS), no `expires_at`, no Unicode chunking, no P2/P3 shedding near cap, and legacy Mram/Repo monitors partially bypass `NotificationDispatcher` via direct `bot.send_message`! |
| Repo Alert Monitor | `repo_alert_monitor.py:145-620` | Polls Go Agent `:9100` (`_fetch_agent_requests_sync`), tracks `sent_alerts` & `approved_repos` in JSON, upserts incidents, calls `bot.send_message` directly (`:588`). Includes `suppress_repo_alerts` (`:221-262`). |
| Report Generator | `report_generator.py:52-1309` | Daily Executive Brief (`report_runs`, `daily_report_snapshots`, catch-up window 4h, missed-window P1 incident) & Weekly Operational Report (`zalo_reports.json`). |
| Telemetry Collector | `telemetry_collector.py:72-496` | Collects snapshot from `labops_service` + `HostRegistry` + `WorkspaceController` + `beszel`/`arcane` adapters; `classify_freshness` (`ONLINE`, `DEGRADED`, `STALE`, `OFFLINE`, `UNKNOWN`); persists `telemetry_history_samples` for 24h KPIs & disk forecast. |
| API Endpoints | `ai_assistant.py:19-413`, `_lib.ts:79-146` | RBAC-hardened FastAPI routes + Next.js BFF fail-closed proxy. Admin-only: `overview`, `config`, `reports*`, `daily-brief/*`, `tools`, `history`, `incidents/{id}/resolve`, `incidents/evaluate`, `identity-links*`, `subscriptions*`. User-scoped: `incidents`, `incidents/{id}`, `incidents/{id}/ack`, `command`, `ask`. Open: `heartbeat` (should stay secret-free). |

### 2.2 Existing Bot Commands & Role Matrix (`bot_core.py:54-451`)
| Command | Roles Allowed | Data Source / Side Effects |
|---|---|---|
| `/start`, `/help`, `/quota` | Any authorized user | Static greeting, role-filtered command list, `QuotaLedger.get_status()` |
| `/status`, `/gpu` | Any authorized user | `UnifiedTelemetryCollector.collect_snapshot()` (cluster-wide host/GPU summary) |
| `/alloc`, `/allocations`, `/gpus` | Admin (all), Researcher (own only) | `labops_service.get_gpu_allocations_by_device()` / `get_gpu_metrics` |
| `/jobs` | Admin (all), Researcher (own only) | `get_active_jobs` tool + `psutil` fallback filtered by owner |
| `/myjobs`, `/myworkspace` | Any authorized user (own only; Admin sees all workspaces) | `get_active_jobs`, `get_workspace_usage` |
| `/incidents`, `/incident <id>`, `/ack <id>` | Admin (all), Researcher (own `owner_id` only) | `UnifiedIncidentEngine` (`ack` mutates incident status) |
| `/report [today\|week]` | Read weekly: authorized; `today` or generate: **Admin only** | `ReportGenerator` |
| `/security` | **Admin only** | `get_security_events` tool |
| `/requests`, `/ssh-requests` | **Admin only** | `get_pending_ssh_requests` (`mram_service`) |
| `/link <CODE>` | Pre-authorization allowed (DM/auth check on code) | `ZaloIdentityRBACService.claim_link_code()` |
| `/ask <question>` / unknown text | Any authorized user (rate-limited 10/60s) | `SREAgentRunner.run_investigation()` |

### 2.3 Core Request, Auth & Provisioning Paths
- `backend/app/services/mram_service.py`:
  - `submit_ssh_request` (`:1643-1851`): writes `ServerAccessRequestRecord`, `RequestResourceRecommendationRecord`, `PolicyAcceptanceRecord`, updates `MemberProfileRecord` in `self.db.session_scope()` (`:1796-1820`), then issues `ApprovalInvitationRecord` & sends immediate Zalo DM inline if `MRAM_IMMEDIATE_ZALO_SEND=1` (`:1550-1585`).
  - `decide_ssh_request` (`:2220-2357`): validates decision/version, creates `CapacityReservationRecord` & `RequestApprovalRecord`, updates request & member profile, marks `ApprovalInvitationRecord` USED/REVOKED (`:2240-2337`), then calls `provision_approved_request` (`:2350`).
  - `expire_overdue_requests` (`:1898-1936`): sweeps 24h expired requests inside `session_scope()`.
  - `provision_approved_request` (`:2362-2630`): idempotent via `idempotency_key=f"mram-prov:{request_id}:v{app_rec.request_version}"`, executes runtime provisioning (or dry-run in tests) + verification gate, commits `VERIFIED_READY` / `PROVISIONING_FAILED` (`:2577-2611`).
- `backend/app/api/v1/endpoints/repositories.py`:
  - `submit_repository_request` (`:325-350`), `rescan_repository_request` (`:354-421`), `decide_repository_request` (`:652-685`, calls `monitor.suppress_repo_alerts` on `APPROVED`).
- **Dashboard Authentication (`src/app/api/labops/_lib.ts`, `src/proxy.ts`):**
  - Cookie `labops_session` (HMAC-SHA256, max 8h TTL) or Clerk fallback; BFF validates `validateSameOriginMutation` (CSRF Origin/Referer) and `isAdminRequiredLabOpsPath`, attaches short-lived (300s) HMAC Bearer token or session rawToken, forwards to FastAPI `:8000`.
  - Dashboard AI page (`src/app/dashboard/ai-assistant/page.tsx`) calls `await requireAdmin()` and renders `AISREControlView` (`src/features/ai-assistant/components/ai-sre-control-view.tsx`) with 6 tabs.

---

## 3. Event Producer Coverage Matrix (Actual Codebase vs Spec §4)

| Domain | Event Types | Real Producer / Source | Available? | Freshness / Trigger Point | Default Audience | Gap / Status |
|---|---|---|---|---|---|---|
| Node | `node.unreachable`, `node.recovered`, `telemetry.stale` | `UnifiedTelemetryCollector` + `HostRegistry` + `HostMonitorAdapter` + `UnifiedIncidentEngine` | Yes | 60s worker poll; hysteresis=2 (`h:m`), freshness (`ONLINE`/`STALE`/`OFFLINE`/`UNKNOWN`) | Admin | Wire durable `EventEnvelope` in `IncidentEngine`; correlate children; notify recovery. |
| GPU | `gpu.temperature.high`, `gpu.error`, `gpu.throttle`, `gpu.missing` | `HostMonitorAdapter` (`nvidia-smi`), `UnifiedIncidentEngine` (`GPU_THERMAL_HIGH`, `GPU_HARDWARE_FAULT`) | Partial | 60s poll (`temp_celsius`, `xid_errors`, `ecc_uncorrectable`, `gpu_fault`) | Admin + owner | `gpu.throttle` / `clock_throttle_reasons` not yet parsed by `nvidia-smi` query -> mark throttle inference `NOT_SUPPORTED` unless present; `gpu.missing` detectable via expected vs observed count. |
| CPU / RAM | `resource.pressure` | `UnifiedTelemetryCollector` (`cpu_usage_percent`, `ram_usage_percent`, `oom_killed`) | Yes | 60s poll + sustained rule in V4 policy | Admin / owner | Add sustained CPU/RAM threshold evaluation to V4 policy/engine. |
| Storage | `storage.threshold`, `quota.exceeded` | `UnifiedTelemetryCollector` (`disk_usage_percent`, `FILESYSTEM_ERROR`), workspace storage usage | Yes | 60s poll (`disk_warn_percent=85`, `disk_crit_percent=95`) | Admin / owner | Add per-workspace `quota.exceeded` event evaluation (`>=100%` / soft warning). |
| Service | `service.down`, `service.recovered` | `HostMonitorAdapter.get_external_daemons_telemetry` (Docker/PostgreSQL/Trivy/WireGuard) + `DatabaseManager` | Yes | 60s poll (`POSTGRESQL_UNAVAILABLE` exists; expand Docker/WireGuard/Agent/9Router) | Admin | Add `service.recovered` & expand probe-backed service rules. |
| Network / VPN | `network.degraded`, `vpn.unavailable`, `ssh.failure` | `wg_easy_adapter` / Go Agent `node_agent_adapter` / `audit_events` (`SSH_AUTH_FAILURES_SPIKE`) | Partial | Poll + audit window | Admin | Probe read-only only; external latency/packet-loss probes disabled (`NOT_CONFIGURED`). |
| Backup / Cert | `backup.failed`, `certificate.expiring` | `ReportGenerator._check_latest_backup_status()` (file existence only) | **No** | No backup scheduler or TLS cert probe configured for local bot | Admin | Mark `NOT_CONFIGURED` / `NOT_SUPPORTED` (no fake data). |
| Request | `request.created`, `request.updated`, `request.approved`, `request.rejected`, `request.overdue`, `request.withdrawn` | `mram_service.py` (`submit_ssh_request`, `decide_ssh_request`, `expire_overdue_requests`) | Yes | Same DB transaction + worker SLA sweep | Admin + verified applicant (+ supervisor if DB-mapped) | Hook durable `EventEnvelope` in `mram_service` tx; add 12h/22h/24h reminders in V4 scheduler; `withdrawn` `NOT_SUPPORTED` if endpoint absent. |
| Provisioning | `provisioning.succeeded`, `provisioning.failed` | `mram_service.provision_approved_request` | Yes | DB completion tx | Admin + applicant | Hook durable `EventEnvelope` on gate pass/fail. |
| Workspace | `workspace.failed`, `workspace.expiring`, `workspace.expired` | `WorkspaceController`, `mram_service.sweep_expired_ssh_access`, `UnifiedIncidentEngine` | Yes | Poll (`DOCKER_OOM_KILLED`, `DOCKER_RESTART_LOOP`) + daily/sweep expiry | Owner + Admin | Add 7/3/1 day `workspace.expiring` scan from `MemberProfileRecord.ssh_expires_at`. |
| Job | `job.started`, `job.completed`, `job.failed`, `job.stalled` | `labops_service.jobs` (`psutil` + `nvidia-smi`), `UnifiedIncidentEngine` | Partial | 60s poll (`psutil` PID list, `status`, `stalled`, `cuda_oom`) | Owner + Admin | No batch scheduler (Slurm) — job completion inferred from tracked PID disappearance only; mark SDK events `NOT_CONFIGURED` unless adapter registered. |
| Training | `training.oom`, `training.nan`, `training.checkpoint` | Structured adapter (`CUDA_OOM` flag exists if reported) | **Limited** | Structured payload only (never guessed from GPU %) | Owner | Unstructured log tail / wandb / tensorboard not integrated -> `training.nan`/`training.checkpoint` `NOT_CONFIGURED`. |
| Security / Repo | `security.denied`, `security.secret_detected`, `security.privilege_changed`, `repository.cloned`, `repository.scan_failed`, `repository.risk_found` | `repositories.py`, `RepoSecurityAlertMonitor`, Go Agent `:9100`, `audit_events` | Yes | Event-driven webhook/push + 30s reconciliation | Admin | Hook durable `EventEnvelope` into repo submission, scan result, approval & agent-tool denies. |
| GitHub External | `github.action_failed`, `github.review_requested`, `github.issue_assigned` | No inbound GitHub webhook currently mounted | **No** | N/A | Admin | Implement disabled adapter + verified HMAC endpoint behind feature flag `external_connectors.github=false`. |
| Personal Admin | `mail.important`, `calendar.upcoming`, `research.update` | None | **No** | N/A | Admin only | Implement opt-in disabled adapters (`NOT_CONFIGURED`) isolated to Admin role. |
| Assistant Tasks | `task.due`, `task.condition_met`, `task.failed` | Does not exist yet | **New in V4** | Scheduler tick | Task owner | Implement in V4 Phase 3. |
| Delivery / Health | `notification.failed`, `worker.stalled`, `ai.unavailable` | `NotificationDispatcher`, `AIGatewayClient`, Watchdog | Partial | Dispatcher DLQ, AI circuit breaker, Watchdog checks | Admin | Implement in V4 Phase 1–4. |

---

## 4. Zalo Bot Platform & 9Router Contract Analysis

### 4.1 `python-zalo-bot 0.1.9` Capabilities & Delivery Risks
- **Mode:** Currently long-polling via `Bot(token).get_update(offset=offset, timeout=15, limit=20)` in `worker.py:308`. Webhook methods `set_webhook(url, secret_token)` and `delete_webhook()` exist on `Bot`, using `X-Bot-Api-Secret-Token` validation if webhook mode is enabled.
- **Formatting & Limits:** `send_message(chat_id, text, reply_to_message_id=None)` sends plain UTF-8 text and returns a `Message` object (`message_id`). **No Markdown parser, no inline interactive keyboard buttons, and no user read receipts are verified.** Only provider API acceptance (`message_id`) is observable; delivery timeouts must transition to `UNKNOWN_DELIVERY` rather than claiming success or blindly re-sending.
- **Quota Model:** V2 `QuotaLedger` tracks internal estimates (`2800` standard + `200` critical = `3000`) and sets `"provider_confirmed": False` (`quota_ledger.py:165`). V4 introduces configurable `monthly_budget=3000` / `critical_reserve=600` (with 80%/90% soft shedding) as a configuration proposal in V4 Policy without mutating V2 `QuotaLedger` defaults (preserving V2 unit tests).
- **Cursor & Duplicate Risks Found in Audit:**
  1. `worker.py:298` initializes `offset = None` in RAM; crash/restart may re-receive recent updates. Solution: persist cursor in `v4_worker_cursors` AFTER durable inbox commit + deduplicate logically by `(channel, bot_id, external_message_id)` in `v4_message_inbox`.
  2. Dual sender paths exist (`NotificationDispatcher` SQLite outbox vs `RepoSecurityAlertMonitor` direct `bot.send_message` vs `mram_service` inline `asyncio.run` DM when `MRAM_IMMEDIATE_ZALO_SEND=1`). V4 unifies outbound delivery behind `OutboxV4Service` when `notification_v2=True` (keeping legacy intact while flag is `False`).

### 4.2 9Router Model Alias Inventory (`http://127.0.0.1:20128/v1/models`)
Verified read-only on 2026-11-25 — **25 configured aliases**, zero paid fallback requested:
- Active alias: `model_free` (`owned_by: combo`, `tools: True`, `reasoning: True`, `contextWindow: 200000`, `maxOutput: 262144`)
- Available individual free aliases (24): `nous/meituan/longcat-2.0:free`, `nous/meituan/longcat-2.5-preview:free`, `nous/stepfun/step-5-preview:free`, `nous/upstage/solar-mini4:free`, `nous/stepfun/step-3.7-flash:free`, `nous/poolside/laguna-s-2.1:free`, `nous/poolside/laguna-xs-2.1:free`, `nous/stealth/missingno`, `nous/tencent/hy4-flash:free`, `nous/meituan/longcat-2.5:free`, `mmf/mimo-auto`, `oc/big-pickle`, `oc/mimo-v2.6-flash-free`, `oc/deepseek-v4.1-flash-free`, `oc/iceman-free`, `oc/beam-free`, `oc/flint-40b-free`, `oc/nemotron-3.5-super-vl-free`, `oc/jev-1.13-free` (`tools: None`), `oc/nemotron-3.5-lightning-free`, `oc/hy4-flash-free`, `oc/tidepool-alpha-free`, `oc/glm-5.3-flash-free`, `oc/inkling-2-small-free`.
- **Capability Gate Decision:** Implement synthetic & opt-in `ModelCapabilityProbe` testing plain chat, typed tool calling, argument schema validation, multi-tool result pairing, conversation history, malformed JSON handling, timeouts, budget caps, and fallback to deterministic native response. Because `pydantic-ai` is not installed and 9Router free upstream models can vary in strict OpenAI tool-call pairing, **`agent_runtime=native` is selected as the authoritative default**, with `AgentRuntime` typed protocol allowing future Pydantic AI cutover once installed and validated.

---

## 5. Threat Model Summary

| Threat / Boundary | Risk in LabOps Context | Mitigation in V4 Design |
|---|---|---|
| Identity Spoofing / BOLA / IDOR | Researcher asks AI for another member's workspace/logs/container or submits forged username/role args. | Principal always reloaded from signed auth / `ZaloIdentityLinkRecord` server-side; model cannot supply caller identity; container stats require exact DB-owned `container_id` (no prefix matching); omitted target scopes to caller's authorized records only. |
| Prompt Injection | Malicious text in repo names, commit messages, logs, web pages, or emails tries to invoke write tools or exfiltrate secrets. | Untrusted strings tagged/scrubbed before prompt; write tools removed from read-only investigation/chat runners; R0–R3 risk tiers isolate read from execution; SSRF allowlist blocks internal/private outbound URLs. |
| Cross-Session / Group Chat Leakage | Admin conversation or personal email/ops data leaks into Researcher or group chat session. | Session key includes `(tenant_id, principal_id, channel, bot_id, chat_id, session_id)`; group chats rejected for R2/R3 confirmations & personal skills; personal skills restricted strictly to `ADMIN` role. |
| Stale Data Misreporting | Cached assistant response or missing node heartbeat interpreted as healthy / recovered. | Every live metric tool queries collector fresh at call time and returns `observed_at` + `freshness`; missing/stale samples break watch hold windows and never clear active incidents. |
| Duplicate Writes / Queue Split-Brain | Double webhook/poll, worker crash after send, or stale lease owner finalizing completed outbox row. | Durable inbox unique key + committed polling cursor; `claim_batch` fence increment committed pre-I/O; fenced finalize (`WHERE id AND fence AND claimed_by`); atomic core `idempotency_key` + `version` check on proposals. |
| Secret / PII Leakage | Bot token, session secret, SSH key, or private Watchdog URL logged in `agent_tool_audit`, outbox, or model prompt. | Centralized redactor scrubbing keys/tokens/URLs before prompt storage, DB audit, outbox persistence, and OTel/structured export; Dashboard review links contain no auth tokens/secrets (only opaque ID/exchange flow). |

---

## 6. Additive Migration & Non-Destructive Rollback Plan

- **Zero modification to existing 33 tables:** All V4 persistence lives in 17 new additive `v4_*` tables created by Alembic revision `0005_labops_personal_ai_v4` (`down_revision = "0004_labops_mram_v1"`). Existing SQLite/PostgreSQL production rows, workers, and queries continue unaffected whether or not `0005` has been applied (once applied, feature flags remain OFF until explicitly enabled).
- **Feature Flag Isolation:** All new behaviors default to `false` (`enabled=false`, `event_engine=false`, `notification_v2=false`, `task_manager=false`, `natural_language=false`, `proactive_investigation=false`, `write_actions=false`, `watchdog.enabled=false`, all `external_connectors.*=false`).
- **Safe Rollback:** Disable V4 flags (`ASSISTANT_V4_*=false`), stop/drain V4 consumer cleanly preserving pending queues/tasks, re-enable legacy delivery with cursor handoff, and optionally downgrade Alembic only on staging/disposable DBs (never run destructive down-migration on production data by default).

---

## 7. Current Honest Blockers (External / Authorization Required)
1. **Production DB & Service Rollout:** Explicit authorization required to run `alembic upgrade head`, deploy via `deploy-zero-downtime.sh`, or flip V4 feature flags on `:8000`/`:3000` or `labops-ai-sre-worker`.
2. **External Watchdog (Healthchecks):** Requires external endpoint signup/secret URL provisioned outside lab power/network domain (`ASSISTANT_V4_WATCHDOG_PING_URL`). Implemented with adapter & degraded-coverage visibility; external checks marked `NOT_CONFIGURED`.
3. **Personal Connectors (Email, Calendar, Documents, GitHub Webhooks, Web Search):** Disabled (`NOT_CONFIGURED`) pending explicit Admin consent, OAuth/credential provisioning, and repo/domain allowlist.
4. **Zalo Live Entitlement & Group Policy:** Exact paid quota tier beyond the assumed 3,000/month and official Bot Platform group/read-receipt behavior require verification against the lab's Zalo developer console.
5. **`supervisor` Group Scope:** Currently `SupervisorAssignmentRecord` tracks student-supervisor links in DB, whereas JWT auth roles only define `ADMIN | RESEARCHER`; any supervisor routing must strictly verify active DB assignment rows rather than trusting display names.
6. **Hardware/Source Limits:** `rack` (`192.168.167.250`) is currently OFFLINE in lab; backup/cert expiry & structured training NaN/checkpoint adapters have no underlying producer daemon and are marked `NOT_CONFIGURED`.
