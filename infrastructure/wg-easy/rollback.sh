#!/usr/bin/env bash
set -euo pipefail

echo "=== ROLLBACK DỊCH VỤ WG-EASY ==="
echo "Dừng và gỡ container labops-wg-easy..."
docker compose -f "$(dirname "$0")/compose.yaml" down

echo "ℹ️ Volume 'labops_wg_easy_data' vẫn được giữ nguyên an toàn."
echo "ℹ️ Hạ tầng WireGuard cũ (wg0 cổng 2204) và SSH cổng 1911 không bị ảnh hưởng."
echo "✅ Đã rollback container wg-easy thành công."

