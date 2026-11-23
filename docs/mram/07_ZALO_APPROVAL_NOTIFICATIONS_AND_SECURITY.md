# 07 — Zalo Approval Notifications & Security Model

## 1. Targeted Recipient Control

- Intended approver: **Hiếu Lê** (`zalo_user_id = 0c00ed813eccd7928edd`, `ADMIN` private DM).
- Verified via `ZaloIdentityRBACService.verify_daily_brief_recipient("0c00ed813eccd7928edd")` before enqueueing DM.
- If unverified, returns `RECIPIENT_NOT_VERIFIED` — **never** broadcasts to groups or `ZALO_AUTHORIZED_USERS` / `ZALO_REPORT_CHAT_IDS`.

## 2. Secure Opaque Approval Invitations

- Token generated with `secrets.token_urlsafe(32)` (32 bytes CSPRNG).
- Database table `approval_invitations` stores ONLY `SHA-256(token)` + `(request_id, request_version, admin_username, admin_zalo_id)` + `expires_at = min(issued_at + 24h, review_expires_at)`.
- Deep link format: `https://aiotserver.fitdnu.id.vn/dashboard/ssh-requests/{request_id}/review?invite={token}`.
- `GET /api/v1/admin/approval-invitations/verify` is read-only and **never mutates state or consumes tokens**.
- Actual decision (`approve` / `reject` / `request-info`) requires Admin JWT session + `X-Step-Up-Token` + matching `request_version`.
- Also surfaces MRAM request/expiration KPIs inside the 08:00 Daily Executive Brief.
