# 05 — State Machines, Lifecycle & 24-Hour Review SLA

## 1. Request Lifecycle

`PENDING_APPROVAL` (submitted with `review_expires_at = submitted_at + 24h` UTC):
- → `APPROVED` → `PROVISIONING` → `VERIFIED_READY` (all verification gates pass)
- → `APPROVED` → `PROVISIONING` → `PROVISIONING_FAILED` (gate fails; reservation released)
- → `REJECTED` (Admin step-up rejection with reason)
- → `NEED_INFO` (Admin requests clarification; still subject to SLA sweep)
- → `EXPIRED` (automatic sweep when `now_utc > review_expires_at`, revoking active invitations)

From `EXPIRED` or `REJECTED`, members can trigger `POST /api/v1/me/ssh-requests/{id}/reapply` to open a new request ID prefilled from prior inputs with a fresh 24-hour SLA.

## 2. SSH Entitlement Lifecycle

`NONE` → `PENDING_APPROVAL` → `APPROVED_PROVISIONING` → `VERIFIED_READY` → `EXPIRED` (for Students past `ssh_expires_at`).  
Expiration revokes active SSH/GPU/capacity reservations while retaining `web_account_status = WEB_ACTIVE` and preserving workspace data on disk (`<mount>/labops/users/<username>`).
