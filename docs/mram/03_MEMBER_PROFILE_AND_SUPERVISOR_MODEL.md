# 03 — Member Profile & Supervisor Grouping Model

## 1. Relational Schema

- `member_profiles`: immutable `member_id` PK linked to `members.id`, `member_category` (`STUDENT` | `LECTURER`), `member_code` (Student ID / Staff ID), `department_class`, `primary_supervisor_id`, `direct_manager_id`, `web_account_status`, `ssh_access_state`, `ssh_expires_at`, `verified_by`, `created_by`.
- `supervisor_assignments`: audit-preserved history of `(student_member_id, supervisor_member_id, direct_manager_id, assignment_role, is_active, assigned_by, change_reason, effective_from, effective_to)`. Only one `PRIMARY` row per student has `is_active = True` at any instant; updates close the prior row with `effective_to`.

## 2. Dashboard Views

- **Admin (`/dashboard/users`)**: Toggle between standard table view and expandable **Nhóm theo Giảng viên hướng dẫn** (`GET /api/v1/admin/supervisor-groups`), displaying per-lecturer student count, pending requests, active SSH accounts, expiring (<7 days), project titles, hosts, storage GiB, GPU count, plus `UNASSIGNED` group and inline `Đổi GVHD` action.
- **Lecturer (`/api/v1/me/supervised-students`)**: Scoped read-only roster strictly filtered by `primary_supervisor_id == lecturer_id OR direct_manager_id == lecturer_id`, with all credentials redacted.
