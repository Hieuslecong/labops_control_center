# PROCESS OWNERSHIP & CLASSIFICATION DESIGN

**Tài liệu kỹ thuật:** Quy chuẩn định danh và phân loại quyền sở hữu tiến trình trong LabOps Control Center  
**Phiên bản:** 2.0  
**Ngày phê duyệt:** 11/11/2026  

---

## 1. MÔ HÌNH PHÂN LOẠI 4 NHÓM TIẾN TRÌNH

Mọi tiến trình trên hệ thống máy chủ vật lý hoặc bên trong các container đều được gán nhãn thuộc một trong bốn nhóm sau:

```
+-----------------------------------------------------------------------------------+
|                              LINUX PROCESS CLASSIFICATION                         |
+--------------------------+----------------------------+---------------------------+
|                          |                            |                           |
|  1. SYSTEM_PROCESS       |  2. MEMBER_PROCESS         |  3. RESEARCH_JOB          |
|  (Hệ thống & Dịch vụ)    |  (Tiến trình thành viên)   |  (Tác vụ tính toán AI/ML) |
|  - host sshd (:1911)     |  - bash/zsh shell trong ws |  - python train.py        |
|  - labops-agent (:9100)  |  - git, curl, tmux trong ws|  - torchrun, accelerate   |
|  - backend uvicorn       |  - container sshd (:220xx) |  - jupyter lab, ollama    |
|  - next-server (:3000)   |  - tiện ích người dùng     |  - deepspeed, vllm        |
|  - dockerd, containerd   |  (Thuộc ws-*, UID thành    |  (Chạy trong ws-* hoặc    |
|  - wg-easy, systemd      |   viên, không chiếm GPU)   |   được cấp phát GPU)      |
+--------------------------+----------------------------+---------------------------+
|                                                                                   |
|  4. GPU_PROCESS (Tiến trình chiếm dụng GPU)                                      |
|  - Bất kỳ tiến trình nào có mặt trong NVML compute apps (PID, GPU UUID, VRAM MB) |
|  - Có thể là con của RESEARCH_JOB hoặc tiến trình độc lập chiếm VRAM              |
+-----------------------------------------------------------------------------------+
```

---

## 2. NGUYÊN TẮC ÁNH XẠ TIẾN TRÌNH TỚI THÀNH VIÊN VÀ WORKSPACE

1. **Phân tích cgroup `/proc/{pid}/cgroup`:**
   - Nếu đường dẫn cgroup chứa `docker-<container_id>` hoặc `lxc.payload.<workspace_name>`:
     - Tiến trình thuộc về container đó.
     - Lấy tên container thông qua Docker Engine API hoặc nhãn `labops.workspace.owner` / tiền tố `ws-<username>`.
     - Ánh xạ trực tiếp tới `Member ID` và `Workspace ID`.
2. **Phân tích UID và Session:**
   - Nếu tiến trình chạy trực tiếp trên host:
     - Kiểm tra UID của tiến trình. Nếu UID thuộc dải người dùng thành viên được quản lý (`UID >= 1000` và username khớp với danh sách thành viên trong cơ sở dữ liệu LabOps), tiến trình được gán cho thành viên tương ứng.
     - Nếu UID thuộc `root`, `daemon`, `aiot-1` (user hạ tầng admin) và không nằm trong container workspace: mặc định phân loại là `SYSTEM_PROCESS`.
3. **Phân tích NVML Compute Apps:**
   - Lấy danh sách PID đang chạy trên từng GPU từ `nvidia-smi` hoặc thư viện NVML (`nvmlDeviceGetComputeRunningProcesses`).
   - Ghép nối PID với bảng tiến trình `/proc` để xác định chính xác GPU Index (0, 1, 2), GPU UUID, và lượng VRAM (MiB) đang chiếm dụng.

---

## 3. ĐỊNH DANH AN TOÀN TRÁNH PID REUSE (SAFE PROCESS REF)

Để ngăn ngừa việc kill nhầm tiến trình khi PID bị Linux tái sử dụng sau khi tiến trình cũ kết thúc, LabOps quy định chuỗi định danh duy nhất `ProcessRef`:

$$\text{ProcessRef} = \text{server\_id} : \text{container\_id} : \text{pid} : \text{start\_time\_ticks}$$

Ví dụ:  
`rack:ws-testacc:101234:16982340`  
hoặc (tiến trình host):  
`rack:host:852137:16892010`

### Quy trình xác thực trước khi gửi tín hiệu (Signal Pre-flight Check):
1. Tách chuỗi `ProcessRef` để lấy `pid` và `expected_start_time`.
2. Đọc `/proc/{pid}/stat` trên host.
3. Kiểm tra trường số 22 (`starttime` theo clock ticks của kernel Linux):
   - Nếu `/proc/{pid}` không tồn tại $\rightarrow$ Trả về lỗi `PROCESS_NOT_FOUND` (tiến trình đã kết thúc trước đó).
   - Nếu `current_start_time != expected_start_time` $\rightarrow$ Trả về lỗi `PID_RECYCLED_ERROR` (PID đã bị cấp cho tiến trình mới, hủy lệnh dừng ngay lập tức).
4. Kiểm tra `/proc/{pid}/cgroup`:
   - Nếu `container_id` không khớp $\rightarrow$ Trả về lỗi `CONTAINER_MISMATCH`.
5. Kiểm tra danh sách trắng bảo vệ (Safe Guards):
   - Nếu tiến trình thuộc nhóm được bảo vệ $\rightarrow$ Từ chối với lỗi `PROTECTED_SYSTEM_PROCESS`.
