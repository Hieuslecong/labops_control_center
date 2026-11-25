#!/usr/bin/env bash
# ==============================================================================
# Zero-Downtime Blue-Green Deployment for LabOps Control Center
# Builds & health-checks standby slot while active slot serves 100% traffic,
# then flips router proxy in 0ms without touching SSH containers, VPN, or GPUs.
# Usage:
#   ./scripts/deploy-zero-downtime.sh [--frontend-only | --backend-only | --skip-build]
# ==============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -d "$HOME/.nvm/versions/node" ]; then
  LATEST_NVM_BIN=$(find "$HOME/.nvm/versions/node" -maxdepth 2 -name bin -type d 2>/dev/null | sort -V | tail -n 1 || true)
  [ -n "$LATEST_NVM_BIN" ] && export PATH="$LATEST_NVM_BIN:$PATH"
fi
export PATH="$HOME/.bun/bin:$HOME/.local/bin:/usr/local/bin:$PATH"

set -a
[ -f .env ] && source .env
[ -f .env.local ] && source .env.local
set +a

RUNTIME_DIR="$PROJECT_DIR/.runtime"
STATE_FILE="$RUNTIME_DIR/active-slots.json"
LOG_DIR="$RUNTIME_DIR/logs"
mkdir -p "$RUNTIME_DIR" "$LOG_DIR"

PYTHON_BIN="$PROJECT_DIR/backend/.venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="python3"

DEPLOY_FE=true
DEPLOY_BE=true
SKIP_BUILD=false

for arg in "$@"; do
  case "$arg" in
    --frontend-only) DEPLOY_BE=false ;;
    --backend-only) DEPLOY_FE=false ;;
    --skip-build) SKIP_BUILD=true ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

proxy_running() {
  curl -sf http://127.0.0.1:3099/status >/dev/null 2>&1
}

read_active_slot() {
  local key="$1"
  local fallback="$2"
  node -e '
    const fs = require("fs");
    const file = process.argv[1];
    const key = process.argv[2];
    const fallback = process.argv[3];
    try {
      const data = JSON.parse(fs.readFileSync(file, "utf8"));
      process.stdout.write(String(data[key] ?? fallback));
    } catch {
      process.stdout.write(fallback);
    }
  ' "$STATE_FILE" "$key" "$fallback"
}

CUR_FE_SLOT="$(read_active_slot frontendSlot green)"
CUR_BE_SLOT="$(read_active_slot backendSlot green)"
CUR_FE_PORT="$(read_active_slot frontendPort 3002)"
CUR_BE_PORT="$(read_active_slot backendPort 8002)"

if [ "$CUR_FE_SLOT" = "blue" ]; then
  NEW_FE_SLOT="green"
  NEW_FE_PORT=3002
  OLD_FE_SLOT="blue"
  OLD_FE_PORT=3001
else
  NEW_FE_SLOT="blue"
  NEW_FE_PORT=3001
  OLD_FE_SLOT="green"
  OLD_FE_PORT=3002
fi

if [ "$CUR_BE_SLOT" = "blue" ]; then
  NEW_BE_SLOT="green"
  NEW_BE_PORT=8002
  OLD_BE_SLOT="blue"
  OLD_BE_PORT=8001
else
  NEW_BE_SLOT="blue"
  NEW_BE_PORT=8001
  OLD_BE_SLOT="green"
  OLD_BE_PORT=8002
fi

echo "🚀 Zero-Downtime Deploy (SSH containers / VPN / GPU jobs remain completely untouched)"

# 1. Build new Frontend in standby distDir BEFORE switching anything
if [ "$DEPLOY_FE" = true ] && [ "$SKIP_BUILD" = false ]; then
  DIST_DIR=".next-${NEW_FE_SLOT}"
  echo "📦 [1/4] Building Frontend into ${DIST_DIR} while current Web stays 100% online..."
  TSCONFIG_BACKUP="$(mktemp)"
  cp "$PROJECT_DIR/tsconfig.json" "$TSCONFIG_BACKUP"
  if ! NEXT_DIST_DIR="$DIST_DIR" bun run build; then
    cp "$TSCONFIG_BACKUP" "$PROJECT_DIR/tsconfig.json"
    rm -f "$TSCONFIG_BACKUP"
    echo "❌ Next.js build failed — active services untouched." >&2
    exit 1
  fi
  cp "$TSCONFIG_BACKUP" "$PROJECT_DIR/tsconfig.json"
  rm -f "$TSCONFIG_BACKUP"
