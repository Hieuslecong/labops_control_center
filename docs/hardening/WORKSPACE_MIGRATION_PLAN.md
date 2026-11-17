# Workspace Migration and Pilot Plan

Snapshot: 2026-11-17
Current state: no production workspace migration or pilot provisioning performed.

## Workspace classes

1. Existing production/legacy workspace: preserve current container, SSH, volumes, mounts, network, GPU jobs and data. Do not recreate or mutate it in this remediation.
2. Pilot candidate: a newly created disposable workspace with a distinct owner, workspace ID, node ID, storage root and audit trail.
3. Enforced workspace: only after pilot acceptance, fresh runtime attestation and verification of the Broker/terminal/egress boundary.
4. Legacy host-shell workspace: label enforcement as unverified; a container policy cannot control a Researcher running commands in the host namespace.

## Preconditions for a pilot

- Implement a trusted workspace provisioner that creates the runtime and registry record together.
- Use a non-privileged container, isolated user identity, no Docker socket, no host network namespace, no CAP_NET_ADMIN and no user network attach permission.
- Provide a scoped terminal broker or PTY attach service. It must authenticate the user and workspace, enforce process/session ownership, deny host shell, audit connect/disconnect, and reject cross-user attach.
- Make AIOTGIT binary, Broker endpoint and short-lived token provisioning an authenticated, non-writable configuration path.
- Provision explicit durable Repository Guard storage with owner/mode, capacity limits, backup, restore test and retention.
- Separate quarantine fetch/scanning from the privileged Agent process; no repository hooks/scripts, host secrets or Docker socket in the worker.
- Define package, model, dataset, pip/conda, apt and experiment-tracking access through constrained mirrors/brokers or document which workflows are unavailable.
- Validate API/token scopes, artifact digest, approved commit and workspace destination against a test Backend and Agent.

## Pilot acceptance sequence

1. Record the test-only resource IDs and canonical paths; add ownership markers.
2. Create a new disposable workspace; leave existing workspace configuration untouched.
3. Inspect mounts, capabilities, user namespace, network namespace, runtime ID, node ID and cgroup limits through the test controller.
4. Verify the terminal broker workflow, identity, working directory, process ownership, concurrent sessions, logout and cross-user denial.
5. Verify Broker IPC/API authentication and that no secrets, host paths or Docker socket are exposed.
6. Run AIOTGIT pending, reject, approve, scanner-failure, digest-tamper and service-unavailable scenarios.
7. Verify a delivered repository has the approved HEAD and supports local status/log/branch/add/commit.
8. Attempt Git, curl, wget, Python, Node, API, proxy, SOCKS, SSH, git protocol, alternate hosts, IPv4 and IPv6 direct downloads from the pilot.
9. Verify package/model/data workflows against their approved brokers without adding unrestricted proxy access.
10. Verify GPU assignment, shared dataset access, quota and storage. Do not use or stop another user's GPU job.
11. Test rollback only against the owned pilot resources and verify identity before every cleanup.
12. Only after evidence and operator review, consider a controlled canary for additional new workspaces.

## Rollback

- Disable assignment of new pilot workspaces.
- Revoke the pilot capability and mark external access as pending until actually revoked.
- Preserve request, approval, audit, bundle and workspace state for forensic review.
- Restore only the pilot's saved workspace configuration and artifacts.
- Remove only test-created resources after verifying their recorded immutable IDs and ownership markers.
- Do not change host firewall, IP, routing, DNS, Docker daemon, SSH, WireGuard or existing workspace configuration as part of rollback.
- Do not migrate or recreate an existing workspace as an automatic rollback action.

No production approval is implied by this plan.
