#!/usr/bin/env bash
set -euo pipefail

# Script cài đặt và khởi chạy LabOps Agent Gateway trên Server Rack
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=========================================================="
echo "🛡️ CÀI ĐẶT LABOPS AGENT GATEWAY (PORT 9443 mTLS)"
echo "=========================================================="

export PATH="/home/aiot-1/.local/go/bin:/usr/local/go/bin:$HOME/.local/go/bin:$HOME/go/bin:$PATH"

# 1. Chuẩn bị Gateway binary
echo "📦 [1/3] Chuẩn bị labops-gateway binary..."
mkdir -p "${ROOT_DIR}/bin"

if [ -f "${ROOT_DIR}/bin/labops-gateway" ]; then
    echo "✅ Sử dụng binary có sẵn tại: ${ROOT_DIR}/bin/labops-gateway"
elif command -v go &>/dev/null; then
    echo "🔨 Đang biên dịch labops-gateway bằng Go toolchain..."
    (cd "${ROOT_DIR}/go" && go build -o "${ROOT_DIR}/bin/labops-gateway" ./cmd/labops-gateway)
    echo "✅ Đã biên dịch binary thành công."
elif [ -x /home/aiot-1/.local/go/bin/go ]; then
    echo "🔨 Đang biên dịch labops-gateway bằng Go toolchain tại /home/aiot-1/.local/go/bin/go..."
    (cd "${ROOT_DIR}/go" && /home/aiot-1/.local/go/bin/go build -o "${ROOT_DIR}/bin/labops-gateway" ./cmd/labops-gateway)
    echo "✅ Đã biên dịch binary thành công."
else
    echo "❌ Lỗi: Không tìm thấy Go compiler và binary '${ROOT_DIR}/bin/labops-gateway' chưa tồn tại!"
    exit 1
fi
chmod +x "${ROOT_DIR}/bin/labops-gateway"

# 2. Khởi tạo thư mục PKI (chống symlink escalation khi chạy dưới quyền root)
LABOPS_STATE_DIR="/home/aiot-1/.labops"
PKI_DIR="${LABOPS_STATE_DIR}/pki"
if [ -L "${LABOPS_STATE_DIR}" ] || [ -L "${PKI_DIR}" ]; then
    echo "❌ Lỗi bảo mật: phát hiện symlink tại ${LABOPS_STATE_DIR} hoặc ${PKI_DIR}, từ chối thay đổi quyền sở hữu."
    exit 1
fi
mkdir -p "${PKI_DIR}"
chmod 700 "${LABOPS_STATE_DIR}" "${PKI_DIR}"
if [ "$(id -u)" -eq 0 ]; then
    find "${LABOPS_STATE_DIR}" -maxdepth 3 -type l -exec sh -c 'echo "❌ Từ chối symlink trong PKI: $1"; exit 1' _ {} \;
    chown -h aiot-1:aiot-1 "${LABOPS_STATE_DIR}" "${PKI_DIR}"
    find "${PKI_DIR}" -maxdepth 2 \( -type f -o -type d \) -exec chown -h aiot-1:aiot-1 {} +
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

