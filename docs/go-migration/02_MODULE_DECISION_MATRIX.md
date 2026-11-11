# MA TRẬN QUYẾT ĐỊNH CHUYỂN ĐỔI MODULE (PHASE 2)

**Nguyên tắc chỉ đạo:** Không viết lại toàn bộ hệ thống. Chỉ chuyển những thành phần mà Go có lợi thế vượt trội về hiệu năng, sử dụng tài nguyên (CPU/RAM overhead), tính đồng thời (concurrency), độ tin cậy và khả năng đóng gói triển khai (single static binary).

---

## 1. PHÂN LOẠI CHI TIẾT CÁC MODULE

### Nhóm 1: `MIGRATE_TO_GO` (Bắt buộc ưu tiên chuyển sang Go)

| Module / Tính năng | Nguồn Python hiện tại | Đích đến trong Go | Lý do kỹ thuật & Bằng chứng đo lường |
|---|---|---|---|
| **A. Host Telemetry Collector** | `app/adapters/host_monitor_adapter.py` | `internal/host/collector.go` | Python tốn **1,069 ms** mỗi request do fork/exec 4 subprocesses. Go đọc trực tiếp `/proc/stat`, `/proc/meminfo`, `/proc/mounts`, `syscall.Statfs` trong **< 2 ms** (nhanh hơn >500 lần), tiêu tốn 0% CPU. |
| **B. Process Discovery & Monitor** | `app/adapters/process_monitor_adapter.py` | `internal/process/collector.go` | Python tốn **258 ms** quét `/proc`. Go goroutines quét `/proc` song song với bộ nhớ nhẹ, phân loại session SSH và PID chính xác không làm lock interpreter. |
| **C. Node Agent Daemon** | `app/adapters/node_agent_adapter.py` | `cmd/labops-agent/main.go` | Cần một binary duy nhất (single static binary) chạy trên cả Server Rack và Server Workstation, tiêu thụ <15MB RAM, tự động gửi heartbeat, telemetry và giám sát container. |
| **D. Docker Workspace Inspector & Lifecycle** | `app/services/workspace_controller.py` | `internal/docker/controller.go` | Sử dụng Official Docker Go SDK giao tiếp trực tiếp với `/var/run/docker.sock` qua HTTP Unix socket, loại bỏ hoàn toàn việc parse JSON từ `subprocess.run(["docker", ...])`. Xác minh trạng thái thực tế: container running, cgroup limits, SSH port publish. |
| **E. GPU Telemetry Collector (NVIDIA A30)** | `app/adapters/nvml_adapter.py` | `internal/gpu/collector.go` | Go collector truy vấn thông tin 3 GPU NVIDIA A30 qua struct hóa tối ưu, hỗ trợ map PID tiến trình GPU với Docker container và người dùng tương ứng. |

---

### Nhóm 2: `KEEP_PYTHON` (Giữ nguyên ở Python)

| Module / Tính năng | Vị trí hiện tại | Lý do kỹ thuật giữ nguyên |
|---|---|---|
| **FastAPI REST API & Routing** | `backend/app/api/v1/endpoints/` | Đang phục vụ hoàn hảo cho Next.js Dashboard. Response time endpoints thông thường đạt **2.37 ms** (như `/api/v1/users`). Viết lại toàn bộ 15 endpoints sang Go là lãng phí công sức và có nguy cơ phá vỡ API contract với Frontend. |
| **Member & Onboarding Orchestrator** | `app/services/labops_service.py` (phần CRUD) | Logic nghiệp vụ đăng ký thành viên, cấp phát thông tin, quản lý RAM Vault secrets đã hoạt động ổn định và được cover bởi toàn bộ bộ test pytest. |
| **Clerk Auth & Session Verification** | `app/core/security.py` | Xác thực HMAC token và role-based access control đã tích hợp sâu với Clerk SSO trên Next.js. |
| **Legacy Backup & Report Exporters** | `app/api/v1/endpoints/audit.py`, `events.py` | Tần suất gọi thấp, không gây ảnh hưởng đến hiệu năng máy chủ. |

---

### Nhóm 3: `SHARED_CONTRACT` (Giao tiếp & Hợp đồng chia sẻ giữa Python và Go)

| Thành phần | Giao thức / Định dạng | Vai trò & Trách nhiệm |
|---|---|---|
| **Node Agent Telemetry & Heartbeat** | REST JSON qua HTTP / Unix Socket | Go Agent định kỳ thu thập và gửi telemetry lên Backend qua POST `/api/v1/v2/hosts/heartbeat` hoặc expose endpoint `/metrics` / `/telemetry` cục bộ để Python đọc. |
| **Docker Container Labels** | Standard Docker Labels | Go và Python cùng sử dụng nhãn chuẩn:<br>`labops.managed=true`<br>`labops.member=<username>`<br>`labops.host=<host_id>`<br>`labops.workspace=<workspace_id>` |
| **VPN Integration (wg-easy)** | HTTP REST API (`http://127.0.0.1:51821`) | Cả hai đều coi `labops-wg-easy` container là Single Source of Truth cho WireGuard peers. Không ghi trực tiếp vào SQLite của wg-easy. |
| **Data Models (Schemas)** | JSON Schema / Pydantic Models | Các trường `PhysicalServerResponse`, `WireGuardDevice`, `DockerWorkspace` giữ nguyên 100% tên trường (`snake_case` ở backend, `camelCase` trên Frontend). |

---

### Nhóm 4: `DEPRECATE_AFTER_VERIFICATION` (Đánh dấu hủy bỏ sau khi kiểm chứng)

| Module cũ | Lý do loại bỏ | Điều kiện an toàn để loại bỏ |
|---|---|---|
| `app/adapters/wireguard_adapter.py` | Can thiệp trực tiếp `wg0` trên host bằng lệnh `wg set wg0`. Đã chuyển toàn bộ sang `wg-easy`. | Sau khi xác nhận 100% peer VPN kết nối qua `labops-wg-easy` (đã hoàn thành kiểm thử). |
| `app/adapters/host_monitor_adapter.py` (phần subprocess nặng) | Thay thế bằng Go Agent collector. | Sau khi Go Agent chạy ổn định và cung cấp dữ liệu cho Dashboard. |
| `app/adapters/node_agent_adapter.py` (Local executor Python) | Thay thế bằng Go Node Agent độc lập. | Sau khi binary Go Agent triển khai thành systemd service. |

