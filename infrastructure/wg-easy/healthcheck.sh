#!/usr/bin/env bash
set -euo pipefail

echo "=== KIỂM TRA TRẠNG THÁI WG-EASY ==="
if ! docker ps --filter "name=labops-wg-easy" --format '{{.Names}}: {{.Status}}' | grep -q "labops-wg-easy"; then
    echo "❌ Container labops-wg-easy không hoạt động."
    exit 1
fi

echo "✅ Container labops-wg-easy đang chạy:"
docker ps --filter "name=labops-wg-easy" --format '  ID: {{.ID}} | Image: {{.Image}} | Status: {{.Status}} | Ports: {{.Ports}}'

echo ""
echo "Kiểm tra cổng lắng nghe nội bộ 127.0.0.1:51821..."
if curl -s -f -m 3 http://127.0.0.1:51821/api/session > /dev/null 2>&1 || curl -s -m 3 http://127.0.0.1:51821/ > /dev/null 2>&1; then
    echo "✅ Web API nội bộ cổng 51821 phản hồi tốt."
else
    echo "⚠️ Web API cổng 51821 chưa sẵn sàng hoặc cần thêm thời gian khởi động."
fi

echo ""
echo "Kiểm tra cổng UDP 2204 trên host (MikroTik NAT)..."
if ss -lntup | grep -q ":2204 "; then
    echo "✅ Cổng UDP 2204 đang lắng nghe trên host."
else
    echo "⚠️ Cổng UDP 2204 chưa xuất hiện trong danh sách lắng nghe."
fi

echo ""
echo "Kiểm tra trạng thái WireGuard bên trong container..."
docker exec labops-wg-easy wg show 2>/dev/null || echo "ℹ️ Chưa có interface active hoặc wg tool đang chạy."

echo ""
echo "=== HOÀN TẤT KIỂM TRA ==="

