# Workspace Egress Test Report

Snapshot: 2026-11-17
Result: HOST_ENFORCEMENT_NOT_VERIFIED

## Policy and source checks

- The pilot host egress and rollback scripts were replaced with fail-closed no-op guards. The static script safety regression test passed as part of the Python suite.
- Docker runtime attestation includes the observed NetworkMode.
- Backend and Agent registry logic reject REPOSITORY_ENFORCED workspaces unless the recorded and observed mode is none and egress status is VERIFIED_NO_NETWORK.
- A fake runtime observation using bridge mode is rejected by regression tests; a fake none-mode observation is accepted as an attestation input.
- No Docker create/inspect/stop operation was performed on the Rack.

## Runtime network tests

| Test | Expected | Actual | Result |
|---|---|---|---|
| New isolated pilot workspace | network mode none and no outbound path | No pilot was created | SKIP |
| DNS resolution from pilot | unavailable without Broker channel | Not run | SKIP |
| Direct GitHub HTTPS over IPv4 | denied | Not run | SKIP |
| Direct GitHub HTTPS over IPv6 | denied | Not run | SKIP |
| curl, wget, Python, Node download paths | denied | Not run | SKIP |
| HTTP and SOCKS proxy bypass | denied | Not run | SKIP |
| SSH Git, git://, alternate hosting and archive endpoints | denied | Not run | SKIP |
| Terminal access through scoped host-side broker | authenticated, workspace-only PTY | No terminal broker exists in the inspected source | SKIP |
| Docker socket, host network namespace and attach-network denial | denied | No pilot container to inspect | SKIP |

No direct-download command was issued against a production or Rack-host network. No host firewall, DNS, route, Docker network, NAT, MikroTik, SSH or VPN configuration was changed.

## Operational limits

The existing workspace controller records metadata and runtime observations; it does not create a no-network pilot container or terminal broker. A host SSH shell can bypass container-level egress isolation if a Researcher is able to access it. The current source does not establish whether Researcher SSH sessions terminate in a container or on the host.

## Verdict

Application approval, CLI installation and a Docker NetworkMode field do not prove egress enforcement. Direct egress remains unverified, and host shell bypass remains possible. Do not mark any existing workspace REPOSITORY_ENFORCED based only on source metadata.
