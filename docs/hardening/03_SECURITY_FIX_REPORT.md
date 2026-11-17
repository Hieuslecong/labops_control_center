# Security remediation report

**Snapshot:** 2026-11-17
**Repository:** /home/aiot-1/labops-v2-hardening
**Production repository:** no write, deployment, service operation or credential transfer occurred.

## Remediations evidenced in source and tests

- **Gateway PKI and sessions:** missing/empty authorization state fails closed; bootstrap is explicit and cannot overwrite persisted state. Revocation writes use a temporary file, file sync, rename and directory sync; corrupt/interrupted state and I/O failure deny nodes. Certificate SAN identity is bound to NodeHello; reconnect and duplicate-session checks are covered. Go unit/fault-injection/race tests and isolated loopback TLS/gRPC integration tests PASS.
- **Gateway control API:** a configured service secret of at least 32 bytes is required; privileged HTTP API rejects non-loopback binds. A service secret is not treated as a human Admin identity. Only host/GPU/telemetry read-only actions are dispatchable; mutating actions remain denied.
- **Docker ownership:** names and prefixes are not proof. Stop requires a trusted registry record, canonical container ID, active workspace, owner, local node and matching runtime labels/name; registry is rechecked before stop and state verified afterward. Fake Docker/registry tests PASS. No host Docker socket was accessed.
- **Repository Guard / AIOTGIT:** signed user/workspace capabilities are rechecked by Backend and Agent; the Agent is configured with a scoped actor resolver. Approved Git bundle delivery checks request owner/workspace, artifact digest and commit; CLI publishes a verified Git working tree. Fake Broker and route tests pass, but live API-to-Agent delivery E2E was not run.
- **Member lifecycle:** suspension preserves data and marks SSH/VPN/Teleport revocation pending. Dry-run inventories owned resources; permanent cleanup is fail-closed without backup receipt, retention, job-quiescence proof and a trusted cleanup executor.
- **wg-easy:** no default credentials; missing credentials fail closed; configured CIDR/Endpoint are not rewritten; malformed/time-out/unverified provider results surface as errors. Provider and ownership tests use mocks.
- **Test safety:** default test collection gates host-facing tests before application imports. Python state is directed to temporary paths; Docker uses fake HTTP; VPN uses mocks. Full Python and Go suites pass with documented host-facing skips.
- **Application P1 fixes:** principal and Clerk subject binding, active-member checks, CSRF Origin checks, process ownership/ref safeguards, telemetry freshness/GPU UUID mapping, scheduler locking, workspace pending verification and atomic JSON handling have isolated tests. The closure matrix records remaining gaps.
- **Frontend repository panels:** shared typed TanStack query now provides polling, response validation and visible error states; login fields have associated labels. Bun query tests, typecheck, lint and Next build pass.

## Residual security risks and blockers

1. **Delegated Gateway authorization is not implemented.** Service authentication does not establish the initiating human or their scope. Privileged Gateway/Agent operations remain unavailable.
2. **Docker runtime is not integrated with provisioning.** Agent can attest a container and Backend has registry routes, but normal provisioning does not call the attestation flow. No dedicated test daemon was available or validated; no Docker command/socket was used.
3. **Runtime artifact delivery remains unverified.** The AIOTGIT bundle route and local CLI publication path exist in source, but no live Backend-to-Agent request, scanner approval, durable artifact store or pilot workspace delivery was exercised.
4. **Permanent member deletion is disabled.** Backup verification, retention, runtime job quiescence, archive lifecycle and trusted cleanup are missing.
5. **Gateway allowlist rollout is blocked.** Operators must inventory current Rack/Workstation certificates and prepare an explicit bootstrap/migration with recovery before deployment.
6. **Authentication remains partly unverified.** Login rate limiting, broad session invalidation, deployed Clerk identity mapping and live browser/API auth were not tested.
7. **PostgreSQL is not integrated or verified.** SQLAlchemy/asyncpg are declared, but a backend engine/session integration was not found in the reviewed application paths; no test server or migration was run.
8. **Repository egress enforcement is not proven.** No network namespace bypass test or host firewall change was performed. Host enforcement remains unverified.
9. **Runtime remains unverified.** No production Docker, DB, Gateway, Agent, VPN, SSH, process-control or GPU workload was accessed.

## Security review result

Three review passes found fail-closed behavior in sensitive paths and regression coverage for isolated logic. The isolated PKI/Gateway loopback tests prove test-certificate behavior only. This is not a security certification. Overall readiness is **NOT_READY**.
