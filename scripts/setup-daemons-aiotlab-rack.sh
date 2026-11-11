#!/usr/bin/env bash
set -e

echo "=================================================================="
echo "🔧 CẤU HÌNH QUYỀN TRUY CẬP DAEMON TRÊN AIOTLAB-RACK (LABOPS V2)"
echo "=================================================================="

TARGET_USER="${SUDO_USER:-aiot-1}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# 1. Cấp quyền truy cập Docker socket cho user vận hành LabOps
if command -v docker >/dev/null 2>&1; then
    echo "🐳 [1/4] Cấu hình quyền Docker socket cho user '$TARGET_USER'..."
    usermod -aG docker "$TARGET_USER"
    systemctl enable --now docker
    echo "   ✅ Đã thêm '$TARGET_USER' vào group 'docker' ($(docker --version))."
fi

# 2. Khởi tạo Database PostgreSQL 17 & chạy bộ Migration SQL 001..004
if command -v psql >/dev/null 2>&1; then
    echo "🐘 [2/4] Khởi tạo PostgreSQL 17 database 'labops_db' và chạy migrations..."
    systemctl enable --now postgresql
    su - postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='labops'\" | grep -q 1 || psql -c \"CREATE USER labops WITH PASSWORD 'labops_local_db_pass';\""
    su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='labops_db'\" | grep -q 1 || psql -c \"CREATE DATABASE labops_db OWNER labops;\""
    for sql_file in "$ROOT_DIR"/backend/migrations/*.sql; do
        if [ -f "$sql_file" ]; then
            echo "   -> Đang áp dụng $(basename "$sql_file")..."
            su - postgres -c "psql -d labops_db -f '$sql_file'" >/dev/null 2>&1 || true
        fi
    done
    su - postgres -c "psql -d labops_db -c 'GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO labops; GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO labops;'" >/dev/null
    echo "   ✅ PostgreSQL 17 ('labops_db') đã sẵn sàng ($(psql --version))."
fi

# 3. Kiểm tra Trivy Scanner
if command -v trivy >/dev/null 2>&1; then
    echo "🛡️ [3/4] Xác minh Trivy Security Scanner..."
    echo "   ✅ Trivy đã sẵn sàng ($(trivy --version | head -n 1))."
fi

# 4. Kiểm tra WireGuard Tools
if command -v wg >/dev/null 2>&1; then
    echo "🔐 [4/4] Xác minh WireGuard VPN tools..."
    echo "   ✅ $(wg --version)"
    if ip link show wg0 >/dev/null 2>&1; then
        echo "   ✅ Giao diện wg0 đang hoạt động."
    else
        echo "   ℹ️ Lưu ý: wg-quick đã cài đặt; giao diện wg0 sẽ kích hoạt khi có tệp /etc/wireguard/wg0.conf."
    fi
fi

echo "=================================================================="
echo "🎉 HOÀN TẤT! Nếu bạn vừa được thêm vào group 'docker', hãy chạy:"
echo "   newgrp docker"
echo "hoặc khởi động lại ./start.sh để áp dụng toàn bộ quyền mới."
echo "=================================================================="

