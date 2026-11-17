#!/usr/bin/env bash
# ==============================================================================
# LabOps Control Center - Script Khởi động Toàn bộ Hệ thống trên Workstation
# Tự động điều phối: 9Router AI Gateway -> Go Node Agent -> FastAPI Backend -> Next.js UI
# ==============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Màu hiển thị console
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${CYAN}${BOLD}==============================================================================${NC}"
echo -e "${CYAN}${BOLD}🚀 KHỞI ĐỘNG LABOPS CONTROL CENTER — WORKSTATION PRIMARY (aiot-ws1)${NC}"
echo -e "${CYAN}${BOLD}==============================================================================${NC}"

# 1. Cấu hình PATH tự động (NVM Node, Bun, Python Venv, Go)
if [ -d "$HOME/.nvm/versions/node" ]; then
    LATEST_NVM_BIN=$(find "$HOME/.nvm/versions/node" -maxdepth 2 -name bin -type d 2>/dev/null | sort -V | tail -n 1 || true)
    if [ -n "$LATEST_NVM_BIN" ]; then
        export PATH="$LATEST_NVM_BIN:$PATH"
    fi
fi
export PATH="$HOME/.bun/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
export PYTHONPATH="$PROJECT_DIR/backend:${PYTHONPATH:-}"

# 2. Khởi tạo và nạp biến môi trường (.env.local / .env)
if [ ! -f .env.local ] && [ ! -f .env ]; then
    echo -e "${YELLOW}⚙️  Chưa tìm thấy .env.local. Đang tự động tạo từ .env.workstation.example...${NC}"
    if [ -f .env.workstation.example ]; then
        cp .env.workstation.example .env.local
        # Tự sinh chuỗi bảo mật ngẫu nhiên nếu có openssl / python
        RAND_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32 2>/dev/null || echo "labops-local-session-secret-2026-secure")
        sed -i "s/REPLACE_WITH_OPENSSL_RAND_HEX_32/$RAND_SECRET/g" .env.local 2>/dev/null || true
        echo -e "${GREEN}✅ Đã tạo tệp cấu hình an toàn: .env.local${NC}"
    fi
fi

set -a
[ -f .env ] && source .env
[ -f .env.local ] && source .env.local
set +a

# 3. Quản lý dọn dẹp tiến trình con khi dừng script (Graceful Shutdown)
STARTED_ROUTER_PID=""
STARTED_AGENT_PID=""
STARTED_BACKEND_PID=""

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM
    echo ""
    echo -e "${YELLOW}🛑 Đang dừng an toàn các tiến trình do script này khởi động...${NC}"
    if [ -n "$STARTED_BACKEND_PID" ]; then
        echo -e "   • Dừng FastAPI Backend (PID: $STARTED_BACKEND_PID)"
        kill "$STARTED_BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$STARTED_AGENT_PID" ]; then
        echo -e "   • Dừng Go Node Agent (PID: $STARTED_AGENT_PID)"
        kill "$STARTED_AGENT_PID" 2>/dev/null || true
    fi
    if [ -n "$STARTED_ROUTER_PID" ]; then
        echo -e "   • Dừng 9Router Gateway (PID: $STARTED_ROUTER_PID)"
        kill "$STARTED_ROUTER_PID" 2>/dev/null || true
    fi
    echo -e "${GREEN}✅ Đã giải phóng tài nguyên. Host GPU và các tiến trình nền giữ nguyên an toàn.${NC}"
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# ------------------------------------------------------------------------------
# DỊCH VỤ 1: 9ROUTER AI GATEWAY (Cổng 20128)
# ------------------------------------------------------------------------------
echo -e "\n${BLUE}[1/4] Kiểm tra 9Router AI Gateway (http://127.0.0.1:20128)...${NC}"
if curl -s -f http://127.0.0.1:20128/v1/models >/dev/null 2>&1; then
    echo -e "${GREEN}✅ 9Router AI Gateway đang hoạt động trên cổng 20128 (Model: model_free).${NC}"
else
    if command -v 9router &>/dev/null; then
        echo -e "${YELLOW}⚡ Đang khởi động 9Router AI Gateway cục bộ...${NC}"
        9router -p 20128 --tray --skip-update >/dev/null 2>&1 &
        STARTED_ROUTER_PID=$!
        sleep 2
        if curl -s -f http://127.0.0.1:20128/v1/models >/dev/null 2>&1; then
            echo -e "${GREEN}✅ 9Router đã sẵn sàng trên http://127.0.0.1:20128.${NC}"
        else
            echo -e "${YELLOW}⚠️  9Router đang khởi chạy nền; tiếp tục tiến trình.${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  Không tìm thấy lệnh 9router trong PATH. Bỏ qua bước tự khởi động 9Router.${NC}"
    fi
fi

# ------------------------------------------------------------------------------
# DỊCH VỤ 2: LABOPS GO NODE AGENT (Cổng 9100)
# ------------------------------------------------------------------------------
echo -e "\n${BLUE}[2/4] Kiểm tra LabOps Go Node Agent (http://127.0.0.1:9100)...${NC}"
if curl -s -f http://127.0.0.1:9100/health >/dev/null 2>&1; then
    echo -e "${GREEN}✅ LabOps Go Node Agent đang chạy trên cổng 9100.${NC}"
