# 08 — Durable Provisioning, Verification Gate & Handover

## 1. Idempotent Execution

- Triggered strictly from immutable `RequestApprovalRecord.approved_config_json` (`config_hash` SHA-256 verified), never directly from raw user request input.
- Keyed in `provisioning_operations` by unique `idempotency_key = mram-prov:{request_id}:v{approval_version}`.

## 2. Read-Back Verification Gate

Transitions to `VERIFIED_READY` (and moves `capacity_reservations` to `COMMITTED`) ONLY after verifying:
1. Member record exists and matches profile.
2. Container configuration enforces non-privileged isolation (`privileged=False`, `mount_docker_socket=False`).
3. CPU and RAM limits match the approved snapshot.
4. Storage directory ends with `/labops/users/{username}` under the approved mount.
5. SSH endpoint (`ssh_ip`, `ssh_port`) is assigned.
6. GPU entitlement list matches the approved config.

Failure transitions to `PROVISIONING_FAILED` and releases `CapacityReservationRecord` (`status = RELEASED`).

## 3. Step-Up Handover & Reissue

- Handover UI (`/dashboard/researcher/access`) is gated behind `ssh_access_state == VERIFIED_READY`.
- Claiming temporary SSH secret (`POST /api/v1/me/ssh-access/claim`) requires owner Step-Up (`CLAIM_SSH_SECRET`), records `credential_claims`, and rejects replay (`410 CREDENTIAL_ALREADY_CLAIMED`).
- If RAM vault expired after service restart, `POST /api/v1/me/ssh-access/reissue` rotates a fresh temporary secret safely.
