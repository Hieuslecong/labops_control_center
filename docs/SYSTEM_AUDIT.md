# SYSTEM AUDIT REPORT — LABOPS CONTROL CENTER & HẠ TẦNG THỰC TẾ

**Thời điểm kiểm tra**: 2026-11-10 21:18 (ICT)  
**Mục tiêu**: Đánh giá toàn diện hạ tầng máy chủ Debian Rack, mạng WireGuard, dịch vụ SSH, Docker, và mã nguồn LabOps Control Center trước khi triển khai tích hợp wg-easy.

---

## 1. Môi trường Máy chủ Thực tế (Real Host Environment)

### 1.1. Thông số Hệ điều hành & Kernel
- **Hostname**: `aiotlab-rack` (trong sandbox ảo hóa mang nhãn `sandbox`)
- **Hệ điều hành**: `Debian GNU/Linux 13 (trixie)`
- **Phiên bản chi tiết**: `Debian 13.7`
- **Linux Kernel**: `6.12.116+deb13-amd64 #1 SMP PREEMPT_DYNAMIC (2026-11-07)`
- **Kiến trúc**: `x86_64`

### 1.2. Mạng và Định tuyến (Network & Routing)
- **Giao diện vật lý chính**: `eno1`
  - **Địa chỉ IPv4**: `192.168.167.250/21`
  - **Gateway mặc định**: `192.168.160.1 via eno1 proto static metric 100`
- **Giao diện WireGuard hiện tại (`wg0`)**:
  - **Trạng thái**: `UP` / `ACTIVE`
  - **Địa chỉ IPv4 VPN**: `10.88.30.1/24`
  - **Dịch vụ systemd**: `wg-quick@wg0.service` (Active: active (exited) từ `2026-11-10 16:05:26 +07`)
  - **Cổng lắng nghe (Listen Port)**: UDP `2204` (`0.0.0.0:2204` và `[::]:2204`)
  - **Tệp cấu hình**: `/etc/wireguard/wg0.conf` (phân quyền `0700` thuộc sở hữu `root`)
- **Docker Bridge (`docker0`)**:
  - **Địa chỉ IP**: `172.17.0.1/16`
- **Firewall & Gói tin**:
  - **Phiên bản iptables**: `iptables v1.8.11 (nf_tables)`
  - **Cơ chế**: Quản lý qua `nftables` kernel subsystem

### 1.3. Dịch vụ SSH Legacy
- **Dịch vụ**: `ssh.service` (OpenBSD Secure Shell server)
- **Trạng thái**: `Active: active (running)` (PID 4249)
- **Cổng lắng nghe thực tế**:
  - TCP `1911` (`0.0.0.0:1911` & `[::]:1911`) — Cổng quản trị SSH chính
  - TCP `22` (`0.0.0.0:22` & `[::]:22`) — Cổng SSH tiêu chuẩn
- **Tài khoản quản trị**: `aiot-1` (UID 1000, nhóm `docker`, `sudo`, `users`)

### 1.4. Môi trường Docker Daemon
- **Docker Engine**: `26.1.5+dfsg1, build a72d7cd`
- **Docker Compose**: `2.26.1-4`
- **Socket Docker**: `/var/run/docker.sock` (truy cập được bởi người dùng thuộc nhóm `docker`)
- **Mạng hiện có**: `bridge`, `host`, `none`

---

## 2. Kiểm tra Mã Nguồn LabOps Control Center

### 2.1. Cấu trúc Kiến trúc
- **Frontend**: Next.js 16.4 (App Router), React 19.3, Tailwind CSS v4, shadcn/ui, TanStack Table & TanStack Query.
- **Backend**: Python 3.13 FastAPI kết hợp HTTP stdlib fallback server (`backend/app/main.py`).
- **Data Persistence**: JSON-based transactional stores trong `backend/data/` (`members.json`, `v2_workspaces.json`, `v2_hosts.json`, `audit_logs.json`, `wireguard_peers.json`).
- **Cơ chế xác thực**: Clerk Auth ở frontend kết hợp HMAC-SHA256 session token V2 giữa Next.js BFF (`src/app/api/labops/_lib.ts`) và Backend FastAPI (`LABOPS_SESSION_SECRET`).

### 2.2. Điểm Tích hợp WireGuard Hiện tại
1. `backend/app/adapters/wireguard_adapter.py`:
   - Quản lý giao diện `wg0` thông qua các lệnh nhị phân `wg show wg0 dump`, `wg set wg0 peer ...`.
   - Lưu trữ danh sách peer vào `backend/data/wireguard_peers.json` với file lock `wireguard_peers.lock`.
   - Cấp phát IP VPN trong dải `10.88.30.0/24`.
2. `backend/app/services/ssh_access_manager.py`:
   - Sinh cấu hình WireGuard client (`generate_wireguard_client_profile`) với Endpoint `118.70.169.236:2204`.
   - Đóng gói file `.conf` vào bộ kết nối ZIP `LabOps_Access_<username>.zip`.
3. `backend/app/services/labops_service.py`:
   - Tích hợp `register_wireguard_device()`, `revoke_wireguard_device()`, `claim_member_access_zip()`.
4. `src/features/users/components/member-management-view.tsx` & `member-provisioning-wizard.tsx`:
   - Hiển thị thông tin thiết bị, cấp khóa VPN, tải trọn bộ kết nối ZIP.

### 2.3. Đánh giá Rủi ro & Tồn tại (Findings)
- **Tồn tại 1 (Quyền hạn wg)**: `wireguard_adapter.py` cố gắng gọi trực tiếp lệnh `wg` trên host. Vì `aiot-1` không có passwordless sudo, các lệnh `wg set` trên host trực tiếp có thể gặp rào cản quyền hạn.
- **Tồn tại 2 (Xung đột cổng/interface)**: Instance mới của `wg-easy` tuyệt đối không được dùng interface `wg0` hay cổng UDP `2204` của WireGuard hiện hữu.
- **Giải pháp chuyển tiếp**: Triển khai `wg-easy` trong Docker container độc lập với cổng UDP `2205`, dải IP `10.88.40.0/24`, Docker volume riêng, và xây dựng lớp trừu tượng `VPNProvider` / `WgEasyProvider` để LabOps giao tiếp qua REST API nội bộ (cổng `127.0.0.1:51821`).

---

## 3. Kết luận Kiểm toán
1. Hạ tầng Debian 13, Docker 26, SSH (1911/22), WireGuard cũ (`wg0`, 2204) đang ở trạng thái ổn định và sẵn sàng cho mô hình triển khai song song (**Parallel Deployment**).
2. Phiên bản `ghcr.io/wg-easy/wg-easy:15` đã được kéo thành công về máy, xác minh mã băm `sha256:071f79a639e9361123a5e57733107320207b844ddbbd92dfa6962f541fe8aac8`.
3. Toàn bộ API v15 của wg-easy đã được giải mã chính xác trực tiếp từ Nitro router của container.

