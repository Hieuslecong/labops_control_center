# Gateway delegated authorization report

**Status:** PARTIALLY_FIXED. Privileged actions remain disabled.

## Implemented boundary

- Gateway control API requires a configured strong service secret and a loopback bind.
- Caller-supplied actor IDs are not trusted as human identity.
- Gateway and Agent action allowlists permit only INSPECT_HOST, INSPECT_GPUS and COLLECT_TELEMETRY.
- Arbitrary commands and mutating actions fail closed.

## Tests

- Go unit tests cover missing/invalid control authentication, read-only action allowlisting and rejection of terminate/stop/run-shell actions.
- Full Go and race suites pass.
- Isolated Gateway mTLS test verifies a test node session; it does not prove human authorization.

## Missing contract

No signed delegation token currently binds verified actor, action, node, resource, request ID/idempotency, audience and expiration from Backend through Gateway to Agent. A shared service credential is not a substitute. No service token is treated as human Admin.

**Operational consequence:** stop workspace, process termination, SSH synchronization and other privileged dispatch remain unavailable. Do not restore them until delegation is implemented and adversarial tests pass. Production Gateway was not changed.
