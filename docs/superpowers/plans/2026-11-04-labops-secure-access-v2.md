# LabOps Secure Access V2 Implementation Plan

> **For agentic workers:** Use the inline `superpowers:executing-plans` workflow and TDD for each task. Keep existing uncommitted work intact.

**Goal:** Replace fabricated WireGuard/SSH provisioning results with an access workflow that only reports observed service state and is ready to connect to Teleport Community.

**Architecture:** Preserve the existing FastAPI and feature-based Next.js structure. Add fail-closed service adapters and explicit provisioning states; keep private client keys on client devices; provide Teleport configuration/commands through a narrow adapter, never a generic shell. Do not alter production hosts/router from this sandbox.

**Tech Stack:** Python 3.10, FastAPI, `unittest`, Next.js 16.4, TypeScript 7, Teleport Community 18.x.

**Spec:** `docs/superpowers/specs/2026-11-04-labops-secure-access-v2.md` and the user-supplied LABOPS SECURE ACCESS V2 brief.

## Global Constraints

- “Private key phải được tạo và giữ trên thiết bị người dùng.”
- “Không đưa private key vào log, API response hoặc audit event.”
- “Không đánh dấu `ACTIVE` chỉ vì backend đã ghi dữ liệu xuống database.”
- “Không mở các cổng SSH của workspace trực tiếp ra Internet.”
- “Teleport Proxy chỉ truy cập được từ mạng VPN.”
- “Teleport Auth Service không public.”
- “Không cho researcher đăng nhập shell quản trị host.”
- “Không gây gián đoạn GPU allocation, workspace, dữ liệu nghiên cứu hoặc phiên SSH quản trị.”
- When gateway, Teleport, TLS/DNS, or remote agents cannot be inspected, report `NOT VERIFIED` and do not synthesize success.

## Review Focus

- Invalid or absent server public key/endpoint must block config generation.
- Missing `wg`, permission errors, or nonzero `wg set` must not persist an active peer.
- Duplicate client public keys or VPN IPs must not create a second active assignment.
- Teleport command failure or missing auth configuration must leave the user pending and must not leak invite tokens to logs.
- A revoked/suspended user must not be restored by UI-local state or mock telemetry.

---

### Task 1: WireGuard peer correctness and secret handling

**Files:**
- Modify: `backend/app/adapters/wireguard_adapter.py`
- Modify: `backend/app/services/labops_service.py`
- Modify: `backend/app/schemas/labops.py`
- Test: `backend/tests/test_wireguard_provisioning.py`

**Interfaces:**
- `WireGuardAdapter.register_peer(username, device_id, public_key, client_ip, server_id)` accepts a client public key and returns only public connection metadata after real registration succeeds.
- Client private keys and full client config contents are absent from persisted member records and API response schemas.
- Missing runtime/configuration raises a typed operational error; it never returns simulated `ACTIVE` state.

- [ ] Add failing tests for invalid configured server data, `wg set` failure, and absence of private-key material in the returned/persisted profile.
- [ ] Run the targeted tests and confirm each fails for the intended current behavior.
- [x] Implement configuration-driven server identity, strict public-key validation, checked peer commands, and secret-free profiles.
- [x] Run the backend unit suite in an isolated copy so existing `backend/data/*.json` changes are preserved.

### Task 2: Teleport Community adapter and truthful provisioning states

**Files:**
- Create: `backend/app/adapters/teleport_adapter.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/labops_service.py`
- Modify: `backend/app/schemas/labops.py`
- Test: `backend/tests/test_teleport_provisioning.py`
- Create: `deploy/teleport/teleport.yaml.example`

**Interfaces:**
- A narrow adapter exposes cluster health, role provisioning, local-user invitation, and user lock/revoke operations through configured Teleport tooling.
- Researcher roles have workspace node-label scope, 12h max TTL, no agent forwarding, and no per-session MFA; admin roles use 2h TTL and require per-session MFA.
- Local login MFA is WebAuthn-required in the example cluster configuration.
- Adapter absence/failure reports `NOT_CONFIGURED`/`PENDING`; it never fabricates users, sessions, certificates, or recordings.

- [ ] Add failing tests for role limits, workspace labels, absent `tctl`, and command failure.
- [ ] Verify the tests fail before implementation.
- [x] Implement a narrow, argument-safe Teleport adapter and versioned configuration example.
- [x] Run Teleport tests and the backend unit suite in the isolated copy; live `tctl`/cluster behavior remains `NOT VERIFIED`.

### Task 3: Access API authorization and lifecycle state

**Files:**
- Modify: `backend/app/api/v1/endpoints/users.py`
- Modify: `backend/app/api/v1/endpoints/admin_members.py`
- Modify: `backend/app/api/v1/api.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_http_endpoints.py`

**Interfaces:**
- Access-management mutations require server-side authentication/authorization; missing credentials or missing production configuration fail closed.
- Provisioning progresses through pending/verified states; suspension/revocation is idempotent and preserves workspace data.
- Public endpoints never return WireGuard private keys, full client configurations, passwords, or invitation tokens in logs.

- [ ] Add failing endpoint tests for unauthenticated reads/mutations and for non-active state when adapters are unavailable.
- [ ] Run the targeted API tests and confirm expected failures.
- [x] Add the smallest compatible auth dependency and route guards, plus truthful lifecycle transitions.
- [x] Run backend suite; HTTP listener tests are skipped when this sandbox cannot bind loopback.

### Task 4: Replace local simulated user provisioning in the dashboard

**Files:**
- Modify: `src/features/users/components/member-provisioning-wizard.tsx`
- Modify: `src/features/users/components/member-management-view.tsx`
- Modify: `src/features/labops/store.ts`
- Modify: `src/types/labops.ts`
- Modify: `src/lib/wireguard-client-config.ts`
- Remove or replace: `src/lib/qr-generator.ts`, `src/features/users/components/wireguard-qr-modal.tsx`

**Interfaces:**
- Production create/suspend/revoke actions use authenticated backend responses; local Zustand mutation remains demo-only.
- The UI requires user-supplied public keys and does not generate or display private keys/passwords.
- UI status badges distinguish `PENDING`, `ACTIVE`, `NOT_CONFIGURED`, and `REVOKED`; only service observations can set `ACTIVE`.
- A config/QR is shown only when built from valid real server metadata and a client-owned private key; otherwise it is unavailable with a clear status.

- [x] Add/adjust backend-focused checks and run TypeScript typecheck.
- [x] Implement API-backed actions and remove synthetic progress/success statuses.
- [x] Run `bun run typecheck`; lint/build were not run.

### Task 5: Safe operator scripts and deployment evidence

**Files:**
- Modify: `scripts/wireguard-provisioning/add_vpn_user.sh`
- Modify: `scripts/wireguard-provisioning/revoke_vpn_user.sh`
- Modify: `scripts/wireguard-provisioning/restrict_ssh_to_vpn.sh`
- Modify: `docs/REAL_INFRASTRUCTURE_AUDIT.md`
- Modify: `docs/PRODUCTION_ACTIVATION_REPORT.md`
- Modify: `.env.example`

- [x] Run a script-level smoke check proving retired scripts fail closed, exit nonzero, and print no private-key material.
- [x] Retire unsafe key-export scripts; public SSH and current router rules were not changed.
- [x] Document prerequisites and mark unavailable Teleport/remote-agent/NAT checks `NOT VERIFIED`.
- [x] Run shell syntax checks, typecheck and isolated backend tests; production E2E remains `NOT VERIFIED`.

