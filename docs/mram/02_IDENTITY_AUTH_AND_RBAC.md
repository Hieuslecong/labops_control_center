# 02 — Identity, Auth Separation & RBAC Specification

## 1. Credential Domain Separation

- **Web Portal Credential (`member_web_credentials`)**:
  - Salted `argon2id` or `scrypt` (`n=16384, r=8, p=1`, 16-byte random salt) hash.
  - Lockout after 5 failed attempts (15 minutes).
  - `must_change_password = True` on initial account creation; cleared via `POST /api/v1/auth/change-web-password`.
  - Backward compatibility: if no row in `member_web_credentials`, `POST /api/v1/auth/login` falls back cleanly to `service.verify_member_workspace_password`.
- **Temporary SSH Credential (`ssh_access_manager` + RAM Vault)**:
  - Provisioned ONLY after Admin approval + durable provisioning reaches `VERIFIED_READY`.
  - Claimed at `/dashboard/researcher/access` using `POST /api/v1/me/ssh-access/claim` with owner Step-Up verification (`CLAIM_SSH_SECRET`).

## 2. Security Role vs. Academic Category

- `Principal.role` remains `ADMIN` or `RESEARCHER` (no privilege drift).
- `member_category` (`STUDENT` | `LECTURER`) is informational/academic; being a `LECTURER` grants read-only access ONLY to `GET /api/v1/me/supervised-students` (returning assigned students without credentials, tokens, or ZIP downloads), never Admin controls.
- `/api/v1/auth/register` and `/api/v1/auth/sign-up` fail closed (`403 Forbidden`).
