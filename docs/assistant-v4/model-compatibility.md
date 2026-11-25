# LabOps Personal AI Agent V4 — Model & Runtime Compatibility

## 1. AI Gateway Integration
- Targets LabOps local 9Router gateway (`http://127.0.0.1:20128/v1`) with configured free alias `model_free`.
- Policy: `model.paid_fallback = false` (enforced by `AssistantV4Config.enforce_paid_policy()`; setting `true` raises a startup configuration error).

## 2. Agent Runtime & Dependency Audit
- **Verified Environment:** `pydantic-ai` is **not installed** in `backend/.venv`.
- **Active Runtime:** `NativeAgentRuntime` (`backend/app/services/assistant_v4/agent_runtime.py`) implementing the shared `AgentRuntime` protocol behind `select_agent_runtime(...)`.
- **Optional Future Upgrade:** If `pydantic-ai` is installed and passes capability probes, `agent_runtime` supports configuration selection (`native | pydantic_ai`).

## 3. Reliability Controls & Capability Probe
- **Bounded Execution Budgets (defaults):** `request_limit=4`, `tool_limit=8`, `wall_timeout_s=45`, `daily_user_request_limit=120`.
- **Circuit Breaker:** Opens after 3 consecutive AI failures (`cooldown_seconds=120`), emits single `ai.unavailable` event, falls back cleanly to deterministic slash commands (`/status`, `/incidents`, `/jobs`, `/quota`), and emits `service.recovered` on recovery.
- **General Chat Isolation:** `IntentRouter` routes general coding/research questions (`GENERAL_CHAT`) with `allowed_tools=[]` so internal lab tools and data are never queried.
- **ModelCapabilityProbe:** Tests plain chat and synthetic read-only tool schema invocation safely using `get_node_status` without exposing sensitive data.
