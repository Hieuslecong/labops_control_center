# KIẾN TRÚC TÍCH HỢP WG-EASY V15 VÀO LABOPS CONTROL CENTER

## 1. Tổng quan kiến trúc

Hệ thống **LabOps Control Center** trên Debian 13 (`aiotlab-rack`) được tích hợp dịch vụ **wg-easy v15** chạy trong Docker container cô lập (`labops-wg-easy`). Mục tiêu là cho phép Quản trị viên (Admin) quản lý tập trung toàn bộ vòng đời truy cập WireGuard VPN, SSH key, tài khoản thành viên và phân bổ tài nguyên nghiên cứu trực tiếp từ Dashboard, không phụ thuộc vào dòng lệnh thủ công hay cấu hình MikroTik WinBox mỗi khi thêm thành viên.

Kiến trúc tuân thủ nghiêm ngặt nguyên tắc **Zero-Breakage**:
- **Bảo toàn nguyên vẹn** dịch vụ SSH quản trị máy chủ vật lý trên cổng TCP `1911` và `22`.
- **Khớp chuẩn quy tắc NAT MikroTik:** Router MikroTik hiện hữu đã có sẵn quy tắc NAT chuyển tiếp UDP `2204` từ WAN `118.70.169.236:2204` về Debian Rack `192.168.167.250:2204`.
- Dịch vụ **wg-easy v15** trong Docker container tiếp nhận trực tiếp cổng UDP `2204` (port mapping `2204:2204/udp`), đóng vai trò là WireGuard Gateway chính cho toàn bộ thành viên.

---

## 2. Sơ đồ tương tác toàn hệ thống (Mermaid Diagram)

```mermaid
flowchart TD
    subgraph Internet["Public Internet & Researcher Devices"]
        AdminUser["Quản trị viên (Admin Browser)"]
        ClientLaptop["Thành viên: Laptop / PC"]
        ClientMobile["Thành viên: Mobile / Tablet"]
    end

    subgraph MikroTik["Router MikroTik (Public IP: 118.70.169.236)"]
        NAT_2204["NAT UDP 2204 -> Debian Rack (:2204)"]
        Legacy_2203["WireGuard cũ UDP 2203 (Untouched)"]
    end

    subgraph Host["Debian 13 Server (aiotlab-rack - 192.168.167.250)"]
        subgraph NativeServices["Native Host Services"]
            SSHD_Admin["OpenSSH Host (Port 1911 & 22)"]
        end

        subgraph LabOpsPlatform["LabOps Control Center Stack"]
            FE["Next.js 16 Frontend (Dashboard UI)"]
            BFF["Next.js BFF Proxy (/api/labops/*)"]
            BE["FastAPI Python Backend (:8000)"]
            VPN_Mgr["VPNManager (Abstraction Layer)"]
        end

        subgraph DockerWGEasy["Docker Container: labops-wg-easy"]
            WGEasy_API["Nitro/Nuxt API (:51821 loopback only)"]
            WGEasy_WG["WireGuard Engine (UDP 2204 - 10.88.40.1/24)"]
            DataVol["Docker Volume: labops_wg_easy_data"]
        end

        subgraph MemberWorkspaces["Member Containers / Workspaces"]
            WS_SSH["Dedicated SSH (Ports 22001-29999)"]
            ComputeEnv["Isolated PyTorch/CUDA Environments"]
        end
    end

    AdminUser -->|HTTPS| FE
    FE -->|BFF Session| BFF
    BFF -->|HMAC Bearer Auth| BE
    BE --> VPN_Mgr
    VPN_Mgr -->|HTTP Basic Auth (:51821)| WGEasy_API
    WGEasy_API --> DataVol
    WGEasy_API --> WGEasy_WG

    ClientLaptop -->|WireGuard UDP 2204| NAT_2204
    ClientMobile -->|WireGuard UDP 2204| NAT_2204
    NAT_2204 -->|UDP 2204| WGEasy_WG

    WGEasy_WG -->|VPN Mesh 10.88.40.0/24| WS_SSH
    WS_SSH --> ComputeEnv
```

---

## 3. Bảng phân bổ cổng mạng và dải địa chỉ (Network Allocation)

