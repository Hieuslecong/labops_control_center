# 12 — PRODUCTION MIGRATION & SAFE ROLLBACK RUNBOOK
**LabOps Control Center V2 — Resource Allocation & Storage Subsystem**

---

## 1. Migration Strategy Overview

The resource allocation subsystem update is designed to be **100% non-destructive and zero-downtime**:
- **Existing Workspaces**: Workspaces already running continue operating without interruption. No running container is restarted or deleted during deployment.
- **Existing Filesystem Data**: Existing files inside user workspace directories are preserved via `exist_ok=True`.
- **Database Schema**: Forward-compatible JSON store automatically inherits new storage and resource attributes.

---

## 2. Pre-Deployment Verification Checklist

Before deploying the update to production:
1. Verify that all secondary storage drives are mounted and online:
   ```bash
   df -hT /media/LabOps-Data /data
   ```
2. Verify that existing GPU workloads are active and note their PIDs/container IDs:
   ```bash
   nvidia-smi
   docker ps --filter "label=com.labops.managed=true"
   ```
3. Create a point-in-time backup of the LabOps data directory:
   ```bash
   cp -r backend/data backend/data_backup_$(date +%Y%m%d_%H%M%S)
   ```

---

## 3. Deployment Steps

1. **Deploy Go Node Agent**:
   ```bash
   cd go
   go build -o /usr/local/bin/labops-agent ./cmd/labops-agent
   sudo systemctl restart labops-agent
   # Verify port 9100 and storage discovery
   curl -s http://127.0.0.1:9100/api/v1/agent/storage | jq .
   ```
2. **Deploy FastAPI Backend**:
   ```bash
   cd backend
   # Ensure dependencies and run test validation
   PYTHONPATH=. .venv/bin/pytest tests/test_storage_and_shared_resources.py
   sudo systemctl restart labops-backend
   ```
3. **Deploy Next.js Dashboard**:
   ```bash
   bun run build
   sudo systemctl restart labops-web
   ```

---

## 4. Rollback Procedure

If unexpected behavior or drive detection failures occur in production:
1. **Restore Backend Data**:
   ```bash
   cp -r backend/data_backup_*/* backend/data/
   ```
2. **Revert Services**:
   ```bash
   git checkout hardening/labops-v2
   sudo systemctl restart labops-backend labops-agent labops-web
   ```
3. **Verify Host Stability**:
   ```bash
   nvidia-smi
   docker ps
   ```
   *No user workspace data or model checkpoints are altered during rollback because all storage operations are additive and directory-confined.*
