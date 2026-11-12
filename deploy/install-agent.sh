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

# 2. Kiểm tra hoặc biên dịch binary
echo "📦 [1/4] Chuẩn bị LabOps Go Agent binary..."
mkdir -p "${ROOT_DIR}/bin"

if [ -f "${ROOT_DIR}/bin/labops-agent" ]; then
    echo "✅ Sử dụng binary có sẵn tại: ${ROOT_DIR}/bin/labops-agent"
elif command -v go &>/dev/null; then
    echo "🔨 Đang biên dịch LabOps Go Agent bằng Go toolchain..."
    (cd "${ROOT_DIR}/go" && go build -o "${ROOT_DIR}/bin/labops-agent" ./cmd/labops-agent)
    echo "✅ Đã biên dịch binary thành công."
else
    echo "❌ Lỗi: Không tìm thấy binary '${ROOT_DIR}/bin/labops-agent' và máy chưa cài đặt Go compiler!"
    echo "   Vui lòng copy tệp bin/labops-agent từ Server Rack sang."
    exit 1
fi
chmod +x "${ROOT_DIR}/bin/labops-agent"

# Tự động copy vào /usr/local/bin nếu có quyền sudo/root
if [ -w /usr/local/bin ]; then
    cp "${ROOT_DIR}/bin/labops-agent" /usr/local/bin/labops-agent
    chmod +x /usr/local/bin/labops-agent
    echo "✅ Đã cài đặt binary vào /usr/local/bin/labops-agent"
fi

# 3. Tạo file cấu hình env nếu chưa có
if [ ! -f "${SCRIPT_DIR}/labops-agent.env" ]; then
    echo "⚙️ [2/4] Tạo cấu hình deploy/labops-agent.env từ mẫu..."
    cp "${SCRIPT_DIR}/labops-agent.env.example" "${SCRIPT_DIR}/labops-agent.env"
fi
if [ "$(id -u)" -eq 0 ]; then
    chown aiot-1:docker "${SCRIPT_DIR}/labops-agent.env" 2>/dev/null || true
    chmod 640 "${SCRIPT_DIR}/labops-agent.env"
    if [ -f "/etc/labops/certs/client.key" ]; then
        chown root:docker "/etc/labops/certs/client.key" 2>/dev/null || true
        chmod 640 "/etc/labops/certs/client.key" 2>/dev/null || true
    fi
else
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
    sleep 1.5
else
    echo "ℹ️ Không có quyền ghi vào /etc/systemd/system. Chạy lệnh sau bằng quyền root/sudo nếu cần cài systemd:"
    echo "   sudo cp ${SCRIPT_DIR}/labops-agent.service /etc/systemd/system/"
    echo "   sudo systemctl daemon-reload && sudo systemctl enable --now labops-agent"
fi

# 5. Xác minh tiến trình
echo "🔍 [4/4] Kiểm tra hoạt động của Agent..."
bash "${SCRIPT_DIR}/verify-agent.sh"

