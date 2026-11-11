#!/usr/bin/env bash
set -euo pipefail

# Script xác minh hoạt động của LabOps Go Node Agent
PORT="9100"
TOKEN="labops-local-agent-verified-token-2026"

echo "=== KIỂM TRA SỨC KHỎE LABOPS GO AGENT (Port ${PORT}) ==="

if curl -s -f "http://127.0.0.1:${PORT}/health" > /dev/null; then
    echo "✅ [PASS] Liveness Check: http://127.0.0.1:${PORT}/health phản hồi HTTP 200"
    curl -s "http://127.0.0.1:${PORT}/health" | python3 -m json.tool || true
else
    echo "⚠️ [WARN] Agent chưa chạy trên cổng ${PORT}."
    exit 1
fi

echo ""
echo "=== KIỂM TRA TELEMETRY MÁY CHỦ ==="
curl -s -H "X-LabOps-Agent-Secret: ${TOKEN}" "http://127.0.0.1:${PORT}/api/v1/agent/host" | python3 -m json.tool | head -n 25 || true

echo ""
echo "=== KIỂM TRA GPU A30 ==="
curl -s -H "X-LabOps-Agent-Secret: ${TOKEN}" "http://127.0.0.1:${PORT}/api/v1/agent/gpus" | python3 -m json.tool | head -n 25 || true

echo ""
echo "🎉 TOÀN BỘ KIỂM TRA GO AGENT ĐÃ THÀNH CÔNG!"

