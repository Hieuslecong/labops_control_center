#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "🔄 Đang nâng cấp LabOps Go Agent..."
export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"

# Sao lưu binary hiện tại
if [ -f "${ROOT_DIR}/bin/labops-agent" ]; then
    cp "${ROOT_DIR}/bin/labops-agent" "${ROOT_DIR}/bin/labops-agent.backup"
    echo "💾 Đã sao lưu binary cũ vào bin/labops-agent.backup"
fi

# Biên dịch binary mới
(cd "${ROOT_DIR}/go" && go build -o "${ROOT_DIR}/bin/labops-agent" ./cmd/labops-agent)

if command -v systemctl &>/dev/null && systemctl is-active --quiet labops-agent; then
    echo "🔄 Khởi động lại dịch vụ systemd..."
    sudo systemctl restart labops-agent
fi

bash "${SCRIPT_DIR}/verify-agent.sh"
echo "✅ Nâng cấp thành công!"