| Dịch vụ / Thành phần | Giao thức / Port | Địa chỉ IP / Subnet | Mục đích | Ràng buộc bảo mật |
| :--- | :--- | :--- | :--- | :--- |
| **Legacy Host SSH** | TCP `1911`, `22` | `192.168.167.250` | Quản trị máy chủ Debian vật lý (`aiot-1`) | Bảo toàn nguyên vẹn, không chỉnh sửa |
| **wg-easy WireGuard VPN** | UDP `2204` (Host -> Container) | `10.88.40.1/24` | VPN Mesh cho toàn bộ thành viên nghiên cứu | **NAT từ MikroTik qua UDP 2204** |
| **wg-easy Web API** | TCP `51821` | `127.0.0.1:51821` | Quản lý client, sinh key, cấp config | **CHỈ BIND 127.0.0.1**, cấm mở ra Internet |
| **LabOps Backend API** | TCP `8000` | `127.0.0.1:8000` | API nghiệp vụ LabOps V2 | Truy cập qua BFF Proxy xác thực Clerk |
| **LabOps Frontend UI** | TCP `3000` | `0.0.0.0:3000` | Giao diện điều khiển Web Dashboard | Xác thực Clerk RBAC |
| **Workspace SSH Per-Member** | TCP `22001` - `29999` | `192.168.167.250` / `10.88.40.x` | Cổng SSH riêng vào container từng thành viên | Xác thực bằng Ed25519 Public Key riêng |

---

## 4. Luồng dữ liệu và gói tin (Packet Flow)

### 4.1. Luồng cấp quyền và tạo thiết bị VPN (Admin Provisioning Flow)
1. **Admin thao tác trên Dashboard:** Chọn thành viên `@username`, nhập tên thiết bị (ví dụ: `MacBook Pro M3`) và bấm **"+ Cấp thiết bị"** (hoặc tạo thành viên mới qua Wizard).
2. **Next.js BFF chuyển tiếp:** Client gửi yêu cầu đến `/api/labops/v2/members/{id}/devices`. BFF xác thực phiên Clerk của Admin, ký token HMAC và gửi tiếp tới FastAPI Backend.
3. **VPNManager điều phối:** `VPNManager` nhận lệnh và gọi `WgEasyAdapter.create_client(name, expires_at=None)`.
4. **wg-easy sinh cặp khóa:**
   - Adapter gửi `POST http://127.0.0.1:51821/api/client` với Basic Auth.
   - Engine wg-easy sinh private key và public key cho client, gán IP chưa dùng trong subnet `10.88.40.0/24` (ví dụ `10.88.40.2`).
   - Cấu hình wireguard trong container được reload tức thì (zero-downtime).
5. **Trả về cấu hình 1-chạm:**
   - Adapter gọi `GET /api/client/{id}/configuration` lấy toàn bộ nội dung file `.conf` với `Endpoint = 118.70.169.236:2204`.
   - Backend gói file cấu hình kèm script SSH và trả về cho Dashboard để người dùng tải ngay file `.conf` hoặc tải toàn bộ bộ kết nối `.zip`.

### 4.2. Luồng kết nối của thành viên (Member Traffic Flow)
1. **Khởi tạo VPN Handshake:** Client mở WireGuard, import file `.conf` và bật kết nối tới `118.70.169.236:2204`.
2. **MikroTik Router chuyển tiếp:** Router nhận gói UDP port 2204 trên WAN và thực hiện `dst-nat` sang `192.168.167.250:2204`.
3. **Container wg-easy giải mã:** Container nhận gói UDP 2204, giải mã phiên WireGuard và đưa gói tin vào mạng nội bộ VPN `10.88.40.0/24`.
4. **Kết nối SSH vào Workspace:**
   - Thành viên chạy lệnh: `ssh -i id_ed25519_username -p 22007 username@10.88.40.1` (hoặc IP LAN `192.168.167.250`).
   - OpenSSH server của container kiểm tra khóa công khai của thành viên trong `/home/username/.ssh/authorized_keys`.
   - Kết nối thành công, thành viên làm việc trực tiếp trong container riêng với đầy đủ tài nguyên CPU/RAM/GPU được phân bổ.

