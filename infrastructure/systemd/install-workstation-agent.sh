#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
SKIP_IMAGE_BUILD=false
if [[ ${1:-} == --skip-image-build ]]; then
    SKIP_IMAGE_BUILD=true
    shift
fi
if (( $# != 0 )); then
    echo "Usage: $0 [--skip-image-build]" >&2
    exit 2
fi
if (( EUID != 0 )); then
    echo "Vui lòng chạy installer bằng sudo." >&2
    exit 1
fi

exec 9>/run/labops-agent-install.lock
flock -n 9 || { echo "Installer LabOps Agent đang chạy." >&2; exit 1; }

AGENT_BIN="$REPO_DIR/go/bin/labops-agent"
BIN_TARGET=/usr/local/bin/labops-agent
UNIT=labops-agent.service
UNIT_SOURCE="$REPO_DIR/infrastructure/systemd/$UNIT"
UNIT_TARGET="/etc/systemd/system/$UNIT"
IMAGE_TAG=labops-workspace:v2

if [[ ! -x "$AGENT_BIN" || ! -r "$UNIT_SOURCE" ]]; then
    echo "Cần binary đã build ($AGENT_BIN) và unit ($UNIT_SOURCE)." >&2
    exit 1
fi
if [[ "$SKIP_IMAGE_BUILD" == false ]]; then
    docker build -t "$IMAGE_TAG" "$REPO_DIR/infrastructure/workspace-image"
fi
if ! IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_TAG")" ||
    [[ ! "$IMAGE_ID" =~ ^sha256:[a-f0-9]{64}$ ]]; then
    echo "Không xác minh được Docker Image ID của $IMAGE_TAG." >&2
    exit 1
fi

install -d -m 0750 -o aiot-ws1 -g aiot-ws1 /var/lib/labops /var/lib/labops/repositoryguard /var/lib/labops-agent /var/lib/labops-agent/runtime
BACKUP_DIR="$(mktemp -d /var/backups/labops-agent.XXXXXXXX)"
for target in "$BIN_TARGET" "$UNIT_TARGET"; do
    if [[ -e "$target" || -L "$target" ]]; then
        cp -a -- "$target" "$BACKUP_DIR/"
    fi
done
echo "Backup: $BACKUP_DIR"

WAS_ACTIVE=false
if systemctl is-active --quiet "$UNIT"; then
    WAS_ACTIVE=true
fi

rollback() {
    local status=$? target backup failed=0
    trap - EXIT INT TERM
    if (( status == 0 )); then
        return
    fi
    echo "Triển khai thất bại. Đang khôi phục từ $BACKUP_DIR." >&2
    systemctl stop "$UNIT" || failed=1
    for target in "$BIN_TARGET" "$UNIT_TARGET"; do
        backup="$BACKUP_DIR/${target##*/}"
        if [[ -e "$backup" || -L "$backup" ]]; then
            cp -a --remove-destination -- "$backup" "$target" || failed=1
        else
            rm -f -- "$target" || failed=1
        fi
    done
    systemctl daemon-reload || failed=1
    if [[ "$WAS_ACTIVE" == true ]]; then
        systemctl restart "$UNIT" || failed=1
    fi
    if (( failed != 0 )); then
        echo "Rollback chưa hoàn tất. Kiểm tra backup tại $BACKUP_DIR." >&2
    fi
    exit "$status"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

install -m 0755 -o root -g root "$AGENT_BIN" "$BIN_TARGET"
install -m 0644 -o root -g root "$UNIT_SOURCE" "$UNIT_TARGET"
systemctl daemon-reload
systemctl restart "$UNIT"
HEALTH_RESPONSE="$(curl --noproxy "*" --fail --silent --show-error --retry 5 --retry-connrefused --retry-delay 1 \
    --max-time 3 --retry-max-time 20 http://127.0.0.1:9100/health)"
printf '%s\n' "$HEALTH_RESPONSE" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"HEALTHY"'
printf '%s\n' "$HEALTH_RESPONSE" | grep -Eq '"agent"[[:space:]]*:[[:space:]]*"labops-go-agent-v2"'
systemctl is-active --quiet "$UNIT"
trap - EXIT INT TERM

printf '\nCấu hình workspace cho backend:\n'
printf '  LABOPS_WORKSPACE_IMAGE_REF=%s\n' "$IMAGE_ID"
printf '  LABOPS_WORKSPACE_IMAGE_DIGEST=%s\n' "$IMAGE_ID"
