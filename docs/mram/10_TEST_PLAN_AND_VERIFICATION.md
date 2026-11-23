# 10 — End-to-End Test Plan & Verification Matrix

Automated verification lives in `backend/tests/test_mram_v1.py` covering:
1. Initial Web-Only account creation (`WEB_ACTIVE`, zero compute/SSH/GPU) & web login without SSH.
2. Legacy `verify_member_workspace_password` fallback login compatibility & password lockout.
3. Initial web password change (`POST /api/v1/auth/change-web-password`) before SSH exists.
4. Public self-registration blocked (`403 Forbidden`).
5. Student vs Lecturer form validation rules, locked identity/client-override rejection, duplicate open request blocking (`409`).
6. Relational supervisor assignment history, `/dashboard/users` grouping (`UNASSIGNED` + lecturer totals), and Lecturer scoped read-only `GET /api/v1/me/supervised-students` (Students blocked `403`).
7. Exact 24-hour review SLA expiration sweep, late approval denial (`409 REQUEST_REVIEW_EXPIRED`), and Re-apply workflow.
8. Default resource recommendation (`4 vCPU / 16 GiB RAM / 100 GiB / GPU=NONE`) & `manual_review_required` when storage pool lacks space.
9. Concurrent capacity reservations preventing storage pool or exclusive GPU overcommit + host-switch pool/GPU validation.
10. Targeted Zalo approval invitation (`0c00ed813eccd7928edd` verified-only, fail-closed `RECIPIENT_NOT_VERIFIED`), CSPRNG SHA-256 hash, GET-safe verify, and Step-Up approval requirement.
11. Idempotent provisioning replay (`mram-prov:{id}:v{ver}`), failed gate releasing capacity reservation, Step-Up one-time secret claim/replay rejection (`410`), secret reissue, student SSH expiration preserving web login (`WEB_ACTIVE`), and renewal request.
