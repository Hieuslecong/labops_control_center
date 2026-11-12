# LABOPS WORKSTATION (WS1) DEPLOYMENT & ONBOARDING GUIDE

## 1. MỤC TIÊU VÀ ĐIỀU KIỆN TIÊN QUYẾT

Máy chủ **Workstation 01 (`aiot-ws1`)** đóng vai trò là **Dedicated Compute Node** với 1 card đồ họa NVIDIA A30 24GB.

### Thông số kỹ thuật
- **Địa chỉ LAN**: `192.168.167.251`
- **Hệ điều hành**: Debian/Ubuntu Linux x86_64
- **Cấu hình GPU**: 1 × NVIDIA A30 24GB (Driver 550.x, CUDA 12.x)
- **Cổng Inbound mở**: Không yêu cầu! Mọi liên lạc là outbound tới Gateway `192.168.167.250:9443`.

---

## 2. QUY TRÌNH KẾT NỐI TỰ ĐỘNG (ENROLLMENT)

Chỉ với 2 bước đơn giản trên máy Workstation:

### Bước 1: Ghi danh lấy chứng chỉ mTLS (Enrollment)
Từ terminal của Workstation `aiot-ws1`:
```bash
# Tải script ghi danh hoặc clone repository
cd ~/admin_labops_control_center

# Thực thi script ghi danh tự động với Control Plane IP
sudo ./deploy/enroll-node.sh workstation 192.168.167.250 9444 9443 /etc/labops/certs
```

Script sẽ:
1. Gửi yêu cầu xin cấp enrollment token từ Gateway tại `http://192.168.167.250:9444`.
2. Trao đổi token lấy cặp chứng chỉ mTLS (`client.crt` và `client.key`) và `ca.crt`.
3. Lưu trữ an toàn tại `/etc/labops/certs/` với quyền `0600` cho khóa riêng tư.
4. Tự động sinh tệp cấu hình môi trường `/etc/labops/agent.env`.

### Bước 2: Khởi chạy Node Agent
Cài đặt và kích hoạt dịch vụ Node Agent:
```bash
sudo ./deploy/install-agent.sh
```

Dịch vụ `labops-agent` sẽ khởi động, nạp chứng chỉ mTLS từ `/etc/labops/certs/`, và mở luồng gRPC bidirectional streaming tới `192.168.167.250:9443`.

---

## 3. XÁC MINH KẾT NỐI TRÊN CONTROL PLANE

Từ Control Plane (Server Rack), kiểm tra danh sách máy chủ kết nối:
```bash
curl -s http://127.0.0.1:9444/api/v1/gateway/nodes | jq .
```

Kết quả kỳ vọng hiển thị cả 2 máy chủ:
```json
{
  "nodes": [
    {
      "node_id": "track",
      "hostname": "aiotlab-rack",
      "status": "ONLINE",
      "gpu_count": 3
    },
    {
      "node_id": "workstation",
      "hostname": "aiot-ws1",
      "status": "ONLINE",
      "gpu_count": 1
    }
  ],
  "total_count": 2
}
```

Kiểm tra số liệu GPU chi tiết của Workstation:
```bash
curl -s http://127.0.0.1:9444/api/v1/gateway/nodes/workstation/telemetry | jq .gpu
```

Truy cập Dashboard tại `http://192.168.167.250:3000/dashboard/servers`:
- Server Track hiển thị 3x NVIDIA A30.
- Server Workstation hiển thị 1x NVIDIA A30.
- Tổng cộng: **4x NVIDIA A30** được giám sát và vận hành tập trung trong một giao diện duy nhất.
