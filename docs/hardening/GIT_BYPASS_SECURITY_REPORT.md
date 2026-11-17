# Git Bypass Security Report

Snapshot: 2026-11-17
Verdict: direct-download bypass remains possible outside a verified isolated workspace.

## Scope tested

The source-level CLI/Broker path was reviewed and unit-tested. No direct outbound bypass attempt was made from a Rack production shell or existing member workspace. No isolated pilot network existed for adversarial runtime tests.

| Vector | Intended mitigation | Evidence | Status |
|---|---|---|---|
| AIOTGIT Broker unavailable then fallback to git clone | CLI exits with error and does not create a destination | Fake transport pending/unavailable tests | PASS |
| Pending or rejected request | Do not materialize a repository | Fake Broker CLI tests | PASS |
| Wrong workspace/user capability | Backend and Agent scope and ownership checks | Token/route/handler tests | PASS at unit/handler level |
| Bundle replacement or tampering | SHA-256, commit and clean-tree verification | Generated test bundle tests | PASS |
| Path traversal and symlink parent | Workspace-relative validation and fd-rooted staging | Temporary filesystem tests | PASS |
| git clone directly from GitHub | Workspace network boundary | No isolated pilot; no egress test | NOT TESTED |
| git fetch/pull, SSH Git, git protocol, helpers, submodules, LFS, bundle URI | Controlled remote/mirror plus egress policy | No pilot remote or egress boundary | NOT TESTED |
| curl/wget/Python/Node/API/archive downloads | Egress boundary | No isolated egress tests | NOT TESTED |
| IPv4/IPv6/proxy/SOCKS/alternate host | Egress boundary | No network namespace/pilot tests | NOT TESTED |
| Host SSH shell, Docker API, attach-network privilege | Execution boundary and no Docker socket | Topology not proven; no pilot | HOST_SHELL_BYPASS_POSSIBLE |

## Preconditions and impact

Any Researcher with direct host-shell networking, unrestricted workspace egress, a user-controlled proxy, or permission to attach a network can fetch source outside AIOTGIT. The current implementation does not claim to detect code copied or imported through already permitted channels.

## Required controls before enforcement claims

- Provision a new pilot only, with network_mode none or an independently audited equivalent.
- Provide an authenticated, workspace-scoped terminal broker that does not grant host shell or Docker API access.
- Mount only a narrow authenticated Broker IPC/API channel and an immutable read-only approved artifact interface.
- Verify container capabilities, mounts, namespaces, user identity and resource limits.
- Test direct Git and alternate downloader paths from the pilot.
- Keep existing SSH workspaces unchanged until pilot workflows, GPU, storage, package/model/data access and rollback pass.
- For flexible Internet access, design a controlled egress service with repository policy; do not expose a general-purpose unrestricted proxy.

Host firewall application is outside the approved source/test work and requires a separate canary change request and operator approval.
