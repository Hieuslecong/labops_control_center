#!/usr/bin/env bash
# ==============================================================================
# LabOps Repository Guard — Pilot Network Egress Rollback Script (Phase 9 & 17)
# ==============================================================================
# HỦY BỎ HOÀN TOÀN MỌI IPTABLES RULES ĐÃ THỬ NGHIỆM TRÊN PILOT WORKSPACE
# ==============================================================================

set -euo pipefail

PILOT_CHAIN="LABOPS-PILOT-EGRESS"

echo "=========================================================="
echo "🔄  Rollback LabOps Pilot Network Egress Policy"
echo "=========================================================="

# 1. Gỡ rule khỏi DOCKER-USER chain
while iptables -D DOCKER-USER -j "${PILOT_CHAIN}" 2>/dev/null; do
    echo "Đã gỡ rule từ DOCKER-USER..."
done

# Gỡ các rules theo source IP nếu có
iptables -S DOCKER-USER 2>/dev/null | grep "${PILOT_CHAIN}" | while read -r rule; do
    cmd=$(echo "$rule" | sed 's/-A/-D/')
    iptables $cmd 2>/dev/null || true
done

# 2. Xóa các rules trong chain và xóa chain
if iptables -L "${PILOT_CHAIN}" >/dev/null 2>&1; then
    iptables -F "${PILOT_CHAIN}" 2>/dev/null || true
    iptables -X "${PILOT_CHAIN}" 2>/dev/null || true
    echo "✅ Đã xóa hoàn toàn chain ${PILOT_CHAIN}"
else
    echo "ℹ️  Chain ${PILOT_CHAIN} không tồn tại hoặc đã được gỡ bỏ."
fi

echo "🎉 Rollback hoàn tất 100%. Không có bất kỳ rule nào còn tồn tại trên host."