else
    AGENT_BIN=""
    if [ -x /usr/local/bin/labops-agent ]; then
        AGENT_BIN="/usr/local/bin/labops-agent"
    elif [ -x "$PROJECT_DIR/go/bin/labops-agent" ]; then
        AGENT_BIN="$PROJECT_DIR/go/bin/labops-agent"
    elif [ -x "$PROJECT_DIR/bin/labops-agent" ]; then
        AGENT_BIN="$PROJECT_DIR/bin/labops-agent"
    fi

    if [ -n "$AGENT_BIN" ]; then
        echo -e "${YELLOW}⚡ Khởi động LabOps Go Node Agent ở chế độ Standalone Workstation...${NC}"
        LABOPS_SERVER_ID="workstation" \
        LABOPS_SERVER_NAME="Workstation 01 (aiot-ws1 • Cục bộ)" \
        LABOPS_GATEWAY_ENABLED=false \
        LABOPS_AGENT_BIND_ADDR="127.0.0.1" \
        LABOPS_AGENT_PORT=9100 \
        "$AGENT_BIN" >/dev/null 2>&1 &
        STARTED_AGENT_PID=$!
        sleep 1
        echo -e "${GREEN}✅ LabOps Node Agent đã khởi động (PID: $STARTED_AGENT_PID).${NC}"
    else
        echo -e "${YELLOW}⚠️  Chưa có binary labops-agent; bỏ qua khởi động agent cục bộ.${NC}"
    fi
fi

# ------------------------------------------------------------------------------
# DỊCH VỤ 3: FASTAPI BACKEND API (Cổng 8000)
# ------------------------------------------------------------------------------
echo -e "\n${BLUE}[3/4] Kiểm tra LabOps FastAPI Backend (http://127.0.0.1:8000)...${NC}"
PYTHON_BIN="python3"
if [ -x "$PROJECT_DIR/backend/.venv/bin/python" ]; then
    PYTHON_BIN="$PROJECT_DIR/backend/.venv/bin/python"
fi

is_backend_healthy() {
    curl -s http://127.0.0.1:8000/healthz 2>/dev/null | grep -q '"status":"ok"'
}

if is_backend_healthy; then
    echo -e "${GREEN}✅ LabOps Backend API V2 đang hoạt động trên cổng 8000.${NC}"
else
    echo -e "${YELLOW}⚡ Đang khởi động LabOps FastAPI Backend API...${NC}"
    "$PYTHON_BIN" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 >/dev/null 2>&1 &
    STARTED_BACKEND_PID=$!

    # Đợi backend phản hồi health check (tối đa 15 giây)
    ready=false
    for i in {1..15}; do
        if is_backend_healthy; then
            ready=true
            break
        fi
        sleep 1
    done

    if [ "$ready" = true ]; then
        echo -e "${GREEN}✅ FastAPI Backend đã sẵn sàng tại http://127.0.0.1:8000 (PID: $STARTED_BACKEND_PID).${NC}"
    else
        echo -e "${RED}❌ Không thể khởi động Backend API cổng 8000. Vui lòng kiểm tra log.${NC}"
        exit 1
    fi
fi

# ------------------------------------------------------------------------------
# DỊCH VỤ 4: DASHBOARD FRONTEND NEXT.JS 16 (Cổng 3000)
# ------------------------------------------------------------------------------
echo -e "\n${BLUE}[4/4] Khởi động Dashboard Frontend Next.js 16...${NC}"
echo -e "${CYAN}------------------------------------------------------------------------------${NC}"
echo -e "${GREEN}${BOLD}🌐 HỆ THỐNG ĐÃ SẴN SÀNG TRUY CẬP TẠI:${NC}"
echo -e "   • Giao diện Dashboard: ${BOLD}http://localhost:3000${NC}"
echo -e "   • Trợ lý AI & Zalo:   ${BOLD}http://localhost:3000/dashboard/ai-assistant${NC}"
echo -e "   • Backend REST API:    ${BOLD}http://127.0.0.1:8000/api/v1/docs${NC}"
echo -e "   • 9Router Gateway:     ${BOLD}http://127.0.0.1:20128/v1 (Model: model_free)${NC}"
echo -e "${CYAN}------------------------------------------------------------------------------${NC}"
echo -e "${YELLOW}Nhấn Ctrl+C để dừng toàn bộ hệ thống bất kỳ lúc nào.${NC}\n"

# Kiểm tra cờ tham số --prod để chạy chế độ production hoặc dev
if [ "$1" == "--prod" ]; then
    echo -e "${GREEN}Chạy chế độ Production (bun start)...${NC}"
    if command -v bun &>/dev/null; then
        exec bun start -p 3000
    else
        exec node ./node_modules/next/dist/bin/next start -p 3000
    fi
else
    echo -e "${GREEN}Chạy chế độ Phát triển (bun dev)...${NC}"
    if command -v bun &>/dev/null; then
        exec bun dev
    else
        exec node ./node_modules/next/dist/bin/next dev
    fi
fi
