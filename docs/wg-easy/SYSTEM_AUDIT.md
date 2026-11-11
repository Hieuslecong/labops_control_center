# KIỂM TOÁN HỆ THỐNG THỰC TẾ (SYSTEM AUDIT)
**Hạ tầng**: aiotlab-rack | Debian 13 Trixie | Linux 6.12.63+deb13-amd64

---

## 1. THÔNG SỐ PHẦN CỨNG & HẠ TẦNG

- **Hostname**: `aiotlab-rack`
- **Địa chỉ IPv4 LAN**: `192.168.167.250/21`
- **Default Gateway**: `192.168.160.1` (MikroTik Router)
- **Địa chỉ IP Public**: `118.70.169.236`
- **Docker Engine**: Phiên bản `26.1.5`
- **Docker Compose**: Phiên bản `2.26.1-4`

---

## 2. DỊCH VỤ MẠNG & CỔNG KẾT NỐI

| Cổng | Giao thức | Dịch vụ | Mục đích | Trạng thái bảo mật |
| :--- | :--- | :--- | :--- | :--- |
| **1911** | TCP | OpenSSH Server (Host) | Quản trị máy chủ vật lý cho Admin | Public NAT từ MikroTik (Bảo toàn) |
| **2204** | UDP | Kernel WireGuard `wg0` | Kết nối VPN Legacy (MacBook Admin) | Host bind `0.0.0.0:2204` |
| **2205** | UDP | Container `labops-wg-easy` | Cổng VPN công khai cho Thành viên | Docker mapped `0.0.0.0:2205->2204/udp` |
| **51821** | TCP | Web API của `wg-easy` | LabOps Backend điều khiển VPN | **Chỉ bind loopback `127.0.0.1`** |
| **8000** | TCP | LabOps Backend API | FastAPI / API Điều khiển trung tâm | Loopback / Internal LAN |
| **3000** | TCP | LabOps Next.js Frontend | Giao diện Web Admin | Internal LAN / Web Console |
| **22001 - 22050** | TCP | Member SSH Workspaces | Cổng kết nối vào container thành viên | Truy cập qua VPN WireGuard |

---

## 3. KIỂM CHỨNG TÍNH NĂNG MẠNG VÀ TƯƠNG THÍCH KERNEL

- **Kernel Module WireGuard**: Đã nạp sẵn trong kernel (`modprobe wireguard`).
- **IP Forwarding**: Đã kích hoạt (`net.ipv4.ip_forward = 1`).
- **Định tuyến xuyên container**: Lưu lượng từ container `labops-wg-easy` (subnet `10.88.40.0/24`) có thể kết nối thông suốt tới `192.168.167.250:1911` và các cổng workspace qua iptables MASQUERADE.

