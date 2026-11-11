#!/usr/bin/env bash
set -euo pipefail

BACKUP_FILE="${1:-}"

if [ -z "$BACKUP_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Lỗi: Cần truyền đường dẫn file sao lưu tar.gz hợp lệ."
    echo "Ví dụ: bash restore.sh /path/to/wg_easy_data_20261110.tar.gz"
    exit 1
fi

echo "⚠️ CẢNH BÁO: Thao tác này sẽ ghi đè dữ liệu của volume 'labops_wg_easy_data'."
read -rp "Bạn có chắc chắn muốn khôi phục không? (yes/N): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Đã hủy thao tác."
    exit 0
fi

echo "1. Dừng container nếu đang chạy..."
docker compose -f "$(dirname "$0")/compose.yaml" stop || true

echo "2. Xóa và giải nén dữ liệu từ bản sao lưu..."
docker run --rm \
    -v labops_wg_easy_data:/volume \
    -v "$(dirname "$BACKUP_FILE")":/backup \
    alpine:latest \
    sh -c "rm -rf /volume/* && tar -xzf '/backup/$(basename "$BACKUP_FILE")' -C /volume"

echo "3. Khởi động lại container..."
docker compose -f "$(dirname "$0")/compose.yaml" up -d

echo "✅ Đã khôi phục dữ liệu thành công!"

