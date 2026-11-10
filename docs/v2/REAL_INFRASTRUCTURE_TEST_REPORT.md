# BÁO CÁO KIỂM THỬ HẠ TẦNG VẬT LÝ THỰC TẾ (REAL INFRASTRUCTURE TEST REPORT)

**Ngày kiểm thử:** `2026-11-10`  
**Máy chủ được kiểm tra trực tiếp:** `aiotlab-rack` (Role: `track` — Server Rack 3 GPU)  
**Người dùng thực thi:** `aiot-1@aiotlab-rack`

---

## 1. Thông tin phần cứng & hệ điều hành thực tế trên máy `aiotlab-rack`

Mọi thông số dưới đây được thu thập trực tiếp từ kernel Linux (`/etc/os-release`, `/proc/cpuinfo`, `/proc/meminfo`, `/proc/mounts`, `ip -br addr`) và driver NVIDIA (`nvidia-smi`) trên máy thật:

| Hạng mục | Giá trị xác minh thực tế trên máy `aiotlab-rack` | Nguồn kiểm chứng |
|---|---|---|
| **Hostname vật lý** | `aiotlab-rack` | `/etc/hostname` |
| **Vai trò trong LabOps** | `track` (`Server 01 — Server Rack (aiotlab-rack • Cục bộ)`) | `settings.LOCAL_HOST_ID == "track"` |
| **Hệ điều hành** | `Debian GNU/Linux 13 (trixie)` — Phiên bản `13.7` | `/etc/os-release` |
| **Kernel** | `Linux 6.12.116+deb13-amd64 x86_64` | `uname -r` |
| **Vi xử lý (CPU)** | `2× Intel(R) Xeon(R) Silver 4410Y` (`24 cores` vật lý / `48 threads`) | `lscpu` / `/proc/cpuinfo` |
| **Bộ nhớ RAM** | `125.5 GiB` (`128,487 MiB` tổng, ~`2.9 GiB` đang sử dụng) | `free -m` / `psutil.virtual_memory()` |
| **Giao diện mạng vật lý** | `eno1` (`UP`, IP LAN: `192.168.167.250/21`), `eno2` (`DOWN`) | `ip -br addr` |
| **Giao diện WireGuard** | Chưa khởi tạo (`wg0` không tồn tại trên host hiện tại) | `ip -br addr` |
| **Ổ đĩa & Phân vùng** | `/dev/sda2` mount tại `/` (`ext4`, tổng `833 GiB`, đã dùng `11 GiB` (`2%`), trống `780 GiB`) | `df -h /` & `/proc/mounts` |
| **NVIDIA Driver & CUDA** | Driver `550.163.01` — CUDA `12.4` | `nvidia-smi` |

---

## 2. Kiểm kê GPU vật lý thực tế trên `aiotlab-rack` (3× NVIDIA A30 24GB)

Kết quả truy vấn trực tiếp từ `nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw,power.limit,pci.bus_id --format=csv`:

| GPU Index | Tên GPU | GPU UUID thực tế | PCI Bus ID | VRAM (Used / Total) | Nhiệt độ | Công suất (Draw / Limit) | Trạng thái MIG | Tiến trình đang chạy |
|---|---|---|---|---|---|---|---|---|
| **GPU 0** | `NVIDIA A30` | `GPU-339df6af-ed13-f42e-eb9e-2b671c387a30` | `00000000:2A:00.0` | `1 MiB / 24576 MiB` | `35°C` | `31.49 W / 165.00 W` | `Disabled` | `0 process` (`Idle`) |
| **GPU 1** | `NVIDIA A30` | `GPU-88ffcc1c-edfd-b71d-1915-f7cefd868227` | `00000000:3D:00.0` | `1 MiB / 24576 MiB` | `32°C` | `29.40 W / 165.00 W` | `Disabled` | `0 process` (`Idle`) |
| **GPU 2** | `NVIDIA A30` | `GPU-cfd02713-d67e-4921-e14e-c6b626728af1` | `00000000:BD:00.0` | `1 MiB / 24576 MiB` | `32°C` | `29.93 W / 165.00 W` | `Disabled` | `0 process` (`Idle`) |

### Đối chiếu với mô tả hạ tầng của người dùng (3 máy vật lý, tổng 4 GPU NVIDIA A30):
- **Máy số 1 (`aiotlab-rack` / `track`):** **ĐÃ XÁC MINH THẬT (`PASS_REAL`)** — Là máy Server Rack hiện tại, sở hữu **3× NVIDIA A30 24GB** như bảng trên.
- **Máy số 2 & Máy số 3 (2 máy Workstation từ xa, sở hữu 1× NVIDIA A30 24GB còn lại):** **CHƯA THỂ XÁC MINH TRÊN HẠ TẦNG THẬT (`NOT_VERIFIED` / `BLOCKED`)** — Hiện chưa kết nối mạng VPN/Node Agent về `aiotlab-rack`. Backend LabOps báo cáo trung thực trạng thái của `workstation` là `unknown` (*"Chưa kết nối agent thu thập dữ liệu từ xa"*), hoàn toàn không tự bịa số liệu CPU/RAM/GPU cho máy từ xa.

