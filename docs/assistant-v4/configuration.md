# LabOps Personal AI Agent V4 — Configuration Reference

Authoritative schema: `backend/app/services/assistant_v4/config.py` (`AssistantV4Config`, `load_assistant_v4_config`).
Example template: `config/assistant_v4.example.yaml`.

## 1. Feature Flags (ALL Default `false`)
| YAML Key | Env Override (supported) | Default | Purpose |
|---|---|---|---|
| `assistant_v4.enabled` | `ASSISTANT_V4_ENABLED` | `false` | Master V4 worker & NL gate |
| `assistant_v4.event_engine` | `ASSISTANT_V4_EVENT_ENGINE` | `false` | Reconciliation & recovery evaluation |
| `assistant_v4.notification_v2` | `ASSISTANT_V4_NOTIFICATION_V2` | `false` | V4 policy, outbox delivery & suppression of legacy `MRAM_IMMEDIATE_ZALO_SEND` |
| `assistant_v4.shadow_mode` | `ASSISTANT_V4_SHADOW_MODE` | `false` | Evaluate events & record `shadow_only` without outbound delivery |
| `assistant_v4.task_manager` | `ASSISTANT_V4_TASK_MANAGER` | `false` | Persistent reminders, hardware watches & NL watch creation |
| `assistant_v4.natural_language` | `ASSISTANT_V4_NATURAL_LANGUAGE` | `false` | Route `/ask` & free text through `AssistantV4Facade` |
| `assistant_v4.proactive_investigation` | `ASSISTANT_V4_PROACTIVE_INVESTIGATION` | `false` | Automated evidence-grounded RCA triggers |
| `assistant_v4.write_actions` | `ASSISTANT_V4_WRITE_ACTIONS` | `false` | Allow confirmed R1–R3 proposals to mutate core state |
| `assistant_v4.watchdog.enabled` | `ASSISTANT_V4_WATCHDOG_ENABLED` | `false` | External dead-man ping adapter (`ASSISTANT_V4_WATCHDOG_PING_URL`) |

## 2. Subsystems & Policy Defaults
- **YAML Config Path:** Set `ASSISTANT_V4_CONFIG_PATH=/path/to/assistant_v4.yaml`.
- **Ingress:** `ingress_mode: polling` (`polling | webhook`; single active mode enforced by `DurableMessageInbox.ensure_ingress_mode`).
- **Notifications (`NotificationsV4Config`):** `monthly_budget=3000`, `critical_reserve=600`, `soft_percent=80`, `high_percent=90`, `target_chars=1800`, `p0_unacked_repeat_minutes=30`, `p0_acked_repeat_minutes=360`, `p1_repeat_hours=24`.
- **Legacy Preservation:** Existing V2 `QuotaLedger` (`2800` standard / `200` critical) remains unchanged when `notification_v2=false`.
- **External Connectors (`ExternalConnectorsConfig`):** `github`, `web_search`, `email`, `calendar`, `documents` all default to `false` (`NOT_CONFIGURED`).
