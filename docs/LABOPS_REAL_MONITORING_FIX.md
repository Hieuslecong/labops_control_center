# BÁO CÁO KỸ THUẬT: KHẮC PHỤC DỮ LIỆU GIẢ ĐỊNH & KÍCH HOẠT GIÁM SÁT TIẾN TRÌNH THỜI GIAN THỰC (LABOPS REAL-TIME PROCESS MONITORING)

---

## 1. TỔNG QUAN VÀ NGUYÊN NHÂN GỐC RỄ (ROOT CAUSE ANALYSIS)

Trước khi thực hiện đợt nâng cấp này, giao diện LabOps Control Center vẫn còn hiển thị một số thông tin giả định hoặc dữ liệu snapshot tĩnh không phản ánh đúng hiện trạng máy chủ vật lý:
1. **Dữ liệu Jobs tính toán tĩnh:** Trong `src/features/labops/store.ts`, biến `PROD_JOBS` và `PROD_GPUS` lưu một bản chụp cũ chứa các PID chết (`3505602`, `73683`, `1833592`, `537529`) cùng danh sách tác vụ demo (`job-8921`, `job-8924`, `job-101` đến `job-104`).
2. **Backend sử dụng danh sách giả lập in-memory:** Thuộc tính `jobs` trong `backend/app/services/labops_service.py` trước đây được khởi tạo bằng `self._demo_jobs` cố định, dẫn đến các API `/api/v1/jobs` và `/api/v1/dashboard/summary` trả về dữ liệu mẫu (như người dùng demo `@hieulc`, `@tuanpm`, `@lanpt`).
3. **Thiếu kênh thu thập tiến trình thực từ hệ điều hành:** Hệ thống chưa có adapter chuyên trách để đọc cây tiến trình từ `/proc`, không phân giải được PID của NVIDIA CUDA ra script Python thực tế, và không đối chiếu phiên SSH thực qua `utmp`/socket.

---

## 2. KIẾN TRÚC GIÁM SÁT THỜI GIAN THỰC (MONITORING AGENT ARCHITECTURE)

Chúng tôi đã thiết kế và triển khai `ProcessMonitorAdapter` tại `backend/app/adapters/process_monitor_adapter.py` với các nguyên tắc kiến trúc cốt lõi:

### 2.1. Nguồn dữ liệu vật lý (Read-Only Data Sources)
- **Linux `/proc` & `psutil`**: Quét toàn bộ tiến trình hệ thống, thu thập PID, PPID, UID, username, CPU %, RAM RSS (MB), trạng thái thực thi (`RUNNING`, `SLEEPING`, `ZOMBIE`), thời điểm khởi tạo (`create_time`), và thời gian đã chạy (`elapsed_time`).
- **NVIDIA System Management Interface (`nvidia-smi`)**:
  - Truy vấn ứng dụng CUDA qua `--query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits`.
  - Phân giải trực tiếp dung lượng VRAM (MB) bị chiếm dụng bởi từng PID.
- **Incus & Cgroups (`/proc/{pid}/cgroup`)**: Tự động phát hiện container namespace (ví dụ: `lxc.payload.{container_name}`) để phân loại tiến trình chạy bên trong container Incus hay trực tiếp trên máy chủ vật lý (Host).
- **Active SSH Sessions (`utmp` / `psutil.users()` & Socket check)**: Đọc danh bạ người dùng đang mở terminal SSH vật lý (`pts/*`), IP nguồn, và thời gian duy trì phiên.

### 2.2. Khử trùng tham số bảo mật (Security Argument Sanitization)
Toàn bộ dòng lệnh (`cmdline`) được lọc tự động qua biểu thức chính quy (`SENSITIVE_ARGS_PATTERN`) nhằm che giấu hoàn toàn mật khẩu, token, API keys, certificates:
```python
SENSITIVE_ARGS_PATTERN = re.compile(
    r'(--(?:password|token|secret|key|api_key|auth|credential)[=\s]+)([^\s]+)',
    re.IGNORECASE
)
# Thay thế giá trị nhạy cảm thành ******** trước khi gửi sang API và frontend
```

