#!/usr/bin/env bash
set -euo pipefail

# Script tự động đăng ký và cấp phát chứng chỉ mTLS cho Compute Node
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

NODE_ID="${1:-workstation}"
GATEWAY_HOST="${2:-192.168.167.250}"
GATEWAY_HTTP_PORT="${3:-9444}"
GATEWAY_GRPC_PORT="${4:-9443}"
TARGET_CERT_DIR="${5:-/etc/labops/certs}"

echo "=========================================================="
echo "🛡️ LABOPS ZERO-TRUST NODE ENROLLMENT (mTLS PROVISIONING)"
echo "=========================================================="
echo "Node ID:            ${NODE_ID}"
echo "Gateway Host:       ${GATEWAY_HOST}"
echo "Gateway HTTP API:   http://${GATEWAY_HOST}:${GATEWAY_HTTP_PORT}"
echo "Gateway gRPC mTLS:  ${GATEWAY_HOST}:${GATEWAY_GRPC_PORT}"
echo "Thư mục chứng chỉ:  ${TARGET_CERT_DIR}"
echo "=========================================================="

# 1. Kiểm tra curl và jq
for tool in curl jq; do
    if ! command -v "$tool" &>/dev/null; then
        echo "❌ Thiếu công cụ bắt buộc: $tool. Vui lòng cài đặt: sudo apt install -y $tool"
        exit 1
    fi
done

CONTROL_SECRET="${6:-${LABOPS_GATEWAY_CONTROL_SECRET:-${LABOPS_SESSION_SECRET:-${LABOPS_ADMIN_API_TOKEN:-}}}}"
AUTH_HEADER=""
if [ -n "${CONTROL_SECRET}" ]; then
    AUTH_HEADER="-H Authorization: Bearer ${CONTROL_SECRET}"
fi

TOKEN="${ENROLLMENT_TOKEN:-}"

# 2. Yêu cầu cấp Enrollment Token từ Gateway Control Plane (nếu chưa được cấp sẵn)
if [ -z "${TOKEN}" ]; then
    echo "🔑 [1/4] Yêu cầu One-Time Enrollment Token từ Gateway Control Plane..."
    ENROLL_RESP=$(curl -s -f -X POST "http://${GATEWAY_HOST}:${GATEWAY_HTTP_PORT}/api/v1/gateway/enroll" \
        -H "Content-Type: application/json" \
        ${AUTH_HEADER} \
        -d "{\"node_id\": \"${NODE_ID}\", \"ttl_minutes\": 15}" || true)

    if [ -z "${ENROLL_RESP}" ]; then
        echo "❌ Không thể kết nối tới Gateway Control Plane hoặc chưa xác thực tại http://${GATEWAY_HOST}:${GATEWAY_HTTP_PORT}"
        exit 1
    fi

    TOKEN=$(echo "${ENROLL_RESP}" | jq -r '.token // empty')
    if [ -z "${TOKEN}" ]; then
        echo "❌ Không nhận được token từ Gateway: ${ENROLL_RESP}"
        exit 1
    fi
    echo "✅ Đã nhận Enrollment Token (có hiệu lực trong 15 phút)"
else
    echo "🔑 [1/4] Sử dụng Enrollment Token được chỉ định trước: ${TOKEN:0:8}..."
fi

# 3. Sử dụng token để nhận cặp chứng chỉ và private key từ Gateway CA
echo "📜 [2/4] Trao đổi Token lấy Chứng chỉ mTLS Client TLS 1.3..."
ISSUE_RESP=$(curl -s -f -X POST "http://${GATEWAY_HOST}:${GATEWAY_HTTP_PORT}/api/v1/gateway/issue-cert" \
    -H "Content-Type: application/json" \
    -d "{\"node_id\": \"${NODE_ID}\", \"enrollment_token\": \"${TOKEN}\"}")

CA_CERT=$(echo "${ISSUE_RESP}" | jq -r '.ca_certificate // empty')
CLIENT_CERT=$(echo "${ISSUE_RESP}" | jq -r '.certificate // empty')
CLIENT_KEY=$(echo "${ISSUE_RESP}" | jq -r '.private_key // empty')

if [ -z "${CA_CERT}" ] || [ -z "${CLIENT_CERT}" ] || [ -z "${CLIENT_KEY}" ]; then
    echo "❌ Phản hồi cấp chứng chỉ không hợp lệ: ${ISSUE_RESP}"
    exit 1
fi

# 4. Lưu chứng chỉ an toàn với quyền hạn nghiêm ngặt
echo "💾 [3/4] Lưu chứng chỉ và khóa bí mật vào ${TARGET_CERT_DIR}..."
mkdir -p "${TARGET_CERT_DIR}"
echo "${CA_CERT}" > "${TARGET_CERT_DIR}/ca.crt"
echo "${CLIENT_CERT}" > "${TARGET_CERT_DIR}/client.crt"
echo "${CLIENT_KEY}" > "${TARGET_CERT_DIR}/client.key"

chmod 644 "${TARGET_CERT_DIR}/ca.crt"
chmod 644 "${TARGET_CERT_DIR}/client.crt"
chmod 600 "${TARGET_CERT_DIR}/client.key"
echo "✅ Đã phân quyền an toàn: client.key có quyền 0600 (chỉ owner đọc)"

# 5. Tạo file cấu hình agent.env
echo "⚙️ [4/4] Khởi tạo cấu hình agent.env..."
CONFIG_FILE="${TARGET_CERT_DIR}/../agent.env"
mkdir -p "$(dirname "${CONFIG_FILE}")"

cat <<EOF > "${CONFIG_FILE}"
# LabOps Node Agent Environment
LABOPS_SERVER_ID="${NODE_ID}"
LABOPS_SERVER_NAME="Server Workstation (${NODE_ID} • 1x NVIDIA A30)"
LABOPS_AGENT_BIND_ADDR="0.0.0.0"
LABOPS_AGENT_PORT="9100"
LABOPS_AGENT_SECRET="labops-workstation-verified-token-2026"
DOCKER_HOST="/var/run/docker.sock"

# Zero-Trust Gateway Tunnel
LABOPS_GATEWAY_ENABLED="true"
LABOPS_GATEWAY_URL="${GATEWAY_HOST}:${GATEWAY_GRPC_PORT}"
LABOPS_GATEWAY_CA_PATH="${TARGET_CERT_DIR}/ca.crt"
LABOPS_GATEWAY_CERT_PATH="${TARGET_CERT_DIR}/client.crt"
LABOPS_GATEWAY_KEY_PATH="${TARGET_CERT_DIR}/client.key"
LABOPS_GATEWAY_SERVER_NAME="gateway.control-plane.labops.local"
EOF
chmod 600 "${CONFIG_FILE}"

echo "=========================================================="
echo "🎉 GHI DANH THÀNH CÔNG! Node '${NODE_ID}' đã sẵn sàng kết nối."
echo "Để khởi chạy agent, thực thi:"
echo "   sudo ${SCRIPT_DIR}/install-agent.sh"
echo "=========================================================="

