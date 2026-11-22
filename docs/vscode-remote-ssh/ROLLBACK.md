# Rollback Procedure — Workspace SSHD Forwarding Policy

If a migrated workspace experiences issues after applying the VS Code Remote-SSH policy, restore the exact pre-migration `sshd_config` backup without recreating the container.

## 1. Locate the Timestamped Backup
Backups are stored by `scripts/ops/fix-workspace-vscode-ssh.py` outside `ssh_ro` at:
```text
/var/lib/labops-agent/runtime/<workspace_id>/sshd_config.<UTC_TIMESTAMP>.bak
```

## 2. Atomic Rollback via Migration Tool
```bash
python3 scripts/ops/fix-workspace-vscode-ssh.py \
  --workspace-id ws-7e9cc034ff \
  --rollback /var/lib/labops-agent/runtime/ws-7e9cc034ff/sshd_config.<UTC_TIMESTAMP>.bak
```
This atomically restores the backup content, preserves file permissions/ownership, and runs `sshd -t -f /etc/labops-ssh/sshd_config` inside the container.

## 3. Reload Container SSHD to Apply Rolled-Back Config
Verify PID 1 is `sshd`, then send `SIGHUP` only to the target container:
```bash
CID="118bb15b6d7d2dd6e17dbc0f6111730c702f49abe8d778034ecb3e243b635f42"
docker kill --signal=HUP "$CID"
docker exec "$CID" /usr/sbin/sshd -T -f /etc/labops-ssh/sshd_config | grep -Ei 'allowtcpforwarding|permitopen'
```
Expected after rollback: `allowtcpforwarding no`.

## 4. Source Code Revert (Optional)
To revert Go controller changes only (without touching existing uncommitted work):
```bash
git checkout -- go/internal/docker/controller.go go/internal/docker/controller_test.go
```