### 2.3. Phân loại tiến trình thông minh (Workload Classification)
Hệ thống tự động phân loại tiến trình thành:
- **Tác vụ nghiên cứu AI / Machine Learning (`research`)**: Các tiến trình chạy Python (`python`, `python3`, `torchrun`, `deepspeed`, `vllm`, `ray`, `jupyter`) hoặc có tham số chứa các từ khóa học sâu (`train`, `eval`, `inference`, `finetune`, `lora`, `transformers`, `.py`).
- **Tiến trình tăng tốc phần cứng (`gpu`)**: Các PID đang trực tiếp giữ ngữ cảnh CUDA Context trên GPU NVIDIA A30.
- **Dịch vụ hệ thống (`all`)**: Toàn bộ tiến trình nền OS (systemd, journald, udev, v.v.).

---

## 3. HIỆN TRẠNG HẠ TẦNG VẬT LÝ ĐÃ XÁC THỰC (VERIFIED HARDWARE STATE)

### 3.1. Server 02 — Workstation (Cục bộ - TP.HCM)
- **CPU**: Intel(R) Xeon(R) Silver 4210R @ 2.40GHz (20 Cores / 40 vCPUs).
- **RAM**: 125.5 GB (Dung lượng thực tế đang dùng ~49.4 GB).
- **GPU**: 1 × NVIDIA A30 24GB HBM2 (PCIe Bus: `00000000:D8:00.0`, Driver: `595.71.05`, CUDA: `13.2`).
- **Tác vụ AI đang chạy thực tế (Active CUDA Workload)**:
  - **PID**: `2701676`
  - **Người sở hữu**: `tungtt` (Linux UID: `1001`)
  - **Lệnh thực thi**: `env/bin/python -u scripts/train_binary_lora.py --train_csv data/fusion_public_historical_v1/train.csv`
  - **VRAM chiếm dụng**: **20,906 MB** (~85% dung lượng VRAM của GPU A30)
  - **Worker phụ trợ**: PyTorch DataLoader processes (`PID 2707684`, `2707724`, `2707767`, `2707810`).
- **Phiên SSH vật lý**:
  - Người dùng `nhannv` đang mở 2 phiên kết nối từ IP `192.168.167.188` trên `pts/2` và `pts/4` (thời gian online liên tục: 2 ngày 8 giờ).

### 3.2. Server 01 — Track (Từ xa - Hà Nội)
- **Tình trạng kết nối**: **MẤT KẾT NỐI (UNAVAILABLE)** do đường hầm WireGuard VPN (`10.200.0.1`) chưa được kích hoạt ở đầu xa.
- **Chính sách hiển thị**: LabOps từ chối hiển thị dữ liệu giả định. Giao diện hiển thị rõ ràng: `Mất kết nối: Server 01 — Track chưa kết nối qua WireGuard VPN`.

---

## 4. CÁC API ENDPOINTS ĐÃ MỞ RỘNG VÀ KIỂM THỬ

Toàn bộ các endpoint sau đã được hiện thực trong FastAPI và tích hợp vào `LabOpsRequestHandler` phục vụ Next.js:

| Phương thức | Endpoint | Chức năng | Trạng thái kiểm thử |
|---|---|---|---|
| `GET` | `/api/v1/servers/{id}/processes?filter=...` | Lấy danh sách tiến trình thực tế (`all`, `research`, `gpu`) | Đã kiểm thử (200 OK) |
| `GET` | `/api/v1/servers/{id}/gpu-processes` | Lấy các tiến trình đang chiếm dụng VRAM GPU | Đã kiểm thử (200 OK) |
| `GET` | `/api/v1/servers/{id}/ssh-sessions` | Lấy danh sách phiên SSH vật lý đang hoạt động | Đã kiểm thử (200 OK) |
| `GET` | `/api/v1/jobs/active` | Trả về danh sách tác vụ tính toán thời gian thực | Đã kiểm thử (200 OK) |
| `GET` | `/api/v1/monitoring/health` | Kiểm tra tình trạng sức khỏe của Monitoring Agent | Đã kiểm thử (200 OK) |
| `GET` | `/api/v1/workspaces/{id}/processes` | Lấy tiến trình gắn với một workspace/container cụ thể | Đã kiểm thử (200 OK) |

