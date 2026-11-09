#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'NOTICE'
Disabled legacy production script.

The previous version mixed WireGuard removal with Linux password locking,
authorized_keys moves, and process termination while ignoring command errors.
It cannot prove Teleport sessions ended. Use the authenticated LabOps revoke
workflow after WireGuard Gateway and Teleport are configured; inspect any
PARTIAL_FAILURE result before considering access revoked.
NOTICE
exit 2
