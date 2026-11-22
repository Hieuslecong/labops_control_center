# Runtime Migration Guide — Existing Docker Workspaces

Existing workspaces provisioned prior to rebuilding and deploying `labops-agent` still contain `AllowTcpForwarding no` in `<runtime_dir>/ssh_ro/sshd_config`. Because `sshd_config` is bind-mounted read-only at `/etc/labops-ssh/sshd_config`, you can safely migrate each workspace without recreating the container or losing in-memory processes.

## Empirical Reload Findings (`ws-testacc` / `OpenSSH_10.0p2`)
- Tested in an identical ephemeral container (`UID 29999`, `CapDrop=ALL`, `no-new-privileges:true`, `PID 1 = /usr/sbin/sshd -D -e -f /etc/labops-ssh/sshd_config`):
  - Sending `SIGHUP` (`docker kill --signal=HUP <CID>`) cleanly reloaded `/etc/labops-ssh/sshd_config`:
    ```text
    Received SIGHUP; restarting.
    Server listening on 0.0.0.0 port 2222.
    ```
  - Container remained running (`State.Running=true`, `ExitCode=0`), and new SSH connections enforced the updated forwarding policy immediately without interrupting running research jobs or restarting the container.

## Step 1: Identify and Verify Target Workspace
```bash
docker ps --filter "publish=22001" --format 'table {{.ID}}\t{{.Names}}\t{{.Ports}}'
```

## Step 2: Dry-Run Validation (No Changes Written)
```bash
python3 scripts/ops/fix-workspace-vscode-ssh.py \
  --workspace-id ws-7e9cc034ff \
  --dry-run
```
Expected status: `dry-run -> MIGRATED`, with a unified diff replacing `AllowTcpForwarding no` with the 4 scoped directives.

## Step 3: Apply Migration (Requires Admin Approval)
```bash
python3 scripts/ops/fix-workspace-vscode-ssh.py \
  --workspace-id ws-7e9cc034ff \
  --apply
```
*(Run with `sudo` if the runtime directory is owned by root on another host; on `aiot-ws1` `/var/lib/labops-agent/runtime/ws-7e9cc034ff` is owned by `aiot-ws1:aiot-ws1`.)*

The script automatically:
1. Backs up `sshd_config` to `/var/lib/labops-agent/runtime/<workspace_id>/sshd_config.<UTC_TIMESTAMP>.bak`.
2. Writes the updated `sshd_config` atomically, preserving file mode/ownership and leaving `authorized_keys` untouched.
3. Validates syntax (`sshd -t`) and effective policy (`sshd -T`) inside the target container, rolling back automatically on error.

## Step 4: Safe In-Place Reload of Container SSHD (Requires Admin Approval)
**Do NOT run `docker restart` or host `systemctl restart ssh`.**
Verify PID 1 is `sshd`, then send `SIGHUP` strictly to the verified container:
```bash
CID="118bb15b6d7d2dd6e17dbc0f6111730c702f49abe8d778034ecb3e243b635f42"
docker exec "$CID" cat /proc/1/cmdline | tr '\0' ' '; echo
docker kill --signal=HUP "$CID"
docker exec "$CID" /usr/sbin/sshd -T -f /etc/labops-ssh/sshd_config | grep -Ei 'allowtcpforwarding|permitopen|gatewayports|allowstreamlocalforwarding'
```
Confirm the container remains running (`docker ps --filter "id=$CID"`) and verify SSH authentication from macOS (`ssh -T labops-testacc 'id && printf "SSH_OK\n"'`).
