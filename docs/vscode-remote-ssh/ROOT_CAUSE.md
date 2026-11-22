# Root Cause — VS Code Remote-SSH Connection Failure in LabOps Docker Workspaces

## Summary
VS Code Remote-SSH authenticated successfully against `labops-testacc` (`192.168.167.251:22001 -> 2222/tcp`), downloaded and unpacked `/workspace/.vscode-server`, and started the VS Code Server listening on loopback (`127.0.0.1:37677`) inside container `ws-testacc`, then failed immediately when opening the forwarded TCP channel:

```text
channel 3: open failed: administratively prohibited: open failed
ERROR: TCP port forwarding appears to be disabled on the remote host.
```

## Technical Root Cause
1. **Hard-disabled TCP forwarding in container `sshd_config`**:
   - In `go/internal/docker/controller.go` (`prepareWorkspaceRuntimeDir()`), LabOps generated the container-specific read-only OpenSSH configuration at `/var/lib/labops-agent/runtime/<workspace_id>/ssh_ro/sshd_config` (bind-mounted `:ro` to `/etc/labops-ssh` inside the container) with:
     ```text
     AllowTcpForwarding no
     X11Forwarding no
     ```
   - `sshd` inside the container runs directly as PID 1 via `/usr/sbin/sshd -D -e -f /etc/labops-ssh/sshd_config` (`OpenSSH_10.0p2 Debian-7+deb13u4`).
   - Verified via `docker exec 118bb15b6d7d... /usr/sbin/sshd -T -f /etc/labops-ssh/sshd_config`:
     `allowtcpforwarding no` (while `allowstreamlocalforwarding yes` and `permitopen any` defaulted to permissive values even though TCP forwarding was disabled).

2. **Why authentication succeeded while VS Code failed**:
   - Step 1 (SSH session & SFTP/exec channel) uses OpenSSH `PubkeyAuthentication yes` via `/etc/labops-ssh/authorized_keys`, which was working normally.
   - Step 2 (VS Code Remote-SSH tunnel) requires SSH local or dynamic SOCKS forwarding (`direct-tcpip`) to reach the dynamically allocated VS Code Server loopback port (`127.0.0.1:<port>`) inside the workspace container. Because `AllowTcpForwarding no` disables all `direct-tcpip` channels, `sshd` returned `administratively prohibited`.

3. **Unrelated components ruled out**:
   - Host Workstation SSHD (`/etc/ssh/sshd_config`) is NOT involved in workspace port `22001` (`docker-proxy` routes `192.168.167.251:22001` directly to container port `2222`).
   - WireGuard VPN / Pangolin / host firewall allowed TCP `22001` cleanly.
   - `authorized_keys` (`command="/usr/bin/python3 /etc/labops-ssh/first_login_gate.py"` for unactivated accounts) did not restrict port forwarding (`no-port-forwarding` is not set).
   - Nothing regenerates `sshd_config` after provisioning; `SyncAuthorizedKeys()` and `sync_container_first_login_gate()` only manage `authorized_keys`, `first_login_gate.py`, and `first_login_policy.json`.
