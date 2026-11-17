#!/usr/bin/env sh
# Host firewall enforcement is deliberately disabled in this repository.
# The previous implementation edited DOCKER-USER/iptables on the host and
# used an incomplete GitHub IPv4 blocklist. It is not a workspace security boundary.
set -eu
printf '%s\n' \
  'BLOCKED: this helper does not change host firewall, NAT, routing, or Docker state.' \
  'Repository enforcement requires an isolated workspace with a trusted runtime attestation (Docker NetworkMode=none) and a separately tested terminal/Broker path.' \
  'No network or service operation was performed.' >&2
exit 2
