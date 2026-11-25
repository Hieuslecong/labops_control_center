# LabOps Personal AI Agent V4 — Architecture Overview

## 1. Design Principles
- **Event-Driven Core Reliability First:** Critical alerts (`P0`/`P1`), SSH request lifecycle transitions, provisioning outcomes, and security alerts are driven by transactional DB events and deterministic notification policies — never blocked on an LLM or 9Router response.
- **Additive Schema & Flagged Rollout:** All V4 tables live in 17 purely additive `v4_*` tables (`alembic` revision `0005_labops_personal_ai_v4`). Every rollout flag in `AssistantV4Config` defaults to `false`.
- **Dual-Dialect Storage:** Supports SQLite WAL (current default `.env.local`) and PostgreSQL (`FOR UPDATE SKIP LOCKED` on PG, optimistic compare-and-set `WHERE ... AND fence=:fence` on SQLite).

## 2. Layered Component Diagram
```text
Core Mutations (mram_service, incident_engine, repositories)
   │  [same DB session_scope]
   ▼
v4_event_store  +  v4_event_consumer_receipts   (event_store.py)
   │
   ├─► EventPolicyEngine + QuotaGovernorV4 + DependencyCorrelator  (notification_policy.py)
   │      └─► v4_notification_outbox + v4_delivery_attempts        (inbox_outbox.py)
   │
   ├─► TaskService + PersistentScheduler + WatchdogAdapter           (task_scheduler.py)
   │      └─► v4_assistant_tasks + v4_watch_rules
   │
   └─► InvestigationService + Reviewed Runbooks                    (investigation_service.py)
          └─► v4_investigations (verified cause vs missing_information)

Ingress & Conversational Plane:
Zalo Polling / Webhook ──► v4_message_inbox + v4_worker_cursors    (inbox_outbox.py, worker.py)
Dashboard HTTP API     ──► /api/v1/ai-assistant/v4/*               (ai_assistant.py + BFF _lib.ts)
                               │
                               ▼
                        ZaloBotEngine / AssistantV4Facade          (bot_core.py, facade.py)
                          ├─► IntentRouter (EXPLICIT_COMMAND | WATCH_OR_REMINDER_NL | GENERAL_CHAT | LABOPS_AGENT)
                          ├─► ScopedSessionMemory (v4_chat_sessions, v4_chat_messages, v4_user_preferences)
                          ├─► NativeAgentRuntime + CircuitBreaker + ModelCapabilityProbe (agent_runtime.py)
                          ├─► SecureToolExecutor (read-only + proposal tools, exact RBAC) (secure_tools.py)
                          ├─► SkillRegistry + KnowledgeBaseService + ConnectorsService + SafeOutboundGuard
                          └─► ActionProposalService (v4_action_proposals, R0-R3 gate) (approval_service.py)
```

## 3. Additive Database Tables (`0005_labops_personal_ai_v4`)
1. `v4_event_store`, `v4_event_consumer_receipts`
2. `v4_message_inbox`, `v4_worker_cursors`
3. `v4_notification_outbox`, `v4_delivery_attempts`
4. `v4_incident_meta`
5. `v4_assistant_tasks`, `v4_watch_rules`
6. `v4_action_proposals`
7. `v4_chat_sessions`, `v4_chat_messages`, `v4_user_preferences`
8. `v4_knowledge_documents`, `v4_investigations`
9. `v4_connector_states`, `v4_webhook_deliveries`
