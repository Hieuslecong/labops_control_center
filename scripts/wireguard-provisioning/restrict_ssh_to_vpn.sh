#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'NOTICE'
Disabled legacy production script.

The previous version changed sshd policy and enabled password authentication.
Do not apply it. Keep the existing management SSH path until a canary over the
verified Teleport route and an administrator rollback path have both passed.
NOTICE
exit 2
