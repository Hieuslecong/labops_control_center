# KIẾN TRÚC TỔNG THỂ LABOPS CONTROL CENTER + WG-EASY
**Single Source of Truth cho WireGuard VPN & Container SSH**

---

## 1. MÔ HÌNH TRUY CẬP (ACCESS FLOW)

```
[ THIẾT BỊ NGHIÊN CỨU VIÊN ] (MacBook / Linux / Windows / iOS / Android)
             │
             │  1. WireGuard Tunnel (UDP 2205 hoặc 2204)
             ▼
[ MIKROTIK ROUTER / GATEWAY ] (IP Tĩnh: 118.70.169.236)
             │  (Port Forwarding UDP NAT)
             ▼
[ DEBIAN RACK SERVER: aiotlab-rack ] (LAN IP: 192.168.167.250)
             │
             ├──► [ DOCKER: labops-wg-easy ] (Subnet 10.88.40.0/24)
             │        │
             │        └── iptables MASQUERADE -> eth0
             │
             └──► [ SSH CONTAINERS / WORKSPACES ]
                      ├── ws-member-1 (Port: 22001)
                      ├── ws-member-2 (Port: 22002)
                      └── ws-testacc233 (Port: 22011)
```

Luồng kết nối:
1. Client thiết lập VPN Tunnel tới `118.70.169.236:2205` (hoặc `2204`).
2. Giao thức WireGuard giải mã lưu lượng bên trong container `labops-wg-easy` với IP nguồn thuộc dải `10.88.40.x`.
3. Nhờ rule `MASQUERADE`, lưu lượng từ VPN có thể định tuyến trực tiếp vào IP nội bộ `192.168.167.250` tại các cổng SSH container được mở trên host.
4. Client gõ lệnh: `ssh labops-<username>` là đăng nhập thẳng vào workspace của mình.

---

## 2. PHÂN TẦNG KIẾN TRÚC PHẦN MỀM

```
┌────────────────────────────────────────────────────────┐
│                   LABOPS DASHBOARD                     │
│         (Next.js 16 + React 19 + Tailwind v4)          │
└───────────────────────────┬────────────────────────────┘
                            │ REST API (JSON)
                            ▼
┌────────────────────────────────────────────────────────┐
│                   LABOPS BACKEND                       │
│                   (FastAPI / Python)                   │
├────────────────────────────────────────────────────────┤
│  • LabOpsDataService (Orchestrator)                    │
│  • SshAccessManager (Workspace & Key Management)       │
│  • VPNManager (VPN Routing Coordinator)                │
│       │                                                │
│       └──► WgEasyAdapter (Active REST Client)          │
│                 │                                      │
│                 └── HTTP JSON (127.0.0.1:51821)       │
└─────────────────┼──────────────────────────────────────┘
                  ▼
┌────────────────────────────────────────────────────────┐
│                CONTAINER: labops-wg-easy               │
│               (ghcr.io/wg-easy/wg-easy:15)             │
├────────────────────────────────────────────────────────┤
│  • Nitro Web Engine (Port 51821)                       │
│  • SQLite Database (/etc/wireguard/wg-easy.db)         │
│  • In-Kernel WireGuard Module & wg0 Interface          │
└────────────────────────────────────────────────────────┘
```

---

## 3. CƠ CHẾ AN TOÀN VÀ ĐỘ LẬP LẬP TỨC
- **Không ghi đè cấu hình host**: Backend không thực hiện `sudo wg set` hay chỉnh sửa tệp tĩnh trên máy chủ.
- **REST API xác thực**: Backend giao tiếp với `wg-easy` qua HTTP Basic/Session Auth nội bộ `127.0.0.1:51821`.
- **Đồng bộ thời gian thực**: Trạng thái Handshake, byte truyền/nhận (Rx/Tx) được truy vấn trực tiếp từ `wg-easy` khi mở Dashboard.
- **Kiểm soát đa thiết bị**: Mỗi thiết bị là một Client riêng biệt trên `wg-easy`, cho phép Admin vô hiệu hóa thiết bị bị thất lạc mà không làm gián đoạn các thiết bị khác của thành viên.

