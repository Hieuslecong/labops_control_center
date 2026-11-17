# Repository Guard and AIOTGIT Report

Snapshot: 2026-11-17
Status: PARTIALLY_FIXED; runtime integration and workspace enforcement are not verified.

## Implemented in source

- AIOTGIT CLI supports only clone and status and has no generic Git passthrough or direct-clone fallback.
- Legacy labops repository commands are disabled; that machine-token CLI has no repository submission, listing or delivery path.
- Clone requests include a short-lived signed workspace capability, workspace ID and idempotency key.
- Backend verifies token scope and rechecks active workspace ownership/runtime identity.
- Backend forwards both Agent service authentication and the signed actor capability.
- Go Agent verifies both credentials and configures the Repository Guard scoped-token resolver for action, role, node, workspace/resource and owner checks.
- Requests require a registered owned workspace. The CLI accepts only workspace-relative destinations, stages through a directory handle, verifies bundle SHA-256 and approved commit, disables Git hooks, removes temporary origin, checks clean Git status, and publishes without replacement.
- Approved artifact API checks request ownership/workspace and Guard approval metadata.
- Repository Guard state no longer defaults to /tmp. An explicit durable directory and scoped authorization key are required; missing configuration yields service unavailable.
- Egress pilot scripts are fail-closed no-op guards. No firewall operation was run.

## Verification evidence

- Go race: legacy CLI, AIOTGIT CLI, config, transport, Repository Guard and Docker package suites PASS.
- Go AIOTGIT/Agent/Gateway builds PASS; focused go vet PASS.
- Python targeted AIOTGIT/workspace tests: 22 passed, 1 Pydantic deprecation warning.
- CLI generated-bundle tests verify approved Git HEAD, clean working tree, local add/commit, pending/unavailable fail-closed behavior, digest mismatch denial and destination traversal/symlink rejection.
- Frontend query tests: 3 passed; TypeScript typecheck and Next.js Webpack build PASS.
- No live Backend/Agent service, scanner-to-artifact end-to-end flow, PostgreSQL, Docker daemon, real workspace or GitHub fetch was exercised.

## Residual risks and blockers

- CLI binary and token/config are not provisioned into an actual workspace.
- Repository Guard runs in the Agent process; a separate resource-limited quarantine Fetch Worker is not established.
- No persistent store path, backup or restore verification is configured for production.
- No terminal broker or no-network pilot exists.
- Direct Git/HTTP/SSH/proxy/IPv4/IPv6 bypass behavior is not tested. Legacy host-shell access may bypass container policy.
- No existing workspace migration or production deployment was performed.

Result: CLI helper behavior has unit evidence. Level 1 CLI Ready and workspace enforcement acceptance are not met. Overall NOT_READY.
