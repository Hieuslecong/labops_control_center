# Final AIOTGIT Security Readiness

Snapshot: 2026-11-17
Verdict: NOT_READY
Host egress: HOST_ENFORCEMENT_NOT_VERIFIED
Host shell: HOST_SHELL_BYPASS_POSSIBLE

## Component status

| Component | Current evidence | Status |
|---|---|---|
| AIOTGIT CLI clone/status | Fake Broker, generated bundle, local Git and fail-closed tests pass | FIXED_NOT_RUNTIME_VERIFIED |
| Backend scoped authorization | HMAC capability and handler-level route/workspace checks pass | FIXED_NOT_RUNTIME_VERIFIED |
| Go Agent Repository Guard authorization | Service credential plus signed scoped capability; scoped resolver configured in source and unit-tested | FIXED_NOT_RUNTIME_VERIFIED |
| Approval/scanner state and artifact store | Source flow and unit tests exist; durable directory now must be explicit | FIXED_NOT_RUNTIME_VERIFIED |
| Approved Git bundle delivery | CLI verifies digest/commit and produces valid local Git metadata in unit tests | FIXED_NOT_RUNTIME_VERIFIED |
| Durable production artifact storage | No operator-provisioned path, backup or restore verification | NOT_FIXED |
| Workspace CLI installation/token provisioning | No workspace image/provisioner integration | NOT_FIXED |
| Quarantine worker isolation | Repository Guard is part of Agent process; separate restricted fetch worker not established | NOT_FIXED |
| Pilot terminal access | No identity-scoped terminal broker | NOT_FIXED |
| Workspace egress isolation | No pilot or bypass matrix runtime tests | BLOCKED_BY_IMMUTABLE_INFRASTRUCTURE_POLICY |
| Existing workspace migration | None performed | NOT_STARTED |
| Production services/runtime | Not exercised or deployed | NOT_VERIFIED |

## Evidence from this pass

- Go race tests passed for config, transport, AIOTGIT, Repository Guard and Docker packages.
- Go AIOTGIT, Agent and Gateway builds exited successfully; focused go vet exited successfully.
- Targeted Python AIOTGIT/workspace tests: 22 passed, one Pydantic deprecation warning.
- Frontend query tests: 3 passed; TypeScript noEmit exited successfully; Next.js Webpack build completed successfully.
- Repository Guard no longer defaults its state directory to /tmp. Without an explicit directory and authorization key, routes return service unavailable.
- No production repository write, deployment, Docker command, service operation, host network change, firewall operation, production data operation, GPU action, SSH/VPN change or reboot was performed.
- Host IP/routes/DNS/firewall and system runtime state were not measured.

## Acceptance levels

- Level 1 CLI Ready: not met; unit behavior passes, but actual workspace provisioning, Broker/Agent runtime and delivery E2E are absent.
- Level 2 Workspace Enforcement Ready: not met; no isolated pilot, terminal broker or direct-download bypass tests.
- Level 3 Controlled Canary Ready: not met; pilot, monitoring and runtime rollback verification do not exist.
- Level 4 Production Ready: not met.

## Required blockers

1. Provision a separate pilot workspace with a trusted runtime registry and scoped terminal broker.
2. Deploy/test a separate constrained fetch/scanner worker and verify scanner policy behavior.
3. Configure explicit durable Repository Guard storage, permissions, capacity, backup and restore.
4. Provision CLI, signed workspace token and Broker endpoint to the pilot through a trusted channel.
5. Execute approved/pending/rejected/tampered delivery E2E in the pilot.
6. Execute Git/HTTP/SSH/proxy/IPv4/IPv6 bypass tests inside the pilot.
7. Verify required package, model, dataset and experiment-tracking workflows through constrained services.
8. Only then prepare a separately approved controlled canary. Do not modify existing workspaces or host network policy in this remediation.
