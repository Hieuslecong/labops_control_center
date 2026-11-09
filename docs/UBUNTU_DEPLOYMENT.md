# Hướng dẫn Triển khai LabOps Control Center trên Ubuntu 22.04 LTS

Tài liệu này cung cấp toàn bộ quy trình thiết lập, cấu hình và vận hành hệ thống **LabOps Control Center — Nền tảng Quản trị Hạ tầng Tính toán AI** cho 2 máy chủ vật lý:
- **Server 01 — Track** (Hà Nội): 3 × NVIDIA A30 24GB, AMD EPYC 64-Core, 256GB RAM, 8TB ZFS.
- **Server 02 — Workstation** (TP.HCM): 1 × NVIDIA A30 24GB, Intel Xeon 32-Core, 128GB RAM, 4TB ZFS.

---

## 1. Yêu cầu Tiên quyết Hệ thống (Prerequisites)

- **Hệ điều hành**: Ubuntu 22.04.4 LTS Server (Linux Kernel 5.15+ hoặc 6.5+ HWE).
- **Quyền hạn**: Quyền quản trị `sudo` trên cả hai máy chủ.
- **Mạng kết nối**:
  - Địa chỉ IP tĩnh trên mạng LAN nội bộ phòng Lab.
  - Cổng WireGuard UDP `51820` mở giữa hai cơ sở Hà Nội và TP.HCM.
- **Phần cứng**:
  - NVIDIA A30 24GB HBM2 (PCIe Gen4).
  - Ổ đĩa NVMe / Enterprise SSD dành riêng cho ZFS pool.

---

## 2. Cài đặt NVIDIA Driver và Cấu hình GPU A30

> [!IMPORTANT]
> Tuyệt đối không xóa hoặc gỡ driver khi đang có tác vụ nghiên cứu đang chạy. Đảm bảo sử dụng driver phiên bản Production Branch 550.x.

```bash
# 1. Thêm NVIDIA Official Package Repository
sudo apt-get update
sudo apt-get install -y linux-headers-$(uname -r)
distribution=$(. /etc/os-release;echo $ID$VERSION_ID | sed -e 's/\.//g')
wget https://developer.download.nvidia.com/compute/cuda/repos/$distribution/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update

# 2. Cài đặt NVIDIA Driver 550 headless và CUDA 12.4
sudo apt-get install -y nvidia-driver-550-server nvidia-utils-550-server cuda-drivers-fabricmanager-550
sudo systemctl enable --now nvidia-fabricmanager

# 3. Bật Persistence Mode và kiểm tra trạng thái
sudo nvidia-smi -pm 1
nvidia-smi
```

Đầu ra chuẩn:
```text
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 550.120                Driver Version: 550.120        CUDA Version: 12.4     |
| 0  NVIDIA A30               On   | 00000000:3B:00.0 Off |                    0 |
| N/A   42C    P0             32W / 165W |       0MiB /  24576MiB |      0%      Default |
+-----------------------------------------------------------------------------------------+
```

---

## 3. Cài đặt và Khởi tạo Incus Container Engine

Incus là công cụ quản lý container cấp hệ thống (system container), mang lại hiệu năng bare-metal cho CUDA và tích hợp sâu với ZFS:

```bash
# 1. Cài đặt Incus từ repo chính thức
sudo apt-get install -y curl
curl -fsSL https://pkgs.zabbly.com/key.asc | sudo gpg --show-keys --fingerprint
sudo mkdir -p /etc/apt/keyrings
sudo curl -fsSL https://pkgs.zabbly.com/key.asc -o /etc/apt/keyrings/zabbly.asc
sudo sh -c 'cat <<EOF > /etc/apt/sources.list.d/zabbly-incus-stable.sources
Enabled: yes
Types: deb
URIs: https://pkgs.zabbly.com/incus/stable
Suites: $(. /etc/os-release && echo ${VERSION_CODENAME})
Components: main
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/zabbly.asc
EOF'
sudo apt-get update
sudo apt-get install -y incus incus-client zfsutils-linux

# 2. Khởi tạo Incus với ZFS Pool
sudo incus admin init --auto \
  --storage-backend=zfs \
  --storage-pool=labops-pool \
  --network-address=0.0.0.0 \
  --network-port=8443

# 3. Thêm card NVIDIA GPU vào Incus profile mặc định
incus profile device add default gpu gpu
```

---

## 4. Thiết lập Mạng WireGuard Kết nối 2 Cơ sở

Mạng WireGuard kết nối Server 01 (Hà Nội, `10.200.0.1`) và Server 02 (TP.HCM, `10.200.0.2`):

