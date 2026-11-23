# 06 — Auto Resource Recommendation & Host/Storage Capacity Engine

## 1. Default Configurable Profile

- `cpu_cores`: `4` (Shared Weighted cgroup)
- `ram_gb`: `16` (`SHARED_HOST_PROTECTED`)
- `storage_quota_gb`: `100`
- `gpu_mode`: `NONE` (unless Admin overrides)
- `duration_days`: `30` (Student default) / `None` (Lecturer, no mandatory expiration)

## 2. Dynamic Host & Storage Pool Selection

- Hosts are inspected dynamically via `service.get_v2_hosts()`. Offline/unverified hosts are excluded.
- Storage pools are inspected via `storage_service.get_storage_pools(host_id)`, filtering for `eligible=True`, `read_only=False`, `mounted=True`, valid `filesystem_uuid`, non-root mount.
- Net allocatable capacity = `pool.allocatable_gib - max(db_capacity_reservations_gb, memory_reservations_gb)` (no double counting).
- If no online host or qualifying >= 100 GiB pool is found, sets `manual_review_required=True` with explicit rationale.

## 3. GPU Policy Honesty

- `EXCLUSIVE`: Fails validation if any active `RESERVED`/`COMMITTED` reservation exists on `gpu_uuid`.
- `SHARED`: Allowed when no `EXCLUSIVE` hold exists; UI and API explicitly warn that soft sharing does **not** enforce hard physical VRAM isolation.
