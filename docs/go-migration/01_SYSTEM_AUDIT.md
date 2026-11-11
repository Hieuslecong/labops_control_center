# LABOPS CONTROL CENTER — KIỂM TOÁN HỆ THỐNG VÀ RUNTIME (PHASE 1)

**Ngày kiểm toán:** 11/11/2026  
**Chủ trì:** Principal Go Engineer, Linux Systems Engineer, DevSecOps Architect  
**Môi trường thực tế:** Server 01 — `aiotlab-rack` (ASUS ESC4000-E11, Debian GNU/Linux 13 trixie)  
**Git Branch:** `feature/labops-control-center` (`5d12c02`)  
**Repository:** `https://github.com/Hieuslecong/labops_control_center`

---

## 1. TỔNG QUAN HẠ TẦNG VÀ RUNTIME HIỆN TẠI

### 1.1. Thông số phần cứng & Hệ điều hành
- **Hostname:** `aiotlab-rack`
- **Hệ điều hành:** Debian GNU/Linux 13 (trixie) - Kernel `6.12.116+deb13-amd64`
- **Kiến trúc:** x86_64
- **Phần cứng máy chủ:** ASUSTeK ESC4000-E11, Firmware 2201
- **CPU:** 48 vCPUs (Intel Xeon Silver / Gold class)
- **Bộ nhớ (RAM):** 125 GiB Total, ~8.1 GiB Used, 117 GiB Available, Swap 46 GiB
- **Mạng cục bộ (LAN IP):** `192.168.167.250` (Primary default route)
- **Mạng công khai (WAN IP):** `118.70.169.236`

### 1.2. Kiểm toán GPU NVIDIA A30
Kiểm tra thực tế qua lệnh `nvidia-smi -L` và `nvidia-smi --query-gpu=... --format=csv`:
- **GPU 0:** `NVIDIA A30` — UUID: `GPU-339df6af-ed13-f42e-eb9e-2b671c387a30`, VRAM: 24,576 MiB, Util: 0%
- **GPU 1:** `NVIDIA A30` — UUID: `GPU-88ffcc1c-edfd-b71d-1915-f7cefd868227`, VRAM: 24,576 MiB, Util: 0%
- **GPU 2:** `NVIDIA A30` — UUID: `GPU-cfd02713-d67e-4921-e14e-c6b626728af1`, VRAM: 24,576 MiB, Util: 0%
- **Driver / CUDA:** NVIDIA Driver 550.120, CUDA 12.4 (Persistence Daemon `nvidia-persistenced.service` đang chạy).

### 1.3. Cổng mạng đang lắng nghe (Network Sockets & Port Bindings)
| Giao thức / Port | Interface | Tiến trình / Dịch vụ sở hữu | Vai trò bảo mật |
|---|---|---|---|
| **TCP 1911** | `0.0.0.0:*` | Host OpenSSH (`sshd`) | **CỔNG SSH QUẢN TRỊ GỐC (BẢO VỆ TUYỆT ĐỐI, KHÔNG CHẠM VÀO)** |
| **TCP 22** | `0.0.0.0:*` | Host OpenSSH (`sshd`) | Cổng SSH tiêu chuẩn của hệ điều hành host |
| **UDP 2204** | `0.0.0.0:*` | Docker `labops-wg-easy` | Cổng VPN WireGuard chính (MikroTik NAT: `118.70.169.236:2204`) |
| **UDP 2205** | `0.0.0.0:*` | Docker `labops-wg-easy` | Cổng VPN WireGuard phụ dự phòng |
| **TCP 51821** | `127.0.0.1:*` | Docker `labops-wg-easy` | API & Web UI quản trị wg-easy nội bộ |
| **TCP 5432** | `127.0.0.1:*` | `postgresql@17-main.service` | PostgreSQL Database cục bộ version 17 |
| **TCP 22013** | `0.0.0.0:*` | Docker `ws-testacc2332` | Cổng SSH container cá nhân của nghiên cứu viên `testacc2332` |
| **TCP 22001** | `0.0.0.0:*` | Docker `ws-multidev_user` | Cổng SSH container thử nghiệm multi-device |
| **TCP 22002** | `0.0.0.0:*` | Docker `ws-canary_b` | Cổng SSH container canary |
| **TCP 8000** | `0.0.0.0:*` | Python FastAPI (`app.main:app`) | Cổng REST API Backend LabOps |
| **TCP 3000** | `0.0.0.0:*` | Next.js Node/Bun | Cổng Dashboard Quản trị Web UI |

