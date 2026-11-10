# Legacy SSH — Pre-deployment baseline

**Captured:** 2026-11-05 (Asia/Ho_Chi_Minh)  
**Baseline status:** `BLOCKED_BY_INFRASTRUCTURE` · not an authoritative host baseline  
**Change policy:** read-only inspection only; no Legacy SSH configuration or runtime changes were made in Phase 2.

## Scope protected from Secure Access v2

OpenSSH daemon and ports; SSH/NAT forwarding; Linux accounts and passwords; user and host keys; `authorized_keys`; `/etc/ssh/sshd_config` and included files; firewall/routing; existing SSH containers; active SSH sessions and training jobs. These remain outside the new WireGuard/Teleport provisioning path.

## Baseline evidence

| Protected item | Baseline value/evidence | Status |
|---|---|---|
| OpenSSH service | User previously supplied `ssh.service` as `active (running)`. Current `systemctl` check could not connect to the system bus. | `NOT_REVALIDATED` |
| SSH listen addresses/ports | `ss` could not open netlink in the current sandbox. No listening port was confirmed. | `BLOCKED_BY_INFRASTRUCTURE` |
| SSH daemon configuration | No checksum captured for `/etc/ssh/sshd_config` or `/etc/ssh/sshd_config.d/*`; this execution environment is not a verified administrative host session. | `BLOCKED_BY_INFRASTRUCTURE` |
| Host keys / authorized keys | No file was read, copied or hashed; host key and `authorized_keys` baseline is unavailable. | `BLOCKED_BY_INFRASTRUCTURE` |
| Linux accounts | No account inventory was collected from the Workstation. No account was created, removed or edited. | `BLOCKED_BY_INFRASTRUCTURE` |
| Active SSH sessions | No host session inventory was collected. The process list available here only describes the sandbox namespace. | `BLOCKED_BY_INFRASTRUCTURE` |
| Firewall, NAT, routes and port mappings | `ip`, `nft`, `iptables` and `ss` reads were denied by netlink restrictions. No rules or routes were changed. | `BLOCKED_BY_INFRASTRUCTURE` |
| Existing containers/workspaces | Docker CLI is present, but access to `/var/run/docker.sock` was denied. No container state or port mapping was observed. | `BLOCKED_BY_INFRASTRUCTURE` |
| Training jobs | Workstation process/GPU workloads are not visible from this namespace. No job was stopped or modified. | `NOT_TESTED` |
| Admin rescue path | The previously user-provided SSH address is recorded only as `118.70.xxx.xxx`; it was not revalidated and is not assumed to be the VPN Gateway. A read-only SSH probe from this sandbox failed locally with `socket: Operation not permitted`; no remote session was established. | `NOT_REVALIDATED` |

No private key, password, `authorized_keys` content, token or SSH configuration content is included in this document. Checksums are **not available**; therefore this file cannot be used to claim that Legacy SSH was verified unchanged.

## Phase 2 separation record

- No command changed OpenSSH, SSH ports, accounts, keys, firewall/NAT, routes, containers, sessions or jobs.
- No Teleport OpenSSH integration was run, and no Teleport service was installed on the Workstation.
- No Legacy SSH account or credential was imported into Secure Access v2.
- Secure Access v2 remains blocked from creating a researcher access path until its separate Gateway, Teleport cluster and workspace are verified.
- The LabOps application code from Phase 1 disables its old per-user SSH/password provisioning endpoints. This is an application-layer change; it did not change the host OpenSSH configuration. Existing LabOps workflows for managing legacy credentials are therefore not verified as preserved by the UI/API and must not be described as operating normally without a separate review.

## Required capture before deployment

An operator with authorized read-only access to the actual Workstation must collect service state, listening ports, SSH configuration checksums, account/session and container inventory, SSH-related firewall/NAT/routing evidence, and workload status. Record only checksums/counts/redacted metadata; never paste private key or password contents. Repeat the same capture after each new-infrastructure phase. If any protected value changes unexpectedly, stop Secure Access v2 deployment and investigate without altering Legacy SSH.

**Readiness:** Legacy baseline capture is incomplete. Do not begin host-side deployment or claim non-impact until this baseline is captured and can be compared.
