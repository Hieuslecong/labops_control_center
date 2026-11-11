# HƯỚNG DẪN TRIỂN KHAI VÀ QUY TRÌNH ROLLBACK (PHASE 17)

---

## 1. QUY TRÌNH TRIỂN KHAI TRÊN MÁY CHỦ SẢN XUẤT (SERVER 01 - RACK)

### Cách 1: Khởi động tự động qua script tích hợp `start.sh`
Khi quản trị viên chạy `./start.sh`, hệ thống sẽ tự động khởi động đầy đủ 3 thành phần theo thứ tự:
1. `⚡ [1/3] Khởi động LabOps Go Node Agent (http://127.0.0.1:9100)`
2. `📡 [2/3] Khởi động LabOps Backend API V2 (http://127.0.0.1:8000)`
3. `💻 [3/3] Khởi động Dashboard Next.js (http://localhost:3000)`

### Cách 2: Triển khai độc lập qua Systemd Service
1. Cài đặt file service:
   ```bash
   sudo cp deploy/labops-agent.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable labops-agent
   sudo systemctl start labops-agent
   ```
2. Kiểm tra trạng thái:
   ```bash
   sudo systemctl status labops-agent
   ./deploy/verify-agent.sh
   ```

---

## 2. QUY TRÌNH TRIỂN KHAI TRÊN MÁY CHỦ THỨ HAI (SERVER 02 - WORKSTATION)

Trên Server 02 (Workstation):
1. Copy binary tĩnh `bin/labops-agent` sang Server 02:
   ```bash
   scp bin/labops-agent aiot-workstation:/opt/labops/bin/
   ```
2. Cấu hình biến môi trường trên Server 02 trong `/opt/labops/deploy/labops-agent.env`:
   ```bash
   LABOPS_SERVER_ID="workstation"
   LABOPS_SERVER_NAME="Server 02 — Workstation (Phòng Lab)"
   LABOPS_AGENT_BIND_ADDR="0.0.0.0"
   LABOPS_AGENT_PORT="9100"
   LABOPS_AGENT_SECRET="labops-local-agent-verified-token-2026"
   ```
3. Khởi chạy binary `labops-agent` qua systemd trên Server 02.
4. Go Agent trên Server 02 sẽ giao tiếp an toàn với Server 01 (Rack) qua dải IP đường hầm WireGuard VPN (`10.88.40.X:9100`).

---

## 3. QUY TRÌNH ROLLBACK VÀ XỬ LÝ SỰ CỐ (DISASTER RECOVERY)

Nếu cần tạm dừng Go Agent hoặc quay trở về Python Collector gốc:

1. **Chạy script rollback tự động:**
   ```bash
   ./deploy/rollback-agent.sh
   ```
2. **Cơ chế Fallback thụ động (Zero-Downtime Fallback):**
   - Khi tiến trình `labops-agent` bị kill hoặc dừng, Backend Python `HostMonitorAdapter` và `NvmlAdapter` sẽ phát hiện kết nối tới cổng 9100 bị từ chối và **ngay lập tức tự động fallback về Python Collector gốc**.
   - Người dùng và Dashboard trên trình duyệt không nhận thấy bất kỳ sự cố gián đoạn nào (Zero Downtime).
   - Không có container Docker nào bị tắt.
   - Không có WireGuard VPN peer nào bị ngắt kết nối.

