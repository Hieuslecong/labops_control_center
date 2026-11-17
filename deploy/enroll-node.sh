#!/usr/bin/env bash
set -euo pipefail

# Script tự động đăng ký và cấp phát chứng chỉ mTLS cho Compute Node
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ "${1:-}" != "--apply" ]]; then
    echo "Dry run only: enrollment contacts Gateway and writes private keys. Rerun with --apply after operator approval."
    exit 0
fi
shift

NODE_ID="${1:-workstation}"
GATEWAY_HOST="${2:-192.168.167.250}"
GATEWAY_HTTP_PORT="${3:-9444}"
GATEWAY_GRPC_PORT="${4:-9443}"
TARGET_CERT_DIR="${5:-/etc/labops/certs}"
AGENT_BIND_ADDR="${LABOPS_AGENT_BIND_ADDR:-}"
AGENT_PORT="${LABOPS_AGENT_PORT:-}"

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

CONTROL_SECRET="${LABOPS_GATEWAY_CONTROL_SECRET:-}"
AGENT_SECRET="${LABOPS_AGENT_SECRET:-}"
if [ "${#CONTROL_SECRET}" -lt 32 ]; then
    echo "❌ LABOPS_GATEWAY_CONTROL_SECRET phải được cung cấp riêng (tối thiểu 32 ký tự)."
    exit 1
fi
if [ "${#AGENT_SECRET}" -lt 32 ]; then
    echo "❌ LABOPS_AGENT_SECRET phải được cung cấp rõ ràng (tối thiểu 32 ký tự)."
    exit 1
fi
if [[ ! "${AGENT_SECRET}" =~ ^[A-Za-z0-9_-]{32,256}$ ]]; then
    echo "❌ LABOPS_AGENT_SECRET chỉ chấp nhận chuỗi base64url/hex dài 32-256 ký tự."
    exit 1
fi
if [[ ! "${NODE_ID}" =~ ^[a-z0-9][a-z0-9._-]{0,62}$ ]]; then
    echo "❌ NODE_ID không hợp lệ."
    exit 1
fi
if [ -z "${AGENT_BIND_ADDR}" ] || [ -z "${AGENT_PORT}" ] || ! [[ "${AGENT_PORT}" =~ ^[0-9]{1,5}$ ]] || [ "${AGENT_PORT}" -lt 1 ] || [ "${AGENT_PORT}" -gt 65535 ]; then
    echo "❌ Hãy cung cấp LABOPS_AGENT_BIND_ADDR và LABOPS_AGENT_PORT hiện hữu; script không tự chọn endpoint."
    exit 1
fi

if [ -L "${TARGET_CERT_DIR}" ]; then
    echo "❌ Từ chối ghi chứng chỉ qua symlink target."
    exit 1
fi
TARGET_CERT_DIR="$(realpath -m "${TARGET_CERT_DIR}")"
CONFIG_FILE="$(dirname "${TARGET_CERT_DIR}")/agent.env"
if [ -e "${TARGET_CERT_DIR}" ] || [ -L "${TARGET_CERT_DIR}" ] || [ -e "${CONFIG_FILE}" ] || [ -L "${CONFIG_FILE}" ]; then
    echo "❌ Từ chối ghi đè cert directory hoặc agent.env đã tồn tại. Sao lưu và xử lý riêng trước khi enroll."
    exit 1
fi

AUTH_HEADER_FILE="$(mktemp)"
chmod 600 "${AUTH_HEADER_FILE}"
TOKEN_BODY_FILE="$(mktemp)"
ISSUE_BODY_FILE="$(mktemp)"
cleanup_secrets() { rm -f -- "${AUTH_HEADER_FILE}" "${TOKEN_BODY_FILE}" "${ISSUE_BODY_FILE}"; }
trap cleanup_secrets EXIT
printf 'Authorization: Bearer %s\n' "${CONTROL_SECRET}" > "${AUTH_HEADER_FILE}"

TOKEN="${ENROLLMENT_TOKEN:-}"

# 2. Yêu cầu cấp Enrollment Token từ Gateway Control Plane (nếu chưa được cấp sẵn)
if [ -z "${TOKEN}" ]; then
    echo "🔑 [1/4] Yêu cầu One-Time Enrollment Token từ Gateway Control Plane..."
    printf '{"node_id":"%s","ttl_minutes":15}\n' "${NODE_ID}" > "${TOKEN_BODY_FILE}"
    ENROLL_RESP=$(curl --max-time 20 -s -f -X POST "http://${GATEWAY_HOST}:${GATEWAY_HTTP_PORT}/api/v1/gateway/enroll" \
        -H "Content-Type: application/json" \
        -H "@${AUTH_HEADER_FILE}" \
        --data-binary "@${TOKEN_BODY_FILE}" || true)

    if [ -z "${ENROLL_RESP}" ]; then
        echo "❌ Không thể kết nối tới Gateway Control Plane hoặc chưa xác thực tại http://${GATEWAY_HOST}:${GATEWAY_HTTP_PORT}"
        exit 1
    fi

    TOKEN=$(echo "${ENROLL_RESP}" | jq -r '.token // empty')
    if [ -z "${TOKEN}" ]; then
        echo "❌ Gateway không trả Enrollment Token hợp lệ; response đã được giữ kín."
        exit 1
    fi
    echo "✅ Đã nhận Enrollment Token (có hiệu lực trong 15 phút)"
