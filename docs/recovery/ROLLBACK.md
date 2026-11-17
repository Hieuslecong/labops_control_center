# LabOps Workstation Deployment Rollback Procedure

**Date:** 2026-11-17T15:44:00+07:00  
**Host Target:** `aiot-ws1`  
**Rollback Policy:** Strict preservation of existing user workloads, host databases, network settings, and persistent data.

---

## 1. Rollback Principles

In the event of an operational failure during startup or runtime:
1. **Targeted Process Termination Only**: Terminate **only** the newly deployed LabOps processes (Backend port 8000, Frontend port 3000, local test agent).
2. **Never Disrupt Host Workloads**: Do **NOT** terminate GPU jobs running under other user accounts (e.g., `nhannv` training processes).
3. **Preserve Generated Data & Logs**: Retain `backend/data/*.json`, `zalo_quota_ledger.json`, `zalo_reports.json`, and process logs for post-mortem analysis.
4. **No Network Changes**: Do not reset or reconfigure network interfaces or WireGuard routes during rollback.

---

## 2. Step-by-Step Rollback Execution

### Step 1: Identify and Stop Newly Started LabOps Processes
```bash
# Locate and terminate only the LabOps backend and frontend processes started for recovery
kill $(pgrep -f "backend/app/main.py") 2>/dev/null || true
kill $(pgrep -f "next-server.*3000") 2>/dev/null || true
```

### Step 2: Preserve Logs and State Files
```bash
# Archive runtime logs for debugging
mkdir -p /tmp/labops_rollback_logs
cp backend/data/*.json /tmp/labops_rollback_logs/ 2>/dev/null || true
```

### Step 3: Revert Git Working Tree (If Needed)
```bash
# Ensure working branch feature/workstation-recovery is clean
cd /home/aiot-ws1/projects/labops_control_center
git status
```

### Step 4: Verify Existing Workloads Intact
```bash
# Verify GPU A30 workload is continuing without interruption
nvidia-smi
# Verify 9Router is still operational
curl -s http://127.0.0.1:20128/v1/models | grep -q "model_free" && echo "9Router OK"
```