elif [ "$DEPLOY_FE" = true ]; then
  DIST_DIR=".next-${NEW_FE_SLOT}"
  if [ ! -d "$PROJECT_DIR/$DIST_DIR/server" ]; then
    echo "❌ Missing built $DIST_DIR; run without --skip-build to build a clean slot." >&2
    exit 1
  fi
fi

# Stop any stale process sitting on the standby port before booting standby instance
kill_pid_file() {
  local pfile="$1"
  if [ -f "$pfile" ]; then
    local old_pid
    old_pid="$(cat "$pfile" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
      kill -TERM -- -"$old_pid" 2>/dev/null || kill -TERM "$old_pid" 2>/dev/null || true
      for _ in $(seq 1 30); do
        if ! kill -0 "$old_pid" 2>/dev/null; then
          break
        fi
        sleep 0.1
      done
      if kill -0 "$old_pid" 2>/dev/null; then
        kill -KILL -- -"$old_pid" 2>/dev/null || kill -KILL "$old_pid" 2>/dev/null || true
      fi
    fi
    rm -f "$pfile"
  fi
}

# 2. Boot standby Backend
TARGET_BE_SLOT="$CUR_BE_SLOT"
TARGET_BE_PORT="$(read_active_slot backendPort 8001)"
if [ "$DEPLOY_BE" = true ]; then
  kill_pid_file "$RUNTIME_DIR/backend-${NEW_BE_SLOT}.pid"
  echo "⚙️  [2/4] Starting standby Backend (${NEW_BE_SLOT} on 127.0.0.1:${NEW_BE_PORT})..."
  setsid env PYTHONPATH="$PROJECT_DIR/backend:${PYTHONPATH:-}" "$PYTHON_BIN" \
    -m uvicorn app.main:app --app-dir "$PROJECT_DIR/backend" \
    --host 127.0.0.1 --port "$NEW_BE_PORT" \
    > "$LOG_DIR/backend-${NEW_BE_SLOT}.log" 2>&1 < /dev/null &
  NEW_BE_PID=$!
  echo "$NEW_BE_PID" > "$RUNTIME_DIR/backend-${NEW_BE_SLOT}.pid"

  be_ready=false
  for _ in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${NEW_BE_PORT}/healthz" | grep -q '"status":"ok"'; then
      be_ready=true
      break
    fi
    sleep 0.5
  done
  if [ "$be_ready" != true ]; then
    echo "❌ Standby Backend ${NEW_BE_SLOT} failed health check; rolling back." >&2
    kill "$NEW_BE_PID" 2>/dev/null || true
    tail -n 20 "$LOG_DIR/backend-${NEW_BE_SLOT}.log" || true
    exit 1
  fi
  TARGET_BE_SLOT="$NEW_BE_SLOT"
  TARGET_BE_PORT="$NEW_BE_PORT"
  echo "✅ Standby Backend ${NEW_BE_SLOT}:${NEW_BE_PORT} healthy."
fi