---

## 3. Kiểm thử Canary trực tiếp trên GPU thật của `aiotlab-rack`

Đã thực thi bài kiểm thử canary tự động `test_real_host_aiotlab_rack_gpu_inventory_and_canary_reservation` trong [backend/tests/test_v2_comprehensive.py](file:///home/aiot-1/admin_labops_control_center/backend/tests/test_v2_comprehensive.py):

1. **Bước 1 — Đọc trực tiếp phần cứng qua `HostMonitorAdapter` và `NVMLAdapter`:**
   - Xác nhận `settings.LOCAL_HOST_ID == "track"`.
   - Xác nhận máy `track` có `status == "online"`, `gpu_count == 3`, và toàn bộ 3 GPU trả về đều có `server_id == "track"`, tên `NVIDIA A30`, UUID bắt đầu bằng `GPU-`.
2. **Bước 2 — Cấp phát Canary độc quyền (`FULL_GPU_EXCLUSIVE`) trên GPU thật số 0 (`GPU-339df6af-ed13-f42e-eb9e-2b671c387a30`):**
   - Gọi `GPUSchedulerService.allocate_gpu(gpu_id="gpu-0", gpu_uuid="GPU-339df6af-ed13-f42e-eb9e-2b671c387a30", server_id="track", ...)`.
   - Do `nvidia-smi` xác nhận GPU 0 đang có `0` tiến trình bên ngoài chiếm dụng, Scheduler cấp phát thành công với `mode="FULL_GPU_EXCLUSIVE"`, `status="RESERVED"`, `runtime_verification="PENDING_VERIFICATION"`.
3. **Bước 3 — Kiểm thử chặn cấp trùng (Double Allocation) trên cùng UUID thật:**
   - Gửi yêu cầu thứ hai từ một thành viên khác xin cấp phát cùng `GPU-339df6af-ed13-f42e-eb9e-2b671c387a30`.
   - Scheduler ném `AllocationConflictError` ngay lập tức, bảo vệ tính độc quyền của GPU.
4. **Bước 4 — Thu hồi Canary & Khôi phục trạng thái sạch:**
   - Gọi `release_gpu()` và xác nhận trạng thái trở về `RELEASED`, không để lại rác dữ liệu trên hệ thống thật.

---

## 4. Trạng thái các dịch vụ và binary trên `aiotlab-rack`

| Thành phần / Công cụ | Trạng thái trên `aiotlab-rack` | Hành vi hiện tại của LabOps V2 | Phân loại |
|---|---|---|---|
| **Python 3 + `psutil`** | Đã cài (`/usr/bin/python3` v3.13.5) | Chạy trực tiếp Backend API & Collector thật | `PASS_REAL` |
| **Node.js + Next.js 16** | Đã có (`node` v22.21.1, `node_modules` đầy đủ) | Chạy trực tiếp Frontend Dashboard & `tsc --noEmit` | `PASS_REAL` |
| **NVIDIA Driver + `nvidia-smi`** | Đã cài (`v550.163.01`, 3× A30 hoạt động tốt) | Đọc trực tiếp telemetry & kiểm tra tiến trình GPU thật | `PASS_REAL` |
| **Docker Engine + NVIDIA Container Toolkit** | **Chưa cài đặt** (`docker: not found`) | Workspace & GPU Job báo trạng thái trung thực `PENDING_RUNTIME` / `PENDING_VERIFICATION` | `BLOCKED` (Chờ cài Docker trên host) |
| **WireGuard (`wg`, `wg-quick`)** | **Chưa cài đặt** (`wg: not found`, chưa có `wg0`) | `WireGuardAdapter` từ chối tạo peer giả, báo lỗi thiếu binary/interface trung thực | `BLOCKED` (Chờ cài `wireguard-tools`) |
| **Trivy Scanner (`trivy`)** | **Chưa cài đặt** (`trivy: not found`) | `SecurityScannerService` fail-closed (`FAILED` / `NOT_CONFIGURED`), không bao giờ trả `PASSED` giả | `BLOCKED` (Chờ cài binary `trivy`) |
| **PostgreSQL (`psql`, `postgres`)** | **Chưa cài đặt** (`psql: not found`) | Hệ thống hoạt động bền vững trên JSON Store có khoá `fcntl` và hàm `reconcile_json_stores()`; bộ migration SQL `001..004` đã sẵn sàng | `BLOCKED` (Chờ cài PostgreSQL) |
| **Arcane (`v1.1.0`) & Beszel (`v0.9.1`)** | **Chưa chạy container** (do chưa cài Docker) | Adapter trả về `NOT_CONFIGURED` trung thực | `BLOCKED` (Chờ cài Docker) |
