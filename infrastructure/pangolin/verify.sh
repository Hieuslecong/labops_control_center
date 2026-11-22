#!/usr/bin/env bash
set -euo pipefail
curl --silent --show-error --fail --max-time 5 -o /dev/null -w 'Next.js local HTTP %{http_code}\n' http://127.0.0.1:3000/
curl --silent --show-error --fail --max-time 5 -o /dev/null -w 'FastAPI health HTTP %{http_code}\n' http://127.0.0.1:8000/healthz
if [[ $# == 1 ]]; then
    [[ $1 =~ ^[a-zA-Z0-9.-]+$ ]]
    getent ahosts "$1"
    curl --silent --show-error --max-time 15 --proto '=https' -o /dev/null \
        -w 'Unauthenticated HTTPS status %{http_code}\n' "https://$1/"
    echo 'Verify Pangolin login, authorization and response content in a browser.'
fi
