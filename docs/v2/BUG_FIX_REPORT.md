# BÁO CÁO PHÁT HIỆN VÀ KHẮC PHỤC LỖI (BUG FIX REPORT) — LABOPS CONTROL CENTER V2

**Ngày cập nhật:** `2026-11-10`  
**Máy chủ kiểm thử:** `aiotlab-rack` (`track` — 3× NVIDIA A30 24GB)

Dưới đây là danh sách các lỗi thực tế được phát hiện trong quá trình kiểm thử độc lập trên máy chủ `aiotlab-rack` và đã được khắc phục hoàn toàn kèm kiểm thử hồi quy (`68/68 tests PASS`).

---

## BUG-001 — Nhận diện sai máy chủ vật lý đang chạy (`aiotlab-rack` 3 GPU bị nhận nhầm thành `workstation`)

- **ID:** `BUG-001`
- **Mức độ:** `P0` (Sai lệch dữ liệu hạ tầng cốt lõi)
- **Chức năng:** Hosts & GPU Inventory / Telemetry Collector
- **Môi trường:** Máy vật lý `aiotlab-rack` (`Debian 13.7`, 3× NVIDIA A30 24GB)
- **Các bước tái hiện:**
  1. Khởi chạy backend trên máy chủ `aiotlab-rack` (có `/etc/hostname` là `aiotlab-rack` và 3 GPU NVIDIA A30).
  2. Gọi API `GET /api/v1/servers` và `GET /api/v1/gpus`.
- **Kết quả thực tế (trước khi sửa):**
  - Hệ thống gán cứng máy cục bộ là `workstation` (`Server 02 — Workstation`), gắn cả 3 GPU NVIDIA A30 của `aiotlab-rack` vào `workstation`, trong khi báo cáo máy `track` (`Server 01 — Track Server`) là `unknown` (0 GPU) và gán cứng mô tả CPU/ổ đĩa giả (`ZFS Mirror`, `100.200.1.11`).
- **Kết quả mong đợi:**
  - Hệ thống phải tự động phát hiện hostname `aiotlab-rack` chính là máy chủ `track` (`Server 01 — Server Rack`), hiển thị `track` ở trạng thái `online` với đúng 3 GPU NVIDIA A30 (`GPU-339df6af...`, `GPU-88ffcc1c...`, `GPU-cfd02713...`), đúng IP thực tế (`192.168.167.250`) và đúng ổ đĩa thực tế (`EXT4 (/dev/sda2)`), đồng thời chuyển `workstation` (máy trạm từ xa chưa kết nối) sang trạng thái `unknown`.
- **Nguyên nhân:**
  - `backend/app/core/config.py`, `host_monitor_adapter.py`, `nvml_adapter.py`, `process_monitor_adapter.py`, và `labops_service.py` gán cứng `workstation` là máy cục bộ và chặn thu thập nếu `server_id == "track"`.
