#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 2 || $EUID != 0 ]]; then
    echo 'Usage: sudo deploy-site.sh VERIFIED_NEWT_BINARY EXPECTED_SHA256' >&2
    exit 2
fi
[[ $2 =~ ^[a-fA-F0-9]{64}$ ]]
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
actual=$(sha256sum < "$1")
[[ ${actual%% *} == "${2,,}" ]] || { echo 'Newt checksum mismatch' >&2; exit 1; }
for destination in /opt/labops-pangolin/newt /etc/systemd/system/labops-newt.service; do
    [[ ! -e $destination && ! -L $destination ]] || { echo "Already exists: $destination" >&2; exit 1; }
done
install -d -m 0755 /opt/labops-pangolin
install -d -m 0700 /etc/labops-pangolin
install -m 0755 -- "$1" /opt/labops-pangolin/newt
install -m 0644 -- "$source_dir/newt.service" /etc/systemd/system/labops-newt.service
echo 'Connector files installed. Configure the site credentials before starting the service.'
