# LabOps Secure Access V2 — Design Notes

## Intent

Replace simulated SSH/WireGuard provisioning with a truthful, fail-closed access workflow using per-device WireGuard peers and Teleport Community for SSH identity, MFA, certificates, workspace RBAC, and audit.

## Approaches

1. **WireGuard plus Teleport Community (selected by the user):** VPN clients reach a private Teleport Proxy; workspace agents join through reverse tunnels. This supplies centralized identity, certificates, RBAC, and audit.
2. **WireGuard plus direct OpenSSH:** smaller deployment, but it does not provide the requested Teleport identity, certificate lifecycle, or centralized session audit.
3. **Teleport Cloud:** provides a managed control plane, but conflicts with the request to use Community Edition.

## Selected approach

The user selected a self-hosted Teleport Community control plane reachable only over WireGuard, with SSH resources joining through Teleport reverse tunnels. Direct public SSH per workspace and Teleport Cloud are outside this design.

## Goals

- Never generate, store, log, or return client private keys from the backend.
- Keep each device peer and workspace assignment unique and revocable.
- Report `PENDING`, `ACTIVE`, and `NOT_CONFIGURED` only from observed service state; never infer success from persisted records or fallback data.
- Use Teleport local users with required WebAuthn MFA, workspace-scoped roles, short-lived certificates, and auditable sessions.
- Preserve existing members, workspaces, storage, GPU allocations, running jobs, and administrator SSH during migration.
- Keep UI state sourced from backend/service observations and mark unavailable infrastructure `NOT VERIFIED`.

## Non-goals

- Replacing the existing data store or resource-allocation logic without a separate migration.
- Opening public SSH or broad LAN access.
- Deploying to physical hosts or changing router/firewall state from this sandbox.

## Constraints and current blockers

- Existing repo changes are uncommitted and must be preserved.
- The current backend/UI contain hardcoded WireGuard endpoints/keys, plaintext client configs, synthetic success paths, and no Teleport integration.
- This execution environment cannot use host root/systemd/netlink privileges or access the MikroTik management plane.
- Production WebAuthn and Teleport access require a real cluster name/TLS certificate and reachable proxy; infrastructure behavior must remain `NOT VERIFIED` until those are supplied and tested.

## Required behavior

- WireGuard enrollment accepts a client public key only; the client owns its private key.
- Missing server configuration, command failures, duplicate peers/IPs, or missing gateway state fail closed.
- Teleport resources are labelled per workspace; researcher access cannot include host-admin resources or other workspaces.
- Teleport local authentication requires WebAuthn; researcher/admin certificate TTLs are 12h/2h, with researcher `require_session_mfa: false`, admin `true`, and agent forwarding disabled.
- User creation, activation, suspension, and revocation remain separate observed states; revoke never deletes research data.

## Infrastructure requirements outside this code change

- An authorized WireGuard gateway and UDP ingress rule.
- A Teleport Community cluster on a valid TLS hostname, with its proxy restricted to VPN clients and auth service private.
- Teleport SSH agents installed in the actual workspace/container boundary on both servers, joining through reverse tunnels.
- A production-safe admin identity for LabOps-to-Teleport provisioning.

