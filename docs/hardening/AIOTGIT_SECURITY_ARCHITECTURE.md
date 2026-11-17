# AIOTGIT Security Architecture — Current Clone

Snapshot: 2026-11-17

## Intended trust flow

Researcher terminal → AIOTGIT CLI → authenticated FastAPI Repository API → Go Agent Repository Guard → request/scanner/artifact state → approved Git bundle → AIOTGIT CLI verifies and publishes a local Git repository.

The CLI exposes only clone and status. Local development commands continue to use the ordinary Git binary. AIOTGIT does not forward arbitrary Git commands and never falls back to a direct remote clone when the Broker is unavailable.

The legacy labops binary no longer exposes repository submission, listing or delivery. Its clone/repo commands fail closed and do not send requests using a machine credential.

## Identity and authorization in source

- Browser authentication is resolved by the Backend before a short-lived signed capability is issued for a verified workspace.
- Capability claims bind subject, role, member, workspace, node, scopes, audience, expiry and token ID.
- The CLI sends the capability to the Backend. The Backend validates it and rechecks that the workspace remains active and owned by the subject.
- Backend-to-Agent requests carry both the configured Agent service credential and the original signed capability.
- The Agent middleware verifies both credentials. Its Repository Guard handler uses a scoped-token resolver and checks action, role, node, workspace/resource and request ownership.
- Admin decisions are distinct from Researcher request/read/bundle scopes.
- Agent configuration now requires an explicit Repository Guard state directory; it does not silently default approval and artifact state to /tmp. If durable storage or the signing key is absent, Repository Guard routes return service unavailable.
- No Admin token is placed in a workspace. The browser token issuance endpoint returns a short-lived workspace capability to the authenticated Researcher; the frontend keeps it only in page memory.

These controls have unit and handler-level evidence. No live Backend-to-Agent request was made during this pass.

## Repository and artifact integrity

The Go service accepts validated HTTPS GitHub/GitLab repository URLs, resolves a commit, fetches into quarantine, scans without running repository hooks/build scripts, records approval state, stores content-addressed artifacts and Git bundles, and serves bundles only for approved requests to authorized actors.

AIOTGIT checks the returned bundle digest and approved commit, imports through a trusted system Git binary with hooks disabled, removes the temporary bundle remote, confirms HEAD and a clean working tree, then atomically publishes into a workspace-relative destination without replacing an existing destination. Local Git status/log/branch/add/commit behavior is covered with a generated test bundle.

The Go Repository Guard currently runs in the Agent process. A separate constrained quarantine Fetch Worker with independently proven CPU/RAM/PID/storage limits, no Docker socket and no production secrets has not been deployed or runtime-tested. Durable data directory ownership and backup policy must be selected by the operator.

## Workspace boundary

Backend and Docker registry code distinguish repository policy mode, observed Docker network mode and egress verification. A workspace marked REPOSITORY_ENFORCED is rejected unless runtime attestation reports network mode none and VERIFIED_NO_NETWORK. This is an attestation contract, not a provisioner or network enforcement mechanism.

No pilot container, no-network workspace, authenticated terminal broker, or workspace CLI installation was created. Existing SSH/legacy workspaces were not changed. A no-network container also removes ordinary TCP access, so a tested identity-scoped terminal broker is a prerequisite for a usable pilot.

## What this architecture does not claim

- No proof that existing workspaces have no direct Internet or host-shell access.
- No proof that every direct Git, HTTP, SSH, IPv4/IPv6 or proxy path is blocked.
- No production scanner completeness claim.
- No production artifact-store durability or backup/restore claim.
- No production PKI bootstrap or actual node fingerprint inventory.
- No guarantee against code supplied by a user through an already permitted data/import channel.

## Readiness

CLI behavior is unit-tested with fake Broker responses and generated local Git bundles. Broker/API/Agent/durable-storage runtime integration and workspace egress isolation remain unverified. Overall readiness is NOT_READY.
