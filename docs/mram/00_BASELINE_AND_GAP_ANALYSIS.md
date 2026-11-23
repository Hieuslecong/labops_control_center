# 00 — Baseline Audit & Gap Analysis (LabOps MRAM v1.0)

**Branch:** `feature/labops-ai-sre-agent-v2` (branched from `feature/storage-resource-allocation` @ `4000d5fd`)  
**Public URL:** `https://aiotserver.fitdnu.id.vn`  
**Timezone:** `Asia/Ho_Chi_Minh`

## 1. Runtime vs. Historical Documentation Reconciliation

- **SSH Runtime**: Runtime uses Docker workspaces isolated per user + WireGuard `wg-easy` v15 + OpenSSH gateway / Go Node Agent (`ssh_access_manager.py`, `workspace_controller.py`, `vpn_ssh_service.py`). Historical Teleport design docs are NOT active and were NOT substituted into the runtime.
- **Role / Auth System**: Session tokens in `backend/app/core/security.py` and `src/lib/session-token.ts` restrict `Principal.role` to `{"ADMIN", "RESEARCHER"}`. MRAM preserves that boundary while separating `member_category` (`STUDENT` | `LECTURER`) and `ssh_access_state` in `member_profiles`.
- **Storage Pools**: `StorageInventoryService` collects verified mounts from Go Node Agent (`/api/v1/agent/storage`) and `/proc/mounts`, rejecting root/system/loop mounts and enforcing `net_allocatable_gib`.

## 2. Requirement → Existing Implementation → Gap → Proposed Change → Test

| Requirement | Existing Implementation | Gap | Proposed Change | Test Coverage |
| :--- | :--- | :--- | :--- | :--- |
| Public self-registration disabled | `sign-up-view.tsx` static redirect | Backend lacked explicit `403` guard | `POST /api/v1/auth/register` & `/sign-up` return `403`; Admin-only web account creation | `test_02_public_signup_disabled` |
| Web login without SSH/Workspace | `auth.py` required `verify_member_workspace_password` | New members couldn't log in before SSH provisioning | Added `member_web_credentials` (salted scrypt/Argon2id + lockout + `must_change_password`), fallback to legacy workspace pw | `test_01_web_only_login_and_legacy_compat` |
| Supervisor relational modeling & grouping | Flat `members` list only | No `STUDENT`/`LECTURER` link or `/dashboard/users` grouping | `member_profiles`, `supervisor_assignments`, `GET /admin/supervisor-groups`, `GET /me/supervised-students` | `test_04_supervisor_grouping_and_lecturer_rbac` |
| Post-login SSH request & 24h SLA | Admin directly ran Provisioning Wizard | No post-login member form, separate reason, or 24h expiry | `server_access_requests`, `policy_acceptances`, `expire_overdue_requests()`, `reapply` | `test_05_student_vs_lecturer_forms`, `test_06_sla_24h_expiry_and_reapply` |
| Auto recommendation & DB capacity reservation | In-memory `_reservations` in `storage_service` | Lost on restart, double-count risk, no pre-approval lock | `request_resource_recommendations`, `capacity_reservations`, optimistic version check | `test_07_resource_recommendation_and_concurrent_reservation`, `test_08_host_switch_and_pool_validation` |
| Zalo approval invitation | Alert worker sent general alerts | No 32-byte CSPRNG invite bound to verified Admin `0c00ed813eccd7928edd` | `approval_invitations` (SHA-256 hash), GET-safe verify, step-up required | `test_09_zalo_invitation_security_and_get_safety` |
| Durable provisioning & verified handover | Direct synchronous provision call | Needed idempotent link from approved snapshot & step-up claim | `provisioning_operations`, `credential_claims`, one-time claim + reissue, expiry retaining web login | `test_10_durable_provisioning_and_verification_gate`, `test_11_handover_claim_reissue_and_ssh_expiry` |
