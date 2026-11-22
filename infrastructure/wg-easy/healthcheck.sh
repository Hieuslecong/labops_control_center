#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${WG_EASY_CONTAINER_NAME:-labops-wg-easy}"
EXPECTED_CIDR="${WG_EASY_VPN_SUBNET:-10.88.40.0/24}"
EXPECTED_GW_IP="${WG_EASY_EXPECTED_GW_IP:-10.88.40.1/24}"
EXPECTED_PORT="${WG_EASY_UDP_PORT:-2204}"
FAIL=0

echo "=== KIỂM TRA TRẠNG THÁI & ĐỊNH TUYẾN WG-EASY V15 ==="
if ! docker ps --filter "name=^/${CONTAINER_NAME}$" --format '{{.Names}}: {{.Status}}' | grep -q "${CONTAINER_NAME}"; then
    echo "❌ Container ${CONTAINER_NAME} không hoạt động."
    exit 1
fi

echo "✅ Container ${CONTAINER_NAME}:"
docker ps --filter "name=^/${CONTAINER_NAME}$" --format '  ID: {{.ID}} | Image: {{.Image}} | Status: {{.Status}} | Ports: {{.Ports}}'

echo ""
echo "1. Kiểm tra Web API nội bộ 127.0.0.1:51821..."
if curl -s -f -m 3 http://127.0.0.1:51821/login >/dev/null 2>&1; then
    echo "   ✅ VPN_API_HEALTHY (127.0.0.1:51821)"
else
    echo "   ❌ Web API cổng 51821 không phản hồi."
    FAIL=1
fi

echo ""
echo "2. Kiểm tra UDP ${EXPECTED_PORT} trên host..."
if ss -ulnp 2>/dev/null | grep -q ":${EXPECTED_PORT} "; then
    echo "   ✅ Cổng UDP ${EXPECTED_PORT} đang lắng nghe trên host."
else
    echo "   ❌ Cổng UDP ${EXPECTED_PORT} chưa mở trên host."
    FAIL=1
fi

echo ""
echo "3. Kiểm tra runtime wg0, route và NAT trong container..."
ADDR_OUT="$(docker exec "${CONTAINER_NAME}" ip -4 -o addr show dev wg0 2>/dev/null || true)"
if echo "$ADDR_OUT" | grep -q "${EXPECTED_GW_IP}"; then
    echo "   ✅ wg0 IPv4 khớp ${EXPECTED_GW_IP}"
else
    echo "   ❌ VPN_INTERFACE_MISMATCH: wg0='${ADDR_OUT}' (expected ${EXPECTED_GW_IP})"
    FAIL=1
fi

ROUTE_OUT="$(docker exec "${CONTAINER_NAME}" ip -4 route show dev wg0 2>/dev/null || true)"
if echo "$ROUTE_OUT" | grep -q "${EXPECTED_CIDR}"; then
    echo "   ✅ Route khớp ${EXPECTED_CIDR} dev wg0"
else
    echo "   ❌ VPN_ROUTE_MISMATCH: routes='${ROUTE_OUT}' (expected ${EXPECTED_CIDR})"
    FAIL=1
fi

NAT_OUT="$(docker exec "${CONTAINER_NAME}" iptables -t nat -S POSTROUTING 2>/dev/null || true)"
if echo "$NAT_OUT" | grep -q "\-s ${EXPECTED_CIDR}.*MASQUERADE"; then
    echo "   ✅ NAT POSTROUTING khớp ${EXPECTED_CIDR} MASQUERADE"
else
    echo "   ❌ VPN_NAT_MISMATCH: NAT='${NAT_OUT}' (expected -s ${EXPECTED_CIDR})"
    FAIL=1
fi

echo ""
echo "4. Kiểm tra Peer AllowedIPs và WireGuard show..."
docker exec "${CONTAINER_NAME}" wg show wg0 allowed-ips 2>/dev/null || true

if [ "$FAIL" -ne 0 ]; then
    echo "❌ PHÁT HIỆN LỖI ĐỊNH TUYẾN/RUNTIME WG-EASY."
    exit 1
fi

echo "✅ TẤT CẢ KIỂM TRA WG-EASY RUNTIME ĐẠT."