### 1.4. Trạng thái Docker Engine
- **Server Version:** `26.1.5+dfsg1`, Driver: `overlay2`
- **Containers đang chạy:**
  - `labops-wg-easy` (`ghcr.io/wg-easy/wg-easy:15`) — Up (healthy), UDP 2204/2205, HTTP 51821.
  - `ws-testacc2332` (`labops-workspace:latest`) — Up, port 22013.
  - `ws-multidev_user` (`labops-workspace:latest`) — Up, port 22001.
  - `ws-canary_b` (`labops-workspace:latest`) — Up, port 22002.

---

## 2. KIỂM TOÁN SOURCE CODE BACKEND HIỆN CÓ

### 2.1. Danh mục Module Backend Python
```text
backend/app/
├── main.py (FastAPI app + Built-in ThreadingHTTPServer fallback)
├── core/
│   ├── config.py (Pydantic BaseSettings, nạp từ .env/.env.local)
│   └── security.py (HMAC-SHA256 Token issuance/verification, require_role, require_node_agent)
├── adapters/
│   ├── host_monitor_adapter.py (Host CPU, RAM, Disk, Uptime, Uvicorn/psutil/subprocesses)
│   ├── process_monitor_adapter.py (Process table, psutil scan, SSH sessions)
│   ├── nvml_adapter.py (GPU VRAM, GPU compute processes, nvidia-smi parser)
│   ├── node_agent_adapter.py (LocalNodeAgentExecutor / NodeAgentClient)
│   ├── wireguard_adapter.py (Legacy direct host wg0 manipulation - DEPRECATED)
│   ├── wg_easy_adapter.py (REST client giao tiếp với wg-easy HTTP API 51821)
│   ├── vpn_provider.py (Abstract Base Class VPNProvider)
│   ├── legacy_wireguard_provider.py (Fallback provider)
│   ├── arcane_adapter.py (Tích hợp Arcane Docker management)
│   ├── beszel_adapter.py (Tích hợp Beszel telemetry)
│   ├── incus_adapter.py (Tích hợp Incus/LXD system containers)
│   └── teleport_adapter.py (Tích hợp Teleport SSH gateway)
├── services/
│   ├── labops_service.py (God object: 130KB+, sở hữu dữ liệu member, SSH, workspace, vpn)
│   ├── vpn_manager.py (Điều phối VPNProvider, quản lý multi-device trên wg-easy)
│   ├── ssh_access_manager.py (Canary daemons, dual-auth verification, key distribution)
│   ├── workspace_controller.py (Quản lý trạng thái workspace mong muốn vs quan sát)
│   ├── host_registry.py (Heartbeat, freshness, đăng ký host)
│   ├── gpu_scheduler.py (Đặt chỗ GPU, reservation slots)
│   └── security_scanner.py (Quét lỗ hổng, kiểm tra cấu hình)
└── api/v1/endpoints/
    ├── dashboard.py, servers.py, gpus.py, users.py, jobs.py, security.py,
    ├── events.py, audit.py, admin_members.py, access.py, storage_pools.py,
    ├── ssh_endpoints.py, monitoring.py, workspaces.py, v2_platform.py
```

