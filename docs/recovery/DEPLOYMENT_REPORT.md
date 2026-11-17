# LabOps Control Center Workstation Deployment & Recovery Report

**Execution Date:** 2026-11-17T15:45:00+07:00  
**Host:** Workstation `aiot-ws1` (Debian 13 Trixie, NVIDIA A30 24GB)  
**Branch:** `feature/workstation-recovery`  
**Base Commit SHA:** `9b1d8e7e966c29892e8b5787711ad1f311411bfe`  
**Author / Lead:** Senior DevOps & AI Systems Architect (Antigravity Agent)  

---

## 1. Executive Summary

Due to connectivity issues on rack server `aiotlab-rack`, **LabOps Control Center V2** has been successfully recovered, adapted, and built on workstation `aiot-ws1` as the **Primary Control Plane**.

All phases were executed strictly under the **Critical Non-Disruptive Policy**:
- **Data & Network Safety**: 0 changes to host IP, DNS, routing, WireGuard VPN, or iptables.
- **GPU Safety**: Active user training workload on the host A30 GPU (23 GB VRAM utilized at 100% compute) was left completely undisturbed.
- **Docker & Storage Safety**: Zero destructive prune operations; 0 modifications to existing host disks or volumes.

---

## 2. Recovery Phases Completed

### Phase 0: Workstation Audit
- **OS & Kernel**: Debian GNU/Linux 13 (trixie) 13.7, Kernel `6.12.116+deb13-amd64`.
- **GPU**: NVIDIA A30 (24,576 MiB total), Driver `620.70.06`, CUDA `13.5`.
- **Memory & Storage**: 125 GiB RAM (94 GiB available), Root drive 778 GiB free (2% used).
- Full audit saved to `docs/recovery/WS_ENVIRONMENT_AUDIT.md`.

### Phase 1: Repository & Branch Setup
- Verified clean repository state.
- Created dedicated working branch `feature/workstation-recovery` from `feature/labops-control-center`.

### Phase 2: Runtime Dependencies
- **Python**: Isolated virtual environment created at `backend/.venv` via user-space bootstrap; all dependencies from `backend/requirements.txt` (FastAPI 0.163, Uvicorn 0.54, Asyncpg 0.32, SQLAlchemy 2.1.9, etc.) installed.
- **Node.js & npm**: Verified Node v22.23.5 and npm 12.2.2 via NVM.
- **Bun**: Installed Bun 1.4.4 into user NVM binary directory.
- **Frontend Packages**: Installed all 325 packages via `bun install --frozen-lockfile`.
- Full details documented in `docs/recovery/DEPENDENCY_REPORT.md`.

### Phase 3: Workstation Primary Architecture
- Reconfigured host identity to `workstation` (`CONTROL_PLANE`).
- Updated `backend/data/v2_hosts.json` setting `workstation` as Local Primary (`ONLINE`) and `track` (`aiotlab-rack`) as `OFFLINE` with historical heartbeat preserved.
- Isolated legacy rack gateway tunnel (`LABOPS_GATEWAY_ENABLED=false`) to eliminate connection hang.

### Phase 4: Database Status
- **RACK DATABASE NOT RECOVERED**: Past database on `aiotlab-rack` remains inaccessible over the network.
- LabOps V2 persistent state is managed via transactional JSON stores in `backend/data/` with `fcntl.flock` atomic file locking.
- Schema migrations `001` through `004` validated for PostgreSQL 16 standalone deployment when desired.

### Phase 5 & 6: Backend & Frontend Recovery
- FastAPI backend updated with AI Assistant endpoints (`/api/v1/ai-assistant/*`).
- Next.js 16 App Router Dashboard enhanced with route `/dashboard/ai-assistant` and sidebar integration.
- `bun run typecheck` passed with 0 errors; `bun run build` generated all 45 routes successfully.

### Phase 7: 9Router AI Gateway Integration
- Verified 9Router 0.5.119 running on `http://127.0.0.1:20128/v1`.
- Verified user-designated model `model_free` with live chat completion test (`PONG` returned).

### Phase 8 & 9: Zalo AI Assistant & Dashboard Tab
- Implemented `QuotaLedger` enforcing hard limit of 3.000 messages/month (2.800 standard / 200 critical alert reserve) with atomic reservation and idempotency.
- Implemented `ZaloBotEngine` with 7 standard commands: `/start`, `/help`, `/ask`, `/status`, `/jobs`, `/report`, `/quota` (plus `/gpu` alias).
  - Consolidated `/status` summarizes Rack/WS, CPU/RAM, GPU/VRAM, Disk, Services, and Alerts in a single response.
  - Read-only tools enabled; destructive write tools disabled.
  - Single-consumer polling guard implemented.
- Implemented `ReportGenerator` creating automated weekly reports (default schedule: Friday 17:00 Asia/Ho_Chi_Minh).
- Created interactive 5-tab Dashboard view at `/dashboard/ai-assistant` (Tổng quan, Cấu hình, Báo cáo tuần, Quyền & Công cụ, Lịch sử & Quota).

### Phase 10: Isolated Testing
- Executed unit and regression tests:
  - `backend/tests/test_zalo_ai_assistant.py`: 5 passed.
  - `backend/tests/test_p0_regressions.py`, `test_v2_comprehensive.py`, `test_http_endpoints.py`: 22 passed.
  - Full details in `docs/recovery/TEST_REPORT.md`.

---

## 3. Safe Startup Instructions

To start the recovered LabOps Control Center on `aiot-ws1`:

```bash
# 1. Activate working directory and environment
cd /home/aiot-ws1/projects/labops_control_center
export PATH="$HOME/.nvm/versions/node/v22.23.5/bin:$PATH"

# 2. Configure environment (copy template and set secrets)
cp .env.workstation.example .env.local

# 3. Launch Backend (Port 8000)
PYTHONPATH=backend backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# 4. Launch Frontend Dashboard (Port 3000)
bun dev
# (Or production mode): bun start
```

---

## 4. Rollback Procedure
If any unexpected issue occurs:
1. Terminate only the newly launched backend (`kill $(pgrep -f "app.main:app")`) and frontend (`kill $(pgrep -f "next-server.*3000")`).
2. Follow instructions in `docs/recovery/ROLLBACK.md`. Existing user GPU workloads and system daemons remain completely unaffected.
