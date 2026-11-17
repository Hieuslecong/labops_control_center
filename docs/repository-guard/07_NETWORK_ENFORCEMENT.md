# Workspace Egress Enforcement — Current Status

Snapshot: 2026-11-17
Result: HOST_ENFORCEMENT_NOT_VERIFIED

## Supersedes the earlier pilot proposal

The older proposal to append Docker-specific iptables rules was not applied. The pilot and rollback scripts in the current clone are fail-closed no-op guards and exit with an error. They do not change host firewall, Docker, routing, DNS or network state. No script was executed.

The previous claim that a one-command rollback guarantees restoration is withdrawn. No rollback can be claimed without a recorded prior state and verification.

## Evidence

- Source attestation rejects a workspace marked REPOSITORY_ENFORCED unless observed Docker NetworkMode is none and the registry records VERIFIED_NO_NETWORK.
- Fake registry/runtime tests exercise bridge rejection and none-mode attestation.
- Static egress script safety test passes.
- No pilot container, Docker network, network namespace or host rule was created.
- No direct Git, HTTP, proxy, IPv4/IPv6 or DNS bypass was tested from a workspace.
- Current source has no authenticated terminal broker. The actual Researcher SSH termination point is unknown.

## Required next step

Use a newly provisioned disposable pilot, not an existing workspace. First implement and test workspace provisioning, identity-scoped terminal attach, durable artifact storage, Broker channel, mount/capability restrictions and package/data access. Then test direct downloads inside the pilot. Host firewall changes require a separate operator-approved change request with exact rules, scope, outside-band access and verified rollback.
