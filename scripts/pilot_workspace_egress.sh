#!/usr/bin/env bash
# ==============================================================================
# LabOps Repository Guard — Pilot Network Egress Enforcement Script (Phase 9)
# ==============================================================================
# CHỈ ÁP DỤNG TRÊN 1 WORKSPACE THỬ NGHIỆM DUY NHẤT (PILOT)
# TUYỆT ĐỐI KHÔNG ẢNH HƯỞNG: wg-easy, Docker Daemon, Host SSH, GPU Jobs khác
# ==============================================================================

set -euo pipefail

PILOT_CONTAINER="${1:-ws-pilot}"
PILOT_CHAIN="LABOPS-PILOT-EGRESS"

echo "=========================================================="
echo "🛡️  LabOps Pilot Network Egress Policy Enforcement"
echo "=========================================================="
echo "Target Container: ${PILOT_CONTAINER}"

# 1. Kiểm tra container có tồn tại không
if ! docker inspect "${PILOT_CONTAINER}" >/dev/null 2>&1; then
    echo "⚠️  Container '${PILOT_CONTAINER}' không tồn tại hoặc chưa chạy."
    echo "Tạo container kiểm thử mô phỏng nếu cần:"
    echo "  docker run -d --name ${PILOT_CONTAINER} alpine:latest sleep 3600"
    exit 1
fi

# 2. Lấy IP của container thí điểm
CONTAINER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${PILOT_CONTAINER}")
if [ -z "${CONTAINER_IP}" ]; then
    echo "❌ Không thể lấy địa chỉ IP của container ${PILOT_CONTAINER}"
    exit 1
fi

echo "Container IP: ${CONTAINER_IP}"

# 3. Tạo Chain riêng cho Pilot trong bảng filter (an toàn, cô lập hoàn toàn)
if ! iptables -N "${PILOT_CHAIN}" 2>/dev/null; then
    iptables -F "${PILOT_CHAIN}"
fi

# 4. Thiết lập rules trong chain:
# A. Cho phép truy cập DNS (53)
iptables -A "${PILOT_CHAIN}" -p udp --dport 53 -j ACCEPT
iptables -A "${PILOT_CHAIN}" -p tcp --dport 53 -j ACCEPT

# B. Cho phép truy cập LabOps Repository Guard Agent (port 9100) và Backend (8000)
iptables -A "${PILOT_CHAIN}" -p tcp --dport 9100 -j ACCEPT
iptables -A "${PILOT_CHAIN}" -p tcp --dport 8000 -j ACCEPT

# C. CHẶN trực tiếp kết nối HTTPS (443) và SSH (22) đến GitHub (hoặc toàn bộ outbound git)
# Sử dụng domain lookup hoặc port blocking
iptables -A "${PILOT_CHAIN}" -p tcp --dport 22 -j REJECT --reject-with icmp-port-unreachable
iptables -A "${PILOT_CHAIN}" -d 140.82.112.0/20 -p tcp --dport 443 -j REJECT --reject-with icmp-port-unreachable
iptables -A "${PILOT_CHAIN}" -d 140.82.121.0/24 -p tcp --dport 443 -j REJECT --reject-with icmp-port-unreachable
iptables -A "${PILOT_CHAIN}" -d 192.30.252.0/22 -p tcp --dport 443 -j REJECT --reject-with icmp-port-unreachable
iptables -A "${PILOT_CHAIN}" -d 185.199.108.0/22 -p tcp --dport 443 -j REJECT --reject-with icmp-port-unreachable

# 5. Gắn chain vào DOCKER-USER chỉ cho IP của container pilot
if ! iptables -C DOCKER-USER -s "${CONTAINER_IP}" -j "${PILOT_CHAIN}" 2>/dev/null; then
    iptables -I DOCKER-USER 1 -s "${CONTAINER_IP}" -j "${PILOT_CHAIN}"
fi

echo "✅ Đã kích hoạt Network Egress Policy thành công cho ${PILOT_CONTAINER} (${CONTAINER_IP})."
echo "Thử nghiệm: bên trong container, lệnh 'git clone https://github.com/...' sẽ bị chặn."
echo "Chỉ có thể nạp mã qua: 'labops clone <repo_url>'."
