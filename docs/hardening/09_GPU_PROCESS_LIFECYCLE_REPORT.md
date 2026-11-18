# 09 — BÁO CÁO VÒNG ĐỜI GPU & TIẾN TRÌNH (`GPU & PROCESS LIFECYCLE`)

- **Branch**: `hardening/labops-v2`
- **Thành phần liên quan**:
  - Go Process Controller & Collector: `go/internal/process/{controller,collector,safeguards,ref,types}.go`, `go/internal/gpu/collector.go`
  - Python GPU Scheduler & Adapters: `backend/app/services/gpu_scheduler.py`, `backend/app/adapters/{nvml_adapter,process_monitor_adapter,host_monitor_adapter}.py`, `backend/app/api/v1/endpoints/{gpus,processes}.py`
  - Frontend Dialogs: `src/features/jobs/components/{jobs-monitoring-view,stop-process-dialog,force-kill-dialog}.tsx`

---

## 1. Định danh Phần cứng & Cô lập Nguồn Dữ liệu Workstation / Rack

1. **Định danh Chính xác GPU Cục bộ**:
   - Workstation (`aiot-ws1`, ID `workstation`) sở hữu 1× NVIDIA A30 (24GB HBM2), ID `workstation-gpu-01`, nhãn hiển thị `WS-GPU-01`, bus `00000000:D8:00.0` (khi đọc từ driver/NVML).
   - Server Rack (`aiotlab-rack`, ID `track`) ngoại tuyến trả về trạng thái `OFFLINE` rõ ràng; tuyệt đối không gán GPU cục bộ của Workstation sang Rack.
   - `NvmlAdapter` hỗ trợ phân tích nhiệt độ thập phân (VD: `89.5°C`) và không chế tạo metric `64` hay giả định trạng thái live khi driver vắng mặt (`test_nvml_adapter_parses_decimal_temperature_and_no_fake_64`).

---

## 2. Điều phối GPU & Đối chiếu NVML (`GpuScheduler`)

- `GpuScheduler` (`backend/app/services/gpu_scheduler.py`) cấp phát reservation kèm TTL/lease (`reserve_gpu`), ghi xuống bảng `gpu_reservations`, kiểm tra xung đột (`FULL_GPU_EXCLUSIVE`), hỗ trợ `IdempotencyStore` (replay trả về bản ghi cũ, payload khác khóa cũ trả `409 Conflict`).
- Tích hợp kiểm tra chủ sở hữu trước khi dừng/giải phóng; đối chiếu trạng thái NVML thực tế (`active_processes`) để phân biệt `FREE`, `RESERVED`/`RUNNING`, và `BUSY_EXTERNAL`.

---

## 3. Lọc Tác vụ Nghiên cứu & Dừng Tiến trình An toàn (`pidfd`)

1. **Lọc Tiến trình Người dùng & Gom Nhóm Worker (`ProcessMonitorAdapter`)**:
   - Loại bỏ các tài khoản hệ thống (`root`, `systemd`, `daemon`, `aiot-ws1`) và tiến trình giám sát/shell không phải workload tính toán (`watch`, `top`, `htop`, `nvtop`, `tmux`, `sshd`, `node`, v.v.).
   - Giữ lại workload nghiên cứu của thành viên UID >= 1000, trích xuất tên script Python thực tế và gom nhóm worker PyTorch DataLoader (`children_count`).
2. **Kiểm soát Dừng / Buộc dừng Không Rò rỉ Đặc quyền**:
   - `SendSignalVerified` (`go/internal/process/controller.go`) dùng `unix.PidfdOpen(pid, 0)`, đọc lại thông tin tiến trình kiểm tra `expectedTicks` (`start_time_ticks`) + `expectedUID` trong khi giữ `pidfd`, sau đó mới gọi `unix.PidfdSendSignal` — loại bỏ hoàn toàn lỗi TOCTOU PID-reuse.
   - `CheckSafeGuard` bảo vệ các daemon quan trọng (`systemd`, `sshd`, `dockerd`, `labops-*`, PID 1).
   - Frontend `StopProcessDialog` và `ForceKillDialog` sử dụng ``${job.serverId || 'workstation'}`` thay vì gán cứng `track:`.
   - Cache telemetry `2.0s` ở các adapter loại bỏ lặp quét `nvidia-smi` / `/proc` khi dashboard đồng bộ đồng thời.

---

## 4. Kết quả Kiểm thử

| Suite | Kết quả | Phạm vi xác minh |
|---|---|---|
| `go test -race ./internal/process ./internal/gpu` | **9/9 PASS** | `TestProcessController_All9SafetyScenarios`, `TestCheckSafeGuard_ProtectedProcesses`, `TestCheckSafeGuard_BackendProtection`, `TestRef_BuildAndParse`, `collector` tests |
| `pytest backend/tests/test_process_control.py backend/tests/test_user_workloads_filtering.py backend/tests/test_gpu_allocations_grouped.py backend/tests/test_system_health_regressions.py` | **PASS** | Lọc workload nghiên cứu, gom bảng cấp phát theo GPU, bảo vệ system processes, RBAC kill/stop |
| `pytest backend/tests/test_milestone2_hardening.py` (GPU scheduler & idempotency tests) | **PASS** | `IdempotencyConflictError` 409, NVML reconciliation |
| `backend/tests/test_adapters.py::test_nvml_adapter_live_hardware` | **SKIPPED** | Bỏ qua trong sandbox không truy cập phần cứng live |
