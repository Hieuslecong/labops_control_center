# Dependency Installation and Verification Report

**Date:** 2026-11-17T15:23:00+07:00  
**Host:** Workstation Primary (`aiot-ws1`)  
**Scope:** Runtime dependencies for LabOps Backend, Dashboard, Go Node Agent, 9Router, and Zalo AI Assistant.

---

## 1. Summary of Dependencies

| Dependency | Version Required | Version Detected / Installed | Action Taken | Result | Unresolved Issues |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Python** | Python 3.10+ | Python 3.13.5 | Pre-installed system Python | **VERIFIED** | None |
| **Python venv / pip** | venv with pip | Pip 26.3 | Bootstrap via user-space `get-pip.py` into `backend/.venv` | **VERIFIED** | None |
| **Backend Packages** | `backend/requirements.txt` | fastapi 0.163.1, uvicorn 0.54.0, asyncpg 0.32.0, sqlalchemy 2.1.9, httpx 0.28.1 | Installed in `backend/.venv` | **VERIFIED** | None. Imports tested. |
| **Node.js** | Node.js 22 LTS | v22.23.5 | Sourced via `~/.nvm/versions/node/v22.23.5/bin/node` | **VERIFIED** | None |
| **npm** | npm 10+ | 12.2.2 | Sourced via NVM | **VERIFIED** | None |
| **Bun** | Bun 1.0+ | Bun 1.4.4 | Installed globally in user NVM via `npm install -g --allow-scripts=bun bun` | **VERIFIED** | None |
| **Frontend Packages** | Per `bun.lock` | 325 packages (Next.js 16.4.2, React 19.3.0, TanStack Table 9.3.1, etc.) | Installed via `bun install --frozen-lockfile` | **VERIFIED** | None. `tsc --noEmit` passed with 0 errors. |
| **Go** | Go 1.24+ | go1.24.4 linux/amd64 | System toolchain | **VERIFIED** | None |
| **LabOps Go Node Agent** | Standalone high-performance agent | Built binary at `/usr/local/bin/labops-agent` | Verified binary executable and metadata | **VERIFIED** | Requires standalone config (`LABOPS_GATEWAY_ENABLED=false`, `LABOPS_SERVER_ID=workstation`) |
| **9Router** | 9Router AI Gateway | 0.5.119 | Sourced via NVM global package; service already active on port 20128 | **VERIFIED** | Live API tested with `model_free`; returned `PONG`. |
| **NVIDIA Driver** | Compatible with A30 | 620.70.06 (CUDA 13.5) | Preserved existing host driver | **VERIFIED** | Live GPU workload running on host protected. |

---

## 2. Safety and Isolation Adherence

1. **Non-Disruptive Installation**:
   - Zero modifications to `/etc/apt/sources.list`, `/etc/network`, or system systemd unit files.
   - Zero root packages installed via apt; pip bootstrap executed strictly within isolated `backend/.venv`.
   - Bun installed in user-owned NVM binary directory.
   - Frontend packages locked against immutable `bun.lock`.
2. **GPU Workload Protection**:
   - The running user process on the host A30 GPU (23 GB VRAM utilized) was audited and left completely undisturbed.