else
    echo "🔑 [1/4] Sử dụng Enrollment Token được chỉ định trước."
fi

# 3. Sử dụng token để nhận cặp chứng chỉ và private key từ Gateway CA
echo "📜 [2/4] Trao đổi Token lấy Chứng chỉ mTLS Client TLS 1.3..."
printf '{"node_id":"%s","enrollment_token":"%s"}\n' "${NODE_ID}" "${TOKEN}" > "${ISSUE_BODY_FILE}"
ISSUE_RESP=$(curl --max-time 20 -s -f -X POST "http://${GATEWAY_HOST}:${GATEWAY_HTTP_PORT}/api/v1/gateway/issue-cert" \
    -H "Content-Type: application/json" \
    --data-binary "@${ISSUE_BODY_FILE}")

CA_CERT=$(echo "${ISSUE_RESP}" | jq -r '.ca_certificate // empty')
CLIENT_CERT=$(echo "${ISSUE_RESP}" | jq -r '.certificate // empty')
CLIENT_KEY=$(echo "${ISSUE_RESP}" | jq -r '.private_key // empty')

if [ -z "${CA_CERT}" ] || [ -z "${CLIENT_CERT}" ] || [ -z "${CLIENT_KEY}" ]; then
    echo "❌ Phản hồi cấp chứng chỉ không hợp lệ; nội dung nhạy cảm đã được giữ kín."
    exit 1
fi

# 4. Lưu chứng chỉ an toàn với quyền hạn nghiêm ngặt
echo "💾 [3/4] Lưu chứng chỉ và khóa bí mật vào ${TARGET_CERT_DIR}..."
TARGET_PARENT="$(dirname "${TARGET_CERT_DIR}")"
mkdir -p "${TARGET_PARENT}"
STAGE_DIR="$(mktemp -d "${TARGET_PARENT}/.labops-certs.XXXXXX")"
cleanup_stage() { rm -rf -- "${STAGE_DIR}"; }
trap 'cleanup_stage; cleanup_secrets' EXIT
printf '%s\n' "${CA_CERT}" > "${STAGE_DIR}/ca.crt"
printf '%s\n' "${CLIENT_CERT}" > "${STAGE_DIR}/client.crt"
printf '%s\n' "${CLIENT_KEY}" > "${STAGE_DIR}/client.key"

chmod 644 "${STAGE_DIR}/ca.crt" "${STAGE_DIR}/client.crt"
chmod 600 "${STAGE_DIR}/client.key"
chmod 700 "${STAGE_DIR}"
mv -nT -- "${STAGE_DIR}" "${TARGET_CERT_DIR}"
if [ -e "${STAGE_DIR}" ]; then
    echo "❌ Từ chối thay thế cert directory được tạo đồng thời."
    exit 1
fi
echo "✅ Đã phân quyền an toàn: client.key có quyền 0600 (chỉ owner đọc)"

# 5. Tạo file cấu hình agent.env
echo "⚙️ [4/4] Khởi tạo cấu hình agent.env..."
CONFIG_STAGE="$(mktemp "${TARGET_PARENT}/.agent-env.XXXXXX")"
cat <<EOF > "${CONFIG_STAGE}"
# LabOps Node Agent Environment
LABOPS_SERVER_ID="${NODE_ID}"
LABOPS_SERVER_NAME="Server Workstation (${NODE_ID} • 1x NVIDIA A30)"
LABOPS_AGENT_BIND_ADDR="${AGENT_BIND_ADDR}"
LABOPS_AGENT_PORT="${AGENT_PORT}"
LABOPS_AGENT_SECRET="${AGENT_SECRET}"
DOCKER_HOST="/var/run/docker.sock"

# Zero-Trust Gateway Tunnel
LABOPS_GATEWAY_ENABLED="true"
LABOPS_GATEWAY_URL="${GATEWAY_HOST}:${GATEWAY_GRPC_PORT}"
LABOPS_GATEWAY_CA_PATH="${TARGET_CERT_DIR}/ca.crt"
LABOPS_GATEWAY_CERT_PATH="${TARGET_CERT_DIR}/client.crt"
LABOPS_GATEWAY_KEY_PATH="${TARGET_CERT_DIR}/client.key"
LABOPS_GATEWAY_SERVER_NAME="gateway.control-plane.labops.local"
EOF
chmod 600 "${CONFIG_STAGE}"
mv -nT -- "${CONFIG_STAGE}" "${CONFIG_FILE}"
if [ -e "${CONFIG_STAGE}" ]; then
    echo "❌ Từ chối thay thế agent.env được tạo đồng thời."
    exit 1
fi

echo "=========================================================="
echo "🎉 GHI DANH THÀNH CÔNG! Node '${NODE_ID}' đã sẵn sàng kết nối."
echo "Để khởi chạy agent, thực thi:"
echo "   sudo ${SCRIPT_DIR}/install-agent.sh"
echo "=========================================================="

