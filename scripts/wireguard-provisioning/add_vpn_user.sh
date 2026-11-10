#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'NOTICE'
Disabled legacy production script.

The previous version generated and stored client private keys on this server,
created Linux accounts, and added peers without verified firewall policy.
Use the authenticated LabOps device-enrollment workflow after its prerequisites
are verified. Generate the WireGuard keypair on the user's device and submit
only its public key.
NOTICE
exit 2
