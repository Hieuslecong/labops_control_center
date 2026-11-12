# LABOPS CONTROL PLANE & RACK SERVER DEPLOYMENT GUIDE

## 1. MỤC TIÊU VÀ ĐIỀU KIỆN TIÊN QUYẾT

Máy chủ **Server Rack (`aiotlab-rack`)** đóng vai trò là **Control Plane** kiêm **Hybrid Compute Node** với 3 card đồ họa NVIDIA A30.

### Thông số kỹ thuật
- **Địa chỉ LAN**: `192.168.167.250`
- **Hệ điều hành**: Debian 13 (Trixie) Linux x86_64
- **Dịch vụ đang chạy**:
  - FastAPI Controller: `127.0.0.1:8000`
  - Next.js Dashboard: `127.0.0.1:3000`
  - PostgreSQL: `127.0.0.1:5432`
  - WireGuard wg-easy: UDP `2204`/`2205`, TCP `51821`
  - 9 Docker Workspace containers: Cổng SSH `22001` - `22009`

---

## 2. QUY TRÌNH TRIỂN KHAI GATEWAY VÀ AGENT TRÊN RACK

### Bước 1: Biên dịch và Khởi tạo Gateway
Thực thi script cài đặt tự động:
```bash
cd ~/admin_labops_control_center
./deploy/install-gateway.sh
```

Quá trình này sẽ:
1. Biên dịch binary `bin/labops-gateway`.
2. Tạo thư mục quản lý Root CA `~/.labops/pki` và sinh `labops-ca.crt`, `labops-ca.key`.
3. Cấp phát chứng chỉ Server mTLS cho Gateway.
4. Mở cổng gRPC mTLS trên `0.0.0.0:9443` và HTTP Control Plane API trên `127.0.0.1:9444`.

### Bước 2: Khởi chạy Gateway chạy nền (Systemd hoặc Daemon)
Khởi chạy dịch vụ systemd:
```bash
sudo cp deploy/labops-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now labops-gateway
```

Kiểm tra trạng thái Gateway:
```bash
curl -s http://127.0.0.1:9444/health
# Kết quả kỳ vọng: {"service":"labops-agent-gateway","status":"UP","timestamp":...}
```

### Bước 3: Cấu hình và Chạy Go Node Agent cục bộ
Agent cục bộ trên Rack server kết nối tới Gateway qua gRPC mTLS:
```bash
./deploy/install-agent.sh
```

Kiểm tra Gateway ghi nhận Node Rack kết nối:
```bash
curl -s http://127.0.0.1:9444/api/v1/gateway/nodes
```
Phản hồi mẫu:
```json
{
  "nodes": [
    {
      "node_id": "track",
      "hostname": "aiotlab-rack",
      "role": "HYBRID",
      "status": "ONLINE",
      "gpu_count": 3
    }
  ],
  "total_count": 1
}
```

