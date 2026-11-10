# BÁO CÁO KIỂM TOÁN HIỆN TRẠNG (BASELINE AUDIT — LABOPS CONTROL CENTER V2)

- **Ngày kiểm toán**: `2026-11-10`
- **Nhánh làm việc**: `feature/labops-control-center`
- **Máy chủ vật lý kiểm tra trực tiếp**: Debian GNU/Linux 13 (trixie), Kernel `6.12.116+deb13-amd64`, 2× Intel Xeon Silver 4410Y (24 cores / 48 threads), 125 GiB RAM, **3× NVIDIA A30 24GB** (`GPU-339df6af-ed13-f42e-eb9e-2b671c387a30`, `GPU-88ffcc1c-edfd-b71d-1915-f7cefd868227`, `GPU-cfd02713-d67e-4921-e14e-c6b626728af1`).

---

## 1. Danh sách Phát hiện Kiểm toán (P0 / P1 / P2)

### [P0-01] `Settings.DEMO_MODE` tự động bật ngầm khi chạy `unittest` / `pytest`
- **Tệp & hàm**: `backend/app/core/config.py` (`Settings.DEMO_MODE`)
- **Bằng chứng**: Dòng `if "unittest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"): return True` khiến toàn bộ dịch vụ tự chuyển sang dữ liệu giả (`DEMO`) ngay cả khi đang kiểm thử chế độ production.
- **Mức độ**: `P0 (Critical)`
- **Trạng thái sửa**: `IMPLEMENTED_AND_TESTED` — Đã gỡ bỏ nhánh tự bật `DEMO_MODE` theo `sys.modules`; `DEMO_MODE` chỉ bật khi cả `LABOPS_MODE=demo` và `LABOPS_ALLOW_MOCK_DATA=true`.

### [P0-02] Nhiều Endpoint Backend nhạy cảm không có xác thực và phân quyền RBAC, kèm CORS `*`
- **Tệp & hàm**: `backend/app/api/v1/api.py`, `backend/app/api/v1/endpoints/gpus.py`, `jobs.py`, `security.py`, `audit.py`, `backend/app/main.py` (`LabOpsRequestHandler`)
- **Bằng chứng**: `POST /api/v1/gpus/allocate`, `POST /api/v1/jobs/{job_id}/stop`, `GET /api/v1/audit-logs` cho phép gọi ẩn danh; `LabOpsRequestHandler._send_cors_headers()` gửi `Access-Control-Allow-Origin: *`.
- **Mức độ**: `P0 (Critical)`
- **Trạng thái sửa**: `IMPLEMENTED_AND_TESTED` — Đã bổ sung `backend/app/core/security.py` (HMAC session token + machine token cho Node Agent), áp dụng RBAC (`ADMIN` vs `RESEARCHER`) và kiểm tra quyền sở hữu tài nguyên (`require_owner_or_admin`) cho toàn bộ router FastAPI và stdlib HTTP server, đồng thời gỡ bỏ CORS `*`.

### [P0-03] Frontend Auth Guard (`protectRoute`) và Middleware (`proxy.ts`) âm thầm cho qua khi thiếu khóa hoặc lỗi Clerk
- **Tệp & hàm**: `src/lib/auth-guard.ts`, `src/proxy.ts`, `src/app/dashboard/layout.tsx`, `src/app/dashboard/*/page.tsx`
- **Bằng chứng**: Khối `try { await auth.protect(); } catch {}` nuốt ngoại lệ redirect của Clerk; 9 trang dashboard không gọi `await protectRoute()`.
- **Mức độ**: `P0 (Critical)`
- **Trạng thái sửa**: `IMPLEMENTED_AND_TESTED` — Đã chuyển `src/lib/auth-guard.ts` và `src/proxy.ts` sang cơ chế **fail-closed** (`503` khi thiếu cấu hình xác thực ngoài chế độ demo), và gắn `await protectRoute()` trên `DashboardLayout` cùng toàn bộ trang `/dashboard/*`.

