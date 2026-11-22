#!/usr/bin/env bash
# ==============================================================================
# External Heartbeat Monitor for LabOps AI SRE Control Plane
# IMPORTANT: Run this script from an EXTERNAL host/VPS/Uptime-Kuma/cron outside
# aiot-ws1 so power/network outage on the workstation itself is detected reliably.
# ==============================================================================
set -euo pipefail

HEARTBEAT_URL="${LABOPS_HEARTBEAT_URL:-https://aiotserver.fitdnu.id.vn/api/labops/ai-assistant/heartbeat}"
WEBHOOK_ALERT_URL="${EXTERNAL_ALERT_WEBHOOK_URL:-}"
TIMEOUT_SEC="${CHECK_TIMEOUT_SECONDS:-8}"

if resp=$(curl -fsS --max-time "$TIMEOUT_SEC" "$HEARTBEAT_URL" 2>/dev/null); then
  if echo "$resp" | grep -q '"is_polling":true'; then
    echo "OK: LabOps AI SRE Control Plane & Worker are polling."
    exit 0
  fi
  msg="CRITICAL: LabOps SRE Worker stopped polling on control plane ($HEARTBEAT_URL)"
else
  msg="CRITICAL: LabOps Control Plane unreachable from external watchdog ($HEARTBEAT_URL)"
fi

echo "$msg" >&2
if [ -n "$WEBHOOK_ALERT_URL" ]; then
  curl -fsS -X POST -H "Content-Type: application/json" \
    -d "{\"text\":\"$msg\",\"severity\":\"P0\"}" \
    "$WEBHOOK_ALERT_URL" >/dev/null || true
fi
exit 2
