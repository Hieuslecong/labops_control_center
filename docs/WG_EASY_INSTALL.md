# HƯỚNG DẪN CÀI ĐẶT VÀ CẤU HÌNH WG-EASY V15 TRÊN LABOPS CONTROL CENTER

## 1. Yêu cầu tiên quyết (Prerequisites)

- **Hệ điều hành:** Debian 13 (Trixie) 64-bit kernel `6.12+`.
- **Docker Engine:** `26.x` trở lên với Docker Compose v2 (`docker compose version` >= 2.26).
- **Module WireGuard Kernel:** Module `wireguard` đã nạp trong Linux kernel (`lsmod | grep wireguard`).
- **Tài khoản quản trị:** Người dùng Linux thuộc nhóm `docker` (ví dụ `aiot-1`).
- **Mạng:** Router MikroTik NAT chuyển tiếp UDP `2204` từ WAN `118.70.169.236:2204` vào Debian Rack `192.168.167.250:2204`.

---

## 2. Pinned Image SHA-256 (Bảo mật Supply Chain)

Nhằm đảm bảo tính tái lập (reproducible build) và chống tấn công đầu độc image tag, `wg-easy` được ghim phiên bản cụ thể theo digest:

```yaml
image: ghcr.io/wg-easy/wg-easy:15@sha256:071f79a639e9361123a5e57733107320207b844ddbbd92dfa6962f541fe8aac8
```

---

## 3. Cấu hình Thư mục và File triển khai

Thư mục chuẩn: `/home/aiot-1/admin_labops_control_center/infrastructure/wg-easy/`

Cấu trúc:
```
infrastructure/wg-easy/
├── compose.yaml          # File định nghĩa Docker Compose
├── .env.example          # Mẫu biến môi trường
├── healthcheck.sh        # Script kiểm tra sức khỏe dịch vụ
├── backup.sh             # Script sao lưu volume và database
├── restore.sh            # Script phục hồi cấu hình
├── rollback.sh           # Script khôi phục nhanh khi có lỗi
└── README.md             # Hướng dẫn nhanh tại thư mục
```

### 3.1. File `compose.yaml` chuẩn Production

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
      - INIT_PORT=2204
      - INIT_DNS=1.1.1.1
      - INIT_IPV4_CIDR=10.88.40.0/24
    volumes:
      - wg_easy_data:/etc/wireguard
      - /lib/modules:/lib/modules:ro
    ports:
      # Port UDP WireGuard công khai cho VPN Client kết nối (Port 2204 NAT từ MikroTik)
      - "2204:2204/udp"
      # Port Web API quản trị - CHỈ bind vào loopback 127.0.0.1 để LabOps Backend gọi nội bộ, KHÔNG mở ra Internet
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

## 4. Các bước khởi chạy và xác thực dịch vụ

### Bước 1: Khởi động container
```bash
cd /home/aiot-1/admin_labops_control_center/infrastructure/wg-easy
docker compose up -d
```

### Bước 2: Kiểm tra trạng thái container
```bash
docker ps --filter "name=labops-wg-easy"
```
Kết quả mong muốn: Container có trạng thái `Up ... (healthy)` và các port:
- `127.0.0.1:51821->51821/tcp`
- `0.0.0.0:2204->2204/udp`

### Bước 3: Kiểm tra cổng lắng nghe trên host
```bash
ss -lntup | grep -E "2204|51821"
```

### Bước 4: Chạy script kiểm tra tự động
```bash
bash infrastructure/wg-easy/healthcheck.sh
```

---

## 5. Cấu hình trên Router MikroTik (Port Forwarding)

RouterOS MikroTik sử dụng quy tắc NAT chuyển tiếp UDP port 2204 từ WAN về máy chủ Debian:

```routeros
/ip firewall nat add chain=dstnat action=dst-nat to-addresses=192.168.167.250 to-ports=2204 protocol=udp dst-port=2204 comment="LabOps wg-easy WireGuard VPN Mesh"
```

> [!IMPORTANT]
> **Giữ nguyên quy tắc NAT UDP 2204** sẵn có trên MikroTik và giao diện `wg-lab` (UDP 2203). Toàn bộ gói tin WireGuard của thành viên đi qua UDP 2204 được chuyển tiếp trực tiếp vào container `labops-wg-easy`.

