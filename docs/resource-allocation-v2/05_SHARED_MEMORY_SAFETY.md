# 05 — SHARED HOST-PROTECTED RAM & MEMORY SAFETY SPECIFICATION
**LabOps Control Center V2 — Resource Allocation & Storage Subsystem**

---

## 1. Problem Statement: The Host OOM Crash Vulnerability

Under legacy static RAM allocations, containers were assigned static bounds (e.g. 32 GB or 64 GB). If the total allocated RAM across all users exceeded physical host RAM, or if memory contention occurred, the Linux kernel Out-Of-Memory (OOM) killer would arbitrarily kill processes based on badness score heuristics. 

In production, this frequently led to the termination of:
- `sshd` (locking administrators out of the server)
- `dockerd` / `containerd` (crashing all running AI containers)
- `postgres` (corrupting database transactions)
- `labops-agent` (halting telemetry and control plane communication)

---

## 2. Shared Host-Protected RAM Architecture (`SHARED_HOST_PROTECTED`)

LabOps Control Center V2 introduces **Shared Host-Protected Memory Management**:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Total Physical RAM (100%)                    │
│                 Workstation: 128 GiB | Rack: 256 GiB            │
└────────────────────────────────┬────────────────────────────────┘
                                 │
        ┌────────────────────────┴────────────────────────┐
        ▼                                                 ▼
┌───────────────────────────────────┐     ┌───────────────────────────────────┐
│     Host OS Safety Reserve (20%)  │     │      Shared Container Budget      │
│  Min 8 GiB (WS) / 16 GiB (Rack)   │     │        Total RAM - Host Reserve   │
│  - Linux Kernel & Buffers         │     │  - Workspace Containers           │
│  - PostgreSQL & FastAPI Backend   │     │  - Dynamic Memory Bursts          │
│  - Go Node Agent & mTLS Gateway   │     │  - Monitored by Linux PSI         │
│  - OpenSSH Host Daemons           │     │                                   │
└───────────────────────────────────┘     └───────────────────────────────────┘
```

---

## 3. Mathematical Model & Budget Allocation

1. **Host Safety Reserve Calculation**:
   $$\text{HostReserveBytes} = \max(0.20 \times \text{TotalRamBytes}, \text{MinReserve})$$
   - On Workstation ($128 \text{ GiB}$ total RAM): $20\% \approx 25.6 \text{ GiB}$ host reserve.
   - On Rack ($256 \text{ GiB}$ total RAM): $20\% \approx 51.2 \text{ GiB}$ host reserve.
2. **Shared Container Budget**:
   $$\text{SharedBudgetBytes} = \text{TotalRamBytes} - \text{HostReserveBytes}$$
3. **Container Ceiling Enforcement**:
   - Individual workspace containers are configured with:
     $$\text{MemoryLimit} = \text{SharedBudgetBytes}$$
   - This prevents any single container or group of containers from encroaching into the 20% host safety zone. The host OS daemons always have guaranteed headroom.

---

## 4. Linux Pressure Stall Information (PSI) Telemetry

The Go Node Agent reads Linux kernel memory pressure stall information from `/proc/pressure/memory`:

```text
some avg10=0.00 avg60=0.00 avg300=0.00 total=123456
full avg10=0.00 avg60=0.00 avg300=0.00 total=78910
```

### Telemetry Mapping & Alert Thresholds
- **`NORMAL`**: `some_avg10 < 20.0` and `full_avg10 < 5.0`. No memory contention.
- **`ELEVATED`**: `some_avg10 >= 20.0` or `full_avg10 >= 5.0`. The system is beginning to page out inactive buffers; warnings are raised on the dashboard.
- **`CRITICAL`**: `some_avg10 >= 40.0` or `full_avg10 >= 15.0`. High memory pressure; admission of new high-memory workloads is paused to prevent kernel thrashing.

The telemetry is continuously reported via `/api/v1/agent/resources/profile` and visible in the Admin Provisioning Wizard.
