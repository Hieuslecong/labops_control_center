# LabOps Personal AI Agent V4 — Implementation Status & Blockers

## 1. Phase Completion Matrix
| Phase | Scope | Key Modules | Status |
|---|---|---|---|
| **Phase 0** | Repo audit, schema baseline, event matrix, 9Router contract, threat model | `docs/assistant-v4/00-audit.md`, `config/assistant_v4.example.yaml`, `backend/app/services/assistant_v4/config.py` | **Complete** |
| **Phase 1** | 17 `v4_*` tables, Alembic `0005`, transactional event hooks, durable inbox & fenced outbox | `models.py`, `0005_labops_personal_ai_v4.py`, `event_store.py`, `inbox_outbox.py`, `mram_service.py`, `incident_engine.py`, `repositories.py` | **Complete** |
| **Phase 2** | Deterministic P0/P1 notification policy, quota governor, dependency correlator, verified recovery, Monday digest logic | `notification_policy.py` | **Complete** |
| **Phase 3** | Persistent tasks, validated watch rules, bounded catch-up scheduler, watchdog adapter | `task_scheduler.py` | **Complete** |
| **Phase 4** | Scoped session memory, exact RBAC `SecureToolExecutor`, `NativeAgentRuntime`, `CircuitBreaker`, `IntentRouter`, `ModelCapabilityProbe` | `memory_service.py`, `secure_tools.py`, `agent_runtime.py` | **Complete** |
| **Phase 5** | Evidence-grounded RCA investigations (`REVIEWED_RUNBOOKS`), explicit evidence vs gaps | `investigation_service.py` | **Complete** |
| **Phase 6** | Skill registry, scoped KB, `SafeOutboundGuard` (SSRF), disabled opt-in connectors & HMAC GitHub webhook | `skills_and_connectors.py` | **Complete** |
| **Phase 7** | R0–R3 `ActionProposalService` confirmation state machine, DM/Step-Up separation | `approval_service.py` | **Complete** |
| **Phase 8** | Facade wiring (`service.py`, `bot_core.py`, `worker.py`), `/v4/*` API + BFF RBAC, Dashboard `Personal AI v4` UI, 100-scenario benchmark, docs | `facade.py`, `ai_assistant.py`, `_lib.ts`, `assistant-v4-panel.tsx`, `ai-sre-control-view.tsx`, `test_assistant_v4_benchmark_100.py`, `docs/assistant-v4/*.md` | **Complete (Staged/Flagged OFF)** |

## 2. Explicit Remaining Blockers (Not Executed / Out-of-Repo Dependencies)
1. **Production Deployment & Service Activation:** Live `:8000`/`:3000`, `labops-ai-sre-worker`, `.env.local`, and `backend/data/labops_v2.sqlite3` remain completely untouched per authorization boundary.
2. **External Credentials & Endpoints:**
   - External watchdog dead-man ping URL (`ASSISTANT_V4_WATCHDOG_PING_URL`) is not provisioned.
   - GitHub webhook secret (`ASSISTANT_V4_GITHUB_WEBHOOK_SECRET`), `allowed_github_repos`, and personal OAuth connectors (`personal_email`, `personal_calendar`, `personal_docs`, `web_search`) remain disabled (`NOT_CONFIGURED`).
3. **Optional Dependency (`pydantic-ai`):** Not installed in `backend/.venv`; `NativeAgentRuntime` handles all model orchestration behind `AgentRuntime`.
4. **Hardware & Telemetry Sources:** Live `track` node (`192.168.167.250`) offline state, GPU clock throttle reason fields, backup daemon probes, TLS cert probes, and structured ML training SDK metrics remain `NOT_CONFIGURED` / `NOT_SUPPORTED` until upstream hardware/daemons exist.
