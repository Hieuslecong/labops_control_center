# LabOps Workstation Primary Migration Plan

**Date:** 2026-11-17T15:43:00+07:00  
**Target:** Workstation `aiot-ws1` as Primary Control Plane  
**Status:** Non-Disruptive Migration Planned & Implemented on branch `feature/workstation-recovery`  

---

## 1. Executive Context & Objective

Rack server `aiotlab-rack` is currently inaccessible over the network. In accordance with Master Prompt V4:
- The system cannot assume SSH or network connectivity to the rack.
- Workstation `aiot-ws1` is converted to become the **Primary Control Plane** (`CONTROL_PLANE` / `HYBRID`).
- The rack server is retained in the host registry as `OFFLINE` with its last known heartbeat timestamp preserved; no fake telemetry is generated.
- When the rack server is physically recovered, it can be enrolled as a remote `COMPUTE_NODE` via approved mTLS gRPC tunnel without rebuilding the workstation control plane.

---

## 2. Target Architecture

```
                   [ Workstation (aiot-ws1) — Primary Control Plane ]
                                           │
         ┌─────────────────────────────────┼─────────────────────────────────┐
         │                                 │                                 │
         ▼                                 ▼                                 ▼
┌──────────────────┐             ┌──────────────────┐             ┌──────────────────┐
│ Next.js 16 UI    │ (Port 3000) │ FastAPI Backend  │ (Port 8000) │ 9Router AI Gate  │ (Port 20128)
│ Dashboard +      │ ──────────> │ V2 API Core +    │ ──────────> │ Model:           │
│ AI Assistant Tab │             │ Zalo Assistant   │             │ model_free       │
└──────────────────┘             └──────────────────┘             └──────────────────┘
                                           │
         ┌─────────────────────────────────┼─────────────────────────────────┐
         │                                 │                                 │
         ▼                                 ▼                                 ▼
┌──────────────────┐             ┌──────────────────┐             ┌──────────────────┐
│ LabOps Go Agent  │ (Port 9100) │ Atomic JSON &    │             │ Zalo AI Bot      │ (Polling engine)
│ Standalone Node  │             │ Relational Store │             │ 3.000 msgs/mo    │
│ Host/GPU inspect │             │ (backend/data/)  │             │ Quota Ledger     │
└──────────────────┘             └──────────────────┘             └──────────────────┘
                                           │
                             (Remote connection isolated)
                                           │
                                           ▼
                                ┌──────────────────────┐
                                │ Server Rack (OFFLINE)│
                                │ aiotlab-rack         │
                                │ Status: UNREACHABLE  │
                                └──────────────────────┘
```

---

## 3. Database Recovery & Status Declaration

> [!IMPORTANT]
> **RACK DATABASE NOT RECOVERED**: The historical database on `aiotlab-rack` is currently inaccessible over the network and has NOT been recovered. A new local database instance does NOT equate to recovering past rack records.

### Strategy:
1. **Local State Store**: LabOps V2 utilizes transactional, atomic JSON datastores in `backend/data/` (`v2_hosts.json`, `members.json`, `audit_logs.json`, `zalo_quota_ledger.json`, `zalo_reports.json`) with `fcntl.flock` concurrency locks.
2. **Workstation Registration**: `v2_hosts.json` has been updated with `workstation` as the primary local host (`ONLINE`) and `track` (`aiotlab-rack`) marked explicitly as `OFFLINE`.
3. **Relational PostgreSQL 16 Option**: For production relational storage, an isolated Docker container or standalone user instance running on `127.0.0.1:5432` can be initialized using migrations `backend/migrations/001_initial_schema.sql` through `004_labops_v2_complete.sql` without overwriting any host database.

---

## 4. Re-enrolling the Rack Server upon Physical Recovery

When physical access to `aiotlab-rack` is restored:
1. Verify rack IP connectivity without altering workstation routing.
2. Issue an mTLS client certificate from the workstation control plane for the rack agent.
3. Update `v2_hosts.json` observed state once valid telemetry heartbeats are received.
4. Transition the rack to `COMPUTE_NODE` status.
