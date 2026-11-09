#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'NOTICE'
Disabled legacy production script.

The previous version replaced WireGuard configuration, stopped wg-quick@wg0,
and wrote client private keys to server-side files. Do not run it.

Secure Access V2 requires an approved Gateway, verified firewall policy, and a
rollback plan. This repository does not have access to those production systems.
NOTICE
exit 2