# 3. Boot standby Frontend
TARGET_FE_SLOT="$CUR_FE_SLOT"
TARGET_FE_PORT="$(read_active_slot frontendPort 3001)"
if [ "$DEPLOY_FE" = true ]; then
  kill_pid_file "$RUNTIME_DIR/frontend-${NEW_FE_SLOT}.pid"
  echo "🌐 [3/4] Starting standby Frontend (${NEW_FE_SLOT} on 127.0.0.1:${NEW_FE_PORT}, dist=${DIST_DIR})..."
  setsid env NEXT_DIST_DIR="$DIST_DIR" PORT="$NEW_FE_PORT" HOSTNAME=127.0.0.1 \
    ./node_modules/.bin/next start -p "$NEW_FE_PORT" -H 127.0.0.1 \
    > "$LOG_DIR/frontend-${NEW_FE_SLOT}.log" 2>&1 < /dev/null &
  NEW_FE_PID=$!
  echo "$NEW_FE_PID" > "$RUNTIME_DIR/frontend-${NEW_FE_SLOT}.pid"

  fe_ready=false
  for _ in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${NEW_FE_PORT}/auth/sign-in" || true)"
    if [ "$code" = "200" ]; then
      fe_ready=true
      break
    fi
    sleep 0.5
  done
  if [ "$fe_ready" != true ]; then
    echo "❌ Standby Frontend ${NEW_FE_SLOT} failed readiness check; rolling back." >&2
    kill "$NEW_FE_PID" 2>/dev/null || true
    [ "$DEPLOY_BE" = true ] && kill_pid_file "$RUNTIME_DIR/backend-${NEW_BE_SLOT}.pid"
    tail -n 20 "$LOG_DIR/frontend-${NEW_FE_SLOT}.log" || true
    exit 1
  fi
  TARGET_FE_SLOT="$NEW_FE_SLOT"
  TARGET_FE_PORT="$NEW_FE_PORT"
  echo "✅ Standby Frontend ${NEW_FE_SLOT}:${NEW_FE_PORT} ready (HTTP 200)."
fi

# 4. Atomic Proxy Switch (0ms cutover) or initial Proxy bootstrap
if proxy_running; then
  echo "🔄 [4/4] Switching Blue-Green Proxy atomically -> FE ${TARGET_FE_SLOT}:${TARGET_FE_PORT}, BE ${TARGET_BE_SLOT}:${TARGET_BE_PORT}..."
  curl -sf -X POST http://127.0.0.1:3099/switch \
    -H "Content-Type: application/json" \
    -d "{\"frontendSlot\":\"${TARGET_FE_SLOT}\",\"frontendPort\":${TARGET_FE_PORT},\"backendSlot\":\"${TARGET_BE_SLOT}\",\"backendPort\":${TARGET_BE_PORT}}" \
    >/dev/null

  proxy_fe_ok=false
  proxy_be_ok=false
  for _ in $(seq 1 6); do
    if [ "$DEPLOY_FE" = true ] && [ -n "${NEW_FE_PID:-}" ] && ! kill -0 "$NEW_FE_PID" 2>/dev/null; then
      break
    fi
    fe_code="$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:3000/auth/sign-in" || true)"
    if [ "$fe_code" = "200" ]; then
      proxy_fe_ok=true
    else
      proxy_fe_ok=false
    fi
    if curl -sf "http://127.0.0.1:8000/healthz" | grep -q '"status":"ok"'; then
      proxy_be_ok=true
    else
      proxy_be_ok=false
    fi
    if [ "$proxy_fe_ok" = true ] && [ "$proxy_be_ok" = true ]; then
      break
    fi
    sleep 0.5
  done

  if [ "$proxy_fe_ok" != true ] || [ "$proxy_be_ok" != true ]; then
    echo "❌ Post-switch health check failed via :3000/:8000 (FE=${proxy_fe_ok}, BE=${proxy_be_ok}); rolling back proxy to ${CUR_FE_SLOT}:${CUR_FE_PORT} / ${CUR_BE_SLOT}:${CUR_BE_PORT}!" >&2
    curl -sf -X POST http://127.0.0.1:3099/switch \
      -H "Content-Type: application/json" \
      -d "{\"frontendSlot\":\"${CUR_FE_SLOT}\",\"frontendPort\":${CUR_FE_PORT},\"backendSlot\":\"${CUR_BE_SLOT}\",\"backendPort\":${CUR_BE_PORT}}" \
      >/dev/null || true
    [ "$DEPLOY_FE" = true ] && {
      tail -n 20 "$LOG_DIR/frontend-${NEW_FE_SLOT}.log" >&2 || true
      kill_pid_file "$RUNTIME_DIR/frontend-${NEW_FE_SLOT}.pid"
    }
    [ "$DEPLOY_BE" = true ] && {
      tail -n 20 "$LOG_DIR/backend-${NEW_BE_SLOT}.log" >&2 || true
      kill_pid_file "$RUNTIME_DIR/backend-${NEW_BE_SLOT}.pid"
    }
    exit 1
  fi

  echo "✅ Proxy switched & verified via :3000/:8000 (0ms downtime). Draining previous slots for 3s..."
  sleep 3
  [ "$DEPLOY_FE" = true ] && kill_pid_file "$RUNTIME_DIR/frontend-${OLD_FE_SLOT}.pid"
  [ "$DEPLOY_BE" = true ] && kill_pid_file "$RUNTIME_DIR/backend-${OLD_BE_SLOT}.pid"
