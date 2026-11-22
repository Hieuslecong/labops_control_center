# Test Report — VS Code Remote-SSH Fix

## Status Summary
- `SOURCE_FIXED`: **YES** (`go/internal/docker/controller.go`, `go/internal/docker/controller_test.go`, `scripts/ops/fix-workspace-vscode-ssh.py`, `backend/tests/test_fix_workspace_vscode_ssh.py`)
- `UNIT_TEST_PASSED`: **YES** (Go full suite + `go vet` + Python migration suite)
- `SSHD_CONFIG_VALIDATED`: **YES** (Verified with `OpenSSH_10.0p2` `sshd -t` and `sshd -T` in ephemeral workspace container; dry-run validated on live `ws-testacc` `ws-7e9cc034ff`)
- `SSH_FORWARDING_VERIFIED`: **YES** (Verified end-to-end in isolated ephemeral workspace container `sha256:eb684496...` after `SIGHUP` reload; awaiting admin approval for production live workspace migration)
- `RUNTIME_CONFIG_MIGRATED`: **PENDING_APPROVAL** (`--dry-run` passed on `ws-7e9cc034ff`; `--apply` withheld pending approval)
- `SSHD_RELOADED`: **PENDING_APPROVAL** (`SIGHUP` verified safe on ephemeral `OpenSSH_10.0p2` UID `29999` + `CapDrop=ALL`; production `ws-testacc` not signaled without approval)
- `VSCODE_REMOTE_SSH_VERIFIED`: **PENDING_APPROVAL** (Needs macOS VS Code 1.144.0 / Remote-SSH 0.128.0 connect after approving runtime migration + reload of `ws-testacc`)

## Executed Test Suites & Observed Outputs

### 1. Go Docker & Agent Test Suite
```bash
cd /home/aiot-ws1/projects/labops_control_center/go
go test -count=1 ./internal/docker   # PASS (0.243s) — includes TestWorkspaceSSHDConfigSupportsVSCodeRemoteSSH
go test -count=1 ./...               # PASS across all Go packages (agent, config, docker, gateway, gitaiotcli, gpu, host, pki, process, repositoryguard, resources, storage, transport)
go vet ./...                         # PASS (exit 0)
```

### 2. Migration Script Unit Tests
```bash
PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/test_fix_workspace_vscode_ssh.py
# Result: 4 passed in 0.06s
```

### 3. Ephemeral Container OpenSSH Integration & Negative Security Tests
Executed via isolated ephemeral container (`image sha256:eb684496...`, `UID 29999`, `--cap-drop=ALL`, `--security-opt=no-new-privileges:true`, bound to `127.0.0.1` only):
- Pre-migration check (`AllowTcpForwarding no`): `-W 127.0.0.1:2222` failed with `rc=255` (`refused local port forward: originator 127.0.0.1 port 65535, target 127.0.0.1 port 2222`).
- Post-migration syntax & effective config (`sshd -t` -> `rc=0`, `sshd -T`):
  - `allowtcpforwarding local`
  - `permitopen 127.0.0.1:* localhost:*`
  - `allowstreamlocalforwarding no`
  - `gatewayports no`
  - `x11forwarding no`
  - `passwordauthentication no`
  - `permitrootlogin no`
- In-place `SIGHUP` reload (`docker kill --signal=HUP`): container stayed up (`Running=true`, `ExitCode=0`), logged `Received SIGHUP; restarting.` and `Server listening on 0.0.0.0 port 2222.`.
- Live SSH & forwarding after `SIGHUP`:
  - `ssh -T ... 'id && printf "SSH_OK\n"'`: `uid=29999(ephuser) ... SSH_OK` (`rc=0`)
  - `ssh -W 127.0.0.1:37677`: `VSCODE_LOOPBACK_ECHO_OK` (`rc=0`)
  - `ssh -L 127.0.0.1:<port>:127.0.0.1:37677`: `VSCODE_LOOPBACK_ECHO_OK`
  - `ssh -D 127.0.0.1:<port>` (SOCKS5): handshake `0500`, reply status `0`, `VSCODE_LOOPBACK_ECHO_OK`
  - `ssh -W 192.168.167.251:9100`: `rc=255`, `stdio forwarding failed` (DENIED)
  - `ssh -W 192.168.167.251:8000`: `rc=255`, `stdio forwarding failed` (DENIED)
  - `ssh -R 127.0.0.1:0:127.0.0.1:22`: `rc=255`, `Error: remote port forwarding failed for listen port 0` (DENIED)
  - `ssh -X`: `X11=[]` (DENIED)
  - Password authentication: `rc=255`, `Permission denied (publickey).` (DENIED)

### 4. Production `ws-testacc` (`ws-7e9cc034ff`) Dry-Run
```bash
python3 scripts/ops/fix-workspace-vscode-ssh.py --workspace-id ws-7e9cc034ff --dry-run
# Status: dry-run -> MIGRATED (verified registry, container 118bb15b6d7d..., mount /var/lib/labops-agent/runtime/ws-7e9cc034ff/ssh_ro; 0 writes)
```
