#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${1:-$HOME/labops_backups/wg-easy}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DEST_FILE="$BACKUP_DIR/wg_easy_data_$TIMESTAMP.tar.gz"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "=== SAO LƯU DỮ LIỆU WG-EASY ==="
echo "Đang sao lưu Docker volume 'labops_wg_easy_data'..."

docker run --rm \
    -v labops_wg_easy_data:/volume:ro \
    -v "$BACKUP_DIR":/backup \
    alpine:latest \
    tar -czf "/backup/wg_easy_data_$TIMESTAMP.tar.gz" -C /volume .

chmod 600 "$DEST_FILE"
echo "✅ Đã tạo tệp sao lưu an toàn: $DEST_FILE"
ls -lh "$DEST_FILE"

