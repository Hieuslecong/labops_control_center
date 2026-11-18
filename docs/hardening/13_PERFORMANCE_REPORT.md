# 13 — BÁO CÁO HIỆU NĂNG & ĐỘ TRỄ (PERFORMANCE REPORT)

- **Branch**: `hardening/labops-v2`
- **Timestamp đo đạc**: `2026-11-18T15:15:34+07:00` (50 requests/endpoint liên tiếp sau 1 warm-up bằng `TestClient` trên `aiot-ws1`)

---

## 1. Tối ưu Hóa Backend: Loại bỏ Bão Subprocess trong `get_summary()`

### Nguyên nhân gốc được phát hiện qua Benchmark Milestone 4
Ở lần đo đầu tiên, `TestMilestone4PerformanceBenchmark` thất bại vì `GET /api/v1/dashboard/summary` có **`p95 = 4,117.05 ms`** (`> 4 giây/request`!):
- `LabOpsDataService.get_summary()` gọi lặp `@property self.gpus` **5 lần**, `@property self.servers` **4 lần**, và `@property self.jobs` **7 lần** (bao gồm gọi lồng từ `servers`).
- Mỗi lần đánh giá property chạy lại `nvidia-smi`, kiểm tra `docker --version`, đọc `/proc` và `ProcessMonitorAdapter.get_active_compute_jobs()` chạy quét dư thừa `filter_type="research"` khi `"workloads"` trả về rỗng.

### Biện pháp Tối ưu Đã Triển khai
1. `LabOpsDataService`: tách `_collect_servers(self, jobs_snapshot=None)` và snapshot `gpus_snapshot`, `jobs_snapshot`, `servers_snapshot` **đúng 1 lần** trong `get_summary()`.
2. `HostMonitorAdapter`, `NvmlAdapter`, `ProcessMonitorAdapter`: bổ sung cache đơn điệu theo thời gian thực **`time.monotonic()` TTL = `2.0s`** (`copy.deepcopy` tránh đột biến dữ liệu) và cache `300.0s` cho thông số tĩnh phiên bản NVIDIA driver/CUDA (`_get_nvidia_versions`).
3. Loại bỏ vòng quét `"research"` trùng lặp trong `ProcessMonitorAdapter.get_active_compute_jobs()`.

### Kết quả Đo đạc Thực tế (50 requests/endpoint)

| Endpoint | Min (ms) | p50 (ms) | **p95 (ms)** | p99 (ms) | Max (ms) | Trước tối ưu / Ghi chú |
|---|---|---|---|---|---|---|
| `GET /healthz` | `4.347` | `6.161` | **`7.255`** | `7.788` | `7.796` | Đạt ngưỡng `< 200ms` |
| `GET /api/v1/dashboard/summary` | `8.490` | `10.190` | **`12.219`** | `14.196` | `83.323` | **Giảm 336×** từ **`4,117.05 ms`** xuống **`12.219 ms`** |
| `GET /api/v1/v2/researcher/me` | `9.856` | `11.180` | **`12.336`** | `13.125` | `13.170` | Đạt ngưỡng `< 200ms` |
| `GET /api/v1/repositories/requests` | `8.006` | `9.215` | **`9.957`** | `11.032` | `11.115` | Agent HTTP mock trả `[]`; đo overhead Auth + FastAPI routing |

---

## 2. Tối ưu Hóa Frontend Polling & Bundling

1. **Hợp nhất Polling bằng TanStack Query (`src/features/labops/queries.ts`)**:
   - Xóa toàn bộ **9** vòng lặp `setInterval` (`global-telemetry-poller.tsx`, `overview-content.tsx`, `running-jobs-table.tsx`, `server-detail-view.tsx`, `resources-management-view.tsx`, `jobs-monitoring-view.tsx`, `security-management-view.tsx`, `security-summary-panel.tsx`, `member-repo-history-panel.tsx`).
   - Toàn bộ query cấu hình `refetchIntervalInBackground: false`, `staleTime: 5_000`, `refetchOnWindowFocus: true`, dãn chu kỳ đồng bộ từ 4–5 giây thành **10–15 giây** và tải danh sách tiến trình thô (`/processes?classification=all`) theo nhu cầu (`enabled: viewMode === 'diagnostics' && isAutoRefresh`).
   - Khử trùng lặp request giữa nhiều component nhờ `queryKey` dùng chung; giảm tải nền tab không hoạt động **~100%** khi tab ẩn (`visibilityState !== 'visible'`).
2. **Xóa Code Thừa & Chỉ số Build Cô lập (`c8b360d`)**:
   - Gỡ bỏ 12 route template & biểu đồ demo (`-8,595` dòng).
   - `NEXT_DIST_DIR=.next-verify bun run build` hoàn tất **compile trong `943 ms`**, TypeScript check trong `4.1s`, static pages **29/29 trong `1,498 ms`**.
   - Tắt `<ReactQueryDevtools />` mặc định ngay cả trong dev trừ khi `NEXT_PUBLIC_ENABLE_QUERY_DEVTOOLS=true`.
