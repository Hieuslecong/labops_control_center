#!/usr/bin/env bash
set -e

# Chuyển vào thư mục gốc của dự án LabOps
cd "$(dirname "$0")"
REPO_ROOT="$(pwd -P)"

if [ "${LABOPS_ALLOW_LOCAL_START:-}" != "true" ]; then
    echo "Từ chối khởi động dịch vụ từ start.sh nếu chưa đặt LABOPS_ALLOW_LOCAL_START=true."
    exit 1
fi

# Chỉ nạp cấu hình clone theo lựa chọn tường minh; không kế thừa credential production.
if [ "${LABOPS_LOAD_LOCAL_ENV:-}" = "true" ]; then
    for env_file in .env .env.local; do
        if [ -f "${env_file}" ]; then
            env_real="$(realpath "${env_file}")"
            case "${env_real}" in
                "${REPO_ROOT}"/*) ;;
                *) echo "Từ chối nạp env nằm ngoài clone: ${env_file}"; exit 1 ;;
            esac
            set -a
            source "${env_real}"
            set +a
        fi
    done
else
    unset LABOPS_SESSION_SECRET LABOPS_AUTH_SECRET LABOPS_ADMIN_API_TOKEN LABOPS_ADMIN_PASSWORD LABOPS_GATEWAY_CONTROL_SECRET
    unset LABOPS_GATEWAY_TOKEN_SECRET LABOPS_NODE_AGENT_TOKEN LABOPS_NODE_AGENT_TOKENS LABOPS_AGENT_SECRET
    unset DATABASE_URL POSTGRES_URL WG_EASY_PASSWORD WG_EASY_ADMIN_PASSWORD WG_EASY_ADMIN_USER CLERK_SECRET_KEY SENTRY_AUTH_TOKEN
    unset LABOPS_AGENT_URL LABOPS_BACKEND_URL LABOPS_GATEWAY_HTTP_URL
    unset LABOPS_USER_DATA_DIR LABOPS_STORAGE_DATA_PATH TRACK_SERVER_URL WORKSTATION_SERVER_URL
    unset NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY NEXT_PUBLIC_DEMO_MODE
    export LABOPS_MODE=demo
    export LABOPS_ALLOW_MOCK_DATA=true
    export LABOPS_LOCAL_HOST_ID=clone
    export LOCAL_HOST_ID=clone
    export NEXT_PUBLIC_DEMO_MODE=true
    export LABOPS_USER_DATA_DIR="${REPO_ROOT}/backend/data/dev-users"
    export LABOPS_STORAGE_DATA_PATH="${REPO_ROOT}/backend/data/dev-storage"
    export WG_EASY_ENABLED=false
    export WG_EASY_API_URL=http://127.0.0.1:1
    export WG_EASY_PUBLIC_ENDPOINT=localhost:1
    export TRACK_SERVER_URL=http://127.0.0.1:1
    export WORKSTATION_SERVER_URL=http://127.0.0.1:1
    export NETDATA_TRACK_URL=http://127.0.0.1:1
    export NETDATA_WS_URL=http://127.0.0.1:1
fi

# The clone binds only to loopback and uses ports separate from the Rack services.
export LABOPS_BACKEND_PORT="${LABOPS_BACKEND_PORT:-18000}"
export LABOPS_BIND_HOST=127.0.0.1
export LABOPS_BACKEND_URL="http://127.0.0.1:${LABOPS_BACKEND_PORT}"
export LABOPS_FRONTEND_PORT="${LABOPS_FRONTEND_PORT:-3012}"

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

# The dashboard runner never starts or probes a host Node Agent. Use a dedicated
# fake Agent in isolated integration tests; an existing Agent may be production.
echo "ℹ️ [1/3] Không khởi động hoặc dò Node Agent từ start.sh."

# 4. Chỉ sử dụng lại backend nếu health endpoint xác nhận đúng dịch vụ LabOps.
BACKEND_PID=""
backend_healthy() {
    "$BACKEND_PYTHON" -c 'import json, os, sys, urllib.request
try:
    port = int(os.environ["LABOPS_BACKEND_PORT"])
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
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
    if [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if "$BACKEND_PYTHON" -c 'import os, socket, sys
s = socket.socket()
s.settimeout(1)
try:
    sys.exit(0 if s.connect_ex(("127.0.0.1", int(os.environ["LABOPS_BACKEND_PORT"]))) == 0 else 1)
finally:
    s.close()'; then
    echo "❌ Cổng clone ${LABOPS_BACKEND_PORT} đã được sử dụng; không kết nối hoặc dừng tiến trình hiện có."
    exit 1
fi

echo "📡 [2/3] Đang khởi động Backend clone trên http://127.0.0.1:${LABOPS_BACKEND_PORT}..."
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
    echo "❌ Backend clone chưa sẵn sàng; tiến trình hiện có không bị dừng."
    exit 1
fi

# 5. Khởi động Frontend Next.js trên loopback với port clone riêng.
if command -v bun &> /dev/null; then
    echo "💻 [3/3] Đang khởi động Dashboard clone với Bun tại http://127.0.0.1:${LABOPS_FRONTEND_PORT} ..."
    bun dev --hostname 127.0.0.1 --port "$LABOPS_FRONTEND_PORT"
elif command -v node &> /dev/null && [ -f ./node_modules/next/dist/bin/next ]; then
    echo "💻 [3/3] Đang khởi động Dashboard clone với Node ($(node -v)) tại http://127.0.0.1:${LABOPS_FRONTEND_PORT} ..."
    node ./node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port "$LABOPS_FRONTEND_PORT"
else
    echo "❌ Frontend dependencies are not installed in this clone; install them in an isolated development environment first."
    exit 1
fi
