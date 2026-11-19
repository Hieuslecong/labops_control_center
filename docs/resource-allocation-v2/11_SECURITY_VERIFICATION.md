# 11 — SECURITY VERIFICATION & ADMISSION CONTROL AUDIT
**LabOps Control Center V2 — Resource Allocation & Storage Subsystem**

---

## 1. Threat Modeling & Attack Vectors

The resource allocation hardening addresses four primary threat vectors:
1. **Storage Path Traversal**: An attacker or malicious admin injecting `..` or relative components into usernames, workspace names, or storage paths to overwrite system files (`/etc/passwd`, `/root/.ssh/authorized_keys`).
2. **Symlink Breakout Attacks**: Creating symlinks inside a workspace pointing to external host paths before provisioning or archive operations.
3. **Denial-of-Service via Host Root Disk Exhaustion**: Creating unconfined user directories on the root filesystem `/`, leading to out-of-disk kernel crashes.
4. **Denial-of-Service via Host Memory Starvation & Fork Bombs**: Spawning infinite processes or allocating all RAM to crash host daemons.

---

## 2. Security Mitigation Implementations

### 2.1 Strict Path Sanitization & Traversal Defense
In both Go Node Agent (`go/internal/storage/provisioner.go`) and FastAPI Backend (`backend/app/services/storage_inventory_service.py`):
```python
clean_member_dir = os.path.abspath(member_dir)
if not clean_member_dir.startswith(expected_base_dir + os.sep):
    raise PermissionError(f"SECURITY_VIOLATION: Path traversal detected: {member_dir}")
```
- Path elements are checked sequentially. If any segment is a symbolic link (`os.path.islink`), provisioning is immediately aborted.
- Usernames are restricted to the strict regex `^[a-z_][a-z0-9_-]{2,31}$`. System accounts (`root`, `admin`, `sshd`, `docker`, `sudo`, `wheel`) are explicitly prohibited.

### 2.2 Host Root Confinement & Forbidden Roots
Mount points are validated against a strict forbidden list:
```python
forbidden_roots = ["/", "/etc", "/proc", "/sys", "/dev", "/boot", "/root", "/var/lib/docker", "/home"]
```
Any drive or mount point residing inside or matching these paths is automatically marked `eligible = False` with reason `FORBIDDEN_SYSTEM_ROOT`.

### 2.3 cgroups v2 Process Confinement & PID Ceiling
- Every container is spawned with `PidsLimit = 4096`.
- Fork bombs (`:(){ :|:& };:`) within the container are terminated by the kernel cgroup controller when 4,096 processes are reached, without impacting host processes or neighboring containers.

---

## 3. Admission Control & Race Condition Prevention

To prevent concurrent allocation requests from exceeding physical capacity:
1. **Committed Reservations Store**: The admission controller maintains an in-memory lock on committed allocations:
   $$\text{Allocatable} = \text{PoolAllocatable} - \text{CommittedReservations}$$
2. **Pre-Allocation Admission Validation**: The `/api/v1/storage-pools/validate` endpoint must return `valid: true` before the backend proceeds with directory provisioning.
3. **Fail-Closed Policy**: If an unmounted drive is queried, admission fails immediately without falling back to any default system path.
