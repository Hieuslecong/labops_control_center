#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "⏪ THỰC HIỆN ROLLBACK LABOPS GO AGENT..."

# 1. Dừng systemd nếu đang chạy
if command -v systemctl &>/dev/null && systemctl is-active --quiet labops-agent; then
    echo "🛑 Đang tạm dừng dịch vụ systemd labops-agent..."
    sudo systemctl stop labops-agent || true
fi

# 2. Khôi phục binary backup nếu có
if [ -f "${ROOT_DIR}/bin/labops-agent.backup" ]; then
    echo "💾 Khôi phục binary cũ từ labops-agent.backup..."
    cp "${ROOT_DIR}/bin/labops-agent.backup" "${ROOT_DIR}/bin/labops-agent"
    if command -v systemctl &>/dev/null; then
        sudo systemctl start labops-agent || true
    fi
    echo "✅ Đã khôi phục phiên bản binary trước đó."
else
    echo "⚠️ Không tìm thấy file backup. Dừng Agent hoàn toàn."
    echo "ℹ️ Backend Python sẽ tự động kích hoạt cơ chế fallback nội bộ về Python Collector."
fi

# 3. Kiểm tra tính sẵn sàng của backend Python
echo "🔍 Kiểm tra phản hồi Backend Python sau khi rollback..."
backend/.venv/bin/python -c '
from app.adapters.host_monitor_adapter import HostMonitorAdapter
h = HostMonitorAdapter(is_demo_mode=False, local_host_id="track")
m = h.get_remote_track_telemetry()
print("✅ Python Backend Fallback hoạt động bình thường! Host:", m.get("hostname"), "RAM:", m.get("ram_used_gb"), "/", m.get("ram_total_gb"), "GB")
'

echo "🎉 QUÁ TRÌNH ROLLBACK HOÀN TẤT AN TOÀN!"