### [P0-04] Thao tác Cấp phát GPU và Dừng Job giả lập thành công (Fake PID & Fake Success)
- **Tệp & hàm**:
  - `backend/app/services/labops_service.py` (`allocate_gpu`, `stop_job`)
  - `src/features/overview/components/gpu-allocation-dialog.tsx`
  - `src/features/resources/components/resources-management-view.tsx`
  - `src/features/labops/store.ts`
- **Bằng chứng**: Backend tự cộng VRAM giả và sinh `pid = 120000 + len(self.jobs) * 10`; Frontend dùng `setTimeout(..., 400)` và `Math.random()` để báo `toast.success("Cấp phát thành công GPU...")` kèm thanh trượt chọn VRAM 4–24GB dù Docker không giới hạn VRAM theo GB.
- **Mức độ**: `P0 (Critical)`
- **Trạng thái sửa**: `IMPLEMENTED_AND_TESTED` — Đã xây dựng `GpuScheduler` (`FULL_GPU_EXCLUSIVE` theo UUID thực, khóa file `fcntl`, kiểm tra tiến trình ngoài qua NVML, idempotency key) và nối hộp thoại UI qua BFF `/api/labops/gpus/allocate` với thông báo trung thực `PENDING_VERIFICATION`.

### [P0-05] `NvmlAdapter` gán cứng `"id": "ws-gpu-01"` cho mọi GPU trên máy chủ đa GPU
- **Tệp & hàm**: `backend/app/adapters/nvml_adapter.py` (`get_live_gpu_telemetry_sync`, `_get_live_compute_processes`)
- **Bằng chứng**: Trên máy chủ thật có 3× NVIDIA A30, vòng lặp đọc `nvidia-smi` gán trùng `"id": "ws-gpu-01"` cho cả 3 GPU và gán toàn bộ tiến trình của mọi GPU vào từng GPU.
- **Mức độ**: `P0 (Critical)`
- **Trạng thái sửa**: `IMPLEMENTED_AND_TESTED` — Đã sửa truy vấn `--query-compute-apps=gpu_uuid,pid,process_name,used_memory`, định danh từng GPU theo index và UUID thực (`workstation-gpu-00`, `workstation-gpu-01`, `workstation-gpu-02`), kèm `source`, `collected_at`, `freshness` và `vram_unit`.

### [P0-06] Phê duyệt Bảo mật Mã nguồn, Nhật ký `dmesg` và Trạng thái Sao lưu ZFS `100% OK` giả trên giao diện Production
- **Tệp & hàm**:
  - `src/features/security/components/security-management-view.tsx`
  - `src/features/overview/components/security-summary-panel.tsx`
  - `src/features/servers/components/server-detail-view.tsx`
- **Bằng chứng**: Cứng hóa chữ `"Sao lưu ZFS Snapshots: 100% OK / ĐỒNG BỘ 100%"`, 5 dòng `dmesg` giả và cảnh báo xanh `"Máy chủ hoạt động tối ưu"`.
- **Mức độ**: `P0 (Critical)`
- **Trạng thái sửa**: `IMPLEMENTED_AND_TESTED` — Đã thay bằng trạng thái trung thực `NOT_CONFIGURED` / `UNKNOWN` và nối luồng quét mã nguồn vào `SecurityScanner` (Trivy worker state machine).

### [P1-01] `docker-compose.yml` phơi cổng PostgreSQL `5432:5432` ra `0.0.0.0` và dùng mật khẩu mặc định yếu
- **Tệp**: `docker-compose.yml`
- **Mức độ**: `P1 (High)`
- **Trạng thái sửa**: `IMPLEMENTED_AND_TESTED` — Đã giới hạn bind `127.0.0.1:5432:5432` và `127.0.0.1:8000:8000`, bắt buộc khai báo biến bí mật qua `${POSTGRES_PASSWORD:?...}` và `${LABOPS_SESSION_SECRET:?...}`.

