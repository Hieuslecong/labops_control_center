# THIẾT KẾ BỘ LẬP LỊCH GPU & KẾ TOÁN GPU-HOURS (GPU SCHEDULER DESIGN V2)

## 1. Tại sao V1 chỉ công bố chế độ `FULL_GPU_EXCLUSIVE`?
Docker Engine và NVIDIA Container Toolkit truyền thống gán thiết bị GPU vật lý vào container thông qua biến `NVIDIA_VISIBLE_DEVICES=<gpu-uuid>`. Docker **không có cơ chế giới hạn cứng dung lượng VRAM theo từng GB** (ví dụ 8GB hay 12GB trên một thẻ A30 24GB) nếu không cấu hình phân vùng phần cứng NVIDIA MIG ở cấp driver. Do đó:
- LabOps V2 **gỡ bỏ hoàn toàn thanh trượt chọn VRAM 4–24GB giả** trên giao diện.
- Bộ lập lịch GPU V1 (`backend/app/services/gpu_scheduler.py`) chỉ cho phép chế độ **`FULL_GPU_EXCLUSIVE`** (cấp phát độc quyền toàn bộ 1 GPU vật lý theo UUID thực).
- Mọi yêu cầu gửi chế độ `SHARED` đều bị từ chối với mã lỗi `400 MODE_NOT_SUPPORTED`.

---

## 2. Cơ chế Chống Cấp phát Trùng & Kiểm tra Tiến trình Ngoài
Khi nhận yêu cầu `reserve(gpu_uuid, owner, actor, job_name, mode, idempotency_key)`:
1. **Khóa đồng thời (`fcntl.flock`)**: Toàn bộ quá trình đọc trạng thái, kiểm tra xung đột và ghi reservation mới diễn ra trong cùng một critical section có khóa độc quyền trên tệp lock.
2. **Idempotency Key**: Nếu `Idempotency-Key` đã tồn tại, trả về ngay reservation đã tạo trước đó mà không tạo bản ghi trùng.
3. **Truy vấn NVML Trực tiếp (`nvidia-smi`)**:
   - Nếu không đọc được NVML hoặc `gpu_uuid` không xuất hiện trong kết quả quan sát thực tế → từ chối `503 GPU_NOT_OBSERVED` (fail-closed).
   - Nếu GPU đang có bất kỳ tiến trình nào trong `active_processes` (kể cả tiến trình chạy ngoài hệ thống lịch) → từ chối `409 GPU_BUSY_EXTERNAL`.
   - Nếu GPU đang có reservation ở trạng thái `RESERVED | STARTING | RUNNING | STOP_REQUESTED` → từ chối `409 GPU_ALREADY_RESERVED`.

---

## 3. Vòng đời Reservation & Kế toán GPU-Hours Trung thực
- Khi giữ chỗ thành công, reservation ở trạng thái `RESERVED` với `runtime_verified = False`, `pid = None` và kết quả trả về là `PENDING_VERIFICATION` (không bao giờ tự bịa PID giả).
- Chỉ khi Node Agent xác thực bằng machine token gọi `confirm_from_agent(reservation_id, "RUNNING", pid)` thì trạng thái mới chuyển sang `RUNNING` (`runtime_verified = True`).
- Hàm `GpuScheduler.gpu_hours(reservation)` tách bạch 3 chỉ số:
  1. `allocated_hours`: Tổng thời gian giữ GPU (`reserved_at` → `ended_at` hoặc hiện tại).
  2. `runtime_hours`: Thời gian thực thi đã được Node Agent xác minh (`started_at` → `ended_at`), trả về `None` nếu chưa xác minh runtime.
  3. `idle_hold_hours`: Thời gian giữ GPU nhưng chưa chạy thực tế (`allocated_hours - runtime_hours`).

