# 01 — CURRENT IMPLEMENTATION AUDIT & ROOT CAUSE ANALYSIS
**LabOps Control Center V2 — Resource Allocation & Storage Subsystem**

---

## 1. Executive Summary

This audit evaluates the legacy resource allocation model of LabOps Control Center V2 prior to the Storage Allocation & Shared Resource Management hardening milestone. 

The previous architecture suffered from three architectural deficiencies:
1. **Hardcoded Host Storage Paths**: Arbitrary assignment of user workspace paths (e.g., hardcoded `/media/LabOps-Data` on workstation or `/tank/workspaces` on rack) without querying physical block devices or filesystem capabilities.
2. **Artificial CPU Core and RAM Pinning**: Assigning fixed core counts (e.g. 8 cores) and static RAM allocations (e.g. 32 GB) to individual researcher containers. This caused severe artificial resource fragmentation, idle capacity waste, and failed to protect host OS daemons from Out-Of-Memory (OOM) panics.
3. **Misleading Quota & VRAM Enforcement Claims**: The user interface claimed "HARD_ENFORCED" storage quotas and isolated VRAM even when filesystem project quotas were absent and GPU MIG hardware slicing was unconfigured.

---

## 2. Deep Dive: Pre-Hardening Deficiencies

### 2.1 Hardcoded Storage Paths and Lack of Volume Admission Control
- **Legacy Behavior**: `labops_service.py` assigned workspaces to `/media/LabOps-Data/labops/users/{username}` or static Incus paths (`/var/lib/incus/storage-pools/...`) regardless of whether the target partition was mounted, online, or had sufficient free capacity.
- **Root Cause**: Absence of a dynamic host storage discovery protocol between FastAPI Backend and Go Node Agent.
- **Failure Mode**: When secondary NVMe/SATA drives were unmounted or placed under maintenance, container creation would either fail unpredictably or fall back to writing directly onto the root filesystem (`/`), risking disk exhaustion on Debian root partitions.

### 2.2 Artificial Resource Pinning vs. Real AI Workloads
- **Legacy Behavior**: Each researcher workspace container had `NanoCpus = cores * 1e9` and static `Memory = ram_gb * 1024^3`.
- **System Impact**:
  - **CPU Starvation**: A machine with 16 cores could only host 2 users if each was assigned 8 cores, even if both users spent 90% of their day typing code in Jupyter or editing scripts.
  - **Memory Crash Vulnerability**: The sum of container memory limits could equal or exceed 100% of host RAM without reserving memory for the Linux kernel, PostgreSQL, Docker daemon, and Node Agent. Under peak allocations, the Linux OOM Killer terminated critical host system daemons instead of containerized user processes.

### 2.3 Unverified Quota & VRAM Claims
- **Legacy Behavior**: The frontend wizard displayed "CƯỠNG CHẾ CỨNG" (Hard Enforced) for both storage and VRAM quotas.
- **Reality**: Standard ext4 partitions without `prjquota` mount options cannot enforce project quotas. Non-MIG NVIDIA A30 GPUs on workstation/rack have no hardware memory slicing; VRAM is managed cooperatively by NVIDIA drivers and CUDA runtime.
- **Security & Operational Risk**: Researchers expected hard isolation and were surprised when runaway jobs exceeded memory limits or when disk write errors crashed unisolated sibling workloads.

---

## 3. Remediation Architecture Matrix

| Dimension | Legacy Model (V1) | Hardened Architecture (V2) | Enforcement Mechanism |
| :--- | :--- | :--- | :--- |
| **Storage Discovery** | Static mock arrays in memory | Dynamic Host Storage Inventory via Go Agent | Block device & mount table inspection |
| **Storage Allocation** | Shared host root / arbitrary path | Dedicated directory on admin-selected drive | Host path: `<mount>/labops/users/<user>` (0700) |
| **Storage Quota** | Mock integer tracking | XFS Project Quota / ext4 Project Quota / ZFS dataset | Filesystem kernel quota with read-back verification |
| **Container Mount** | Varied paths (`/data`, `/home/...`) | Fixed `/workspace` mount point | Read-write bind mount with symlink escape prevention |
| **CPU Scheduling** | Fixed static core allocation | Shared Weighted CPU (`SHARED_WEIGHTED`) | `CpuShares = 100/1024`, `NanoCpus = 0`, Burstable |
| **Process Limits** | Unrestricted PID count | Strict Fork-Bomb Mitigation | `PidsLimit = 4096` per container |
| **RAM Allocation** | Static memory boundaries | Shared Host-Protected (`SHARED_HOST_PROTECTED`) | 20% Host OS Reserve, PSI monitoring |
| **VRAM Policy** | Claimed hard isolation | Honest Policy Status (`POLICY_LIMIT` / `MONITORED`) | Telemetry monitoring via NVML |

---

## 4. Conclusion

The remediation replaces static assumptions with live, verifiable host telemetry, hard filesystem project quotas, and Linux cgroups v2 proportional resource sharing.
