# QUẢN TRỊ DỮ LIỆU VÀ QUYỀN SỞ HỮU TRẠNG THÁI (PHASE 6 & 10)

---

## 1. NGUYÊN TẮC: MỘT NGUỒN GHI DUY NHẤT (SINGLE ACTIVE WRITER)

Trong quá trình chuyển đổi lai song song Python và Go, **tuyệt đối không cho phép 2 backend cùng ghi đồng thời vào một file JSON** mà không có transaction hay distributed lock tương thích:

| Tài nguyên | Active Writer (Quyền Ghi) | Reader (Quyền Đọc) | Cơ chế kiểm soát |
|---|---|---|---|
| **Members & Credentials** | Python (`labops_service.py`) | Python API & Go Inspector | Python quản lý JSON `members.json` với `fcntl.flock` |
| **SSH Endpoints** | Python (`labops_service.py`) | Python API & Go Inspector | Phân bổ dải cổng `22001-22050` |
| **Workspaces Runtime State** | **Go Agent (`internal/docker`)** | Python API & Go Agent | Go Agent truy vấn trực tiếp Docker Engine `/var/run/docker.sock` |
| **Host Telemetry** | **Go Agent (`internal/host`)** | Python Control Plane & UI | Go đọc trực tiếp `/proc`, Python nhận dữ liệu qua cache/API |
| **GPU Telemetry** | **Go Agent (`internal/gpu`)** | Python Control Plane & UI | Go đọc trực tiếp NVIDIA metrics |
| **VPN Peers & Subnet** | **`labops-wg-easy` Container** | Python Adapter / Go Adapter | wg-easy là nguồn chân lý duy nhất cho WireGuard |

---

## 2. KẾ HOẠCH BẢO LƯU DỮ LIỆU VÀ DI CƯ SANG POSTGRESQL

- Hệ thống hiện tại có PostgreSQL 17 đang chạy tại `127.0.0.1:5432` (`postgresql@17-main.service`).
- Trước khi chuyển bất kỳ dữ liệu JSON nào sang SQL, phải thực hiện sao lưu toàn bộ thư mục `backend/data/` thành bản snapshot có gắn tem thời gian (vd: `backend/data_backup_$(date +%s)`).
- Không được tự động xóa bỏ file JSON cũ trước khi kiểm chứng tính tương đương 100% của bản sao dữ liệu.

