# Gateway PKI bootstrap verification

**Test result:** isolated PKI persistence and loopback mTLS PASS.
**Production bootstrap:** NOT RUN; operator migration required.

## Implemented and tested

- Empty or missing allowlist denies nodes.
- Bootstrap is explicit, nonempty and does not overwrite an existing state file.
- Allow/revoke state persists through temporary write, file sync, atomic rename and directory sync.
- I/O and corrupted-state failures fail closed.
- Certificate node identity must match the presented node identity.
- Revoked nodes cannot reconnect through the tested authorization path; duplicate/stale session protections are covered.
- Fault-injection tests cover write/sync/rename and concurrent revocation updates.
- Test CA and certificates are generated under temporary directories.
- Isolated tests passed:
  - TestPKIWorkflowAndTLSHandshake
  - TestGRPCGatewayMTLSTunnel

## Production migration still required

Before any Gateway rollout, an operator must inventory the current node certificate fingerprints and IDs, verify that the intended Rack and Workstation identities are valid, prepare an explicit allowlist, back up current PKI state, and rehearse recovery/rollback. This task did not read or write production certificates, keys, allowlist state or Gateway runtime.

No production restart, enrollment, re-issuance or allowlist change was performed.
