# Deployment and rollback plan

**Current decision: REQUIRES_OPERATOR_APPROVAL — do not deploy this clone.** Current readiness is NOT_READY; this clone is engineering review material.

## Blockers before a controlled canary

1. Integrate the trusted workspace registry into the actual Agent provisioning/launch lifecycle and verify immutable workspace/container/owner/node bindings with a dedicated test daemon.
2. Implement and test delegated human/action authorization before restoring privileged Gateway operations.
3. Integrate Repository Guard actor/workspace authorization and bind approved commit, scanner result and artifact digest; public delivery currently returns unavailable.
4. Implement verified backup receipt, retention, running-job quiescence, archive lifecycle and trusted cleanup before enabling permanent member deletion.
5. Prepare an operator-reviewed Gateway allowlist bootstrap/migration for current Rack and Workstation identities. Verify certificate fingerprints, recovery and rollback before rollout.
6. Add or integrate a real PostgreSQL persistence layer, then test migrations, transactions, audit and recovery against a separate test database.
7. Run the missing browser/API and end-to-end repository workflow tests against isolated Backend/Gateway/Agent services.
8. Test repository direct-egress bypass in an isolated network namespace/container. Host firewall changes require a separate change request and approval.
9. Complete independent correctness, security and operational-safety review of the full WIP diff.

## Controlled canary sequence (future, after blockers close)

1. Operator inventories the exact release, service configuration paths, current node certificates/allowlist, database schema and service dependencies without changing network/SSH/VPN configuration.
2. Build immutable artifacts in CI or isolated development and record hashes/source revision. Do not run install/deploy scripts as part of this Rack remediation.
3. Back up only affected application/PKI/database state using operator-owned procedures; verify restore in staging. Do not copy private keys or production .env into the clone.
4. Review migration compatibility and rollback against existing data. Do not apply a migration that can lose or reinterpret user/workspace/audit data.
5. Obtain separate maintenance approval for any production service operation. Preserve all immutable infrastructure rules and GPU/user workloads.
6. Deploy one service at a time in an operator-controlled canary. Validate authenticated health, API contracts, node identity, telemetry freshness and audit. Stop progression on ambiguity.
7. Expand only after canary and rollback rehearsal pass. Gateway rollout requires explicit reviewed allowlist; never auto-trust observed nodes.

## Rollback

- Keep the prior verified application artifact/configuration and tested restore procedure for each affected state store.
- Roll back only the affected service after verifying no incompatible persistent-state downgrade. Do not restart unrelated services.
- For PKI, do not restore stale revocation state blindly. Review the latest revocations and node allowlist before restore.
- For data changes, restore from a verified pre-change backup and reconcile audit records; never delete data to make rollback appear successful.
- Any health failure or unexpected node identity halts rollout and uses the operator-approved recovery path.

## Immutable infrastructure guardrail

This plan authorizes no production action. No IP, NetworkManager/interface, subnet, DNS, gateway, route, firewall/NAT, MikroTik, WireGuard/wg-easy runtime, SSH configuration/port, reboot or shutdown change is allowed. Do not use Docker/install hooks as an indirect host-network change. Any required action that conflicts with these restrictions remains blocked.
