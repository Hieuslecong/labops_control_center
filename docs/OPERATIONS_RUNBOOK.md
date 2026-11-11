# SỔ TAY VẬN HÀNH & XỬ LÝ SỰ CỐ (OPERATIONS RUNBOOK)

Tài liệu này cung cấp các quy trình vận hành thực tế và các bước chẩn đoán, xử lý sự cố (troubleshooting) dành cho Quản trị viên trạm LabOps.

---

## 1. Lệnh Kiểm tra Nhanh Tình trạng Hệ thống (Health Quick-Check)

Chạy các lệnh sau trực tiếp trên host `aiotlab-rack`:

```bash
# 1. Kiểm tra container wg-easy
docker ps --filter "name=labops-wg-easy" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# 2. Chạy script healthcheck tự động
bash /home/aiot-1/admin_labops_control_center/infrastructure/wg-easy/healthcheck.sh

# 3. Kiểm tra các cổng mạng đang lắng nghe trên host
ss -lntup | grep -E "2204|1911|22|8000|51821"

# 4. Kiểm tra WireGuard kernel status bên trong container
docker exec labops-wg-easy wg show

# 5. Kiểm tra WireGuard host (wg0 legacy)
wg show wg0
```

---

## 2. Các Sự cố Thường gặp và Quy trình Khắc phục

### Sự cố 1: Thành viên không Handshake được vào WireGuard VPN (Handshake Timeout)
- **Hiện tượng:** Ứng dụng WireGuard trên máy thành viên hiển thị "Transfer: Sent ... KB, Received: 0 B".
- **Nguyên nhân tiềm ẩn:**
  1. Router MikroTik chưa forward cổng UDP 2204 tới IP `192.168.167.250`.
  2. Endpoint trong file `.conf` bị trỏ sai IP hoặc port.
  3. Container `labops-wg-easy` bị tắt hoặc cấu hình tường lửa trên máy chủ chặn UDP 2204.
- **Cách xử lý:**
  1. Kiểm tra trên host: `ss -unlp | grep 2204`. Đảm bảo Docker proxy đang lắng nghe `0.0.0.0:2204`.
  2. Kiểm tra router MikroTik: Đảm bảo NAT rule `dst-nat` cho UDP 2204 đang active và packet counter tăng khi client kết nối.
  3. Kiểm tra file cấu hình `.conf` của client: Dòng `Endpoint` phải là `118.70.169.236:2204`.
  4. Nếu thiết bị bị tạm dừng trên Dashboard: Admin bấm nút **"▶ Kích hoạt"** trong bảng quản lý thiết bị của thành viên.

---

### Sự cố 2: Container `labops-wg-easy` rơi vào trạng thái `unhealthy` hoặc bị crash
- **Hiện tượng:** `docker ps` báo `(unhealthy)` hoặc `Exited`.
- **Cách xử lý:**
  1. Xem nhật ký lỗi container:
     ```bash
     docker logs --tail 100 labops-wg-easy
     ```
  2. Kiểm tra health check:
     ```bash
     docker inspect labops-wg-easy --format='{{json .State.Health}}'
     ```
  3. Khởi động lại container bằng Docker Compose:
     ```bash
     cd /home/aiot-1/admin_labops_control_center/infrastructure/wg-easy
     docker compose restart
     ```
  4. Nếu cấu hình database bị lỗi, chạy script phục hồi từ bản backup gần nhất:
     ```bash
     bash restore.sh $(ls -t backups/*.tar.gz | head -n 1)
     ```

---

### Sự cố 3: Thành viên vào được VPN nhưng báo "Connection Refused" khi SSH vào Workspace
- **Hiện tượng:** Ping `10.88.40.1` thành công nhưng lệnh `ssh -p <PORT> username@10.88.40.1` báo connection refused hoặc timeout.
- **Cách xử lý:**
  1. Kiểm tra container workspace của thành viên:
     ```bash
     docker ps | grep "ws-username"
     ```
  2. Nếu container bị stopped: Admin bấm nút kích hoạt lại workspace trên Dashboard hoặc `docker start ws-username`.
  3. Kiểm tra SSH daemon bên trong workspace:
     ```bash
     docker exec ws-username service ssh status
     ```
  4. Nếu SSH daemon bị tắt, khởi động lại:
     ```bash
     docker exec ws-username service ssh start
     ```

---

### Sự cố 4: Thành viên báo mất file SSH Private Key
- **Hiện tượng:** Thành viên xóa nhầm thư mục `~/.ssh` hoặc đổi máy tính mới.
- **Cách xử lý (Chỉ 1-chạm từ Dashboard):**
  1. Admin mở dòng của thành viên trên Dashboard.
  2. Bấm nút **"⚡ Cấp khóa kết nối thẳng 1 chạm"** (hoặc **"🔒 Cấp khóa có Passphrase"**).
  3. Hệ thống tự động sinh cặp khóa Ed25519 mới, ghi khóa công khai vào workspace và hủy khóa cũ.
  4. Admin bấm **"📦 Tải bộ kết nối (.zip)"** để tải gói mới và bàn giao cho thành viên.

---

## 3. Khởi động lại Hệ thống Toàn diện (System Restart Sequence)

Khi máy chủ vật lý `aiotlab-rack` khởi động lại:
1. Docker daemon tự động khởi động các container với chính sách `restart: unless-stopped`.
2. Dịch vụ `wg-quick@wg0` tự động chạy qua systemd.
3. FastAPI Backend khởi động qua systemd unit hoặc script `start.sh`.
4. Next.js Frontend khởi động.
5. Kiểm tra toàn bộ bằng lệnh:
   ```bash
   ss -lntup | grep -E "1911|22|2204|8000|51821"
   ```

