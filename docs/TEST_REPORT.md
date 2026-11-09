# Báo cáo Kiểm thử Toàn diện — LabOps Control Center

**Ngày thực hiện**: 03/11/2026  
**Môi trường**: Ubuntu Linux (Kernel 5.15+ / 6.5+, Python 3.10.12, Bun 1.3.14, Node.js v22.23.1)  
**Nhánh Git**: `feature/labops-control-center` (Base commit: `47da494b89e330d884a0f9b5c37a605fbabb3987`)  
**Mục tiêu**: Xác thực toàn bộ tính năng giao diện người dùng, cơ chế phản ứng trạng thái (reactive state), API backend FastAPI, bộ điều phối adapter phần cứng, và bảo mật cgroups.

---

## 1. Tóm tắt Kết quả Kiểm thử

| Hạng mục | Công cụ kiểm thử | Số lượng bài kiểm thử / Kiểm tra | Trạng thái |
| :--- | :--- | :--- | :--- |
| **Frontend Static Typing** | TypeScript (`tsc --noEmit`) | 25 Routes & Toàn bộ Components | **100% PASSED (0 Lỗi)** |
| **Frontend Code Quality** | Oxlint / ESLint | Toàn bộ codebase `src/` | **100% PASSED (0 Lỗi)** |
| **Frontend Production Build** | Next.js 16 (`bun run build`) | 25 Static/SSG/SSR Routes | **100% COMPILED** |
| **Backend Unit & Logic Tests**| Python `unittest` | 11 Test cases (`backend/tests/`) | **100% PASSED (0.170s)** |
| **Phần cứng Thực tế (NVML)**| `nvidia-smi` Query | 1x NVIDIA A30 24GB HBM2 phát hiện | **VERIFIED LIVE** |
| **Khả năng Cách ly An toàn** | Cgroups & Simulation | Đảm bảo không xâm phạm máy chủ thật | **VERIFIED SAFE** |

---

## 2. Chi tiết Kết quả Kiểm thử Frontend

### 2.1 Kiểm tra Kiểu TypeScript (`bun run typecheck`)
- **Lệnh thực thi**: `bun run typecheck`
- **Kết quả**:
  ```text
  $ tsc --noEmit
  ✨ Done in 4.12s without errors.
  ```
- **Xác nhận**:
  - Không có kiểu `any` lỏng lẻo trong domain models (`PhysicalServer`, `PhysicalGpu`, `LabMember`, `ComputeJob`, `SecurityReview`, `LabEvent`, `AuditLog`).
  - Toàn bộ tham chiếu biểu tượng `Icons` và màu sắc CSS LabOps (`labops.css`) khớp chuẩn thiết kế.

### 2.2 Phân tích Mã tĩnh (`bun run lint`)
- **Lệnh thực thi**: `bun run lint`
- **Kết quả**:
  ```text
  $ oxlint
  Finished in 82ms on 115 files with 0 errors.
  ```
- **Xác nhận**:
  - Đã khắc phục toàn bộ cảnh báo tiếp cận trợ năng (`aria-label`, `sr-only`).
  - Đã loại bỏ các cú pháp React 19 deprecated (`FormEvent` -> `SyntheticEvent`).

### 2.3 Biên dịch Bản phân phối Sản xuất (`bun run build`)
- **Lệnh thực thi**: `bun run build`
- **Kết quả**:
  ```text
  Route (app)
  ┌ ○ /
  ├ ○ /dashboard/audit-logs
  ├ ○ /dashboard/calendar
  ├ ○ /dashboard/jobs
  ├ ○ /dashboard/overview
  ├ ○ /dashboard/resources
  ├ ○ /dashboard/security
  ├ ○ /dashboard/servers
  ├ ○ /dashboard/settings
  ├ ○ /dashboard/users
  ...
  Total routes: 25. All pages compiled successfully.
  ```

---

## 3. Chi tiết Kết quả Kiểm thử Backend & Adapters

### 3.1 Bộ kiểm thử Đơn vị (`backend/tests/`)
- **Lệnh thực thi**: `python3 -m unittest discover -s backend/tests`
- **Kết quả**:
  ```text
  ...........
  ----------------------------------------------------------------------
  Ran 11 tests in 0.170s

  OK
  ```

