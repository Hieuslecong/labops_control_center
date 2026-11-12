# LABOPS THREAT MODEL & SECURITY HARDENING SPECIFICATION

## 1. PHÂN TÍCH RỦI RO THEO KHUNG STRIDE

| Nguy cơ (STRIDE) | Mối đe dọa tiềm tàng | Biện pháp giảm thiểu của LabOps Zero-Trust |
|---|---|---|
| **Spoofing (Giả mạo danh tính)** | Máy chủ độc hại trong mạng LAN cố tình kết nối giả mạo là `workstation` hoặc `track`. | **Bắt buộc mTLS TLS 1.3**: Gateway xác thực chuỗi chứng chỉ ký bởi LabOps Root CA bí mật. Tự động trích xuất Node ID từ DNS SAN `<node_id>.node.labops.local` và đối chiếu nghiêm ngặt với `NodeHello.node_id`. |
| **Tampering (Can thiệp dữ liệu)** | Kẻ tấn công nằm giữa (MITM) đánh chặn và sửa đổi số liệu telemetry hoặc chèn lệnh điều khiển độc hại. | **Mã hóa điểm cuối (End-to-End TLS 1.3)**: Sử dụng các bộ mã hóa AEAD hiện đại (AES-128-GCM, CHACHA20-POLY1305). Dữ liệu truyền tải được xác thực tính toàn vẹn ở tầng transport. |
| **Repudiation (Chối bỏ trách nhiệm)** | Quản trị viên hoặc tiến trình từ chối việc đã phát lệnh dừng/kill tác vụ hoặc thay đổi workspace. | **Audit Trail có cấu trúc**: Mỗi lệnh mang `command_id`, `request_id`, `actor_id`, `idempotency_key` và timestamp chính xác. Cả Gateway và Node Agent đều ghi nhận log kiểm toán với hash tương ứng. |
| **Information Disclosure (Lộ lọt thông tin)** | Dữ liệu nhạy cảm (mật khẩu, token, biến môi trường, khóa SSH) bị rò rỉ qua dòng lệnh tiến trình. | **Lọc và làm sạch chuỗi lệnh (Sanitization Engine)**: Bộ thu thập tiến trình tự động che chắn các tham số nhạy cảm (`--password`, `--token`, `--key`, `api_key`) bằng ký tự `********`. File khóa bí mật được lưu với quyền nghiêm ngặt `0600`. |
| **Denial of Service (Từ chối dịch vụ)** | Tràn ngập bản tin telemetry hoặc lệnh làm nghẽn kênh truyền dẫn và treo Control Plane. | **Quản lý phiên và Buffer có kiểm soát**: Giới hạn kích thước gói tin tối đa 32MB, cơ chế ping/pong heartbeat định kỳ 5s với ngưỡng phát hiện lỗi kết nối. Thực thi bất đồng bộ có timeout/deadline nghiêm ngặt. |
| **Elevation of Privilege (Leo thang đặc quyền)** | Kẻ tấn công khai thác API điều khiển để chạy các lệnh bash/shell tùy ý với quyền root trên máy chủ. | **Tuyệt đối không nhận lệnh shell**: Chỉ hỗ trợ các hành động định kiểu (typed actions) trong Allowlist cố định. Không có bất kỳ interface nào chấp nhận tham số là chuỗi script hay câu lệnh bash. |

---

## 2. NGUYÊN TẮC ZERO-TRUST CHO COMPUTE NODE

1. **Không mở cổng inbound (Zero Inbound Exposure)**:
   - Các compute node độc lập (Workstations) không bao giờ phải mở bất kỳ cổng lắng nghe nào ra mạng ngoài hoặc mạng LAN nội bộ phục vụ quản trị.
   - Toàn bộ kết nối được khởi xướng từ bên trong (outbound) tới IP Gateway `192.168.167.250:9443`.
2. **Quyền hạn tối thiểu (Principle of Least Privilege)**:
   - Go Agent chạy dưới người dùng kỹ thuật không có quyền root tùy tiện (`aiot-1`).
   - Các thao tác can thiệp hệ thống nhạy cảm (Docker daemon, kill process) được kiểm soát bởi bộ bảo vệ (Safeguards) ngăn chặn triệt để việc tác động vào container và tiến trình hệ thống trọng yếu (`init`, `systemd`, `postgres`, `wg-easy`).
3. **Phân quyền truy cập chứng chỉ**:
   - Khóa bí mật (`client.key`, `labops-ca.key`) luôn được thiết lập chế độ cấp quyền tệp tin `0600` (`-rw-------`) chỉ thuộc quyền sở hữu của tiến trình vận hành.
