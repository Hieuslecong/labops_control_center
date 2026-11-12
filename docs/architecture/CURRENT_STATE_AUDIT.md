# BÁO CÁO KIỂM TOÁN HIỆN TRẠNG HẠ TẦNG VÀ MÃ NGUỒN LABOPS CONTROL CENTER
**Ngày kiểm toán:** 12/11/2026  
**Chi nhánh tham chiếu:** `feature/labops-control-center`  
**Git HEAD:** `4fcf0f4`  
**Mục tiêu:** Đánh giá toàn diện kiến trúc hiện tại, xác định khoảng trống bảo mật và lập cơ sở triển khai LabOps Zero-Trust Multi-Node Architecture.

---

## 1. TỔNG QUAN HIỆN TRẠNG HẠ TẦNG THỰC TẾ

### 1.1. Máy chủ Rack Server (Control Plane — Host ID: `track`)
- **Hostname:** `aiotlab-rack`
- **Hệ điều hành:** Debian GNU/Linux 13 (Trixie), Linux Kernel `6.12.x`
- **Địa chỉ mạng (LAN):** `192.168.167.250/21` (Giao diện `eno1`)
- **GPU Vật lý:** 3 × NVIDIA A30 24GB (VRAM 24,576 MiB mỗi GPU; UUIDs: `GPU-339df6af...`, `GPU-88ffcc1c...`, `GPU-cfd02713...`; Driver `550.163.01`).
- **Trình biên dịch Go:** `go1.26.11 linux/amd64` tại `/home/aiot-1/.local/go/bin/go`.
- **Trình biên dịch Protobuf:** `protoc` v29.5, `protoc-gen-go` v1.36.11, `protoc-gen-go-grpc` v1.6.2.
- **Tiến trình vận hành thực tế:**
  - `sshd` quản trị lắng nghe tại port 22 và port 1911.
  - PostgreSQL nội bộ: lắng nghe `127.0.0.1:5432` (Container `labops-postgres` / daemon host).
  - WireGuard VPN: Container `labops-wg-easy` v15 healthy, quản trị nội bộ `127.0.0.1:51821`, cổng UDP 2204 và 2205.
  - Docker Workspaces: 9 container đang chạy (`ws-testacc`, `ws-canary_a`, `ws-canary_b`, `ws-canary_zip`, `ws-res_beta`, `ws-sec_audit_u`, `ws-user_no_container`, `ws-onetime_u`, `ws-temppw_u`) gắn các port SSH tương ứng từ `22001` đến `22009`.
  - Go Node Agent cục bộ: PID `1390814` lắng nghe tại `127.0.0.1:9150`.
  - Cổng `9443/TCP`: Đang rảnh (sẵn sàng cho Go Agent Gateway mTLS).

### 1.2. Máy trạm Workstation WS1 (Compute Node — Host ID: `workstation`)
- **Hostname:** `aiot-ws1`
- **Hệ điều hành:** Debian GNU/Linux 13 (Trixie)
- **Địa chỉ mạng (LAN):** `192.168.167.251` (Ping phản hồi tốt: 0.48ms).
- **GPU Vật lý:** 1 × NVIDIA A30 24GB (Driver `620.70.06`, NVIDIA Container Toolkit `1.20.2`).
- **Dịch vụ mạng:** Cổng 22 (SSH) đang mở.
- **Hiện trạng:** Chưa cài Go compiler, chưa triển khai LabOps Node Agent. Tài khoản thông thường không có sudo.
- **Phương thức triển khai:** Biên dịch binary tự hành (standalone static/CGO-free binary) trên Rack Server và phân phối sang WS1, chạy qua Systemd Service.

---

## 2. BẢNG PHÂN LOẠI HIỆN TRẠNG TÍNH NĂNG & THÀNH PHẦN