- **File/code liên quan:**
  - [backend/app/core/config.py](file:///home/aiot-1/admin_labops_control_center/backend/app/core/config.py)
  - [backend/app/adapters/host_monitor_adapter.py](file:///home/aiot-1/admin_labops_control_center/backend/app/adapters/host_monitor_adapter.py)
  - [backend/app/adapters/nvml_adapter.py](file:///home/aiot-1/admin_labops_control_center/backend/app/adapters/nvml_adapter.py)
  - [backend/app/adapters/process_monitor_adapter.py](file:///home/aiot-1/admin_labops_control_center/backend/app/adapters/process_monitor_adapter.py)
  - [backend/app/services/labops_service.py](file:///home/aiot-1/admin_labops_control_center/backend/app/services/labops_service.py)
  - [backend/app/services/host_registry.py](file:///home/aiot-1/admin_labops_control_center/backend/app/services/host_registry.py)
  - [src/features/labops/store.ts](file:///home/aiot-1/admin_labops_control_center/src/features/labops/store.ts)
- **Cách sửa:**
  - Bổ sung hàm `_detect_local_host_id()` trong `config.py` đọc `/etc/hostname` (nhận diện `aiotlab-rack` -> `"track"`) và cho phép ghi đè qua biến môi trường `LABOPS_LOCAL_HOST_ID`.
  - Cập nhật `HostMonitorAdapter`, `NVMLAdapter`, `ProcessMonitorAdapter`, `LabOpsService`, và `HostRegistryService` để thu thập dữ liệu thật cho `settings.LOCAL_HOST_ID` (`"track"`), đọc phân vùng thật từ `/proc/mounts` và IP thật từ socket/interface, đồng thời trả về `unknown` trung thực cho máy từ xa chưa kết nối.
- **Test đã chạy lại:**
  - `test_real_host_aiotlab_rack_gpu_inventory_and_canary_reservation` trong [backend/tests/test_v2_comprehensive.py](file:///home/aiot-1/admin_labops_control_center/backend/tests/test_v2_comprehensive.py) và toàn bộ `68` tests.
- **Trạng thái:** `FIXED & VERIFIED ON REAL HARDWARE`

---

## BUG-002 — Lỗi `ModuleNotFoundError: No module named 'app'` khi khởi chạy `./start.sh`

- **ID:** `BUG-002`
- **Mức độ:** `P0` (Không khởi động được Backend khi chạy trực tiếp `python3 backend/app/main.py`)
- **Chức năng:** Khởi động dịch vụ Backend (`start.sh` / `backend/app/main.py`)
- **Môi trường:** `aiotlab-rack` (`Python 3.13.5`)
- **Các bước tái hiện:**
  1. Chạy `./start.sh` hoặc `python3 backend/app/main.py` từ thư mục gốc của dự án mà không truyền `PYTHONPATH`.
- **Kết quả thực tế (trước khi sửa):**
  - `ModuleNotFoundError: No module named 'app'` tại dòng 6 của `backend/app/main.py`.
- **Kết quả mong đợi:**
  - Backend tự động nạp thư mục `backend/` vào `sys.path` trước khi import bất kỳ module `app.*` nào và khởi động thành công trên cổng `8000`.
- **Nguyên nhân:**
  - Câu lệnh `from app.core.security import ...` được đặt ở dòng 6 của `backend/app/main.py`, phía trên đoạn khởi tạo `sys.path.insert(0, backend_dir)`.
- **File/code liên quan:**
  - [backend/app/main.py](file:///home/aiot-1/admin_labops_control_center/backend/app/main.py)
  - [start.sh](file:///home/aiot-1/admin_labops_control_center/start.sh)
- **Cách sửa:**
  - Đưa đoạn khởi tạo `sys.path.insert(0, backend_dir)` lên đầu file `backend/app/main.py` trước tất cả các câu lệnh `from app.* import ...`, đồng thời export `PYTHONPATH="$ROOT_DIR/backend"` trong `start.sh`.
- **Test đã chạy lại:**
  - Chạy trực tiếp `python3 backend/app/main.py` và kiểm tra `GET http://127.0.0.1:8000/healthz` trả về `200 OK`.
- **Trạng thái:** `FIXED & VERIFIED`

---

## BUG-003 — Script `./start.sh` và `.husky/pre-push` thất bại do máy chủ không cài `curl` và `bun` toàn cục

- **ID:** `BUG-003`
- **Mức độ:** `P1` (Chặn khởi chạy Frontend và chặn `git push` trên máy chủ tối giản)
- **Chức năng:** DevOps / Khởi động hệ thống & Git Hooks
- **Môi trường:** `aiotlab-rack` (Debian 13.7 tối giản không có `/usr/bin/curl` và không có `bun` trong `/usr/bin`)
- **Các bước tái hiện:**
  1. Chạy `./start.sh` hoặc `git push -u origin feature/labops-control-center`.
- **Kết quả thực tế (trước khi sửa):**
  - `./start.sh: line 55: curl: command not found`, `bun: command not found`
  - `.husky/pre-push: 1: bun: not found`
- **Kết quả mong đợi:**
  - `./start.sh` tự động tìm thấy runtime `node` sẵn có trên máy chủ (hoặc tải Bun bằng `python3 urllib.request` không cần `curl`) và khởi chạy Next.js thành công trên cổng `3000`.
  - `.husky/pre-push` kiểm tra sự tồn tại của `bun` trước khi gọi lệnh.
- **File/code liên quan:**
  - [start.sh](file:///home/aiot-1/admin_labops_control_center/start.sh)
  - [.husky/pre-push](file:///home/aiot-1/admin_labops_control_center/.husky/pre-push)
- **Cách sửa:**
  - Cập nhật `start.sh` tự động dò tìm binary `node` trong `$HOME/.antigravity-ide-server` / `$HOME/.nvm` để chạy `./node_modules/next/dist/bin/next dev`, và dùng Python `urllib.request` + `zipfile` nếu cần tải Bun.
  - Cập nhật `.husky/pre-push` để chỉ chạy `bun run build` khi có `bun` trong `PATH`.
- **Test đã chạy lại:**
  - Chạy `./start.sh` khởi động đồng thời Backend (`:8000`) và Frontend Next.js 16 (`:3000`) đều trả về `HTTP 200 OK`.
- **Trạng thái:** `FIXED & VERIFIED`

---

## BUG-004 — Cảnh báo `ResourceWarning: unclosed file` khi đọc file cấu hình và kho dữ liệu JSON

- **ID:** `BUG-004`
- **Mức độ:** `P2` (Rò rỉ file descriptor khi chạy lâu dài)
- **Chức năng:** Security Scanner, VPN/SSH Key Registry, Host Monitor
- **Môi trường:** `Python 3.13` với cờ `-W error::ResourceWarning`
- **Các bước tái hiện:**
  1. Chạy bộ test với lệnh `python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py'`.
- **Kết quả thực tế (trước khi sửa):**
  - Xuất hiện `ResourceWarning: unclosed file` tại các lời gọi `json.load(open(...))` hoặc `open("/etc/hostname").read()` không dùng `with`.
- **Kết quả mong đợi:**
  - Toàn bộ thao tác mở file phải đóng file descriptor ngay lập tức qua context manager `with open(...) as f:`, đạt `0 ResourceWarning`.
- **File/code liên quan:**
  - [backend/app/core/config.py](file:///home/aiot-1/admin_labops_control_center/backend/app/core/config.py)
  - [backend/app/adapters/host_monitor_adapter.py](file:///home/aiot-1/admin_labops_control_center/backend/app/adapters/host_monitor_adapter.py)
  - [backend/app/services/security_scanner.py](file:///home/aiot-1/admin_labops_control_center/backend/app/services/security_scanner.py)
  - [backend/app/services/vpn_ssh_service.py](file:///home/aiot-1/admin_labops_control_center/backend/app/services/vpn_ssh_service.py)
- **Cách sửa:**
  - Thay thế toàn bộ lệnh `open(...)` trần bằng `with open(..., encoding="utf-8") as f:`.
- **Test đã chạy lại:**
  - `python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v` -> `Ran 68 tests in 12.696s — OK`.
- **Trạng thái:** `FIXED & VERIFIED`
