# LABOPS ZERO-TRUST MULTI-NODE ARCHITECTURE SPECIFICATION

## 1. TỔNG QUAN KIẾN TRÚC

Hệ thống **LabOps Control Center** được thiết kế nhằm quản lý tập trung và an toàn nhiều cụm máy chủ tính toán GPU (Rack Server và Workstations) theo nguyên lý **Zero-Trust**.

Kiến trúc chuẩn hoá chính thức của hệ thống là luồng liên lạc 5 tầng:

```
[ Go Node Agent (WS1) ]  \
                          ---> [ Agent-Initiated gRPC mTLS Stream (TCP 9443) ] ---> [ Go Agent Gateway ] ---> [ FastAPI Controller ] ---> [ Next.js Dashboard ]
[ Go Node Agent (Rack) ] /
```

### Đặc tính cốt lõi
1. **Agent-Initiated Outbound-Only Streaming**:
   - Tất cả các Compute Node (`aiot-ws1`, v.v.) tự chủ động khởi tạo luồng kết nối **ra ngoài** tới Control Plane Gateway qua cổng TCP `9443`.
   - **Tuyệt đối không yêu cầu mở cổng inbound nào trên Workstation**, giải quyết triệt để vấn đề NAT/Firewall nội bộ và loại bỏ bề mặt tấn công từ mạng ngoài vào compute nodes.
2. **Mutual TLS (mTLS) bắt buộc TLS 1.3**:
   - Mọi kết nối đều phải xác thực 2 chiều (Server Certificate & Client Certificate) được cấp bởi Root CA nội bộ của LabOps.
   - Định danh của Node được ràng buộc chặt chẽ trong Subject Alternative Name (SAN): `<node_id>.node.labops.local`.
   - Bất kỳ mismatch nào giữa chứng chỉ mTLS và định danh khai báo trong payload đều bị từ chối ngay lập tức.
3. **Luồng dữ liệu 2 chiều (Bidirectional Streaming)**:
   - Một kết nối gRPC duy nhất truyền tải liên tục Heartbeat, Host Telemetry, GPU Telemetry, Process Telemetry và Workspace Telemetry.
   - Chiều ngược lại được sử dụng để chuyển phát các lệnh điều khiển từ xa có kiểm soát (typed commands) từ Control Plane xuống Node Agent mà không cần mở kết nối mới.
4. **Tuyệt đối không chạy Shell tùy ý (No Arbitrary Shell Execution)**:
   - Các thao tác điều khiển chỉ được phép nằm trong **Allowlist** nghiêm ngặt.
   - Mọi payload được parse thành cấu trúc kiểu tĩnh (typed parameters), kiểm tra ràng buộc trước khi bàn giao cho executor an toàn của Node Agent.

---

## 2. BỐI CẢNH HẠ TẦNG VÀ PHẦN CỨNG

Hệ thống quản lý cụm GPU phân tán gồm 2 nút thực tế:

| Nút Mạng | Hostname | Vai Trò | Địa Chỉ IP | Cấu Hình GPU | Hệ Điều Hành |
|---|---|---|---|---|---|
| **Server 01 — Server Rack** | `aiotlab-rack` | Control Plane & Hybrid Compute | `192.168.167.250` | 3 × NVIDIA A30 24GB (`GPU-339df6af`, `GPU-88ffcc1c`, `GPU-cfd02713`) | Debian 13 (Trixie) Linux 6.12 |
| **Server 02 — Workstation** | `aiot-ws1` | Dedicated Compute Node | `192.168.167.251` | 1 × NVIDIA A30 24GB | Debian/Ubuntu Linux x86_64 |

**Tổng tài nguyên quản lý tập trung**: **4 × NVIDIA A30 24GB** (96 GB VRAM tổng thể), phục vụ 9 Docker Workspace và các tác vụ tính toán AI của thành viên phòng Lab.

---

## 3. ĐẶC TẢ GIAO THỨC PROTOBUF (gRPC)

Định nghĩa tại: `go/proto/labops/v1/agent_gateway.proto`.

### Dịch vụ chính
```protobuf
service NodeAgentGatewayService {
  rpc Tunnel(stream AgentMessage) returns (stream GatewayMessage);
}
```

### Các nhóm bản tin chính
1. **Hello & Registration**:
   - `NodeHello`: Gửi ngay khi mở kênh stream, khai báo `node_id`, `hostname`, `role`, `agent_version`, `gpu_count`.
   - `NodeRegistration`: Phản hồi từ Gateway phê duyệt kết nối, trả về chu kỳ heartbeat, telemetry interval và danh sách `allowed_actions`.
2. **Heartbeat**:
   - `NodeHeartbeat`: Gửi mỗi 5 giây, mang số thứ tự `sequence_number`, trạng thái sức khỏe `NodeHealthStatus` (HEALTHY, DEGRADED, UNHEALTHY), uptime.
   - `HeartbeatAck`: Gateway xác nhận, đồng bộ thời gian máy chủ.
3. **Telemetry Streaming**:
   - `HostTelemetry`: CPU utilization, core count, RAM bytes, Disk bytes, network throughput, load average.
   - `GPUTelemetry`: Thông số từng GPU A30 (VRAM used/free, GPU/Mem utilization %, nhiệt độ, công suất điện, tiến trình GPU).
   - `ProcessTelemetry`: Danh sách tiến trình đã lọc và phân loại an toàn theo chuẩn LabOps Process Classification.
   - `WorkspaceTelemetry`: Danh sách container workspace Docker, cổng SSH, tài nguyên giới hạn.
4. **Command Dispatch & Progress**:
   - `CommandRequest`: Gateway gửi lệnh thuộc allowlist xuống node (mang `command_id`, `request_id`, `action`, `parameters`, `deadline_seconds`).
   - `CommandProgress`: Node báo cáo tiến độ % thực thi.
   - `CommandResponse`: Kết quả thực thi có mã trạng thái, payload JSON, thời gian xử lý microsecond.

---

## 4. BẢO MẬT & ALLOWLISTED ACTIONS

Danh sách hành động được phép thực thi qua gRPC Gateway:

```go
var AllowedActions = map[string]bool{
    "INSPECT_HOST":               true,
    "INSPECT_GPUS":               true,
    "VERIFY_PROCESS_PID":         true,
    "PROVISION_DOCKER_WORKSPACE": true,
    "STOP_DOCKER_WORKSPACE":      true,
    "SYNC_SSH_AUTHORIZED_KEYS":   true,
    "INSPECT_WIREGUARD_PEERS":    true,
    "COLLECT_TELEMETRY":          true,
    "TERMINATE_PROCESS":          true,
}
```

Mọi yêu cầu ngoài danh sách trên sẽ bị Gateway từ chối ngay lập tức với mã lỗi `ACTION_NOT_ALLOWED` (HTTP 403 / REJECTED_UNAUTHORIZED) trước khi truyền sang node.
