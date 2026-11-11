# HỢP ĐỒNG API VÀ GIAO THỨC TRUYỀN THÔNG (PHASE 4)

---

## 1. NGUYÊN TẮC BẢO TOÀN API CONTRACT

**Cam kết tuyệt đối:** Mọi thay đổi ở tầng Backend (Go hay Python) KHÔNG ĐƯỢC làm thay đổi schema của các endpoint REST phục vụ cho Next.js Frontend. Tên trường (`camelCase` trên Frontend, `snake_case` ở Backend), kiểu dữ liệu (number, string, boolean), mã trạng thái HTTP (200, 400, 401, 403, 404) và cấu trúc phân trang phải được giữ nguyên 100%.

---

## 2. HỢP ĐỒNG DỮ LIỆU TELEMETRY (GO AGENT -> CONTROL PLANE)

Go Node Agent expose endpoint HTTP nội bộ tại `http://127.0.0.1:9100/api/v1/agent/...` hoặc gửi Heartbeat định kỳ lên Python Control Plane.

### 2.1. Host Telemetry (`GET /api/v1/agent/host`)
```json
{
  "source": "labops-go-agent",
  "collected_at": "2026-11-11T09:10:00Z",
  "status": "VERIFIED",
  "server_id": "track",
  "hostname": "aiotlab-rack",
  "ip_address": "192.168.167.250",
  "uptime": "1 ngày 04 giờ",
  "uptime_seconds": 100800,
  "cpu": {
    "model": "Intel(R) Xeon(R) Gold 6330 CPU @ 2.00GHz",
    "cores_physical": 28,
    "cores_logical": 48,
    "usage_percent": 3.4
  },
  "ram": {
    "total_gb": 125.0,
    "used_gb": 8.1,
    "free_gb": 2.7,
    "available_gb": 117.0,
    "usage_percent": 6.5
  },
  "disk": {
    "mount_point": "/",
    "fstype": "ext4",
    "total_tb": 1.75,
    "used_tb": 0.32,
    "free_tb": 1.43,
    "usage_percent": 18.2
  },
  "gpu_count": 3
}
```

### 2.2. GPU Telemetry (`GET /api/v1/agent/gpus`)
```json
{
  "source": "labops-go-agent",
  "collected_at": "2026-11-11T09:10:00Z",
  "status": "VERIFIED",
  "driver_version": "550.120",
  "cuda_version": "12.4",
  "gpus": [
    {
      "index": 0,
      "uuid": "GPU-339df6af-ed13-f42e-eb9e-2b671c387a30",
      "name": "NVIDIA A30",
      "memory_total_mb": 24576,
      "memory_used_mb": 1,
      "memory_free_mb": 24575,
      "utilization_gpu_pct": 0,
      "temperature_c": 38,
      "power_watts": 45,
      "active_processes": []
    }
  ]
}
```

### 2.3. Docker Workspace Containers (`GET /api/v1/agent/workspaces`)
```json
{
  "source": "labops-go-agent",
  "collected_at": "2026-11-11T09:10:00Z",
  "workspaces": [
    {
      "id": "c590866c5c2d",
      "name": "ws-testacc2332",
      "member_username": "testacc2332",
      "status": "running",
      "ssh_port": 22013,
      "container_ip": "172.17.0.2",
      "cpu_limit_cores": 4,
      "ram_limit_bytes": 8589934592,
      "created_at": "2026-11-10T16:30:00Z"
    }
  ]
}
```