else
  echo "🔀 [4/4] Bootstrapping Blue-Green Proxy on :3000 & :8000..."
  cat > "$STATE_FILE" <<EOF
{
  "frontendSlot": "${TARGET_FE_SLOT}",
  "frontendPort": ${TARGET_FE_PORT},
  "backendSlot": "${TARGET_BE_SLOT}",
  "backendPort": ${TARGET_BE_PORT},
  "updatedAt": "$(date -Iseconds)"
}
EOF
  # Stop legacy single-instance listeners holding :3000 or :8000 (never touching 9router or containers)
  LEGACY_PIDS="$(ss -tlnp 2>/dev/null | grep -E ':3000 |:8000 ' | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)"
  if [ -n "$LEGACY_PIDS" ]; then
    echo "$LEGACY_PIDS" | xargs -r kill 2>/dev/null || true
    sleep 1
  fi
  setsid node "$PROJECT_DIR/scripts/bluegreen-proxy.mjs" > "$LOG_DIR/proxy.log" 2>&1 < /dev/null &
  echo $! > "$RUNTIME_DIR/proxy.pid"
  sleep 1
  if ! proxy_running; then
    echo "❌ Failed to start bluegreen-proxy.mjs" >&2
    tail -n 20 "$LOG_DIR/proxy.log" || true
    exit 1
  fi
  echo "✅ Blue-Green Proxy active on :3000 (FE ${TARGET_FE_SLOT}:${TARGET_FE_PORT}) & :8000 (BE ${TARGET_BE_SLOT}:${TARGET_BE_PORT})."
fi

# Refresh Zalo worker cleanly without duplicating systemd-managed worker (single-consumer DB lease)
if [ "$DEPLOY_BE" = true ] && [ -n "${ZALO_BOT_TOKEN:-}" ]; then
  if systemctl is-enabled --quiet labops-ai-sre-worker 2>/dev/null || systemctl is-active --quiet labops-ai-sre-worker 2>/dev/null; then
    # Clean up any stray worker.py launched outside systemd previously
    pkill -TERM -f "backend/app/services/zalo_assistant/worker.py" 2>/dev/null || true
    # Stop existing systemd worker so Restart=always spawns a single fresh instance cleanly
    ZALO_MOD_PIDS="$(ps -eo pid,cmd | awk '/app\.services\.zalo_assistant\.worker/ {if ($0 !~ /awk/) print $1}')"
    if [ -n "$ZALO_MOD_PIDS" ]; then
      echo "$ZALO_MOD_PIDS" | xargs -r kill -TERM 2>/dev/null || true
    fi
    echo "ℹ️  Triggered clean systemd restart for labops-ai-sre-worker (single-instance DB lease)."
  else
    ZALO_PIDS="$(ps -eo pid,cmd | awk '/zalo_assistant(\.worker|\/worker\.py)/ {if ($0 !~ /awk/) print $1}')"
    if [ -n "$ZALO_PIDS" ]; then
      echo "$ZALO_PIDS" | xargs -r kill -TERM 2>/dev/null || true
      sleep 1
    fi
    setsid env PYTHONPATH="$PROJECT_DIR/backend:${PYTHONPATH:-}" "$PYTHON_BIN" \
      -m app.services.zalo_assistant.worker \
      >> "$PROJECT_DIR/backend/zalo_worker.log" 2>&1 < /dev/null &
  fi
fi

echo "🎉 Deploy finished cleanly!"