| Thành phần / Tính năng | File mã nguồn / Dịch vụ liên quan | Trạng thái phân loại | Đánh giá & Rủi ro phát hiện |
| :--- | :--- | :--- | :--- |
| **Go Host & GPU Collector** | `go/internal/host/`, `go/internal/gpu/` | `IMPLEMENTED_AND_VERIFIED` | Thu thập metrics CPU, RAM, Disk, NVML/nvidia-smi GPU thật, hoạt động chính xác trên Rack. |
| **Go Process Safeguards** | `go/internal/process/` | `IMPLEMENTED_AND_VERIFIED` | Ngăn chặn kill PID 1 (`PROTECTED_PROCESS_DENIED`), chống tái sử dụng PID, phân loại 4 nhóm tiến trình. |
| **Go Repository Guard** | `go/internal/repositoryguard/` | `IMPLEMENTED_AND_VERIFIED` | Kiểm duyệt kho mã tự động qua Trivy, lưu trữ nội dung có định danh SHA-256 digest, TOCTOU protection. |
| **Workspace Isolation (0700)** | `backend/app/services/ssh_access_manager.py` | `IMPLEMENTED_AND_VERIFIED` | Đã chuẩn hóa quyền thư mục workspace về `0o700` (`rwx------`), vượt qua 9/9 test an toàn. |
| **WireGuard / wg-easy Adapter** | `backend/app/adapters/wg_easy_adapter.py` | `IMPLEMENTED_AND_VERIFIED` | wg-easy v15 chạy ổn định, quản lý thiết bị đa tầng, kiểm thử API 51821 đạt 100%. |
| **Local Node Agent HTTP Mux** | `go/internal/transport/server.go` | `PARTIALLY_IMPLEMENTED` | Hoạt động tốt cục bộ, nhưng còn bỏ qua xác thực với `127.0.0.1` và dùng secret cố định. |
| **Multi-Node Telemetry gRPC** | `go/internal/gateway/`, `go/proto/` | `NOT_IMPLEMENTED` | Chưa có gRPC bidirectional stream, chưa có Gateway mTLS port 9443 trên Rack Server. |
| **Internal PKI / Mutual TLS** | `go/internal/pki/`, `certs/` | `NOT_IMPLEMENTED` | Chưa có CA chuyên dụng, cơ chế cấp phát và xác thực chứng chỉ client cho từng Compute Node. |
| **Remote Host Command Dispatch** | `backend/app/adapters/node_agent_adapter.py` | `PARTIALLY_IMPLEMENTED` | Backend gọi `/v1/execute` nhưng Go Agent cũ không có endpoint này; WS1 chưa có inbound endpoint. |
| **Dynamic Host Registry** | `backend/app/services/host_registry.py` | `IMPLEMENTED_NOT_VERIFIED` | Có cấu trúc dữ liệu cho `track` và `workstation`, nhưng trạng thái `workstation` hiện là `UNKNOWN` do chưa có kết nối. |
| **WS1 Agent Deployment** | `deploy/install-agent.sh` | `BLOCKED` | WS1 chưa có cấu hình SSH tự động hoặc sudo; cần script triển khai qua gói binary phân phối an toàn. |
| **Multi-Server Dashboard UI** | `src/features/servers/components/` | `IMPLEMENTED_AND_VERIFIED` | Giao diện Next.js tiếng Việt đã có bảng server, thẻ GPU và process monitor; cần liên kết dữ liệu WS1 khi kết nối. |

---

## 3. CÁC VẤN ĐỀ BẢO MẬT & KỸ THUẬT CỐT LÕI CẦN XỬ LÝ (PHASE B - G)

1. **Lỗ hổng xác thực Go Agent cục bộ:**
   - Trong `go/internal/transport/server.go`, dòng 94-97 bỏ qua kiểm tra token nếu request đến từ loopback (`127.0.0.1` hoặc `[::1]`). Bất kỳ tiến trình nào chạy trên host (hoặc container có host network) đều có thể gọi API quản trị mà không cần token.
   - Token mặc định `labops-local-agent-verified-token-2026` bị hardcode trong `go/internal/config/config.go`.
   - **Giải pháp:** Chuyển sang mô hình Zero-Trust mTLS bắt buộc với chứng chỉ máy được ký bởi CA nội bộ LabOps; loại bỏ hoàn toàn bypass xác thực theo IP.

2. **Bất đối xứng giao thức gọi lệnh (Protocol Mismatch):**
   - `backend/app/adapters/node_agent_adapter.py` gửi POST tới `{agent_url}/v1/execute`, trong khi Agent chỉ có các endpoint `/api/v1/agent/...`.
   - Đối với node từ xa như `workstation`, mở cổng HTTP inbound là vi phạm nguyên tắc bảo mật (tăng bề mặt tấn công mạng).
   - **Giải pháp:** Kiến trúc **Agent-Initiated gRPC Bidirectional Streaming over mTLS (TLS 1.3)**. Compute Node WS1 chủ động kết nối ra ngoài về Rack Server (Gateway port 9443), giữ kết nối stream liên tục để vừa gửi telemetry vừa nhận lệnh điều khiển thông qua cùng kênh an toàn.

3. **Cấu hình Systemd chưa linh hoạt:**
   - `deploy/labops-agent.service` hardcode đường dẫn `/home/aiot-1/admin_labops_control_center`.
   - **Giải pháp:** Tạo script sinh service file động theo tham số node, hỗ trợ cài đặt trên cả Rack Server và Compute Node WS1.

4. **Trạng thái Node Registry với WS1:**
   - Host `workstation` trong `v2_hosts.json` hiện mang `observed_state: "UNKNOWN"` và `telemetry_source: "NOT_CONFIGURED"`.
   - **Giải pháp:** Khi WS1 Agent kết nối mTLS và gửi `NodeHello` + `HostTelemetry`, Gateway tự động cập nhật trạng thái `ONLINE`, nạp thông số 1 × NVIDIA A30 vào tổng hợp tài nguyên hệ thống.

---

## 4. KẾT LUẬN & ĐỊNH HƯỚNG BƯỚC TIẾP THEO

Toàn bộ các thành phần hiện có trên Rack Server (Docker Workspaces 22001–22009, WireGuard VPN 2204/2205/51821, PostgreSQL 5432, 3 × NVIDIA A30) đang hoạt động ổn định.
Quá trình nâng cấp lên Multi-Node sẽ được thực hiện theo nguyên tắc **bổ sung không phá hủy (additive, non-destructive)**:
1. Thiết kế Protobuf schema và sinh Go code cho gRPC Gateway.
2. Xây dựng PKI engine nội bộ (CA, Gateway cert, Node client certs, validation SAN).
3. Triển khai Go Agent Gateway (lắng nghe TCP 9443 mTLS) trên Rack.
4. Nâng cấp Go Node Agent hỗ trợ chế độ Client gRPC mTLS kết nối về Gateway.
5. Tạo giao tiếp nội bộ giữa FastAPI và Gateway để đồng bộ Host Registry & dispatch commands.
6. Đóng gói binary và script triển khai cho WS1.
