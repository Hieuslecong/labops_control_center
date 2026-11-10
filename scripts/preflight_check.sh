#!/usr/bin/env bash
# ============================================================================
# LabOps Control Center V2 — Preflight Verification Script
# Kiểm tra an toàn môi trường trước khi khởi chạy hoặc triển khai Production.
# Không sửa đổi dịch vụ hệ thống, không chạm vào wg0/OpenSSH/NVIDIA driver.
# ============================================================================
set -euo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

ok()   { echo "[PASS] $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { echo "[WARN] $1"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { echo "[FAIL] $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

echo "=== LabOps Control Center V2 Preflight Check ==="

# 1. Kiểm tra Python
if command -v python3 >/dev/null 2>&1; then
  ok "Tìm thấy python3: $(python3 --version 2>&1)"
else
  fail "Không tìm thấy python3"
fi

# 2. Kiểm tra GPU & NVIDIA Driver (chỉ đọc)
if command -v nvidia-smi >/dev/null 2>&1; then
  GPU_LINES=$(nvidia-smi --query-gpu=index,name,uuid,memory.total --format=csv,noheader 2>/dev/null | wc -l || echo 0)
  if [ "$GPU_LINES" -gt 0 ]; then
    ok "Phát hiện ${GPU_LINES} GPU vật lý qua nvidia-smi"
  else
    warn "Có nvidia-smi nhưng không đọc được danh sách GPU"
  fi
else
  warn "Không tìm thấy nvidia-smi trên máy chủ này"
fi

# 3. Kiểm tra Trivy
if command -v trivy >/dev/null 2>&1; then
  ok "Tìm thấy Trivy scanner: $(trivy --version 2>&1 | head -n 1)"
else
  warn "Chưa cài đặt Trivy (quét mã nguồn sẽ báo FAILED: NOT_CONFIGURED, không giả lập PASSED)"
fi

# 4. Kiểm tra các tệp migration SQL V2
if [ -f "backend/migrations/004_labops_v2_complete.sql" ]; then
  ok "Tồn tại migration SQL V2: backend/migrations/004_labops_v2_complete.sql"
else
  fail "Thiếu tệp backend/migrations/004_labops_v2_complete.sql"
fi

# 5. Kiểm tra docker-compose không phơi cổng 5432 ra 0.0.0.0
if grep -q '"5432:5432"' docker-compose.yml 2>/dev/null; then
  fail "docker-compose.yml đang phơi cổng 5432 ra 0.0.0.0"
else
  ok "docker-compose.yml không phơi cổng PostgreSQL 5432 ra 0.0.0.0"
fi

echo "=== Tổng kết: PASS=${PASS_COUNT} | WARN=${WARN_COUNT} | FAIL=${FAIL_COUNT} ==="
if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi

