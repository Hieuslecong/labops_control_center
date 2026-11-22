# Security Verification — Scoped SSH Forwarding Boundary

## Allowed Scope
- **Local & Dynamic TCP Forwarding to Container Loopback Only**:
  - `AllowTcpForwarding local` permits SSH client `-L`, `-D` (SOCKS5 dynamic proxy used by VS Code Remote-SSH), and `-W` channels.
  - `PermitOpen 127.0.0.1:* localhost:*` limits open target addresses strictly to loopback ports inside the container namespace (supporting dynamic VS Code Server ports such as `37677`).

## Blocked / Enforced Controls
| Vector / Control | Directive / Enforcement | Observed Verification |
|---|---|---|
| LAN Host / Agent Port (`192.168.167.251:9100`, `8000`) | `PermitOpen 127.0.0.1:* localhost:*` | Immediate SSHD rejection (`rc=255`, `stdio forwarding failed`) |
| Other Container IPs (`172.17.0.x:<port>`) | `PermitOpen 127.0.0.1:* localhost:*` | Blocked by OpenSSH `PermitOpen` before TCP dial |
| Remote Port Forwarding (`ssh -R`) | `AllowTcpForwarding local` | Immediate SSHD rejection (`Error: remote port forwarding failed`, `rc=255`) |
| Unix Socket Forwarding | `AllowStreamLocalForwarding no` | Verified `allowstreamlocalforwarding no` in `sshd -T` |
| External Listener Binding | `GatewayPorts no` | Verified `gatewayports no` in `sshd -T` |
| X11 Forwarding (`ssh -X`) | `X11Forwarding no` | `DISPLAY` remains unset (`X11=[]`), `x11forwarding no` in `sshd -T` |
| Password / Root Login | `PasswordAuthentication no`, `PermitRootLogin no` | Password login rejected with `Permission denied (publickey)` (`rc=255`) |
| Privilege / Container Isolation | `UID>=20000`, `CapDrop=ALL`, `no-new-privileges`, `:ro` `/etc/labops-ssh` | Unchanged; container user cannot modify `sshd_config` or `authorized_keys` |
| First-Login Activation Gate | `command="/usr/bin/python3 /etc/labops-ssh/first_login_gate.py"` | Unchanged in `authorized_keys`; `fix-workspace-vscode-ssh.py` does not touch `authorized_keys` |
| Revoked SSH Keys | `SyncAuthorizedKeys` clears `authorized_keys` | Prevents new SSH & forwarding connections (already open sessions remain unaffected until terminated separately) |
