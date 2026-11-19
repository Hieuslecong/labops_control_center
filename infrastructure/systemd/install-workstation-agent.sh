#!/usr/bin/env bash
set -euo pipefail

# Script triển khai an toàn LabOps Go Node Agent & Standard Workspace Image (chỉ Admin chạy bằng sudo).
REPO_DIR="/home/aiot-ws1/projects/labops_control_center"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "Vui lòng chạy với sudo: sudo bash $0" >&2
    exit 1
fi

echo "=== 1. Chuẩn bị thư mục runtime cô lập của Node Agent ==="
install -d -m 0750 -o aiot-ws1 -g aiot-ws1 /var/lib/labops-agent /var/lib/labops-agent/runtime

echo "=== 2. Kiểm tra binary labops-agent mới build ==="
AGENT_BIN="${REPO_DIR}/go/bin/labops-agent"
if [ ! -x "$AGENT_BIN" ]; then
    echo "Lỗi: Chưa build $AGENT_BIN (chạy: cd $REPO_DIR/go && go build -o bin/labops-agent ./cmd/labops-agent)" >&2
    exit 1
fi

echo "=== 3. Sao lưu unit & binary hiện hữu (để rollback nếu cần) ==="
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="/var/backups/labops-agent-${TS}"
mkdir -p "$BACKUP_DIR" && chmod 0700 "$BACKUP_DIR"
[ -f /usr/local/bin/labops-agent ] && cp /usr/local/bin/labops-agent "$BACKUP_DIR/labops-agent.bak"
[ -f /etc/systemd/system/labops-agent.service ] && cp /etc/systemd/system/labops-agent.service "$BACKUP_DIR/labops-agent.service.bak"

echo "=== 4. Cài đặt binary & hardened systemd unit ==="
install -m 0755 -o root -g root "$AGENT_BIN" /usr/local/bin/labops-agent
install -m 0644 -o root -g root "${REPO_DIR}/infrastructure/systemd/labops-agent.service" /etc/systemd/system/labops-agent.service
systemctl daemon-reload
systemctl restart labops-agent.service
systemctl --no-pager status labops-agent.service

echo "=== 5. Gợi ý build & ghim digest @sha256: cho Workspace Image ==="
echo "  docker build -t labops-workspace:v2 ${REPO_DIR}/infrastructure/workspace-image"
echo "  docker inspect --format='{{index .RepoDigests 0}}' labops-workspace:v2"
