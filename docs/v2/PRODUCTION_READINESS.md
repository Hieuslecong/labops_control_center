# ĐÁNH GIÁ SẴN SÀNG TRIỂN KHAI PRODUCTION (PRODUCTION READINESS REPORT) — LABOPS CONTROL CENTER V2

**Ngày cập nhật:** `2026-11-10`  
**Máy chủ hiện tại:** `aiotlab-rack` (`track` — Server Rack 3× NVIDIA A30 24GB, IP `192.168.167.250`)  
**Trạng thái kiểm thử tự động & Canary phần cứng thật:** `68/68 PASS` (`0 errors`, `0 warnings`, `tsc --noEmit` `0 errors`)

---

## 1. Những hạng mục ĐÃ SẴN SÀNG VÀ ĐÃ XÁC MINH THẬT (`PASS_REAL`)

1. **Nhận diện đúng phần cứng máy chủ `aiotlab-rack` (`track`):**
   - Thu thập chính xác thời gian thực CPU (`2× Intel Xeon Silver 4410Y`, 24 cores / 48 threads), RAM (`125.5 GiB`), ổ đĩa (`EXT4 /dev/sda2`, `833 GiB`), mạng (`192.168.167.250`) và **3× GPU NVIDIA A30 24GB** (`GPU-339df6af...`, `GPU-88ffcc1c...`, `GPU-cfd02713...`).
   - Không còn dữ liệu giả (`DEMO_MODE=false` mặc định); các máy trạm từ xa chưa kết nối (`workstation`) được hiển thị trung thực là `unknown`.
2. **Bảo mật API, RBAC, Ownership và BFF Proxy:**
   - Xác thực bắt buộc `HMAC-SHA256` (Fail-closed `503`/`401`/`403`), tách biệt hoàn toàn token người dùng và token máy trạm (`LABOPS_NODE_AGENT_TOKEN`), chống CSRF trên Next.js BFF Proxy, loại bỏ hoàn toàn CORS wildcard `*`.
3. **Bộ lập lịch GPU độc quyền (`FULL_GPU_EXCLUSIVE`) và Kế toán GPU-hours:**
   - Khoá đồng bộ cấp hệ điều hành (`fcntl.flock`), kiểm tra tiến trình NVML ngoài hệ thống trước khi cấp phát, tách biệt minh bạch `allocated_hours`, `runtime_hours`, và `idle_hold_hours`.
4. **Kiểm soát cô lập Workspace & Quản lý khóa SSH:**
   - Chặn tuyệt đối container `privileged`, chặn mount `/var/run/docker.sock`, `/etc`, hoặc thư mục của thành viên khác.
   - Đăng ký/thu hồi khóa SSH công khai chuẩn OpenSSH kèm kiểm tra SHA256 fingerprint và chặn tải lên khóa bí mật (private key).

---

## 2. Danh sách hạng mục `CHƯA THỂ XÁC MINH TRÊN HẠ TẦNG THẬT` (`BLOCKED` / `NOT_VERIFIED`) & Lệnh nghiệm thu khi cài đặt đủ daemon

Do máy chủ vật lý `aiotlab-rack` hiện tại là bản cài đặt Debian 13 tối giản (chưa cài `docker`, `wireguard-tools`, `trivy`, `postgresql`) và 2 máy Workstation từ xa chưa nối mạng về `aiotlab-rack`, các tích hợp phụ thuộc vào daemon bên ngoài được đánh dấu rõ là **`CHƯA THỂ XÁC MINH TRÊN HẠ TẦNG THẬT`**.

Dưới đây là quy trình cài đặt và lệnh nghiệm thu cho quản trị viên hệ thống (`root` / `sudo`):

### 2.1. Cài đặt Docker Engine & NVIDIA Container Toolkit trên `aiotlab-rack`
- **Hiện trạng:** Chưa có binary `docker` trên `aiotlab-rack`; các Workspace và GPU Job hiện dừng ở trạng thái trung thực `PENDING_RUNTIME` / `PENDING_VERIFICATION`.
- **Lệnh nghiệm thu sau khi cài đặt:**
  ```bash
  docker info
  docker run --rm --gpus '"device=GPU-339df6af-ed13-f42e-eb9e-2b671c387a30"' nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi -L
  ```

### 2.2. Cài đặt Trivy Scanner (`/usr/local/bin/trivy`)
- **Hiện trạng:** Chưa có binary `trivy`; `SecurityScannerService` đang fail-closed (`FAILED` / `NOT_CONFIGURED`).
- **Lệnh nghiệm thu sau khi cài đặt:**
  ```bash
  trivy --version
  # Sau khi có trivy, gửi yêu cầu quét commit SHA 40 ký tự qua API /api/v1/v2/scanner/submit
  ```

### 2.3. Cài đặt WireGuard (`wireguard-tools`) và khởi tạo `wg0`
- **Hiện trạng:** Chưa có lệnh `wg` và interface `wg0` trên `aiotlab-rack`.
- **Lệnh nghiệm thu sau khi cài đặt:**
  ```bash
  sudo apt-get update && sudo apt-get install -y wireguard-tools
  sudo wg show wg0
  ```

### 2.4. Cài đặt PostgreSQL 16 và chạy Migration V2
- **Hiện trạng:** Đang chạy trên JSON Store có khoá `fcntl` và `reconcile_json_stores()`. Toàn bộ file SQL migration (`backend/migrations/001..004.sql`) đã hoàn tất.
- **Lệnh nghiệm thu sau khi cài đặt PostgreSQL:**
  ```bash
  psql "$DATABASE_URL" -f backend/migrations/001_initial_schema.sql
  psql "$DATABASE_URL" -f backend/migrations/002_v31_enterprise_upgrade.sql
  psql "$DATABASE_URL" -f backend/migrations/003_wireguard_access_hardening.sql
  psql "$DATABASE_URL" -f backend/migrations/004_labops_v2_complete.sql
  ```

### 2.5. Kết nối 2 máy Workstation từ xa (1× NVIDIA A30 còn lại)
- **Hiện trạng:** Mới chỉ có máy `aiotlab-rack` (`track` — 3× NVIDIA A30) đang chạy cục bộ.
- **Cách nghiệm thu:** Chạy `labops-node-agent` trên 2 máy Workstation gửi heartbeat kèm `X-Node-Agent-Token` về `http://192.168.167.250:8000/api/v1/v2/hosts/heartbeat`, sau đó kiểm tra `/api/v1/v2/hosts` chuyển sang `ONLINE` và tổng số GPU toàn phòng Lab đạt `4× NVIDIA A30`.
