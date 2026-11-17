# AIOTGIT CLI Design — Current Source Behavior

Snapshot: 2026-11-17

## Supported commands

AIOTGIT exposes only:

- aiotgit clone [-b BRANCH | --ref REF] URL [DESTINATION]
- aiotgit status [REQUEST_ID]

The ordinary Git executable remains available for local operations such as status, log, branch, add and commit. AIOTGIT has no generic Git passthrough and does not fall back to GitHub when its Broker is unavailable.

The legacy labops clone and labops repo commands are disabled in the current Go source. They return a nonzero status and direct the user to AIOTGIT. Regression tests cover this denial.

## Clone behavior

1. Read a short-lived workspace-scoped token from a user-owned private regular file.
2. Validate the workspace-relative destination and reject symlink path components.
3. Submit the repository/ref and idempotency key to the configured Broker API.
4. Poll only the request bound to the signed workspace capability.
5. Do not create a destination while pending, rejected, failed or unavailable.
6. Retrieve an approved Git bundle only after an approved state.
7. Verify bundle digest and pinned commit.
8. Import with system Git while disabling hooks and ambient Git configuration.
9. Verify HEAD and clean working tree, remove the temporary bundle remote, and atomically publish without replacing an existing destination.

Unit tests use a fake Broker and generated temporary Git bundles. They verify local Git metadata and local commit operations. No live approval-to-delivery integration was run.

## Status behavior

Without an ID, the CLI requests the newest workspace-scoped request. With an ID, it validates the request ID and asks the Broker for only that request. Authorization is server-side; caller-supplied owner IDs are not used.

## Provisioning is not complete

No actual workspace image or provisioner installs the CLI, Broker URL or short-lived token. There is no tested terminal broker or no-network pilot. The command examples describe the source interface, not a currently available production workspace feature.

Overall status: unit-tested, NOT_READY for runtime use.
