# 02 — STORAGE INVENTORY ARCHITECTURE & DISCOVERY SPECIFICATION
**LabOps Control Center V2 — Resource Allocation & Storage Subsystem**

---

## 1. Architectural Overview

The Storage Inventory subsystem provides host-scoped, dynamic discovery of block devices and filesystem mount points across all physical servers in the LabOps cluster (`workstation` and `track`).

```
┌──────────────────────────────────────────────────────────────────┐
│                   LabOps Web Dashboard (Next.js)                 │
│               Step 2: Server & Dynamic Drive Selector            │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ GET /api/labops/storage-pools?host_id=workstation
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                  LabOps FastAPI Backend Service                  │
│       StorageInventoryService (app/services/storage_inventory)   │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ HTTP / mTLS Gateway Tunnel
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                   LabOps Go Node Agent (Port 9100)               │
│                  Package: internal/storage/inventory.go          │
└──────────────┬───────────────────────────────┬───────────────────┘
               │                               │
               ▼                               ▼
    /proc/mounts, lsblk, statfs      xfs_quota, chattr +P, zfs
    (Block device & mount table)     (Project Quota Capability)
```

---

## 2. Storage Pool Discovery Pipeline

The Go Node Agent scans the operating system storage hierarchy at startup and on demand through `/api/v1/agent/storage`:

1. **Mount Table Inspection**: Reads `/proc/mounts` and filters for real physical filesystems (`ext4`, `xfs`, `zfs`, `btrfs`). Virtual filesystems (`tmpfs`, `devtmpfs`, `sysfs`, `proc`, `cgroup`) are discarded.
2. **Block Device Corroboration**: Resolves devices against `lsblk -J -b -o NAME,KNAME,MOUNTPOINT,FSTYPE,UUID,SIZE,RO` to extract block device identifiers and UUIDs.
3. **Capacity & Reservation Calculation**:
   - Total Capacity ($C$): Extracted via POSIX `statfs(2)`.
   - Free Space ($F$): Extracted via `statfs.F_bavail * statfs.F_bsize`.
   - Safety Reserve ($R$): 10% safety reserve (minimum 10 GiB, maximum 50 GiB) reserved for filesystem metadata, root inode maintenance, and emergency operations.
   - Allocatable Space ($A$): Calculated as $A = \max(0, F - R)$.
4. **Quota Capability Detection**:
   - **XFS**: Verifies if mounted with `pquota` / `prjquota`. Executes `xfs_quota -x -c 'report -p'`.
   - **ext4**: Verifies kernel project quota support (`/sys/fs/ext4/<dev>/project_quota`). Checks if `prjquota` flag is active in super block via `tune2fs -l` or `/proc/mounts`.
   - **ZFS**: Verifies dataset existence via `zfs get quota,refquota`.

---

## 3. Storage Pool Data Model & Schema

Each discovered drive is normalized into the following data contract:

```typescript
export interface StoragePoolInfo {
  hostId: 'workstation' | 'track';
  storageId: string;           // E.g. "ws-nvme-data" or "track-tank-pool"
  device: string;              // E.g. "/dev/nvme0n1p1" or "/dev/sdb1"
  filesystemUuid: string;      // Filesystem UUID for stable persistence
  mountPoint: string;          // E.g. "/media/LabOps-Data" or "/data"
  filesystem: 'ext4' | 'xfs' | 'zfs' | 'btrfs';
  capacityGib: number;         // Total storage capacity in GiB
  usedGib: number;             // Used storage in GiB
  freeGib: number;             // Raw free space in GiB
  allocatableGib: number;      // Free space minus 10% safety reserve
  reservedGib: number;         // Safety reserve in GiB
  quotaBackend: 'XFS_PROJECT' | 'EXT4_PROJECT' | 'ZFS_DATASET' | 'NONE';
  quotaSupported: boolean;     // Whether filesystem supports hard project quotas
  quotaEnforced: boolean;      // Whether quota is actively enforced by kernel
  readOnly: boolean;           // True if mounted read-only (ro)
  mounted: boolean;            // True if mount point is online and accessible
  eligible: boolean;           // True if drive can safely host user workspaces
  ineligibilityReason?: string;// Reason if drive is ineligible
  lastVerifiedAt: string;      // ISO-8601 timestamp of last probe
  verificationStatus: 'VERIFIED' | 'NOT_VERIFIED' | 'INELIGIBLE';
}
```

---

## 4. Eligibility & Safety Filters

To ensure user workspaces cannot compromise host integrity, the inventory engine enforces strict eligibility criteria:

| Mount Point / Criterion | Eligibility | Rule / Rationale |
| :--- | :--- | :--- |
| Root Filesystem (`/`) | **INELIGIBLE** | `FORBIDDEN_SYSTEM_ROOT`: Prevents root disk exhaustion. |
| User Home (`/home`, `/home/*`) | **INELIGIBLE** | `FORBIDDEN_USER_HOME`: Prevents circular nesting in host user homes. |
| System Directories (`/etc`, `/boot`, `/var`) | **INELIGIBLE** | `FORBIDDEN_SYSTEM_DIR`: Protects OS binaries and log storage. |
| Docker Root (`/var/lib/docker`) | **INELIGIBLE** | `FORBIDDEN_DOCKER_STORE`: Prevents storage loops with container runtime. |
| Read-Only Mount (`ro`) | **INELIGIBLE** | `FILESYSTEM_READ_ONLY`: User workspaces require read-write access. |
| Offline / Unmounted | **INELIGIBLE** | `MOUNT_OFFLINE`: Prevents phantom directory creation. |
| Available Space < 5 GiB | **INELIGIBLE** | `INSUFFICIENT_SPACE`: Ensures minimum threshold for workspace provisioning. |
| Dedicated Mount (`/media/LabOps-Data`, `/data`) | **ELIGIBLE** | Approved high-performance storage pool for AI datasets and checkpoints. |

---

## 5. Host Scope Isolation

Storage pools are strictly partitioned by `host_id`:
- Switching from `workstation` to `track` in the Admin Provisioning Wizard instantly resets pool selection and triggers a targeted query to `track`'s Node Agent.
- The backend rejects any attempt to allocate a `workstation` storage pool on `track` or vice versa (`HTTP 400: STORAGE_POOL_HOST_MISMATCH`).
