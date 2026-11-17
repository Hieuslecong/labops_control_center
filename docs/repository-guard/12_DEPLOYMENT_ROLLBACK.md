# Repository Guard Deployment and Rollback Plan

Snapshot: 2026-11-17
Status: planning only. No production deployment, service restart, firewall operation or data cleanup was performed.

## Current gate

Do not deploy the current clone as a workspace enforcement solution. AIOTGIT unit behavior is tested, but workspace provisioning, terminal access, persistent artifact storage, scanner isolation, live service integration and direct egress enforcement are not verified.

A future deployment requires a separate operator change request and review. The seven immutable Rack/Workstation network, firewall, SSH/VPN and reboot restrictions remain in force.

## Preflight for a future pilot

- Use a new disposable workspace with a recorded owner, workspace ID, node ID, runtime ID and canonical storage paths.
- Leave existing workspaces, volumes and GPU jobs unchanged.
- Provision LABOPS_REPO_GUARD_DATA_DIR on explicitly selected persistent storage with restrictive ownership, capacity, backup and a verified restore procedure.
- Configure matching Backend/Agent signing keys through the approved secret-management process; never place Admin or service credentials in a Researcher workspace.
- Verify the pilot image, user namespace, capabilities, mounts, cgroups, network mode and absence of Docker socket/host network access.
- Implement and test the identity-scoped terminal broker before using network_mode none.
- Verify package, model, dataset and experiment-tracking access through approved constrained services.
- Run Go/Python/Frontend regression suites and live E2E only against test instances and the disposable pilot.
- Prepare service health checks, observability and an exact rollback artifact/config for the pilot.

## Rollout order

1. Test Backend and Repository Guard with a test database and test signing keys.
2. Verify the explicit persistent artifact directory and restoration in the test environment.
3. Verify signed actor scopes, approval, bundle digest, commit and audit end-to-end.
4. Provision one disposable no-network pilot and its scoped terminal channel.
5. Test local Git, AIOTGIT, package/data workflows and direct-download bypasses.
6. Review evidence and residual risks before any separately approved canary.

## Rollback

- Stop assigning new test/pilot workspaces.
- Preserve request, approval, audit and artifact state for review.
- Revert only the pilot-specific application image/configuration to its recorded prior version.
- Keep existing user workspaces and research data untouched.
- Remove only resources created by the test after verifying immutable IDs, ownership markers, canonical paths and backup status.
- Do not use broad process-name kills, wildcard file deletion, host firewall rollback scripts, or Git reset/checkout against a production repository.
- If network policy was ever applied under a separately approved change, use only its reviewed exact rollback and verify the resulting state; current scripts are no-op guards and do not perform rollback.

## Operator approval requirements

Any production service change, data cleanup, migration, Docker runtime/network operation or host egress rule requires its own impact review, backup/rollback evidence and operator approval. This document is not authorization to perform those actions.
