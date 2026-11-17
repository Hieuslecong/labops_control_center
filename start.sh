#!/usr/bin/env bash
# Proxy to start_all.sh
exec "$(dirname "$0")/start_all.sh" "$@"
