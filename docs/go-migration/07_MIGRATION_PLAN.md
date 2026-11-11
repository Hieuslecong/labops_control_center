# KẾ HOẠCH TRIỂN KHAI VÀ CHUYỂN ĐỔI TỪNG BƯỚC (PHASE 7 & 15)

---

## 1. CÁC GIAI ĐOẠN CHUYỂN ĐỔI (STAGES)

### Stage A: Go Agent Read-Only Telemetry (Triển khai đầu tiên)
1. Xây dựng package Go `internal/host`, `internal/gpu`, `internal/process`, `internal/docker`.
2. Tạo binary `labops-agent` chạy tại cổng nội bộ `127.0.0.1:9100`.
3. Agent chỉ đọc dữ liệu từ `/proc`, Docker socket và GPU. Hoàn toàn KHÔNG thực hiện thay đổi nào (Mutation = 0).
4. So sánh số liệu giữa Python collector và Go collector để đảm bảo tính chính xác 100%.

### Stage B: Chuyển nguồn Telemetry của Python sang Go Agent
1. Trong Python `HostMonitorAdapter`, nếu phát hiện Go Agent đang chạy tại `http://127.0.0.1:9100`, sử dụng dữ liệu từ Go Agent thay vì chạy subprocess `nvidia-smi` và `psutil`.
2. Giảm ngay lập tức thời gian phản hồi của `GET /api/v1/servers` từ **1,069 ms** xuống còn **< 5 ms**.

### Stage C: Docker Workspace Inspection & Lifecycle
1. Chuyển logic kiểm tra container, xác minh port SSH, kiểm tra cgroups từ Python subprocess sang Go Docker SDK.
2. Kiểm thử với container thực tế `ws-testacc2332`.

### Stage D: Systemd Service Deployment & Rollback Plan
1. Tạo file cấu hình systemd `deploy/labops-agent.service`.
2. Cung cấp script cài đặt `install-agent.sh`, script kiểm tra `verify-agent.sh` và script khôi phục `rollback-agent.sh`.
3. Kiểm thử tắt Agent để xác nhận Python tự động fallback về cơ chế cũ một cách mượt mà không làm sập hệ thống.

