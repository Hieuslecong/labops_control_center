# LABOPS DEPLOYMENT ROLLBACK & RECOVERY RUNBOOK

## 1. NGUYÊN TẮC AN TOÀN ROLLBACK

Hệ thống được thiết kế theo nguyên lý **Non-Destructive Failover**:
1. Nếu Node Agent hoặc Gateway ngừng hoạt động hoặc bị lỗi kết nối, **tuyệt đối không làm gián đoạn các GPU training jobs, tiến trình của thành viên, kết nối SSH (cổng 22, 1911, 22001-22009) hay mạng WireGuard**.
2. Khi không có kết nối gRPC Gateway, Python Backend tự động kích hoạt cơ chế **Fallback Cục bộ**: đọc số liệu trực tiếp qua Linux `/proc`, `psutil` và `nvidia-smi` trên máy chủ sở tại.
3. Các file cấu hình cũ và binary phiên bản trước được lưu trữ tại `bin/labops-agent.backup`.

---

## 2. QUY TRÌNH THỰC HIỆN ROLLBACK

### Kịch bản A: Dừng hoặc Quay lại phiên bản trước của Agent
Chạy script rollback tự động:
```bash
./deploy/rollback-agent.sh
```

Các bước script thực hiện:
1. Dừng an toàn `labops-agent` qua systemd (`systemctl stop labops-agent`).
2. Nếu có `bin/labops-agent.backup`, khôi phục tệp này và khởi động lại.
3. Nếu không có backup, dừng agent hoàn toàn và chuyển hệ thống về chế độ fallback.
4. Tự động kiểm tra và in kết quả xác nhận từ Python Backend.

### Kịch bản B: Tắt Gateway và Chuyển về Chế độ Quản lý Độc Lập
Nếu muốn vô hiệu hóa Zero-Trust Gateway:
```bash
sudo systemctl stop labops-gateway
sudo systemctl disable labops-gateway
```
Trong cấu hình backend (`backend/.env` hoặc biến môi trường):
- Không cần cấu hình `LABOPS_GATEWAY_HTTP_URL`.
- Backend tiếp tục phục vụ các yêu cầu trên giao diện web sử dụng collector nội bộ.
