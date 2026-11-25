# LabOps Personal AI Agent V4 — Security & Threat Model

## 1. Identity & RBAC Enforcement
- **Server-Side Identity Verification:** Every Zalo tool call and confirmation reloads the principal from signed auth / `ZaloIdentityLinkRecord` via `identity_reloader`. Model tool arguments cannot forge caller username or role.
- **Exact Resource Ownership (`secure_tools.py`):**
  - `get_container_stats` / `get_workspace` require exact match on `id`, `name`, or `container_id`. Partial prefix matching (e.g., `ws-sv` matching `ws-sv_hoa`) is rejected with `CONTAINER_NOT_FOUND`.
  - Non-admin callers specifying another user's job, request, task, member profile, or container receive `DENIED` / `PERMISSION_DENIED`.
  - Omitted container target lists only the caller's own workspaces for non-admin callers.
- **Next.js BFF Fail-Closed RBAC (`src/app/api/labops/_lib.ts`):**
  - Admin-required V4 paths enforced at the BFF layer before backend forwarding: `ai-assistant/v4/metrics`, `ai-assistant/v4/connectors`, `ai-assistant/v4/events/coverage`, `ai-assistant/v4/investigations*`, and `POST ai-assistant/v4/approvals/*/confirm`.

## 2. Action Risk Tiers & Write Boundaries (`approval_service.py`)
| Risk Tier | Actions | Gate |
|---|---|---|
| `R0` (Read-Only) | `read`, `list`, `search`, `explain` | Authorized principal tool call |
| `R1` (Personal Task/Ack) | `create_task`, `create_watch`, `cancel_task`, `ack_incident` | Explicit slash command or 1-1 DM one-time code |
| `R2` (Incident/Reject) | `resolve_incident`, `reject_request` | Admin only; 1-1 DM `/confirm <action_id> <code>` (no generic `OK`/`YES` or group chats) or Dashboard POST; `write_actions=true` required |
| `R3` (Privileged Provision/Quota) | `approve_request`, `grant_gpu`, `revoke_gpu`, `change_quota`, `restart_workspace` | **Blocked on Zalo**; Dashboard `POST` + Admin + single-use `X-LabOps-Step-Up-Token` (`v4_confirm_action`) + `write_actions=true` required |
| `FORBIDDEN` | `shell_exec`, `sudo`, `delete_user_workspace`, `modify_network`, `raw_sql`, etc. | Permanently rejected (`FORBIDDEN_ACTION`) |

- Proposals bind `actor_id`, `chat_id`, `session_id`, `canonical_args_hash`, live `target_version`, `nonce_hash`, and TTL (`expires_at`). Role revocation or concurrent target state changes abort confirmation with `ROLE_REVOKED` / `TARGET_STATE_CHANGED`.

## 3. Data Redaction, Injection, SSRF & Webhooks
- **Secret Redaction (`redact_text`, `redact_payload`):** Scrubs PEM private keys, HTTP `Bearer <token>`, key/value tokens/passwords/secrets (`ghp_`, `glpat-`, `sk_live_`, etc.), and Watchdog ping URLs before DB persistence, logs, memory, and model prompts.
- **Scoped Conversational Memory (`ScopedSessionMemory`):** Partitioned by `(tenant_id, principal_id, channel, bot_id, chat_id, session_id)` with cross-session isolation checks and atomic tool-call/tool-result turn retention. `/clear` clears chat context only and never deletes audit logs or events.
- **Outbound SSRF Guard (`SafeOutboundGuard.validate_url`):** Requires HTTPS, strips/blocks embedded URL credentials, enforces domain allowlists, blocks IP literals, and resolves DNS to reject RFC1918/loopback/link-local/reserved IPs (DNS rebinding guard).
- **Webhooks:** Zalo webhook requires constant-time `X-Bot-Api-Secret-Token` validation and `ingress_mode="webhook"`. GitHub webhook (`POST /api/v1/ai-assistant/v4/webhooks/github`) verifies HMAC-SHA256 `X-Hub-Signature-256`, delivery deduplication (`v4_webhook_deliveries`), and repository allowlist.