```bash
# 1. Cài đặt WireGuard
sudo apt-get install -y wireguard

# 2. Cấu hình /etc/wireguard/wg0.conf trên Server 01 (Track)
cat <<EOF | sudo tee /etc/wireguard/wg0.conf
[Interface]
Address = 10.200.0.1/24
ListenPort = 51820
PrivateKey = <TRACK_PRIVATE_KEY>

[Peer]
# Server 02 - Workstation TP.HCM
PublicKey = <WORKSTATION_PUBLIC_KEY>
Endpoint = 198.51.100.20:51820
AllowedIPs = 10.200.0.2/32
PersistentKeepalive = 25
EOF

# 3. Khởi động WireGuard service
sudo systemctl enable --now wg-quick@wg0
sudo wg show
```

---

## 5. Cài đặt Netdata Telemetry Agent

Netdata cung cấp chỉ số thời gian thực (1-giây độ trễ) cho CPU, RAM, Disk ZFS và Network:

```bash
# Cài đặt Netdata một dòng chính thức
wget -O /tmp/netdata-kickstart.sh https://get.netdata.cloud/kickstart.sh && sh /tmp/netdata-kickstart.sh --non-interactive

# Cấu hình bind vào WireGuard IP (bảo mật không public ra ngoài)
sudo sed -i 's/bind to = \*/bind to = 10.200.0.1/g' /etc/netdata/netdata.conf
sudo systemctl restart netdata
```

---

## 6. Triển khai LabOps Control Center bằng Docker Compose

```bash
# 1. Clone mã nguồn
git clone https://github.com/Kiranism/next-shadcn-dashboard-starter.git labops-control-center
cd labops-control-center
git checkout feature/labops-control-center

# 2. Cấu hình biến môi trường
cp .env.example .env

# Chỉnh sửa các thông số kết nối thực tế trong .env
# - DEMO_MODE=false (chuyển sang chế độ kết nối phần cứng thực)
# - POSTGRES_PASSWORD=<mật_khẩu_bảo_mật>
# - DATABASE_URL=postgresql+asyncpg://labops:<mật_khẩu_bảo_mật>@postgres:5432/labops_db

# 3. Khởi chạy toàn bộ hệ thống
docker compose up -d --build

# 4. Kiểm tra trạng thái containers
docker compose ps
docker compose logs -f backend
```

Hệ thống sẽ chạy trên:
- **Frontend Dashboard**: `http://<IP_MAY_CHU>:3000` (hoặc domain qua Reverse Proxy NGINX/Caddy)
- **FastAPI Backend**: `http://<IP_MAY_CHU>:8000/api/v1`
- **Swagger API Docs**: `http://<IP_MAY_CHU>:8000/api/v1/docs`

---

## 7. Chạy trực tiếp qua Systemd (Tùy chọn không dùng Docker)

Nếu muốn chạy trực tiếp trên host:

```bash
# Backend Service: /etc/systemd/system/labops-backend.service
[Unit]
Description=LabOps Control Center FastAPI Backend
After=network.target postgresql.service

[Service]
Type=simple
User=hieulc
WorkingDirectory=/opt/labops-control-center/backend
ExecStart=/usr/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=5
EnvironmentFile=/opt/labops-control-center/.env

[Install]
WantedBy=multi-user.target
```

Kích hoạt:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now labops-backend
```

---

## 8. Sao lưu và Khôi phục (ZFS Snapshots)

```bash
# Tạo snapshot workspace người dùng
sudo zfs snapshot labops-pool/workspaces/ws-hieulc@backup-$(date +%F)

# Sao chép snapshot sang Server 02 qua đường truyền WireGuard
sudo zfs send labops-pool/workspaces/ws-hieulc@backup-$(date +%F) | ssh -i /etc/labops/backup_key root@10.200.0.2 zfs recv backup-pool/workspaces/ws-hieulc
```

---

## 9. Danh mục Kiểm tra An toàn trước Vận hành (Safety Checklist)

- [x] Không gỡ bỏ hoặc sửa đổi NVIDIA Driver 550.x khi GPU đang có tiến trình CUDA.
- [x] Mỗi người dùng được cách ly trong Incus cgroups (CPU, RAM, Disk Quota).
- [x] Kho mã nguồn từ GitHub phải vượt qua bước AST Security Review trước khi cho phép clone vào workspace.
- [x] Quản trị viên theo dõi nhật ký kiểm toán (Audit Logs) có Correlation ID cho mọi hành động nhạy cảm.

