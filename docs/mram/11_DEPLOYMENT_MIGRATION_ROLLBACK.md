# 11 — Deployment, Migration & Rollback Plan

## 1. Pre-Deployment Checklist

- Take backup snapshot of active SQLite (`labops.db`) or PostgreSQL database.
- Ensure `LABOPS_ADMIN_PASSWORD`, `LABOPS_SESSION_SECRET`, and `LABOPS_PUBLIC_URL=https://aiotserver.fitdnu.id.vn` are set in environment.

## 2. Database Migration

Run idempotent Alembic migration up to `head` (`0004_labops_mram_v1`):
```bash
cd backend
alembic upgrade head
```

## 3. Rollout Order

1. Apply Alembic migration `0004_labops_mram_v1`.
2. Restart FastAPI backend (`uvicorn` / systemd) so new `/api/v1/admin/*` and `/api/v1/me/*` routes and auth web credential checks are live.
3. Restart `labops-ai-sre-worker.service` so 24-hour SLA & student SSH expiration sweeps run continuously alongside Zalo outbox dispatch.
4. Build and restart Next.js frontend (`bun run build && bun run start`).

## 4. Rollback Plan

- Frontend routes under `/dashboard/ssh-requests` and `/dashboard/researcher/*` are additive and isolated; reverting frontend bundle restores prior navigation.
- `alembic downgrade 0003_labops_ai_sre_agent_v2` cleanly drops the 11 new MRAM tables without altering existing `members`, `workspaces`, `zalo_identity_links`, or `report_runs` tables.
- Legacy member login via `verify_member_workspace_password` stays functional regardless of `member_web_credentials`.
