#!/usr/bin/env bash
set -e

# Chuyển vào thư mục gốc của dự án LabOps
cd "$(dirname "$0")"

# Nạp biến môi trường từ .env.local và .env (nếu có)
set -a
[ -f .env ] && source .env
[ -f .env.local ] && source .env.local
set +a

# Thêm đường dẫn Bun / Node (kể cả Node đi kèm IDE Remote Server) vào PATH
IDE_NODE_DIR=$(dirname "$(find "$HOME/.antigravity-ide-server" "$HOME/.vscode-server" -name node -type f 2>/dev/null | head -n 1)" 2>/dev/null || true)
if [ -n "$IDE_NODE_DIR" ] && [ -d "$IDE_NODE_DIR" ]; then
    export PATH="$IDE_NODE_DIR:$PATH"
fi
export PATH="$HOME/.bun/bin:$HOME/.nvm/current/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
export PYTHONPATH="$(pwd)/backend:${PYTHONPATH:-}"

echo "=========================================================="
echo "🚀 KHỞI ĐỘNG HỆ THỐNG LABOPS CONTROL CENTER V2"
echo "=========================================================="

# 1. Kiểm tra Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Lỗi: Không tìm thấy python3 trên máy chủ."
    exit 1
fi

# 2. Giải phóng cổng 8000 nếu còn tiến trình cũ
fuser -k 8000/tcp 2>/dev/null || true

# 3. Khởi động Backend API (Port 8000)
echo "📡 [1/2] Đang khởi động LabOps Backend API V2 (http://127.0.0.1:8000)..."
python3 backend/app/main.py &
BACKEND_PID=$!

cleanup() {
    echo ""
    echo "🛑 Đang tắt hệ thống LabOps Control Center..."
    if [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

sleep 1.5

# 4. Khởi động Frontend Next.js (Port 3000)
if command -v bun &> /dev/null; then
    echo "💻 [2/2] Đang khởi động Dashboard Next.js với Bun tại http://localhost:3000 ..."
    bun dev
elif command -v node &> /dev/null && [ -f ./node_modules/next/dist/bin/next ]; then
    echo "💻 [2/2] Đang khởi động Dashboard Next.js với Node ($(node -v)) tại http://localhost:3000 ..."
    node ./node_modules/next/dist/bin/next dev
else
    echo "⚠️ Đang tải Bun trực tiếp bằng Python3 (không cần curl)..."
    python3 - <<'PYEOF'
import os, stat, urllib.request, zipfile
bun_dir = os.path.expanduser("~/.bun/bin")
os.makedirs(bun_dir, exist_ok=True)
zip_path = os.path.join(bun_dir, "bun.zip")
url = "https://github.com/oven-sh/bun/releases/latest/download/bun-linux-x64.zip"
urllib.request.urlretrieve(url, zip_path)
with zipfile.ZipFile(zip_path, "r") as zf:
    for m in zf.namelist():
        if m.endswith("/bun"):
            target = os.path.join(bun_dir, "bun")
            with zf.open(m) as src, open(target, "wb") as dst:
                dst.write(src.read())
            os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
os.remove(zip_path)
print("✅ Đã cài đặt Bun vào", os.path.join(bun_dir, "bun"))
PYEOF
    export PATH="$HOME/.bun/bin:$PATH"
    bun dev
fi
