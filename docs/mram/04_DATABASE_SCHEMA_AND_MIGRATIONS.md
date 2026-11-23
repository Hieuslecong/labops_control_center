# 04 — Database Schema & Alembic Migrations

## 1. Migration `0004_labops_mram_v1`

- Depends on `0003_labops_ai_sre_agent_v2` (`down_revision`).
- Idempotent creation (`insp.get_table_names()`) of 11 tables in SQLite and PostgreSQL:
  1. `member_web_credentials`
  2. `member_profiles`
  3. `supervisor_assignments`
  4. `server_access_requests`
  5. `request_resource_recommendations`
  6. `request_approvals`
  7. `approval_invitations`
  8. `capacity_reservations`
  9. `provisioning_operations`
  10. `credential_claims`
  11. `policy_acceptances`

## 2. Concurrency & Integrity Controls

- Optimistic concurrency version column `server_access_requests.version` prevents double-approval or stale edits (`409 VERSION_CONFLICT`).
- Unique `provisioning_operations.idempotency_key` (`mram-prov:{request_id}:v{approval_version}`) guarantees idempotent replay.
- Unique `approval_invitations.token_hash` stores only SHA-256 digests (raw CSPRNG token is never persisted).
