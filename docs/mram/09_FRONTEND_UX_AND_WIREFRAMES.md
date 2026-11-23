# 09 — Frontend UX Architecture (Admin & Researcher Portal)

## 1. Admin Interfaces

- **Member & Access Control (`/dashboard/users`)**:
  - `+ Tạo Tài khoản Web (SV/GV)` modal (`CreateWebAccountDialog`) returning initial one-time web password without triggering SSH/GPU/Docker provisioning.
  - Toggleable `Nhóm theo Giảng viên hướng dẫn` (`SupervisorGroupsPanel`) with expandable per-lecturer summary metrics and inline `Đổi GVHD`.
- **SSH Request Review Queue (`/dashboard/ssh-requests`)**:
  - Filterable by `ALL`, `PENDING_APPROVAL`, `NEED_INFO`, `VERIFIED_READY`, `EXPIRED`, `REJECTED` with live 24h SLA countdown and manual SLA sweep button.
- **Mobile-Responsive Request Review (`/dashboard/ssh-requests/[id]/review`)**:
  - Zalo `?invite=` read-only validation banner, editable host/pool/CPU/RAM/storage/GPU/duration fields, automatic pool+GPU reset on host switch, Pre-Validation button, and Step-Up decision actions.

## 2. Member / Researcher Interfaces

- **Portal Home (`/dashboard/researcher`)**: Shows `member_category`, `ssh_access_state`, supports web-only initial password change (`POST /api/v1/auth/change-web-password`), and quick actions.
- **SSH Request Form (`/dashboard/researcher/ssh-request`)**: Locked session identity, Student vs Lecturer dynamic requirements, explicit `registration_reason`, policy acceptance, and pre-submit summary preview.
- **My Requests (`/dashboard/researcher/requests`)**: SLA countdown and 1-click Re-apply on `EXPIRED`/`REJECTED`.
- **Verified Handover (`/dashboard/researcher/access`)**: VS Code Remote-SSH snippet, WireGuard+SSH ZIP link, and Step-Up protected 1-time secret claim/reissue.
