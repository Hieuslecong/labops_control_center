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

# 2. Chọn môi trường Python và kiểm tra dependency trước khi khởi động
BACKEND_PYTHON=python3
if [ -x backend/.venv/bin/python ]; then
    BACKEND_PYTHON=backend/.venv/bin/python
fi
if ! "$BACKEND_PYTHON" -c 'import fastapi, uvicorn, pydantic_settings, psutil' 2>/dev/null; then
    echo "❌ Backend thiếu dependency. Cài môi trường Python bằng các lệnh:"
    echo "   sudo apt install python3-venv"
    echo "   python3 -m venv backend/.venv"
    echo "   backend/.venv/bin/python -m pip install -r backend/requirements.txt"
    exit 1
fi

# 3. Kiểm tra hoặc khởi động LabOps Go Node Agent (Port 9100)
AGENT_PID=""
if curl -s http://127.0.0.1:9100/health >/dev/null 2>&1; then
    echo "✅ [1/3] LabOps Go Node Agent đang hoạt động (http://127.0.0.1:9100)"
elif [ -x ./bin/labops-agent ]; then
    echo "⚡ [1/3] Khởi động LabOps Go Node Agent (http://127.0.0.1:9100)..."
    ./bin/labops-agent &
    AGENT_PID=$!
    sleep 0.5
fi

# 4. Chỉ sử dụng lại backend nếu health endpoint xác nhận đúng dịch vụ LabOps.
BACKEND_PID=""
backend_healthy() {
    "$BACKEND_PYTHON" -c 'import json, sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2) as response:
        data = json.load(response)
    sys.exit(0 if data.get("status") == "ok" and data.get("app") == "LabOps Control Center API" else 1)
except Exception:
    sys.exit(1)' 2>/dev/null
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    echo ""
    echo "🛑 Đang tắt các tiến trình LabOps do script này khởi động..."
    if [ -n "$AGENT_PID" ]; then
        kill "$AGENT_PID" 2>/dev/null || true
    fi
    if [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if backend_healthy; then
    echo "✅ [2/3] Backend LabOps đang hoạt động (http://127.0.0.1:8000)"
else
    # Không tự kill tiến trình đang chạy: có thể là dịch vụ khác hoặc backend đang khởi động.
    if ! "$BACKEND_PYTHON" -c 'import socket, sys
s = socket.socket()
s.settimeout(1)
try:
    sys.exit(0 if s.connect_ex(("127.0.0.1", 8000)) == 0 else 1)
finally:
    s.close()'; then
        echo "📡 [2/3] Đang khởi động LabOps Backend API V2 (http://127.0.0.1:8000)..."
        "$BACKEND_PYTHON" backend/app/main.py &
        BACKEND_PID=$!
        ready=false
        for attempt in {1..30}; do
            if backend_healthy; then
                ready=true
                break
            fi
            if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        if [ "$ready" != true ]; then
            echo "❌ Backend chưa sẵn sàng; kiểm tra lỗi bên trên và tiến trình đang dùng cổng 8000."
            exit 1
        fi
    else
        echo "❌ Cổng 8000 đang được sử dụng nhưng không xác nhận được backend LabOps."
        echo "   Kiểm tra bằng: ss -ltnp 'sport = :8000'"
        echo "   Script không tự dừng tiến trình đang chạy."
        exit 1
    fi
fi

# 5. Khởi động Frontend Next.js (Port 3000)
if command -v bun &> /dev/null; then
    echo "💻 [3/3] Đang khởi động Dashboard Next.js với Bun tại http://localhost:3000 ..."
    bun dev
elif command -v node &> /dev/null && [ -f ./node_modules/next/dist/bin/next ]; then
    echo "💻 [3/3] Đang khởi động Dashboard Next.js với Node ($(node -v)) tại http://localhost:3000 ..."
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
