# Docker and workspace integration report

**Status:** PARTIALLY_FIXED. No Docker runtime integration test was run.

## Code path reviewed

- Go controller verifies a trusted registry record, canonical immutable container ID, workspace ID, owner ID, node ID, runtime labels and expected name.
- Stop performs registry/identity checks immediately before the Docker API call and verifies stopped state afterward. Failures are not reported as success.
- Go registry client uses the configured Backend API URL and machine credential; missing configuration fails closed.
- Agent can inspect a container and report a runtime observation to the authenticated Backend registry route.
- Backend accepts observations only from an authenticated node principal and validates workspace/node/container/labels/resources before marking runtime verified.

## Test evidence

- Full Go suite and full Go race suite pass.
- Fake HTTP Docker tests cover registered container, unregistered fake ws name, owner/node mismatch, label spoofing, ID mismatch/replacement, Docker error, stop error and failed post-stop verification.
- Backend workspace registry tests pass in the full Python suite.
- No Docker command or socket was used. The Docker CLI exists, but no dedicated dockerd/rootless daemon was started or validated. Production Docker API was not queried.

## Remaining integration gaps

1. Normal workspace provision/launch does not invoke Agent AttestWorkspace and persist the trusted runtime record.
2. No dedicated container was created for this task.
3. No real Docker inspect/stop/replacement race was exercised.
4. Resource quota, GPU UUID assignment and storage enforcement remain unverified end-to-end.
5. Do not use production container names or the host Docker context to close these gaps.

**Result:** fake-client correctness PASS; Docker runtime integration SKIP; workspace lifecycle remains fail-closed until registry provisioning is wired.
