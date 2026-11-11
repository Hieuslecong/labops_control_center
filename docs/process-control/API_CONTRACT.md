# LABOPS PROCESS CONTROL & MONITORING — API CONTRACT

**Phiên bản:** 2.0  
**Giao thức:** REST JSON qua HTTPS / HTTP  
**Xác thực:** Bearer JWT (hoặc Header `Authorization: Bearer <token>`)  
**Phân quyền (RBAC):** Chỉ tài khoản có vai trò `ADMIN` mới được phép thực thi các lệnh dừng tiến trình.  

---

## 1. DANH SÁCH ENDPOINTS CONTROL PLANE (PYTHON FASTAPI)

### 1.1. Lấy danh sách tiến trình toàn diện
- **Endpoint:** `GET /api/v1/processes`
- **Query Parameters:**
  - `server_id`: Lọc theo máy chủ (`rack` | `workstation` | `all`, mặc định: `all`)
  - `member_id` / `username`: Lọc theo thành viên lab sở hữu
  - `workspace_id`: Lọc theo container workspace (ví dụ: `ws-testacc`)
  - `gpu_id`: Lọc theo GPU (`0` | `1` | `2` hoặc UUID)
  - `classification`: `all` | `SYSTEM_PROCESS` | `MEMBER_PROCESS` | `RESEARCH_JOB` | `GPU_PROCESS`
  - `search`: Từ khóa tìm kiếm (lệnh cmdline, PID, username)
  - `limit`: Số bản ghi tối đa (mặc định: 100)
- **Response 200 OK:**
  ```json
  {
    "server_id": "rack",
    "timestamp": "2026-11-11T12:00:00Z",
    "total_count": 15,
    "items": [
      {
        "ref": "rack:ws-testacc:102450:16892300",
        "pid": 102450,
        "ppid": 102400,
        "pgid": 102450,
        "user": "testacc",
        "member_id": "user-testacc",
        "full_name": "Test Account",
        "name": "python3",
        "command_line": "python3 train.py --batch-size 64",
        "classification": "RESEARCH_JOB",
        "workspace": "ws-testacc",
        "server_id": "rack",
        "server_name": "Server 01 — Rack (Debian 12)",
        "cpu_percent": 92.1,
        "memory_rss_mb": 3412.0,
        "memory_percent": 2.7,
        "state": "RUNNING",
        "start_time": "2026-11-11 11:20:10",
        "elapsed_time": "01h 15m 20s",
        "is_gpu": true,
        "gpu_id": "rack-gpu-00",
        "gpu_index": 0,
        "vram_mb": 6144,
        "is_protected": false
      }
    ]
  }
  ```

### 1.2. Chi tiết cây tiến trình
- **Endpoint:** `GET /api/v1/processes/{ref}/tree`
- **Response 200 OK:**
  Trả về tiến trình gốc và cấu trúc mảng con `children: [...]` cho phép hiển thị dạng cây phân cấp trên Dashboard.

### 1.3. Dừng tiến trình an toàn (Graceful Stop - SIGTERM)
- **Endpoint:** `POST /api/v1/processes/{ref}/stop`
- **Quyền yêu cầu:** `ADMIN` (hoặc Member sở hữu chính tiến trình đó)
- **Request Body:**
  ```json
  {
    "reason": "Giải phóng tài nguyên GPU cho thành viên khác",
    "terminate_tree": true,
    "timeout_seconds": 10
  }
  ```
- **Response 200 OK:**
  ```json
  {
    "status": "SUCCESS",
    "action": "SIGTERM",
    "ref": "rack:ws-testacc:102450:16892300",
    "pid": 102450,
    "message": "Tiến trình đã dừng an toàn thành công.",
    "executed_at": "2026-11-11T12:05:00Z"
  }
  ```

### 1.4. Buộc dừng tiến trình (Force Kill - SIGKILL)
- **Endpoint:** `POST /api/v1/processes/{ref}/kill`
- **Quyền yêu cầu:** `ADMIN`
- **Request Body:**
  ```json
  {
    "reason": "Tiến trình bị treo, không phản hồi SIGTERM sau 30 giây",
    "force_confirmed": true,
    "kill_tree": true
  }
  ```
- **Response 200 OK:**
  ```json
  {
    "status": "SUCCESS",
    "action": "SIGKILL",
    "ref": "rack:ws-testacc:102450:16892300",
    "pid": 102450,
    "message": "Tiến trình đã bị buộc dừng cưỡng chế.",
    "executed_at": "2026-11-11T12:06:00Z"
  }
  ```

### 1.5. Xử lý mã lỗi chuẩn hóa
| HTTP Status | Error Code | Ý nghĩa |
| :--- | :--- | :--- |
| `400 Bad Request` | `INVALID_PROCESS_REF` | Cú pháp `ref` không hợp lệ hoặc thiếu thông tin |
| `400 Bad Request` | `FORCE_CONFIRMATION_REQUIRED` | Thiếu cờ xác nhận `force_confirmed: true` khi gọi `kill` |
| `403 Forbidden` | `PROTECTED_PROCESS_DENIED` | Tiến trình thuộc danh sách trắng hệ thống (sshd, agent, dockerd...) |
| `403 Forbidden` | `PERMISSION_DENIED` | Người gọi không có quyền quản trị hoặc không sở hữu tiến trình |
| `404 Not Found` | `PROCESS_NOT_FOUND` | Tiến trình không tồn tại hoặc đã thoát |
| `409 Conflict` | `PID_RECYCLED` | Phát hiện PID đã bị tái cấp phát cho tiến trình khác |
