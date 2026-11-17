#!/usr/bin/env sh
# Automated cleanup is disabled. The previous helper could remove unrelated
# DOCKER-USER rules and could not prove that its host firewall chain was safe to delete.
set -eu
printf '%s\n' \
  'BLOCKED: no host firewall rollback was attempted.' \
  'This script cannot verify whether an earlier version changed the host. Inspect any existing LABOPS-PILOT-EGRESS rule only under an approved, backed-up operator change.' \
  'No network or service operation was performed.' >&2
exit 2
