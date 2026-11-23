# 01 — Product & Operational Architecture (MRAM v1.0)

## 1. End-to-End Lifecycle

1. **Admin creates initial Web-Only Account** (`POST /api/v1/admin/member-web-accounts`) with `STUDENT` or `LECTURER` category, student/staff code, department, and optional supervisor. Zero Linux users, SSH keys, WireGuard peers, Docker containers, storage dirs, or GPU reservations are created.
2. **Member logs in Web Portal** (`/auth/sign-in` → `POST /api/v1/auth/login`), changes initial web password (`POST /api/v1/auth/change-web-password`), and accesses `/dashboard/researcher`.
3. **Member submits SSH Access Request** (`/dashboard/researcher/ssh-request` → `POST /api/v1/me/ssh-requests`):
   - Identity & category locked from server session.
   - Students select verified Lecturer (`primary_supervisor_id`), duration (default 30d), and enter separate explicit `registration_reason`.
   - Lecturers omit mandatory SSH expiration.
   - Server opens exact 24-hour review SLA (`review_expires_at = submitted_at + 24h`).
4. **System recommends resources & notifies Admin Hiếu Lê (`0c00ed813eccd7928edd`)**:
   - Default profile: `4 vCPU shared`, `16 GiB RAM`, `100 GiB Storage`, `GPU=NONE`.
   - Live online host & verified physical storage pool selected dynamically (`manual_review_required=True` if none qualifies).
   - Opaque CSPRNG invitation link sent via private DM only to verified Admin `0c00ed813eccd7928edd`.
5. **Admin Reviews, Edits & Approves with Step-Up Auth** (`/dashboard/ssh-requests/{id}/review`):
   - Read-only pre-validation (`POST /api/v1/admin/ssh-requests/{id}/validate`).
   - Switching host resets pool and GPU selections cleanly.
   - Transactional approval stores immutable config snapshot + SHA-256 hash + DB `capacity_reservations` lock.
6. **Durable Idempotent Provisioning & Runtime Verification Gate** (`mram-prov:{request_id}:v{version}`):
   - Reuses `LabOpsDataService`, `StorageInventoryService`, `WorkspaceController`, Go Node Agent, WireGuard `wg-easy`.
   - Transitions to `VERIFIED_READY` strictly after read-back checks pass.
7. **Verified Handover, Expiry & Renewal**:
   - Step-Up verified one-time temporary SSH secret claim / vault reissue + WireGuard/SSH ZIP download.
   - Student SSH expiration sweeps revoke SSH/GPU entitlements while keeping `web_account_status = WEB_ACTIVE` and preserving storage data on disk.
