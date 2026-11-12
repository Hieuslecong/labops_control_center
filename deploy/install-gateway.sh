#!/usr/bin/env bash
set -euo pipefail

# Script cài đặt và khởi chạy LabOps Agent Gateway trên Server Rack
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=========================================================="
echo "🛡️ CÀI ĐẶT LABOPS AGENT GATEWAY (PORT 9443 mTLS)"
echo "=========================================================="

export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"

# 1. Biên dịch Gateway
echo "📦 [1/3] Biên dịch labops-gateway binary..."
mkdir -p "${ROOT_DIR}/bin"
(cd "${ROOT_DIR}/go" && go build -o "${ROOT_DIR}/bin/labops-gateway" ./cmd/labops-gateway)
chmod +x "${ROOT_DIR}/bin/labops-gateway"
echo "✅ Đã tạo binary tại: ${ROOT_DIR}/bin/labops-gateway"

# 2. Khởi tạo thư mục PKI
PKI_DIR="/home/aiot-1/.labops/pki"
mkdir -p "${PKI_DIR}"
chmod 700 "${PKI_DIR}"
if [ "$(id -u)" -eq 0 ]; then
    chown -R aiot-1:aiot-1 "/home/aiot-1/.labops"
fi
echo "📁 [2/3] Đã cấu hình thư mục Root CA PKI: ${PKI_DIR}"

# 3. Cài đặt systemd nếu có quyền sudo
echo "🔧 [3/3] Thiết lập systemd service..."
if command -v systemctl &>/dev/null && [ -w /etc/systemd/system ]; then
    cp "${SCRIPT_DIR}/labops-gateway.service" /etc/systemd/system/labops-gateway.service
    systemctl daemon-reload
    systemctl enable labops-gateway.service
    systemctl restart labops-gateway.service
    echo "✅ LabOps Gateway systemd service đã được kích hoạt và khởi chạy."
else
    echo "ℹ️ Chạy lệnh sau để cấu hình systemd:"
    echo "   sudo cp ${SCRIPT_DIR}/labops-gateway.service /etc/systemd/system/"
    echo "   sudo systemctl daemon-reload && sudo systemctl enable --now labops-gateway"
fi

echo "=========================================================="
echo "🎉 LabOps Agent Gateway đã sẵn sàng lắng nghe:"
echo "   gRPC mTLS: 0.0.0.0:9443"
echo "   HTTP API:  127.0.0.1:9444"
echo "=========================================================="

