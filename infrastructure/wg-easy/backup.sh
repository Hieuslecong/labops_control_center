#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${1:-$HOME/labops_backups/wg-easy}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
SNAP_DIR="$BACKUP_DIR/snapshot_$TIMESTAMP"
DEST_FILE="$BACKUP_DIR/wg_easy_data_$TIMESTAMP.tar.gz"
CONTAINER_NAME="${WG_EASY_CONTAINER_NAME:-labops-wg-easy}"

mkdir -p "$SNAP_DIR"
chmod 700 "$BACKUP_DIR" "$SNAP_DIR"

echo "=== SAO LƯU AN TOÀN DỮ LIỆU WG-EASY V15 ==="
echo "Đang sao lưu persistent config & SQLite (chế độ đọc nhất quán)..."

# 1. Nếu sqlite3 có trong container và DB tồn tại, tạo hot-backup nhất quán trước khi nén
if docker ps --filter "name=^/${CONTAINER_NAME}$" --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    docker exec "$CONTAINER_NAME" sh -c \
        'if command -v sqlite3 >/dev/null 2>&1 && [ -f /etc/wireguard/wg-easy.db ]; then sqlite3 /etc/wireguard/wg-easy.db ".backup /tmp/wg-easy.db.bak"; fi' \
        >/dev/null 2>&1 || true
    docker cp "${CONTAINER_NAME}:/tmp/wg-easy.db.bak" "$SNAP_DIR/wg-easy.db.sqlite-backup" >/dev/null 2>&1 || true
    docker exec "$CONTAINER_NAME" rm -f /tmp/wg-easy.db.bak >/dev/null 2>&1 || true
fi

# 2. Sao lưu volume Docker (read-only mount, không tắt hoặc xóa volume)
docker run --rm \
    -v labops_wg_easy_data:/volume:ro \
    -v "$SNAP_DIR":/backup \
    alpine:latest \
    tar -czf /backup/volume.tar.gz -C /volume .

# 3. Lưu kèm compose.yaml để phục hồi chuẩn phiên bản
cp "$SCRIPT_DIR/compose.yaml" "$SNAP_DIR/compose.yaml.bak"

# 4. Đóng gói tổng thể và kiểm chứng toàn vẹn lưu trữ
tar -czf "$DEST_FILE" -C "$SNAP_DIR" .
chmod 600 "$DEST_FILE"
rm -rf "$SNAP_DIR"

if ! tar -tzf "$DEST_FILE" >/dev/null 2>&1; then
    echo "❌ Lỗi: File sao lưu $DEST_FILE bị hỏng!"
    exit 1
fi

echo "✅ Đã tạo & xác minh tệp sao lưu an toàn: $DEST_FILE"
ls -lh "$DEST_FILE"

