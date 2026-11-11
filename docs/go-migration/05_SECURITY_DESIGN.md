# BẢO MẬT HỆ THỐNG VÀ PHÂN QUYỀN TRUY CẬP (PHASE 5)

---

## 1. MÔ HÌNH ĐẶC QUYỀN TỐI THIỂU (LEAST PRIVILEGE MODEL)

1. **Go Agent Daemon:**
   - Go Agent chạy dưới người dùng hệ thống chuyên biệt `aiot-1` thuộc group `docker` (hoặc service account riêng), không chạy dưới quyền `root` trực tiếp.
   - Nhờ thuộc group `docker`, Agent có thể đọc/ghi socket `/var/run/docker.sock` mà không cần gọi `sudo`.
2. **Bảo vệ Cổng Quản trị Gốc:**
   - Cổng SSH gốc **TCP 1911** và **TCP 22** của máy chủ Debian được cô lập tuyệt đối. Go Agent không có bất kỳ logic nào can thiệp vào `/etc/ssh/sshd_config` hoặc file `authorized_keys` của tài khoản `root`/`aiot-1`.
3. **Bảo vệ Hệ thống VPN WireGuard:**
   - Go Agent KHÔNG gọi `ip link`, `wg set`, hoặc `iptables` trên host.
   - Toàn bộ WireGuard peers do container `labops-wg-easy` quản lý. Go Agent nếu cần tương tác thì chỉ gọi REST API của wg-easy qua cổng nội bộ `http://127.0.0.1:51821`.
4. **Cách ly Nghiên cứu viên (Multi-Tenant Isolation):**
   - Mỗi nghiên cứu viên được cấp phát một container Docker riêng (`ws-{username}`).
   - Container tuyệt đối KHÔNG có đặc quyền `--privileged` và KHÔNG được mount `/var/run/docker.sock`.
   - Cổng SSH được map độc lập trên từng dải cổng riêng (`22001 - 22050`), cấm xung đột cổng.

---

## 2. XÁC THỰC GIỮA CONTROL PLANE VÀ AGENT

1. **Localhost Socket / Token Guard:**
   - Khi Agent chạy trên cùng host với Control Plane (`aiotlab-rack`), liên lạc diễn ra qua `127.0.0.1:9100` với Header `X-LabOps-Agent-Secret` được sinh ngẫu nhiên 32 bytes khi Agent khởi động, lưu trong RAM hoặc `.env.local`.
2. **Remote Host Communication (Workstation):**
   - Khi giao tiếp với Server Workstation qua mạng, toàn bộ traffic đi xuyên qua đường hầm **WireGuard VPN** (IP nội bộ `10.88.40.X`), có xác thực token Bearer hoặc TLS.
3. **Audit Logging:**
   - Mọi yêu cầu can thiệp container (start, stop, inspect, delete) đều được ghi nhật ký với định danh người thực hiện (`actor`), địa chỉ IP nguồn và dấu thời gian RFC3339.

