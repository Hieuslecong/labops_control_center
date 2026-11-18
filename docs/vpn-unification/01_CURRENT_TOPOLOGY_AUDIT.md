# BÁO CÁO RÀ SOÁT HẠ TẦNG VÀ HIỆN TRẠNG MẠNG LABOPS V2
## 01_CURRENT_TOPOLOGY_AUDIT.md

### 1. Tổng quan hiện trạng máy chủ phòng Lab AI

Hệ thống phòng Lab AI bao gồm 2 cụm máy chủ vật lý chính kết nối qua mạng nội bộ Switch 1GbE/10GbE:

| Tham số | Máy chủ Rack (`aiotlab-rack`) | Máy chủ Workstation (`aiot-ws1`) |
| :--- | :--- | :--- |
| **Node ID** | `track` | `workstation` |
| **LAN IP** | `192.168.167.250` | `192.168.167.251` |
| **Vai trò hạ tầng** | GPU Training Node (Production) | Primary Control Plane & Dev Node |
| **GPU phần cứng** | 3 × NVIDIA A30 Tensor Core | 1 × NVIDIA A30 Tensor Core |
| **SSH Host Port** | `1911` | `1912` |
| **VPN Legacy Host** | `wg0` (10.88.30.1/24) trên UDP 2204 | Client / Peer kết nối tới Rack |
| **Container VPN** | Docker `wg-easy` v15 (10.88.40.1/24) | Client qua WireGuard |

### 2. Hiện trạng mạng định tuyến và WireGuard Gateway

Trước khi thực hiện unification:
1. **Sự tồn tại song song của 2 WireGuard providers**:
   - `legacy-wg0`: Chạy trực tiếp trên Linux Kernel của máy chủ Rack qua `wg-quick@wg0.service`, subnet `10.88.30.0/24`, lắng nghe trên cổng UDP `2204`. Quản lý peer bằng lệnh nhị phân `wg set` trên host.
   - `wg-easy v15`: Chạy trong Docker container (`labops-wg-easy`), subnet `10.88.40.0/24`, web UI cổng `51821`, lắng nghe UDP cổng `2205` (hoặc `2204`).
2. **Xung đột cấu hình và port binding**:
   - Trong `infrastructure/wg-easy/compose.yaml`, tệp cấu hình cũ khai báo cả `"2204:2204/udp"` và `"2205:2204/udp"`. Khi host `wg0` đang chiếm cổng UDP `2204`, container `wg-easy` không thể bind vào cổng 2204, dẫn đến crash loop hoặc không thể tiếp nhận gói tin handshake từ Internet.
3. **Lỗ hổng định tuyến tĩnh trong cấu hình cấp phát (Hardcoded IP)**:
   - Ứng dụng LabOps Control Center cũ hardcode `AllowedIPs = 10.88.30.0/24, 192.168.167.250/32` cho TẤT CẢ người dùng.
   - Hậu quả: Người dùng được cấp workspace trên Workstation (`192.168.167.251`) nhận cấu hình WireGuard trỏ tới Rack (`192.168.167.250`). Khi kết nối, VPN client không có route tới `192.168.167.251`, làm đứt gãy hoàn toàn kết nối SSH tới Workstation.

### 3. Sơ đồ luồng mạng thực tế (As-Is vs Target)

```text
[INTERNET]
    │
    ▼ UDP 2205 (WAN: 118.70.169.236)
[MIKROTIK ROUTER] (dst-nat: 2205 -> 192.168.167.250:2205)
    │
    ▼
[RACK: Docker wg-easy v15] (Subnet: 10.88.40.0/24)
    │
    ├── (Định tuyến per-host AllowedIPs)
    │
    ├── [AllowedIPs: 10.88.40.0/24, 192.168.167.250/32] ──► [Rack Workspaces (SSH: 22010-22099)]
    │
    └── [AllowedIPs: 10.88.40.0/24, 192.168.167.251/32] ──► [WS Workspaces   (SSH: 22010-22099)]
```

### 4. Kết luận rà soát

Cần thực hiện dứt điểm:
1. Độc quyền hóa `wg-easy` v15, vô hiệu hóa toàn bộ cơ chế gọi lệnh `wg set` và legacy provider khỏi mã nguồn backend.
2. Tách biệt cấu hình `AllowedIPs` và SSH Host/Port động theo đúng máy chủ được gán workspace.
3. Chuẩn hóa biến môi trường cổng UDP sang `2205` để không xung đột với `wg0` host trong giai đoạn chuyển đổi an toàn (cutover).
