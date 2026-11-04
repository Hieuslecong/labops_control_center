#!/usr/bin/env bash
set -e

# Chuyển vào thư mục gốc của dự án LabOps
cd "$(dirname "$0")"

echo "=========================================================="
echo "🚀 KHỞI ĐỘNG HỆ THỐNG LABOPS CONTROL CENTER"
echo "=========================================================="

# 1. Kiểm tra Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Lỗi: Không tìm thấy python3 trên máy chủ."
    exit 1
fi

# 2. Khởi động Backend API (Port 8000)
echo "📡 [1/2] Đang khởi động LabOps Backend API (Port 8000)..."
python3 backend/app/main.py &
BACKEND_PID=$!

# Đảm bảo tắt backend khi thoát kịch bản (Ctrl+C)
cleanup() {
    echo ""
    echo "🛑 Đang tắt hệ thống LabOps Control Center..."
    if [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# Chờ backend khởi động
sleep 1.5

# 3. Khởi động Frontend Next.js (Port 3000 / 3001)
if command -v bun &> /dev/null; then
    echo "💻 [2/2] Đang khởi động Frontend Next.js với Bun..."
    bun dev
elif command -v npm &> /dev/null; then
    echo "💻 [2/2] Đang khởi động Frontend Next.js với npm..."
    npm run dev
else
    echo "❌ Lỗi: Không tìm thấy bun hoặc npm để chạy giao diện Frontend."
    exit 1
fi
