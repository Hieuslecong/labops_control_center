# KIẾN TRÚC MỤC TIÊU — LABOPS CONTROL CENTER LAI GO-PYTHON (PHASE 3)

---

## 1. MÔ HÌNH TỔNG THỂ HỆ THỐNG LAI (HYBRID ARCHITECTURE)

Hệ thống kết hợp sức mạnh xử lý I/O và tính song song của **Go** ở tầng Node Agent/Collector và tính linh hoạt trong quản trị của **Python/FastAPI** ở tầng Control Plane nghiệp vụ:

```text
               +-------------------------------------------+
               |        Next.js Dashboard (Port 3000)      |
               |       (TanStack React Query + shadcn)     |
               +-------------------------------------------+
                                     |
                                     | REST API (HTTP 8000)
                                     v
               +-------------------------------------------+
               |        LabOps Control Plane (Python)      |
               |      - Fast API / Clerk Auth              |
               |      - Member CRUD / Secrets RAM Vault    |
               |      - wg-easy VPN Provider Adapter       |
               +-------------------------------------------+
                        |                          |
       mTLS / Local HTTP API                       | mTLS / Over VPN (WireGuard)
       (Unix Socket or 127.0.0.1:9100)             | (10.88.40.X:9100)
                        v                          v
       +-------------------------------+   +-------------------------------+
       |    LabOps Go Node Agent       |   |    LabOps Go Node Agent       |
       |    Host: Server 01 (Rack)     |   |    Host: Server 02 (Station)  |
       +-------------------------------+   +-------------------------------+
         |       |       |        |          |       |       |        |
         v       v       v        v          v       v       v        v
      Linux    Docker   GPU      SSH       Linux    Docker   GPU      SSH
      /proc    Socket   A30    Endpoint    /proc    Socket   A30    Endpoint
```

---

## 2. CẤU TRÚC DỰ ÁN GO (`/go` hoặc `/agent`)

Chúng ta đặt thư mục mã nguồn Go chuẩn tại thư mục gốc `agent/` (hoặc `go/` theo quy định Master Prompt):
```text
go/
├── go.mod
├── go.sum
├── cmd/
│   ├── labops-agent/
│   │   └── main.go       # Binary Node Agent chạy trên từng host
│   └── labops-cli/
│       └── main.go       # Công cụ CLI kiểm toán và benchmark
├── internal/
│   ├── config/           # Cấu hình Agent (flags, env, defaults)
│   ├── host/             # Thu thập CPU, RAM, Disk, Uptime thuần Go (/proc)
│   ├── process/          # Quét và liên kết Process với SSH/GPU/Containers
│   ├── gpu/              # Thu thập thông số NVIDIA A30, VRAM, GPU PIDs
│   ├── docker/           # Official Docker Go SDK: Inspect, Stats, Lifecycle
│   ├── ssh/              # Kiểm tra SSH port, verify connectivity, host keys
│   ├── vpn/              # Client wg-easy adapter bằng Go (nếu cần)
│   ├── transport/        # HTTP Server nội bộ cho Agent (expose /telemetry, /health)
│   └── telemetry/        # Cấu trúc dữ liệu và chu kỳ lấy mẫu (5s host, 5s gpu, 10s procs)
```

---

## 3. NGUYÊN TẮC THIẾT KẾ CỦA GO NODE AGENT

1. **Không phụ thuộc thư viện ngoài không cần thiết (Minimal Dependencies):**
   - Đọc CPU, RAM, Disk trực tiếp từ Linux Virtual File System (`/proc/stat`, `/proc/meminfo`, `/proc/mounts`, `syscall.Statfs`).
   - Sử dụng Official Docker Go SDK (`github.com/docker/docker/client`) để giao tiếp trực tiếp với Docker Daemon qua `/var/run/docker.sock`.
2. **Tiêu thụ tài nguyên cực thấp (Ultra-Low Footprint):**
   - RAM sử dụng mục tiêu: **< 15 MiB**.
   - CPU trung bình nhàn rỗi: **< 0.1%**.
   - Thời gian khởi động: **< 10 ms**.
3. **Độ tươi mới của dữ liệu (Data Freshness & Provenance):**
   - Mọi bản tin telemetry đều đính kèm:
     ```json
     {
       "source": "labops-go-agent",
       "collected_at": "2026-11-11T09:05:00Z",
       "status": "VERIFIED",
       "server_id": "track"
     }
     ```
   - Khi không đọc được dữ liệu, trả về trạng thái rõ ràng (`UNKNOWN` hoặc `NOT_SUPPORTED`), tuyệt đối không bịa số liệu.
4. **An toàn bảo mật (Safety-First):**
   - Chỉ lắng nghe trên giao diện cục bộ `127.0.0.1:9100` hoặc Unix domain socket.
   - Không cho phép thực thi shell commands tùy ý từ xa.
   - Giao tiếp với Docker qua API có kiểm soát, áp đặt nhãn `labops.managed=true`.

