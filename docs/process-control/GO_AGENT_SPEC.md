# LABOPS GO NODE AGENT SPECIFICATION (PROCESS ENGINE V2)

**Dự án:** LabOps Control Center  
**Module:** `labops-agent` (Go 1.24)  
**Port lắng nghe:** `127.0.0.1:9100`  
**Phiên bản:** 2.1  

---

## 1. MỤC TIÊU VÀ TRÁCH NHIỆM

Go Node Agent là daemon hiệu năng cao chạy thường trực trên mỗi máy chủ vật lý (Rack, Workstation). Agent chịu trách nhiệm:
1. Đọc trực tiếp `/proc` và cgroups với độ trễ dưới 2ms, không tốn CPU/RAM (CPU < 0.3%, RAM < 25MB).
2. Ánh xạ chính xác quan hệ giữa PID host, container namespace, cgroup v2, và tiến trình compute NVML GPU.
3. Cung cấp API kiểm soát tiến trình an toàn (Terminate/Kill) có chống PID reuse và bảo vệ danh sách trắng.

---

## 2. API ENDPOINTS QUẢN LÝ TIẾN TRÌNH

### 2.1. Lấy danh sách tiến trình
- **Method:** `GET`
- **Path:** `/api/v1/agent/processes`
- **Query Parameters:**
  - `filter`: `all` | `member` | `system` | `gpu` | `research` (mặc định: `all`)
  - `container_id`: lọc theo container (ví dụ `ws-testacc`)
  - `user`: lọc theo tên người dùng
- **Response Format:**
  ```json
  {
    "source": "labops-go-agent",
    "collected_at": "2026-11-11T05:00:00Z",
    "server_id": "rack",
    "total_count": 45,
    "processes": [
      {
        "ref": "rack:ws-testacc:102450:16892300",
        "pid": 102450,
        "ppid": 102400,
        "pgid": 102450,
        "uid": 1001,
        "user": "testacc",
        "name": "python3",
        "command_line": "python3 train_resnet.py --epochs 50",
        "classification": "RESEARCH_JOB",
        "container_id": "ws-testacc",
        "workspace": "ws-testacc",
        "cpu_percent": 85.4,
        "memory_rss_mb": 4250.5,
        "memory_percent": 3.4,
        "state": "R",
        "start_time": "2026-11-11 10:15:30",
        "start_time_ticks": 16892300,
        "is_gpu": true,
        "gpu_index": 0,
        "gpu_uuid": "GPU-a1b2c3d4-e5f6-7890",
        "vram_mb": 8192,
        "is_protected": false
      }
    ]
  }
  ```

### 2.2. Lấy cây tiến trình (Process Tree)
- **Method:** `GET`
- **Path:** `/api/v1/agent/processes/{ref}/tree`
- **Response Format:**
  Trả về thông tin tiến trình gốc cùng danh sách các tiến trình con (`children`).

### 2.3. Dừng tiến trình có ân hạn (Graceful Terminate)
- **Method:** `POST`
- **Path:** `/api/v1/agent/processes/{ref}/terminate`
- **Request Body:**
  ```json
  {
    "timeout_seconds": 10,
    "terminate_tree": true
  }
  ```
- **Xử lý:**
  1. Kiểm tra tính hợp lệ của `ref` (PID, start_time_ticks, container_id).
  2. Kiểm tra danh sách trắng `is_protected == false`.
  3. Gửi `SIGTERM`.
  4. Lắng nghe trong vòng `timeout_seconds`. Nếu tiến trình thoát, trả về `status: "TERMINATED"`. Nếu vẫn còn chạy sau timeout, trả về `status: "TIMEOUT_STILL_RUNNING"`.

### 2.4. Buộc dừng tiến trình (Force Kill)
- **Method:** `POST`
- **Path:** `/api/v1/agent/processes/{ref}/kill`
- **Request Body:**
  ```json
  {
    "kill_tree": true
  }
  ```
- **Xử lý:**
  1. Kiểm tra tính hợp lệ của `ref` và danh sách trắng.
  2. Gửi `SIGKILL` tới PID hoặc toàn bộ Process Group.
  3. Xác nhận tiến trình đã bị tiêu diệt khỏi `/proc`.

---

## 3. THIẾT KẾ CẤU TRÚC DỮ LIỆU GO (INTERNAL ENGINE)

Tập trung tại gói `go/internal/process/`:
- `ref.go`: Xử lý phân tích và tạo chuỗi `ProcessRef`.
- `collector.go`: Quét `/proc`, đối chiếu jiffies, đọc `/proc/{pid}/cgroup`, tích hợp bảng GPU từ NVML.
- `controller.go`: Kiểm tra danh sách trắng, xác thực trước khi gửi signal, thực thi `syscall.Kill`.
- `tree.go`: Xây dựng cây quan hệ cha-con từ PPID.
