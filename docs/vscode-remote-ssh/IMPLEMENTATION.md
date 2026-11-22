# Implementation — Scoped Loopback Forwarding for VS Code Remote-SSH

## Code Changes
1. **Go Docker Controller (`go/internal/docker/controller.go`)**:
   - Function: `prepareWorkspaceRuntimeDir()`
   - Replaced `"AllowTcpForwarding no\n"` with least-privilege loopback-only forwarding directives:
     ```text
     AllowTcpForwarding local
     PermitOpen 127.0.0.1:* localhost:*
     AllowStreamLocalForwarding no
     GatewayPorts no
     X11Forwarding no
     ```
   - Preserved all existing isolation and authentication settings:
     - `PermitRootLogin no`, `PasswordAuthentication no`, `PubkeyAuthentication yes`, `UsePAM no`, `AllowUsers <username>`
     - `Subsystem sftp internal-sftp`
     - Non-root UID/GID (`20000-59999`), `CapDrop=ALL`, `no-new-privileges:true`, Docker socket prohibition, and host storage isolation.

2. **Go Regression Test (`go/internal/docker/controller_test.go`)**:
   - Added `TestWorkspaceSSHDConfigSupportsVSCodeRemoteSSH` verifying all required directives exist as full lines and all unsafe directives (`AllowTcpForwarding yes`, `AllowTcpForwarding no`, `PermitOpen any`, `AllowStreamLocalForwarding yes`, `GatewayPorts yes`, `X11Forwarding yes`, `PasswordAuthentication yes`, `PermitRootLogin yes`) are absent.

3. **Safe Existing Workspace Migration Tool (`scripts/ops/fix-workspace-vscode-ssh.py`)**:
   - Validates `workspace_id` against `/var/lib/labops/workspace_registry.json`, verifies live Docker container ID, labels (`labops.managed=true`, `labops.workspace_id`), port binding, read-only `/etc/labops-ssh` mount source, and rejects symlinks or paths escaping `/var/lib/labops-agent/runtime`.
   - Supports `--dry-run`, `--apply` (atomic write with timestamped backup, mode preservation, `sshd -t` + `sshd -T` validation, and automatic rollback on failure), and `--rollback <backup>`.
   - Covered by unit tests in `backend/tests/test_fix_workspace_vscode_ssh.py`.

## Policy Rationale
| Directive | Value | Purpose |
|---|---|---|
| `AllowTcpForwarding` | `local` | Enables SSH local/dynamic forwarding needed by VS Code Server while blocking remote (`-R`) forwarding |
| `PermitOpen` | `127.0.0.1:* localhost:*` | Restricts forwarding destinations exclusively to dynamic loopback ports inside the container |
| `AllowStreamLocalForwarding` | `no` | Closes Unix domain socket forwarding (previously defaulted to `yes` in OpenSSH) |
| `GatewayPorts` | `no` | Prevents binding forwarded listeners to non-loopback interfaces |
| `X11Forwarding` | `no` | Keeps GUI X11 forwarding disabled |
