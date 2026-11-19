#!/usr/bin/env bash
set -euo pipefail

# Bảo vệ nghiêm ngặt: cấm chạy dưới root (UID 0) hoặc UID quản trị host (1000)
CURRENT_UID="$(id -u)"
if [ "$CURRENT_UID" -eq 0 ]; then
    echo "SECURITY_ABORT: Workspace container must NOT run as UID 0 (root)." >&2
    exit 1
fi
if [ "$CURRENT_UID" -lt 20000 ]; then
    echo "SECURITY_ABORT: Workspace container UID ($CURRENT_UID) must be >= 20000 (isolated workspace range)." >&2
    exit 1
fi

export HOME="${HOME:-/workspace}"

# Sinh host key riêng của container trong /tmp (thuộc sở hữu non-root UID của workspace)
HOST_KEY="/tmp/ssh_host_ed25519_key"
if [ ! -f "$HOST_KEY" ]; then
    ssh-keygen -q -t ed25519 -N "" -f "$HOST_KEY"
    chmod 0600 "$HOST_KEY"
fi

SSHD_CFG="/etc/labops-ssh/sshd_config"
if [ ! -r "$SSHD_CFG" ]; then
    echo "RUNTIME_ABORT: Missing read-only /etc/labops-ssh/sshd_config" >&2
    exit 1
fi

# Khởi chạy OpenSSH daemon không đặc quyền lắng nghe port 2222
exec /usr/sbin/sshd -D -e -f "$SSHD_CFG"