Bộ test tự động `backend/tests/test_http_endpoints.py` đã vượt qua **39/39 ca kiểm thử** thành công.

---

## 5. CẢI TIẾN GIAO DIỆN NGƯỜI DÙNG (FRONTEND ENHANCEMENTS)

1. **`RunningJobsTable` (`src/features/overview/components/running-jobs-table.tsx`)**:
   - Tự động thăm dò dữ liệu (auto-polling) mỗi 4 giây từ API `/api/v1/jobs/active`.
   - Có nút **Tạm dừng / Tiếp tục** cập nhật để chuyên viên phân tích không bị nhảy bảng khi quan sát.
   - Nút **Làm mới ngay** tức thì.
   - Hiển thị thời điểm đồng bộ gần nhất (`Cập nhật: HH:MM:SS`) và banner cảnh báo nếu mất kết nối backend.
   - Bộ lọc đa tiêu chí: Lọc theo máy chủ, theo người dùng, và theo loại tải (Tất cả / Chỉ GPU / Chỉ CPU).
2. **`ProcessDetailSheet` (`src/features/overview/components/process-detail-sheet.tsx`)**:
   - Slide-over Sheet chi tiết khi nhấp chuột vào bất kỳ dòng tiến trình nào.
   - Hiển thị đầy đủ: Host PID, Parent PID (PPID), User/UID, Cụm/Máy chủ, Workspace/Cgroup container, Lệnh thực thi đầy đủ đã khử trùng (Command line), Tải CPU (%), RAM (MB/GB), VRAM (MB/GB), Nhiệt độ GPU, và Tình trạng SSH liên kết.
3. **`JobsMonitoringView` (`src/features/jobs/components/jobs-monitoring-view.tsx`)**:
   - Đồng bộ KPI card và bảng tiến trình thực tế.
   - Bổ sung nút làm mới và chỉ số thời gian cập nhật.
4. **`ServerDetailView` (`src/features/servers/components/server-detail-view.tsx`)**:
   - Tự động lấy danh sách tiến trình và phiên SSH thực tế cho máy chủ được chọn.
   - Hiển thị thông báo trạng thái rõ ràng khi máy chủ chưa có tiến trình hoặc đang ngoại tuyến.

---

## 6. TUÂN THỦ AN TOÀN VÀ HIỆU NĂNG (SAFETY & PERFORMANCE)

- **Nguyên tắc Read-Only Tuyệt đối**: Monitoring Agent hoạt động ở chế độ chỉ đọc. Không bao giờ phát sinh lệnh gửi tín hiệu dừng (`SIGKILL`, `SIGTERM`, `SIGSTOP`) tới tiến trình huấn luyện của nhóm nghiên cứu (`PID 2701676`).
- **Tải hệ thống thấp**: Quá trình thu thập từ `/proc` và `nvidia-smi` chỉ đọc thông tin tiến trình thực sự cần thiết, thời gian thực thi của mỗi chu kỳ thăm dò < 50ms, tiêu thụ CPU < 0.2%.
- **Bảo toàn lịch sử quan sát**: Tích hợp module lưu vết `backend/data/process_history.json` để ghi nhận đỉnh VRAM (`peak_vram_mb`) và tổng thời gian sống của các tiến trình ngay cả khi chúng hoàn thành.

---

## 7. KẾT QUẢ XÁC MINH TOÀN DIỆN (FINAL VERIFICATION)

1. **TypeScript Typecheck**:
   - Lệnh: `bun run typecheck` (`tsc --noEmit`)
   - Kết quả: **0 lỗi** (Exit code: 0).
2. **Next.js Production Build**:
   - Lệnh: `bun run build`
   - Kết quả: **32/32 routes biên dịch thành công** (Exit code: 0).
3. **Python Backend Test Suite**:
   - Lệnh: `python3 -m unittest discover -s backend/tests`
   - Kết quả: **Ran 39 tests in 0.954s — OK**.

