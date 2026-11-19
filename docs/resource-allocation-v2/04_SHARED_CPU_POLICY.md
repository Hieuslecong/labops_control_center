# 04 — SHARED WEIGHTED CPU SCHEDULING SPECIFICATION
**LabOps Control Center V2 — Resource Allocation & Storage Subsystem**

---

## 1. Motivation: Eliminating Artificial CPU Fragmentation

In AI research labs, pinning fixed CPU cores (e.g., assigning cores 0-7 exclusively to user A) results in severe resource starvation:
- When User A is reading papers or editing a script, 8 cores sit 100% idle and cannot be utilized by User B who is running a multi-threaded data preprocessing pipeline.
- Conversely, when User A needs to run a high-throughput CPU tokenization script, they are constrained to 8 cores even if the remaining 8 or 24 cores on the server are completely idle.

---

## 2. Shared Weighted CPU Architecture (`SHARED_WEIGHTED`)

LabOps Control Center V2 transitions all researcher containers to **Shared Weighted CPU Scheduling**:

```
┌─────────────────────────────────────────────────────────────────┐
│                     Host Operating System                       │
│           16 Physical Cores / 32 Threads (100% Shared)          │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                 Linux CFS / cgroups v2 Scheduler
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  Workspace User 1│    │  Workspace User 2│    │ Host OS & Daemons│
│  CpuShares = 100 │    │  CpuShares = 100 │    │ Host Slices / SSH│
│  NanoCpus = 0    │    │  NanoCpus = 0    │    │ Protected Slices │
│  PidsLimit = 4096│    │  PidsLimit = 4096│    │                  │
└──────────────────┘    └──────────────────┘    └──────────────────┘
```

### 2.1 Technical Invariants

1. **Burstable Idle Utilization**:
   - `NanoCpus = 0` (or `CpuQuota = 0`): No hard cap on core count.
   - When the host is idle, a researcher's Python data loader can burst to **100% of all physical CPU cores** ($16$ cores on Workstation, $32$ cores on Rack), completing preprocessing in a fraction of the time.
2. **Fair-Share Contention Resolution**:
   - `CpuShares = 100` (relative weight out of 1024, or cgroups v2 `cpu.weight = 100`).
   - When multiple researchers run heavy computations simultaneously, the Linux Completely Fair Scheduler (CFS) automatically divides CPU time proportionally based on assigned weights. No user can monopolize the CPU.
3. **Strict Fork-Bomb Protection**:
   - `PidsLimit = 4096`: Every workspace container has a hard process table limit.
   - Runaway multiprocessing scripts or fork bombs cannot exhaust the host kernel PID space (`/proc/sys/kernel/pid_max`), preventing denial-of-service to the host OS.

---

## 3. Docker Container Configuration Mapping

In `go/internal/docker/controller.go`, container specifications are mapped directly to Docker HostConfig:

```go
// For SHARED_WEIGHTED mode:
hostConfig.Resources = container.Resources{
    CPUShares: req.CPUWeight, // default 100
    NanoCPUs:  0,             // Burstable to all cores
    PidsLimit: &pidsLimit,    // 4096
}
```

Safety validation in `ValidateContainerSafety`:
```go
if req.ResourceMode == ResourceModeSharedWeighted {
    if hostConfig.Resources.NanoCPUs != 0 {
        return fmt.Errorf("SHARED_WEIGHTED mode must have NanoCPUs=0 for dynamic burst")
    }
    if hostConfig.Resources.CPUShares <= 0 {
        return fmt.Errorf("CpuShares must be positive")
    }
}
```
