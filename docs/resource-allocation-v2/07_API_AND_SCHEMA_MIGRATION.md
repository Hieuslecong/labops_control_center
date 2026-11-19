# 07 — REST API SPECIFICATION & SCHEMA MIGRATION
**LabOps Control Center V2 — Resource Allocation & Storage Subsystem**

---

## 1. REST Endpoints Specification

### 1.1 `GET /api/v1/storage-pools?host_id={host_id}`
Returns list of discovered storage pools on the specified physical host.

**Query Parameters**:
- `host_id` (string, optional): `'workstation'` or `'track'`. Defaults to local host.

**Response Body (`200 OK`)**:
```json
[
  {
    "host_id": "workstation",
    "storage_id": "ws-nvme-data",
    "device": "/dev/nvme0n1p1",
    "filesystem_uuid": "3a8f9c1e-7b2d-4e5f-9a1c-8b3d5e7f2a1b",
    "mount_point": "/media/LabOps-Data",
    "filesystem": "ext4",
    "capacity_gib": 1800.0,
    "used_gib": 200.0,
    "free_gib": 1600.0,
    "allocatable_gib": 1420.0,
    "reserved_gib": 180.0,
    "quota_backend": "EXT4_PROJECT",
    "quota_supported": true,
    "quota_enforced": true,
    "read_only": false,
    "mounted": true,
    "eligible": true,
    "ineligibility_reason": null,
    "last_verified_at": "2026-11-19T08:00:00Z",
    "verification_status": "VERIFIED"
  }
]
```

---

### 1.2 `POST /api/v1/storage-pools/validate`
Validates whether a proposed storage allocation is admissible before creating member resources.

**Request Body**:
```json
{
  "host_id": "workstation",
  "storage_id": "ws-nvme-data",
  "requested_quota_gib": 100
}
```

**Response Body (`200 OK`)**:
```json
{
  "valid": true,
  "host_id": "workstation",
  "storage_id": "ws-nvme-data",
  "requested_quota_gib": 100,
  "allocatable_gib": 1420.0,
  "mount_point": "/media/LabOps-Data",
  "quota_backend": "EXT4_PROJECT",
  "member_storage_dir_preview": "/media/LabOps-Data/labops/users/{username}"
}
```

---

### 1.3 `GET /api/v1/resources/profile?host_id={host_id}`
Returns the host resource topology, CPU scheduling weights, RAM safety reserves, and live Linux PSI memory pressure.

**Response Body (`200 OK`)**:
```json
{
  "host_id": "workstation",
  "total_cores": 16,
  "cpu_scheduling_mode": "SHARED_WEIGHTED",
  "default_cpu_weight": 100,
  "pids_limit": 4096,
  "total_ram_bytes": 137438953472,
  "host_reserve_bytes": 27487790694,
  "shared_budget_bytes": 109951162778,
  "memory_protection_mode": "SHARED_HOST_PROTECTED",
  "memory_pressure": {
    "some_avg10": 0.05,
    "some_avg60": 0.02,
    "some_avg300": 0.01,
    "full_avg10": 0.0,
    "full_avg60": 0.0,
    "full_avg300": 0.0,
    "pressure_level": "NORMAL",
    "is_high_pressure": false
  }
}
```

---

## 2. Schema Evolution & Backward Compatibility

All database models and Pydantic schemas in `backend/app/schemas/labops.py` and `app/db/models.py` preserve full backward compatibility:
- Legacy fields (`cpu_limit_cores`, `ram_limit_gb`, `mount_path`) remain supported for older CLI scripts and integration tests.
- New fields (`storage_id`, `host_storage_root`, `member_storage_directory`, `container_mount_path`, `storage_quota_backend`, `storage_quota_enforcement`, `cpu_policy`, `cpu_weight`, `memory_policy`, `vram_enforcement_status`) default to intelligent values if omitted.
- No disruptive database schema migration is required; the JSON payload store gracefully accommodates the extended attributes.