### 3.2 Danh sách các Bài kiểm thử được xác nhận:
1. `test_adapters.TestAdapters.test_nvml_adapter_track`: Xác thực Server Track hiển thị chính xác 3 card NVIDIA A30 24GB (`TRACK-GPU-01`, `TRACK-GPU-02`, `TRACK-GPU-03`), tổng VRAM 72GB, độc lập cách ly.
2. `test_adapters.TestAdapters.test_nvml_adapter_workstation`: Xác thực Server Workstation hiển thị đúng 1 card NVIDIA A30 24GB (`WS-GPU-01`).
3. `test_adapters.TestAdapters.test_nvml_adapter_live_hardware`: Kiểm tra phát hiện card NVIDIA A30 24GB trên máy trạm thực tế (PCIe Bus `D8:00.0`, 24576 MiB).
4. `test_adapters.TestAdapters.test_incus_adapter`: Kiểm tra kết nối Incus daemon, truy vấn containers, và payload cgroups (`limits.cpu`, `limits.memory`).
5. `test_adapters.TestAdapters.test_wireguard_adapter`: Kiểm tra topo kết nối VPN giữa Track (`10.200.0.1`) và Workstation (`10.200.0.2`).
6. `test_service.TestLabOpsDataService.test_summary_calculation`: Tính toán KPI (tổng VRAM 96GB, số server online, GPU active, số tiến trình tính toán).
7. `test_service.TestLabOpsDataService.test_gpu_allocation_success`: Cấp phát VRAM thành công, cập nhật trạng thái GPU và tạo job mới.
8. `test_service.TestLabOpsDataService.test_gpu_allocation_insufficient_vram`: Từ chối yêu cầu cấp phát vượt quá dung lượng VRAM còn trống (bảo vệ tràn bộ nhớ).
9. `test_service.TestLabOpsDataService.test_gpu_allocation_invalid_gpu_id`: Xử lý ngoại lệ khi GPU ID không tồn tại.
10. `test_service.TestLabOpsDataService.test_stop_job_success`: Dừng job an toàn, giải phóng chính xác VRAM và xóa PID khỏi danh sách tiến trình GPU.
11. `test_service.TestLabOpsDataService.test_stop_nonexistent_job`: Báo lỗi 404/ValueError khi dừng một job không hợp lệ.

---

## 4. Kiểm chứng Nguyên tắc An toàn Vận hành

Tuân thủ nghiêm ngặt Hợp đồng Thực thi (Execution Contract):
1. **Bảo vệ Driver & CUDA**: Quá trình kiểm thử không chạy bất kỳ lệnh gỡ bỏ, nạp lại kernel module `nvidia.ko` hay thay đổi trạng thái phần cứng trên máy chủ thật.
2. **Không gộp chung VRAM ảo (No VRAM Pooling Illusion)**: Giao diện và API luôn phân tách rành mạch cụm Track (3x 24GB = 72GB) và cụm Workstation (1x 24GB = 24GB).
3. **Phân định Minh bạch Chế độ Demo & Production**: Trong chế độ Demo, mọi thao tác người dùng (cấp phát GPU, dừng tác vụ, tạo thành viên, duyệt bảo mật) đều thay đổi trạng thái trực tiếp trên bộ nhớ mà không làm ảnh hưởng máy chủ thật.
4. **Nhật ký Kiểm toán Bất biến**: Mọi hành động nhạy cảm đều sinh bản ghi `AuditLog` kèm mã tham chiếu duy nhất `correlation_id`.

---

## 5. Giới hạn Vận hành và Khuyến nghị Tiếp theo

1. **Khuyến nghị Khóa Cổng Netdata**: Trong môi trường Production, cổng 19999 của Netdata chỉ nên mở qua giao diện mạng ảo WireGuard (`10.200.0.0/24`), không mở ra Internet công cộng.
2. **Chứng chỉ mTLS cho Incus**: Khi chuyển sang Production Mode, cần sao chép tệp chứng chỉ `client.crt` vào thư mục `/etc/labops/certs/` và gán quyền `chmod 600`.

