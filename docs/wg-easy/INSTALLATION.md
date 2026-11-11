# HƯỚNG DẪN CÀI ĐẶT VÀ VẬN HÀNH WG-EASY CONTAINER
**Thành phần**: `infrastructure/wg-easy/compose.yaml`

---

## 1. CẤU HÌNH COMPOSE (COMPOSE.YAML)

Tệp cấu hình chính thức nằm tại `infrastructure/wg-easy/compose.yaml`:

```yaml
services:
  wg-easy:
    image: ghcr.io/wg-easy/wg-easy:15@sha256:071f79a639e9361123a5e57733107320207b844ddbbd92dfa6962f541fe8aac8
    container_name: labops-wg-easy
    environment:
      - INSECURE=true
      - PORT=51821
      - INIT_ENABLED=true
      - INIT_USERNAME=${WG_EASY_ADMIN_USER:-admin}
      - INIT_PASSWORD=${WG_EASY_ADMIN_PASSWORD:-LabOpsSecureVPN2026!}
      - INIT_HOST=${WG_EASY_PUBLIC_ENDPOINT:-118.70.169.236}
      - INIT_PORT=2205
      - INIT_DNS=1.1.1.1
      - INIT_IPV4_CIDR=10.88.40.0/24
    volumes:
      - wg_easy_data:/etc/wireguard
      - /lib/modules:/lib/modules:ro
    ports:
      # Giai đoạn dual-run kiểm thử: host 2205 -> container 2204
      # Giai đoạn cutover: đổi thành "2204:2204/udp"
      - "2205:2204/udp"
      # Quản trị REST API - CHỈ bind nội bộ 127.0.0.1
      - "127.0.0.1:51821:51821/tcp"
    restart: unless-stopped
    cap_add:
      - NET_ADMIN
      - SYS_MODULE
    sysctls:
      - net.ipv4.ip_forward=1
      - net.ipv4.conf.all.src_valid_mark=1
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://127.0.0.1:51821/login || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  wg_easy_data:
    name: labops_wg_easy_data
```

---

## 2. LỆNH KHỞI CHẠY VÀ QUẢN LÝ DỊCH VỤ

### Khởi động container:
```bash
docker compose -f infrastructure/wg-easy/compose.yaml up -d
```

### Kiểm tra trạng thái sức khỏe:
```bash
docker ps --filter "name=labops-wg-easy"
```

### Xem nhật ký hoạt động:
```bash
docker logs -f labops-wg-easy
```

### Dừng dịch vụ an toàn:
```bash
docker compose -f infrastructure/wg-easy/compose.yaml down
```
*(Dữ liệu client và database SQLite được lưu trữ bền vững trong volume `labops_wg_easy_data`, không bị mất khi dừng hoặc xóa container).*

