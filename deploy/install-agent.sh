#!/usr/bin/env bash
set -euo pipefail

# Script cài đặt LabOps Go Node Agent trên máy chủ Debian
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=========================================================="
echo "🚀 CÀI ĐẶT LABOPS GO NODE AGENT TRÊN DEBIAN"
echo "=========================================================="

# 1. Thêm Go vào PATH nếu có
export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"

# 2. Biên dịch binary
echo "📦 [1/4] Đang biên dịch LabOps Go Agent..."
mkdir -p "${ROOT_DIR}/bin"
(cd "${ROOT_DIR}/go" && go build -o "${ROOT_DIR}/bin/labops-agent" ./cmd/labops-agent)
chmod +x "${ROOT_DIR}/bin/labops-agent"
echo "✅ Đã tạo binary tại: ${ROOT_DIR}/bin/labops-agent"

# 3. Tạo file cấu hình env nếu chưa có
if [ ! -f "${SCRIPT_DIR}/labops-agent.env" ]; then
    echo "⚙️ [2/4] Tạo cấu hình deploy/labops-agent.env từ mẫu..."
    cp "${SCRIPT_DIR}/labops-agent.env.example" "${SCRIPT_DIR}/labops-agent.env"
    chmod 600 "${SCRIPT_DIR}/labops-agent.env"
fi

# 4. Kiểm tra quyền systemd
echo "🔧 [3/4] Cài đặt dịch vụ systemd..."
if command -v systemctl &>/dev/null && [ -w /etc/systemd/system ]; then
    cp "${SCRIPT_DIR}/labops-agent.service" /etc/systemd/system/labops-agent.service
    systemctl daemon-reload
    systemctl enable labops-agent.service
    systemctl restart labops-agent.service
    echo "✅ Dịch vụ systemd 'labops-agent' đã được kích hoạt và khởi chạy."
else
    echo "ℹ️ Không có quyền ghi vào /etc/systemd/system. Chạy lệnh sau bằng quyền root/sudo nếu cần cài systemd:"
    echo "   sudo cp ${SCRIPT_DIR}/labops-agent.service /etc/systemd/system/"
    echo "   sudo systemctl daemon-reload && sudo systemctl enable --now labops-agent"
fi

# 5. Xác minh tiến trình
echo "🔍 [4/4] Kiểm tra hoạt động của Agent..."
bash "${SCRIPT_DIR}/verify-agent.sh"