### 2.2. Điểm nghẽn hiệu năng phát hiện được qua Benchmark thực tế
1. **Host Monitoring trong Python (`host_monitor_adapter.py`):**
   - Mỗi lần gọi `GET /api/v1/servers`, Python thực hiện 4 lệnh shell `subprocess.check_output` (`nvidia-smi`, `nvidia-smi --query-gpu`, `docker --version`, và đọc `/proc/mounts`).
   - Thời gian thực thi trung bình: **1,069.14 ms** (~1.07 giây cho MỘT request HTTP!).
   - Khi Dashboard poll 5 giây/lần, Python backend liên tục spawn sub-process gây tốn CPU không cần thiết.
2. **Process Monitoring (`process_monitor_adapter.py`):**
   - Quét toàn bộ bảng `/proc` bằng `psutil.process_iter()` tốn **258.83 ms** mỗi chu kỳ.
3. **Bộ nhớ RAM của Python runtime:**
   - FastAPI baseline chiếm **62.0 MiB RSS** và **170.0 MiB VMS** khi hoàn toàn nhàn rỗi (idle), với 5 threads OS.
   - Khi có nhiều request telemetry đồng thời, GIL của Python cản trở việc xử lý song song các tác vụ I/O hệ thống.

---

## 3. BẢNG PHÂN BỔ QUYỀN SỞ HỮU TRẠNG THÁI (STATE OWNERSHIP)

| File dữ liệu / Kho lưu trữ | Quyền ghi hiện tại (Active Writer) | Quyền đọc (Reader) | Rủi ro xung đột |
|---|---|---|---|
| `backend/data/members.json` | Python `labops_service.py` | Python REST API | Cao nếu Go ghi đồng thời mà không qua locking đồng bộ |
| `backend/data/ssh_endpoints.json` | Python `labops_service.py` | Python REST API | Cần giữ Python là Single Writer trong Giai đoạn 1 |
| `backend/data/v2_workspaces.json` | Python `workspace_controller.py` | Python / Go Inspector | Go Agent chỉ ĐỌC để kiểm tra Docker thực tế |
| `backend/data/v2_hosts.json` | Python `host_registry.py` | Python REST API | Go Agent gửi Heartbeat JSON qua REST API |
| `backend/data/audit_logs.json` | Python `labops_service.py` | Python REST API | Append-only |
| `wg-easy container volume` | `ghcr.io/wg-easy/wg-easy:15` | wg-easy REST API | **wg-easy là nguồn chân lý duy nhất cho WireGuard peers** |

---

## 4. DANH SÁCH CÁC HÀNH ĐỘNG ĐẶC QUYỀN (PRIVILEGE AUDIT)

1. **Docker Engine (`/var/run/docker.sock`):**
   - Thao tác: `docker run`, `docker stop`, `docker rm -f`, `docker inspect`, `docker stats`.
   - Nguy cơ: Nếu container được cấp quyền truy cập Docker socket của host, người dùng có thể leo thang đặc quyền root trên máy chủ.
   - **Quy tắc an toàn:** Tuyệt đối không mount `/var/run/docker.sock` vào container workspace của nghiên cứu viên.
2. **OpenSSH Host Daemon (`sshd`):**
   - Thao tác: Quản lý port 1911 và 22.
   - **Quy tắc an toàn:** Không chỉnh sửa cấu hình `/etc/ssh/sshd_config` của Debian host. Toàn bộ SSH của nghiên cứu viên phải chạy trong Docker container độc lập cách ly cổng (`22001-22050`).
3. **WireGuard Network Interface:**
   - Thao tác: Thêm/xóa peer, cấu hình subnet VPN.
   - **Quy tắc an toàn:** Không gọi `wg set wg0` trên host. Mọi thao tác cấu hình VPN thực hiện độc quyền qua REST API của container `labops-wg-easy` tại cổng nội bộ `http://127.0.0.1:51821`.
4. **NVIDIA GPU Access:**
   - Thao tác: Gán GPU `--gpus device=UUID` cho container.
   - **Quy tắc an toàn:** Chỉ gán GPU cụ thể theo UUID kiểm chứng, không dùng mode privileged.

