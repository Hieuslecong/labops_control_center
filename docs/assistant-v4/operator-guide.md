# LabOps Personal AI Agent V4 — Operator Guide

## 1. Zalo Slash Commands

### Legacy V2 Commands (Preserved)
- `/start`, `/help`, `/quota`
- `/status`, `/gpu`, `/alloc`, `/jobs`, `/myjobs`, `/myworkspace`
- `/incidents`, `/incident <id>`, `/ack <id>`
- `/report [today|week]`, `/security` *(Admin)*, `/requests` *(Admin)*, `/link <CODE>`, `/ask <question>`

### Additive V4 Commands (`bot_core.py`)
| Command | Access | Function |
|---|---|---|
| `/new` | Authorized user | Close current channel session and start a clean chat session (`v4_chat_sessions`) |
| `/clear` | Authorized user | Clear active conversation context only (preserves all audit logs and events) |
| `/memory` | Authorized user | View active channel sessions and opted-in preferences |
| `/settings [key=value]` | Authorized user | Read or save opted-in personal preferences (redacted before persistence) |
| `/tasks` | Owner / Admin | List active reminders and hardware watch rules |
| `/watch <target> <metric> <lt\|lte\|gt\|gte> <threshold> [sec]` | Owner / Admin | Create a persistent hardware watch (`task_manager=true` required) |
| `/cancel <task_id>` | Owner / Admin | Cancel an active task or watch rule and suppress pending outbox deliveries |
| `/summary [today\|week]` | Authorized / Admin (`today`) | Summary operational briefing |
| `/compare [host_id]` | Authorized user | Compare live snapshot metrics vs historical KPI window (`INSUFFICIENT_HISTORY` if < 2 samples) |
| `/who [username]` | **Admin only** | Look up LabOps member profile, SSH port, and quotas |
| `/confirm <action_id> <code>` | DM 1-1 only | Confirm R1/R2 proposed actions (`approve_request` / R3 remain Dashboard Step-Up only) |

## 2. Dashboard `Personal AI v4` Tab
Navigate to **Dashboard → AI Assistant → `Personal AI v4`**:
- View live feature flags, Watchdog health, V4 Outbox & Quota state, and Event Coverage summary.
- Create or cancel hardware Watch Rules.
- Review Evidence-Grounded RCA investigations (`VERIFIED RCA` vs `INSUFFICIENT_EVIDENCE`) and submit Admin review feedback.
- Confirm R1/R2 proposals via `POST`, or confirm R3 proposals using Admin password Step-Up (`X-LabOps-Step-Up-Token`).
- Manage opt-in user preferences, clear active chat context, and monitor external connector states (`NOT_CONFIGURED` until configured).
